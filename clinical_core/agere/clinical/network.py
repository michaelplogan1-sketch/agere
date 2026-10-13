"""The provider network as a graph: patient-sharing networks, hubs, communities, care-team cohesion, referral
concentration, leakage by service line, steering arithmetic and a diffusion model over the PCP sharing graph.

Answers "which specialists and hospitals do our patients actually use?", "who are the key referral hubs?", "where does
care leak out of our preferred network?", "which PCPs share the most patients?", "if we steer cardiology to practice X,
how much leakage do we recover?" and "how would a practice-pattern change spread through the network?".

Data (semantic layer): visits (attending clinician, organisation), claims (rendering NPI -> clinician, service
organisation, allowed dollars), clinician (specialty, NPI, taxonomy), organization (type, TIN), patient (attributed PCP
and practice), referral records (requesting -> receiving clinician/organisation). Warehouse v1.23 has no NPIs or TINs:
clinician-level membership then comes from visits only, TIN-based leakage is unavailable and the report says so.

Methods (numpy/scipy, small graph algorithms written here):
  bipartite patient x provider incidence -> provider projection W = B'B (edge weight = shared patients), minimum-sharing
  threshold, significance filter (hypergeometric upper tail with Benjamini-Hochberg, or the Serrano disparity filter);
  degree/strength, weighted betweenness (Brandes over Dijkstra, distance = 1/weight), PageRank, eigenvector centrality;
  Louvain communities (modularity); care density (Pollack et al. 2013: mean shared-patient count over each patient's
  provider pairs); referral concentration (Herfindahl-Hirschman index); leakage = share of attributed patients' visits and
  claim dollars outside the network (pathway.outside_organization, the same OUTSIDE definition the pathway engine uses,
  or a supplied TIN list); steering = stated share of a leaked pool reassigned to an in-network target; spread =
  independent-cascade or linear-threshold diffusion over the PCP sharing graph with greedy (CELF) influence-maximising
  seeds. The spread and steering outputs are models with stated assumptions, not observations.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
import heapq, json, math, re, sqlite3

import numpy as np
from scipy.stats import hypergeom, norm

from .pathway import LEAKAGE as PATHWAY_LEAKAGE, outside_organization, wilson

VERSION = 'network/1'
FOCUS = ('all', 'usage', 'hubs', 'communities', 'pcp_sharing', 'cohesion', 'concentration', 'leakage', 'steering', 'spread')
LEVELS = ('clinician', 'organization')
FILTERS = ('hypergeometric', 'disparity', 'none')
LEAKAGE = tuple(PATHWAY_LEAKAGE) + ('tins',)
GROUP_BY = ('specialty', 'practice')
STEER_FROM = ('outside', 'all_other')
SPREAD_MODELS = ('independent_cascade', 'linear_threshold')
SPREAD_GRAPHS = ('auto', 'pcp', 'pcp_via_specialists')
SEED_STRATEGIES = ('greedy', 'degree')
FIELDS = {'focus', 'level', 'period', 'window_days', 'min_shared', 'filter', 'alpha', 'leakage', 'network_tins', 'group_by', 'specialty',
          'steer_specialty', 'steer_to', 'steer_share', 'steer_from', 'capacity_growth', 'spread_model', 'spread_graph', 'seeds',
          'seed_strategy', 'transmission', 'steps', 'runs', 'random_seed', 'top', 'include_ancillary'}
PRIMARY_SPECIALTIES = {'family medicine', 'internal medicine', 'pediatrics', 'pediatrics and family medicine', 'general practice',
                       'geriatric medicine', 'primary care'}
SUPPORT_ROLES = {'registered nursing', 'nursing', 'clinical pharmacy', 'social work', 'diagnostic technician'}
ACUTE_SPECIALTIES = {'emergency medicine', 'urgent care'}
NON_CARE_ORG_TYPES = {'payer', 'claims_clearinghouse', 'medication_history_network', 'retail_pharmacy'}
ANCILLARY_ORG_TYPES = {'independent_outpatient_laboratory', 'independent_anatomic_pathology_laboratory'}
NON_SPECIALIST_ORG_TYPES = {'hospital', 'standalone_urgent_care', 'home_health_agency', 'primary_care'}
MAX_NODES_OUT, MAX_EDGES_OUT = 150, 300
MIN_FLOW = 5  # specialist visits a PCP needs before its HHI enters the summary


# ---- parameters ------------------------------------------------------------------------------
def _iso(v, what):
    if not isinstance(v, str) or (v and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v)): raise ValueError(f'{what}: ISO date or ""')
    if v: date.fromisoformat(v)
    return v


def _int(p, k, lo, hi, d):
    v = p.get(k, d)
    if type(v) is not int or not lo <= v <= hi: raise ValueError(f'{k} {lo}..{hi}')
    return v


def _num(p, k, lo, hi, d):
    v = p.get(k, d)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi: raise ValueError(f'{k} {lo}..{hi}')
    return float(v)


def _choice(p, k, values, d):
    v = p.get(k) or d
    if v not in values: raise ValueError(f'{k} must be one of {values}')
    return v


def validate(p):
    if p is None: p = {}
    if not isinstance(p, dict) or set(p) - FIELDS: raise ValueError(f'network parameters: {sorted(FIELDS)}')
    out = {'focus': _choice(p, 'focus', FOCUS, 'all'), 'level': _choice(p, 'level', LEVELS, 'clinician')}
    per = p.get('period') or {}
    if not isinstance(per, dict) or set(per) - {'start', 'end'}: raise ValueError('period takes start/end')
    out['period'] = {'start': _iso(per.get('start', ''), 'period.start'), 'end': _iso(per.get('end', ''), 'period.end')}
    if all(out['period'].values()) and out['period']['start'] > out['period']['end']: raise ValueError('period start after end')
    out['window_days'] = _int(p, 'window_days', 30, 3650, 730)
    out['min_shared'] = _int(p, 'min_shared', 1, 100, 2)
    out['filter'] = _choice(p, 'filter', FILTERS, 'hypergeometric')
    out['alpha'] = _num(p, 'alpha', 0.0001, 0.5, 0.05)
    out['leakage'] = _choice(p, 'leakage', LEAKAGE, 'organization')
    tins = p.get('network_tins') or []
    if not isinstance(tins, list) or len(tins) > 200 or any(not isinstance(t, str) or not t.strip() for t in tins):
        raise ValueError('network_tins: list of TIN strings')
    out['network_tins'] = sorted({t.strip() for t in tins})
    if out['leakage'] == 'tins' and not out['network_tins']: raise ValueError('leakage tins needs network_tins')
    out['group_by'] = _choice(p, 'group_by', GROUP_BY, 'specialty')
    for k in ('specialty', 'steer_specialty', 'steer_to'):
        v = p.get(k) or ''
        if not isinstance(v, str) or len(v) > 80: raise ValueError(f'{k}: short string')
        out[k] = v.strip().lower() if k != 'steer_to' else v.strip()
    out['steer_share'] = _num(p, 'steer_share', 0.0, 1.0, 0.5)
    out['steer_from'] = _choice(p, 'steer_from', STEER_FROM, 'outside')
    out['capacity_growth'] = _num(p, 'capacity_growth', 0.0, 10.0, 0.25)
    out['spread_model'] = _choice(p, 'spread_model', SPREAD_MODELS, 'independent_cascade')
    out['spread_graph'] = _choice(p, 'spread_graph', SPREAD_GRAPHS, 'auto')
    out['seeds'] = _int(p, 'seeds', 1, 10, 3)
    out['seed_strategy'] = _choice(p, 'seed_strategy', SEED_STRATEGIES, 'greedy')
    out['transmission'] = _num(p, 'transmission', 0.001, 1.0, 0.05)
    out['steps'] = _int(p, 'steps', 1, 20, 6)
    out['runs'] = _int(p, 'runs', 20, 2000, 300)
    out['random_seed'] = _int(p, 'random_seed', 0, 2 ** 31 - 1, 7)
    out['top'] = _int(p, 'top', 1, 200, 15)
    v = p.get('include_ancillary', False)
    if not isinstance(v, bool): raise ValueError('include_ancillary must be boolean')
    out['include_ancillary'] = v
    if out['focus'] == 'steering' and not out['steer_specialty']: raise ValueError('steering needs steer_specialty (a service line, e.g. cardiology)')
    return out


# ---- graph construction ----------------------------------------------------------------------
def project(pairs, providers=None):
    """pairs: iterable of (patient, provider). -> (providers, patients, W) with W[i, j] = patients shared by providers i and j
    (diagonal = panel size). Each patient counts once per provider however many contacts."""
    pairs = set(pairs)
    providers = sorted({b for _, b in pairs}) if providers is None else list(providers)
    pi = {b: i for i, b in enumerate(providers)}
    patients = sorted({a for a, b in pairs if b in pi})
    ri = {a: i for i, a in enumerate(patients)}
    B = np.zeros((len(patients), len(providers)), dtype=np.float64)
    for a, b in pairs:
        if b in pi: B[ri[a], pi[b]] = 1.0
    return providers, patients, B.T @ B


def edge_filter(W, n_patients, min_shared=2, method='hypergeometric', alpha=0.05):
    """Undirected weighted adjacency kept after the minimum-sharing threshold and the significance filter.
    hypergeometric: P(X >= w) for X ~ Hypergeom(N patients, panel_i, panel_j), Benjamini-Hochberg q <= alpha.
    disparity (Serrano, Boguna & Vespignani 2009): alpha_ij = (1 - w/s_i)^(k_i - 1) at either endpoint < alpha; degree-1 ends kept.
    Returns (A, info) with info['p'] the per-edge statistic for kept candidates."""
    W = np.asarray(W, float)
    n = W.shape[0]
    off = W.copy(); np.fill_diagonal(off, 0)
    cand = np.triu(off >= min_shared, 1)
    ii, jj = np.nonzero(cand)
    stats = {}
    keep = np.zeros_like(cand)
    if method == 'none' or not len(ii):
        keep = cand.copy()
    elif method == 'hypergeometric':
        panel = np.diag(W)
        pv = np.array([hypergeom.sf(W[i, j] - 1, n_patients, panel[i], panel[j]) for i, j in zip(ii, jj)])
        q = _bh(pv)
        for (i, j), pvalue, qv in zip(zip(ii, jj), pv, q):
            stats[(int(i), int(j))] = {'p': float(pvalue), 'q': float(qv)}
            if qv <= alpha: keep[i, j] = True
    else:
        C = np.where(cand | cand.T, off, 0.0)
        k = (C > 0).sum(1); s = C.sum(1)
        for i, j in zip(ii, jj):
            a = [1.0 if k[x] <= 1 else (1 - C[x, y] / s[x]) ** (k[x] - 1) for x, y in ((i, j), (j, i))]
            stats[(int(i), int(j))] = {'alpha_i': float(a[0]), 'alpha_j': float(a[1])}
            if k[i] <= 1 or k[j] <= 1 or min(a) < alpha: keep[i, j] = True
    A = np.where(keep | keep.T, off, 0.0)
    return A, {'candidates': int(len(ii)), 'kept': int(keep.sum()), 'stats': stats}


def _bh(p):
    p = np.asarray(p, float); n = len(p)
    if not n: return p
    order = np.argsort(p); ranked = p[order] * n / np.arange(1, n + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1].clip(0, 1)
    q = np.empty(n); q[order] = adj
    return q


# ---- centrality ------------------------------------------------------------------------------
def betweenness(A, weighted=True):
    """Brandes betweenness for an undirected graph; weighted distance = 1/weight. Unnormalised pair counts (each unordered
    pair counted once) and the normalised value (divided by (n-1)(n-2)/2)."""
    A = np.asarray(A, float); n = len(A)
    nbrs = [[(int(j), (1.0 / A[i, j]) if weighted else 1.0) for j in np.nonzero(A[i])[0]] for i in range(n)]
    cb = np.zeros(n)
    for s in range(n):
        S, P, sigma, dist = [], [[] for _ in range(n)], np.zeros(n), np.full(n, np.inf)
        sigma[s] = 1; dist[s] = 0
        heap, seen = [(0.0, s, s)], set()
        while heap:
            d, v, pred = heapq.heappop(heap)
            if v in seen: continue
            seen.add(v); S.append(v)
            for w, l in nbrs[v]:
                nd = d + l
                if nd < dist[w] - 1e-12:
                    dist[w] = nd; sigma[w] = sigma[v]; P[w] = [v]; heapq.heappush(heap, (nd, w, v))
                elif abs(nd - dist[w]) <= 1e-12 and w not in seen:
                    sigma[w] += sigma[v]; P[w].append(v)
        delta = np.zeros(n)
        for w in reversed(S):
            for v in P[w]: delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s: cb[w] += delta[w]
    cb /= 2.0
    norm_ = cb / ((n - 1) * (n - 2) / 2) if n > 2 else cb * 0
    return cb, norm_


def pagerank(A, d=0.85, tol=1e-12, max_iter=500):
    A = np.asarray(A, float); n = len(A)
    if not n: return np.zeros(0)
    s = A.sum(1)
    x = np.full(n, 1.0 / n)
    for _ in range(max_iter):
        dangling = x[s == 0].sum()
        spread = np.divide(x, s, out=np.zeros(n), where=s > 0) @ A
        nx = (1 - d) / n + d * (spread + dangling / n)
        if np.abs(nx - x).sum() < tol: x = nx; break
        x = nx
    return x / x.sum()


def eigenvector(A):
    A = np.asarray(A, float)
    if not len(A) or not A.any(): return np.zeros(len(A))
    vals, vecs = np.linalg.eigh(A)
    v = np.abs(vecs[:, -1])
    return v / v.max()


def components(A):
    n = len(A); label = [-1] * n; c = 0
    for s in range(n):
        if label[s] >= 0: continue
        stack = [s]; label[s] = c
        while stack:
            v = stack.pop()
            for w in np.nonzero(A[v])[0]:
                if label[w] < 0: label[w] = c; stack.append(int(w))
        c += 1
    return label


# ---- communities -----------------------------------------------------------------------------
def modularity(A, labels, resolution=1.0):
    A = np.asarray(A, float); m2 = A.sum()
    if m2 <= 0: return 0.0
    k = A.sum(1); labels = np.asarray(labels)
    q = 0.0
    for c in set(labels.tolist()):
        idx = labels == c
        q += A[np.ix_(idx, idx)].sum() / m2 - resolution * (k[idx].sum() / m2) ** 2
    return float(q)


def louvain(A, seed=0, resolution=1.0, max_levels=10):
    """Louvain (Blondel et al. 2008): local moving of nodes by modularity gain in a seeded random order, then aggregation,
    until no level improves. Labels are renumbered by community size (largest = 0), ties by first member."""
    A = np.asarray(A, float); n = len(A)
    rng = np.random.default_rng(seed)
    member = np.arange(n)  # original node -> current super-node
    G = A.copy()
    for _ in range(max_levels):
        comm, moved = _one_level(G, rng, resolution)
        if not moved: break
        _, comm = np.unique(comm, return_inverse=True)
        member = comm[member]
        k = comm.max() + 1
        M = np.zeros((len(G), k)); M[np.arange(len(G)), comm] = 1
        G = M.T @ G @ M
    sizes = Counter(member.tolist())
    order = sorted(sizes, key=lambda c: (-sizes[c], int(np.nonzero(member == c)[0][0])))
    remap = {c: i for i, c in enumerate(order)}
    labels = np.array([remap[c] for c in member])
    return labels, modularity(A, labels, resolution)


def _one_level(G, rng, resolution):
    n = len(G); m2 = G.sum()
    comm = np.arange(n)
    if m2 <= 0: return comm, False
    k = G.sum(1); tot = k.copy()
    moved_any, improved = False, True
    while improved:
        improved = False
        for i in rng.permutation(n):
            ci = comm[i]
            links = defaultdict(float)
            for j in np.nonzero(G[i])[0]:
                if j != i: links[comm[j]] += G[i, j]
            tot[ci] -= k[i]
            best, best_gain = ci, links.get(ci, 0.0) - resolution * tot[ci] * k[i] / m2
            for c, w in sorted(links.items()):
                gain = w - resolution * tot[c] * k[i] / m2
                if gain > best_gain + 1e-12: best, best_gain = c, gain
            comm[i] = best; tot[best] += k[i]
            if best != ci: improved = moved_any = True
    return comm, moved_any


# ---- cohesion, concentration ------------------------------------------------------------------
def care_density(provider_sets, W, index):
    """Pollack et al. 2013 care density per patient: sum over the patient's provider pairs of the patients those two providers
    share (whole-population projection W), divided by the number of pairs m(m-1)/2. Patients with fewer than 2 providers: None."""
    out = {}
    for pid, provs in provider_sets.items():
        idx = sorted(index[b] for b in provs if b in index)
        m = len(idx)
        if m < 2: out[pid] = None; continue
        tot = sum(W[a, b] for x, a in enumerate(idx) for b in idx[x + 1:])
        out[pid] = tot / (m * (m - 1) / 2)
    return out


def hhi(counts):
    """Herfindahl-Hirschman index on a 0-10,000 scale from destination counts."""
    c = np.asarray(list(counts.values()) if isinstance(counts, dict) else counts, float)
    if c.sum() <= 0: return None
    s = c / c.sum()
    return float((s ** 2).sum() * 10000)


# ---- steering, spread ---------------------------------------------------------------------------
def steering(pool_visits, pool_patients, pool_dollars, share, target_visits, capacity_growth, target_price=None):
    """Reassign `share` of a pool of visits to an in-network target. Expected recovered visits, patients and dollars
    (outside dollars x share; optionally repriced at the target's mean allowed per visit) and the capacity check."""
    rv = pool_visits * share
    out = {'pool_visits': pool_visits, 'pool_patients': pool_patients, 'pool_dollars': round(pool_dollars, 2), 'share': share,
           'recovered_visits': round(rv, 2), 'recovered_patients': round(pool_patients * share, 2),
           'recovered_dollars': round(pool_dollars * share, 2),
           'recovered_dollars_at_target_price': round(rv * target_price, 2) if target_price is not None else None,
           'target_current_visits': target_visits,
           'target_volume_increase': round(rv / target_visits, 4) if target_visits else None}
    out['within_capacity'] = (out['target_volume_increase'] is not None and out['target_volume_increase'] <= capacity_growth) or rv == 0
    return out


def live_edge_samples(W, p0, runs, rng):
    """Independent cascade as live-edge graphs: each edge is live with probability 1 - (1 - p0)^w (every shared patient an
    independent channel with transmission p0). Returns a list of adjacency lists, one per run."""
    W = np.asarray(W, float); n = len(W)
    ii, jj = np.nonzero(np.triu(W, 1))
    prob = 1 - (1 - p0) ** W[ii, jj]
    samples = []
    for _ in range(runs):
        live = rng.random(len(ii)) < prob
        adj = [[] for _ in range(n)]
        for a, b in zip(ii[live], jj[live]): adj[a].append(int(b)); adj[b].append(int(a))
        samples.append(adj)
    return samples


def cascade_curve(samples, seeds, steps, weights=None):
    """Active nodes by step (BFS depth in each live-edge sample = cascade round). -> array runs x (steps+1), cumulative
    (node counts, or the sum of `weights` over active nodes)."""
    out = np.zeros((len(samples), steps + 1))
    for r, adj in enumerate(samples):
        depth = {s: 0 for s in seeds}; frontier = list(seeds)
        for t in range(1, steps + 1):
            nxt = []
            for v in frontier:
                for w in adj[v]:
                    if w not in depth: depth[w] = t; nxt.append(w)
            frontier = nxt
        d = np.array(list(depth.values())); wv = np.ones(len(d)) if weights is None else np.asarray(weights, float)[list(depth)]
        out[r] = [wv[d <= t].sum() for t in range(steps + 1)]
    return out


def threshold_curve(W, seeds, steps, thresholds, weights=None):
    """Linear threshold: node adopts when the weight share of adopted neighbours reaches its uniform random threshold."""
    W = np.asarray(W, float); s = W.sum(1)
    Wn = np.divide(W, s[:, None], out=np.zeros_like(W), where=s[:, None] > 0)
    wv = np.ones(len(W)) if weights is None else np.asarray(weights, float)
    out = np.zeros((len(thresholds), steps + 1))
    for r, th in enumerate(thresholds):
        active = np.zeros(len(W), bool); active[list(seeds)] = True
        out[r, 0] = wv[active].sum()
        for t in range(1, steps + 1):
            new = (~active) & (Wn @ active >= th)
            active |= new
            out[r, t] = wv[active].sum()
    return out


def greedy_seeds(k, n, reach, candidates=None):
    """Greedy influence maximisation (Kempe, Kleinberg & Tardos 2003) with CELF lazy evaluation over a fixed set of random
    draws (common random numbers, so marginal gains are comparable). reach(seed list) -> expected final reach."""
    cand = list(range(n)) if candidates is None else list(candidates)
    chosen, base = [], 0.0
    heap = [(-reach([c]), c, 0) for c in cand]
    heapq.heapify(heap)
    gains = []
    while heap and len(chosen) < k:
        g, c, it = heapq.heappop(heap)
        if it == len(chosen):
            chosen.append(c); base += -g; gains.append(round(-g, 4)); continue
        heapq.heappush(heap, (-(reach(chosen + [c]) - base), c, len(chosen)))
    return chosen, gains


# ---- data ---------------------------------------------------------------------------------------
def connect(path):
    p = Path(path)
    target = p / 'semantic.sqlite3' if p.is_dir() else p
    if not target.exists() and p.is_dir() and (p / 'run.json').exists():
        from . import semantic_layer
        target = semantic_layer.ensure(p)
    if not target.exists(): raise FileNotFoundError(f'no semantic layer at {target}')
    return sqlite3.connect(f'file:{target}?mode=ro', uri=True)


def _cols(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def capability(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    meta = dict(db.execute('SELECT key, value FROM meta').fetchall()) if 'meta' in tables else {}
    oc = _cols(db, 'organization') if 'organization' in tables else set()
    cc = _cols(db, 'clinician') if 'clinician' in tables else set()
    kc = _cols(db, 'claim') if 'claim' in tables else set()
    one = lambda sql: db.execute(sql).fetchone()[0]
    cap = {'version': meta.get('version'), 'as_of': meta.get('as_of'),
           'visits': 'visit' in tables, 'clinicians': 'clinician' in tables, 'organizations': 'organization' in tables,
           'clinician_npi': bool('npi' in cc and one('SELECT count(*) FROM clinician WHERE npi IS NOT NULL')),
           'organization_tin': bool('tin' in oc and one('SELECT count(*) FROM organization WHERE tin IS NOT NULL')),
           'claims': 'claim' in tables,
           'claim_rendering_npi': bool('rendering_npi' in kc and one('SELECT count(*) FROM claim WHERE rendering_npi IS NOT NULL')),
           'referral_records': bool('record' in tables and one("SELECT count(*) FROM record WHERE stream = 'referrals'"))}
    gaps = []
    if not cap['clinician_npi']: gaps.append('No clinician NPIs received (warehouse before v2.0): clinicians are identified by local ids only.')
    if not cap['organization_tin']: gaps.append('No organisation TINs received: leakage uses the OUTSIDE organisation marker, not TINs.')
    if cap['claims'] and not cap['claim_rendering_npi']:
        gaps.append('Claims carry no rendering NPI: clinician-level sharing comes from visits only; claim dollars attach to the service organisation.')
    if not cap['claims']: gaps.append('No claims: leakage and steering are in visits only, without dollars.')
    cap['data_gaps'] = gaps
    return cap


def _period(p, as_of):
    end = p['period']['end'] or as_of.isoformat()
    start = p['period']['start'] or (date.fromisoformat(end) - timedelta(days=p['window_days'] - 1)).isoformat()
    return start, end


def load(db, cap, patient_ids, p, as_of):
    start, end = _period(p, as_of)
    orgs = {}
    if cap['organizations']:
        oc = _cols(db, 'organization')
        sel = ', '.join(c if c in oc else 'NULL' for c in ('organization_id', 'organization_type', 'display_name', 'tin', 'npi'))
        for oid, typ, name, tin, npi in db.execute(f'SELECT {sel} FROM organization'):
            orgs[oid] = {'type': typ or 'unknown', 'name': name or oid, 'tin': tin, 'npi': npi}
    clin, npi_clin = {}, {}
    if cap['clinicians']:
        cc = _cols(db, 'clinician')
        sel = ', '.join(c if c in cc else 'NULL' for c in ('clinician_id', 'organization_id', 'specialty', 'npi'))
        for cid, oid, spec, npi in db.execute(f'SELECT {sel} FROM clinician'):
            clin[cid] = {'org': oid, 'specialty': (spec or 'unknown').strip()}
            if npi: npi_clin[str(npi)] = cid
    pc = _cols(db, 'patient')
    sel = ', '.join(c if c in pc else 'NULL' for c in ('patient_id', 'pcp_clinician_id', 'pcp_practice_id'))
    members = set(patient_ids) if patient_ids is not None else None
    patients = {pid: {'pcp': pcp, 'practice': prac} for pid, pcp, prac in db.execute(f'SELECT {sel} FROM patient')
                if members is None or pid in members}
    contacts = []  # (patient, date, clinician|None, org|None, source, dollars|None)
    vc = _cols(db, 'visit') if cap['visits'] else set()
    if cap['visits']:
        status = "AND coalesce(status, '') NOT IN ('cancelled', 'entered-in-error', 'no_show')" if 'status' in vc else ''
        att = 'attending_clinician_id' if 'attending_clinician_id' in vc else 'NULL'
        for pid, d, c, o in db.execute(f'SELECT patient_id, substr(event_date, 1, 10), {att}, organization_id FROM visit '
                                       f'WHERE event_date IS NOT NULL AND substr(event_date, 1, 10) BETWEEN ? AND ? {status}', (start, end)):
            if pid in patients: contacts.append((pid, d, c, o or (clin.get(c) or {}).get('org'), 'visit', None))
    claim_rows = []
    if cap['claims']:
        kc = _cols(db, 'claim')
        rnpi = 'rendering_npi' if 'rendering_npi' in kc else 'NULL'
        org = 'service_organization_id' if 'service_organization_id' in kc else 'NULL'
        dollars = 'allowed_cents' if 'allowed_cents' in kc else 'net_payer_paid_cents' if 'net_payer_paid_cents' in kc else 'NULL'
        st = 'final_status' if 'final_status' in kc else 'NULL'
        for pid, d, npi, o, cents, fs in db.execute(f'SELECT patient_id, substr(service_date, 1, 10), {rnpi}, {org}, {dollars}, {st} FROM claim '
                                                    'WHERE service_date IS NOT NULL AND substr(service_date, 1, 10) BETWEEN ? AND ?', (start, end)):
            if pid not in patients: continue
            c = npi_clin.get(str(npi)) if npi else None
            usd = 0.0 if (fs or '').lower() == 'denied' else (cents or 0) / 100.0
            claim_rows.append((pid, d, c, o or (clin.get(c) or {}).get('org'), 'claim', usd))
    referrals = []
    if cap['referral_records']:
        seen = set()
        enc_att = dict(db.execute('SELECT encounter_id, attending_clinician_id FROM visit WHERE attending_clinician_id IS NOT NULL')) \
            if {'encounter_id', 'attending_clinician_id'} <= vc else {}
        for pid, d, payload in db.execute("SELECT patient_id, substr(event_date, 1, 10), payload FROM record WHERE stream = 'referrals' "
                                          'AND event_date IS NOT NULL AND substr(event_date, 1, 10) BETWEEN ? AND ?', (start, end)):
            if pid not in patients: continue
            try: x = json.loads(payload)
            except (TypeError, ValueError): continue
            src = x.get('requesting_clinician_id') or x.get('ordering_clinician_id') or \
                enc_att.get(x.get('requesting_encounter_id') or x.get('from_encounter_id') or x.get('encounter_id'))
            dst_org = x.get('receiving_organization_id') or x.get('destination_organization_id')
            dst = x.get('receiving_clinician_id')
            spec = x.get('specialty') or x.get('requested_specialty')
            if not src or not (dst_org or dst): continue
            key = (pid, d, src, dst_org, dst, spec)
            if key in seen: continue
            seen.add(key)
            referrals.append({'patient': pid, 'date': d, 'from': src, 'to_clinician': dst, 'to_org': dst_org or (clin.get(dst) or {}).get('org'),
                              'specialty': (spec or (clin.get(dst) or {}).get('specialty') or 'unknown').lower()})
    return {'orgs': orgs, 'clin': clin, 'patients': patients, 'visits': contacts, 'claims': claim_rows, 'referrals': referrals,
            'period': {'start': start, 'end': end}}


# ---- classification ------------------------------------------------------------------------------
def org_type(data, org):
    return (data['orgs'].get(org) or {}).get('type') or ('outside' if outside_organization(org) else 'unknown')


def service_line(data, clinician, org):
    spec = (data['clin'].get(clinician) or {}).get('specialty')
    if spec and spec.lower() != 'unknown': return spec.lower()
    return org_type(data, org).replace('_', ' ')


def is_specialist(data, clinician, org):
    line = service_line(data, clinician, org)
    return bool(clinician) and line not in PRIMARY_SPECIALTIES | SUPPORT_ROLES | ACUTE_SPECIALTIES and org_type(data, org) not in NON_SPECIALIST_ORG_TYPES


def in_graph(data, org, p):
    t = org_type(data, org)
    return t not in NON_CARE_ORG_TYPES and (p['include_ancillary'] or t not in ANCILLARY_ORG_TYPES)


def in_network(data, p, org, line, patient_practice):
    """Leakage definition. organization: the pathway engine's OUTSIDE marker; organization_or_practice: also primary care at a
    practice other than the patient's attributed practice (as in pathway); tins: the organisation's TIN is in network_tins."""
    if p['leakage'] == 'tins':
        return (data['orgs'].get(org) or {}).get('tin') in set(p['network_tins'])
    if outside_organization(org): return False
    if p['leakage'] == 'organization_or_practice' and line in PRIMARY_SPECIALTIES and org_type(data, org) == 'primary_care' \
            and patient_practice and org != patient_practice:
        return False
    return True


def _label(data, node, level):
    if level == 'organization':
        o = data['orgs'].get(node) or {}
        return o.get('name') or node
    c = data['clin'].get(node) or {}
    return f"{node} ({c.get('specialty', '?')})"


# ---- analysis -------------------------------------------------------------------------------------
def analyse(data, p, cap=None, seed=None):
    cap = cap or {'data_gaps': []}
    level = p['level']
    gaps = list(cap.get('data_gaps') or [])
    if p['leakage'] == 'tins' and not cap.get('organization_tin', True):
        gaps.append('leakage by TIN requested but no TINs are received: reported with the OUTSIDE organisation marker instead.')
        p = {**p, 'leakage': 'organization'}
    rng_seed = p['random_seed'] if seed is None else seed
    contacts = data['visits'] + data['claims']
    pairs = set()
    for pid, _, c, o, _, _ in contacts:
        if not in_graph(data, o, p): continue
        node = c if level == 'clinician' else o
        if node: pairs.add((pid, node))
    providers, pts, W = project(pairs)
    n = len(providers)
    report = {'version': VERSION, 'status': 'answered', 'parameters': p, 'capability': {k: v for k, v in cap.items() if k != 'data_gaps'},
              'period': data['period'], 'data_gaps': gaps, 'synthetic': True}
    if n < 2:
        report.update(status='empty', graph={'level': level, 'providers': n, 'patients': len(pts), 'edges': 0})
        report['method'] = method(report); report['limitations'] = LIMITATIONS
        return report
    A, finfo = edge_filter(W, len(pts), p['min_shared'], p['filter'], p['alpha'])
    idx = {b: i for i, b in enumerate(providers)}
    deg = (A > 0).sum(1); strength = A.sum(1)
    _, btw = betweenness(A); pr = pagerank(A)
    labels, Q = louvain(A, seed=rng_seed)
    comp = components(A)
    eig = np.zeros(n)
    for c in set(comp):  # per connected component (a disconnected graph has no single leading eigenvector)
        m = [i for i in range(n) if comp[i] == c]
        if len(m) > 1: eig[m] = eigenvector(A[np.ix_(m, m)])
    comp_sizes = Counter(comp)
    isolated = int((deg == 0).sum())
    attributed = {pid for pid, a in data['patients'].items() if a.get('practice')}
    pcp_of = {pid: a.get('pcp') for pid, a in data['patients'].items()}
    panel = np.diag(W)
    nodes = []
    for b in providers:
        i = idx[b]
        if level == 'clinician':
            c = data['clin'].get(b) or {}; org = c.get('org'); spec = c.get('specialty', 'unknown')
        else:
            org = b; spec = org_type(data, b).replace('_', ' ')
        nodes.append({'id': b, 'label': _label(data, b, level), 'specialty': spec, 'organization': org,
                      'organization_type': org_type(data, org), 'in_network': not outside_organization(org),
                      'patients': int(panel[i]), 'degree': int(deg[i]), 'strength': int(strength[i]),
                      'betweenness': round(float(btw[i]), 5), 'pagerank': round(float(pr[i]), 5), 'eigenvector': round(float(eig[i]), 5),
                      'community': int(labels[i]) if deg[i] else None})
    hubs = sorted(nodes, key=lambda r: (-r['betweenness'], -r['pagerank'], r['id']))
    ii, jj = np.nonzero(np.triu(A, 1))
    edges = sorted(({'a': providers[i], 'b': providers[j], 'shared': int(A[i, j]), **{k: round(v, 6) for k, v in finfo['stats'].get((int(i), int(j)), {}).items()}}
                    for i, j in zip(ii, jj)), key=lambda e: (-e['shared'], e['a'], e['b']))
    report['graph'] = {'level': level, 'providers': n, 'patients': len(pts), 'candidate_edges': finfo['candidates'], 'edges': finfo['kept'],
                       'filter': p['filter'], 'alpha': p['alpha'], 'min_shared': p['min_shared'],
                       'density': round(2 * finfo['kept'] / (n * (n - 1)), 5), 'isolated_providers': isolated,
                       'components': sum(1 for c, s in comp_sizes.items() if s > 1), 'largest_component': max(comp_sizes.values()),
                       'modularity': round(Q, 4)}
    report['nodes'] = sorted(nodes, key=lambda r: (-r['pagerank'], r['id']))
    report['edges'] = edges
    report['hubs'] = hubs
    # communities
    comms = defaultdict(list)
    for r in nodes:
        if r['community'] is not None: comms[r['community']].append(r)
    crow = []
    for c, rs in sorted(comms.items()):
        members = [idx[r['id']] for r in rs]
        inside = A[np.ix_(members, members)].sum() / 2; total = A[members].sum() - inside
        practices = Counter((data['patients'].get(pid) or {}).get('practice') for pid, b in pairs if b in {r['id'] for r in rs})
        crow.append({'community': c, 'providers': len(rs), 'internal_weight_share': round(inside / total, 4) if total else None,
                     'top_specialties': ', '.join(f'{s} {k}' for s, k in Counter(r['specialty'] for r in rs).most_common(3)),
                     'organizations': ', '.join(f'{o} {k}' for o, k in Counter(r['organization'] for r in rs).most_common(3)),
                     'dominant_attributed_practice': next((f'{x} ({k})' for x, k in practices.most_common() if x), None),
                     'hub': max(rs, key=lambda r: (r['pagerank'], r['id']))['id']})
    report['communities'] = crow
    # PCP sharing
    pcps = sorted({a.get('pcp') for a in data['patients'].values() if a.get('pcp')} & set(idx)) if level == 'clinician' else []
    psh = []
    for x, a in enumerate(pcps):
        for b in pcps[x + 1:]:
            w = W[idx[a], idx[b]]
            if w > 0:
                psh.append({'pcp_a': a, 'pcp_b': b, 'practice_a': (data['clin'].get(a) or {}).get('org'),
                            'practice_b': (data['clin'].get(b) or {}).get('org'), 'shared_patients': int(w),
                            'significant_edge': bool(A[idx[a], idx[b]] > 0)})
    psh.sort(key=lambda r: (-r['shared_patients'], r['pcp_a'], r['pcp_b']))
    practice_pairs = Counter()
    by_patient_practice = defaultdict(set)
    for pid, _, c, o, _, _ in contacts:
        if org_type(data, o) == 'primary_care': by_patient_practice[pid].add(o)
    for pid, s in by_patient_practice.items():
        s = sorted(s)
        for x, a in enumerate(s):
            for b in s[x + 1:]: practice_pairs[(a, b)] += 1
    report['pcp_sharing'] = {'pcps': len(pcps), 'pairs_sharing': len(psh), 'pairs': psh,
                             'practice_pairs': [{'practice_a': a, 'practice_b': b, 'shared_patients': k} for (a, b), k in practice_pairs.most_common()]}
    # care density (Pollack): over the clinician projection from all contacts
    provs = defaultdict(set)
    for pid, b in pairs: provs[pid].add(b)
    dens = care_density(provs, W, idx)
    vals = {pid: v for pid, v in dens.items() if v is not None}
    by = defaultdict(list)
    for pid, v in vals.items(): by[(data['patients'].get(pid) or {}).get('practice') or 'unattributed'].append(v)
    by_pcp = defaultdict(list)
    for pid, v in vals.items(): by_pcp[pcp_of.get(pid) or 'unattributed'].append(v)
    q = lambda xs: (round(float(np.mean(xs)), 3), round(float(np.median(xs)), 3)) if xs else (None, None)
    report['cohesion'] = {'patients_with_2plus_providers': len(vals), 'patients_with_1_provider': sum(1 for v in dens.values() if v is None),
                          'mean': q(list(vals.values()))[0], 'median': q(list(vals.values()))[1],
                          'by_practice': sorted(({'practice': k, 'patients': len(v), 'mean_care_density': q(v)[0], 'median_care_density': q(v)[1]}
                                                 for k, v in by.items()), key=lambda r: -(r['mean_care_density'] or 0)),
                          'by_pcp': sorted(({'pcp': k, 'patients': len(v), 'mean_care_density': q(v)[0]} for k, v in by_pcp.items()),
                                           key=lambda r: -(r['mean_care_density'] or 0))}
    # referral concentration
    report['concentration'] = _concentration(data, p)
    # usage + leakage
    report['usage'] = _usage(data, p, attributed)
    report['leakage'] = _leakage(data, p, attributed)
    if p['focus'] == 'steering' or p['steer_specialty']:
        report['steering'] = _steer(data, p, attributed)
    if p['focus'] == 'spread':
        report['spread'] = _spread(data, p, W, idx, pairs)
    report['assumptions'] = _assumptions(report)
    report['method'] = method(report)
    report['limitations'] = LIMITATIONS
    report['notes'] = _notes(report, data)
    return report


def _concentration(data, p):
    """HHI of each attributed PCP's patients' specialist visits across specialist clinicians and organisations, and of explicit
    referral records per requesting clinician across receiving organisations."""
    spec = p['specialty']
    flows = defaultdict(Counter); flows_org = defaultdict(Counter)
    for pid, _, c, o, _, _ in data['visits']:
        if not is_specialist(data, c, o): continue
        if spec and service_line(data, c, o) != spec: continue
        pcp = (data['patients'].get(pid) or {}).get('pcp')
        if not pcp: continue
        flows[pcp][c] += 1; flows_org[pcp][o] += 1
    rows = []
    for pcp in sorted(flows):
        f, fo = flows[pcp], flows_org[pcp]
        top, k = fo.most_common(1)[0]
        rows.append({'pcp': pcp, 'practice': (data['clin'].get(pcp) or {}).get('org'), 'specialist_visits': sum(f.values()),
                     'specialists_used': len(f), 'organizations_used': len(fo), 'hhi_specialists': round(hhi(f), 1),
                     'hhi_organizations': round(hhi(fo), 1), 'top_organization': top, 'top_share': round(k / sum(fo.values()), 4)})
    rows.sort(key=lambda r: (r['specialist_visits'] < MIN_FLOW, -r['hhi_organizations'], -r['specialist_visits'], r['pcp']))
    ref = defaultdict(Counter)
    for x in data['referrals']:
        if spec and x['specialty'] != spec: continue
        ref[x['from']][x['to_org'] or x['to_clinician']] += 1
    rrows = sorted(({'requesting_clinician': k, 'referrals': sum(v.values()), 'destinations': len(v), 'hhi': round(hhi(v), 1),
                     'top_destination': v.most_common(1)[0][0], 'top_share': round(v.most_common(1)[0][1] / sum(v.values()), 4)}
                    for k, v in ref.items()), key=lambda r: (-r['referrals'], r['requesting_clinician']))
    allv = [r['hhi_organizations'] for r in rows if r['specialist_visits'] >= MIN_FLOW]
    return {'specialty': spec or 'all specialist service lines', 'by_pcp': rows, 'pcps_scored': len(allv), 'min_specialist_visits': MIN_FLOW,
            'mean_hhi_organizations': round(float(np.mean(allv)), 1) if allv else None,
            'pcps_fully_concentrated': sum(1 for x in allv if x >= 9999.5),
            'referral_records': rrows, 'referral_record_count': sum(r['referrals'] for r in rrows)}


def _dollars_by(data, key):
    out = defaultdict(float)
    for row in data['claims']:
        k = key(row)
        if k is not None: out[k] += row[5] or 0.0
    return out


def _usage(data, p, attributed):
    """Which organisations (and service lines) attributed patients actually use, primary care excluded."""
    n_attr = max(1, len(attributed))
    vis = defaultdict(lambda: {'visits': 0, 'patients': set(), 'lines': Counter()})
    for pid, _, c, o, _, _ in data['visits']:
        if pid not in attributed or not o or org_type(data, o) in NON_CARE_ORG_TYPES: continue
        line = service_line(data, c, o)
        if org_type(data, o) == 'primary_care' and line in PRIMARY_SPECIALTIES | SUPPORT_ROLES: continue
        v = vis[o]; v['visits'] += 1; v['patients'].add(pid); v['lines'][line] += 1
    dollars = _dollars_by(data, lambda r: r[3] if r[0] in attributed else None)
    rows = [{'organization': o, 'name': (data['orgs'].get(o) or {}).get('name', o), 'type': org_type(data, o),
             'in_network': not outside_organization(o), 'tin': (data['orgs'].get(o) or {}).get('tin'),
             'patients': len(v['patients']), 'share_of_attributed_patients': round(len(v['patients']) / n_attr, 4), 'visits': v['visits'],
             'claim_dollars': round(dollars.get(o, 0.0), 2), 'service_lines': ', '.join(f'{s} {k}' for s, k in v['lines'].most_common(3))}
            for o, v in vis.items()]
    rows.sort(key=lambda r: (-r['patients'], r['organization']))
    return rows


def _leakage(data, p, attributed):
    group = p['group_by']; spec = p['specialty']
    pr = lambda pid: (data['patients'].get(pid) or {}).get('practice')
    agg = defaultdict(lambda: {'visits': 0, 'outside': 0, 'patients': set(), 'outside_patients': set(), 'dollars': 0.0,
                               'outside_dollars': 0.0, 'dest': Counter()})
    tot = agg['__all__']
    for pid, _, c, o, src, usd in data['visits'] + data['claims']:
        if pid not in attributed or org_type(data, o) in NON_CARE_ORG_TYPES: continue
        line = service_line(data, c, o)
        if spec and line != spec: continue
        out = not in_network(data, p, o, line, pr(pid))
        key = line if group == 'specialty' else (pr(pid) or 'unattributed')
        for a in (agg[key], tot):
            if src == 'visit':
                a['visits'] += 1; a['patients'].add(pid)
                if out: a['outside'] += 1; a['outside_patients'].add(pid); a['dest'][o] += 1
            else:
                a['dollars'] += usd or 0.0
                if out: a['outside_dollars'] += usd or 0.0
    def row(k, a):
        lo, hi = wilson(a['outside'], a['visits'])
        return {group: k, 'visits': a['visits'], 'outside_visits': a['outside'],
                'leakage_share': round(a['outside'] / a['visits'], 4) if a['visits'] else None,
                'ci95_low': round(lo, 4) if lo is not None else None, 'ci95_high': round(hi, 4) if hi is not None else None,
                'patients': len(a['patients']), 'patients_leaking': len(a['outside_patients']),
                'claim_dollars': round(a['dollars'], 2), 'outside_dollars': round(a['outside_dollars'], 2),
                'dollar_leakage_share': round(a['outside_dollars'] / a['dollars'], 4) if a['dollars'] else None,
                'outside_destinations': ', '.join(f'{d} {n}' for d, n in a['dest'].most_common(3))}
    rows = [row(k, a) for k, a in agg.items() if k != '__all__' and (a['visits'] or a['dollars'])]
    rows.sort(key=lambda r: (-(r['outside_visits']), -(r['outside_dollars']), str(r[group])))
    return {'definition': p['leakage'], 'group_by': group, 'specialty': spec or None, 'attributed_patients': len(attributed),
            'overall': row('all', tot), 'by': rows}


def _steer(data, p, attributed):
    line = p['steer_specialty']
    pr = lambda pid: (data['patients'].get(pid) or {}).get('practice')
    vol = Counter(); pool_v, pool_p, pool_d = 0, set(), 0.0; dollars_at = defaultdict(float); claims_at = Counter()
    lines = Counter()
    for pid, _, c, o, src, usd in data['visits'] + data['claims']:
        if pid not in attributed or org_type(data, o) in NON_CARE_ORG_TYPES: continue
        sl = service_line(data, c, o)
        if src == 'visit': lines[sl] += 1
        if sl != line: continue
        inn = in_network(data, p, o, sl, pr(pid))
        if src == 'visit' and inn: vol[o] += 1
        if src == 'claim' and inn: dollars_at[o] += usd or 0.0; claims_at[o] += 1
    asked = p['steer_to']
    target = match_or(asked, data, vol) if asked else ''
    if not target:
        target = vol.most_common(1)[0][0] if vol else ''
    for pid, _, c, o, src, usd in data['visits'] + data['claims']:
        if pid not in attributed or org_type(data, o) in NON_CARE_ORG_TYPES: continue
        sl = service_line(data, c, o)
        if sl != line or o == target: continue
        inn = in_network(data, p, o, sl, pr(pid))
        if p['steer_from'] == 'outside' and inn: continue
        if src == 'visit': pool_v += 1; pool_p.add(pid)
        else: pool_d += usd or 0.0
    price = dollars_at[target] / claims_at[target] if claims_at.get(target) else None
    res = steering(pool_v, len(pool_p), pool_d, p['steer_share'], vol.get(target, 0), p['capacity_growth'], price)
    res.update(service_line=line, target=target, requested_target=asked or None, target_matched=not asked or target == match_or(asked, data, vol),
               source=p['steer_from'],
               service_line_known=line in lines, target_price_per_claim=round(price, 2) if price is not None else None,
               known_service_lines=[s for s, _ in lines.most_common(25)])
    return res


def match_or(asked, data, volume=None):
    """Organisation id or display name (exact, else a name ending in the words given, e.g. 'practice 3'); among several matches
    the one with the most service-line volume, then a primary-care practice, then the first id."""
    t = asked.lower()
    m = sorted(o for o in data['orgs'] if t in (o.lower(), (data['orgs'][o].get('name') or '').lower()))
    m = m or sorted(o for o in data['orgs'] if re.search(rf'\b{re.escape(t)}$', (data['orgs'][o].get('name') or '').lower()))
    volume = volume or {}
    return min(m, key=lambda o: (-volume.get(o, 0), org_type(data, o) != 'primary_care', o)) if m else ''


def _spread(data, p, W, idx, pairs):
    """Diffusion over the PCP graph. pcp: PCPs linked by directly shared patients; pcp_via_specialists: PCPs linked through
    shared specialists (weight = sum over specialists of min(patients shared with each PCP)); auto picks direct when it has at
    least as many edges as PCPs, else via specialists."""
    level_ok = p['level'] == 'clinician'
    pcps = sorted({a.get('pcp') for a in data['patients'].values() if a.get('pcp')} & set(idx)) if level_ok else []
    if len(pcps) < 2:
        return {'status': 'unavailable', 'reason': 'needs clinician-level graph with at least 2 attributed PCPs'}
    P = [idx[x] for x in pcps]
    direct = W[np.ix_(P, P)].copy(); np.fill_diagonal(direct, 0)
    specialists = [idx[b] for b in idx if b not in set(pcps) and is_specialist(data, b, (data['clin'].get(b) or {}).get('org'))]
    via = np.zeros_like(direct)
    if specialists:
        S = W[np.ix_(P, specialists)]
        via = np.minimum(S[:, None, :], S[None, :, :]).sum(2); np.fill_diagonal(via, 0)
    n_direct = int((np.triu(direct, 1) > 0).sum())
    graph = p['spread_graph']
    if graph == 'auto': graph = 'pcp' if n_direct >= len(pcps) else 'pcp_via_specialists'
    G = direct if graph == 'pcp' else via
    rng = np.random.default_rng(p['random_seed'])
    steps, runs, k = p['steps'], p['runs'], min(p['seeds'], len(pcps))
    if p['spread_model'] == 'independent_cascade':
        samples = live_edge_samples(G, p['transmission'], runs, rng)
        curve = lambda seeds, w=None: cascade_curve(samples, seeds, steps, w)
    else:
        thresholds = rng.random((runs, len(pcps)))
        curve = lambda seeds, w=None: threshold_curve(G, seeds, steps, thresholds, w)
    final = lambda seeds: float(curve(seeds)[:, -1].mean())
    deg_rank = sorted(range(len(pcps)), key=lambda i: (-G[i].sum(), pcps[i]))
    if p['seed_strategy'] == 'greedy':
        seeds, gains = greedy_seeds(k, len(pcps), final)
    else:
        seeds, gains = deg_rank[:k], None
    panel = Counter(a.get('pcp') for a in data['patients'].values() if a.get('pcp'))
    c = curve(seeds); base = curve(deg_rank[:k]); cp = curve(seeds, [panel[x] for x in pcps])
    over_time = [{'step': t, 'expected_pcps': round(float(c[:, t].mean()), 3), 'p10': float(np.percentile(c[:, t], 10)),
                  'p90': float(np.percentile(c[:, t], 90)), 'share_of_pcps': round(float(c[:, t].mean()) / len(pcps), 4),
                  'expected_attributed_patients': round(float(cp[:, t].mean()), 1),
                  'degree_seeds_expected_pcps': round(float(base[:, t].mean()), 3)} for t in range(steps + 1)]
    return {'status': 'model', 'model': p['spread_model'], 'graph': graph, 'pcps': len(pcps), 'direct_pcp_edges': n_direct,
            'graph_edges': int((np.triu(G, 1) > 0).sum()), 'transmission_per_shared_patient': p['transmission'], 'runs': runs,
            'random_seed': p['random_seed'], 'seed_strategy': p['seed_strategy'],
            'seeds': [{'pcp': pcps[i], 'practice': (data['clin'].get(pcps[i]) or {}).get('org'), 'attributed_patients': panel[pcps[i]],
                       'marginal_gain': (gains[x] if gains else None)} for x, i in enumerate(seeds)],
            'over_time': over_time, 'final_expected_pcps': over_time[-1]['expected_pcps'],
            'final_expected_attributed_patients': over_time[-1]['expected_attributed_patients'],
            'degree_seeds': [pcps[i] for i in deg_rank[:k]]}


def _assumptions(report):
    p = report['parameters']; out = []
    out.append(f"Patient-sharing window {report['period']['start']}..{report['period']['end']}; a patient links two providers when both have a "
               f"visit{' or claim' if report['capability'].get('claims') else ''} with them in the window; edges need >= {p['min_shared']} shared patients"
               + {'hypergeometric': f" and hypergeometric BH q <= {p['alpha']}", 'disparity': f" and disparity-filter alpha < {p['alpha']}", 'none': ''}[p['filter']] + '.')
    out.append('Laboratories, pathology, pharmacies, payers and clearinghouses are left out of the sharing graph' +
               (' (ancillary included on request).' if p['include_ancillary'] else '.'))
    out.append({'organization': 'Leakage: visits and claim dollars at organisations marked OUTSIDE (the pathway engine\'s definition).',
                'organization_or_practice': 'Leakage: OUTSIDE organisations, plus primary care at a practice other than the attributed one (pathway definition).',
                'tins': 'Leakage: organisations whose TIN is not in the supplied network TIN list.'}[p['leakage']])
    out.append('Attributed patients = patients with a recorded PCP practice. Specialist = a clinician whose specialty is not primary care, support, '
               'emergency or urgent care, outside hospital/urgent-care/home-health/primary-care organisations.')
    if report.get('steering'):
        s = report['steering']
        out.append(f"Steering (model): {s['share']:.0%} of the {'leaked' if s['source'] == 'outside' else 'non-target'} {s['service_line']} pool moves to "
                   f"{s['target'] or 'no target'}; recovered dollars use the outside claims' allowed amounts, and optionally the target's mean allowed per claim; "
                   f"no patient preference, travel or access effects; capacity flagged above +{p['capacity_growth']:.0%} of the target's current volume.")
    if report.get('spread') and report['spread'].get('status') == 'model':
        s = report['spread']
        out.append(f"Spread (model, not an observation): {s['model'].replace('_', ' ')} over the {s['graph'].replace('_', ' ')} graph; "
                   + (f"each shared patient transmits independently with probability {s['transmission_per_shared_patient']} per round"
                      if s['model'] == 'independent_cascade' else 'thresholds uniform(0,1), weights normalised per PCP')
                   + f"; {s['runs']} Monte Carlo runs, seed {s['random_seed']}; seeds chosen by {s['seed_strategy']} influence maximisation.")
    return out


def _notes(report, data):
    out = []
    lk = report['leakage']['overall']
    if lk['visits'] and not lk['outside_visits']:
        out.append('No visits at outside organisations observed: leakage beyond the received feeds cannot be seen.')
    elif lk['visits']:
        lines = sorted({r['specialty'] for r in report['leakage']['by'] if r.get('outside_visits') and 'specialty' in r})
        if lines: out.append('Outside visits occur only in: ' + ', '.join(lines[:8]) + '.')
    ps = report['pcp_sharing']
    if ps['pcps'] and ps['pairs_sharing'] < ps['pcps'] / 2:
        out.append(f"PCPs barely share patients ({ps['pairs_sharing']} sharing pairs among {ps['pcps']} PCPs): each patient sees one PCP.")
    c = report['concentration']
    if c['pcps_scored'] and c['pcps_fully_concentrated'] == c['pcps_scored']:
        out.append('Every PCP sends all specialist visits to a single organisation (HHI 10,000): referral flow is fully concentrated.')
    s = report.get('steering')
    if s and not s['service_line_known']:
        out.append(f"Service line '{s['service_line']}' not seen in the window; known: {', '.join(s['known_service_lines'][:10])}.")
    if s and s['source'] == 'outside' and not s['pool_visits'] and s['service_line_known']:
        out.append(f"No {s['service_line']} visits leaked outside the network in the window, so steering recovers nothing; "
                   'steer_from=all_other shows the consolidation pool instead.')
    if s and not s['target_matched']:
        out.append(f"Steering target '{s['requested_target']}' is not a received organisation id or name; the busiest in-network "
                   f"{s['service_line']} organisation ({s['target'] or 'none'}) is used instead.")
    if s and s['target'] and s['target_current_visits'] == 0:
        out.append(f"{s['target']} has no {s['service_line']} visits in the window: the capacity check has no baseline.")
    return out


LIMITATIONS = ['Patient-sharing ties are co-treatment of the same patients, not documented referrals or communication.',
               'Edges and centralities depend on the window, the minimum-sharing threshold and the significance filter; they describe this feed, not the market.',
               'Care density counts shared patients over all providers in the received data; care outside the feeds is invisible.',
               'Steering and spread results are models under stated assumptions, not observed effects; nothing here is causal.',
               'Synthetic data.']


def method(report):
    p = report['parameters']
    t = (f"Bipartite patient x {p['level']} incidence from visits" + (' and claims (rendering NPI -> clinician, service organisation)' if report['capability'].get('claims') else '') +
         f", window {report['period']['start']}..{report['period']['end']}; projected to provider-provider edges weighted by shared patients (W = B'B). "
         f"Edges kept with >= {p['min_shared']} shared patients and ")
    t += {'hypergeometric': f"a hypergeometric upper-tail test against the panel sizes (Benjamini-Hochberg q <= {p['alpha']}). ",
          'disparity': f"the Serrano disparity filter (alpha < {p['alpha']} at either endpoint). ", 'none': 'no significance filter. '}[p['filter']]
    t += ('Centrality: degree, strength, weighted betweenness (Brandes, Dijkstra on 1/weight, normalised), PageRank (d = 0.85), eigenvector. '
          'Communities: Louvain modularity maximisation (seeded node order). Care density (Pollack et al. 2013): mean shared-patient count over '
          'each patient\'s provider pairs. Referral concentration: HHI (0-10,000) of each PCP\'s attributed patients\' specialist visits across '
          'specialists and organisations, and of referral records across receiving organisations. Leakage: share of attributed patients\' visits '
          '(Wilson 95% intervals; patients contribute several visits) and claim allowed dollars outside the network. ')
    if report.get('steering'): t += 'Steering: stated share of the pool reassigned to the target (expected values). '
    if report.get('spread') and report['spread'].get('status') == 'model':
        t += ('Spread: Monte Carlo diffusion with common random numbers; greedy CELF seed selection (Kempe et al. 2003) compared with top-degree seeds. ')
    return t


# ---- entry points ------------------------------------------------------------------------------------
def execute(path, patient_ids, params, as_of=None):
    p = validate(params)
    db = connect(path)
    try:
        cap = capability(db)
        as_of_d = date.fromisoformat(str(as_of or cap['as_of'] or date.today().isoformat())[:10])
        data = load(db, cap, patient_ids, p, as_of_d)
    finally:
        db.close()
    report = analyse(data, p, cap)
    report['as_of'] = as_of_d.isoformat()
    return report


def _usd(x): return f'${x:,.0f}'


def summarize(report, limit=None):
    if report.get('status') == 'empty':
        return 'Too few providers with patient contacts in the window to form a network.', []
    p = report['parameters']; g = report['graph']; limit = limit or p['top']
    focus = p['focus']
    head = (f"{g['providers']} {p['level']}s, {g['patients']} patients, {g['edges']} significant patient-sharing edges "
            f"({g['candidate_edges']} with >= {p['min_shared']} shared), {len(report['communities'])} communities (modularity {g['modularity']})")
    lk = report['leakage']['overall']
    leak = (f"leakage {lk['leakage_share']:.1%} of {lk['visits']} attributed-patient visits" + (f", {_usd(lk['outside_dollars'])} of {_usd(lk['claim_dollars'])} claim dollars"
            if lk['claim_dollars'] else '')) if lk['visits'] else 'no attributed-patient visits'
    hub_txt = ', '.join(f"{h['label']} (betweenness {h['betweenness']:.3f})" for h in report['hubs'][:3])
    keep = lambda rows, cols: [{k: r.get(k) for k in cols} for r in rows[:limit]]
    hub_cols = ['id', 'specialty', 'organization', 'in_network', 'patients', 'degree', 'strength', 'betweenness', 'pagerank', 'eigenvector', 'community']
    if focus == 'usage':
        u = report['usage']
        top = ', '.join(f"{r['name']} ({r['patients']} patients, {r['share_of_attributed_patients']:.0%})" for r in u[:3])
        return f"Organisations our attributed patients use beyond their primary care practice: {top}. {leak}.", u[:limit]
    if focus == 'leakage':
        by = report['leakage']['by']; grp = report['leakage']['group_by']
        worst = [r for r in by if r['outside_visits']][:3]
        txt = f"{leak.capitalize()} ({report['leakage']['definition']} definition)"
        if worst: txt += '; most leakage in ' + ', '.join(f"{r[grp]} {r['leakage_share']:.1%} ({r['outside_visits']} visits; to {r['outside_destinations']})" for r in worst)
        return txt + '.', by[:limit]
    if focus == 'communities':
        return head + '. ' + '; '.join(f"community {c['community']}: {c['providers']} providers ({c['top_specialties']})" for c in report['communities'][:4]) + '.', report['communities'][:limit]
    if focus == 'pcp_sharing':
        ps = report['pcp_sharing']
        if not ps['pairs']:
            return f"No two of the {ps['pcps']} attributed PCPs share a patient in the window.", ps['practice_pairs'][:limit]
        t = ps['pairs'][0]
        return (f"{ps['pairs_sharing']} PCP pairs share patients among {ps['pcps']} PCPs; most: {t['pcp_a']} and {t['pcp_b']} "
                f"({t['shared_patients']} patients).", ps['pairs'][:limit])
    if focus == 'cohesion':
        c = report['cohesion']
        return (f"Care density (Pollack) mean {c['mean']}, median {c['median']} over {c['patients_with_2plus_providers']} patients with 2+ providers "
                f"({c['patients_with_1_provider']} saw one provider); highest practice: " +
                (f"{c['by_practice'][0]['practice']} {c['by_practice'][0]['mean_care_density']}" if c['by_practice'] else 'none') + '.', c['by_practice'][:limit])
    if focus == 'concentration':
        c = report['concentration']
        return (f"Referral concentration ({c['specialty']}): mean HHI across organisations {c['mean_hhi_organizations']} over {c['pcps_scored']} PCPs "
                f"with >= {c['min_specialist_visits']} specialist visits; "
                f"{c['pcps_fully_concentrated']} send every specialist visit to one organisation; {c['referral_record_count']} referral records.",
                c['by_pcp'][:limit])
    if focus == 'steering' and report.get('steering'):
        s = report['steering']
        txt = (f"Steering model: moving {s['share']:.0%} of the {s['pool_visits']} {'leaked' if s['source'] == 'outside' else 'non-target'} "
               f"{s['service_line']} visits ({s['pool_patients']} patients, {_usd(s['pool_dollars'])} claim dollars) to {s['target'] or 'no in-network target'} "
               f"recovers {s['recovered_visits']:g} visits and {_usd(s['recovered_dollars'])}")
        if s['target_volume_increase'] is not None:
            txt += f"; the target's {s['service_line']} volume rises {s['target_volume_increase']:.0%} ({'within' if s['within_capacity'] else 'ABOVE'} the stated capacity)"
        return txt + '.', [{k: v for k, v in s.items() if k != 'known_service_lines'}]
    if focus == 'spread' and report.get('spread'):
        s = report['spread']
        if s.get('status') != 'model': return 'Spread model unavailable: ' + s['reason'], []
        txt = (f"Model (not an observation): {s['model'].replace('_', ' ')} over the {s['graph'].replace('_', ' ')} graph of {s['pcps']} PCPs; seeding "
               f"{', '.join(x['pcp'] for x in s['seeds'])} reaches an expected {s['final_expected_pcps']:g} PCPs "
               f"({s['over_time'][-1]['share_of_pcps']:.0%}) after {len(s['over_time']) - 1} rounds vs {s['over_time'][-1]['degree_seeds_expected_pcps']:g} with top-degree seeds")
        return txt + '.', s['over_time']
    if focus == 'hubs':
        return f"{head}. Key hubs: {hub_txt}.", keep(report['hubs'], hub_cols)
    return f"{head}. Key hubs: {hub_txt}; {leak}.", keep(report['hubs'], hub_cols)


def visual(report, max_nodes=MAX_NODES_OUT, max_edges=MAX_EDGES_OUT):
    """Compact payload for the console graph: the highest-PageRank connected nodes and the heaviest edges among them."""
    if report.get('status') != 'answered': return {}
    nodes = [n for n in report['nodes'] if n['degree']][:max_nodes]
    pos = {n['id']: i for i, n in enumerate(nodes)}
    edges = [e for e in report['edges'] if e['a'] in pos and e['b'] in pos][:max_edges]
    return {'nodes': [{k: n[k] for k in ('id', 'specialty', 'organization', 'in_network', 'patients', 'degree', 'pagerank', 'betweenness', 'community')}
                      for n in nodes],
            'edges': [[pos[e['a']], pos[e['b']], e['shared']] for e in edges],  # [node index, node index, shared patients]
            'shown_nodes': len(nodes), 'shown_edges': len(edges)}


def follow_up():
    return ['Who are the key referral hubs in our provider network?', 'Which PCPs share the most patients?',
            'Where does care leak out of our preferred network by specialty?', 'How cohesive are our care teams by practice?']
