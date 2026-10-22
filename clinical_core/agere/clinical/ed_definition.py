"""One governed emergency-department visit definition across EHR, ADT and claims, with availability status.

Every ED count, rate, list and index event reads this definition (relation_sql / events), so EHR, ADT and claims never
disagree about what an ED visit is:

  sources   EHR      encounter.type_concept = 'emergency_visit' from the EHR encounter feed (source_table 'encounters'),
                     not cancelled / entered in error
            ADT      adt_visit with patient class E at registration or at the final class (HL7 PV1-2 / A08 updates)
            claims   payer claims and the client's own billing claims that count in metrics and are not voided (HOS-09:
                     a denied claim, incl. a CO-18 duplicate without its original, still proves the visit), with place of
                     service 23, a revenue code 045x / 0981 on a line or the claim, or an ED E/M CPT 99281-99285
  grain     one visit per patient per calendar day (an EHR encounter, its ADT registration and the professional and
            facility claims of the same day are one visit)
  admitted  the visit resulted in admission: ADT final class I or an inpatient bill (TOB 011x) on the same claim
  classes   'ed'          at least one source states an ED visit outright
            'unresolved'  only an ADT registration whose class changed between E and I/O, with no EHR or claim ED evidence:
                          ED visit vs direct admission cannot be decided from the received data (reported, not counted)

Availability, stated on every answer (answer['availability'], answer['ed_status']):
  observed               ED visits found
  true_zero              no ED visit, and at least one ED-capable source (EHR encounters, ADT, claims) was delivered for the
                         period and covers the patients asked about
  source_not_delivered   no ED-capable source covers the period (or the patients asked about): the zero is not a count
  definition_unresolved  only unresolved registrations: no visit can be confirmed or ruled out
Synthetic data only; no model calls.
"""
import datetime as dt
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

VERSION = 'agere-ed-definition/1'
NAME = 'Agere all-source ED visit (EHR + ADT + claims, one per patient-day)'
BASIS = ('EHR emergency encounters, ADT patient class E (registration or final), and non-voided claims counting in metrics '
         'with POS 23, revenue 045x/0981 or CPT 99281-99285 (denied claims count: they prove the visit); one visit per '
         'patient per day; visits whose only evidence is an ADT class change E<->I/O are unresolved and not counted.')
ED_CPT = ('99281', '99282', '99283', '99284', '99285')
INACTIVE = ('cancelled', 'canceled', 'entered-in-error', 'entered_in_error', 'no_show', 'noshow')
STATUSES = ('observed', 'true_zero', 'source_not_delivered', 'definition_unresolved')
OUTPUTS = ('summary', 'visits', 'patients', 'by_month')
GROUP_BY = ('none', 'practice', 'pcp', 'payer', 'source')
ADMITTED = ('any', 'exclude', 'only')


def _q(v): return "'" + str(v).replace("'", "''") + "'"


def _rev(col):
    return f"({col} LIKE '045_' OR {col} LIKE '45_' OR {col} IN ('0981', '981'))"


