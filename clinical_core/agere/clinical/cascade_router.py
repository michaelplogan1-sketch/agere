"""Learned cascade router, labelled by outcome (FRONTIER #28; Mike 2026-10-22: "label the router by outcome").

labels() turns what Agere already logged into (question, tier, right) rows:
  bench rows        non-blind saved runs (latest per question; blind/heldout sets never used): the tier that produced the
                    delivered answer is right when the adjudicated / scored answer was right, else wrong; on cascade
                    receipts every proposer in the log that was tried and did not answer is a negative for that question
  cascade traces    <training>/traffic/cascade-traces-*.jsonl (written by record_trace after every answer): the accepted
                    tier is right when the answer was verified (learning_hooks.verified) and not corrected, every other
                    tried tier that proposed nothing / was rejected / errored is a negative
  traffic labels    labels-traffic-*.jsonl: verified accepted proposer = right; gaps-*.jsonl proposer logs = negatives
  learning outcomes <stack>/learning.sqlite3 outcomes (legacy routes mapped to tiers): answered and never corrected or
                    quarantined = right; escalated / declined / internal_error = that route failed; a correction = wrong
train() fits one L2 logistic model per tier with enough labels (hashed word uni/bigrams + the router's lexical cues),
smoothed by the tier's base rate; tiers with too few labels keep only the smoothed base rate. predict() -> P(this tier's
checked answer is right | question). reorder() is used by proposers.Cascade.order AFTER System One routing: inside each
block System One formed (its moved front tiers, then the rest) and inside each cost class (free, System One, Claude) the
tiers are sorted by predicted success, so a paid tier never jumps ahead of a free one and no tier is ever removed.
AGERE_CASCADE_ROUTER=off keeps the order. Model: cascade_router_model.json (tools/learned_routing/train_cascade_router.py).
"""
import hashlib
import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from .evaluation import active as _evaluating

VERSION = 'cascade-router/1'
ROOT = Path(__file__).resolve().parents[3]
MODEL_FILE = Path(__file__).with_name('cascade_router_model.json')
DIM = 1024
MIN_LABELS = 12          # per tier, with both outcomes present, to fit a question-dependent model
PRIOR_STRENGTH = 4.0     # Beta prior pseudo-counts toward the global success rate

TIER_OF_ROUTE = {
    'local_compiler': 'local_compiler', 'metric_router': 'metric_router', 'operator_router': 'operator_router',
    'hosted_verified_sql': 'hosted_verified_sql', 'hosted_missing_data': 'hosted_verified_sql', 'cohort_family': 'cohort_family_early',
    'cohort_family_bottled': 'bottled_sql', 'dq_family': 'specialist_families_early', 'vbc_family': 'specialist_families_early',
    'semantic_cache': 'semantic_cache', 'template_decoding': 'template_decoding', 'learned_verified_sql': 'learned_verified_sql', 'bottled_sql': 'bottled_sql',
    'local_model_rewrite_compiled': 'qwen_rewrite_compiler', 'local_model_sql': 'local_free_sql', 'frontier_escalation': 'frontier_graph_planner',
    'compact_retrieval_typed_router': 'typed_decider', 'evidence_retrieval': 'evidence_retrieval', 'evidence_review': 'evidence_review',
    'together_semantic_analyst': 'semantic_analyst', 'hybrid_analysts': 'hybrid_analysts', 'local_requires_input': 'undelivered_input',
    'local_unsupported': 'undelivered_input', 'scoped_review_compiler': 'scoped_review_compiler', 'atomic_system_one': 'atomic_system_one',
}
NEGATIVE_OUTCOMES = {'no proposal', 'rejected', 'error', 'declined'}
# a provider outage (no credits, rate limit, overload) says nothing about whether the tier would have been right
OUTAGE = re.compile(r'provider_unavailable|credit balance|billing|rate.?limit|overload|ProviderUnavailable|HTTP 429|HTTP 529', re.I)
_WORD = re.compile(r"[a-z0-9%$]+")


# -- features --------------------------------------------------------------------------------------------------------
def _h(token):
    return int(hashlib.md5(token.encode()).hexdigest()[:8], 16) % DIM


