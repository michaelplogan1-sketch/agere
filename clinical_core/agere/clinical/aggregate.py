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
import json, math, os, sqlite3, statistics

from . import concepts, grounding, source_profile

VERSION = 'population-aggregate/1'
TIME_GROUPS = ('month', 'quarter', 'year')
GROUP_BY = ('none', 'pcp', 'practice', 'coverage_type', 'age_band', 'sex', 'facility') + TIME_GROUPS
STATISTICS = {'patients': ('count', 'share'), 'events': ('count', 'rate_per_1000'), 'value': ('mean', 'median'),
              'readmissions': ('count', 'rate')}
STAY_TYPES = r'^(inpatient.*|observation|l&d admission)$'
READMIT_DAYS = 30
ADJUSTMENTS = ('none', 'age_sex', 'age_sex_conditions')
BURDEN_CONCEPTS = ('diabetes', 'hypertension', 'heart_failure', 'chronic_kidney_disease', 'copd', 'asthma',
                   'coronary_artery_disease', 'atrial_fibrillation', 'depression', 'dementia', 'cancer')
PCP_ROLES = ('primary_care', 'pediatric_primary_care')
SMALL_GROUP = 11


def validate(p):
    if not isinstance(p, dict) or set(p) - {'measure', 'concept', 'statistic', 'group_by', 'adjust', 'window',
                                             'value_aggregate', 'top', 'status', 'columns'}:
        raise ValueError('Aggregate parameters: measure, concept, statistic, group_by, adjust, window, value_aggregate, top')
    measure = p.get('measure')
    if measure not in STATISTICS: raise ValueError('measure must be patients, events, value or readmissions')
    concept = p.get('concept', '')
    if measure == 'readmissions' and concept: raise ValueError('readmissions use hospital stays; restrict the population upstream instead')
    if measure == 'value' and concept not in concepts.MEASUREMENTS:
        raise ValueError('value aggregates need a measurement concept')
    if measure == 'events' and concept not in concepts.ENCOUNTERS and concept not in concepts.CONDITIONS:
        raise ValueError('event aggregates need an encounter or condition concept')
    if measure == 'patients' and concept and not concepts.is_concept(concept):
        raise ValueError('Unknown concept')
    statistic = p.get('statistic') or STATISTICS[measure][-1]
    if statistic not in STATISTICS[measure]: raise ValueError(f'{measure} supports {STATISTICS[measure]}')
    group_by = p.get('group_by', 'none')
    group_by = 'facility' if group_by in ('event_organization', 'hospital', 'organization') else group_by
    if group_by not in GROUP_BY: raise ValueError(f'group_by must be one of {GROUP_BY}')
    if group_by in TIME_GROUPS and (measure != 'events' or (p.get('statistic') or 'count') != 'count'):
        raise ValueError('time groups (month/quarter/year) count events per period')
    if group_by == 'facility' and measure not in ('events', 'readmissions'):
        raise ValueError('facility groups events (where they occurred) or readmissions (index stay facility)')
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
    if p.get('status', 'completed') != 'completed' or ('status' in p and concept not in concepts.ENCOUNTERS):
        raise ValueError('status filter: "completed", for encounter concepts')
    columns = p.get('columns')
    if columns is not None and not (isinstance(columns, list) and len(columns) == 2 and
                                    all(isinstance(c, str) and c.replace('_', '').isalnum() and len(c) <= 64 for c in columns)):
        raise ValueError('columns: [group column name, value column name]')
    top = p.get('top', 25)
    if type(top) is not int or not 1 <= top <= 200: raise ValueError('top must be 1..200')
    return {'measure': measure, 'concept': concept, 'statistic': statistic, 'group_by': group_by, 'adjust': adjust,
            'window': {'start': window.get('start', ''), 'end': window.get('end', '')},
            'value_aggregate': value_aggregate, 'top': top, **({'status': p['status']} if p.get('status') else {}),
            **({'columns': columns} if columns else {})}


def period_label(day, grain):
    """'2028-07-14' -> '2028-07' (month), '2028-Q3' (quarter) or '2028' (year)."""
    if grain == 'month': return day[:7]
    if grain == 'quarter': return f'{day[:4]}-Q{(int(day[5:7]) - 1) // 3 + 1}'
    return day[:4]


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