def relation_sql(tables):
    """The governed ED relation over the semantic layer: (patient_id, event_date, encounter_id, sources, principal_icd10,
    resulted_in_admission, classification, source_records). tables: {table: set(columns)} (request_plan Catalog.tables or
    columns(db)). Sources the layer does not carry are left out (availability says so)."""
    t = lambda name: set(tables.get(name) or ())
    parts = []
    enc, visit = t('encounter'), t('visit')
    if {'patient_id', 'event_date', 'type_concept'} <= enc:
        src = " AND e.source_table = 'encounters'" if 'source_table' in enc else ''
        st = f" AND lower(coalesce(e.status, '')) NOT IN ({', '.join(map(_q, INACTIVE))})" if 'status' in enc else ''
        dx, join = ('v.principal_icd10', " LEFT JOIN visit v ON v.type_concept = 'emergency_visit' AND v.patient_id = e.patient_id AND v.encounter_id = e.encounter_id") if \
            {'encounter_id', 'principal_icd10'} <= visit and 'encounter_id' in enc else ('NULL', '')  # join through the visit_concept index
        eid = 'e.encounter_id' if 'encounter_id' in enc else 'e.record_id'
        parts.append(f"SELECT e.patient_id, substr(e.event_date,1,10) AS event_date, {eid} AS encounter_id, 'ehr' AS source, "
                     f"{dx} AS principal_icd10, 0 AS admitted, 1 AS ed_signal FROM encounter e{join} "
                     f"WHERE e.type_concept = 'emergency_visit' AND e.event_date IS NOT NULL{src}{st}")
    if {'patient_id', 'event_date', 'type_concept'} <= visit:  # the deduplicated EHR visits too (a layer may carry only visit)
        st = f" AND lower(coalesce(status, '')) NOT IN ({', '.join(map(_q, INACTIVE))})" if 'status' in visit else ''
        parts.append(f"SELECT patient_id, substr(event_date,1,10) AS event_date, {'encounter_id' if 'encounter_id' in visit else 'NULL'} AS encounter_id, "
                     f"'ehr' AS source, {'principal_icd10' if 'principal_icd10' in visit else 'NULL'} AS principal_icd10, 0 AS admitted, "
                     f"1 AS ed_signal FROM visit WHERE type_concept = 'emergency_visit' AND event_date IS NOT NULL{st}")
    adt = t('adt_visit')
    if {'patient_id', 'admit_date', 'patient_class'} <= adt:
        fin = 'final_patient_class' if 'final_patient_class' in adt else 'patient_class'
        a, f = f"upper(substr(coalesce(patient_class, ''),1,1))", f"upper(substr(coalesce({fin}, ''),1,1))"
        parts.append(f"SELECT patient_id, substr(admit_date,1,10), {'visit_number' if 'visit_number' in adt else 'record_id'}, 'adt', NULL, "
                     f"CASE WHEN {f} = 'I' THEN 1 ELSE 0 END, CASE WHEN ({a} = 'E' AND {f} IN ('E', '')) OR ({f} = 'E' AND {a} IN ('E', '')) "
                     f"THEN 1 ELSE 0 END FROM adt_visit WHERE ({a} = 'E' OR {f} = 'E') AND admit_date IS NOT NULL")
    pc, pl = t('payer_claim'), t('payer_claim_line')
    if {'patient_id', 'service_from', 'payer_claim_id'} <= pc:
        ok = ' AND '.join(x for x in ["coalesce(c.counts_in_metrics, 1) = 1" if 'counts_in_metrics' in pc else '',
                                     "coalesce(c.final_status, '') <> 'voided'" if 'final_status' in pc else ''] if x) or '1'
        ed = [f"c.place_of_service = '23'"] if 'place_of_service' in pc else []
        line = [x for x in [_rev('l.revenue_code') if 'revenue_code' in pl else '',
                            f"l.cpt IN ({', '.join(map(_q, ED_CPT))})" if 'cpt' in pl else ''] if x]
        if line and 'payer_claim_id' in pl:
            ed.append(f"c.payer_claim_id IN (SELECT l.payer_claim_id FROM payer_claim_line l WHERE {' OR '.join(line)})")
        if ed:
            adm = "CASE WHEN c.type_of_bill LIKE '011%' THEN 1 ELSE 0 END" if 'type_of_bill' in pc else '0'
            dx = 'c.principal_diagnosis' if 'principal_diagnosis' in pc else 'NULL'
            parts.append(f"SELECT c.patient_id, substr(c.service_from,1,10), c.payer_claim_id, 'claim', {dx}, {adm}, 1 FROM payer_claim c "
                         f"WHERE {ok} AND c.service_from IS NOT NULL AND ({' OR '.join(ed)})")
    cl = t('claim')
    if {'patient_id', 'service_date', 'claim_id'} <= cl:
        ed = [x for x in ["c.place_of_service = '23'" if 'place_of_service' in cl else '', _rev('c.revenue_code') if 'revenue_code' in cl else '',
                          f"c.cpt IN ({', '.join(map(_q, ED_CPT))})" if 'cpt' in cl else ''] if x]
        if ed:
            ok = "coalesce(c.final_status, '') <> 'voided'" if 'final_status' in cl else '1'
            # the client's own billing; payer claims copied into claim are read above with their metric flags
            if 'claim_source' in cl and {'payer_claim_id', 'service_from'} <= pc: ok += " AND coalesce(c.claim_source, '') <> 'payer_medical_claims'"
            if 'counts_in_metrics' in pc: ok += ' AND c.claim_id NOT IN (SELECT payer_claim_id FROM payer_claim WHERE counts_in_metrics = 0)'
            adm = "CASE WHEN c.type_of_bill LIKE '011%' THEN 1 ELSE 0 END" if 'type_of_bill' in cl else '0'
            dx = 'c.principal_diagnosis' if 'principal_diagnosis' in cl else 'NULL'
            parts.append(f"SELECT c.patient_id, substr(c.service_date,1,10), c.claim_id, 'claim', {dx}, {adm}, 1 FROM claim c "
                         f"WHERE {ok} AND c.service_date IS NOT NULL AND ({' OR '.join(ed)})")
    if not parts:
        return ("(SELECT NULL AS patient_id, NULL AS event_date, NULL AS encounter_id, NULL AS sources, NULL AS principal_icd10, "
                "0 AS resulted_in_admission, NULL AS classification, 0 AS source_records WHERE 0)")
    return ("(SELECT patient_id, event_date, min(encounter_id) AS encounter_id, group_concat(DISTINCT source) AS sources, "
            "coalesce(max(CASE WHEN source = 'ehr' THEN principal_icd10 END), max(principal_icd10)) AS principal_icd10, "
            "max(admitted) AS resulted_in_admission, CASE WHEN max(ed_signal) = 1 THEN 'ed' ELSE 'unresolved' END AS classification, "
            "count(*) AS source_records FROM (" + ' UNION ALL '.join(parts) + ") GROUP BY patient_id, event_date)")