def features(question):
    """Sparse {index: value}: hashed word unigrams + bigrams (L2-normalised) and the learned router's lexical cues."""
    words = _WORD.findall(str(question).lower())
    f = {}
    for t in words + [a + '_' + b for a, b in zip(words, words[1:])]:
        f[_h(t)] = f.get(_h(t), 0.0) + 1.0
    norm = math.sqrt(sum(v * v for v in f.values())) or 1.0
    f = {k: v / norm for k, v in f.items()}
    try:
        from .learned_router import lexical, cue_names
        for name, v in zip(cue_names(), lexical(question)):
            if v: f[_h('cue:' + name)] = f.get(_h('cue:' + name), 0.0) + 0.5
    except Exception:  # noqa: BLE001 - cues need the compiler; words alone still work
        pass
    return f


def _dot(w, f): return sum(w.get(str(k), 0.0) * v for k, v in f.items())


# -- labels ----------------------------------------------------------------------------------------------------------
def _norm(q): return ' '.join(re.sub(r'[^a-z0-9%<>=.]+', ' ', str(q).lower()).split())


def _tier_of(receipt):
    receipt = receipt or {}
    name = ((receipt.get('interpretation') or {}).get('proposer') or {}).get('name')
    if name: return name
    route = receipt.get('route')
    if route == 'exact_question_cache': route = receipt.get('original_route')
    return TIER_OF_ROUTE.get(route)


def _from_log(question, log, accepted, right, source):
    rows = []
    if accepted: rows.append({'question': question, 'tier': accepted, 'right': bool(right), 'source': source})
    for e in log or []:
        if e.get('outcome') == 'error' and OUTAGE.search(str(e.get('detail') or '')): continue
        if e.get('proposer') != accepted and e.get('outcome') in NEGATIVE_OUTCOMES:
            rows.append({'question': question, 'tier': e['proposer'], 'right': False, 'source': source + ':tried'})
    return rows


def bench_labels(results=None):
    try:
        import sys
        sys.path.insert(0, str(ROOT))
        from tools import certify_gates as CG
        pool, _, blind_q = CG.bench_rows(CG.results_dir(results))
    except Exception:  # noqa: BLE001 - no saved runs
        return []
    rows = []
    for r in pool:
        if _norm(r.get('question')) in blind_q: continue
        receipt = (r.get('response') or {}).get('planner_receipt') or {}
        if not CG.asserted(r.get('answer')): continue
        tier = _tier_of(receipt)
        rows += _from_log(r['question'], (receipt.get('interpretation') or {}).get('proposers') or receipt.get('proposers'),
                          tier, not r['wrong'], 'bench')
    return rows


def traffic_labels(directory=None):
    from .learning_hooks import training_dir
    d = Path(directory) if directory else training_dir()
    rows = []
    for path in sorted(d.glob('cascade-traces-*.jsonl')) if d.exists() else []:
        for line in path.read_text().splitlines():
            try: t = json.loads(line)
            except ValueError: continue
            rows += _from_log(t['question'], t.get('proposers'), t.get('accepted'), t.get('verified'), 'trace')
    for path in sorted(d.glob('labels-traffic-*.jsonl')) if d.exists() else []:
        for line in path.read_text().splitlines():
            try: t = json.loads(line)
            except ValueError: continue
            q = t.get('canonical') if not t.get('paraphrases') else t['paraphrases'][0].get('text')
            if t.get('proposer') and q: rows.append({'question': q, 'tier': t['proposer'], 'right': True, 'source': 'traffic'})
    for path in sorted(d.glob('gaps-*.jsonl')) if d.exists() else []:
        for line in path.read_text().splitlines():
            try: t = json.loads(line)
            except ValueError: continue
            q = t.get('question')
            if isinstance(q, str) and q.startswith('{'):
                m = re.search(r"'text':\s*'([^']+)'", q); q = m.group(1) if m else None
            if q: rows += _from_log(q, t.get('proposers'), None, False, 'gaps')
    return rows


def outcome_labels(stack_paths):
    rows = []
    for path in stack_paths:
        target = Path(path) / 'learning.sqlite3'
        if not target.exists(): continue
        with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'outcomes' not in names: continue
            corrected = {_norm(q) for (q,) in db.execute('SELECT question FROM corrections')} if 'corrections' in names else set()
            quarantined = {_norm(q) for (q,) in db.execute('SELECT question FROM quarantined')} if 'quarantined' in names else set()
            for q, route, status, reason in db.execute('SELECT question, route, status, reason FROM outcomes'):
                tier = TIER_OF_ROUTE.get(route)
                if not tier or OUTAGE.search(str(reason or '')): continue
                if status == 'answered':
                    right = _norm(q) not in corrected and _norm(q) not in quarantined
                elif status in ('escalated', 'declined', 'internal_error', 'not_understood'):
                    right = False
                else:
                    continue
                rows.append({'question': q, 'tier': tier, 'right': right, 'source': 'outcomes'})
    return rows


