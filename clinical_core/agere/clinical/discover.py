"""Population pattern discovery: where is an outcome concentrated, and is it real?

Ported from insight-engine's discovery layer (which found 7/7 planted patterns
where measure- and SQL-semantic-layer baselines found 2-5). For a population and
an outcome expressible as Agere predicates, every segment (practice, PCP, payer,
age band, sex, social-screen flags) and every pair of segments is tested against
the rest of the population with a two-proportion test; Benjamini-Hochberg
controls false discoveries across all cells. A card is emitted only when the
cell is large enough, the excess is material, and q is small.

Documentation guard: an apparent excess of an outcome defined by *absence*
(e.g. "no A1c test") can be a recording gap. For each card the share of
segment members with any record in the outcome's source streams is compared
with the rest; a materially lower share labels the card "documentation-limited".
"""
from collections import defaultdict
from itertools import combinations
import json, math, sqlite3

from . import aggregate, cohorts, semantic_layer
from ..mathematics import benjamini_hochberg

VERSION = 'population-discovery/1'
SEGMENTS = ('practice', 'pcp', 'coverage_type', 'age_band', 'sex', 'food_difficulty', 'transport_difficulty', 'financial_hardship')
MIN_COUNT, MIN_LIFT, MIN_ABS_EXCESS, MAX_Q = 8, 1.5, 0.10, 0.05


def validate(p):
    if not isinstance(p, dict) or set(p) - {'outcome', 'segments', 'pairs', 'direction'}:
        raise ValueError('discover parameters: outcome (cohort specification), segments, pairs, direction')
    spec = p.get('outcome')
    if not isinstance(spec, dict): raise ValueError('outcome must be a cohort specification')
    segments = p.get('segments') or list(SEGMENTS)
    if any(s not in SEGMENTS for s in segments): raise ValueError(f'segments must be from {SEGMENTS}')
    direction = p.get('direction', 'high')
    if direction not in ('high', 'low', 'both'): raise ValueError('direction is high, low or both')
    return {'outcome': spec, 'segments': segments, 'pairs': bool(p.get('pairs', True)), 'direction': direction}


def _z(a, n1, b, n2):
    if not n1 or not n2: return 0.0
    p = (a + b) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) or 1e-9
    return (a / n1 - b / n2) / se


def _p_two_sided(z):
    return math.erfc(abs(z) / math.sqrt(2))


def _segments(path, members):
    """Segment labels per patient from the semantic layer (attribution, payer, demographics, social screens)."""
    db = sqlite3.connect(f'file:{semantic_layer.ensure(path)}?mode=ro', uri=True)
    try:
        seg = {pid: {'practice': pr or 'unattributed', 'pcp': pc or 'unattributed', 'coverage_type': ct or 'unknown',
                     'age_band': aggregate.age_band(age), 'sex': sex or 'unknown',
                     'food_difficulty': 'not reported', 'transport_difficulty': 'not reported', 'financial_hardship': 'not reported'}
               for pid, pr, pc, ct, age, sex in db.execute(
                   'SELECT patient_id, pcp_practice_id, pcp_clinician_id, coverage_type, age_reported, sex_reported FROM patient')
               if pid in members}
        flags = {'food_difficulty_reported': 'food_difficulty', 'transport_difficulty_reported': 'transport_difficulty',
                 'difficulty_paying_for_care_reported': 'financial_hardship', 'difficulty_paying_medical_bills': 'financial_hardship'}
        for pid, payload in db.execute("SELECT patient_id, payload FROM record WHERE stream IN ('social_screens','financial_screens')"):
            if pid not in seg: continue
            r = json.loads(payload)
            for field, name in flags.items():
                if r.get(field) is True: seg[pid][name] = 'reported'
                elif r.get(field) is False and seg[pid][name] == 'not reported': seg[pid][name] = 'screened negative'
        coverage = defaultdict(set)
        for stream, pid in db.execute('SELECT DISTINCT stream, patient_id FROM record'):
            coverage[stream].add(pid)
    finally:
        db.close()
    return seg, coverage


