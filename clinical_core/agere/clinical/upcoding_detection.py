"""Upcoding detection and incentive-compatible rule design ('upcoding_detection' operation).

focus='detect' (review prompts, never findings of fraud):
  em        E/M level intensity per rendering NPI within (E/M family x specialty) peers. Families: office established
            99211-99215 (top 99215), office new 99202-99205 (top 99205), ED 99281-99285 (top 99285). Top-level share tested
            against a beta-binomial prior fitted to the leave-one-out peers (method of moments; between-provider variance net of
            binomial noise), upper-tail p, Benjamini-Hochberg across every test; trend = top-level share second half vs first
            half of the period (two-proportion z). Excess top-level visits x observed allowed price gap (top - next level).
  drg       DRG severity creep: MCC share of MS-DRG families that split on MCC (curated MS-DRG v41/v42 family table below,
            CMS ICD-10 MS-DRG Definitions Manual) per billing hospital vs peers (beta-binomial, BH) and the MCC share by year.
  risk      risk-adjustment coding response: CMS-HCC V28 payment-HCC count and raw score per member-year (payer claim diagnoses,
            risk_adjustment.score) for Medicare Advantage (medicare_advantage, dual_ma) vs FFS (medicare_ffs, dual_ffs); panel
            score growth for members present in consecutive years; coding-intensity differential = MA growth - FFS growth (the
            MedPAC approach); HCCs supported only by assessment claims (home POS 12, AWV G0438/G0439, HRA 96160/96161, home visits
            99341-99350) with no other claim carrying that HCC in the year (the OIG "diagnoses only on HRAs" pattern).
focus='design' (mechanism design):
  deterrence  an upcoding gain is deterred iff audit_rate x detection_prob x (1 + penalty_multiplier) >= 1 (expected repayment +
            penalty >= gain). Gain per upcoded member-year = score delta x benchmark PMPM x 12; per upcoded visit = observed
            top-minus-next E/M price. Reports required audit rate and required penalty multiplier.
  attribution manipulability of plurality attribution on primary-care E/M visits: patients whose runner-up provider is within
            one visit of the attributed provider (one added visit flips or ties the assignment), under the stated lookback and
            a 24-month alternative.
"""
from collections import Counter, defaultdict
from datetime import date
import math
import re

import numpy as np
from scipy.stats import betabinom, norm

from . import revenue_common as rc

VERSION = 'upcoding-detection/1'
OP = 'upcoding_detection'
FOCUS = ('detect', 'design')
CHECKS = ('em', 'drg', 'risk')
EM_FAMILIES = {'office_established': (('99211', '99212', '99213', '99214', '99215'), '99215'),
               'office_new': (('99202', '99203', '99204', '99205'), '99205'),
               'ed': (('99281', '99282', '99283', '99284', '99285'), '99285')}
EM_CODES = {c: f for f, (codes, _) in EM_FAMILIES.items() for c in codes}
ASSESSMENT_CODES = ('G0438', 'G0439', '96160', '96161', '99341', '99342', '99344', '99345', '99347', '99348', '99349', '99350')
MA_LINES, FFS_LINES = ('medicare_advantage', 'dual_ma'), ('medicare_ffs', 'dual_ffs')
PRIMARY_CARE = re.compile(r'family|internal medicine|general practice|geriatric|pediatric|nurse practitioner|physician assistant|primary', re.I)
# MS-DRG -> (family, True = "with MCC" level, False = without MCC). CMS ICD-10 MS-DRG Definitions Manual v41/v42 (FY2024/FY2025).
_TRIPLES = {'heart failure and shock': (291, 292, 293), 'simple pneumonia': (193, 194, 195), 'COPD': (190, 191, 192),
            'GI hemorrhage': (377, 378, 379), 'renal failure': (682, 683, 684), 'diabetes': (637, 638, 639),
            'cardiac arrhythmia': (308, 309, 310), 'stroke': (64, 65, 66), 'AMI discharged alive': (280, 281, 282),
            'vaginal delivery': (805, 806, 807), 'cesarean section w/o sterilization': (786, 787, 788),
            'cesarean section w sterilization': (783, 784, 785), 'GI obstruction': (388, 389, 390), 'disorders of pancreas': (438, 439, 440)}
_PAIRS = {'septicemia': (871, 872), 'major joint replacement lower extremity': (469, 470), 'hip replacement for hip fracture': (521, 522),
          'kidney and urinary tract infections': (689, 690), 'signs and symptoms': (947, 948), 'cellulitis': (602, 603),
          'esophagitis and misc digestive': (391, 392), 'red blood cell disorders': (811, 812), 'nutritional and metabolic': (640, 641)}
