"""Proactive briefing: the insights nobody asked for, computed locally and ranked.

Runs Agere's own analytics over the whole population without a question:
discovery on actionable outcomes, material lab trajectories, metric-change
explanations (with data-feed checks), shrinkage-ranked utilization outliers, and
monitor findings (control-chart signals, data-feed gaps, claims review flags) and
'what moved' market cards (the most surprising practice moves, network-wide vs idiosyncratic).
Each card carries its evidence, the method and a follow-up question that
reproduces it through the normal answer path. Cards are ranked by information x
impact: surprise in bits times log2(2 + patients affected), the cold-start prior of briefing_rank, which learns
the order from card feedback (opened / asked follow-up / dismissed). No model calls.
"""
import math, sqlite3, time

from . import discover, market, monitor, trajectory, vbc, semantic_layer

VERSION = 'briefing/5'  # /5: cards carry card_id, ranking features and 'why' (briefing_rank)
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
    annual = None
    try:
        watch = monitor.execute(path, None, {'check': 'all'})
        annual = watch.get('annual_allowed_per_member')
        cards += monitor_cards(watch)
    except ValueError:  # too little history to chart: the other cards stand on their own
        watch = None
    try:  # markets view: the month's most surprising practice moves (feed gaps already excluded inside market)
        cards += market.market_cards(market.execute(path, None, {}))
    except ValueError:
        pass
    for c in cards:
        rank(c, annual)
    cards.sort(key=lambda c: -c['score'])
    from . import briefing_rank  # cold-start order: the information x impact prior plus MMR diversity (no history, no explore)
    cards = briefing_rank.rank(cards, explore=False)
    return {'schema': VERSION, 'cards': cards, 'elapsed_seconds': round(time.perf_counter() - started, 1), 'api_calls': 0,
            'monitor': {k: watch[k] for k in ('tests_examined', 'look_elsewhere_bits', 'period')} if watch else None,
            'method': 'Discovery (BH-controlled segment tests), uncertainty-weighted lab trajectories, Kitagawa change decomposition with '
                      'feed checks, empirical-Bayes utilization outliers, and statistical process control / feed / claims-integrity '
                      'monitoring. Ranked by information x impact: surprise in bits (-log2 of the multiplicity-adjusted p - BH q for '
                      'discovery, -log2 p minus the look-elsewhere cost for monitor findings, the two-sided normal tail of the '
                      'standardised strength otherwise) times log2(2 + patients affected, with dollars counted as member-years of '
                      'average allowed spend). That rule is the cold-start prior of a learning-to-rank model (briefing_rank): '
                      'cards are re-ordered for diversity (MMR) and, as feedback accrues, by a pairwise ranker over surprise, impact, '
                      'actionability, novelty, confidence, data-quality and recency, with one Thompson-sampled explore slot.'}


MAX_CARD_BITS = 40.0


def surprise_bits(card):
    """Multiplicity-adjusted surprise of a card, in bits."""
    if card.get('surprise_bits') is not None: return float(card['surprise_bits'])
    q = (card.get('evidence') or {}).get('q') if isinstance(card.get('evidence'), dict) else None
    if isinstance(q, (int, float)) and q > 0: return round(min(MAX_CARD_BITS, -math.log2(q)), 2)
    return monitor.z_bits(card.get('strength') or 0.0)


def rank(card, annual=None):
    card['surprise_bits'] = surprise_bits(card)
    card['impact'] = round((card.get('patients') or 0) + ((card.get('dollars') or 0) / annual if annual else 0), 1)
    card['score'] = monitor.score(card['surprise_bits'], card['impact'] or 10)
    return card


def monitor_cards(watch, limit=10):
    """Salient monitor findings as briefing cards: one per distinct (kind of problem, series/stream), echoes left out."""
    cards, seen = [], set()
    kinds = {'data_feed': 'data gap', 'rate': 'anomaly', 'claims_review_flag': 'review flag'}
    same = {'event_type_gap': 'gap', 'feed_gap': 'gap'}
    for f in watch['rows']:
        if not f['salient'] or f.get('echo'): continue
        key = (same.get(f['category'], f['category']), f.get('series') or f.get('stream') or f['group'])
        if key in seen: continue
        seen.add(key)
        cards.append({'kind': kinds.get(f['kind'], 'anomaly'), 'topic': (f.get('series') or f.get('stream') or f['category']).replace('_', ' '),
                      'headline': f['headline'], 'patients': f.get('patients'), 'dollars': f.get('dollars'),
                      'strength': f['excess_bits'], 'surprise_bits': max(f['excess_bits'], 0.0), 'follow_up': f['follow_up'],
                      'evidence': {k: f.get(k) for k in ('kind', 'category', 'group_by', 'group', 'months', 'rules', 'surprise_bits',
                                                         'look_elsewhere_bits', 'excess_bits')}})
        if len(cards) >= limit: break
    return cards


def summarize(report, top=10):
    """The briefing as an answer: value sentence and one row per card with the reason for its position."""
    cards = report.get('cards') or []
    rows = [{'rank': c.get('rank', i + 1), 'kind': c.get('kind'), 'topic': c.get('topic'), 'headline': c.get('headline'),
             'patients': c.get('patients'), 'surprise_bits': c.get('surprise_bits'), 'score': c.get('rank_score'),
             'explore': bool(c.get('explore')), 'why': c.get('why_text'), 'follow_up': c.get('follow_up'), 'card_id': c.get('card_id')}
            for i, c in enumerate(cards[:top])]
    if not rows: return 'No significant findings in this data release.', rows
    return (f"{len(cards)} findings; top: {rows[0]['headline']}" + (f" (ask: {rows[0]['follow_up']})" if rows[0]['follow_up'] else '')), rows