def visits_sql(db):
    """SELECT patient_id, event_date (one row per governed ED visit) for engines that count ED days themselves."""
    return f"SELECT patient_id, event_date FROM {relation_sql(columns(db))} WHERE classification = 'ed'"


def columns(db):
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")]
    return {n: {r[1] for r in db.execute(f'PRAGMA table_info("{n}")')} for n in names}


def events(db, start=None, end=None, keep=None, *, include_unresolved=False, tables=None):
    """Governed ED visits in [start, end] -> [{patient_id, day, end_day, encounter_id, sources, principal, admitted,
    classification}] (the row shape default_definitions and measures read)."""
    sql = f"SELECT patient_id, event_date, encounter_id, sources, principal_icd10, resulted_in_admission, classification FROM {relation_sql(tables or columns(db))} WHERE 1"
    args = []
    if start: sql += ' AND event_date >= ?'; args.append(str(start)[:10])
    if end: sql += ' AND event_date <= ?'; args.append(str(end)[:10])
    if not include_unresolved: sql += " AND classification = 'ed'"
    out = []
    for pid, d, eid, srcs, dx, adm, cls in db.execute(sql, args):
        if keep is not None and pid not in keep: continue
        out.append({'patient_id': pid, 'day': d, 'end_day': d, 'encounter_id': eid, 'sources': srcs, 'principal': dx,
                    'admitted': bool(adm), 'classification': cls})
    return out