def _outcome_streams(spec):
    streams = set()
    for atom in sum(spec.values(), []):
        if atom.get('kind') == 'record': streams.add(atom['stream'])
        elif atom.get('kind') == 'measurement' or atom.get('concept') in cohorts.concepts.MEASUREMENTS: streams.update({'labs', 'vitals'})
        elif atom.get('concept') in cohorts.concepts.ENCOUNTERS: streams.update({'encounters', 'adt'})
        elif atom.get('kind') == 'concept': streams.update({'conditions', 'condition_assessments'})
    return streams


def execute(path, patient_ids, params):
    p = validate(params)
    members = set(patient_ids)
    selected = cohorts.select(path, p['outcome'], patient_ids=sorted(members))
    positive = set(selected['patient_ids']) & members
    unknown = set(selected['unknown_patient_ids']) & members
    population = members - unknown
    seg, coverage = _segments(path, population)
    streams = _outcome_streams(p['outcome'])
    documented = set().union(*(coverage[s] for s in streams)) if streams else population
    n_all, k_all = len(population), len(positive)
    base = k_all / n_all if n_all else 0
    cells = []
    keys = [(s,) for s in p['segments']] + (list(combinations(p['segments'], 2)) if p['pairs'] else [])
    for key in keys:
        groups = defaultdict(set)
        for pid in population:
            groups[tuple(seg[pid][k] for k in key)].add(pid)
        for value, pids in groups.items():
            n1 = len(pids)
            if n1 < MIN_COUNT or n1 == n_all: continue
            a = len(pids & positive)
            b, n2 = k_all - a, n_all - n1
            z = _z(a, n1, b, n2)
            rate, rest = a / n1, (b / n2 if n2 else 0)
            cells.append({'segment': dict(zip(key, value)), 'patients': n1, 'with_outcome': a, 'rate': round(rate, 4),
                          'rest_rate': round(rest, 4), 'lift': round(rate / rest, 3) if rest else None,
                          'excess_patients': round(a - n1 * rest, 1), 'z': round(z, 3), 'p': _p_two_sided(z), 'pids': pids})
    qs = benjamini_hochberg([c['p'] for c in cells]) if cells else []
    cards = []
    for c, q in zip(cells, qs):
        c['q'] = float(q)
        high = c['rate'] > c['rest_rate']
        if p['direction'] == 'high' and not high or p['direction'] == 'low' and high: continue
        material = abs(c['rate'] - c['rest_rate']) >= MIN_ABS_EXCESS or (c['lift'] and (c['lift'] >= MIN_LIFT or c['lift'] <= 1 / MIN_LIFT))
        if c['q'] > MAX_Q or not material or (high and c['with_outcome'] < MIN_COUNT): continue
        doc_in = len(c['pids'] & documented) / c['patients']
        doc_out = len((population - c['pids']) & documented) / max(len(population - c['pids']), 1)
        card = {k: v for k, v in c.items() if k not in ('pids', 'p')}
        card['q'] = round(c['q'], 6)
        card['documentation'] = {'segment_share_with_source_records': round(doc_in, 3), 'rest_share': round(doc_out, 3)}
        card['kind'] = 'documentation-limited' if doc_in < 0.7 * doc_out else ('concentration' if high else 'protective')
        card['patients_with_outcome'] = sorted(c['pids'] & positive)[:50]
        cards.append(card)
    # Keep the most specific non-redundant cards: drop a pair card explained by one of its single-segment cards.
    singles = {tuple(sorted(c['segment'].items())): c for c in cards if len(c['segment']) == 1}
    def redundant(card):
        if len(card['segment']) == 1: return False
        for k, v in card['segment'].items():
            s = singles.get(((k, v),))
            if s and abs(s['rate'] - card['rate']) < MIN_ABS_EXCESS / 2: return True
        return False
    cards = sorted((c for c in cards if not redundant(c)), key=lambda c: (c['q'], -abs(c['excess_patients'])))
    return {'schema': VERSION, 'population': n_all, 'with_outcome': k_all, 'base_rate': round(base, 4),
            'unknown_excluded': len(unknown), 'cells_tested': len(cells), 'cards': cards[:25],
            'method': f'Two-proportion tests for every segment and segment pair vs the rest; Benjamini-Hochberg q <= {MAX_Q}; '
                      f'cells >= {MIN_COUNT} patients; excess >= {MIN_ABS_EXCESS:.0%} or lift >= {MIN_LIFT}. '
                      'Documentation-limited = segment members are much less often present in the outcome source streams.'}
