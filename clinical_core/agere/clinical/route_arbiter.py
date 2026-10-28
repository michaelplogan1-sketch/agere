"""Route arbitration: every engine route and the native compiler plan are collected, then one is chosen on a free, deterministic score.

The engine suite used to answer first-match (engine_suite.ROUTE_ORDER), so an engine added later could silently take an older
engine's or a native route's question. Candidates here are every engine route that returns a plan plus the native route chain's own plan
(local_compiler._compile(engines=False)). Score of a candidate operation for a question:
  similarity   best IDF-weighted token cosine to native engine TEMPLATES and declared operation descriptions.
               Evaluation examples and legacy learned-template files are never runtime routing inputs.
  cue          small bonus for what the plan recognised, a penalty for what it left unexplained
  learned      the learned router's probability for the operation, only when a local encoder is bound (else lexical only)
The previous order's winner (first engine claim, else the native plan) keeps the question unless another candidate beats it by SWITCH_MARGIN.
When the top two score within TIE_MARGIN and System One is live (bound by the planner for a live stack) it picks between them; otherwise the
previous order's winner stands. The decision is a receipt in the plan info: candidates, scores, winner, tie-break used.
"""
import contextvars
import json
import math
import os
import re
from collections import Counter
from pathlib import Path

VERSION = 'route-arbiter/2'
SWITCH_MARGIN = 0.1  # a later candidate must beat the previous order's winner by this much to take the question
IDF_OP = 0.5          # weight of operation-level IDF (vs example-level) in the token weights
CENTROID = 0.5        # weight of the operation-centroid cosine (vs the best single example)
TIE_MARGIN = 0.05     # top two this close: System One (live only) breaks the tie
ROOT = Path(__file__).resolve().parents[3]
STOP = frozenset('a an the of for to in on at by with and or is are was were be our we us my me i you your it its this that these those '
                 'what which who whom whose how many much do does did can could should would will any all each per from as than then '
                 'there their they them have has had not no about over under into out up down show give tell list find get'.split())
_ENCODER = contextvars.ContextVar('agere_route_arbiter_encoder', default=None)
_LIVE = contextvars.ContextVar('agere_route_arbiter_live', default=None)  # (stack, ledger) when System One may be asked


class bind:
    """with bind(stack, ledger): -- the planner's stack supplies the local encoder (learned router) and, when live, System One."""
    def __init__(self, stack=None, ledger=None):
        self.encoder = getattr(getattr(stack, 'retriever', None), 'encoder', None)
        self.live = (stack, ledger) if getattr(stack, 'allow_live', False) else None

    def __enter__(self):
        self.tokens = (_ENCODER.set(self.encoder), _LIVE.set(self.live))
        return self

    def __exit__(self, *exc):
        _ENCODER.reset(self.tokens[0]); _LIVE.reset(self.tokens[1])


def tokens(text):
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9'.%-]*", re.sub(r'\{\w+\}', ' ', str(text).lower())):
        w = w.strip(".'-")
        if w in STOP or not w: continue
        out.append(w[:-1] if len(w) > 4 and w.endswith('s') and not w.endswith('ss') else w)
    return out


def normal(text):
    return ' '.join(tokens(text))


_EXAMPLES = {}


def _acceptance():
    """Retired corpus hook; preserve call compatibility without reading evaluation files."""
    return []


def _learned_templates():
    """Retired legacy-template hook; versioned native capabilities supply runtime examples."""
    return []


def examples():
    """op -> [(example question, source)] from the native capability registry only."""
    if _EXAMPLES: return _EXAMPLES
    from . import engine_suite
    got = {}
    for op, m in engine_suite.available().items():
        for t in getattr(m, 'TEMPLATES', ()): got.setdefault(op, []).append((t, 'template'))
    for op, m in engine_suite.available().items():
        for text in (m.PRODUCT.get('use'), m.CONTRACT.get('meaning')):
            if text: got.setdefault(op, []).append((text, 'description'))
    _EXAMPLES.update(got)
    return _EXAMPLES


_INDEX = {}