# ---- availability ---------------------------------------------------------------------------------------------------
def availability(db, start, end, patient_ids=None, tables=None):
    """Which ED-capable sources were delivered for [start, end] and which asked-about patients they cover."""
    tables = tables or columns(db)
    feeds, covered = {}, set()
    probes = {'ehr': ('encounter', 'event_date', " AND source_table = 'encounters'" if 'source_table' in tables.get('encounter', ()) else ''),
              'adt': ('adt_visit', 'admit_date', ''), 'payer_claims': ('payer_claim', 'service_from', ''), 'billing_claims': ('claim', 'service_date', '')}
    for name, (table, col, extra) in probes.items():
        if not {'patient_id', col} <= set(tables.get(table, ())):
            feeds[name] = {'delivered': False, 'reason': f'{table}.{col} not in this warehouse'}; continue
        n, lo, hi = db.execute(f'SELECT count(*), min(substr({col},1,10)), max(substr({col},1,10)) FROM {table} '
                               f'WHERE substr({col},1,10) BETWEEN ? AND ?{extra}', (start, end)).fetchone()
        feeds[name] = {'delivered': bool(n), 'records_in_period': n, 'first': lo, 'last': hi}
        if n and patient_ids is not None:
            covered |= {r[0] for r in db.execute(f'SELECT DISTINCT patient_id FROM {table} WHERE substr({col},1,10) BETWEEN ? AND ?{extra}', (start, end))}
    if 'member_month' in tables and feeds.get('payer_claims', {}).get('delivered') and patient_ids is not None:
        # enrolled members are covered by the payer claims feed even without a claim of their own
        covered |= {r[0] for r in db.execute('SELECT DISTINCT patient_id FROM member_month WHERE month BETWEEN ? AND ?', (start[:7], end[:7]))}
    delivered = [k for k, v in feeds.items() if v['delivered']]
    asked = None if patient_ids is None else set(patient_ids)
    return {'feeds': feeds, 'delivered_sources': delivered,
            'uncovered_patients': sorted(asked - covered) if asked is not None and delivered else (sorted(asked) if asked else []),
            'asked_patients': None if asked is None else len(asked)}


def status(count, unresolved, avail):
    if count: return 'observed'
    if not avail['delivered_sources']: return 'source_not_delivered'
    if avail['asked_patients'] and len(avail['uncovered_patients']) == avail['asked_patients']: return 'source_not_delivered'
    if unresolved: return 'definition_unresolved'
    return 'true_zero'


# ---- registered engine ----------------------------------------------------------------------------------------------
PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'EMERGENCY DEPARTMENT VISITS under the one governed all-source ED definition (EHR + ADT + claims, one per patient-day, '
                  'denied claims count): how many ED visits, ED visits per 1,000 member-years, which patients had N or more ED visits, '
                  'ED visits by month / practice / PCP / payer / source, treat-and-release vs admitted. Every answer states whether a '
                  'zero is a true zero, a source that was not delivered, or an unresolved ED-vs-admission registration.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'period': {'start': '', 'end': ''}, 'output': 'summary|visits|patients|by_month', 'group_by': 'none|practice|pcp|payer|source',
                           'min_visits': 1, 'admitted': 'any|exclude|only', 'include_unresolved': False},
            'meaning': 'The single governed ED-visit definition used everywhere (' + BASIS + ') Returns the count, patients, per-1,000 '
                       'member-years rate and availability status: observed, true_zero, source_not_delivered or definition_unresolved.'}
NATIVE_SCALARS = ('count', 'value')


def parameter_schema(catalog, obj, string, array):
    return obj({'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                'output': string(OUTPUTS, 'summary: count/rate/status; visits: one row per visit; patients: one row per patient with >= min_visits; by_month'),
                'group_by': string(GROUP_BY), 'min_visits': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                'admitted': string(ADMITTED, 'exclude = treat-and-release only; only = visits resulting in admission'),
                'include_unresolved': {'type': 'boolean', 'description': 'also count ADT-only E<->I/O class changes (default false)'}})


def validate(p):
    p = dict(p or {})
    if set(p) - {'period', 'output', 'group_by', 'min_visits', 'admitted', 'include_unresolved'}:
        raise ValueError('ed_visits parameters: period, output, group_by, min_visits, admitted, include_unresolved')
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or any(
            v and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)) for v in period.values()):
        raise ValueError('period: {start, end} as YYYY-MM-DD')
    p['period'] = {'start': period.get('start') or '', 'end': period.get('end') or ''}
    p['output'] = p.get('output') or 'summary'
    p['group_by'] = p.get('group_by') or 'none'
    p['admitted'] = p.get('admitted') or 'any'
    if p['output'] not in OUTPUTS: raise ValueError(f'output: one of {OUTPUTS}')
    if p['group_by'] not in GROUP_BY: raise ValueError(f'group_by: one of {GROUP_BY}')
    if p['admitted'] not in ADMITTED: raise ValueError(f'admitted: one of {ADMITTED}')
    mv = p.get('min_visits', 1)
    if type(mv) is not int or not 1 <= mv <= 100: raise ValueError('min_visits: integer 1..100')
    p['min_visits'] = mv
    p['include_unresolved'] = bool(p.get('include_unresolved', False))
    return p


