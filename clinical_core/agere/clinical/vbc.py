"""Value-based-care engine: eligibility, attribution, utilization, cost, risk, quality, shared savings.

Computed from the received data via the semantic layer. Every metric states its
population, period, denominator and method. Definitions are Agere's internal,
documented versions (not certified CMS/NCQA specifications): the warehouse has
no CPT/HCPCS, claim diagnoses or official value sets, and contract terms must be
supplied. Small groups are stabilised with empirical-Bayes shrinkage and carry
95% intervals, so rankings reflect evidence rather than panel size.
"""
from collections import defaultdict
from datetime import date
import json, math, re, sqlite3

import numpy as np

from . import aggregate, semantic_layer

VERSION = 'vbc-engine/1'
METRICS = ('eligibility', 'attribution', 'utilization', 'cost', 'risk', 'quality', 'shared_savings', 'change')
CHANGE_MEASURES = ('pmpm', 'ed_rate', 'admit_rate')
# race/ethnicity/language/interpreter: health-equity stratification, only where the release carries the fields (v2.0+)
DEMOGRAPHIC_GROUPS = ('race', 'ethnicity', 'race_ethnicity', 'language', 'interpreter')
TIME_GROUPS = ('month', 'quarter', 'year')  # calendar periods: cost and utilization only
GROUP_BY = ('none', 'pcp', 'practice', 'coverage_type', 'age_band', 'sex') + DEMOGRAPHIC_GROUPS + TIME_GROUPS


def _time_key(month, group_by):
    """'2029-08' -> '2029-08' | '2029-Q3' | '2029'."""
    if group_by == 'month': return month
    if group_by == 'quarter': return f'{month[:4]}-Q{(int(month[5:7]) - 1) // 3 + 1}'
    return month[:4]
PCP_VISIT_TYPES = ('primary_care_visit',)
STATIN = re.compile(r'statin\b|atorvastatin|simvastatin|rosuvastatin|pravastatin|lovastatin|pitavastatin|fluvastatin', re.I)
CHRONIC = aggregate.BURDEN_CONCEPTS


def _connect(path):
    return sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)


def _months(start, end):
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while (y, m) <= (int(end[:4]), int(end[5:7])):
        out.append(f'{y:04d}-{m:02d}'); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def _period(p, as_of):
    end = (p.get('period') or {}).get('end') or as_of
    start = (p.get('period') or {}).get('start') or f'{int(end[:4]) - 1}{end[4:7]}-01'
    return start[:10], end[:10]


def validate(p):
    if not isinstance(p, dict) or p.get('metric') not in METRICS:
        raise ValueError(f'vbc metric must be one of {METRICS}')
    if p.get('group_by', 'none') not in GROUP_BY: raise ValueError(f'group_by must be one of {GROUP_BY}')
    if p.get('group_by') in TIME_GROUPS and p['metric'] not in ('cost', 'utilization'):
        raise ValueError('group_by month/quarter/year applies to cost and utilization (calendar-period rates)')
    if p['metric'] == 'shared_savings':
        terms = p.get('terms') or {}
        if p.get('program'):  # a named program (e.g. 'MSSP ENHANCED 2026') supplies its cited terms; stated terms override
            from . import programs
            terms = programs.contract_terms(p['program'], terms)
            p = {**p, 'terms': terms}
        missing = [k for k in ('benchmark_pmpm', 'sharing_rate') if not isinstance(terms.get(k), (int, float))
                   and not (k == 'sharing_rate' and terms.get('corridors'))]
        if missing:
            known = (terms.get('program') or {}).get('label')
            raise ValueError('Shared savings needs supplied contract terms: ' + ', '.join(missing)
                             + (f' ({known} supplies the sharing, MSR/MLR, loss and cap terms; the benchmark is contract-specific)' if known else ''))
        validate_settlement_terms(terms)
    if p['metric'] == 'change':
        if p.get('measure') not in CHANGE_MEASURES: raise ValueError(f'change measure must be one of {CHANGE_MEASURES}')
        cmp = p.get('compare') or {}
        if cmp and not all(isinstance(cmp.get(k), dict) and cmp[k].get('start') and cmp[k].get('end') for k in ('a', 'b')):
            raise ValueError('compare needs periods a and b with start/end dates')
    out = {'metric': p['metric'], 'group_by': p.get('group_by', 'none'), 'period': p.get('period') or {}, 'compare': p.get('compare') or {},
           'measure': p.get('measure', ''), 'terms': p.get('terms') or {}, 'attribution': p.get('attribution', 'hybrid'),
           'denominator': _denominator(p.get('denominator', 'member_years'))}
    if p['metric'] == 'quality' and (p.get('measure') or p.get('measures')) and p.get('measure') not in INTERNAL_MEASURES:
        # Measure library (config/agere/measures): a measure id, a list, or a named set (e.g. mssp_app_plus).
        from . import measures
        lib = measures.validate({'measure': p.get('measure'), 'measures': p.get('measures'), 'output': p.get('output', 'rates')})
        out.update(measure_ids=lib['measures'], output=lib['output'], **({'measures': p['measures']} if p.get('measures') else {}))
    return out


INTERNAL_MEASURES = ('', 'diabetes_a1c_poor_control', 'bp_control', 'statin_therapy_diabetes')


def _denominator(d):
    if d not in ('member_years', 'roster'): raise ValueError('denominator: member_years (default, NCQA/ACO REACH) or roster (headcount)')
    return d


# ---- building blocks -------------------------------------------------------------
def _has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def span_months(db, start, end):
    """Member months from payer enrollment spans (semantic member_month), for every patient with any span:
    {pid: {'months': [...in window], 'line_of_business': latest in window (else latest overall)}}. {} when the
    release has no spans (v1.x, v2.0)."""
    if not _has_table(db, 'member_month'): return {}
    out = {pid: {'months': [], 'line_of_business': None} for (pid,) in db.execute('SELECT DISTINCT patient_id FROM enrollment_span')}
    latest = {}
    for pid, month, lob in db.execute('SELECT patient_id, month, line_of_business FROM member_month ORDER BY patient_id, month'):
        if pid not in out: continue
        latest[pid] = lob
        if start[:7] <= month <= end[:7]: out[pid]['months'].append(month); out[pid]['line_of_business'] = lob
    for pid, info in out.items():
        info['line_of_business'] = info['line_of_business'] or latest.get(pid)
    return out


def eligibility(db, as_of, start, end):
    """Enrolled months per patient. Patients with payer enrollment spans: the months their spans cover (member_month,
    terminations respected). Others: active coverage from its effective start, open-ended to the snapshot (the coverage
    record carries no termination date), confirmed by any benefit verification in the period."""
    from .age import age_at
    cols = {r[1] for r in db.execute('PRAGMA table_info(patient)')}
    birth = 'birth_date' if 'birth_date' in cols else 'NULL'
    people = {pid: {'age': age_at(born, as_of), 'sex': s, 'coverage_type': ct, 'status': st}
              for pid, born, s, ct, st in db.execute(f'SELECT patient_id, {birth}, sex_reported, coverage_type, coverage_status FROM patient')}
    for pid, values in demographics(db).items():
        if pid in people: people[pid].update(values)
    starts = {pid: d for pid, d in db.execute("SELECT patient_id, min(coalesce(json_extract(payload,'$.effective_start'), event_date)) "
                                              "FROM record WHERE stream='coverage' GROUP BY 1")}
    window = _months(start, end)
    spans = span_months(db, start, end)
    for pid, info in people.items():
        if pid in spans:  # enrollment spans present: member months come from the spans, not a static roster
            info['months'] = [m for m in spans[pid]['months'] if m in window]
            info['coverage_type'] = info['coverage_type'] or spans[pid]['line_of_business']
            info['enrollment_basis'] = 'enrollment_span'
            continue
        eff = starts.get(pid)
        enrolled = info['status'] == 'active' and info['coverage_type'] not in (None, 'uninsured') and eff
        info['months'] = [m for m in window if enrolled and m >= eff[:7]] if enrolled else []
        info['enrollment_basis'] = 'coverage_record'
    return people


def payer_attribution(db, end):
    """Payer-attributed practice/NPI per patient from the latest member month at or before `end` that carries one."""
    if not _has_table(db, 'member_month'): return {}
    out = {}
    for pid, practice, npi in db.execute('SELECT patient_id, attribution_practice_id, attribution_npi FROM member_month '
                                         'WHERE month <= ? AND (attribution_practice_id IS NOT NULL OR attribution_npi IS NOT NULL) '
                                         'ORDER BY patient_id, month', (end[:7],)):
        out[pid] = (practice, npi)
    return out


