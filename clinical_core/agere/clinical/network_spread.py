"""Epidemic / percolation spread of a practice pattern across the shared-patient clinician network ('network_spread').

The 'network' operation simulates a hypothetical cascade from chosen seeds. This engine starts from what was OBSERVED: when each
clinician first used a practice pattern (a drug or drug class they prescribed - payer_pharmacy_claim.prescriber_npi on paid
fills - or a CPT/HCPCS code they rendered - payer_claim_line on claims counting in metrics, rendering NPI), and asks whether the
pattern spreads along shared-patient ties, how contagious it is, and where it is heading.

Graph: clinicians (NPI) linked by patients they share (network.project over patient x clinician pairs from claims and
prescriptions), edges with >= min_shared shared patients (optionally the hypergeometric or disparity filter, network.edge_filter).
Methods:
  exposure hazard   discrete-time (monthly) hazard of first use among non-adopters by exposure e = number of neighbours who
                    adopted in earlier months; logistic slope on log(1 + e) (Christakis-Fowler / Aral-style exposure curves).
  shuffle test      Anagnostopoulos, Kumar & Mahdian (KDD 2008): permute adoption months among adopters (graph and adopter set
                    fixed) and refit; if the observed slope is not above the shuffled slopes the clustering is homophily /
                    shared environment, not contagion. p = share of shuffles with slope >= observed.
  transmissibility  independent-channel model h(e) = 1 - (1 - h0)(1 - tau)^e fitted by maximum likelihood; per-edge
                    transmissibility over 12 months T = 1 - (1 - tau)^12.
  epidemic threshold SIS threshold 1 / lambda_max of the adjacency (Wang et al. 2003; Chakrabarti et al. 2008): spread persists
                    when tau / delta exceeds it (delta = monthly discontinuation rate among adopters).
  percolation       bond percolation with occupation T: R = T (<k^2> - <k>) / <k> (Newman 2002, configuration-model
                    approximation); Monte Carlo giant component share and expected final reach from current adopters.
  forecast          Monte Carlo of the fitted monthly hazard forward months_ahead from current adopters; mean and 90% interval
                    of adopters and each clinician's probability of adopting.
All spread outputs are models with stated assumptions; adoption timing is left-censored at the start of the data (first
burn_in months' users are treated as prior adopters). Seeded RNG; synthetic data only.
"""
from collections import defaultdict
from datetime import date
import re

import numpy as np

from . import revenue_common as rc

VERSION = 'network-spread/1'
OP = 'network_spread'
KINDS = ('auto', 'drug', 'drug_class', 'procedure')
FILTERS = ('none', 'hypergeometric', 'disparity')
CLASS_WORDS = {'glp1_agonist': r'\bglp-?1s?\b|\bsemaglutide\b|\btirzepatide\b|\bozempic\b|\bmounjaro\b|\bwegovy\b',
               'sglt2_inhibitor': r'\bsglt-?2s?\b|\bsglt2 inhibitors?\b|\bempagliflozin\b|\bdapagliflozin\b|\bjardiance\b|\bfarxiga\b',
               'statin': r'\bstatins?\b', 'benzodiazepine': r'\bbenzodiazepines?\b|\bbenzos?\b', 'opioid': r'\bopioids?\b',
               'arni': r'\barnis?\b|\bsacubitril\b|\bentresto\b', 'mra': r'\bmras?\b|\bspironolactone\b',
               'anticoagulant': r'\banticoagulants?\b|\bdoacs?\b|\bapixaban\b', 'gabapentinoid': r'\bgabapentin\w*\b|\bpregabalin\b',
               'adhd_stimulant': r'\bstimulants?\b', 'proton_pump_inhibitor': r'\bppis?\b|\bproton pump\b'}

PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'PRACTICE-PATTERN CONTAGION on the clinician shared-patient network: observed adoption of a drug/drug class or '
                  'CPT code, exposure hazard by adopted neighbours with a timing-shuffle test (contagion vs homophily), per-edge '
                  'transmissibility, epidemic threshold (1/lambda_max), percolation R and giant component, forecast of next adopters.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'pattern': 'glp1_agonist|semaglutide|99497', 'kind': '|'.join(KINDS), 'months': 36, 'burn_in': 3,
                           'min_shared': 2, 'edge_filter': '|'.join(FILTERS), 'shuffles': 100, 'simulations': 200,
                           'months_ahead': 12, 'seed': 7, 'top': 25},
            'meaning': 'Observed first-use month per clinician of the pattern (prescriber NPI on paid fills, or rendering NPI on claim '
                       'lines) on the shared-patient graph; monthly exposure hazard (logistic on log(1+adopted neighbours)) with the '
                       'Anagnostopoulos timing-shuffle p-value; MLE per-edge transmissibility; SIS threshold 1/lambda_max; bond '
                       'percolation R = T(<k^2>-<k>)/<k> with Monte Carlo giant component and final reach; Monte Carlo forecast of '
                       'adopters months_ahead with per-clinician adoption probability.'}
NATIVE = ('count', 'value')