def default_period(as_of):
    end = dt.date.fromisoformat(str(as_of)[:10])
    return dt.date(end.year, 1, 1).isoformat(), end.isoformat()


def _target(path):
    from . import semantic_layer
    return semantic_layer.ensure(path) if (Path(path) / 'run.json').exists() else Path(path) / 'semantic.sqlite3'


def execute(path, patient_ids, params, as_of):
    db = sqlite3.connect(f'file:{_target(path)}?mode=ro', uri=True)
    try:
        return run(db, patient_ids, params, as_of)
    finally:
        db.close()


def _labels(db, tables, group_by):
    if group_by in ('none', 'source'): return {}
    col = {'practice': 'pcp_practice_id', 'pcp': 'pcp_clinician_id', 'payer': 'payer_id'}[group_by]
    cols = tables.get('patient', set())
    if col not in cols and group_by == 'payer' and 'coverage_type' in cols: col = 'coverage_type'
    return dict(db.execute(f'SELECT patient_id, {col} FROM patient')) if col in cols else {}


def run(db, patient_ids, params, as_of):
    p = validate(params)
    start, end = p['period']['start'] or default_period(as_of)[0], min(p['period']['end'] or str(as_of)[:10], str(as_of)[:10])
    tables = columns(db)
    keep = set(patient_ids) if patient_ids is not None else None
    allv = events(db, start, end, keep, include_unresolved=True, tables=tables)
    unresolved = [e for e in allv if e['classification'] == 'unresolved']
    visits = [e for e in allv if e['classification'] == 'ed' or p['include_unresolved']]
    if p['admitted'] == 'exclude': visits = [e for e in visits if not e['admitted']]
    elif p['admitted'] == 'only': visits = [e for e in visits if e['admitted']]
    avail = availability(db, start, end, patient_ids, tables)
    labels = _labels(db, tables, p['group_by'])
    per_patient = Counter(e['patient_id'] for e in visits)
    member_years = None
    if 'member_month' in tables:
        sql, args = 'SELECT patient_id, count(DISTINCT month) FROM member_month WHERE month BETWEEN ? AND ? GROUP BY 1', (start[:7], end[:7])
        mm = {pid: n for pid, n in db.execute(sql, args) if keep is None or pid in keep}
        member_years = round(sum(mm.values()) / 12, 2) if mm else None
    else:
        mm = {}
    def group_of(e):
        if p['group_by'] == 'source': return e['sources']
        return labels.get(e['patient_id']) or ('all' if p['group_by'] == 'none' else 'Unknown')
    groups = defaultdict(lambda: {'ed_visits': 0, 'patients': set(), 'admitted': 0})
    for e in visits:
        g = groups[group_of(e)]; g['ed_visits'] += 1; g['patients'].add(e['patient_id']); g['admitted'] += e['admitted']
    if p['output'] == 'visits':
        rows = [{k: e[k] for k in ('patient_id', 'day', 'encounter_id', 'sources', 'principal', 'admitted', 'classification')} for e in visits]
    elif p['output'] == 'patients':
        rows = sorted(({'patient_id': pid, 'ed_visits': n, 'first_visit': min(e['day'] for e in visits if e['patient_id'] == pid),
                        'last_visit': max(e['day'] for e in visits if e['patient_id'] == pid)}
                       for pid, n in per_patient.items() if n >= p['min_visits']), key=lambda r: (-r['ed_visits'], r['patient_id']))
    elif p['output'] == 'by_month':
        months = defaultdict(lambda: {'ed_visits': 0, 'patients': set()})
        for e in visits: months[e['day'][:7]]['ed_visits'] += 1; months[e['day'][:7]]['patients'].add(e['patient_id'])
        rows = [{'month': m, 'ed_visits': v['ed_visits'], 'patients': len(v['patients'])} for m, v in sorted(months.items())]
    else:
        rows = []
        for g, v in sorted(groups.items(), key=lambda kv: str(kv[0])):
            gm = sum(n for pid, n in mm.items() if p['group_by'] in ('none', 'source') or (labels.get(pid) or 'Unknown') == g)
            my = round(gm / 12, 2) if gm and p['group_by'] != 'source' else None
            rows.append({'group': g, 'ed_visits': v['ed_visits'], 'patients': len(v['patients']), 'resulted_in_admission': v['admitted'],
                         'member_years': my, 'ed_per_1000': round(1000 * v['ed_visits'] / my, 1) if my else None})
    ids = sorted(pid for pid, n in per_patient.items() if n >= p['min_visits'])
    count = len(ids) if p['output'] == 'patients' else len(visits)
    st = status(len(visits), len(unresolved), avail)
    return {'schema': VERSION, 'definition_name': NAME, 'definition_basis': BASIS, 'parameters': p, 'period': {'start': start, 'end': end},
            'rows': rows, 'patient_ids': ids, 'count': count, 'ed_visits': len(visits), 'unresolved_registrations': len(unresolved),
            'source_mix': dict(Counter(e['sources'] for e in visits)), 'member_years': member_years,
            'ed_per_1000': round(1000 * len(visits) / member_years, 1) if member_years else None,
            'availability': avail, 'ed_status': st}


