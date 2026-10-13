"""Learning-to-rank for the daily briefing (search-engine methods, numpy only, no model calls).

Every briefing card, whatever engine produced it, gets one feature vector:

  log_surprise      ln(surprise bits after look-elsewhere / multiplicity adjustment)
  log_impact        ln(log2(2 + impact)); impact = patients affected + dollars as member-years of average spend
                    (10 when a card names neither, as the original rule did)
  actionability     1 = carries a patient worklist, 0.5 = a follow-up question only, 0 = neither
  novelty           1 - share of the last NOVELTY_WINDOW briefings that showed the same card signature
  confidence        1 - q (discovery), 1 - relative CI width (shrunk outliers), crossing probability (forecasts),
                    else 1 - 2^-bits (the chance the signal is not noise)
  data_quality      1 when the card's metric is touched by a data-gap card in the same briefing (gap-affected)
  recency           exp(-months behind the newest dated card / 3); 0.5 for undated (whole-history) cards
  type:<kind>       one-hot card type

Score = w . x. The cold-start PRIOR is w = 1 on log_surprise and log_impact and 0 elsewhere, so
exp(score) = bits x log2(2 + impact): exactly the hand-set information x impact rule the briefing used
before (monitor.score). With no feedback the ranking is that rule.

Learning: RankNet-style logistic pairwise loss on implicit feedback (asked follow-up > opened > shown and
skipped above a clicked card > dismissed), with a Gaussian prior centred on PRIOR (L2 toward the prior,
precision PRIOR_PRECISION). Fitted by Newton's method; the inverse Hessian is a Laplace posterior over w.

Explore slot: one card per briefing (the last displayed slot) is chosen by Thompson sampling: draw
w ~ N(w_hat, Sigma) and take the best card under the draw among those not already shown, so feature
directions with little feedback get tried and the ranker can learn.

Diversity: maximal marginal relevance (MMR) re-orders the list so one practice or one metric does not fill
the briefing: argmax lambda * relevance - (1 - lambda) * max similarity to cards already placed.

Why: each card carries its top feature contributions relative to the briefing's average card.

History (briefing-rank.sqlite3 in the stack directory) is written only by serve(..., record=True), which the
service calls when it builds the briefing for the console; feedback events are logged by record_feedback().
"""
import hashlib, json, math, re, sqlite3, time
from pathlib import Path

import numpy as np

VERSION = 'briefing-rank/1'
STORE = 'briefing-rank.sqlite3'
KINDS = ('concentration', 'data gap', 'trajectory', 'forecast', 'metric change', 'outlier', 'anomaly', 'review flag', 'market move')
FEATURES = ('log_surprise', 'log_impact', 'actionability', 'novelty', 'confidence', 'data_quality', 'recency',
            *[f'type:{k}' for k in KINDS])