DRG_FAMILY = {}
for _f, (_a, _b, _c) in _TRIPLES.items(): DRG_FAMILY.update({_a: (_f, True), _b: (_f, False), _c: (_f, False)})
for _f, (_a, _b) in _PAIRS.items(): DRG_FAMILY.update({_a: (_f, True), _b: (_f, False)})
DRG_FAMILY[870] = ('septicemia', True)

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'UPCODING detection (E/M level 5 share vs specialty peers, DRG MCC severity creep by hospital, MA vs FFS risk-score '
                  'coding intensity and assessment-only HCCs) and MECHANISM DESIGN (is an incentive/attribution rule gameable: '
                  'deterrence audit rate / penalty, attribution flips from one added visit).'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'focus': 'detect|design', 'checks': ['em', 'drg', 'risk'], 'months': 24, 'fdr': 0.05, 'min_claims': 30,
                           'audit_rate': 0.05, 'detection_prob': 0.5, 'penalty_multiplier': 1.0, 'benchmark_pmpm': 0,
                           'score_delta': 0, 'lookback_months': 12, 'top': 25},
            'meaning': 'detect: E/M top-level share per provider vs leave-one-out specialty peers (beta-binomial tail, BH), trend; MCC share '
                       'of MCC-split MS-DRG families per hospital vs peers and by year; CMS-HCC V28 score/HCC growth MA vs FFS (coding '
                       'intensity differential) and HCCs supported only by assessment/home claims. design: deterrence condition audit x '
                       'detection x (1 + penalty) >= 1 with required audit rate / penalty and dollar gain per upcoded member-year or visit; '
                       'plurality attribution manipulability (patients flipped or tied by one added visit) vs a 24-month lookback. Flags '
                       'are review prompts, never findings of fraud.'}
NATIVE = ('value', 'count')


def schema(obj, string, array):
    return obj({'focus': string(FOCUS), 'checks': array(string(CHECKS), 'Detection checks; empty = all'),
                'months': {'type': 'integer', 'minimum': 3, 'maximum': 60, 'description': 'Detection window ending at the as-of date'},
                'fdr': {'type': 'number', 'minimum': 0.001, 'maximum': 0.5}, 'min_claims': {'type': 'integer', 'minimum': 5, 'maximum': 1000},
                'audit_rate': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'detection_prob': {'type': 'number', 'minimum': 0, 'maximum': 1},
                'penalty_multiplier': {'type': 'number', 'minimum': 0, 'maximum': 10},
                'benchmark_pmpm': {'type': 'number', 'minimum': 0, 'description': 'Benchmark/payment PMPM (0 = observed MA allowed PMPM)'},
                'score_delta': {'type': 'number', 'minimum': 0, 'maximum': 2, 'description': 'Risk-score gain from upcoding (0 = observed MA-FFS differential)'},
                'lookback_months': {'type': 'integer', 'minimum': 3, 'maximum': 36}, 'top': {'type': 'integer', 'minimum': 1, 'maximum': 200}})


def validate(p):
    allowed = set(CONTRACT['parameters'])
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('upcoding_detection parameters: ' + ', '.join(sorted(allowed)))
    out = {'focus': p.get('focus', 'detect'), 'checks': sorted(set(p.get('checks') or CHECKS)), 'months': p.get('months', 24),
           'fdr': float(p.get('fdr', 0.05)), 'min_claims': p.get('min_claims', 30), 'audit_rate': float(p.get('audit_rate', 0.05)),
           'detection_prob': float(p.get('detection_prob', 0.5)), 'penalty_multiplier': float(p.get('penalty_multiplier', 1.0)),
           'benchmark_pmpm': float(p.get('benchmark_pmpm') or 0), 'score_delta': float(p.get('score_delta') or 0),
           'lookback_months': p.get('lookback_months', 12), 'top': p.get('top', 25)}
    if out['focus'] not in FOCUS: raise ValueError(f'focus: {FOCUS}')
    if any(c not in CHECKS for c in out['checks']): raise ValueError(f'checks: subset of {CHECKS}')
    for k, lo, hi in (('months', 3, 60), ('min_claims', 5, 1000), ('lookback_months', 3, 36), ('top', 1, 200)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k}: integer {lo}..{hi}')
    for k, lo, hi in (('fdr', 0.001, 0.5), ('audit_rate', 0, 1), ('detection_prob', 0, 1), ('penalty_multiplier', 0, 10),
                      ('score_delta', 0, 2)):
        if not lo <= out[k] <= hi: raise ValueError(f'{k}: {lo}..{hi}')
    if out['benchmark_pmpm'] < 0: raise ValueError('benchmark_pmpm >= 0')
    return out


