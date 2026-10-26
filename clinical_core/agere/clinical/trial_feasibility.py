"""Trial feasibility: computable eligibility funnel, site and diversity breakdown, expected enrolment from history.

Criteria are structured (stated by the user or parsed from the question by the local compiler), never invented:
  age          min / max years at the snapshot (birth date when received, else the reported age)
  sex          one value
  condition    a library concept (or ICD-10 prefixes) documented at any time, or within recency_days
  lab          the LATEST result of a measurement concept within recency_days (default 365) in [low, high] or op value;
               no result in the window = unknown (cannot be verified)
  medication   fills (dispenses, paid pharmacy / payer claims; reversals excluded) of drug classes within recency_days
               (default 180), at least min_fills (2 = "stable")
  encounter    at least min_count encounters of a concept within recency_days
  unevaluated  a stated criterion the received data cannot compute (e.g. ejection fraction without a structured
               result): listed and counted as not applied, so every count is an upper bound for it
Each criterion is inclusion or exclusion. Inclusion: failing or unknown removes the patient (unknown counted apart).
Exclusion: a met exclusion removes; unknown does not (counted as unverified).
Outputs: the ordered attrition funnel (entering, removed, removed for missing data, remaining), the order-free count each
criterion removes on its own (patients failing only that criterion), eligible patients by practice (site proxy) and PCP
(investigator proxy), the eligible population's race / ethnicity / sex / age band / language against the source
population (representation ratios, stated diversity goals), and expected new eligible patients per month from the
trailing 12 months of patients who BECAME eligible (Poisson interval; a stated consent rate converts it to enrolment).
"""
from collections import Counter, defaultdict
from datetime import date
import math, re, sqlite3

from scipy.stats import chi2

from . import aggregate, concepts, semantic_layer
from .drug_classes import CLASSES, GROUPS
from .line_of_therapy import fill_rows

VERSION = 'trial-feasibility/1'
TYPES = ('age', 'sex', 'condition', 'lab', 'medication', 'encounter', 'unevaluated')
OPS = {'lt': lambda v, t: v < t, 'le': lambda v, t: v <= t, 'gt': lambda v, t: v > t, 'ge': lambda v, t: v >= t}
OP_WORDS = {'lt': '<', 'le': '<=', 'gt': '>', 'ge': '>='}
DIVERSITY = ('race', 'ethnicity', 'sex', 'age_band', 'language')
GROUP_BY = ('none', 'practice', 'pcp', 'race', 'ethnicity', 'race_ethnicity', 'sex', 'age_band', 'language')
SMALL = 11


def _int(v, key, lo, hi, default=None):
    v = default if v is None else v
    if v is None: return None
    if type(v) is not int or not lo <= v <= hi: raise ValueError(f'{key} must be an integer {lo}..{hi}')
    return v