def schema(obj, string, array):
    return obj({'pattern': {'type': 'string', 'description': 'Drug class key (glp1_agonist), drug name (semaglutide) or CPT/HCPCS code'},
                'kind': string(KINDS),
                'months': {'type': 'integer', 'minimum': 6, 'maximum': 120, 'description': 'History window in months (default 36)'},
                'burn_in': {'type': 'integer', 'minimum': 0, 'maximum': 12, 'description': 'Initial months treated as prior adopters'},
                'min_shared': {'type': 'integer', 'minimum': 1, 'maximum': 50},
                'edge_filter': string(FILTERS),
                'shuffles': {'type': 'integer', 'minimum': 0, 'maximum': 1000},
                'simulations': {'type': 'integer', 'minimum': 10, 'maximum': 2000},
                'months_ahead': {'type': 'integer', 'minimum': 1, 'maximum': 36},
                'seed': {'type': 'integer'}, 'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})


def validate(p):
    allowed = {'pattern', 'kind', 'months', 'burn_in', 'min_shared', 'edge_filter', 'shuffles', 'simulations', 'months_ahead', 'seed', 'top'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError('network_spread parameters: ' + ', '.join(sorted(allowed)))
    out = {'pattern': str(p.get('pattern') or '').strip().lower(), 'kind': p.get('kind', 'auto'), 'months': p.get('months', 36),
           'burn_in': p.get('burn_in', 3), 'min_shared': p.get('min_shared', 2), 'edge_filter': p.get('edge_filter', 'none'),
           'shuffles': p.get('shuffles', 100), 'simulations': p.get('simulations', 200), 'months_ahead': p.get('months_ahead', 12),
           'seed': p.get('seed', 7), 'top': p.get('top', 25)}
    if not out['pattern'] or len(out['pattern']) > 80: raise ValueError('pattern: a drug class, drug name or CPT/HCPCS code')
    if out['kind'] not in KINDS: raise ValueError(f'kind: {KINDS}')
    if out['edge_filter'] not in FILTERS: raise ValueError(f'edge_filter: {FILTERS}')
    for k, lo, hi in (('months', 6, 120), ('burn_in', 0, 12), ('min_shared', 1, 50), ('shuffles', 0, 1000), ('simulations', 10, 2000),
                      ('months_ahead', 1, 36), ('top', 1, 500)):
        if type(out[k]) is not int or not lo <= out[k] <= hi: raise ValueError(f'{k}: integer {lo}..{hi}')
    if type(out['seed']) is not int: raise ValueError('seed: integer')
    if out['kind'] == 'auto':
        out['kind'] = 'procedure' if re.fullmatch(r'[a-z]?\d{4}[a-z0-9]?', out['pattern']) else \
            'drug_class' if out['pattern'] in CLASS_WORDS or '_' in out['pattern'] else 'drug'
    return out


def _month_index(d, start):
    return (int(d[:4]) - int(start[:4])) * 12 + int(d[5:7]) - int(start[5:7])


def load(db, keep, p, start, end):
    """-> (pairs {(patient, npi)}, uses [(npi, date, record_id)], npi -> specialty, discontinued months info)."""
    spec = {str(n): s for n, s in db.execute('SELECT npi, specialty FROM clinician') if n}
    pairs, uses = set(), []
    for pid, npi in db.execute('SELECT patient_id, rendering_npi FROM payer_claim WHERE counts_in_metrics = 1 AND rendering_npi IS NOT NULL '
                               'AND service_from BETWEEN ? AND ?', (start, end)):
        if keep is None or pid in keep: pairs.add((pid, str(npi)))
    classes = {}
    if p['kind'] == 'drug_class':
        classes = {n.lower() for (n,) in db.execute('SELECT DISTINCT drug_name FROM fill WHERE drug_class = ?', (p['pattern'],)) if n}
    for pid, npi, d, name, rid in db.execute(
            'SELECT patient_id, prescriber_npi, fill_date, drug_name, record_id FROM payer_pharmacy_claim WHERE counts_as_fill = 1 '
            'AND prescriber_npi IS NOT NULL AND fill_date BETWEEN ? AND ?', (start, end)):
        if keep is not None and pid not in keep: continue
        npi = str(npi); pairs.add((pid, npi))
        nm = (name or '').lower()
        if (p['kind'] == 'drug' and p['pattern'] in nm) or (p['kind'] == 'drug_class' and nm in classes):
            uses.append((npi, d[:10], rid))
    if p['kind'] == 'procedure':
        code = p['pattern'].upper()
        for pid, npi, d, rid in db.execute(
                'SELECT l.patient_id, c.rendering_npi, l.service_date, l.record_id FROM payer_claim_line l JOIN payer_claim c '
                'ON c.payer_claim_id = l.payer_claim_id WHERE l.counts_in_metrics = 1 AND c.counts_in_metrics = 1 AND '
                '(UPPER(l.cpt) = ? OR UPPER(l.hcpcs) = ?) AND l.service_date BETWEEN ? AND ?', (code, code, start, end)):
            if (keep is None or pid in keep) and npi: uses.append((str(npi), d[:10], rid))
    return pairs, uses, spec


def exposure_table(A, adopt, T, burn_in):
    """Grouped person-months: {e: [at_risk, adoptions]} for months burn_in..T-1. adopt: month index or -1 (never)."""
    n = len(adopt)
    M = np.zeros((n, T), dtype=np.float64)
    for i, m in enumerate(adopt):
        if m >= 0: M[i, m:] = 1.0
    prior = np.zeros((n, T)); prior[:, 1:] = M[:, :-1]  # adopted before month t
    E = A @ prior
    risk_n, adopt_n = np.zeros(1), np.zeros(1)
    for t in range(max(burn_in, 1), T):
        risk = prior[:, t] == 0
        if not risk.any(): continue
        e = E[risk, t].astype(int)
        y = (M[risk, t] > 0).astype(float)
        m = int(e.max()) + 1
        if m > len(risk_n): risk_n, adopt_n = np.pad(risk_n, (0, m - len(risk_n))), np.pad(adopt_n, (0, m - len(adopt_n)))
        risk_n[:m] += np.bincount(e, minlength=m); adopt_n[:m] += np.bincount(e, weights=y, minlength=m)
    return {int(k): [int(risk_n[k]), int(adopt_n[k])] for k in range(len(risk_n)) if risk_n[k]}


def slope(table):
    """Logistic slope of adoption on log(1 + exposure) from grouped counts (None when not estimable)."""
    xs, ys, ws = [], [], []
    for e, (n, a) in table.items():
        x = np.log1p(e)
        if a: xs.append([1.0, x]); ys.append(1); ws.append(a)
        if n - a: xs.append([1.0, x]); ys.append(0); ws.append(n - a)
    if len({r[1] for r in xs}) < 2 or not any(ys) or all(ys): return None
    return float(rc.logistic_fit(np.array(xs), np.array(ys), l2=1e-3, weights=np.array(ws))[1])


def fit_tau(table):
    """MLE of (h0, tau) for h(e) = 1 - (1 - h0)(1 - tau)^e on grouped counts (grid + refinement)."""
    n0, a0 = table.get(0, [0, 0])
    h0 = (a0 + 0.5) / (n0 + 1.0)
    def ll(tau):
        s = 0.0
        for e, (n, a) in table.items():
            h = min(max(1 - (1 - h0) * (1 - tau) ** e, 1e-12), 1 - 1e-12)
            s += a * np.log(h) + (n - a) * np.log(1 - h)
        return s
    grid = np.concatenate([[0.0], np.geomspace(1e-5, 0.9, 120)])
    vals = [ll(t) for t in grid]
    i = int(np.argmax(vals))
    lo, hi = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
    for _ in range(40):  # golden-section refinement
        m1, m2 = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
        if ll(m1) < ll(m2): lo = m1
        else: hi = m2
    return float(h0), float((lo + hi) / 2)


def percolation(Abin, T, adopters, sims, rng):
    """Bond percolation with occupation T: (R, giant share mean, final reach mean, reach 5-95%)."""
    n = len(Abin)
    k = Abin.sum(1)
    mk = k.mean() if n else 0.0
    R = float(T * ((k ** 2).mean() - mk) / mk) if mk else 0.0
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    ii, jj = np.nonzero(np.triu(Abin, 1))
    giants, reach = [], []
    seeds = np.nonzero(adopters)[0]
    for _ in range(sims if n else 0):
        live = rng.random(len(ii)) < T
        G = coo_matrix((np.ones(int(live.sum())), (ii[live], jj[live])), shape=(n, n))
        _nc, roots = connected_components(G, directed=False)
        sizes = np.bincount(roots, minlength=n)
        giants.append(sizes.max() / n)
        reach.append(int(sizes[np.unique(roots[seeds])].sum()) if len(seeds) else 0)
    reach = np.array(reach) if reach else np.zeros(1)
    return R, float(np.mean(giants)) if giants else 0.0, float(reach.mean()), [int(np.percentile(reach, 5)), int(np.percentile(reach, 95))]


def forecast(A, adopted, h0, tau, months, sims, rng):
    """Monte Carlo of monthly adoption from the current state -> (totals per sim, per-node adoption probability)."""
    n = len(adopted)
    hits = np.zeros(n); totals = []
    for _ in range(sims):
        cur = adopted.astype(bool).copy()
        for _m in range(months):
            e = A @ cur
            h = 1 - (1 - h0) * (1 - tau) ** e
            new = (~cur) & (rng.random(n) < h)
            cur |= new
        hits += cur & ~adopted.astype(bool); totals.append(int(cur.sum()))
    return np.array(totals), hits / sims


def run(db, patient_ids, p, as_of):
    from . import network
    rng = np.random.default_rng(p['seed'])
    start, end = rc.window(as_of, p['months'])
    keep = set(patient_ids) if patient_ids is not None else None
    pairs, uses, spec = load(db, keep, p, start, end)
    providers, patients, W = network.project(pairs)
    A, finfo = network.edge_filter(W, len(patients), min_shared=p['min_shared'], method=p['edge_filter'])
    Abin = (A > 0).astype(np.float64)
    idx = {x: i for i, x in enumerate(providers)}
    first, recs, n_uses = {}, defaultdict(list), defaultdict(int)
    for npi, d, rid in sorted(uses, key=lambda u: u[1]):
        if npi not in idx: continue
        first.setdefault(npi, d); n_uses[npi] += 1
        if len(recs[npi]) < 3: recs[npi].append(rid)
    T = _month_index(end, start) + 1
    adopt = np.full(len(providers), -1)
    for npi, d in first.items(): adopt[idx[npi]] = max(0, _month_index(d, start))
    adopted_now = adopt >= 0
    table = exposure_table(Abin, adopt, T, p['burn_in'])
    beta = slope(table)
    shuffled = []
    events = adopt[adopt >= 0]
    if beta is not None and p['shuffles'] and len(events) > 2:
        pos = np.nonzero(adopt >= 0)[0]
        for _ in range(p['shuffles']):
            s = adopt.copy(); s[pos] = rng.permutation(events)
            b = slope(exposure_table(Abin, s, T, p['burn_in']))
            if b is not None: shuffled.append(b)
    p_shuffle = (sum(1 for b in shuffled if b >= beta) + 1) / (len(shuffled) + 1) if shuffled else None
    h0, tau = fit_tau(table) if table else (0.0, 0.0)
    T12 = 1 - (1 - tau) ** 12
    lam = float(np.max(np.linalg.eigvalsh(Abin))) if len(Abin) else 0.0
    threshold = 1 / lam if lam > 0 else None
    # discontinuation: adopters with no use in the last 6 months of the window, per adopter-month (crude delta)
    last = {}
    for npi, d, _r in uses:
        if npi in idx: last[npi] = max(last.get(npi, d), d)
    lapsed = sum(1 for npi, d in last.items() if _month_index(end, d) >= 6)
    adopter_months = sum(T - m for m in events) or 1
    delta = lapsed / adopter_months
    R, giant, reach, reach_ci = percolation(Abin, T12, adopted_now, min(p['simulations'], 200), rng)
    totals, prob = forecast(Abin, adopted_now, h0, tau, p['months_ahead'], p['simulations'], rng)
    expo_now = Abin @ adopted_now
    rows = []
    for i in np.argsort(-prob)[:p['top']]:
        if adopted_now[i] or prob[i] <= 0: continue
        rows.append({'npi': providers[i], 'specialty': spec.get(providers[i], 'unknown'), 'adopted_neighbours': int(expo_now[i]),
                     'degree': int(Abin[i].sum()), f"p_adopt_{p['months_ahead']}m": round(float(prob[i]), 4)})
    adopters = sorted(({'npi': n, 'specialty': spec.get(n, 'unknown'), 'first_use': d, 'uses': n_uses[n], 'record_ids': recs[n]}
                       for n, d in first.items()), key=lambda r: r['first_use'])
    exposure = [{'adopted_neighbours': e, 'at_risk_months': n, 'adoptions': a, 'monthly_hazard': round(a / n, 5) if n else None}
                for e, (n, a) in sorted(table.items())]
    k = Abin.sum(1)
    contagion = (p_shuffle is not None and p_shuffle <= 0.05 and beta is not None and beta > 0)
    assumptions = [
        f"Synthetic warehouse; pattern '{p['pattern']}' ({p['kind'].replace('_', ' ')}) first use per clinician NPI = "
        + ('prescriber on a paid fill.' if p['kind'] != 'procedure' else 'rendering NPI on a claim line counting in metrics.'),
        f"Window {start} to {end}; adoption before the window is unobserved, so the first {p['burn_in']} months' users are prior "
        'adopters (left censoring) and not counted as hazard events.',
        f"Graph: clinicians sharing >= {p['min_shared']} patients (claims and prescriptions), edge filter {p['edge_filter']}.",
        'Exposure hazard and transmissibility assume neighbours act as independent channels and every adopter stays infectious; '
        'the configuration-model R and SIS threshold ignore clustering and degree correlation.',
        'The timing-shuffle test separates contagion from homophily only for the observed timing; shared drivers that change over '
        'time (guidelines, formulary changes, detailing) can still look like contagion.',
        'Spread, percolation and forecast outputs are models, not observations.']
    report = {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'period': {'start': start, 'end': end},
              'graph': {'clinicians': len(providers), 'edges': int(Abin.sum() / 2), 'mean_degree': round(float(k.mean()), 2) if len(k) else 0,
                        'patients': len(patients), 'filter': {kk: v for kk, v in finfo.items() if kk != 'stats'}},
              'adopters': len(first), 'adopters_list': adopters[:200], 'exposure_hazard': exposure,
              'contagion': {'slope_log1p_exposure': None if beta is None else round(beta, 4),
                            'shuffle_mean_slope': round(float(np.mean(shuffled)), 4) if shuffled else None,
                            'shuffle_p': None if p_shuffle is None else round(p_shuffle, 4), 'shuffles': len(shuffled),
                            'verdict': ('contagion beyond homophily (timing matters)' if contagion else
                                        'clustering not distinguishable from homophily / shared environment' if p_shuffle is not None else
                                        'not estimable (too few adoptions or no exposure variation)')},
              'transmissibility': {'baseline_monthly_hazard': round(h0, 6), 'per_edge_monthly_tau': round(tau, 6), 'T_12_months': round(T12, 5)},
              'epidemic_threshold': {'lambda_max': round(lam, 3), 'sis_threshold': None if threshold is None else round(threshold, 5),
                                     'monthly_discontinuation_delta': round(delta, 5),
                                     'tau_over_delta': round(tau / delta, 4) if delta > 0 else None,
                                     'above_threshold': (tau / delta > threshold) if (delta > 0 and threshold) else (tau > 0 if threshold else None)},
              'percolation': {'R': round(R, 3), 'giant_component_share': round(giant, 4), 'expected_final_reach': round(reach, 1),
                              'reach_5_95': reach_ci, 'supercritical': R > 1},
              'forecast': {'months_ahead': p['months_ahead'], 'adopters_now': int(adopted_now.sum()),
                           'expected_adopters': round(float(totals.mean()), 1),
                           'interval_90': [int(np.percentile(totals, 5)), int(np.percentile(totals, 95))]},
              'rows': rows, 'assumptions': assumptions,
              'method': ('Observed first-use month per clinician on the shared-patient graph (network.project/edge_filter); grouped monthly '
                         'exposure hazard, logistic slope on log(1+adopted neighbours), Anagnostopoulos et al. 2008 timing-shuffle test; '
                         'MLE independent-channel transmissibility; SIS threshold 1/lambda_max; bond percolation R = T(<k^2>-<k>)/<k> '
                         '(Newman 2002) with Monte Carlo giant component and reach; Monte Carlo forecast.')}
    if not first:
        report['assumptions'].insert(0, f"No clinician in the graph used '{p['pattern']}' in the window: there is no observed spread to model.")
    return report


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    with rc.connect(path) as db:
        return run(db, patient_ids, p, str(as_of)[:10])


def headline(r):
    p, c, f, pc = r['parameters'], r['contagion'], r['forecast'], r['percolation']
    if not r['adopters']:
        return f"No clinician used {p['pattern']} between {r['period']['start']} and {r['period']['end']}"
    sp = f", shuffle p={c['shuffle_p']}" if c['shuffle_p'] is not None else ''
    return (f"{p['pattern']}: {r['adopters']} of {r['graph']['clinicians']} clinicians adopted ({r['period']['start']} to {r['period']['end']}); "
            f"{c['verdict']} (exposure slope {c['slope_log1p_exposure']}{sp}); per-edge 12-month transmissibility "
            f"{r['transmissibility']['T_12_months']:.1%}, percolation R {pc['R']} ({'supercritical' if pc['supercritical'] else 'subcritical'}); "
            f"forecast {f['expected_adopters']:.0f} adopters in {f['months_ahead']} months (90% {f['interval_90'][0]}-{f['interval_90'][1]})")


def answer(report, plan, question):
    return rc.answer(report, plan, value=headline(report) + '.', table=report['rows'], number=report['forecast']['expected_adopters'],
                     definitions=[{'term': 'adoption', 'basis': 'first observed use of the pattern by a clinician NPI in the window'},
                                  {'term': 'percolation R', 'basis': 'T (<k^2> - <k>) / <k>, Newman 2002'}])


# ---- free local-compiler route ----------------------------------------------------------------------------------------
CONTAGION = (r'\bcontagio\w+\b|\bpercolat\w+\b|\bepidemic (?:threshold|model|spread)\b|\btipping point\b|\bpeer (?:influence|effects?)\b|'
             r'\bsocial contagion\b|\bcontagious\b')
SPREADING = r'\b(?:spread\w*|diffus\w+|catch(?:ing)? on|uptake|adoption)\b'
WHO = r'\b(?:clinicians?|physicians?|doctors?|prescribers?|providers?|pcps?|network|peers?|colleagues?)\b'


def pattern_of(q):
    for k, rx in CLASS_WORDS.items():
        if re.search(rx, q): return k
    m = re.search(r'\b(?:cpt|hcpcs|code)\s*([a-z]?\d{4}[a-z0-9]?)\b', q) or re.search(r'\b(\d{5})\b', q)
    return m.group(1) if m else None


def route(question, profile, as_of):
    from .engine_suite import plan
    q = rc.low(question)
    if re.search(r'\binfluence maximi|\bseed (?:pcps?|practices?|providers?|physicians?)\b|\bif we (?:seed|start|train)\b', q): return None  # hypothetical cascade = network
    pat = pattern_of(q)
    hit = re.search(CONTAGION, q) or (re.search(SPREADING, q) and re.search(WHO, q) and pat)
    if not hit or not pat or rc.knowledge_only(q): return None
    params = {'pattern': pat}
    m = re.search(r'\b(?:next|in)\s+(\d{1,2})\s+months?\b', q)
    if m: params['months_ahead'] = max(1, min(36, int(m.group(1))))
    return plan(OP, params, f'Observed contagion and percolation of {pat} across the clinician shared-patient network', question,
                [f'pattern {pat}'])


TEMPLATES = ['Is GLP-1 prescribing spreading between doctors who share patients?',
             'Is SGLT2 adoption contagious across our physician network or just homophily?',
             'What is the epidemic threshold for statin prescribing on our provider network?',
             'Peer influence on opioid prescribing among clinicians who share patients',
             'Percolation analysis of advance care planning code 99497 across providers',
             'How many prescribers will adopt GLP-1s in the next 12 months given network spread?',
             'Has semaglutide uptake reached a tipping point among our physicians?']