def attribution(db, people, start, end, method='hybrid'):
    """PCP attribution. prospective = payer attribution from the enrollment span when present (practice; PCP = the
    attributed NPI's clinician, else the assigned PCP or visit-plurality PCP at that practice), else the latest assigned
    primary-care relationship; plurality = clinician with most primary-care visits in the period (ties -> most recent
    visit); hybrid = prospective, else plurality."""
    assigned = {}
    for pid, payload in db.execute("SELECT patient_id, payload FROM record WHERE stream='care_team_relationships'"):
        r = json.loads(payload)
        if r.get('role') in aggregate.PCP_ROLES and r.get('status') in (None, 'assigned'):
            key = r.get('effective_start') or r.get('event_date') or ''
            if key and key[:10]>end[:10]:continue
            if r.get('effective_end') and r['effective_end'][:10]<end[:10]:continue
            if pid not in assigned or key >= assigned[pid][0]:
                assigned[pid] = (key, r.get('clinician_id'), r.get('organization_id'))
    visits = defaultdict(lambda: defaultdict(lambda: [0, '', None]))
    for pid, clin, org, d in db.execute(
            "SELECT patient_id, attending_clinician_id, organization_id, event_date FROM visit "
            "WHERE type_concept IN ('primary_care_visit') AND attending_clinician_id IS NOT NULL AND event_date BETWEEN ? AND ?",
            (start, end)):
        v = visits[pid][clin]; v[0] += 1; v[1] = max(v[1], d or ''); v[2] = org
    payer = payer_attribution(db, end) if method in ('prospective', 'hybrid') else {}
    by_npi = {npi: cid for cid, npi in db.execute('SELECT clinician_id, npi FROM clinician WHERE npi IS NOT NULL')} if payer else {}
    out = {}
    for pid in people:
        plural = max(visits[pid].items(), key=lambda kv: (kv[1][0], kv[1][1]))[0] if visits[pid] else None
        pros = assigned.get(pid)
        if pid in payer:
            practice, npi = payer[pid]
            pcp = by_npi.get(npi) or (pros[1] if pros and pros[2] == practice else None) or \
                  (plural if plural and visits[pid][plural][2] == practice else None)
            out[pid] = {'pcp': pcp or 'unattributed', 'practice': practice or 'unattributed', 'method': 'payer_attribution',
                        'visits_with_pcp': visits[pid].get(pcp, [0])[0] if pcp else 0}
        elif method == 'prospective' or (method == 'hybrid' and pros):
            out[pid] = {'pcp': pros[1], 'practice': pros[2], 'method': 'assigned_pcp', 'visits_with_pcp': visits[pid].get(pros[1], [0])[0]} if pros else \
                       {'pcp': 'unattributed', 'practice': 'unattributed', 'method': 'none'}
        elif plural:
            out[pid] = {'pcp': plural, 'practice': visits[pid][plural][2], 'method': 'visit_plurality', 'visits_with_pcp': visits[pid][plural][0]}
        else:
            out[pid] = {'pcp': 'unattributed', 'practice': 'unattributed', 'method': 'none', 'visits_with_pcp': 0}
        out[pid]['plurality_pcp'] = plural
        out[pid]['agrees_with_plurality'] = bool(pros and plural and pros[1] == plural)
    return out