# ---- statistics ---------------------------------------------------------------------------------------------------------
def bb_prior(events, ns):
    """Beta(a, b) from peer counts by method of moments (between-unit variance net of binomial noise)."""
    events, ns = np.asarray(events, float), np.asarray(ns, float)
    keep = ns > 0
    if keep.sum() == 0: return 1.0, 1.0
    r = events[keep] / ns[keep]
    m = float(np.average(r, weights=ns[keep]))
    m = min(max(m, 1e-4), 1 - 1e-4)
    between = float(np.average((r - m) ** 2, weights=ns[keep])) - float(np.mean(m * (1 - m) / ns[keep]))
    strength = m * (1 - m) / between - 1 if between > 1e-9 else 1000.0
    strength = min(max(strength, 2.0), 1000.0)
    return m * strength, (1 - m) * strength


def peer_tests(units, fdr):
    """units: {key: (group, events, n)} -> rows with leave-one-out beta-binomial upper-tail p and BH q."""
    by_group = defaultdict(list)
    for k, (g, x, n) in units.items(): by_group[g].append(k)
    rows = []
    for k, (g, x, n) in units.items():
        peers = [j for j in by_group[g] if j != k]
        if len(peers) < 2: continue
        a, b = bb_prior([units[j][1] for j in peers], [units[j][2] for j in peers])
        mean = a / (a + b)
        p = float(betabinom.sf(x - 1, n, a, b)) if x > 0 else 1.0
        rows.append({'unit': k, 'group': g, 'events': x, 'n': n, 'rate': round(x / n, 4), 'peer_rate': round(mean, 4),
                     'shrunk_rate': round((x + a) / (n + a + b), 4), 'excess': round(x - n * mean, 1), 'p': p, 'peers': len(peers)})
    for r, q in zip(rows, rc.bh([r['p'] for r in rows])):
        r['q'] = round(q, 5); r['flag'] = q <= fdr and r['rate'] > r['peer_rate']
        r['p'] = round(r['p'], 6)
    return sorted(rows, key=lambda r: (not r['flag'], r['q'], -r['excess']))


def two_prop(x1, n1, x2, n2):
    if not n1 or not n2: return None
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 else 0
    return float(2 * norm.sf(abs((x2 / n2 - x1 / n1) / se))) if se else 1.0


def _in(keep, pid):
    return keep is None or pid in keep


# ---- E/M ----------------------------------------------------------------------------------------------------------------
def em_rows(db, start, end, keep):
    spec = {}
    for npi, s in db.execute('SELECT npi, specialty FROM clinician WHERE npi IS NOT NULL'): spec.setdefault(str(npi), s or 'unknown')
    out = []
    marks = ','.join('?' * len(EM_CODES))
    for pid, npi, cpt, d, allowed in db.execute(
            f'SELECT c.patient_id, c.rendering_npi, l.cpt, c.service_from, l.allowed_cents FROM payer_claim_line l JOIN payer_claim c '
            f'ON c.payer_claim_id = l.payer_claim_id WHERE l.counts_in_metrics = 1 AND c.counts_in_metrics = 1 AND l.cpt IN ({marks}) '
            f'AND c.service_from BETWEEN ? AND ?', (*EM_CODES, start, end)):
        if not npi or not _in(keep, pid): continue
        out.append((str(npi), spec.get(str(npi), 'unknown'), cpt, d, (allowed or 0) / 100))
    return out