def headline(r):
    p = r['parameters']
    what = {'exclude': 'treat-and-release ED visits', 'only': 'ED visits resulting in admission'}.get(p['admitted'], 'ED visits')
    if r['ed_status'] == 'source_not_delivered':
        return f"Not available: no ED-capable source (EHR encounters, ADT, claims) was delivered for {r['period']['start']} to {r['period']['end']}" \
               + (' for the patients asked about' if r['availability']['asked_patients'] else '')
    if r['ed_status'] == 'definition_unresolved':
        return f"0 confirmed {what}; {r['unresolved_registrations']} ADT registrations changed between ED and inpatient/observation with no other evidence (unresolved)"
    if p['output'] == 'patients':
        return f"{r['count']} patients with {p['min_visits']}+ {what}"
    rate = f"; {r['ed_per_1000']} per 1,000 member-years" if r['ed_per_1000'] is not None else ''
    zero = ' (true zero: ED-capable sources were delivered for the period)' if r['ed_status'] == 'true_zero' else ''
    return f"{r['ed_visits']} {what} among {len(set(r['patient_ids']))} patients{rate}{zero}"


def answer(report, plan, question):
    used = f"Definition used ({report['definition_name']}): {report['definition_basis']}"
    st, avail = report['ed_status'], report['availability']
    notes = [used, f"ED availability: {st}; sources delivered for the period: {', '.join(avail['delivered_sources']) or 'none'}."]
    if avail['uncovered_patients']:
        notes.append(f"{len(avail['uncovered_patients'])} asked-about patients have no ED-capable source in the period (source not delivered for them).")
    if report['unresolved_registrations']:
        notes.append(f"{report['unresolved_registrations']} ADT-only registrations changed between ED and inpatient/observation; unresolved, "
                     f"{'counted' if report['parameters']['include_unresolved'] else 'not counted'}.")
    if st == 'source_not_delivered':
        return {'status': 'unsupported', 'answer_type': 'unsupported', 'value': headline(report), 'headline': headline(report),
                'missing': ['An ED-capable source (EHR encounters, ADT or claims) for the period'], 'patient_ids': [],
                'ed_status': st, 'availability': avail, 'limitations': notes}
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements') or avail['uncovered_patients']
    out = {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['rows'], 'count': report['count'],
           'patient_ids': report['patient_ids'], 'value': f"{headline(report)} ({report['period']['start']} to {report['period']['end']}).",
           'method': used, 'definitions': [{'term': 'ED visit', 'basis': report['definition_basis']}], 'ed_status': st,
           'availability': avail, 'limitations': notes, 'membership_role': 'contributing'}
    if report['parameters']['output'] == 'summary' and report['parameters']['group_by'] == 'none' and report['ed_per_1000'] is not None \
            and re.search(r'per 1,?000|\brate\b', question or '', re.I):
        out['value'] = report['ed_per_1000']; out['unit'] = 'ED visits per 1,000 member-years'
        out['denominator'] = f"{report['member_years']} member-years"
    return out