def demographics(db):
    """Received race/ethnicity/language/interpreter fields per patient ({} when the release has none: v1.x)."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(patient)')}
    if not {'race', 'ethnicity', 'preferred_language', 'interpreter_needed'} <= cols: return {}
    return {pid: {'race': race, 'ethnicity': eth, 'language': lang, 'interpreter': interp}
            for pid, race, eth, lang, interp in db.execute('SELECT patient_id, race, ethnicity, preferred_language, interpreter_needed FROM patient')
            if any(v is not None for v in (race, eth, lang, interp))}


def demographic_key(group_by, values):
    """Stratum label for a demographic group_by; missing values are labelled, never dropped."""
    v = values or {}
    if group_by == 'race': return v.get('race') or 'not reported'
    if group_by == 'ethnicity': return v.get('ethnicity') or 'not reported'
    if group_by == 'race_ethnicity': return f"{v.get('race') or 'race not reported'} / {v.get('ethnicity') or 'ethnicity not reported'}"
    if group_by == 'language': return v.get('language') or 'not reported'
    if group_by == 'interpreter':
        i = v.get('interpreter')
        return 'not reported' if i is None else 'interpreter needed' if i in (1, True, '1', 'true', 'yes') else 'no interpreter needed'
    raise ValueError(f'not a demographic group: {group_by}')


def require_demographics(db, group_by):
    """A demographic stratification on a release without the fields is an error, not a table of 'not reported'."""
    if group_by in DEMOGRAPHIC_GROUPS and not demographics(db):
        raise ValueError(f'group_by {group_by} needs race/ethnicity/language fields, which this release does not carry')


def _group_key(group_by, pid, people, attr):
    if group_by == 'none': return 'all'
    if group_by in ('pcp', 'practice'): return attr[pid][group_by] or 'unattributed'
    if group_by == 'age_band': return aggregate.age_band(people[pid]['age'])
    if group_by == 'sex': return people[pid]['sex'] or 'unknown'
    if group_by in DEMOGRAPHIC_GROUPS: return demographic_key(group_by, people[pid])
    return people[pid]['coverage_type'] or 'unknown'


def _gamma_poisson(rows, events_key, exposure_key):
    """Empirical-Bayes rate shrinkage (method of moments) with 95% posterior intervals."""
    rates = [r[events_key] / r[exposure_key] for r in rows if r[exposure_key] > 0]
    if not rates: return
    total_e = sum(r[events_key] for r in rows); total_x = sum(r[exposure_key] for r in rows)
    mean = total_e / total_x if total_x else 0
    var = max(np.var(rates) - mean / max(np.mean([r[exposure_key] for r in rows if r[exposure_key] > 0]), 1e-9), 1e-12)
    # Prior worth at most one typical group's exposure, so intervals keep honest width
    # even when between-group variation is indistinguishable from noise.
    beta = min(mean / var if var > 0 else 1e6, float(np.median([r[exposure_key] for r in rows if r[exposure_key] > 0])))
    alpha = mean * beta
    from scipy.stats import gamma
    for r in rows:
        a, b = alpha + r[events_key], beta + r[exposure_key]
        r['shrunk_rate'] = a / b
        r['rate_ci95'] = [float(gamma.ppf(0.025, a, scale=1 / b)), float(gamma.ppf(0.975, a, scale=1 / b))]


def _beta_binomial(rows, num_key, den_key):
    p = [r[num_key] / r[den_key] for r in rows if r[den_key] > 0]
    if not p: return
    m = sum(r[num_key] for r in rows) / max(sum(r[den_key] for r in rows), 1)
    v = max(np.var(p), 1e-9)
    k = min(max(m * (1 - m) / v - 1, 1.0), float(np.median([r[den_key] for r in rows if r[den_key] > 0])))
    from scipy.stats import beta as beta_dist
    for r in rows:
        a, b = m * k + r[num_key], (1 - m) * k + r[den_key] - r[num_key]
        r['shrunk_rate'] = a / (a + b)
        r['rate_ci95'] = [float(beta_dist.ppf(0.025, a, b)), float(beta_dist.ppf(0.975, a, b))]


# ---- metrics ---------------------------------------------------------------------
def _utilization(db, people, attr, p, start, end):
    groups = defaultdict(lambda: {'members': 0, 'member_years': 0.0, 'ed_visits': 0, 'admissions': 0, 'index_stays': 0,
                                  'readmissions': 0, 'discharges_7d_eligible': 0, 'followup_7d': 0})
    ed = defaultdict(list)
    from .ed_definition import metric_visits_sql
    for pid, d in db.execute(f"SELECT patient_id, event_date FROM ({metric_visits_sql(db)}) WHERE event_date BETWEEN ? AND ?", (start, end)):
        ed[pid].append(d)
    stays = defaultdict(list)
    for pid, a, dis, rd in db.execute("SELECT patient_id, admit_date, discharge_date, readmitted_30d FROM stay WHERE admit_date BETWEEN ? AND ?", (start, end)):
        stays[pid].append((a, dis, rd))
    visits = defaultdict(list)
    for pid, d in db.execute("SELECT patient_id, event_date FROM encounter WHERE type_concept IN ('primary_care_visit','specialist_visit','telehealth_contact') "
                             "AND event_date IS NOT NULL"):
        visits[pid].append(d)
    timed = p['group_by'] in TIME_GROUPS
    for pid, info in people.items():
        if not info['months']: continue
        enrolled = set(info['months'])
        if timed:  # each enrolled month, ED day, stay and discharge lands in its own calendar period
            enrolled = set(info['months'])
            for key in {_time_key(m, p['group_by']) for m in enrolled}: groups[key]['members'] += 1
            for m in enrolled:
                g = groups[_time_key(m, p['group_by'])]; g['member_years'] = round(g['member_years'] + 1 / 12, 4)
            for d in ed[pid]:
                if d and d[:7] in enrolled: groups[_time_key(d[:7], p['group_by'])]['ed_visits'] += 1
            keyed_stays = [((a or '')[:7], a, dis, rd) for a, dis, rd in stays[pid] if a and a[:7] in enrolled]
        else:
            g = groups[_group_key(p['group_by'], pid, people, attr)]
            g['members'] += 1; g['member_years'] = round(g['member_years'] + len(info['months']) / 12, 4)
            g['ed_visits'] += sum(d and d[:7] in enrolled for d in ed[pid])
            keyed_stays = [(None, a, dis, rd) for a, dis, rd in stays[pid] if a and a[:7] in enrolled]
        for month, a, dis, rd in keyed_stays:
            g = groups[_time_key(month, p['group_by'])] if timed else g
            g['admissions'] += 1; g['index_stays'] += 1; g['readmissions'] += rd
            if dis and dis <= end:
                g['discharges_7d_eligible'] += 1
                lo, hi = date.fromisoformat(dis), date.fromisoformat(dis).toordinal() + 7
                g['followup_7d'] += any(lo.toordinal() < date.fromisoformat(v).toordinal() <= hi for v in visits[pid])
    rows = [{'group': k, **v} for k, v in groups.items()]
    for r in rows:
        r['ed_per_1000'] = round(1000 * r['ed_visits'] / r['member_years'], 1) if r['member_years'] else None
        r['admits_per_1000'] = round(1000 * r['admissions'] / r['member_years'], 1) if r['member_years'] else None
        r['readmission_rate'] = round(r['readmissions'] / r['index_stays'], 4) if r['index_stays'] else None
        r['followup_7d_rate'] = round(r['followup_7d'] / r['discharges_7d_eligible'], 4) if r['discharges_7d_eligible'] else None
    if not timed: _gamma_poisson(rows, 'ed_visits', 'member_years')  # periods are a series, not exchangeable units to shrink together
    for r in rows:
        if 'shrunk_rate' in r:
            r['ed_per_1000_shrunk'] = round(1000 * r.pop('shrunk_rate'), 1)
            r['ed_per_1000_ci95'] = [round(1000 * x, 1) for x in r.pop('rate_ci95')]
    if timed: rows.sort(key=lambda r: r['group'])  # chronological
    else: rows.sort(key=lambda r: -(r.get('ed_per_1000_shrunk') or 0))
    if p.get('denominator') == 'roster' and timed: raise ValueError('roster denominators are headcounts; use member-years for rates by period')
    if p.get('denominator') == 'roster':  # registry variant: per 1,000 roster patients (headcount, not annualised)
        roster = [pid for (pid,) in db.execute('SELECT patient_id FROM patient')]
        keyed = defaultdict(list)
        for pid in roster: keyed[_group_key(p['group_by'], pid, people, attr) if pid in people else 'not_enrolled'].append(pid)
        for r in rows:
            pids = keyed.get(r['group'], [])
            r['roster_patients'] = len(pids)
            r['ed_visits_roster'] = sum(len(ed[x]) for x in pids)
            r['admissions_roster'] = sum(len(stays[x]) for x in pids)
            r['ed_per_1000_roster'] = round(1000 * r['ed_visits_roster'] / len(pids), 1) if pids else None
            r['admits_per_1000_roster'] = round(1000 * r['admissions_roster'] / len(pids), 1) if pids else None
    return rows, (('Rates per 1,000 roster patients (headcount, not annualised) as requested; ' if p.get('denominator') == 'roster' else
                   'Rates per 1,000 member-years (NCQA/ACO REACH convention); ') +
                  'Members with enrolled months in the period; ED visits = distinct ED days and admissions = distinct hospital stays '
                  'from every source (EHR encounters, ADT visits, payer claims; one event per real visit, see utilization_event); '
                  '7-day follow-up = clinic/specialist/telehealth contact within 7 days after discharge. ' +
                  (f'By {p["group_by"]}: member-years, ED days and stays (by admit month) fall in their own calendar period, counted in '
                   'enrolled months. ' if timed else '') +
                  'ED rates shrunk with empirical-Bayes gamma-Poisson; rank on the shrunk rate and its interval.')


def _claims(db, start, end):
    """Counted medical paid by service month; net pharmacy payer deltas by event month (Mike 2026-10-25)."""
    if not _has_table(db, 'pharmacy_claim_event') or not _has_table(db, 'payer_claim'):
        raise ValueError('Counted medical claims and pharmacy transaction deltas are required to compute payer spend')
    rows = []
    # Explicit allowed-cost analyses retain received provider allowed amounts. Payer spend never uses these raw payments.
    for pid, payload, available in db.execute("SELECT patient_id, payload, available_date FROM record WHERE stream='medical_claims'"):
        r = json.loads(payload)
        if r.get('status') == 'paid' and r.get('service_date') and start <= r['service_date'][:10] <= end:
            rows.append((pid, r['service_date'][:7], r.get('allowed_cents') or 0, 0, 'medical', available))
    for pid, payload, available in db.execute("SELECT patient_id, payload, available_date FROM record WHERE stream='pharmacy_claims'"):
        r = json.loads(payload)
        d = (r.get('event_date') or '')[:10]
        if r.get('transaction_status') != 'paid' or not d or not (start <= d <= end): continue
        paid = (r.get('payer_paid_cents') or 0)
        rows.append((pid, d[:7], paid + (r.get('patient_paid_cents') or 0), 0, 'pharmacy', available))
    payer = _payer_claims(db, start, end)
    if payer is not None:  # payer feed present: it is the cost source for every patient it covers
        covered = payer[0]
        rows = [r for r in rows if r[0] not in covered] + payer[1]
    # Mike 2026-10-25: paid pharmacy spend spans all sources and nets transactions in their event month.
    rows += [(pid, day[:7], 0, paid or 0, 'pharmacy', day) for pid, day, paid in db.execute(
        'SELECT patient_id, event_date, payer_paid_delta_cents FROM pharmacy_claim_event WHERE event_date BETWEEN ? AND ?', (start, end))]
    return rows


def _payer_claims(db, start, end):
    """Counted payer claims by service date, plus the explicitly allowed pharmacy component.
    Pharmacy payer spend is added separately from all transaction deltas in _claims (Mike 2026-10-25)."""
    if not _has_table(db, 'payer_claim'): return None
    covered = {pid for (pid,) in db.execute('SELECT patient_id FROM payer_claim UNION SELECT patient_id FROM payer_pharmacy_claim')}
    claim_cols = {r[1] for r in db.execute('PRAGMA table_info(payer_claim)')}
    rx_cols = {r[1] for r in db.execute('PRAGMA table_info(payer_pharmacy_claim)')}
    if 'counts_in_metrics' not in claim_cols: raise ValueError('Medical payer spend requires counted claim versions')
    current = ' AND counts_in_metrics = 1'
    filled = ' AND counts_as_fill = 1' if 'counts_as_fill' in rx_cols else ''
    rows = [(pid, svc[:7], allowed or 0, paid or 0, 'medical', available) for pid, svc, allowed, paid, available in db.execute(
        "SELECT patient_id, service_from, CASE WHEN final_status = 'paid' THEN allowed_cents ELSE 0 END, paid_cents, last_available_date FROM payer_claim "
        "WHERE service_from IS NOT NULL AND substr(service_from, 1, 10) BETWEEN ? AND ?" + current, (start, end))]
    rows += [(pid, d[:7], (plan or 0) + (patient or 0), 0, 'pharmacy', available) for pid, d, plan, patient, available in db.execute(
        "SELECT patient_id, fill_date, plan_paid_cents, patient_pay_cents, available_date FROM payer_pharmacy_claim "
        "WHERE claim_status = 'paid' AND fill_date BETWEEN ? AND ?" + filled, (start, end))]
    return covered, rows


def _completion(claims, end, basis='allowed'):
    """Arrival-lag factors on the named monetary basis, estimated on months with six months' runout."""
    if basis not in ('allowed', 'paid'): raise ValueError('completion basis: allowed|paid')
    lag = lambda svc, arr: (int(arr[:4]) - int(svc[:4])) * 12 + int(arr[5:7]) - int(svc[5:7]) if arr else 0
    by_month = defaultdict(lambda: defaultdict(float))
    for row in claims:
        svc, arr = row[1], row[5]
        by_month[svc][max(lag(svc, arr or svc), 0)] += row[2 if basis == 'allowed' else 3]
    end_m = end[:7]
    mature = [m for m in by_month if lag(m, end_m) >= 6]
    factors = {}
    for k in range(0, 7):
        tot = sum(sum(v.values()) for m, v in by_month.items() if m in mature)
        within = sum(sum(x for l, x in v.items() if l <= k) for m, v in by_month.items() if m in mature)
        factors[k] = (tot / within) if within else 1.0
    return {m: factors.get(min(lag(m, end_m), 6), 1.0) for m in by_month}, factors


