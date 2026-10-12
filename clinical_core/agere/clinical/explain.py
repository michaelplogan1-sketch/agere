"""Explain an outcome: which received-data factors distinguish units with it from units without.

Answers "why" questions descriptively. The unit is an index hospital stay for
readmissions, otherwise a patient. Factors come only from received records:
demographics and payer, chronic-condition burden, social screens, PCP
attribution, prior ED use and (for stays) discharge planning and timely
follow-up. Each factor gets outcome rates with/without it, a risk ratio, a
Fisher exact p-value and a Benjamini-Hochberg q-value. With fewer than
MIN_EVENTS positive units no factor statistics are claimed; every positive case
is laid out with its factors instead (case review).

These are associations in observed documentation, not causes.
"""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import json, math, sqlite3

from scipy.stats import fisher_exact

from . import aggregate, cohorts, grounding, source_profile
from ..mathematics import benjamini_hochberg

VERSION = 'outcome-explanation/1'
MIN_EVENTS = 5
OUTCOME_TYPES = ('readmission', 'concept', 'measurement')
FOLLOWUP_DAYS = 7
PRIOR_DAYS = 180
FACTOR_LABELS = {
    'age_65_plus': 'Age 65 or older', 'female': 'Female', 'medicare': 'Medicare coverage', 'medicaid': 'Medicaid coverage',
    'uninsured': 'Uninsured', 'no_assigned_pcp': 'No assigned primary-care clinician',
    'condition_burden_3_plus': 'Three or more chronic conditions', 'financial_hardship': 'Reported difficulty paying for care',
    'food_insecurity': 'Reported food difficulty', 'transport_barrier': 'Reported transport difficulty / unreliable transport',
    'lives_alone': 'Lives alone', 'prior_ed_visit': f'ED visit in the {PRIOR_DAYS} days before',
    'followup_within_7d': f'Clinic or telehealth follow-up within {FOLLOWUP_DAYS} days of discharge',
    'followup_not_confirmed': 'Follow-up attendance not confirmed at transition',
    'home_health_ordered': 'Home health ordered at discharge', 'observation_stay': 'Index stay was observation (not inpatient)',
}
for _c in aggregate.BURDEN_CONCEPTS: FACTOR_LABELS[f'has_{_c}'] = f'Documented {_c.replace("_", " ")}'


def validate(p):
    if not isinstance(p, dict) or set(p) - {'outcome', 'window'}: raise ValueError('explain parameters: outcome, window')
    o = p.get('outcome')
    if not isinstance(o, dict) or o.get('type') not in OUTCOME_TYPES: raise ValueError(f'outcome.type must be one of {OUTCOME_TYPES}')
    if o['type'] == 'concept' and not cohorts.concepts.is_concept(o.get('concept', '')): raise ValueError('Unknown outcome concept')
    if o['type'] == 'measurement':
        if o.get('concept') not in cohorts.concepts.MEASUREMENTS or o.get('op') not in ('lt', 'le', 'gt', 'ge') \
                or type(o.get('value')) not in (int, float):
            raise ValueError('Measurement outcome needs concept, op (lt/le/gt/ge) and numeric value')
        o = {**o, 'aggregate': o.get('aggregate', 'latest')}
    window = p.get('window') or {'start': '', 'end': ''}
    return {'outcome': o, 'window': {'start': window.get('start', ''), 'end': window.get('end', '')}}


def _date(r): return (aggregate._date(r) or '')[:10]