def _index():
    """Cached IDF table and example vectors over every operation's examples."""
    if _INDEX: return _INDEX
    docs = [(op, q, Counter(tokens(q))) for op, items in examples().items() for q, _ in items]
    per_op = {op: {w for o, _, c in docs if o == op for w in c} for op in examples()}
    df_op = Counter(w for ws in per_op.values() for w in ws)  # a word every operation uses says nothing
    df = Counter(w for _, _, c in docs for w in c)
    n, k = max(1, len(docs)), max(1, len(per_op))
    idf = {w: (1 - IDF_OP) * (math.log((1 + n) / (1 + d)) + 1.0) + IDF_OP * (math.log((1 + k) / (1 + df_op[w])) + 1.0) for w, d in df.items()}
    vec = lambda c: ({w: tf * idf.get(w, math.log(1 + n) + 1.0) for w, tf in c.items()})
    rows = []
    for op, q, c in docs:
        v = vec(c)
        rows.append((op, normal(q), v, math.sqrt(sum(x * x for x in v.values())) or 1.0))
    _INDEX.update(idf=idf, rows=rows, vec=vec, n=n)
    return _INDEX


def similarity(question, op, held_out=None):
    """Half best-example cosine, half operation-centroid cosine (the centroid keeps an operation with few examples competitive);
    held_out: that exact example text is ignored (the overlap test scores each example against the rest)."""
    ix = _index()
    v = ix['vec'](Counter(tokens(question)))
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    skip = normal(held_out) if held_out is not None else None
    best, total, count = 0.0, Counter(), 0
    for o, text, w, wn in ix['rows']:
        if o != op or text == skip: continue
        best = max(best, sum(x * w.get(k, 0.0) for k, x in v.items()) / (norm * wn))
        for k, x in w.items(): total[k] += x / wn
        count += 1
    if not count: return 0.0
    cn = math.sqrt(sum(x * x for x in total.values())) or 1.0
    centroid = sum(x * total.get(k, 0.0) for k, x in v.items()) / (norm * cn)
    return (1 - CENTROID) * best + CENTROID * centroid


def learned(question):
    """op -> probability from the learned router when a matching local encoder is bound; {} (lexical only) otherwise."""
    encoder = _ENCODER.get()
    if encoder is None or os.environ.get('AGERE_LEARNED_ROUTER', 'on') == 'off': return {}
    try:
        from . import learned_router as lr
        model = lr.load()
        if model is None or getattr(encoder, 'identity', None) != model['encoder_identity']: return {}
        p = lr.probabilities(model, lr.features([question], encoder))[0]
    except Exception:  # noqa: BLE001 - no encoder / weights / cues drifted: lexical score only
        return {}
    return {lr.LABELS[i]: float(p[i]) for i in range(len(lr.LABELS))}


def native_op(graph):
    nodes = (graph or {}).get('nodes') or []
    return nodes[-1]['operation'] if nodes else None


def candidates(question, profile, as_of, native=True):
    """Every claim, in the previous order: engine routes in ROUTE_ORDER, then the native chain's plan."""
    from . import engine_suite
    out = []
    avail = engine_suite.available()
    for op in engine_suite.ROUTE_ORDER:
        m = avail.get(op)
        if m is None or not hasattr(m, 'route'): continue
        try:
            plan = m.route(question, profile, as_of)
        except (ValueError, KeyError, TypeError):
            continue  # a route that cannot parse declines
        if plan is not None: out.append({'op': native_op(plan[0]) or op, 'source': f'engine:{op}', 'plan': plan})
    if native and out:  # without an engine claim the chain continues natively on its own; the plan is only built here to compete
        from . import local_compiler
        try:
            graph, info = local_compiler._compile(question, profile, as_of, engines=False)
        except (ValueError, KeyError, TypeError):
            graph, info = None, None  # a profile the native chain cannot read: the engine claims decide
        if graph is not None and graph.get('nodes'): out.append({'op': native_op(graph), 'source': 'native', 'plan': (graph, info)})  # a refusal never competes
    return out