PRIOR = {'log_surprise': 1.0, 'log_impact': 1.0}  # exp(score) = bits x log2(2 + impact), the original rule
PRIOR_PRECISION = 1.0
BITS_FLOOR = 1e-3          # zero-surprise cards sink to the bottom (their original score was 0)
NOVELTY_WINDOW = 7         # briefings
MMR_LAMBDA = 0.85
TOP = 10                   # cards the console shows; the explore slot is the last of these
EVENTS = {'asked_follow_up': 2.0, 'opened': 1.0, 'dismissed': -1.0}
LABELS = {'log_surprise': 'surprise', 'log_impact': 'impact', 'actionability': 'actionable', 'novelty': 'new',
          'confidence': 'confidence', 'data_quality': 'data-gap affected', 'recency': 'recent'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS briefings(id TEXT PRIMARY KEY, created REAL, model TEXT);
CREATE TABLE IF NOT EXISTS shown(briefing_id TEXT, card_id TEXT, signature TEXT, position INTEGER, features TEXT, explore INTEGER,
                                 PRIMARY KEY(briefing_id, card_id));
CREATE TABLE IF NOT EXISTS feedback(briefing_id TEXT, card_id TEXT, event TEXT, created REAL);
"""


# ---- card identity and similarity -----------------------------------------------------------------
METRIC_WORDS = [('ed', r'\b(?:ed|er|emergency)\b|ed_rate|ed_visits|emergency_visit'),
                ('admit', r'\badmi(?:t|ssions?)\b|admit_rate|inpatient'),
                ('pmpm', r'\bpmpm\b|\bcost\b|\bspend|allowed|dollars'),
                ('a1c', r'a1c|hba1c|diabet|glyc'), ('bp', r'\bbp\b|blood pressure|systolic|bp_control|hypertens'),
                ('egfr', r'egfr|ckd|kidney'), ('hemoglobin', r'hemoglobin'),
                ('denial', r'denial'), ('no_show', r'no[_ -]?show'), ('lab', r'lab_volume|\blab\b'),
                ('lag', r'arrival_lag|\blag\b'), ('claims', r'claim|benford|duplicate')]


def _ev(card):
    return card.get('evidence') if isinstance(card.get('evidence'), dict) else {}


def metric_key(card):
    """The metric a card is about, normalised across engines (ED rate, admissions, PMPM, A1c...)."""
    ev = _ev(card)
    text = ' '.join(str(x) for x in (ev.get('metric'), ev.get('category'), card.get('topic'), card.get('follow_up')) if x).lower()
    return next((k for k, rx in METRIC_WORDS if re.search(rx, text)), (card.get('topic') or '').lower())


def entity_key(card):
    """The practice / PCP / payer / segment a card is about ('' for population-wide cards)."""
    ev = _ev(card)
    if isinstance(ev.get('segment'), dict) and ev['segment']:
        return ','.join(f'{k}={v}' for k, v in sorted(ev['segment'].items()))
    for k in ('unit', 'group'):
        v = ev.get(k)
        if v not in (None, '', 'all', 'overall', 'network', 'NETWORK'): return str(v)
    return ''


def signature(card):
    return '|'.join([card.get('kind') or '', metric_key(card), entity_key(card), card.get('follow_up') or ''])


def card_id(card):
    return hashlib.sha1(signature(card).encode()).hexdigest()[:12]


def similarity(a, b):
    """0..1: same metric 0.6, same practice/unit 0.4 (population-wide cards share no entity)."""
    s = 0.6 if metric_key(a) == metric_key(b) else 0.0
    ea, eb = entity_key(a), entity_key(b)
    return s + (0.4 if ea and ea == eb else 0.0)


# ---- features ------------------------------------------------------------------------------------
def _bits(card):
    if card.get('surprise_bits') is not None: return float(card['surprise_bits'])
    from .briefing import surprise_bits
    return surprise_bits(card)


def _impact(card):
    return float(card['impact']) if card.get('impact') else 10.0  # same default as the original score()


def _month(card):
    ev = _ev(card)
    m = ev.get('month')
    if not m and isinstance(ev.get('months'), list) and ev['months']: m = ev['months'][-1]
    return str(m)[:7] if m and re.fullmatch(r'\d{4}-\d{2}.*', str(m)) else None


def _confidence(card, bits):
    ev, kind = _ev(card), card.get('kind')
    if isinstance(ev.get('q'), (int, float)): return float(min(max(1 - ev['q'], 0), 1))
    ci = ev.get('ed_per_1000_ci95')
    if ci and ev.get('ed_per_1000_shrunk'): return float(min(max(1 - (ci[1] - ci[0]) / max(ev['ed_per_1000_shrunk'], 1e-9), 0), 1))
    pts = ev.get('patients') if isinstance(ev.get('patients'), list) else None
    if kind == 'forecast' and pts and isinstance(pts[0], dict) and isinstance(pts[0].get('p_cross'), (int, float)):
        return float(pts[0]['p_cross'])
    return float(1 - 2 ** -max(bits, 0))


def features(cards, history=None):
    """Feature matrix (cards x FEATURES) for one briefing; history = {signature: share of recent briefings showing it}."""
    history = history or {}
    gap_metrics = {metric_key(c) for c in cards if c.get('kind') == 'data gap'}
    months = [m for m in map(_month, cards) if m]
    newest = max(months) if months else None
    X = np.zeros((len(cards), len(FEATURES)))
    for i, c in enumerate(cards):
        bits, ev = _bits(c), _ev(c)
        worklist = isinstance(ev.get('patients'), list) and bool(ev['patients'])
        m = _month(c)
        behind = ((int(newest[:4]) - int(m[:4])) * 12 + int(newest[5:7]) - int(m[5:7])) if m and newest else None
        row = {'log_surprise': math.log(max(bits, BITS_FLOOR)), 'log_impact': math.log(math.log2(2 + max(_impact(c), 0))),
               'actionability': 1.0 if worklist else 0.5 if c.get('follow_up') else 0.0,
               'novelty': 1.0 - history.get(signature(c), 0.0), 'confidence': _confidence(c, bits),
               'data_quality': 1.0 if c.get('kind') != 'data gap' and metric_key(c) in gap_metrics else 0.0,
               'recency': math.exp(-behind / 3) if behind is not None else 0.5}
        if c.get('kind') in KINDS: row[f"type:{c['kind']}"] = 1.0
        X[i] = [row.get(f, 0.0) for f in FEATURES]
    return X


def prior_weights():
    return np.array([PRIOR.get(f, 0.0) for f in FEATURES])


# ---- the ranker ----------------------------------------------------------------------------------
class Ranker:
    """Linear scorer w . x with a Gaussian (Laplace) posterior; cold start = PRIOR with covariance I / PRIOR_PRECISION."""

    def __init__(self, w=None, cov=None, pairs=0, events=0):
        self.w = prior_weights() if w is None else np.asarray(w, float)
        self.cov = np.eye(len(FEATURES)) / PRIOR_PRECISION if cov is None else np.asarray(cov, float)
        self.pairs, self.events = pairs, events

    @property
    def learned(self):
        return self.pairs > 0

    def score(self, X):
        return X @ self.w

    def sample(self, rng):
        return rng.multivariate_normal(self.w, self.cov, method='cholesky')

    def describe(self):
        return {'model': 'pairwise logistic (RankNet) with L2 toward the prior' if self.learned else 'prior (information x impact)',
                'pairs': self.pairs, 'events': self.events,
                'weights': {f: round(float(v), 3) for f, v in zip(FEATURES, self.w) if abs(v) > 1e-6}}

    @classmethod
    def fit(cls, diffs, weights=None, precision=PRIOR_PRECISION, iterations=50, events=0):
        """Minimise sum_k a_k log(1 + exp(-w . d_k)) + precision/2 ||w - w0||^2 (Newton). diffs: preferred minus other."""
        w0 = prior_weights()
        D = np.asarray(diffs, float).reshape(-1, len(FEATURES))
        a = np.ones(len(D)) if weights is None else np.asarray(weights, float)
        if not len(D): return cls(events=events)
        w = w0.copy()
        for _ in range(iterations):
            z = D @ w
            p = 1 / (1 + np.exp(np.clip(z, -50, 50)))           # sigma(-z): probability the pair is mis-ordered
            grad = -(D * (a * p)[:, None]).sum(0) + precision * (w - w0)
            H = (D * (a * p * (1 - p))[:, None]).T @ D + precision * np.eye(len(w))
            step = np.linalg.solve(H, grad)
            w = w - step
            if np.abs(step).max() < 1e-8: break
        z = D @ w
        p = 1 / (1 + np.exp(np.clip(z, -50, 50)))
        H = (D * (a * p * (1 - p))[:, None]).T @ D + precision * np.eye(len(w))
        return cls(w, np.linalg.inv(H), pairs=len(D), events=events)


def preference_pairs(shown, grades):
    """Implicit-feedback pairs for one briefing (Joachims' skip-above): shown = [(card_id, position, x)];
    grades = {card_id: grade}. An ungraded card is a negative only if it sat above the lowest card the user
    engaged with (it was seen and passed over); dismissed cards are explicit negatives wherever they sat."""
    engaged = [pos for cid, pos, _ in shown if grades.get(cid, 0) > 0]
    depth = max(engaged) if engaged else -1
    items = [(grades.get(cid, 0.0), x) for cid, pos, x in shown if cid in grades or pos < depth]
    diffs, weights = [], []
    for gi, xi in items:
        for gj, xj in items:
            if gi > gj:
                diffs.append(np.asarray(xi) - np.asarray(xj)); weights.append(gi - gj)
    return diffs, weights


# ---- ranking -------------------------------------------------------------------------------------
def mmr(cards, relevance, lam=MMR_LAMBDA):
    """Greedy maximal marginal relevance order (indices). relevance is min-max normalised within the briefing."""
    r = np.asarray(relevance, float)
    if not len(r): return [], {}
    span = r.max() - r.min()
    rel = (r - r.min()) / span if span > 0 else np.ones_like(r)
    left, order, penalty = list(range(len(cards))), [], {}
    while left:
        def value(i):
            sim = max((similarity(cards[i], cards[j]) for j in order), default=0.0)
            return lam * rel[i] - (1 - lam) * sim, sim
        best = max(left, key=lambda i: (value(i)[0], -i))
        penalty[best] = value(best)[1]
        order.append(best); left.remove(best)
    return order, penalty


def _why(x, w, mean):
    contrib = w * (x - mean)
    out = []
    for k in np.argsort(-np.abs(contrib))[:3]:
        if abs(contrib[k]) < 1e-3: continue
        out.append({'feature': FEATURES[k], 'value': round(float(x[k]), 3), 'contribution': round(float(contrib[k]), 3)})
    return out


def _why_text(card, why, penalty, explore):
    parts = []
    for f in why:
        name = f['feature']
        if name == 'log_surprise': desc = f"surprise {card.get('surprise_bits', 0):.1f} bits"
        elif name == 'log_impact': desc = f"impact {_impact(card):,.0f}"
        elif name.startswith('type:'): desc = f"{name[5:]} card"
        else: desc = f"{LABELS.get(name, name)} {f['value']:.2f}"
        parts.append(f"{desc} ({'+' if f['contribution'] >= 0 else ''}{f['contribution']:.2f})")
    text = 'Ranked by ' + ', '.join(parts) if parts else 'Ranked at the briefing average'
    if penalty: text += f'; moved down for overlap with a card above (similarity {penalty:.1f})'
    if explore: text = 'Explore slot (Thompson sample): shown so the ranker can learn whether this kind of card is useful. ' + text
    return text


def rank(cards, history=None, model=None, top=TOP, explore=True, seed=0, lam=MMR_LAMBDA):
    """Order cards: learned (or prior) score, MMR diversity, one Thompson explore slot at position `top`.
    Returns the cards (copies) with card_id, signature, features, scores and a 'why' explanation."""
    model = model or Ranker()
    cards = [dict(c) for c in cards]
    if not cards: return []
    X = features(cards, history)
    s = model.score(X)
    base = sorted(range(len(cards)), key=lambda i: (-s[i], i))   # stable: equal scores keep engine order
    order, penalty = mmr([cards[i] for i in base], s[base], lam)
    order = [base[i] for i in order]
    penalty = {base[i]: v for i, v in penalty.items()}
    explored = None
    if explore and len(cards) > top:
        rng = np.random.default_rng(seed)
        draw = X @ model.sample(rng)
        pool = order[top - 1:]
        explored = max(pool, key=lambda i: (draw[i], -i))
        if explored != order[top - 1]:
            order.remove(explored); order.insert(top - 1, explored)
        else:
            explored = None
    mean = X.mean(0)
    out, seen = [], {}
    for pos, i in enumerate(order):
        c = cards[i]
        cid = card_id(c)
        seen[cid] = seen.get(cid, 0) + 1
        if seen[cid] > 1: cid = f'{cid}-{seen[cid]}'
        why = _why(X[i], model.w, mean)
        c.update(card_id=cid, signature=signature(c), rank=pos + 1, rank_score=round(float(s[i]), 4),
                 prior_score=round(float(X[i] @ prior_weights()), 4), explore=i == explored,
                 features={f: round(float(v), 4) for f, v in zip(FEATURES, X[i]) if v or not f.startswith('type:')},
                 why=why, why_text=_why_text(c, why, penalty.get(i, 0.0), i == explored))
        out.append(c)
    return out


# ---- history and feedback store -------------------------------------------------------------------
def _db(path):
    db = sqlite3.connect(Path(path) / STORE)
    db.executescript(SCHEMA)
    return db


def _read(path):
    target = Path(path) / STORE
    return sqlite3.connect(f'file:{target}?mode=ro', uri=True) if target.exists() else None


def history(path, n=NOVELTY_WINDOW):
    """{signature: share of the last n briefings that showed it} (read-only)."""
    db = _read(path)
    if db is None: return {}
    try:
        ids = [r[0] for r in db.execute('SELECT id FROM briefings ORDER BY created DESC LIMIT ?', (n,))]
        if not ids: return {}
        rows = db.execute(f"SELECT signature, count(DISTINCT briefing_id) FROM shown WHERE briefing_id IN ({','.join('?' * len(ids))}) "
                          f"AND position < ? GROUP BY signature", (*ids, TOP)).fetchall()
    finally:
        db.close()
    return {sig: k / len(ids) for sig, k in rows}


def train(path):
    """Fit the ranker on every logged briefing with feedback (read-only). Cold start returns the prior."""
    db = _read(path)
    if db is None: return Ranker()
    try:
        shown, grades, events = {}, {}, 0
        for bid, cid, pos, feats in db.execute('SELECT briefing_id, card_id, position, features FROM shown'):
            f = json.loads(feats)
            shown.setdefault(bid, []).append((cid, pos, [f.get(k, 0.0) for k in FEATURES]))
        for bid, cid, event in db.execute('SELECT briefing_id, card_id, event FROM feedback'):
            events += 1
            g = grades.setdefault(bid, {})
            g.setdefault(cid, set()).add(event)
    finally:
        db.close()
    diffs, weights = [], []
    for bid, g in grades.items():  # the strongest positive engagement wins; dismissed without engagement = -1
        g = {cid: max((EVENTS[e] for e in ev if EVENTS[e] > 0), default=EVENTS['dismissed']) for cid, ev in g.items()}
        d, w = preference_pairs(shown.get(bid, []), g)
        diffs += d; weights += w
    return Ranker.fit(diffs, weights, events=events)


def record_briefing(path, briefing_id, cards, model=None):
    db = _db(path)
    try:
        db.execute('INSERT OR REPLACE INTO briefings VALUES (?,?,?)', (briefing_id, time.time(), json.dumps((model or Ranker()).describe())))
        db.executemany('INSERT OR REPLACE INTO shown VALUES (?,?,?,?,?,?)',
                       [(briefing_id, c['card_id'], c['signature'], c['rank'] - 1, json.dumps(c['features']), int(c['explore'])) for c in cards])
        db.commit()
    finally:
        db.close()


def record_feedback(path, card_id, event, briefing_id=None):
    """Log 'opened' / 'asked_follow_up' / 'dismissed' for a card; without briefing_id it attaches to the latest briefing that showed it."""
    if event not in EVENTS: raise ValueError(f"Feedback event must be one of {', '.join(EVENTS)}")
    if not isinstance(card_id, str) or not re.fullmatch(r'[0-9a-f]{12}(?:-\d{1,3})?', card_id): raise ValueError('Unknown briefing card id')
    if briefing_id is not None and (not isinstance(briefing_id, str) or not re.fullmatch(r'[0-9a-z-]{1,64}', briefing_id)):
        raise ValueError('Invalid briefing id')
    db = _db(path)
    try:
        q = ('SELECT s.briefing_id FROM shown s JOIN briefings b ON b.id = s.briefing_id WHERE s.card_id = ?'
             + (' AND s.briefing_id = ?' if briefing_id else '') + ' ORDER BY b.created DESC LIMIT 1')
        row = db.execute(q, (card_id, briefing_id) if briefing_id else (card_id,)).fetchone()
        if not row: raise ValueError('Card was not shown in a recorded briefing')
        db.execute('INSERT INTO feedback VALUES (?,?,?,?)', (row[0], card_id, event, time.time()))
        db.commit()
    finally:
        db.close()
    return {'recorded': True, 'briefing_id': row[0], 'card_id': card_id, 'event': event}


def validate(p):
    """Parameters of the 'briefing' graph operation: {top}."""
    if not isinstance(p, dict) or set(p) - {'top'}: raise ValueError('Briefing takes only top')
    top = p.get('top', TOP)
    if type(top) is not int or not 1 <= top <= 25: raise ValueError('Briefing top must be 1..25')
    return {'top': top}


def handle_feedback(path, body):
    """POST /api/stack/briefing-feedback body: {card_id, event, briefing_id?}."""
    if not isinstance(body, dict) or set(body) - {'card_id', 'event', 'briefing_id'} or not {'card_id', 'event'} <= set(body):
        raise ValueError('Briefing feedback needs card_id and event (opened | asked_follow_up | dismissed), optional briefing_id')
    return record_feedback(path, body['card_id'], body['event'], body.get('briefing_id'))


def summary(path):
    db = _read(path)
    if db is None: return {'schema': VERSION, 'briefings': 0, 'events': {}}
    try:
        n = db.execute('SELECT count(*) FROM briefings').fetchone()[0]
        ev = dict(db.execute('SELECT event, count(*) FROM feedback GROUP BY event').fetchall())
    finally:
        db.close()
    return {'schema': VERSION, 'briefings': n, 'events': ev}


def serve(path, briefing, record=False, top=TOP, explore=True):
    """Re-rank a built briefing with the history-aware learned ranker. record=True (the service's briefing route only)
    logs what was shown so feedback can be attributed and novelty computed."""
    model, hist = train(path), history(path)
    briefing_id = time.strftime('%Y%m%d-%H%M%S') + '-' + hashlib.sha1(str(time.time_ns()).encode()).hexdigest()[:6]
    seed = int(hashlib.sha1(briefing_id.encode()).hexdigest()[:8], 16)
    cards = rank(briefing.get('cards') or [], hist, model, top=top, explore=explore, seed=seed)
    if record and cards: record_briefing(path, briefing_id, cards, model)
    return {**briefing, 'cards': cards, 'briefing_id': briefing_id if record else None,
            'ranking': {'schema': VERSION, **model.describe(), 'features': list(FEATURES), 'prior': PRIOR,
                        'mmr_lambda': MMR_LAMBDA, 'explore_slot': top if explore and len(cards) > top else None,
                        'novelty_window': NOVELTY_WINDOW, 'history_briefings': bool(hist)}}


# ---- offline evaluation ---------------------------------------------------------------------------
def ndcg(relevances, k=5):
    """NDCG@k of a ranked list of graded relevances."""
    rel = np.asarray(relevances, float)
    disc = 1 / np.log2(np.arange(2, k + 2))
    dcg = float(((2 ** rel[:k] - 1) * disc[:len(rel[:k])]).sum())
    ideal = np.sort(rel)[::-1][:k]
    idcg = float(((2 ** ideal - 1) * disc[:len(ideal)]).sum())
    return dcg / idcg if idcg > 0 else 0.0


SIM_METRICS = ('ed', 'admit', 'pmpm', 'a1c', 'bp', 'egfr', 'denial', 'no_show')
SIM_UNITS = ('PR1', 'PR2', 'PR3', 'PR4', 'PR5', 'PR6')
USERS = {  # known preferences (true weights over FEATURES) for the offline simulation
    'care_manager': {'actionability': 3.0, 'data_quality': -2.0, 'confidence': 1.0, 'log_surprise': 0.5, 'log_impact': 0.5,
                     'type:review flag': -1.5, 'type:market move': -0.5},
    'finance_lead': {'log_impact': 2.0, 'type:market move': 1.5, 'type:metric change': 1.5, 'type:trajectory': -1.0,
                     'type:forecast': -1.0, 'log_surprise': 0.3, 'recency': 1.0},
}


def synthetic_cards(rng, n=20):
    """A synthetic briefing shaped like the engines' cards (no real data)."""
    from .briefing import rank as base_rank
    cards = []
    for i in range(n):
        kind = KINDS[int(rng.integers(len(KINDS)))]
        metric, unit = SIM_METRICS[int(rng.integers(len(SIM_METRICS)))], SIM_UNITS[int(rng.integers(len(SIM_UNITS)))]
        ev = {'metric': metric, 'unit': unit, 'month': f'2029-{int(rng.integers(1, 8)):02d}'}
        if kind in ('trajectory', 'forecast'):
            ev['patients'] = [{'patient_id': f'S{i}-{k}', 'p_cross': float(rng.uniform(0.5, 1))} for k in range(3)]
        if kind == 'concentration': ev['q'] = float(10 ** -rng.uniform(1, 8))
        c = {'kind': kind, 'topic': f'{metric} ({unit})', 'headline': f'synthetic {kind} {metric} {unit} #{i}',
             'patients': int(rng.lognormal(4, 1.2)), 'dollars': float(rng.lognormal(10, 1)) if kind in ('market move', 'metric change') else None,
             'strength': float(rng.exponential(3)), 'surprise_bits': float(rng.exponential(6)),
             'follow_up': f'What happened to {metric} at {unit} ({kind}, {i})?', 'evidence': ev}
        cards.append(base_rank(c, 5000.0))
    return cards


def true_grades(cards, prefs):
    """Graded relevance under a known preference: top 20% of true utility = 2, next 30% = 1, rest 0."""
    u = features(cards) @ np.array([prefs.get(f, 0.0) for f in FEATURES])
    order = np.argsort(-u)
    g = np.zeros(len(cards))
    g[order[:max(1, len(cards) // 5)]] = 2
    g[order[max(1, len(cards) // 5):len(cards) // 2]] = 1
    return g


def simulate(path, prefs, train_briefings=40, test_briefings=150, n_cards=20, seed=7, k=5):
    """Offline evaluation: a simulated user with known preferences browses ranked briefings (cascade: examines cards
    top-down with decaying attention, asks about highly relevant ones, opens relevant ones, dismisses some irrelevant
    ones, often stops after an ask). Feedback goes through the real store and trainer (path = a scratch directory).
    Returns NDCG@k of the prior and of the learned ranker on held-out briefings."""
    rng = np.random.default_rng(seed)
    for b in range(train_briefings):
        cards = synthetic_cards(rng, n_cards)
        grades = true_grades(cards, prefs)
        for i, c in enumerate(cards): c['_sim'] = i
        ranked = rank(cards, model=train(path), seed=int(rng.integers(1 << 31)))
        bid = f'sim-{seed}-{b}'
        record_briefing(path, bid, ranked)
        for pos, c in enumerate(ranked[:TOP]):
            if rng.random() > 0.9 ** pos: break                 # attention decays down the list
            g = grades[c['_sim']]
            if g == 2 and rng.random() < 0.8:
                record_feedback(path, c['card_id'], 'asked_follow_up', bid)
                if rng.random() < 0.5: break
            elif g >= 1 and rng.random() < 0.6: record_feedback(path, c['card_id'], 'opened', bid)
            elif g == 0 and rng.random() < 0.3: record_feedback(path, c['card_id'], 'dismissed', bid)
    model = train(path)
    test = np.random.default_rng(seed + 1)
    prior_n, learned_n = [], []
    for _ in range(test_briefings):
        cards = synthetic_cards(test, n_cards)
        g = true_grades(cards, prefs)
        for i, c in enumerate(cards): c['_sim'] = i
        prior_n.append(ndcg([g[c['_sim']] for c in rank(cards, explore=False)], k))
        learned_n.append(ndcg([g[c['_sim']] for c in rank(cards, model=model, explore=False)], k))
    return {'ndcg_prior': round(float(np.mean(prior_n)), 4), 'ndcg_learned': round(float(np.mean(learned_n)), 4),
            'briefings': train_briefings, 'pairs': model.pairs, 'events': model.events, 'weights': model.describe()['weights']}
