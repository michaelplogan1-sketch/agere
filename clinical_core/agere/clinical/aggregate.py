"""Population aggregation: counts, rates, shares and averages by attribution group.

This is the operator behind questions such as "which PCPs have the highest ED
use, adjusted for case mix" or "average latest A1c by practice". It works on a
membership from the graph (the population) and a library concept (what is
counted), and can indirectly standardise by age band, sex and chronic-condition
burden, reporting observed/expected ratios with Poisson confidence intervals.

Attribution uses received records only: PCP = latest assigned primary-care
relationship on or before the snapshot; payer type = latest coverage record.
Event counts are de-duplicated to one per patient per day per concept, because
one visit appears in several sources (encounter + ADT registration).
"""
from collections import defaultdict
from pathlib import Path
import json, math, sqlite3, statistics

from . import concepts, grounding, source_profile

VERSION = 'population-aggregate/1'
GROUP_BY = ('none', 'pcp', 'practice', 'coverage_type', 'age_band', 'sex', 'event_organization')
STATISTICS = {'patients': ('count', 'share'), 'events': ('count', 'rate_per_1000'), 'value': ('mean', 'median')}
ADJUSTMENTS = ('none', 'age_sex', 'age_sex_conditions')
BURDEN_CONCEPTS = ('diabetes', 'hypertension', 'heart_failure', 'chronic_kidney_disease', 'copd', 'asthma',
                   'coronary_artery_disease', 'atrial_fibrillation', 'depression', 'dementia', 'cancer')
PCP_ROLES = ('primary_care', 'pediatric_primary_care')
SMALL_GROUP = 11


def validate(p):
    if not isinstance(p, dict) or set(p) - {'measure', 'concept', 'statistic', 'group_by', 'adjust', 'window',
                                             'value_aggregate', 'top'}:
        raise ValueError('Aggregate parameters: measure, concept, statistic, group_by, adjust, window, value_aggregate, top')
    measure = p.get('measure')
    if measure not in STATISTICS: raise ValueError('measure must be patients, events or value')
    concept = p.get('concept', '')
    if measure == 'value' and concept not in concepts.MEASUREMENTS:
        raise ValueError('value aggregates need a measurement concept')
    if measure == 'events' and concept not in concepts.ENCOUNTERS and concept not in concepts.CONDITIONS:
        raise ValueError('event aggregates need an encounter or condition concept')
    if measure == 'patients' and concept and not concepts.is_concept(concept):
        raise ValueError('Unknown concept')
    statistic = p.get('statistic') or STATISTICS[measure][-1]
    if statistic not in STATISTICS[measure]: raise ValueError(f'{measure} supports {STATISTICS[measure]}')
    group_by = p.get('group_by', 'none')
    if group_by not in GROUP_BY: raise ValueError(f'group_by must be one of {GROUP_BY}')
    if group_by == 'event_organization' and measure != 'events': raise ValueError('event_organization groups events only')
    adjust = p.get('adjust', 'none')
    if adjust not in ADJUSTMENTS: raise ValueError(f'adjust must be one of {ADJUSTMENTS}')
    if adjust != 'none' and measure == 'value': raise ValueError('Standardisation applies to counts and shares')
    window = p.get('window') or {'start': '', 'end': ''}
    if not isinstance(window, dict) or set(window) - {'start', 'end', 'basis'}: raise ValueError('window needs start/end dates')
    for k in ('start', 'end'):
        v = window.get(k, '')
        if v and (not isinstance(v, str) or len(v) != 10): raise ValueError('ISO dates required in window')
    value_aggregate = p.get('value_aggregate', 'latest')
    if value_aggregate not in ('latest', 'all'): raise ValueError('value_aggregate is latest or all')
    top = p.get('top', 25)
    if type(top) is not int or not 1 <= top <= 200: raise ValueError('top must be 1..200')
    return {'measure': measure, 'concept': concept, 'statistic': statistic, 'group_by': group_by, 'adjust': adjust,
            'window': {'start': window.get('start', ''), 'end': window.get('end', '')},
            'value_aggregate': value_aggregate, 'top': top}


def _date(r):
    return r.get('event_date') or r.get('recorded_date') or r.get('available_date') or ''


