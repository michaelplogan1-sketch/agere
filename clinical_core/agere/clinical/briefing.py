"""Proactive briefing: the insights nobody asked for, computed locally and ranked.

Runs Agere's own analytics over the whole population without a question:
discovery on actionable outcomes, material lab trajectories, metric-change
explanations (with data-feed checks) and shrinkage-ranked utilization outliers.
Each card carries its evidence, the method and a follow-up question that
reproduces it through the normal answer path. No model calls.
"""
import math, sqlite3, time

from . import discover, trajectory, vbc, semantic_layer

VERSION = 'briefing/2'
W = {'basis': 'all_observed', 'start': '', 'end': ''}
DISCOVERY = [
    ('ED use', {'all': [{'kind': 'concept', 'concept': 'emergency_visit', 'window': W}], 'any': [], 'exclude': []}, 'Find hotspots of ED visits'),
    ('Inpatient admissions', {'all': [{'kind': 'concept', 'concept': 'inpatient_admission', 'window': W}], 'any': [], 'exclude': []},
     'Where are inpatient admissions concentrated?'),
    ('Uncontrolled diabetes (any A1c > 9)', {'all': [{'kind': 'concept', 'concept': 'diabetes', 'window': W},
                                                     {'kind': 'measurement', 'concept': 'hba1c', 'op': 'gt', 'value': 9, 'aggregate': 'latest', 'window': W}],
                                             'any': [], 'exclude': []}, 'Which practices have unusually high A1c over 9?'),
    ('Diabetics without an A1c', {'all': [{'kind': 'concept', 'concept': 'diabetes', 'window': W}], 'any': [],
                                  'exclude': [{'kind': 'concept', 'concept': 'hba1c', 'window': W}]}, 'Where are diabetics without an A1c concentrated?'),
]
TRAJECTORIES = [('egfr', 'down', 'Which CKD patients have a declining eGFR?'), ('hemoglobin', 'down', 'Which patients have falling hemoglobin?'),
                ('hba1c', 'up', 'Which diabetics have worsening A1c?'), ('systolic_bp', 'up', 'Show patients with rising systolic blood pressure')]
FORECASTS = [('egfr', 30.0, 365, 'Which CKD patients are likely to drop below an eGFR of 30 in the next year?', 'eGFR below 30 (nephrology referral threshold)'),
             ('hba1c', 9.0, 182, 'Which diabetics are on track to exceed an A1c of 9 within six months?', 'A1c above 9 (poor control)'),
             ('systolic_bp', 160.0, 182, 'Which patients are likely to exceed a systolic of 160 within six months?', 'systolic above 160')]
CHANGES = [('pmpm', 'Why did our PMPM increase?'), ('ed_rate', 'Why did ED visits go up?'), ('admit_rate', 'Why did admissions change?')]


def cached(path):
    """Briefing for the current data release, computed once and reused."""
    import json
    from pathlib import Path
    path = Path(path)
    digest = json.loads((path / 'run.json').read_text())['source_digest']
    target = path / 'briefing.json'
    if target.exists():
        saved = json.loads(target.read_text())
        if saved.get('schema') == VERSION and saved.get('source_digest') == digest: return {**saved, 'cache_hit': True}
    result = {**build(path), 'source_digest': digest}
    target.write_text(json.dumps(result, default=str))
    return {**result, 'cache_hit': False}