def _shift(d, days): return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def _patient_factors(person, rs, resolved, ref, as_of):
    """Factors known on or before `ref` (undated records count as known)."""
    known = [r for r in rs if not _date(r) or _date(r) <= ref]
    f = {}
    age = person.get('age_reported')
    f['age_65_plus'] = type(age) in (int, float) and age >= 65
    f['female'] = person.get('sex_reported') == 'F'
    cov = [r for r in known if r.get('source_table') == 'coverage']
    kind = (max(cov, key=_date).get('coverage_type') if cov else None)
    for k in ('medicare', 'medicaid', 'uninsured'): f[k] = kind == k
    f['no_assigned_pcp'] = not any(r.get('source_table') == 'care_team_relationships' and r.get('role') in aggregate.PCP_ROLES for r in known)
    n = 0
    for c in aggregate.BURDEN_CONCEPTS:
        f[f'has_{c}'] = any(aggregate._matches(resolved[c], r) for r in known); n += f[f'has_{c}']
    f['condition_burden_3_plus'] = n >= 3
    screens = [r for r in known if r.get('source_table') in ('financial_screens', 'social_screens')]
    f['financial_hardship'] = any(r.get('difficulty_paying_medical_bills') is True or r.get('difficulty_paying_for_care_reported') is True for r in screens)
    f['food_insecurity'] = any(r.get('food_difficulty_reported') is True for r in screens)
    f['transport_barrier'] = any(r.get('transport_difficulty_reported') is True or r.get('transport') == 'unreliable' for r in screens)
    f['lives_alone'] = any(r.get('source_table') == 'social_history' and str(r.get('lives', '')).lower() == 'alone' for r in known)
    start = _shift(ref, -PRIOR_DAYS)
    f['prior_ed_visit'] = any(aggregate._matches(resolved['emergency_visit'], r) and start <= _date(r) < ref for r in rs if _date(r))
    return f


def _stay_factors(stay, rs, resolved):
    d = stay['discharge']
    end = _shift(d, FOLLOWUP_DAYS)
    nxt = stay.get('next_admit') or '9999-12-31'
    visits = [r for r in rs if _date(r) and d < _date(r) <= min(end, nxt)
              and any(aggregate._matches(resolved[c], r) for c in ('primary_care_visit', 'specialist_visit', 'telehealth_contact'))]
    plans = [r for r in rs if r.get('source_table') in ('discharge_plans', 'hospital_care_transitions') and stay['admit'] <= _date(r) <= d]
    return {'followup_within_7d': bool(visits),
            'followup_not_confirmed': any(r.get('followup_attendance_confirmed') is False for r in plans),
            'home_health_ordered': any(r.get('home_health_ordered') is True for r in plans),
            'observation_stay': str(stay.get('type', '')).lower() == 'observation'}