def _in_window(r, window, as_of):
    d = _date(r)[:10]
    if not d: return None if (window['start'] or window['end']) else True
    return (not window['start'] or d >= window['start']) and d <= (window['end'] or as_of)


def _matches(resolved, r):
    for b in resolved['bindings']:
        if b['stream'] != r.get('source_table'): continue
        if 'values' in b:
            got = r.get(b['field'])
            if isinstance(got, list) and any(x in b['values'] for x in got): return b
            if isinstance(got, str) and got in b['values']: return b
        elif 'names' in b:
            if any(r.get(f) in b['names'] for f in b['name_fields']): return b
        elif r.get(b['value_field']) is not None:
            return b
    return None


def _value(resolved, b, r):
    raw = r.get(b['value_field'])
    if type(raw) not in (int, float) or not math.isfinite(raw): return None
    factor = resolved['units'].get(b.get('fixed_unit') or r.get(b.get('unit_field')))
    return None if factor is None else raw * factor


def age_band(age):
    if type(age) not in (int, float): return 'unknown'
    return '0-17' if age < 18 else '18-44' if age < 45 else '45-64' if age < 65 else '65-74' if age < 75 else '75+'


def _poisson_ci(observed, expected):
    """Exact-ish (Byar) 95% interval for an O/E ratio."""
    if expected <= 0: return [None, None]
    o = observed
    lo = 0.0 if o == 0 else o * (1 - 1 / (9 * o) - 1.96 / (3 * math.sqrt(o))) ** 3
    hi = (o + 1) * (1 - 1 / (9 * (o + 1)) + 1.96 / (3 * math.sqrt(o + 1))) ** 3
    return [round(lo / expected, 3), round(hi / expected, 3)]


def _resolve(concept, profile):
    if not concept: return None
    kind = 'measurement' if concept in concepts.MEASUREMENTS else 'concept'
    atom = {'kind': 'concept', 'concept': concept, 'window': {'basis': 'all_observed', 'start': '', 'end': ''}}
    grounded, _ = grounding.prepare({'all': [atom], 'any': [], 'exclude': []}, profile)
    return grounded['all'][0]['_resolved']