def _criterion(c, i):
    where = f'criteria[{i}]'
    if not isinstance(c, dict) or c.get('type') not in TYPES: raise ValueError(f'{where}.type must be one of {TYPES}')
    allowed = {'type', 'kind', 'label', 'min', 'max', 'value', 'concept', 'icd10', 'recency_days', 'low', 'high', 'op', 'classes',
               'min_fills', 'min_count', 'text'}
    if set(c) - allowed: raise ValueError(f'{where} takes ' + ', '.join(sorted(allowed)))
    kind = c.get('kind', 'inclusion')
    if kind not in ('inclusion', 'exclusion'): raise ValueError(f'{where}.kind inclusion/exclusion')
    t = c['type']
    out = {'type': t, 'kind': kind}
    if t == 'age':
        out['min'] = _int(c.get('min'), f'{where}.min', 0, 120, 0); out['max'] = _int(c.get('max'), f'{where}.max', out['min'], 130, 130)
    elif t == 'sex':
        v = str(c.get('value', '')).upper()[:1]
        if v not in ('F', 'M'): raise ValueError(f'{where}.value F or M')
        out['value'] = v
    elif t == 'condition':
        icd = c.get('icd10') or []
        if c.get('concept') not in concepts.CONDITIONS and not icd: raise ValueError(f'{where}: condition needs a library concept or ICD-10 prefixes')
        if not isinstance(icd, list) or len(icd) > 20 or any(not re.fullmatch(r'[A-Z]\d{2}(?:\.\w{1,4})?', str(x)) for x in icd):
            raise ValueError(f'{where}.icd10: up to 20 ICD-10 prefixes')
        out.update(concept=c.get('concept') or '', icd10=list(icd), recency_days=_int(c.get('recency_days'), f'{where}.recency_days', 1, 3650))
    elif t == 'lab':
        if c.get('concept') not in concepts.MEASUREMENTS: raise ValueError(f'{where}: lab needs a measurement concept')
        lo, hi = c.get('low'), c.get('high')
        if c.get('op') is not None:
            if c['op'] not in OPS or type(c.get('value')) not in (int, float): raise ValueError(f'{where}: op lt/le/gt/ge with a numeric value')
            out.update(op=c['op'], value=float(c['value']))
        elif all(x is None for x in (lo, hi)):
            raise ValueError(f'{where}: lab needs low/high bounds or op/value')
        for k, v in (('low', lo), ('high', hi)):
            if v is not None:
                if type(v) not in (int, float): raise ValueError(f'{where}.{k} numeric')
                out[k] = float(v)
        out.update(concept=c['concept'], recency_days=_int(c.get('recency_days'), f'{where}.recency_days', 1, 3650, 365))
    elif t == 'medication':
        classes = c.get('classes') or []
        expanded = []
        for x in classes:
            if x in GROUPS: expanded += GROUPS[x]
            elif x in CLASSES: expanded.append(x)
            else: raise ValueError(f'{where}.classes: registered drug classes or groups')
        if not expanded: raise ValueError(f'{where}: medication needs classes')
        out.update(classes=sorted(set(expanded)), recency_days=_int(c.get('recency_days'), f'{where}.recency_days', 1, 3650, 180),
                   min_fills=_int(c.get('min_fills'), f'{where}.min_fills', 1, 12, 1))
    elif t == 'encounter':
        if c.get('concept') not in concepts.ENCOUNTERS: raise ValueError(f'{where}: encounter needs a library encounter concept')
        out.update(concept=c['concept'], recency_days=_int(c.get('recency_days'), f'{where}.recency_days', 1, 3650, 365),
                   min_count=_int(c.get('min_count'), f'{where}.min_count', 1, 50, 1))
    else:
        text = str(c.get('text') or '').strip()
        if not text: raise ValueError(f'{where}: unevaluated criteria carry the stated text')
        out['text'] = text[:200]
    out['label'] = str(c.get('label') or describe(out))[:200]
    return out