def score(question, cand, probs, held_out=None):
    graph, info = cand['plan']
    sim = similarity(question, cand['op'], held_out)
    cue = 0.02 * min(len((info or {}).get('recognised') or []), 4) - 0.05 * len((info or {}).get('unexplained') or [])
    numbers = set(re.findall(r'\d+(?:\.\d+)?', re.sub(r'(?<=\d),(?=\d{3})', '', question)))
    if numbers:  # a plan that carries the question's own numbers (12-hour, 30 minutes, 7 days) read the question
        carried = json.dumps(graph, default=str)
        cue += 0.1 * sum(1 for n in numbers if re.search(r'(?<![\d.])' + re.escape(n) + r'(?![\d])', carried)) / len(numbers)
    prob = probs.get(cand['op'], 0.0)
    return {'similarity': round(sim, 4), 'cue': round(cue, 4), 'learned': round(prob, 4) if probs else None,
            'score': round(sim + cue + (0.5 * prob if probs else 0.0), 4)}


def system_one_pick(question, pair):
    """Live only: System One chooses between the top two operations. -> index into pair, or None (not live / errs / unsure)."""
    live = _LIVE.get()
    if live is None or os.environ.get('AGERE_ROUTE_ARBITER_S1', 'on') == 'off': return None
    stack, ledger = live
    try:
        from . import system_one_gate as gate
        if not gate.ready(stack, 'AGERE_ROUTE_ARBITER_S1'): return None
        from .capability_registry import PRODUCT
        opts = {c['op']: str((PRODUCT.get(c['op']) or {}).get('use') or c['op']) for c in pair}
        if len(opts) < 2: return None
        out = gate.s1().decide(f'Which analysis best answers this question for a population-health analytics system? Question: {question}',
                               opts, [], as_of=gate.as_of(stack), task_key='route_arbiter', ledger=ledger, allow_live=True)
        label = out.get('label')
        return next((i for i, c in enumerate(pair) if c['op'] == label), None)
    except Exception:  # noqa: BLE001 - the previous order's winner stands
        return None


def decide(question, cands, held_out=None):
    """-> (winner candidate, receipt). Deterministic unless System One is live and the top two are tied."""
    probs = learned(question)
    for c in cands: c['scores'] = score(question, c, probs, held_out)
    incumbent = cands[0]
    ranked = sorted(cands, key=lambda c: (-c['scores']['score'], cands.index(c)))
    top = ranked[0]
    winner, tie = incumbent, 'previous order (no override)'
    if top is not incumbent and top['scores']['score'] - incumbent['scores']['score'] >= SWITCH_MARGIN:
        winner, tie = top, f"score beat the previous order's winner by >= {SWITCH_MARGIN}"
    elif len(ranked) > 1 and ranked[0]['scores']['score'] - ranked[1]['scores']['score'] < TIE_MARGIN and ranked[0]['op'] != ranked[1]['op']:
        pick = system_one_pick(question, ranked[:2])
        if pick is not None: winner, tie = ranked[:2][pick], 'system_one'
        else: tie = 'previous order (top two within margin; System One not live or unsure)'
    receipt = {'arbiter': VERSION, 'candidates': [{'operation': c['op'], 'source': c['source'], **c['scores']} for c in cands],
               'winner': {'operation': winner['op'], 'source': winner['source']}, 'tie_break': tie,
               'learned_router': 'used' if probs else 'lexical only (no matching local encoder)'}
    return winner, receipt


def arbitrate(question, profile, as_of, native=True, held_out=None):
    """-> (graph, info) of the winning claim, or None when nothing claims the question (engine routes only: native decides on its own)."""
    cands = candidates(question, profile, as_of, native=native)
    if not cands: return None
    if len(cands) == 1 and cands[0]['source'] != 'native': return cands[0]['plan'][0], {**cands[0]['plan'][1], 'arbitration': {
        'arbiter': VERSION, 'candidates': [{'operation': cands[0]['op'], 'source': cands[0]['source']}], 'winner': {
            'operation': cands[0]['op'], 'source': cands[0]['source']}, 'tie_break': 'sole claim'}}
    winner, receipt = decide(question, cands, held_out)
    graph, info = winner['plan']
    return graph, {**info, 'arbitration': receipt}