def _cost(db, people, attr, p, start, end):
    claims = _claims(db, start, end)
    # Completion factors need mature months (6+ months of runout): a window shorter than 12 months (the last 3 months, one
    # quarter) estimates them on the 12 months ending at the period end, not on its own immature months (factors of 1.0).
    twelve = f'{int(end[:4]) - 1}{end[4:7]}-01'
    completion, factors = _completion(_claims(db, twelve, end) if start > twelve else claims, end)
    groups = defaultdict(lambda: {'members': 0, 'member_months': 0, 'allowed': 0.0, 'paid': 0.0, 'allowed_completed': 0.0, 'pharmacy_allowed': 0.0, 'pharmacy_paid': 0.0})
    member_months = {pid: set(info['months']) for pid, info in people.items()}
    timed = p['group_by'] in TIME_GROUPS
    key = (lambda pid, month: _time_key(month, p['group_by'])) if timed else (lambda pid, month: _group_key(p['group_by'], pid, people, attr))
    for pid, info in people.items():
        if not info['months']: continue
        if timed:
            for k in {key(pid, m) for m in info['months']}: groups[k]['members'] += 1
            for m in info['months']: groups[key(pid, m)]['member_months'] += 1
            continue
        g = groups[key(pid, None)]
        g['members'] += 1; g['member_months'] += len(info['months'])
    for pid, month, allowed, paid, kind, _ in claims:
        if month not in member_months.get(pid, ()): continue
        g = groups[key(pid, month)]
        g['allowed'] += allowed / 100; g['paid'] += paid / 100
        g['allowed_completed'] += allowed / 100 * completion.get(month, 1.0)
        if kind == 'pharmacy': g['pharmacy_allowed'] += allowed / 100; g['pharmacy_paid'] += paid / 100
    rows = []
    for k, g in groups.items():
        mm = g['member_months'] or 1
        rows.append({'group': k, 'members': g['members'], 'member_months': g['member_months'],
                     'allowed_pmpm': round(g['allowed'] / mm, 2), 'paid_pmpm': round(g['paid'] / mm, 2),
                     'allowed_pmpm_completed': round(g['allowed_completed'] / mm, 2),
                     'ibnr_estimate': round(g['allowed_completed'] - g['allowed'], 2),
                     'pharmacy_share': round(g['pharmacy_allowed'] / g['allowed'], 4) if g['allowed'] else None,
                     # medical (non-pharmacy) and pharmacy components, so 'medical PMPM' / 'pharmacy PMPM' answer exactly
                     'medical_allowed_pmpm': round((g['allowed'] - g['pharmacy_allowed']) / mm, 2),
                     'medical_paid_pmpm': round((g['paid'] - g['pharmacy_paid']) / mm, 2),
                     'pharmacy_allowed_pmpm': round(g['pharmacy_allowed'] / mm, 2),
                     'pharmacy_paid_pmpm': round(g['pharmacy_paid'] / mm, 2)})
    rows.sort(key=(lambda r: r['group']) if timed else (lambda r: -r['allowed_pmpm_completed']))
    return rows, ('Counted medical paid by service month plus net pharmacy payer deltas by event month, for enrolled member-months. '
                  f'Completion via chain-ladder on arrival lag (factors by months of runout: { {k: round(v, 3) for k, v in factors.items()} }).')


def risk_scores(db, people, start, end):
    """Internal risk score: Poisson GLM of allowed cost on age band, sex and chronic concepts, mean 1.0.
    Fitted on this population (not CMS-HCC); calibration by decile is reported."""
    concepts_by = defaultdict(set)
    for c, pid in db.execute('SELECT concept, patient_id FROM concept_patient WHERE kind = ?', ('condition',)):
        if c in CHRONIC: concepts_by[pid].add(c)
    claims = _claims(db, start, end)
    cost = defaultdict(float)
    for pid, month, allowed, _, _, _ in claims:
        if month in people.get(pid, {}).get('months', ()): cost[pid] += allowed / 100
    pids = [pid for pid, info in people.items() if info['months']]
    if not pids:return {},{'coefficients':{},'calibration_by_decile':[],'members':0,'status':'no_eligible_members'}
    bands = ['0-17', '18-44', '45-64', '65-74', '75+']
    cols = [f'age_{b}' for b in bands[1:]] + ['female'] + [f'has_{c}' for c in CHRONIC]
    X = np.array([[aggregate.age_band(people[p]['age']) == b for b in bands[1:]] + [people[p]['sex'] == 'F'] +
                  [c in concepts_by[p] for c in CHRONIC] for p in pids], dtype=float)
    X = np.hstack([np.ones((len(pids), 1)), X])
    exposure = np.array([len(people[p]['months']) / 12 for p in pids])
    y = np.array([cost[p] for p in pids]) / 1000  # thousands per year of exposure
    beta = np.zeros(X.shape[1]); beta[0] = math.log(max(y.sum() / exposure.sum(), 1e-6))
    for _ in range(50):  # IRLS with a small ridge for rare indicators
        mu = exposure * np.exp(np.clip(X @ beta, -20, 20))
        W = mu
        z = X @ beta + (y - mu) / np.maximum(mu, 1e-9)
        A = X.T @ (X * W[:, None]) + 1e-3 * np.eye(X.shape[1])
        new = np.linalg.solve(A, X.T @ (W * z))
        if np.max(np.abs(new - beta)) < 1e-8: beta = new; break
        beta = new
    pred = np.exp(X @ beta)
    score = pred / pred.mean()
    observed = y / np.maximum(exposure, 1e-9)
    order = np.argsort(score)
    deciles = []
    for i, chunk in enumerate(np.array_split(order, 10)):
        if not len(chunk):continue  # an empty quantile has no mean; never emit NaN calibration
        deciles.append({'decile': i + 1, 'mean_score': round(float(score[chunk].mean()), 3),
                        'observed_to_expected': round(float((y[chunk].sum()) / max((exposure[chunk] * pred[chunk]).sum(), 1e-9)), 3)})
    return ({p: float(s) for p, s in zip(pids, score)},
            {'coefficients': {name: round(float(b), 4) for name, b in zip(['intercept'] + cols, beta)}, 'calibration_by_decile': deciles,
             'members': len(pids)})


def _shift(day, days):
    return date.fromordinal(date.fromisoformat(day[:10]).toordinal() + days).isoformat()


def _risk_pairs(db, origin, group_by):
    """Risk-score cost predictions issued at `origin` (score x population cost per member-year, fitted on the year before)
    against each member's annualized allowed cost in the following year (members with >= 6 enrolled months then)."""
    start = _shift(origin, -365)
    people = eligibility(db, origin, start, origin)
    if sum(1 for i in people.values() if i['months']) < 20: return [], {}, 0.0
    attr = attribution(db, people, start, origin)
    scores, _ = risk_scores(db, people, start, origin)
    cost = defaultdict(float)
    for pid, month, allowed, _, _, _ in _claims(db, start, origin):
        if month in people.get(pid, {}).get('months', ()): cost[pid] += allowed / 100
    years = sum(len(people[p]['months']) for p in scores) / 12
    pmpy = sum(cost[p] for p in scores) / years if years else 0.0
    after, horizon = _shift(origin, 1), _shift(origin, 365)
    nxt = eligibility(db, horizon, after, horizon)
    cost_next = defaultdict(float)
    for pid, month, allowed, _, _, _ in _claims(db, after, horizon):
        if month in nxt.get(pid, {}).get('months', ()): cost_next[pid] += allowed / 100
    pairs = []
    for pid, s in scores.items():
        months = len(nxt.get(pid, {}).get('months', ()))
        if months >= 6:
            pairs.append({'patient_id': pid, 'pred': s * pmpy, 'y': cost_next[pid] * 12 / months,
                          'group': _group_key(group_by, pid, people, attr) if group_by != 'none' else aggregate.age_band(people[pid]['age'])})
    return pairs, scores, pmpy