def stack_paths():
    found = []
    for p in sorted((ROOT / 'artifacts').glob('*/stack/learning.sqlite3')) + sorted((ROOT / 'artifacts').glob('*/learning.sqlite3')):
        found.append(p.parent)
    main = Path('/Users/mikel/clinical-insights-local/artifacts')
    if not found and main.exists():
        found = [p.parent for p in sorted(main.glob('*/stack/learning.sqlite3'))]
    return found


def labels(*, results=None, traffic=None, stacks=None):
    rows = bench_labels(results) + traffic_labels(traffic) + outcome_labels(stacks if stacks is not None else stack_paths())
    latest = {}
    for r in rows:  # one label per (question, tier); a right answer wins over a tried-and-failed record of the same tier
        k = (_norm(r['question']), r['tier'])
        if k not in latest or (r['right'] and not latest[k]['right']) or r['source'] == 'bench': latest[k] = r
    return list(latest.values())


# -- model -----------------------------------------------------------------------------------------------------------
def _fit(rows, l2=0.05, steps=300, lr=0.5):
    w, b = {}, 0.0
    X = [features(r['question']) for r in rows]
    y = [1.0 if r['right'] else 0.0 for r in rows]
    n = len(rows)
    base = sum(y) / n
    b = math.log(max(base, 1e-3) / max(1 - base, 1e-3))
    for _ in range(steps):
        gw, gb = {}, 0.0
        for f, t in zip(X, y):
            p = 1 / (1 + math.exp(-max(-30, min(30, b + sum(w.get(k, 0.0) * v for k, v in f.items())))))
            d = (p - t) / n
            gb += d
            for k, v in f.items(): gw[k] = gw.get(k, 0.0) + d * v
        for k in set(gw) | set(w):
            w[k] = w.get(k, 0.0) - lr * (gw.get(k, 0.0) + l2 * w.get(k, 0.0))
        b -= lr * gb
    return {str(k): round(v, 5) for k, v in w.items() if abs(v) > 1e-4}, round(b, 5)


def train(rows):
    """-> model {'tiers': {tier: {'n', 'right', 'base', 'weights'?, 'bias'?, 'loo_accuracy'?}}, 'global', ...}"""
    by = {}
    for r in rows: by.setdefault(r['tier'], []).append(r)
    total = len(rows) or 1
    g = sum(r['right'] for r in rows) / total if rows else 0.5
    tiers = {}
    for tier, rs in by.items():
        k = sum(r['right'] for r in rs)
        rec = {'n': len(rs), 'right': k, 'base': round((k + PRIOR_STRENGTH * g) / (len(rs) + PRIOR_STRENGTH), 4),
               'sources': sorted({r['source'].split(':')[0] for r in rs})}
        if len(rs) >= MIN_LABELS and 0 < k < len(rs):
            rec['weights'], rec['bias'] = _fit(rs)
            # held-out estimate: 5-fold by question hash
            hits = 0
            for fold in range(5):
                test = [r for r in rs if int(hashlib.md5(_norm(r['question']).encode()).hexdigest(), 16) % 5 == fold]
                trn = [r for r in rs if r not in test]
                if not test or not trn or len({r['right'] for r in trn}) < 2: hits += sum((rec['base'] >= 0.5) == r['right'] for r in test); continue
                w, b = _fit(trn, steps=150)
                hits += sum(((1 / (1 + math.exp(-(b + _dot(w, features(r['question'])))))) >= 0.5) == r['right'] for r in test)
            rec['cv_accuracy'] = round(hits / len(rs), 4)
            rec['base_accuracy'] = round(max(k, len(rs) - k) / len(rs), 4)
        tiers[tier] = rec
    return {'version': VERSION, 'dim': DIM, 'trained_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'global': round(g, 4),
            'n_labels': len(rows), 'tiers': tiers}


def save(model, path=None):
    Path(path or MODEL_FILE).write_text(json.dumps(model, separators=(',', ':'), sort_keys=True) + '\n')
    _MODEL.clear()


_MODEL = {}