def em_check(rows, p, start, end):
    mid = (date.fromisoformat(start).toordinal() + date.fromisoformat(end).toordinal()) // 2
    mid = date.fromordinal(mid).isoformat()
    cells = defaultdict(lambda: [0, 0, 0, 0, 0])  # top, n, top_first, n_first, levels sum
    prices = defaultdict(list)
    spec_of = {}
    for npi, spec, cpt, d, allowed in rows:
        fam = EM_CODES[cpt]; codes, top = EM_FAMILIES[fam]
        c = cells[(npi, fam)]; is_top = cpt == top
        c[0] += is_top; c[1] += 1; c[4] += codes.index(cpt) + 1
        if d < mid: c[2] += is_top; c[3] += 1
        spec_of[npi] = spec
        if allowed > 0: prices[cpt].append(allowed)
    gap = {}
    for fam, (codes, top) in EM_FAMILIES.items():
        hi, lo = prices.get(top), prices.get(codes[-2])
        gap[fam] = round(float(np.median(hi) - np.median(lo)), 2) if hi and lo else 0.0
    eligible = {k: v for k, v in cells.items() if v[1] >= p['min_claims']}
    groups = Counter((spec_of[n], f) for n, f in eligible)
    units = {k: ((spec_of[k[0]], k[1]) if groups[(spec_of[k[0]], k[1])] >= 3 else ('all specialties', k[1]), v[0], v[1])
             for k, v in eligible.items()}
    tested = peer_tests(units, p['fdr'])
    out = []
    for r in tested:
        npi, fam = r['unit']; c = cells[r['unit']]
        first = c[2] / c[3] if c[3] else None
        second = (c[0] - c[2]) / (c[1] - c[3]) if c[1] - c[3] else None
        out.append({'check': 'em', 'provider_npi': npi, 'specialty': spec_of[npi], 'family': fam, 'top_code': EM_FAMILIES[fam][1],
                    'visits': r['n'], 'top_level_visits': r['events'], 'top_share': r['rate'], 'peer_share': r['peer_rate'],
                    'peer_group': ' / '.join(r['group']), 'excess_top_visits': r['excess'],
                    'dollars_at_stake': round(max(r['excess'], 0) * gap[fam], 2), 'mean_level': round(c[4] / c[1], 2),
                    'share_first_half': round(first, 4) if first is not None else None,
                    'share_second_half': round(second, 4) if second is not None else None,
                    'trend_p': (lambda v: round(v, 5) if v is not None else None)(two_prop(c[2], c[3], c[0] - c[2], c[1] - c[3])),
                    'p': r['p'], 'q': r['q'], 'flag': r['flag']})
    return out, gap, len(cells)