def risk_calibration(db, end, group_by, alpha=0.1, min_group=30):
    """Mondrian split conformal for per-member next-year cost implied by the internal risk score.
    Score = |observed - predicted| / predicted. Live intervals are calibrated on predictions issued a year before `end`
    and scored on the following year; the backtest calibrates two years back and evaluates one year back."""
    level = 1 - alpha
    t1, t2 = _shift(end, -365), _shift(end, -730)
    by = group_by if group_by != 'none' else 'age_band'
    out = {'method': (f'Mondrian ({by}) split conformal on per-member next-year allowed cost predicted as risk score x population '
                      f'cost per member-year; score |observed - predicted| / predicted; members with >= 6 enrolled months in the outcome year. '
                      f'Live intervals calibrated on predictions issued {t1}; backtest calibrated on predictions issued {t2}, evaluated on {t1}.'),
           'level': level, 'target': 'allowed cost per member-year, next 12 months (USD)', 'available': False}
    live, _, _ = _risk_pairs(db, t1, group_by)
    if len(live) < 20:
        out['reason'] = f'only {len(live)} members with a prediction issued {t1} and >= 6 enrolled months after it (need 20)'
        out['statement'] = 'Conformal calibration unavailable: ' + out['reason'] + '.'
        return out
    arr = lambda pairs, k: np.array([q[k] for q in pairs], dtype=float)
    from . import conformal
    norm = lambda pairs: conformal.scores(arr(pairs, 'y'), arr(pairs, 'pred'), np.maximum(arr(pairs, 'pred'), 1.0))
    table = conformal.mondrian(norm(live), [q['group'] for q in live], alpha, min_group)
    out.update(available=True, calibration_pairs=len(live), _table=table,
               groups={str(g): {'q': round(v['q'], 4) if math.isfinite(v['q']) else None, 'calibration_cases': v['n'], 'pooled': v['pooled']}
                       for g, v in table['groups'].items()})
    earlier, _, _ = _risk_pairs(db, t2, group_by)
    if len(earlier) >= 20:
        bt = conformal.mondrian(norm(earlier), [q['group'] for q in earlier], alpha, min_group)
        pred, y = arr(live, 'pred'), arr(live, 'y')
        qs = np.array([conformal.mondrian_q(bt, q['group']) for q in live])
        lo, hi = conformal.interval(pred, qs, np.maximum(pred, 1.0), lower=0.0)
        hit = (y >= lo) & (y <= hi)
        groups = np.array([q['group'] for q in live], dtype=object)
        out.update(backtest_coverage=round(float(hit.mean()), 4), backtest_evaluation_pairs=len(live), backtest_calibration_pairs=len(earlier),
                   backtest_by_group={str(g): {'cases': int((groups == g).sum()), 'coverage': round(float(hit[groups == g].mean()), 4)}
                                      for g in sorted(set(groups.tolist()), key=str)})
        out['statement'] = conformal.describe_coverage(level, out['backtest_coverage'], len(live)) + ' in the time-split backtest'
    else:
        out['statement'] = f'Conformal {level:.0%} intervals calibrated on {len(live)} members; too little earlier history to backtest their coverage'
    out['statement'] += f' (per-member next-year cost from the risk score; Mondrian by {by}).'
    return out


def _risk_intervals(db, people, attr, p, start, end, scores, rows):
    """Add per-group conformal fields to risk rows in place; returns the public calibration receipt."""
    cal = risk_calibration(db, end, p['group_by'])
    if cal.get('available') and scores:
        cost = defaultdict(float)
        for pid, month, allowed, _, _, _ in _claims(db, start, end):
            if month in people.get(pid, {}).get('months', ()): cost[pid] += allowed / 100
        years = sum(len(people[x]['months']) for x in scores) / 12
        pmpy = sum(cost[x] for x in scores) / years if years else 0.0
        from . import conformal
        by_group = defaultdict(list)
        for pid, s in scores.items():
            g = _group_key(p['group_by'], pid, people, attr)
            pred = s * pmpy
            key = g if p['group_by'] != 'none' else aggregate.age_band(people[pid]['age'])
            lo, hi = conformal.interval([pred], conformal.mondrian_q(cal['_table'], key), [max(pred, 1.0)], lower=0.0)
            by_group[g].append((pred, float(lo[0]), float(hi[0])))
        for r in rows:
            got = by_group.get(r['group'])
            if not got: continue
            preds = np.array(got)
            r['predicted_cost_per_member_year'] = round(float(preds[:, 0].mean()), 2)
            if np.isfinite(preds[:, 2]).all():
                r['member_interval_90_median'] = [round(float(np.median(preds[:, 1])), 2), round(float(np.median(preds[:, 2])), 2)]
            bt = (cal.get('backtest_by_group') or {}).get(str(r['group']))
            if bt: r['backtest_coverage_90'] = bt['coverage']
            elif p['group_by'] == 'none' and cal.get('backtest_coverage') is not None: r['backtest_coverage_90'] = cal['backtest_coverage']
    return {k: v for k, v in cal.items() if not k.startswith('_')}


def _quality(db, people, attr, p, start, end, extra=None, as_of=None):
    """Internal measures (not certified HEDIS): A1c poor control, BP control, statin therapy, 7-day follow-up.
    A library measure id (config/agere/measures) is evaluated by measures.py instead."""
    if p.get('measure_ids'):
        from . import measures
        rows, method, more = measures.vbc_quality(db, people, attr, p, start, end, as_of or end)
        if extra is not None: extra.update(more)
        return rows, method
    has = defaultdict(set)
    for c, pid in db.execute("SELECT concept, patient_id FROM concept_patient WHERE concept IN ('diabetes','hypertension','coronary_artery_disease')"):
        has[c].add(pid)
    latest = {}
    for concept in ('hba1c', 'systolic_bp', 'diastolic_bp'):
        for pid, v in db.execute("SELECT patient_id, value_canonical FROM (SELECT patient_id, value_canonical, row_number() OVER "
                                 "(PARTITION BY patient_id ORDER BY event_date DESC, available_date DESC) rn FROM measurement "
                                 "WHERE concept=? AND value_canonical IS NOT NULL AND event_date BETWEEN ? AND ?) WHERE rn=1", (concept, start, end)):
            latest[(concept, pid)] = v
    statin = {pid for pid, payload in db.execute("SELECT patient_id, payload FROM record WHERE stream IN ('pharmacy_dispenses','pharmacy_claims') "
                                                 "AND event_date BETWEEN ? AND ?", (start, end))
              if STATIN.search(json.loads(payload).get('medication_name') or '')}
    measures = {
        'diabetes_a1c_poor_control': ('Diabetes 18-75: latest A1c > 9% or no A1c in period (lower is better)',
                                      lambda pid, i: pid in has['diabetes'] and i['age'] is not None and 18 <= i['age'] <= 75,
                                      lambda pid: latest.get(('hba1c', pid)) is None or latest[('hba1c', pid)] > 9),
        'bp_control': ('Hypertension 18-85: latest BP < 140/90 (higher is better)',
                       lambda pid, i: pid in has['hypertension'] and i['age'] is not None and 18 <= i['age'] <= 85,
                       lambda pid: (latest.get(('systolic_bp', pid)) or 999) < 140 and (latest.get(('diastolic_bp', pid)) or 999) < 90),
        'statin_therapy_diabetes': ('Diabetes 40-75: statin dispensed in period (higher is better)',
                                    lambda pid, i: pid in has['diabetes'] and i['age'] is not None and 40 <= i['age'] <= 75,
                                    lambda pid: pid in statin),
    }
    wanted = [p['measure']] if p.get('measure') in measures else list(measures)
    rows = []
    for name in wanted:
        label, den, num = measures[name]
        groups = defaultdict(lambda: {'denominator': 0, 'numerator': 0})
        for pid, info in people.items():
            if not info['months'] or not den(pid, info): continue
            g = groups[_group_key(p['group_by'], pid, people, attr)]
            g['denominator'] += 1; g['numerator'] += bool(num(pid))
        part = [{'measure': name, 'definition': label, 'group': k, **v,
                 'rate': round(v['numerator'] / v['denominator'], 4) if v['denominator'] else None} for k, v in groups.items()]
        _beta_binomial(part, 'numerator', 'denominator')
        for r in part:
            if 'shrunk_rate' in r: r['shrunk_rate'] = round(r['shrunk_rate'], 4); r['rate_ci95'] = [round(x, 4) for x in r['rate_ci95']]
        rows += sorted(part, key=lambda r: str(r['group']))
    return rows, 'Internal measure definitions on enrolled members (no official value sets in the received data); rates shrunk with beta-binomial.'