def execute(path, patient_ids, params):
    path = Path(path)
    p = validate(params)
    meta = json.loads((path / 'run.json').read_text())
    as_of = meta['snapshot']['as_of']
    profile = source_profile.load(path)
    target = _resolve(p['concept'], profile)
    burden = {c: _resolve(c, profile) for c in BURDEN_CONCEPTS} if p['adjust'] == 'age_sex_conditions' else {}
    streams = {'care_team_relationships', 'coverage'}
    for r in [target, *burden.values()]:
        if r: streams.update(b['stream'] for b in r['bindings'])
    members = set(patient_ids)
    db = sqlite3.connect(f'file:{path / "stack.sqlite3"}?mode=ro', uri=True)
    try:
        people = {pid: json.loads(payload) for pid, payload in db.execute('SELECT id, payload FROM patients') if pid in members}
        records = defaultdict(list)
        sql = 'SELECT patient, payload FROM records WHERE stream IN (' + ','.join('?' * len(streams)) + ')'
        for pid, payload in db.execute(sql, sorted(streams)):
            if pid in members: records[pid].append(json.loads(payload))
    finally:
        db.close()

    # Patient attributes from received records.
    attrs = {}
    for pid, person in people.items():
        rs = records[pid]
        pcp = [r for r in rs if r.get('source_table') == 'care_team_relationships' and r.get('role') in PCP_ROLES
               and r.get('status') in (None, 'assigned') and (r.get('effective_start') or _date(r))[:10] <= as_of]
        pcp = max(pcp, key=lambda r: (r.get('effective_start') or _date(r))) if pcp else None
        cov = [r for r in rs if r.get('source_table') == 'coverage' and _date(r)[:10] <= as_of]
        cov = max(cov, key=_date) if cov else None
        n_conditions = sum(any(_matches(res, r) for r in rs) for res in burden.values()) if burden else 0
        attrs[pid] = {'pcp': pcp.get('clinician_id') if pcp else 'unattributed',
                      'practice': pcp.get('organization_id') if pcp else 'unattributed',
                      'coverage_type': (cov or {}).get('coverage_type') or 'unknown',
                      'age_band': age_band(person.get('age_reported')), 'sex': person.get('sex_reported') or 'unknown',
                      'burden': min(n_conditions, 3)}

    # Per-patient numerators.
    events, has, values, unknown_dates = defaultdict(dict), {}, {}, 0
    for pid in people:
        hits = []
        for r in records[pid]:
            b = _matches(target, r) if target else None
            if not b: continue
            w = _in_window(r, p['window'], as_of)
            if w is None: unknown_dates += 1; continue
            if w: hits.append((r, b))
        if p['measure'] == 'patients':
            has[pid] = bool(hits) if target else True
        elif p['measure'] == 'events':
            for r, _ in hits:  # one per patient-day; organisation of first source seen
                events[pid].setdefault(_date(r)[:10] or r.get('record_id'), r.get('organization_id') or 'unknown')
        else:
            vals = [(_date(r), _value(target, b, r)) for r, b in hits]
            vals = [(d, v) for d, v in vals if v is not None]
            if vals:
                values[pid] = max(vals)[1] if p['value_aggregate'] == 'latest' else statistics.mean(v for _, v in vals)

    def numerator(pid):
        return len(events[pid]) if p['measure'] == 'events' else int(has.get(pid, False))

    # Indirect standardisation.
    expected = {}
    if p['adjust'] != 'none':
        key = (lambda a: (a['age_band'], a['sex'])) if p['adjust'] == 'age_sex' else (lambda a: (a['age_band'], a['sex'], a['burden']))
        strata = defaultdict(lambda: [0, 0])
        for pid in people:
            s = strata[key(attrs[pid])]; s[0] += numerator(pid); s[1] += 1
        expected = {pid: strata[key(attrs[pid])][0] / strata[key(attrs[pid])][1] for pid in people}

    groups = defaultdict(list)
    if p['group_by'] == 'event_organization':
        for pid in people:
            for org in events[pid].values(): groups[org].append(pid)
    else:
        for pid in people:
            groups['all' if p['group_by'] == 'none' else attrs[pid][p['group_by']]].append(pid)

    def summarise(label, pids):
        unique = sorted(set(pids))
        row = {'group': label, 'patients': len(unique)}
        if p['measure'] == 'value':
            vals = [values[x] for x in unique if x in values]
            row.update(patients_with_value=len(vals), value=round(statistics.mean(vals) if p['statistic'] == 'mean' else statistics.median(vals), 3) if vals else None)
        else:
            num = len(pids) if p['group_by'] == 'event_organization' else sum(numerator(x) for x in unique)
            row['numerator'] = num
            if p['statistic'] == 'share': row['value'] = round(num / len(unique), 4) if unique else None
            elif p['statistic'] == 'rate_per_1000': row['value'] = round(1000 * num / len(unique), 2) if unique else None
            else: row['value'] = num
            if expected and p['group_by'] != 'event_organization':
                e = sum(expected[x] for x in unique)
                row.update(expected=round(e, 2), oe_ratio=round(num / e, 3) if e else None, oe_ci95=_poisson_ci(num, e))
        row['small_group'] = len(unique) < SMALL_GROUP
        return row

    rows = [summarise(g, pids) for g, pids in groups.items()]
    rank = 'oe_ratio' if expected else 'value'
    rows.sort(key=lambda r: (r.get('small_group'), -(r.get(rank) if r.get(rank) is not None else -1e18), str(r['group'])))
    overall = summarise('all', list(people))
    return {'schema': VERSION, 'parameters': p, 'rows': rows[:p['top']], 'groups_total': len(rows), 'overall': overall,
            'ranked_by': rank, 'records_with_unknown_date': unknown_dates,
            'unattributed_patients': sum(a['pcp'] == 'unattributed' for a in attrs.values()) if p['group_by'] in ('pcp', 'practice') else None,
            'interpretation': grounding.interpretation({'all': [{'kind': 'concept', '_resolved': target}]}) if target else [],
            'method': ('Events = distinct patient-days per concept. ' if p['measure'] == 'events' else '') +
                      ('Indirect standardisation over ' + p['adjust'].replace('_', ' x ') + ' strata; O/E with Byar 95% CI. ' if expected else '') +
                      f'Groups under {SMALL_GROUP} patients are flagged and ranked last. PCP = latest assigned primary-care relationship.'}