# ---- DRG ----------------------------------------------------------------------------------------------------------------
def drg_check(db, start, end, keep, p):
    names = {str(n): d for n, d in db.execute('SELECT npi, display_name FROM organization WHERE npi IS NOT NULL')}
    cells = defaultdict(lambda: [0, 0]); years = defaultdict(lambda: [0, 0]); fams = Counter()
    for pid, npi, drg, d in db.execute('SELECT patient_id, billing_npi, drg, service_from FROM payer_claim WHERE drg IS NOT NULL AND '
                                       'counts_in_metrics = 1 AND service_from BETWEEN ? AND ?', (start, end)):
        if not _in(keep, pid): continue
        try: code = int(str(drg).strip()[:3])
        except ValueError: continue
        fam = DRG_FAMILY.get(code)
        if not fam: continue
        c = cells[str(npi or 'unknown')]; c[0] += fam[1]; c[1] += 1
        y = years[str(d)[:4]]; y[0] += fam[1]; y[1] += 1; fams[fam[0]] += 1
    minimum = max(5, p['min_claims'] // 3)
    units = {k: ('all hospitals', v[0], v[1]) for k, v in cells.items() if v[1] >= minimum}
    rows = [{'check': 'drg', 'hospital_npi': r['unit'], 'hospital': names.get(r['unit'], r['unit']), 'mcc_split_stays': r['n'],
             'mcc_stays': r['events'], 'mcc_share': r['rate'], 'peer_share': r['peer_rate'], 'excess_mcc_stays': r['excess'],
             'p': r['p'], 'q': r['q'], 'flag': r['flag']} for r in peer_tests(units, p['fdr'])]
    by_year = [{'year': y, 'mcc_split_stays': v[1], 'mcc_share': round(v[0] / v[1], 4)} for y, v in sorted(years.items()) if v[1]]
    return rows, by_year, dict(fams.most_common())


# ---- risk adjustment ----------------------------------------------------------------------------------------------------
def member_years(db, keep, years):
    prod = defaultdict(Counter)
    for pid, month, product in db.execute('SELECT patient_id, month, product FROM member_month'):
        if month[:4] in years and _in(keep, pid) and product in MA_LINES + FFS_LINES: prod[(pid, month[:4])][product] += 1
    return {k: ('MA' if c.most_common(1)[0][0] in MA_LINES else 'FFS', c.most_common(1)[0][0], sum(c.values())) for k, c in prod.items()}


def assessment_claims(db):
    marks = ','.join('?' * len(ASSESSMENT_CODES))
    ids = {r[0] for r in db.execute(f'SELECT DISTINCT claim_id FROM procedure_line WHERE code IN ({marks}) OR cpt IN ({marks})',
                                    (*ASSESSMENT_CODES, *ASSESSMENT_CODES))}
    ids |= {r[0] for r in db.execute(f'SELECT DISTINCT payer_claim_id FROM payer_claim_line WHERE cpt IN ({marks})', ASSESSMENT_CODES)}
    ids |= {r[0] for r in db.execute("SELECT payer_claim_id FROM payer_claim WHERE place_of_service IN ('12', '13')")}
    return ids


def risk_check(db, keep, as_of):
    from . import risk_adjustment as ra
    end_year = int(as_of[:4]) - 1
    years = [str(y) for y in range(end_year - 3, end_year + 1)]
    my = member_years(db, keep, set(years))
    if not my: return {'status': 'no Medicare MA/FFS member-years in scope', 'by_group_year': [], 'differential': None}
    demo = {pid: (b, s) for pid, b, s in db.execute('SELECT patient_id, birth_date, sex_reported FROM patient')}
    assess = assessment_claims(db)
    from . import risk_adjustment as ra
    codes = defaultdict(lambda: defaultdict(set))  # (pid, year) -> icd -> claim ids
    for pid, cid, d, icd in db.execute('SELECT patient_id, payer_claim_id, service_from, icd10 FROM payer_claim_diagnosis '
                                       'WHERE counts_in_metrics = 1 AND icd10 IS NOT NULL'):
        k = (pid, str(d)[:4])
        if k in my: codes[k][ra.icd_key(icd)].add(cid)
    model = ra.load_model('V28')
    scores, assess_only, coefs = {}, defaultdict(int), []
    for k, (grp, product, mm) in my.items():
        birth, sex = demo.get(k[0], (None, None))
        try: age = ra.age_on(birth, f'{k[1]}-02-01') if birth else None
        except (TypeError, ValueError): age = None
        age = 70 if age is None else age
        seg = 'community_full_dual' if product.startswith('dual') else 'community_non_dual'
        dx = codes.get(k, {})
        s = ra.score('V28', age, 2 if sex == 'F' else 1, list(dx), segment=seg)
        scores[k] = (s['raw'], s['payment_hcc_count'])
        coefs += [t['coefficient'] for t in s['terms'] if t['kind'] == 'hcc']
        for h in s['hccs']:  # an HCC whose every supporting claim is an assessment / home claim
            support = {cid for icd in h['codes'] for cid in dx.get(icd, ())}
            if support and support <= assess: assess_only[grp] += 1
    table = []
    agg = defaultdict(list)
    for (pid, y), (raw, n) in scores.items(): agg[(my[(pid, y)][0], y)].append((raw, n))
    for (g, y), v in sorted(agg.items()):
        table.append({'check': 'risk', 'group': g, 'year': y, 'member_years': len(v), 'mean_raw_score': round(float(np.mean([a for a, _ in v])), 4),
                      'mean_payment_hccs': round(float(np.mean([b for _, b in v])), 3)})
    panel = defaultdict(list)
    for (pid, y), (raw, _) in scores.items():
        prev = scores.get((pid, str(int(y) - 1)))
        if prev is not None and my[(pid, str(int(y) - 1))][0] == my[(pid, y)][0]: panel[my[(pid, y)][0]].append(raw - prev[0])
    growth = {g: round(float(np.mean(v)), 4) for g, v in panel.items() if v}
    diff = round(growth['MA'] - growth['FFS'], 4) if {'MA', 'FFS'} <= set(growth) else None
    se = None
    if diff is not None and len(panel['MA']) > 1 and len(panel['FFS']) > 1:
        se = math.sqrt(np.var(panel['MA'], ddof=1) / len(panel['MA']) + np.var(panel['FFS'], ddof=1) / len(panel['FFS']))
    member_counts = Counter(g for g, _, _ in my.values())
    return {'status': 'computed', 'years': years, 'by_group_year': table, 'panel_growth': growth,
            'panel_pairs': {g: len(v) for g, v in panel.items()}, 'differential': diff,
            'differential_p': round(float(2 * norm.sf(abs(diff / se))), 5) if diff is not None and se else None,
            'assessment_only_hccs': dict(assess_only), 'member_years': dict(member_counts),
            'mean_hcc_coefficient': round(float(np.mean(coefs)), 4) if coefs else None,
            'model_labels': len(model['labels'])}


# ---- design -------------------------------------------------------------------------------------------------------------
def deterrence(p, gain_unit):
    k = p['audit_rate'] * p['detection_prob'] * (1 + p['penalty_multiplier'])
    req_audit = 1 / (p['detection_prob'] * (1 + p['penalty_multiplier'])) if p['detection_prob'] > 0 else None
    req_pen = 1 / (p['audit_rate'] * p['detection_prob']) - 1 if p['audit_rate'] * p['detection_prob'] > 0 else None
    return {'expected_penalty_per_dollar_gained': round(k, 4), 'deterred': k >= 1, 'net_gain_per_unit': round(gain_unit * (1 - k), 2),
            'required_audit_rate': round(min(req_audit, 1.0), 4) if req_audit is not None else None,
            'audit_rate_feasible': req_audit is not None and req_audit <= 1,
            'required_penalty_multiplier': round(req_pen, 3) if req_pen is not None else None}


def attribution(db, keep, as_of, months):
    spec = {str(n): s or '' for n, s in db.execute('SELECT npi, specialty FROM clinician WHERE npi IS NOT NULL')}
    pc_codes = [c for c in EM_CODES if not c.startswith('9928')] + ['G0438', 'G0439']
    marks = ','.join('?' * len(pc_codes))
    out = {}
    for label, m in ((f'{months}-month lookback', months), ('24-month lookback', 24)):
        start, end = rc.window(as_of, m)
        visits = defaultdict(Counter)
        for pid, npi in db.execute(f'SELECT c.patient_id, c.rendering_npi FROM payer_claim_line l JOIN payer_claim c ON c.payer_claim_id = '
                                   f'l.payer_claim_id WHERE l.counts_in_metrics = 1 AND c.counts_in_metrics = 1 AND l.cpt IN ({marks}) '
                                   f'AND c.service_from BETWEEN ? AND ?', (*pc_codes, start, end)):
            if npi and _in(keep, pid) and PRIMARY_CARE.search(spec.get(str(npi), '')): visits[pid][str(npi)] += 1
        flips = ties = 0; gainers = Counter()
        for pid, c in visits.items():
            ranked = c.most_common(2)
            first = ranked[0][1]; second = ranked[1][1] if len(ranked) > 1 else 0
            if len(ranked) > 1 and first - second == 0: flips += 1; gainers[ranked[1][0]] += 1
            elif len(ranked) > 1 and first - second == 1: ties += 1; gainers[ranked[1][0]] += 1
        n = len(visits)
        out[label] = {'attributed_patients': n, 'flip_with_one_visit': flips, 'tie_with_one_visit': ties,
                      'manipulable_share': round((flips + ties) / n, 4) if n else None,
                      'top_gainers': [{'provider_npi': k, 'patients_contestable': v} for k, v in gainers.most_common(5)]}
    return out


def ma_pmpm(db, keep, as_of):
    y = str(int(as_of[:4]) - 1)
    mm = {}
    for pid, month, product in db.execute('SELECT patient_id, month, product FROM member_month WHERE month LIKE ?', (y + '%',)):
        if product in MA_LINES and _in(keep, pid): mm[(pid, month)] = 1
    if not mm: return None
    paid = sum((a or 0) for pid, d, a in db.execute('SELECT patient_id, service_from, allowed_cents FROM payer_claim WHERE counts_in_metrics = 1 '
                                                      "AND final_status = 'paid' AND service_from LIKE ?", (y + '%',)) if (pid, str(d)[:7]) in mm)
    return round(paid / 100 / len(mm), 2)


# ---- run ----------------------------------------------------------------------------------------------------------------
def run(db, patient_ids, p, as_of):
    keep = set(patient_ids) if patient_ids is not None else None
    start, end = rc.window(as_of, p['months'])
    report = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'period': {'start': start, 'end': end}, 'rows': []}
    assumptions = ['Synthetic warehouse; payer claim lines with counts_in_metrics (final versions, no voids/duplicates).',
                   'Flags are prompts for coding review, never findings of fraud: a high level-5 share can reflect a sicker panel.']
    if p['focus'] == 'detect':
        em_gap = None
        if 'em' in p['checks']:
            em, em_gap, n_cells = em_check(em_rows(db, start, end, keep), p, start, end)
            report['em'] = {'tested': len(em), 'flagged': sum(r['flag'] for r in em), 'price_gap': em_gap, 'provider_families': n_cells}
            report['rows'] += [r for r in em if r['flag']][:p['top']] or em[:min(5, len(em))]
            assumptions.append(f"E/M peers = same specialty and E/M family (>= 3 providers with >= {p['min_claims']} visits, else all specialties); "
                               'dollars = excess top-level visits x median allowed gap to the next level.')
        if 'drg' in p['checks']:
            drg, by_year, fams = drg_check(db, start, end, keep, p)
            report['drg'] = {'tested': len(drg), 'flagged': sum(r['flag'] for r in drg), 'by_year': by_year, 'families': fams}
            report['rows'] += [r for r in drg if r['flag']][:p['top']] or drg[:3]
            assumptions.append('DRG severity uses MS-DRG families that split on MCC (curated from the CMS MS-DRG Definitions Manual v41/v42); '
                               'hospitals compared without case-mix adjustment beyond the family split.')
        if 'risk' in p['checks']:
            risk = risk_check(db, keep, as_of)
            report['risk'] = risk
            report['rows'] += risk.get('by_group_year', [])
            assumptions.append('Risk scores: CMS-HCC V28 raw scores from payer claim diagnoses per calendar year (no normalization or MA '
                               'coding-pattern adjustment); member-year group by majority product; differential from members present in '
                               'consecutive years in the same group.')
    else:
        risk = risk_check(db, keep, as_of)
        bench = p['benchmark_pmpm'] or ma_pmpm(db, keep, as_of) or 0.0
        observed = risk.get('differential') or 0.0
        delta_basis = ('supplied' if p['score_delta'] else 'observed MA-minus-FFS panel risk-score growth' if observed > 0 else
                       'mean V28 coefficient of one coded payment HCC (observed differential is not positive)')
        delta = p['score_delta'] or (observed if observed > 0 else (risk.get('mean_hcc_coefficient') or 0.0))
        em_prices = em_check(em_rows(db, start, end, keep), {**p, 'min_claims': 10**9}, start, end)[1]
        gain_member = delta * bench * 12
        rows = [{'rule': 'risk adjustment (per upcoded member-year)', 'gain_per_unit': round(gain_member, 2),
                 'score_delta': round(delta, 4), 'benchmark_pmpm': bench, **deterrence(p, gain_member)}]
        for fam, gap in em_prices.items():
            if gap: rows.append({'rule': f'E/M {fam} level upcode (per visit)', 'gain_per_unit': gap, **deterrence(p, gap)})
        attr = attribution(db, keep, as_of, p['lookback_months'])
        for label, a in attr.items():
            rows.append({'rule': f'plurality attribution, {label}', **{k: v for k, v in a.items() if k != 'top_gainers'}})
        report.update(rows=rows, attribution=attr, risk=risk, benchmark_pmpm=bench, score_delta=delta)
        assumptions += [f"Audit rate {p['audit_rate']:.0%}, detection probability {p['detection_prob']:.0%}, penalty multiplier "
                        f"{p['penalty_multiplier']:g} (repayment plus penalty) - supplied or default.",
                        f"Benchmark PMPM ${bench:,.2f} ({'supplied' if p['benchmark_pmpm'] else 'observed MA allowed PMPM, last full year'}); "
                        f"score delta {delta:.4f} ({delta_basis}).",
                        'Attribution manipulability uses plurality of primary-care E/M/AWV visits by rendering NPI (MSSP step-1 style); a margin of '
                        'one visit is a tie broken by allowed charges.']
    report['assumptions'] = assumptions
    report['method'] = (f'{VERSION}: ' + ('peer beta-binomial upper tails (leave-one-out method-of-moments prior) with Benjamini-Hochberg for '
                        'E/M top-level and DRG MCC shares; CMS-HCC V28 panel score growth MA vs FFS for coding intensity; assessment-only HCCs.'
                        if p['focus'] == 'detect' else 'deterrence (incentive-compatibility) condition audit x detection x (1 + penalty) >= 1 '
                        'with required audit rate / penalty; plurality-attribution manipulability by one added visit.'))
    return report


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def headline(r):
    p = r['parameters']
    if p['focus'] == 'design':
        rr = r['rows'][0]
        a = r['attribution'][next(iter(r['attribution']))]
        return (f"Risk-adjustment upcoding gains ${rr['gain_per_unit']:,.0f} per member-year (score +{rr['score_delta']:.3f} x "
                f"${rr['benchmark_pmpm']:,.0f} PMPM x 12); expected penalty per dollar {rr['expected_penalty_per_dollar_gained']:.2f} -> "
                f"{'deterred' if rr['deterred'] else 'NOT deterred'}; deterrence needs audit rate {rr['required_audit_rate']:.0%} or penalty "
                f"multiplier {rr['required_penalty_multiplier']:.1f}. Attribution: {a['flip_with_one_visit'] + a['tie_with_one_visit']} of "
                f"{a['attributed_patients']} patients ({(a['manipulable_share'] or 0):.0%}) flip or tie with one added visit")
    parts = []
    if 'em' in r: parts.append(f"E/M: {r['em']['flagged']} of {r['em']['tested']} provider-families above peers on top-level share")
    if 'drg' in r:
        y = r['drg']['by_year']
        trend = f" (MCC share {y[0]['mcc_share']:.0%} in {y[0]['year']} -> {y[-1]['mcc_share']:.0%} in {y[-1]['year']})" if len(y) > 1 else ''
        parts.append(f"DRG: {r['drg']['flagged']} of {r['drg']['tested']} hospitals above peers on MCC share{trend}")
    if 'risk' in r and r['risk'].get('differential') is not None:
        parts.append(f"risk coding: MA risk score grows {r['risk']['panel_growth'].get('MA', 0):+.3f}/yr vs FFS "
                     f"{r['risk']['panel_growth'].get('FFS', 0):+.3f} (differential {r['risk']['differential']:+.3f}); "
                     f"assessment-only HCCs MA {r['risk']['assessment_only_hccs'].get('MA', 0)}, FFS {r['risk']['assessment_only_hccs'].get('FFS', 0)}")
    return 'Upcoding review: ' + ('; '.join(parts) if parts else 'no eligible claims in the period')