def execute(path, patient_ids, params):
    path = Path(path)
    p = validate(params)
    meta = json.loads((path / 'run.json').read_text())
    as_of = meta['snapshot']['as_of']
    profile = source_profile.load(path)
    resolved = {c: aggregate._resolve(c, profile) for c in
                (*aggregate.BURDEN_CONCEPTS, 'emergency_visit', 'primary_care_visit', 'specialist_visit', 'telehealth_contact')}
    members = set(patient_ids)
    streams = {'coverage', 'care_team_relationships', 'financial_screens', 'social_screens', 'social_history',
               'encounters', 'adt', 'discharge_plans', 'hospital_care_transitions'}
    for r in resolved.values(): streams.update(b['stream'] for b in r['bindings'])
    db = sqlite3.connect(f'file:{path / "stack.sqlite3"}?mode=ro', uri=True)
    try:
        people = {pid: json.loads(x) for pid, x in db.execute('SELECT id, payload FROM patients') if pid in members}
        records = defaultdict(list)
        for pid, x in db.execute('SELECT patient, payload FROM records WHERE stream IN (' + ','.join('?' * len(streams)) + ')', sorted(streams)):
            if pid in members: records[pid].append(json.loads(x))
    finally:
        db.close()

    units = []  # (unit_id, patient_id, outcome bool, factors, case detail)
    o = p['outcome']
    if o['type'] == 'readmission':
        for pid, person in people.items():
            stays = aggregate._stays(records[pid], p['window'], as_of)
            for i, s in enumerate(stays):
                s['next_admit'] = stays[i + 1]['admit'] if i + 1 < len(stays) else None
                f = {**_patient_factors(person, records[pid], resolved, s['discharge'], as_of), **_stay_factors(s, records[pid], resolved)}
                units.append((f"{pid}:{s['admit']}", pid, s['readmitted'], f,
                              {k: s[k] for k in ('admit', 'discharge', 'type', 'organization', 'days_to_readmission')}))
        unit_label = 'index hospital stays'
    else:
        atom = ({'kind': 'concept', 'concept': o['concept']} if o['type'] == 'concept' else
                {'kind': 'measurement', **{k: o[k] for k in ('concept', 'op', 'value', 'aggregate')}})
        atom['window'] = {'basis': 'all_observed', 'start': '', 'end': ''}
        selected = cohorts.select(path, {'all': [atom], 'any': [], 'exclude': []}, patient_ids=sorted(members))
        positive, unknown = set(selected['patient_ids']), set(selected['unknown_patient_ids'])
        for pid, person in people.items():
            if pid in unknown: continue
            units.append((pid, pid, pid in positive, _patient_factors(person, records[pid], resolved, as_of, as_of), {}))
        unit_label = 'patients'

    pos = [u for u in units if u[2]]
    neg = [u for u in units if not u[2]]
    result = {'schema': VERSION, 'outcome': o, 'unit': unit_label, 'units': len(units), 'positive_units': len(pos),
              'outcome_rate': round(len(pos) / len(units), 4) if units else None,
              'method': f'Outcome rate with vs without each factor; Fisher exact test, Benjamini-Hochberg q. '
                        f'Factors use records on or before the reference date ({"discharge" if o["type"] == "readmission" else "snapshot"}). '
                        'Associations in received documentation, not causes.'}
    if len(pos) < MIN_EVENTS:
        result['status'] = 'insufficient_events'
        result['finding'] = (f'{len(pos)} of {len(units)} {unit_label} had the outcome; too few to estimate factor associations. '
                             'Each case is listed with its documented factors.')
        result['cases'] = [{'unit': u[0], 'patient_id': u[1], **u[4],
                            'factors_present': [FACTOR_LABELS.get(k, k) for k, v in sorted(u[3].items()) if v]} for u in pos]
        result['factors'] = []
        return result
    # A factor built from the outcome's own concept would explain the outcome by itself.
    circular = {f'has_{o.get("concept")}'} | ({'prior_ed_visit'} if o.get('concept') == 'emergency_visit' else set())
    result['excluded_circular_factors'] = sorted(c for c in circular if c in units[0][3])
    rows = []
    for k in sorted(set(units[0][3]) - circular):
        a = sum(u[3][k] for u in pos); b = len(pos) - a
        c = sum(u[3][k] for u in neg); d = len(neg) - c
        if a + c == 0 or b + d == 0: continue
        rate1, rate0 = a / (a + c), b / (b + d)
        _, pval = fisher_exact([[a, c], [b, d]])
        rr = rate1 / rate0 if rate0 else None
        rows.append({'factor': k, 'label': FACTOR_LABELS.get(k, k), 'units_with_factor': a + c,
                     'outcome_rate_with': round(rate1, 4), 'outcome_rate_without': round(rate0, 4),
                     'risk_ratio': round(rr, 3) if rr is not None else None, 'p_value': round(float(pval), 5),
                     'prevalence_in_outcome': round(a / len(pos), 4), 'prevalence_without_outcome': round(c / len(neg), 4) if neg else None})
    if rows:
        qs = benjamini_hochberg([r['p_value'] for r in rows])
        for r, q in zip(rows, qs): r['q_value'] = round(float(q), 5)
    rows.sort(key=lambda r: (r['p_value'], -abs(math.log(r['risk_ratio'])) if r['risk_ratio'] else 0))
    result.update(status='estimated', factors=rows,
                  finding='; '.join(f"{r['label']}: {r['outcome_rate_with']:.0%} vs {r['outcome_rate_without']:.0%} (RR {r['risk_ratio']}, q={r['q_value']})"
                                    for r in rows if r.get('q_value', 1) < 0.1 and r['risk_ratio'] and r['risk_ratio'] > 1)[:1500]
                  or 'No factor is associated with the outcome after false-discovery correction.')
    return result