def validate_settlement_terms(t):
    """Validate financial inputs before either scalar settlement or simulated risk.

    A savings share does not determine a commercial loss share. Revenue caps
    require the actual revenue base; silently omitting one changes liability.
    """
    if not isinstance(t, dict): raise ValueError('Settlement terms must be an object')
    rates = ('sharing_rate', 'loss_rate', 'quality_score', 'savings_cap_pct', 'loss_cap_pct',
             'loss_cap_revenue_pct', 'discount_pct', 'quality_withhold_pct', 'loss_rate_min', 'loss_rate_max')
    for name in (*rates, 'msr', 'mlr', 'benchmark_pmpm', 'participant_revenue'):
        if name not in t: continue
        v = t[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ValueError(f'Settlement {name} must be a finite nonnegative number')
        if name in rates and v > 1: raise ValueError(f'Settlement {name} must be a fraction between 0 and 1')
        if name in ('msr', 'mlr') and v >= 1: raise ValueError(f'Settlement {name} must be a fraction below 1')
        if name == 'benchmark_pmpm' and v == 0: raise ValueError('Settlement benchmark_pmpm must be positive')
    if 'two_sided' in t and type(t['two_sided']) is not bool:
        raise ValueError('Settlement two_sided must be true or false')
    if t.get('quality_mode', 'sharing_multiplier') not in ('sharing_multiplier', 'withhold'):
        raise ValueError('Unknown settlement quality_mode')
    if not t.get('corridors') and 'sharing_rate' not in t:
        raise ValueError('Settlement needs supplied sharing_rate or risk corridors')
    if t.get('two_sided') and not t.get('corridors') and 'loss_rate' not in t:
        raise ValueError('Downside risk needs supplied loss_rate; it cannot be inferred from sharing_rate')
    if t.get('two_sided') and t.get('loss_cap_revenue_pct') is not None and 'participant_revenue' not in t:
        raise ValueError('Downside risk needs supplied participant_revenue to apply the revenue-based loss cap')
    if t.get('corridors'):
        if not isinstance(t['corridors'], list): raise ValueError('Risk corridors must be a list')
        previous = 0.0
        for band in t['corridors']:
            if not isinstance(band, dict) or not {'from', 'to', 'rate'} <= set(band):
                raise ValueError('Risk corridors need from, to and rate')
            lo, hi, rate = band['from'], band['to'], band['rate']
            for value in (lo, rate, *(() if hi is None else (hi,))):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('Risk corridor values must be finite numbers')
            if previous is None or lo != previous or not 0 <= rate <= 1 or (hi is not None and hi <= lo):
                raise ValueError('Risk corridors must be ordered, contiguous, nonoverlapping bands starting at zero')
            previous = hi
    if effective_benchmark(1.0, t) <= 0:
        raise ValueError('Program adjustments must leave a positive benchmark')


def effective_benchmark(benchmark, t):
    """Benchmark after a program discount (e.g. ACO REACH Global) and any quality withhold not earned back."""
    withhold = float(t.get('quality_withhold_pct') or 0)
    earned = float(t.get('quality_score', 1.0)) if t.get('quality_mode') == 'withhold' else 1.0
    return benchmark * (1 - float(t.get('discount_pct') or 0) - withhold * (1 - earned))


def _corridor_share(pct, corridors):
    """Share of a savings/loss percentage under tiered risk corridors [{'from','to','rate'}] (marginal bands)."""
    return sum(float(c['rate']) * max(0.0, min(pct, float(c['to']) if c.get('to') is not None else pct) - float(c['from'])) for c in corridors)


def settle(actual, benchmark, mm, t):
    """Net settlement for one actual PMPM: positive = paid to the ACO, negative = owed by it."""
    validate_settlement_terms(t)
    for name, value in (('actual PMPM', actual), ('benchmark PMPM', benchmark), ('member-months', mm)):
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)) or not math.isfinite(value) or value < 0:
            raise ValueError(f'Settlement {name} must be a finite nonnegative number')
    if not benchmark: raise ValueError('Settlement benchmark PMPM must be positive')
    if not mm: return 0.0
    benchmark = effective_benchmark(benchmark, t)
    savings_pct = (benchmark - actual) / benchmark
    total = (benchmark - actual) * mm
    default_msr = 0.0 if t.get('corridors') else 0.02
    msr, mlr = float(t.get('msr', default_msr)), float(t.get('mlr', t.get('msr', default_msr)))
    quality = float(t.get('quality_score', 1.0)) if t.get('quality_mode', 'sharing_multiplier') == 'sharing_multiplier' else 1.0
    if savings_pct >= msr and savings_pct > 0:
        if t.get('corridors'): shared = benchmark * mm * _corridor_share(savings_pct, t['corridors']) * quality
        else: shared = total * float(t['sharing_rate']) * quality
        if t.get('savings_cap_pct') is not None: shared = min(shared, benchmark * mm * float(t['savings_cap_pct']))
        return shared
    if -savings_pct >= mlr and savings_pct < 0 and t.get('two_sided'):
        if t.get('corridors'): owed = benchmark * mm * _corridor_share(-savings_pct, t['corridors'])
        else: owed = -total * float(t['loss_rate'])
        if t.get('loss_cap_pct') is not None: owed = min(owed, benchmark * mm * float(t['loss_cap_pct']))
        if t.get('loss_cap_revenue_pct') is not None and t.get('participant_revenue') is not None:
            owed = min(owed, float(t['participant_revenue']) * float(t['loss_cap_revenue_pct']))
        return -owed
    return 0.0


def settlement_explanation(actual, benchmark, mm, t):
    """Auditable scenario arithmetic; no inference of payment entitlement or causal savings."""
    net = settle(actual, benchmark, mm, t)
    adjusted = effective_benchmark(benchmark, t)
    gross = (adjusted - actual) * mm
    default = 0.0 if t.get('corridors') else 0.02
    assumptions = list((t.get('program') or {}).get('assumptions') or [])
    if 'two_sided' not in t: assumptions.append('Upside-only scenario assumed; downside liability was not supplied.')
    if 'quality_score' not in t: assumptions.append('Full quality credit assumed for this scenario.')
    if 'msr' not in t: assumptions.append(f'Minimum savings rate assumed at {default:.1%}.')
    if t.get('two_sided') and 'mlr' not in t: assumptions.append('Minimum loss rate assumed equal to the minimum savings rate.')
    return {'basis': 'scenario_on_supplied_terms_not_cms_reconciliation',
            'benchmark_dollars': round(adjusted * mm, 2), 'expenditure_dollars': round(actual * mm, 2),
            'gross_savings_or_loss': round(gross, 2), 'net_payment_to_aco': round(net, 2),
            'savings_hurdle_dollars': round(adjusted * mm * t.get('msr', default), 2),
            'loss_hurdle_dollars': round(adjusted * mm * t.get('mlr', t.get('msr', default)), 2) if t.get('two_sided') else None,
            'threshold_method': 'marginal_risk_bands' if t.get('corridors') else 'first_dollar_after_threshold',
            'downside_exposure': bool(t.get('two_sided')), 'assumptions': assumptions,
            'causal_savings_established': False, 'payment_entitlement_established': False}