def answer(report, plan, question):
    flagged = [x for x in report['rows'] if x.get('flag')]
    return rc.answer(report, plan, value=headline(report) + '.', table=report['rows'], number=len(flagged) if report['parameters']['focus'] == 'detect' else None,
                     definitions=[{'term': 'upcoding', 'basis': 'billing a higher-paying code (E/M level, DRG severity, HCC) than the '
                                                            'documented service supports; flagged here statistically, for review'},
                                  {'term': 'incentive compatible', 'basis': 'expected penalty (audit x detection x (1 + penalty)) >= gain'}],
                     overall_key='upcoding')


# ---- free local-compiler route ------------------------------------------------------------------------------------------
UPCODE_RX = (r'\bupcod\w*|\bcoding intensity\b|\bcode creep\b|\bseverity creep\b|\bdrg creep\b|\bdrg (?:severity|upcoding)\b|'
             r'\b(?:level[- ]?5|level five|99215|99285|99205) (?:share|rates?|visits?|billing|codes?)\b|\be/?m (?:level|coding) (?:distribution|intensity|creep|outliers?)\b|'
             r'\bmcc (?:share|rates?|capture creep)\b|\b(?:gam(?:e|ed|ing)|manipulat\w*) (?:the )?(?:risk adjustment|risk scores?|attribution|incentives?|benchmarks?|hccs?)\b|'
             r'\b(?:risk adjustment|risk scores?|attribution|incentive|payment) (?:rules? )?(?:be )?(?:gamed|gameable|manipulable)\b|\bincentive[- ]compatib\w*\b|'
             r'\bmechanism design\b|\bdeter(?:rence|red)? upcoding\b|\bhra[- ]only (?:diagnos\w+|hccs?)\b|\b(?:diagnos\w+|hccs?) only on (?:hras?|health risk assessments?|home visits?)\b')


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if not re.search(UPCODE_RX, q): return None
    if rc.knowledge_only(q): return None
    params = {}
    design = re.search(r'\bmechanism design\b|\bincentive[- ]compatib|\bgam(?:e|ed|ing|eable)\b|\bmanipulat|\bdeter|\baudit rate\b|\bpenalt', q)
    if design: params['focus'] = 'design'
    else:
        checks = [c for c, rx in (('em', r'\be/?m\b|\bevaluation and management\b|\blevel[- ]?(?:5|five|4)\b|\b992\d\d\b|\boffice visits?\b|\bed visits?\b'),
                                  ('drg', r'\bdrgs?\b|\bmcc\b|\bcc/mcc\b|\bseverity\b|\binpatient\b|\bhospitals?\b'),
                                  ('risk', r'\brisk (?:adjustment|scores?)\b|\bhccs?\b|\bcoding intensity\b|\bmedicare advantage\b|\bhras?\b|\bhealth risk assessments?\b'))
                  if re.search(rx, q)]
        if checks: params['checks'] = checks
    a = re.search(r'\b(\d+(?:\.\d+)?) ?% (?:audit|of (?:charts|claims|members) audited)|audit(?:ing)? (?:rate )?(?:of )?(\d+(?:\.\d+)?) ?%', q)
    if a: params['audit_rate'] = float(a.group(1) or a.group(2)) / 100
    pen = re.search(r'\b(\d+(?:\.\d+)?)x (?:penalty|penalties|damages)|(?:penalty|penalties|damages) (?:of )?(\d+(?:\.\d+)?)x', q)
    if pen: params['penalty_multiplier'] = float(pen.group(1) or pen.group(2))
    if re.search(r'\btreble damages\b', q): params['penalty_multiplier'] = 2.0
    b = re.search(r'\$\s?([\d,]+(?:\.\d+)?) ?(?:pmpm|per member per month)', q)
    if b: params['benchmark_pmpm'] = float(b.group(1).replace(',', ''))
    return plan(OP, params, 'Upcoding detection / incentive-compatible rule design', question, [f"focus {params.get('focus', 'detect')}"])


TEMPLATES = ['Which providers are upcoding E/M visits compared with their specialty peers?', 'Is there DRG severity creep at our hospitals?',
             'How much coding intensity is there in our Medicare Advantage risk scores versus FFS?',
             'Can our attribution rule be gamed by adding one visit?', 'Is our risk adjustment incentive compatible at a 5% audit rate?',
             'Show level 5 share outliers among ED physicians', 'What audit rate would deter upcoding of HCCs?',
             'Are diagnoses only on health risk assessments driving risk scores?']