def validate(p):
    allowed = {'criteria', 'trial', 'goals', 'consent_rate', 'group_by', 'top', 'history_months'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('trial_feasibility parameters: ' + ', '.join(sorted(allowed)))
    crit = p.get('criteria')
    if not isinstance(crit, list) or not 1 <= len(crit) <= 25: raise ValueError('trial_feasibility needs 1..25 stated criteria (never assumed)')
    out = {'criteria': [_criterion(c, i) for i, c in enumerate(crit)], 'trial': str(p.get('trial') or '')[:120]}
    if not any(c['type'] != 'unevaluated' for c in out['criteria']): raise ValueError('trial_feasibility needs at least one computable criterion')
    goals = p.get('goals') or {}
    if not isinstance(goals, dict) or set(goals) - set(DIVERSITY) or any(
            not isinstance(v, dict) or any(type(x) not in (int, float) or not 0 <= x <= 1 for x in v.values()) for v in goals.values()):
        raise ValueError(f'goals: {{dimension in {DIVERSITY}: {{group: target share 0..1}}}}')
    out['goals'] = goals
    cr = p.get('consent_rate')
    if cr is not None and (type(cr) not in (int, float) or not 0 < cr <= 1): raise ValueError('consent_rate 0..1 (stated; never assumed)')
    out['consent_rate'] = cr
    gb = p.get('group_by', 'none')
    if gb not in GROUP_BY: raise ValueError(f'group_by must be one of {GROUP_BY}')
    out['group_by'] = gb
    out['top'] = _int(p.get('top'), 'top', 1, 200, 20)
    out['history_months'] = _int(p.get('history_months'), 'history_months', 3, 36, 12)
    return out


def describe(c):
    t = c['type']
    k = 'exclude ' if c['kind'] == 'exclusion' else ''
    rec = lambda: f" in the last {c['recency_days']} days" if c.get('recency_days') else ''
    if t == 'age': return f"{k}age {c['min']}-{c['max']}" if c['max'] < 130 else f"{k}age >= {c['min']}"
    if t == 'sex': return f"{k}sex {c['value']}"
    if t == 'condition': return f"{k}{(c['concept'] or ', '.join(c['icd10'])).replace('_', ' ')} documented" + rec()
    if t == 'lab':
        if 'op' in c: cond = f"{OP_WORDS[c['op']]} {c['value']:g}"
        elif 'low' in c and 'high' in c: cond = f"{c['low']:g}-{c['high']:g}"
        elif 'low' in c: cond = f">= {c['low']:g}"
        else: cond = f"<= {c['high']:g}"
        return f"{k}latest {c['concept']} {cond}" + rec()
    if t == 'medication': return f"{k}{' / '.join(x.replace('_', ' ') for x in c['classes'])} fills >= {c['min_fills']}" + rec()
    if t == 'encounter': return f"{k}{c['concept'].replace('_', ' ')} >= {c['min_count']}" + rec()
    return f"{k}{c['text']} (not computable from received data)"


def _day(s): return date.fromisoformat(s[:10]).toordinal()
def _iso(d): return date.fromordinal(int(d)).isoformat()
def _cols(db, table): return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def execute(path, patient_ids, params, as_of=None):
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        as_of = as_of or dict(db.execute('SELECT key, value FROM meta')).get('as_of')
        return run(db, patient_ids, params, as_of)
    finally:
        db.close()


def _people(db, as_of_day, keep):
    cols = _cols(db, 'patient')
    pick = lambda c: c if c in cols else 'NULL'
    out = {}
    for pid, age, sex, prac, pcp, race, eth, lang, birth in db.execute(
            f"SELECT patient_id, age_reported, sex_reported, pcp_practice_id, pcp_clinician_id, {pick('race')}, {pick('ethnicity')}, "
            f"{pick('preferred_language')}, {pick('birth_date')} FROM patient"):
        if keep is not None and pid not in keep: continue
        a = None
        if birth:
            try: a = (as_of_day - _day(birth)) / 365.25
            except ValueError: a = None
        if a is None and isinstance(age, (int, float)): a = float(age)
        s = str(sex or '').upper()[:1]
        out[pid] = {'age': a, 'sex': s if s in ('F', 'M') else 'unknown', 'practice': prac or 'unattributed', 'pcp': pcp or 'unattributed',
                    'race': race or 'not recorded', 'ethnicity': eth or 'not recorded', 'language': lang or 'not recorded',
                    'age_band': aggregate.age_band(a)}
    return out, {'race': 'race' in cols, 'ethnicity': 'ethnicity' in cols, 'language': 'preferred_language' in cols}


def evaluate(db, c, people, as_of_day, cache):
    """{pid: (status, qualifying date)} with status True (met), False (not met) or None (unknown)."""
    t = c['type']
    out = {}
    if t == 'age':
        for pid, x in people.items():
            out[pid] = (None, None) if x['age'] is None else (c['min'] <= x['age'] < c['max'] + 1, None)
        return out
    if t == 'sex':
        return {pid: ((x['sex'] == c['value']) if x['sex'] != 'unknown' else None, None) for pid, x in people.items()}
    if t == 'unevaluated':
        return {pid: (True if c['kind'] == 'inclusion' else False, None) for pid in people}
    lo = as_of_day - c['recency_days'] if c.get('recency_days') else -math.inf
    if t == 'condition':
        terms, args = [], []
        if c['concept']: terms.append('concept = ?'); args.append(c['concept'])
        if c['icd10'] and 'icd10' in _cols(db, 'condition'):
            terms += ['icd10 LIKE ?'] * len(c['icd10']); args += [f'{x}%' for x in c['icd10']]
        first, last = {}, {}
        for pid, d in db.execute('SELECT patient_id, coalesce(substr(event_date,1,10), substr(recorded_date,1,10)) FROM condition WHERE '
                                 + ' OR '.join(terms), args):
            if pid not in people or not d: continue
            day = _day(d)
            if day > as_of_day: continue
            first[pid] = min(first.get(pid, day), day); last[pid] = max(last.get(pid, day), day)
        return {pid: ((pid in last and last[pid] >= lo), first.get(pid)) for pid in people}
    if t == 'lab':
        key = ('lab', c['concept'])
        if key not in cache:
            cols = _cols(db, 'measurement')
            extra = (' AND coalesce(result_current, 1) = 1' if 'result_current' in cols else '') + (' AND excluded_reason IS NULL' if 'excluded_reason' in cols else '')
            series = defaultdict(list)
            for pid, d, v in db.execute('SELECT patient_id, substr(event_date,1,10), value_canonical FROM measurement WHERE concept = ? '
                                        f'AND value_canonical IS NOT NULL AND event_date IS NOT NULL{extra}', (c['concept'],)):
                if pid in people: series[pid].append((_day(d), float(v)))
            for v in series.values(): v.sort()
            cache[key] = series
        series = cache[key]
        for pid in people:
            inside = [(d, v) for d, v in series.get(pid, ()) if lo <= d <= as_of_day]
            if not inside: out[pid] = (None, None); continue
            d, v = inside[-1]
            ok = OPS[c['op']](v, c['value']) if 'op' in c else ((c.get('low') is None or v >= c['low']) and (c.get('high') is None or v <= c['high']))
            out[pid] = (ok, d)
        return out
    if t == 'medication':
        if 'fills' not in cache: cache['fills'] = fill_rows(db, set(people))[0]
        rx = {k: re.compile(CLASSES[k], re.I) for k in c['classes']}
        days = defaultdict(set)
        for pid, day, _, name, cls in cache['fills']:
            if lo <= day <= as_of_day and (cls in rx or any(r.search(name) for r in rx.values())): days[pid].add(day)
        return {pid: (len(days.get(pid, ())) >= c['min_fills'], min(days[pid]) if days.get(pid) else None) for pid in people}
    # encounter
    days = defaultdict(set)
    query, args = 'SELECT patient_id, substr(event_date,1,10) FROM visit WHERE type_concept = ? AND event_date IS NOT NULL', (c['concept'],)
    if c['concept'] == 'hospital_stay':
        query, args = "SELECT patient_id, event_date FROM utilization_event WHERE kind IN ('inpatient', 'observation') AND event_date IS NOT NULL", ()
    for pid, d in db.execute(query, args):
        if pid in people and lo <= _day(d) <= as_of_day: days[pid].add(_day(d))
    return {pid: (len(days.get(pid, ())) >= c['min_count'], min(days[pid]) if days.get(pid) else None) for pid in people}


def _passes(c, status):
    """(kept, unknown) for one patient's status on one criterion."""
    met = status[0]
    if c['kind'] == 'inclusion': return (met is True), met is None
    return (met is not True), met is None


def run(db, patient_ids, params, as_of):
    p = validate(params)
    as_of_day = _day(as_of)
    keep = set(patient_ids) if patient_ids is not None else None
    people, received = _people(db, as_of_day, keep)
    cache = {}
    results = [evaluate(db, c, people, as_of_day, cache) for c in p['criteria']]
    # ordered funnel
    remaining = set(people)
    funnel = [{'step': 0, 'criterion': 'source population' + (' (input population)' if keep is not None else ''), 'kind': '', 'entering': len(people),
               'removed': 0, 'removed_missing_data': 0, 'remaining': len(people), 'unverified_kept': 0}]
    for i, (c, res) in enumerate(zip(p['criteria'], results), 1):
        entering = len(remaining)
        removed = missing = unverified = 0
        nxt = set()
        for pid in remaining:
            kept, unknown = _passes(c, res[pid])
            if kept:
                nxt.add(pid)
                if unknown: unverified += 1
            else:
                removed += 1
                if unknown: missing += 1
        remaining = nxt
        funnel.append({'step': i, 'criterion': c['label'], 'kind': c['kind'] if c['type'] != 'unevaluated' else 'not applied', 'entering': entering,
                       'removed': removed, 'removed_missing_data': missing, 'remaining': len(remaining), 'unverified_kept': unverified,
                       'removed_share': round(removed / entering, 4) if entering else None})
    eligible = remaining
    # order-free: patients failing only this criterion, and patients failing it at all
    fails = {pid: [i for i, (c, res) in enumerate(zip(p['criteria'], results)) if not _passes(c, res[pid])[0]] for pid in people}
    sole = Counter(f[0] for f in fails.values() if len(f) == 1)
    anyfail = Counter(i for f in fails.values() for i in f)
    impact = [{'criterion': c['label'], 'kind': c['kind'], 'removes_alone': sole.get(i, 0), 'fails_overall': anyfail.get(i, 0),
               'would_add_if_dropped': sole.get(i, 0)} for i, c in enumerate(p['criteria']) if c['type'] != 'unevaluated']
    impact.sort(key=lambda r: (-r['removes_alone'], -r['fails_overall']))
    # sites (practice = site proxy; PCP = investigator proxy)
    sites = []
    by_prac = Counter(people[x]['practice'] for x in people)
    elig_prac = Counter(people[x]['practice'] for x in eligible)
    for prac, n in by_prac.most_common():
        e = elig_prac.get(prac, 0)
        sites.append({'site': prac, 'patients': n, 'eligible': e, 'eligible_per_1000': round(1000 * e / n, 1) if n else None,
                      'share_of_eligible': round(e / len(eligible), 4) if eligible else None, 'small_group': e < SMALL})
    sites.sort(key=lambda r: (-r['eligible'], r['site']))
    pcps = [{'pcp': k, 'eligible': v} for k, v in Counter(people[x]['pcp'] for x in eligible).most_common(p['top'])]
    # diversity
    diversity = []
    for dim in DIVERSITY:
        if dim in received and not received[dim]: continue
        base = Counter(people[x][dim] for x in people); el = Counter(people[x][dim] for x in eligible)
        goals = p['goals'].get(dim, {})
        for g in sorted(set(base) | set(goals), key=lambda g: -base.get(g, 0)):
            bs = base.get(g, 0) / len(people) if people else None
            es = el.get(g, 0) / len(eligible) if eligible else None
            row = {'dimension': dim, 'group': g, 'source_patients': base.get(g, 0), 'eligible': el.get(g, 0),
                   'source_share': _r(bs), 'eligible_share': _r(es), 'representation_ratio': _r(es / bs) if bs and es is not None else None}
            if g in goals:
                row['goal_share'] = goals[g]; row['goal_met'] = es is not None and es >= goals[g]
                row['eligible_needed_for_goal'] = math.ceil(goals[g] * len(eligible)) if eligible else None
            diversity.append(row)
    # expected enrolment: patients who BECAME eligible per month (latest date at which an inclusion criterion was first met)
    became = {}
    for pid in eligible:
        ds = [res[pid][1] for c, res in zip(p['criteria'], results) if c['kind'] == 'inclusion' and c['type'] not in ('age', 'sex', 'unevaluated') and res[pid][1]]
        if ds: became[pid] = max(ds)
    end_month = date.fromordinal(as_of_day).replace(day=1)
    months = []
    y, m = end_month.year, end_month.month
    for _ in range(p['history_months']):
        m -= 1
        if m == 0: y, m = y - 1, 12
        months.append(f'{y:04d}-{m:02d}')
    months.reverse()
    per_month = Counter(_iso(d)[:7] for d in became.values())
    history = [{'month': mo, 'newly_eligible': per_month.get(mo, 0)} for mo in months]
    total = sum(h['newly_eligible'] for h in history)
    k = len(months)
    rate = total / k
    lo_r = float(chi2.ppf(0.025, 2 * total)) / 2 / k if total else 0.0
    hi_r = float(chi2.ppf(0.975, 2 * total + 2)) / 2 / k
    forecast = {'basis': f'patients who became eligible in the {k} complete months before the snapshot (criteria other than age/sex; '
                         'exclusions assessed at the snapshot)', 'months': k, 'newly_eligible': total,
                'expected_per_month': round(rate, 2), 'ci95_per_month': [round(lo_r, 2), round(hi_r, 2)],
                'expected_next_12_months': round(12 * rate, 1), 'history': history,
                'undated_eligible': len(eligible) - len(became)}
    if p['consent_rate']:
        forecast['consent_rate'] = p['consent_rate']
        forecast['expected_enrolled_per_month'] = round(rate * p['consent_rate'], 2)
        forecast['ci95_enrolled_per_month'] = [round(lo_r * p['consent_rate'], 2), round(hi_r * p['consent_rate'], 2)]
    warnings = ['Synthetic data: counts describe a generated warehouse, not a real site population.']
    unevaluated = [c['label'] for c in p['criteria'] if c['type'] == 'unevaluated']
    if unevaluated:
        warnings.append('Not applied (no structured source): ' + '; '.join(unevaluated) + '. Eligible counts are upper bounds for these.')
    miss = sum(f['removed_missing_data'] for f in funnel)
    if miss: warnings.append(f'{miss} patients were removed because a required result was missing in its recency window (cannot be verified, not shown to fail).')
    unv = sum(f['unverified_kept'] for f in funnel if f['kind'] == 'exclusion')
    if unv: warnings.append(f'{unv} patients kept despite no data to verify an exclusion (e.g. no recent lab).')
    for dim, ok in received.items():
        if not ok: warnings.append(f'{dim.replace("_", " ")} is not received in this release: no diversity breakdown by {dim}.')
    warnings.append('Sites are PCP practices and investigators are PCPs (no research site / investigator directory is received).')
    if len(eligible) < SMALL: warnings.append(f'Only {len(eligible)} eligible patients: shares and representation ratios are unstable.')
    if not p['consent_rate']: warnings.append('No consent / screen-fail rate stated: the forecast is newly eligible patients, not enrolment.')
    reading = (f"{len(eligible)} of {len(people)} patients meet all {len(p['criteria']) - len(unevaluated)} computable criteria"
               + (f" for {p['trial']}" if p['trial'] else '')
               + (f" ({len(unevaluated)} stated criteria not computable; count is an upper bound)" if unevaluated else '') + '.')
    if impact and impact[0]['removes_alone']:
        reading += f" The criterion removing the most patients on its own: {impact[0]['criterion']} ({impact[0]['removes_alone']})."
    biggest = max(funnel[1:], key=lambda f: f['removed'], default=None)
    if biggest and biggest['removed']: reading += f" Largest funnel step: {biggest['criterion']} (-{biggest['removed']})."
    if sites and eligible: reading += f" Top site {sites[0]['site']} ({sites[0]['eligible']} eligible)."
    reading += f" Expected newly eligible {rate:.1f}/month (95% CI {lo_r:.1f}-{hi_r:.1f}) from the last {k} months."
    goals = [d for d in diversity if 'goal_met' in d]
    if goals: reading += f" Diversity goals met {sum(d['goal_met'] for d in goals)} of {len(goals)}."
    reading += ' Synthetic data.'
    return {'schema': VERSION, 'status': 'answered', 'parameters': p, 'as_of': as_of, 'population': len(people), 'eligible': len(eligible),
            'eligible_patient_ids': sorted(eligible)[:500], 'funnel': funnel, 'criterion_impact': impact, 'sites': sites[:p['top']],
            'pcps': pcps, 'diversity': diversity, 'forecast': forecast, 'warnings': warnings, 'reading': reading,
            'method': ('Computable eligibility: each stated criterion evaluated on received records (latest lab in its recency window, fills '
                       'excluding reversals, documented conditions, visits); ordered attrition funnel plus the order-free count each criterion '
                       'removes alone; practices as sites and PCPs as investigators; eligible vs source population shares by race, ethnicity, '
                       'sex, age band and language with stated goals; expected newly eligible patients per month = mean over the trailing '
                       f"{k} months with an exact Poisson 95% interval. Synthetic data.")}


def summarize(report):
    gb = report['parameters']['group_by']
    if gb in ('practice',): table = report['sites']
    elif gb == 'pcp': table = report['pcps']
    elif gb in DIVERSITY or gb == 'race_ethnicity':
        dims = ('race', 'ethnicity') if gb == 'race_ethnicity' else (gb,)
        table = [d for d in report['diversity'] if d['dimension'] in dims] or report['funnel']
    else: table = report['funnel']
    return report['reading'], table


def _r(v): return None if v is None else round(float(v), 4)