def settlement_risk(db, people, start, end, benchmark, t, draws=4000, seed=7):
    """Value-at-risk for the settlement: resample members (nonparametric bootstrap of member-level completed
    allowed cost and member-months), run each draw through the exact contract arithmetic, and summarise the
    distribution of the net payment. Captures population cost variability (high-cost claimants), not
    benchmark or rule changes."""
    claims = _claims(db, start, end)
    completion, _ = _completion(claims, end)
    months = {pid: set(info['months']) for pid, info in people.items() if info['months']}
    cost = defaultdict(float)
    for pid, month, allowed, _, _, _ in claims:
        if month in months.get(pid, ()): cost[pid] += allowed / 100 * completion.get(month, 1.0)
    pids = sorted(months)
    if not pids: return None
    c = np.array([cost[x] for x in pids]); m = np.array([len(months[x]) for x in pids], dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(pids), size=(draws, len(pids)))
    pmpm = c[idx].sum(axis=1) / m[idx].sum(axis=1)
    mm = float(m.sum())
    net = np.array([settle(x, benchmark, mm, t) for x in pmpm])
    q = lambda a, v: round(float(np.quantile(a, v)), 2)
    # Benchmark at which the expected settlement crosses from loss/corridor into earned savings.
    breakeven = round(float(np.quantile(pmpm, 0.5)) / (1 - float(t.get('msr', 0.02))), 2)
    return {'draws': draws, 'actual_pmpm_p5': q(pmpm, .05), 'actual_pmpm_p50': q(pmpm, .5), 'actual_pmpm_p95': q(pmpm, .95),
            'p_earn_savings': round(float((net > 0).mean()), 3), 'p_owe_loss': round(float((net < 0).mean()), 3),
            'p_within_corridor': round(float((net == 0).mean()), 3), 'expected_net_payment': round(float(net.mean()), 2),
            'net_payment_p5': q(net, .05), 'net_payment_p50': q(net, .5), 'net_payment_p95': q(net, .95),
            'value_at_risk_95': round(min(0.0, float(np.quantile(net, .05))), 2),
            'benchmark_pmpm_needed_for_median_to_clear_msr': breakeven,
            'top_1pct_members_share_of_cost': round(float(np.sort(c)[::-1][:max(1, len(c) // 100)].sum() / c.sum()), 3) if c.sum() else None}


def shapley_allocation(db, people, attr, start, end, benchmark, t, group_by='practice', max_players=12):
    """Fair split of the settlement across groups (exact Shapley values): each group's average marginal
    effect on the pooled settlement over every order of joining. Because the MSR corridor, sharing rate
    and caps make settlement non-additive, this differs from a pro-rata split; it sums exactly to the
    pooled settlement."""
    from itertools import combinations
    from math import factorial
    claims = _claims(db, start, end)
    completion, _ = _completion(claims, end)
    cost, mm = defaultdict(float), defaultdict(float)
    months = {pid: set(info['months']) for pid, info in people.items() if info['months']}
    key = {pid: _group_key(group_by, pid, people, attr) for pid in months}
    for pid in months: mm[key[pid]] += len(months[pid])
    for pid, month, allowed, _, _, _ in claims:
        if month in months.get(pid, ()): cost[key[pid]] += allowed / 100 * completion.get(month, 1.0)
    players = sorted(mm)
    if not players or len(players) > max_players: return None
    def v(coalition):
        m = sum(mm[g] for g in coalition)
        return settle(sum(cost[g] for g in coalition) / m, benchmark, m, t) if m else 0.0
    n = len(players); value = {}
    for size in range(n + 1):
        for c in combinations(players, size): value[frozenset(c)] = v(c)
    out = []
    for g in players:
        phi = 0.0
        for coal, val in value.items():
            if g in coal: continue
            w = factorial(len(coal)) * factorial(n - len(coal) - 1) / factorial(n)
            phi += w * (value[coal | {g}] - val)
        alone = v((g,))
        out.append({'group': g, 'member_months': mm[g], 'pmpm': round(cost[g] / mm[g], 2), 'shapley_share': round(phi, 2),
                    'standalone_settlement': round(alone, 2),
                    'pro_rata_share': None})
    total = value[frozenset(players)]
    for r in out: r['pro_rata_share'] = round(total * r['member_months'] / sum(mm.values()), 2)
    return {'group_by': group_by, 'pooled_settlement': round(total, 2), 'allocation': sorted(out, key=lambda r: -r['shapley_share'])}


def _shared_savings(db, people, attr, p, start, end):
    """Exact settlement arithmetic on supplied terms. Benchmark is risk-adjusted by the population's
    mean internal risk score relative to 1.0 when terms ask for it."""
    t = p['terms']
    cost_rows, cost_method = _cost(db, people, attr, {**p, 'group_by': 'none'}, start, end)
    c = cost_rows[0] if cost_rows else {'member_months': 0, 'allowed_pmpm_completed': 0}
    mm = c['member_months']
    actual = c['allowed_pmpm_completed']
    benchmark = float(t['benchmark_pmpm'])
    if t.get('risk_adjust'):
        scores, _ = risk_scores(db, people, start, end)
        benchmark *= float(np.mean(list(scores.values()))) if scores else 1.0
    if t.get('msr_schedule') and 'msr' not in t:  # program MSR/MLR that varies with the assigned population
        from . import programs
        rate = programs.variable_msr(sum(1 for i in people.values() if i['months']), t['msr_schedule'])
        t = {**t, 'msr': rate, **({'mlr': rate} if 'mlr' not in t else {})}
    adjusted = effective_benchmark(benchmark, t)
    savings_pct = (adjusted - actual) / adjusted if adjusted else 0
    total = (adjusted - actual) * mm
    result = {'member_months': mm, 'benchmark_pmpm': round(benchmark, 2), 'actual_pmpm_completed': actual,
              'savings_pct': round(savings_pct, 4), 'gross_savings_or_loss': round(total, 2), 'terms': t}
    if adjusted != benchmark: result['benchmark_after_program_adjustments_pmpm'] = round(adjusted, 2)
    net = settle(actual, benchmark, mm, t)
    result['settlement_explanation'] = settlement_explanation(actual, benchmark, mm, t)
    if net > 0: result.update(outcome='shared_savings_earned', payment_to_aco=round(net, 2))
    elif net < 0: result.update(outcome='shared_loss_owed', payment_from_aco=round(-net, 2))
    else: result.update(outcome='no_exposure' if not mm else 'upside_only_no_loss' if savings_pct < 0 and not t.get('two_sided') else 'within_corridor', payment_to_aco=0.0)
    result['settlement_risk'] = settlement_risk(db, people, start, end, benchmark, t)
    result['fair_allocation'] = shapley_allocation(db, people, attr, start, end, benchmark, t,
                                                   group_by=p['group_by'] if p.get('group_by') in ('practice', 'pcp', 'coverage_type') else 'practice')
    return [result], ('Settlement on supplied terms: savings % vs MSR/MLR corridor, sharing/loss rates, quality factor and caps; '
                      'actual cost = completion-adjusted allowed PMPM for enrolled member-months. Settlement risk: 4,000-draw member '
                      'bootstrap through the same contract arithmetic (population cost variability; benchmark and rule changes not modelled). '
                      + cost_method + (' ' + _program_note(t) if t.get('program') else ''))


def _program_note(t):
    from . import programs
    return programs.describe_terms(t)


def _measure_by_segment(db, measure, group_by, start, end, keep):
    """(segment -> (exposure, numerator)) for one period. Exposure = member-months (pmpm) or member-years."""
    people = eligibility(db, end, start, end)
    if keep is not None: people = {k: v for k, v in people.items() if k in keep}
    attr = attribution(db, people, start, end)
    out = defaultdict(lambda: [0.0, 0.0])
    for pid, info in people.items():
        if info['months']:
            out[_group_key(group_by, pid, people, attr)][0] += len(info['months']) / (1 if measure == 'pmpm' else 12)
    months = {pid: set(i['months']) for pid, i in people.items()}
    if measure == 'pmpm':
        claims = _claims(db, start, end)
        completion, _ = _completion(claims, end)
        for pid, month, allowed, *_ in claims:
            if month in months.get(pid, ()): out[_group_key(group_by, pid, people, attr)][1] += allowed / 100 * completion.get(month, 1.0)
    else:
        if measure == 'ed_rate':
            from .ed_definition import visits_sql  # the one governed ED definition
            rows = db.execute(f"SELECT DISTINCT patient_id, event_date FROM ({visits_sql(db)}) WHERE event_date BETWEEN ? AND ?", (start, end))
        else:
            rows = db.execute("SELECT patient_id, admit_date FROM stay WHERE admit_date BETWEEN ? AND ?", (start, end))
        for pid, d in rows:
            if d and d[:7] in months.get(pid, ()): out[_group_key(group_by, pid, people, attr)][1] += 1
    return out


def _feed_checks(db, measure, a, b):
    """Did the data feeding the measure change? Monthly record volume per source, CUSUM vs period A."""
    from ...process_monitoring import monitor
    stream = {'pmpm': 'medical_claims', 'ed_rate': 'encounters', 'admit_rate': 'encounters'}[measure]
    counts = defaultdict(lambda: defaultdict(int))
    for org, month, n in db.execute("SELECT organization_id, substr(event_date,1,7), count(*) FROM record WHERE stream=? AND event_date BETWEEN ? AND ? GROUP BY 1,2",
                                    (stream, a['start'], b['end'])):
        counts[org][month] = n
    months_a, months_b = _months(a['start'], a['end']), _months(b['start'], b['end'])
    alerts = []
    for org, series in counts.items():
        ref = [series.get(m, 0) for m in months_a]
        if len(ref) < 3 or sum(ref) < 30: continue
        mean, sd = float(np.mean(ref)), float(max(np.std(ref), math.sqrt(max(np.mean(ref), 1))))
        res = monitor([float(series.get(m, 0)) for m in months_b], reference_mean=mean, reference_sd=sd)
        flagged = [months_b[pt['index']] for pt in res['points'] if pt['investigate_source_change']]
        if flagged:
            alerts.append({'source': org, 'stream': stream, 'reference_monthly_mean': round(mean, 1),
                           'period_b_monthly_mean': round(float(np.mean([series.get(m, 0) for m in months_b])), 1), 'first_flagged_month': flagged[0]})
    # Event-type gaps: the measured event vanishes while its stream keeps flowing (coding/feed change, not care).
    if measure in ('ed_rate', 'admit_rate'):
        from .ed_definition import visits_sql  # the one governed ED definition
        sql = (f"SELECT substr(event_date,1,7), count(DISTINCT patient_id || event_date) FROM ({visits_sql(db)}) "
               "WHERE event_date BETWEEN ? AND ? GROUP BY 1" if measure == 'ed_rate' else
               "SELECT substr(admit_date,1,7), count(*) FROM stay WHERE admit_date BETWEEN ? AND ? GROUP BY 1")
        events = dict(db.execute(sql, (a['start'], b['end'])))
        totals = dict(db.execute("SELECT substr(event_date,1,7), count(*) FROM record WHERE stream=? AND event_date BETWEEN ? AND ? GROUP BY 1",
                                 (stream, a['start'], b['end'])))
        for label, months in (('period_a', months_a), ('period_b', months_b)):
            silent = [m for m in months if totals.get(m, 0) > 100 and events.get(m, 0) == 0]
            if len(silent) >= 3:
                alerts.append({'source': 'all', 'stream': stream, 'kind': 'event_type_gap', 'period': label,
                               'months_without_events': len(silent), 'of_months': len(months),
                               'detail': f'{len(silent)} of {len(months)} months in {label} record {stream} volume but none of this event type: '
                                         'likely a coding or feed change, not a change in care.'})
    return alerts


def _change(db, p, as_of, keep):
    """Kitagawa decomposition of a rate change into segment mix and within-segment rate effects, plus feed checks."""
    end_b = (p['compare'].get('b') or {}).get('end') or as_of
    b = p['compare'].get('b') or {'start': f'{int(end_b[:4]) - 1}{end_b[4:7]}-01', 'end': end_b}
    a = p['compare'].get('a') or {'start': f'{int(b["start"][:4]) - 1}{b["start"][4:]}', 'end': f'{int(b["start"][:4]) - 1}{b["start"][4:7]}-01'}
    if not p['compare'].get('a'):
        a_end = date.fromordinal(date.fromisoformat(b['start']).toordinal() - 1).isoformat()
        a = {'start': f'{int(b["start"][:4]) - 1}{b["start"][4:]}', 'end': a_end}
    group_by = p['group_by'] if p['group_by'] != 'none' else 'coverage_type'
    A = _measure_by_segment(db, p['measure'], group_by, a['start'], a['end'], keep)
    B = _measure_by_segment(db, p['measure'], group_by, b['start'], b['end'], keep)
    scale = 1.0 if p['measure'] == 'pmpm' else 1000.0
    ea, eb = sum(x[0] for x in A.values()), sum(x[0] for x in B.values())
    total_a = scale * sum(x[1] for x in A.values()) / ea if ea else 0
    total_b = scale * sum(x[1] for x in B.values()) / eb if eb else 0
    rows, rate_eff, mix_eff = [], 0.0, 0.0
    for seg in sorted(set(A) | set(B)):
        xa, xb = A.get(seg, [0, 0]), B.get(seg, [0, 0])
        wa, wb = (xa[0] / ea if ea else 0), (xb[0] / eb if eb else 0)
        ra, rb = (scale * xa[1] / xa[0] if xa[0] else 0), (scale * xb[1] / xb[0] if xb[0] else 0)
        r_eff, m_eff = (rb - ra) * (wa + wb) / 2, (wb - wa) * (ra + rb) / 2
        rate_eff += r_eff; mix_eff += m_eff
        rows.append({'segment': seg, 'weight_a': round(wa, 4), 'weight_b': round(wb, 4), 'rate_a': round(ra, 2), 'rate_b': round(rb, 2),
                     'rate_effect': round(r_eff, 2), 'mix_effect': round(m_eff, 2)})
    rows.sort(key=lambda r: -abs(r['rate_effect']) - abs(r['mix_effect']))
    feeds = _feed_checks(db, p['measure'], a, b)
    summary = {'measure': p['measure'], 'period_a': a, 'period_b': b, 'value_a': round(total_a, 2), 'value_b': round(total_b, 2),
               'change': round(total_b - total_a, 2), 'rate_effect': round(rate_eff, 2), 'mix_effect': round(mix_eff, 2),
               'segments_by': group_by, 'feed_alerts': feeds,
               'reading': (next((f['detail'] for f in feeds if f.get('kind') == 'event_type_gap'), None) or
                           ('Feed volume changed in ' + ', '.join(f["source"] for f in feeds[:3]) + '; part of the change may be data, not care.')
                           if feeds else 'No source-volume change detected in the feeding stream.')}
    return [summary] + rows, ('Kitagawa decomposition: change = sum over segments of rate change x average weight (rate effect) + '
                              'weight change x average rate (mix effect). Feed check: CUSUM on monthly record volume per source vs period A.')


def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = _connect(path)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        require_demographics(db, p['group_by'])
        start, end = _period(p, as_of)
        people = eligibility(db, as_of, start, end)
        if patient_ids is not None:
            keep = set(patient_ids); people = {k: v for k, v in people.items() if k in keep}
        attr = attribution(db, people, start, end, p['attribution'])
        metric = p['metric']
        calibration = None
        extra = {}  # measure-library report fields (declines, care gaps, citations)
        if metric == 'eligibility':
            groups = defaultdict(lambda: {'members': 0, 'member_months': 0})
            for pid, info in people.items():
                if info['months']:
                    g = groups[_group_key(p['group_by'], pid, people, attr)]; g['members'] += 1; g['member_months'] += len(info['months'])
            rows = [{'group': k, **v} for k, v in sorted(groups.items())]
            method = 'Active, insured coverage from effective start through the period end (no termination dates are received).'
            if any(i.get('enrollment_basis') == 'enrollment_span' for i in people.values()):
                method = ('Member months from payer enrollment spans (any covered day in the month; terminations respected) for '
                          'patients with spans; others: ' + method[0].lower() + method[1:])
        elif metric == 'attribution':
            groups = defaultdict(lambda: {'members': 0, 'assigned_pcp': 0, 'visit_plurality': 0, 'unattributed': 0, 'agrees_with_plurality': 0})
            for pid, a in attr.items():
                if not people[pid]['months']: continue
                g = groups[_group_key(p['group_by'] if p['group_by'] != 'none' else 'practice', pid, people, attr)]
                k = a['method'] if a['method'] != 'none' else 'unattributed'
                g['members'] += 1; g[k] = g.get(k, 0) + 1; g['agrees_with_plurality'] += a['agrees_with_plurality']
            rows = sorted(({'group': k, **v} for k, v in groups.items()), key=lambda r: -r['members'])
            method = f"{p['attribution']} attribution: assigned PCP relationship, else plurality of primary-care visits in the period."
            if any(a['method'] == 'payer_attribution' for a in attr.values()):
                method = method[:-1] + '; payer attribution from the enrollment span takes precedence where received.'
        elif metric == 'utilization':
            rows, method = _utilization(db, people, attr, p, start, end)
        elif metric == 'cost':
            rows, method = _cost(db, people, attr, p, start, end)
        elif metric == 'risk':
            scores, fit = risk_scores(db, people, start, end)
            groups = defaultdict(list)
            for pid, s in scores.items(): groups[_group_key(p['group_by'], pid, people, attr)].append(s)
            rows = sorted(({'group': k, 'members': len(v), 'mean_risk_score': round(float(np.mean(v)), 3)} for k, v in groups.items()),
                          key=lambda r: -r['mean_risk_score'])
            method = 'Internal risk score (Poisson GLM on age band, sex, chronic conditions; mean 1.0; not CMS-HCC). Fit: ' + json.dumps(fit['calibration_by_decile'][:3]) + '...'
            calibration = _risk_intervals(db, people, attr, p, start, end, scores, rows)
            method += f" Intervals: {calibration['method']} {calibration['statement']}"
        elif metric == 'quality':
            rows, method = _quality(db, people, attr, p, start, end, extra, as_of)
        elif metric == 'change':
            rows, method = _change(db, p, as_of, set(patient_ids) if patient_ids is not None else None)
        else:
            rows, method = _shared_savings(db, people, attr, p, start, end)
    finally:
        db.close()
    return {'schema': VERSION, 'metric': p['metric'], 'parameters': p, 'period': {'start': start, 'end': end},
            'rows': rows, 'method': method, 'members_in_scope': sum(1 for i in people.values() if i['months']),
            **({'conformal': calibration} if calibration is not None else {}), **extra}