def load(path=None):
    path = Path(path or os.environ.get('AGERE_CASCADE_ROUTER_MODEL') or MODEL_FILE)
    try: key = (str(path), path.stat().st_mtime_ns)
    except OSError: return None
    if key not in _MODEL:
        try: _MODEL.clear(); _MODEL[key] = json.loads(path.read_text())
        except (OSError, ValueError): return None
    return _MODEL[key]


def dependent(model):
    """Tiers whose prediction depends on the question (a fitted model that beat the tier's base rate in cross-validation)."""
    return {t for t, rec in (model or {}).get('tiers', {}).items()
            if rec.get('weights') is not None and (rec.get('cv_accuracy') or 0) > (rec.get('base_accuracy') or 1)}


def fixed_tail():
    """Tiers that failed held-out calibration run last by design (proposers.TIERS): the router never moves them."""
    try:
        from .proposers import TIERS
        return {t for t, (_, calibration, _) in TIERS.items() if str(calibration).startswith('FAILED')}
    except Exception:  # noqa: BLE001
        return set()


def predict(question, model=None):
    """{tier: P(right)} for every tier the model knows (question-dependent when the tier has weights)."""
    model = model or load()
    if not model: return {}
    f = features(question)
    out = {}
    for tier, rec in model['tiers'].items():
        if tier in dependent(model):
            z = rec['bias'] + _dot(rec['weights'], f)
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            out[tier] = round((p * rec['n'] + PRIOR_STRENGTH * rec['base']) / (rec['n'] + PRIOR_STRENGTH), 4)
        else:
            out[tier] = rec['base']
    return out


def reorder(stack, question, order, *, model=None, front=None):
    """-> (order, receipt): same tiers, sorted by predicted success inside each System One block and cost class."""
    order = tuple(order)
    if os.environ.get('AGERE_CASCADE_ROUTER', 'on') == 'off': return order, None
    model = model or load()
    if not model: return order, None
    from .claim_verification import cost_class
    from .system_one_gate import PINNED_FIRST
    p = predict(question, model)
    movable = dependent(model) - fixed_tail()  # base rates alone never reorder the designed cascade; question signal does
    if front is None:  # the System One block: pinned tiers + the tiers its route moved first (Cascade passes it)
        front = len([t for t in order[:1] if t in PINNED_FIRST])
    blocks = [order[:front], order[front:]]
    out = []
    for block in blocks:
        pinned = [t for t in block if t in PINNED_FIRST]
        rest = [t for t in block if t not in PINNED_FIRST]
        slots = {}
        for i, t in enumerate(rest):
            if t in movable: slots.setdefault(cost_class(t), []).append(i)
        placed = list(rest)
        for cls, idx in slots.items():
            members = sorted((rest[i] for i in idx), key=lambda t: (-p[t], rest.index(t)))
            for i, t in zip(idx, members): placed[i] = t
        out += pinned + placed
    out = tuple(out)
    if sorted(out) != sorted(order): return order, {'version': VERSION, 'applied': False, 'error': 'not a permutation'}  # never fewer tiers
    moved = [t for t, u in zip(out, order) if t != u]
    return out, {'version': VERSION, 'applied': bool(moved), 'moved': len(moved), 'movable': sorted(movable & set(order)),
                 'predicted': {t: p[t] for t in order if t in p},
                 'model_labels': model.get('n_labels'), 'trained_at': model.get('trained_at')}


# -- traces (labels by outcome, going forward) -------------------------------------------------------------------------
def record_trace(question, result, verified, directory=None):
    """Append the cascade's proposer log with the accepted tier and whether its answer was verified."""
    if _evaluating(): return None
    from .learning_hooks import training_dir, interpretation_of, proposer_of
    interp = interpretation_of(result)
    log = interp.get('proposers') or []
    if not log: return None
    d = Path(directory) if directory else training_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f'cascade-traces-{time.strftime("%Y%m%d")}.jsonl'
    row = {'schema': VERSION, 'question': question, 'accepted': proposer_of(result), 'verified': bool(verified),
           'proposers': [{'proposer': e.get('proposer'), 'outcome': e.get('outcome'),
                          **({'detail': str(e.get('detail'))[:160]} if e.get('outcome') == 'error' else {})} for e in log][:40],
           'usage': interp.get('cascade_usage'), 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    with path.open('a') as f: f.write(json.dumps(row, separators=(',', ':'), default=str) + '\n')
    return str(path)