def _stays(records, window, as_of):
    """Distinct hospital stays (inpatient/observation encounters collapsed by admit day)
    with a flag for a new stay starting within READMIT_DAYS of discharge."""
    import re
    by_day = {}
    for r in records:
        if r.get('source_table') != 'encounters' or not re.match(STAY_TYPES, str(r.get('type') or '').strip(), re.I): continue
        start = (r.get('event_date') or '')[:10]
        if not start or _in_window(r, window, as_of) is not True: continue
        end = (r.get('end_time') or '')[:10] or start
        prior = by_day.get(start)
        by_day[start] = {'admit': start, 'discharge': max(end, prior['discharge']) if prior else end,
                         'type': r.get('type'), 'organization': r.get('organization_id'),
                         'discharge_known': bool(r.get('end_time')) or bool(prior and prior['discharge_known'])}
    ordered = []
    for k in sorted(by_day):  # overlapping records (transfers, duplicates) form one stay
        s = by_day[k]
        if ordered and s['admit'] <= ordered[-1]['discharge']:
            ordered[-1]['discharge'] = max(ordered[-1]['discharge'], s['discharge'])
            ordered[-1]['discharge_known'] = ordered[-1]['discharge_known'] or s['discharge_known']
        else:
            ordered.append(s)
    from datetime import date
    for i, s in enumerate(ordered):
        nxt = ordered[i + 1] if i + 1 < len(ordered) else None
        gap = (date.fromisoformat(nxt['admit']) - date.fromisoformat(s['discharge'])).days if nxt else None
        s['readmitted'] = gap is not None and 0 <= gap <= READMIT_DAYS
        s['days_to_readmission'] = gap if s['readmitted'] else None
    return ordered


def _resolve(concept, profile):
    """Concept bindings; a concept with no received records simply matches nothing."""
    return concepts.resolve(concept, profile) if concept else None