def build(path):
    started = time.perf_counter()
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        everyone = [r[0] for r in db.execute('SELECT patient_id FROM patient')]
    finally:
        db.close()
    cards = []
    for title, spec, follow in DISCOVERY:
        r = discover.execute(path, everyone, {'outcome': spec, 'pairs': False})
        for c in r['cards'][:3]:
            seg = ', '.join(f'{k}={v}' for k, v in c['segment'].items())
            cards.append({'kind': 'concentration' if c['kind'] != 'documentation-limited' else 'data gap', 'topic': title,
                          'headline': (f"{title}: {seg} at {c['rate']:.0%} vs {c['rest_rate']:.0%} elsewhere "
                                       f"({c['with_outcome']} of {c['patients']} patients, q={c['q']:.1g})"
                                       + (' - likely a documentation gap' if c['kind'] == 'documentation-limited' else '')),
                          'patients': c['with_outcome'], 'strength': abs(c['z']), 'follow_up': follow, 'evidence': c})
    for concept, direction, follow in TRAJECTORIES:
        r = trajectory.execute(path, everyone, {'concept': concept, 'direction': direction})
        if r['flagged']:
            top = r['patients'][0]
            cards.append({'kind': 'trajectory', 'topic': concept,
                          'headline': (f"{r['flagged']} patients show a material {direction}ward {concept} trajectory "
                                       f"(e.g. {top['patient_id']}: {top['from']} -> {top['to']} {r['unit']} since {top['changed_on']})"),
                          'patients': r['flagged'], 'strength': min(top['z'], 10), 'follow_up': follow,
                          'evidence': {'patients': r['patients'][:10], 'method': r['method']}})
    for concept, threshold, horizon, follow, label in FORECASTS:
        r = trajectory.execute(path, everyone, {'concept': concept, 'direction': 'any', 'threshold': threshold, 'horizon_days': horizon})
        if r['flagged']:
            top = r['patients'][0]
            cards.append({'kind': 'forecast', 'topic': concept,
                          'headline': (f"{r['flagged']} patients are projected to cross {label} within {horizon // 30} months "
                                       f"(e.g. {top['patient_id']}: {top['last_value']} now, {top['slope_per_year']:+} per year, "
                                       f"{top['p_cross']:.0%} chance)"),
                          'patients': r['flagged'], 'strength': 4 + 4 * top['p_cross'], 'follow_up': follow,
                          'evidence': {'patients': r['patients'][:10], 'method': r['method']}})
    for measure, follow in CHANGES:
        r = vbc.execute(path, None, {'metric': 'change', 'measure': measure})
        s = r['rows'][0]
        if s['value_a'] or s['value_b']:
            rel = (s['value_b'] - s['value_a']) / s['value_a'] if s['value_a'] else math.inf
            data_issue = any(f.get('kind') == 'event_type_gap' for f in s['feed_alerts'])
            if abs(rel) >= 0.05 or data_issue:
                cards.append({'kind': 'data gap' if data_issue else 'metric change', 'topic': measure,
                              'headline': f"{measure} {s['value_a']} -> {s['value_b']} ({'+' if rel > 0 else ''}{rel:.0%}): {s['reading']}"
                                          if rel != math.inf else f"{measure} {s['value_a']} -> {s['value_b']}: {s['reading']}",
                              'patients': None, 'strength': 6 if data_issue else min(abs(rel) * 20, 8), 'follow_up': follow, 'evidence': r['rows'][:5]})
    util = vbc.execute(path, None, {'metric': 'utilization', 'group_by': 'pcp'})
    mean = sum(r['ed_visits'] for r in util['rows']) / max(sum(r['member_years'] for r in util['rows']), 1e-9) * 1000
    for r in util['rows']:
        ci = r.get('ed_per_1000_ci95')
        if ci and ci[0] > mean and r['members'] >= 20:
            cards.append({'kind': 'outlier', 'topic': 'ED use by PCP',
                          'headline': f"PCP {r['group']}: ED {r['ed_per_1000_shrunk']}/1000 (95% CI {ci[0]}-{ci[1]}) vs {mean:.0f} overall, after shrinkage",
                          'patients': r['members'], 'strength': (r['ed_per_1000_shrunk'] - mean) / max((ci[1] - ci[0]) / 4, 1e-9),
                          'follow_up': 'What are ED visits per 1,000 by PCP?', 'evidence': r})
    for c in cards:
        c['score'] = round(c['strength'] * math.log(2 + (c['patients'] or 10)), 2)
    cards.sort(key=lambda c: -c['score'])
    return {'schema': VERSION, 'cards': cards, 'elapsed_seconds': round(time.perf_counter() - started, 1), 'api_calls': 0,
            'method': 'Discovery (BH-controlled segment tests), uncertainty-weighted lab trajectories, Kitagawa change decomposition with '
                      'feed checks, and empirical-Bayes utilization outliers; ranked by evidence strength x patients affected.'}