# ---- exact deterministic routing ------------------------------------------------------------------------------------
_ED = r'(?:ed|er|emergency(?: department| room)?)(?: visits?| encounters?| use| utilization)?'
ED_QUESTION = re.compile(r'\b(?:ed|er|emergency(?: department| room)?)(?: visits?| encounters?| use| utilization)\b', re.I)  # delivered_inputs gate
_FILLER = set('''what is are was were the our we did do does have had has there of total a an in for all during many with any to how
number count were s 's this that patients members people visits visit encounters encounters? show me list give tell overall
whole population across'''.split())


def local_question(question, as_of):
    """Exact grammar for plain ED-visit questions; every word must be consumed or grammatical, else decline."""
    from .metrics import period_for
    t = ' ' + re.sub(r'[?.!,]', ' ', (question or '').lower().replace('1,000', '1000')) + ' '
    t = re.sub(r'\s+', ' ', t)
    if not re.search(rf'\b{_ED}\b', t): return None
    params, consumed = {'output': 'summary', 'group_by': 'none', 'min_visits': 1, 'admitted': 'any'}, t
    def take(rx, fn=None):
        nonlocal consumed
        m = re.search(rx, consumed)
        if not m: return None
        if fn: fn(m)
        consumed = consumed[:m.start()] + ' ' + consumed[m.end():]
        return m
    take(r'\b(?:treat[- ]and[- ]release|did not result in (?:an )?(?:inpatient )?admission|not (?:resulting in|leading to) (?:an )?admission|without (?:an )?admission|not admitted)\b',
         lambda m: params.update(admitted='exclude'))
    take(r'\b(?:that )?(?:resulted|resulting|led|leading) (?:in|to) (?:an )?(?:inpatient )?admission\b|\bthat were admitted\b', lambda m: params.update(admitted='only'))
    take(r'\b(\d+) or more\b|\b(\d+)\+|\bat least (\d+)\b|\bmore than (\d+)\b',
         lambda m: params.update(output='patients', min_visits=int(next(g for g in m.groups() if g)) + (1 if m.group(4) else 0)))
    take(r'\b(?:which|list(?: the)?|who are the|what|how many) (?:patients|members|people)\b', lambda m: params.update(output='patients'))
    take(r'\bby (month|practice|pcp|payer|source)\b', lambda m: params.update(output='by_month') if m.group(1) == 'month' else params.update(group_by=m.group(1)))
    take(r'\b(?:per|by) month\b|\bmonthly\b', lambda m: params.update(output='by_month'))
    for _ in range(2): take(r'\bper 1000(?: members?| member[- ]years?| patients?| enrollees?)?\b|\b(?:visit )?rate\b')
    took_period = take(r'\b(?:in|during|for) (20\d\d)\b|\b(?:in |during |over )?(?:the )?(?:last|past|trailing|prior|previous) (?:12|twelve) months\b|'
                       r'\b(?:in |during )?(?:the )?(?:last|past) year\b|\b(?:so far )?this year\b|\byear[- ]to[- ]date\b|\bytd\b|\b(20\d\d)\b')
    take(rf'\b{_ED}\b')
    take(rf'\b{_ED}\b')
    take(r'\bhow many\b|\bnumber of\b|\bcount of\b')
    left = [w for w in consumed.split() if w not in _FILLER]
    if left: return None
    if params['output'] == 'patients' and not re.search(r'\b(?:patients|members|people|who)\b', t): return None
    start, end, _ = period_for(question, as_of or dt.date.today().isoformat(), default='as_of_year')
    params['period'] = {'start': start.isoformat(), 'end': end.isoformat()}
    from .foundation_engines import graph
    return graph(question, 'ed_visits', validate(params), 'Governed all-source ED visits (ed_definition.py)')