def execute(path, patient_ids, params):
    path = Path(path)
    p = validate(params)
    meta = json.loads((path / 'run.json').read_text())
    as_of = meta['snapshot']['as_of']
    profile = source_profile.load(path)
    target = _resolve(p['concept'], profile)
    burden = {c: _resolve(c, profile) for c in BURDEN_CONCEPTS} if p['adjust'] == 'age_sex_conditions' else {}
    streams = {'care_team_relationships', 'coverage'} | ({'encounters'} if p['measure'] == 'readmissions' else set())
    for r in [target, *burden.values()]:
        if r: streams.update(b['stream'] for b in r['bindings'])
    members = set(patient_ids)
    # Fast path: encounter concepts read the semantic layer's typed encounter rows (concept already resolved)
    # instead of decoding every raw encounter payload; identical matching, dates and status.
    fast = p['concept'] in concepts.ENCOUNTERS and p['measure'] in ('patients', 'events') and os.environ.get('AGERE_AGG_FAST', 'on') != 'off'
    raw_streams = streams - {'encounters', 'adt'} if fast else streams
    db = sqlite3.connect(f'file:{path / "stack.sqlite3"}?mode=ro', uri=True)
    try:
        people = {pid: json.loads(payload) for pid, payload in db.execute('SELECT id, payload FROM patients') if pid in members}
        records = defaultdict(list)
        if raw_streams:
            sql = 'SELECT patient, payload FROM records WHERE stream IN (' + ','.join('?' * len(raw_streams)) + ')'
            for pid, payload in db.execute(sql, sorted(raw_streams)):
                if pid in members: records[pid].append(json.loads(payload))
    finally:
        db.close()
    if fast:
        from . import semantic_layer
        sdb = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
        try:
            for pid, st, d, rd, ad, org, status, rid in sdb.execute(
                    'SELECT e.patient_id, e.source_table, e.event_date, r.recorded_date, r.available_date, e.organization_id, e.status, e.record_id '
                    'FROM encounter e JOIN record r ON r.record_id = e.record_id WHERE e.type_concept = ?', (p['concept'],)):
                if pid in members:
                    records[pid].append({'source_table': st, 'event_date': d, 'recorded_date': rd, 'available_date': ad,
                                         'organization_id': org, 'status': status, 'record_id': rid, '_concept': p['concept']})
        finally:
            sdb.close()

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
    stays = {pid: _stays(records[pid], p['window'], as_of) for pid in people} if p['measure'] == 'readmissions' else {}
    for pid in people:
        if p['measure'] == 'readmissions': continue
        hits = []
        for r in records[pid]:
            b = (r.get('_concept') == p['concept'] or None) if fast and r.get('_concept') else (_matches(target, r) if target else None)
            if not b or (p.get('status') and r.get('status') != p['status']): continue
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
        if p['measure'] == 'readmissions': return sum(s['readmitted'] for s in stays[pid])
        return len(events[pid]) if p['measure'] == 'events' else int(has.get(pid, False))

    def denominator(pids):
        return sum(len(stays[x]) for x in pids) if p['measure'] == 'readmissions' else len(pids)

    # Indirect standardisation.
    expected = {}
    if p['adjust'] != 'none':
        key = (lambda a: (a['age_band'], a['sex'])) if p['adjust'] == 'age_sex' else (lambda a: (a['age_band'], a['sex'], a['burden']))
        strata = defaultdict(lambda: [0, 0])
        for pid in people:
            s = strata[key(attrs[pid])]; s[0] += numerator(pid); s[1] += denominator([pid])
        expected = {pid: (strata[key(attrs[pid])][0] / strata[key(attrs[pid])][1] if strata[key(attrs[pid])][1] else 0)
                         * denominator([pid]) for pid in people}

    groups = defaultdict(list)
    facility_stays = defaultdict(list)
    if p['group_by'] == 'facility' and p['measure'] == 'readmissions':
        for pid in people:
            for s in stays[pid]:
                groups[s['organization'] or 'unknown'].append(pid); facility_stays[s['organization'] or 'unknown'].append((pid, s))
    elif p['group_by'] == 'facility':
        for pid in people:
            for org in events[pid].values(): groups[org].append(pid)
    elif p['group_by'] in TIME_GROUPS:  # each event lands in the period of its source event date
        for pid in people:
            for day in events[pid]:
                if len(day) == 10 and day[4] == '-': groups[period_label(day, p['group_by'])].append(pid)
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
            pairs = facility_stays.get(label) if p['group_by'] == 'facility' and p['measure'] == 'readmissions' else None
            if pairs is not None: num = sum(s['readmitted'] for _, s in pairs)
            else: num = len(pids) if p['group_by'] in ('facility',) + TIME_GROUPS else sum(numerator(x) for x in unique)
            row['numerator'] = num
            if p['measure'] == 'readmissions':
                row['index_stays'] = len(pairs) if pairs is not None else denominator(unique)
                row['value'] = (round(num / row['index_stays'], 4) if row['index_stays'] else None) if p['statistic'] == 'rate' else num
                chosen = pairs if pairs is not None else [(x, s) for x in unique for s in stays[x]]
                row['readmissions'] = [{'patient_id': x, **s} for x, s in chosen if s['readmitted']][:20]
            elif p['statistic'] == 'share': row['value'] = round(100 * num / len(unique), 4) if unique else None  # percent
            elif p['statistic'] == 'rate_per_1000': row['value'] = round(1000 * num / len(unique), 2) if unique else None
            else: row['value'] = num
            if expected and p['group_by'] != 'facility':
                e = sum(expected[x] for x in unique)
                row.update(expected=round(e, 2), oe_ratio=round(num / e, 3) if e else None, oe_ci95=_poisson_ci(num, e))
        row['small_group'] = len(unique) < SMALL_GROUP
        return row

    rows = [summarise(g, pids) for g, pids in groups.items()]
    rank = 'oe_ratio' if expected else 'value'
    if p['group_by'] in TIME_GROUPS: rows.sort(key=lambda r: r['group'])  # chronological
    else: rows.sort(key=lambda r: (r.get('small_group'), -(r.get(rank) if r.get(rank) is not None else -1e18), str(r['group'])))
    overall = summarise('all', list(people))
    notes = [f"No received records match concept '{p['concept']}'."] if target is not None and not target['bindings'] else []
    return {'schema': VERSION, 'parameters': p, 'notes': notes, 'rows': rows[:p['top']], 'groups_total': len(rows), 'overall': overall,
            'ranked_by': rank, 'records_with_unknown_date': unknown_dates,
            'unattributed_patients': sum(a['pcp'] == 'unattributed' for a in attrs.values()) if p['group_by'] in ('pcp', 'practice') else None,
            'interpretation': grounding.interpretation({'all': [{'kind': 'concept', '_resolved': target}]}) if target else [],
            'method': ('Events = distinct patient-days per concept. ' if p['measure'] == 'events' else '') +
                      (f'Index stays = inpatient/observation encounters collapsed by admit day; readmission = next stay within {READMIT_DAYS} days of discharge. ' if p['measure'] == 'readmissions' else '') +
                      ('Indirect standardisation over ' + p['adjust'].replace('_', ' x ') + ' strata; O/E with Byar 95% CI. ' if expected else '') +
                      f'Groups under {SMALL_GROUP} patients are flagged and ranked last. PCP = latest assigned primary-care relationship.'}
