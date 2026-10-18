"""Agere "together": the fast answer path and an independent semantic analyst, reconciled into one answer.

Measured on heldout10, Agere's fast path and an LLM over Agere's own semantic layer (bench frontier_semantic) are right on
different questions; agreement between them is a strong correctness signal and one reconcile turn resolves most
disagreements (bench/reconcile.py). This module makes that combination the product's answer path.

Flow (Stack.compose -> compose_together when AGERE_TOGETHER is on (default) and the stack allows live models; planning
'auto' only):
1. At question start the semantic analyst (semantic_analyst.analyze over <build>/stack/semantic.sqlite3, model
   AGERE_SEMANTIC_MODEL, default claude-haiku-5-5) starts in a worker thread with its own ledger connection and its own
   read-only sqlite connection. Its calls go through the ledgered context_student.send, so cost lands in the provider
   budget ledger and an identical request replays from the ledger cache.
2. The fast path (Stack._compose) runs unchanged on the request thread.
3. The answer waits for the analyst until AGERE_TOGETHER_BUDGET_S (default 90 s) after the question started. The service's
   global stack lock (stack.answer_lock) is released while waiting and while reconciling, and re-acquired before
   returning (handle_compose's logging and learning writes run under it).
4. Decision:
   - analyst timed out / failed / unavailable -> the fast answer ships unchanged;
   - fast path errored but the analyst answered -> the analyst's answer, confidence unverified;
   - an analytic engine answer (survival, risk_adjustment, target_trial, forecast, program_effect, trajectory, demand,
     monitor) is never replaced; its confidence is lowered to low only when the analyst gives a different answer. The
     analyst saying unsupported is only a note: it has no engines, so it cannot compute these;
   - both unsupported -> the abstention ships;
   - same canonical answer -> confidence verified, evidence 'two_independent_paths_agree';
   - different answers -> one reconcile turn (AGERE_RECONCILE_MODEL, default claude-opus-5-5; blind order; both answers
     with their SQL/grounding; at most 4 SQL checks; the question's stated definitions win over governed defaults),
     bounded by AGERE_RECONCILE_BUDGET_S (60 s). Pick 1/2 -> that answer, unverified; 'neither' -> the reconciler's
     corrected answer, unverified; abstain / unresolved / timeout -> the fast answer as partial, confidence low, both
     readings in grounding.
   Certified cascade (cascade.py): a specialist whose route has a Learn-then-Test certificate in
   config/agere/cascade_thresholds.json claims the question only when its score clears the certified threshold (accepted
   answers then have error <= alpha with confidence 1 - delta); below it the question goes on to the analysts. Routes
   without a certificate behave as before and their receipt says 'uncalibrated'.
5. Receipts: result['together'] (decision, winner, reason, model / cost / calls / seconds per part). Analyst and reconcile
   calls are added to planner_receipt steps, planning_economics and new_paid_api_calls (and planner_receipt's count), so
   the bench adapter's cost completeness holds. An analyst still running at the deadline marks the cost unknown.
   A certified specialist answer adds certified {tier, alpha, delta, bound, n_calibration, threshold, score, statement} to
   the result, its answer and planner_receipt; together['certificate'] records the check either way.

AGERE_TOGETHER=off restores the previous behavior exactly (Stack.compose calls Stack._compose only).
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from pathlib import Path

from . import semantic_analyst

VERSION = 'agere-together/1'
# Engines whose output the analyst cannot compute from the semantic layer (models, curves, simulations). 'demand' and
# 'monitor' joined 2026-10-17: forecast questions answered by the demand engine were lost to an analyst saying
# "no forecasting model" under the analyst disagreement policy.
ENGINE_OPERATIONS = frozenset({'survival', 'risk_adjustment', 'target_trial', 'forecast', 'program_effect', 'trajectory',
                               'demand', 'monitor'})
DEFAULT_HYBRID_BUDGET_S = 240
DEFAULT_SEMANTIC_MODEL = 'claude-haiku-5-5'   # semantic-analyst bake-off winner
DEFAULT_RECONCILE_MODEL = 'claude-opus-5-5'
DEFAULT_BUDGET_S = 90.0
DEFAULT_RECONCILE_BUDGET_S = 60.0
SEND_TIMEOUT_S = 180
# The documented semantic model is ~141K characters; the tool loop adds at most 8 tool results of <= 60K characters.
REQUEST_MAX_BYTES = 1_200_000
CONTRACT_KEYS = ('status', 'answer_type', 'count', 'patient_ids', 'value', 'table', 'unknown_count', 'missing', 'grounding', 'readings')
STATUSES = ('answered', 'partial', 'unsupported', 'grounding_failed', 'clarify')
TRANSPORT = None   # urlopen-compatible transport for context_student.send; tests install a fake (no paid calls)

_POOL = None
_POOL_LOCK = threading.Lock()
_WAREHOUSES = {}
_WAREHOUSE_LOCK = threading.Lock()


def enabled():
    return os.environ.get('AGERE_TOGETHER', 'on').strip().lower() not in ('off', '0', 'false', 'no')


def _seconds(name, default):
    try:
        v = float(os.environ.get(name, default))
    except ValueError:
        return default
    return v if math.isfinite(v) and v >= 0 else default


def budget_s():
    # three runs plus a possible tie-break need longer than one analyst
    return _seconds('AGERE_TOGETHER_BUDGET_S', DEFAULT_HYBRID_BUDGET_S if hybrid_on() else DEFAULT_BUDGET_S)


def reconcile_budget_s():
    return _seconds('AGERE_RECONCILE_BUDGET_S', DEFAULT_RECONCILE_BUDGET_S)


def semantic_model():
    return os.environ.get('AGERE_SEMANTIC_MODEL') or DEFAULT_SEMANTIC_MODEL


def reconcile_model():
    return os.environ.get('AGERE_RECONCILE_MODEL') or DEFAULT_RECONCILE_MODEL


def _pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix='agere-together')
        return _POOL


def warehouse(path):
    """One SemanticWarehouse per semantic.sqlite3 file version; it hands each thread its own read-only connection."""
    path = Path(path).resolve()
    key = (str(path), path.stat().st_mtime_ns)
    with _WAREHOUSE_LOCK:
        wh = _WAREHOUSES.get(key)
        if wh is None:
            for old in [k for k in _WAREHOUSES if k[0] == key[0]]:
                _WAREHOUSES.pop(old).close()
            wh = _WAREHOUSES[key] = semantic_analyst.SemanticWarehouse(path)
        return wh


def _ledger_path(stack):
    explicit = getattr(stack, 'ledger_path', None)
    if explicit:
        return Path(explicit)
    from .stack_runtime import ROOT
    return ROOT / 'artifacts/agere-full/provider-budget.sqlite3'


def _ledgered_send(stack, ledger, receipts):
    """send(body) for the shared tool loop through the ledgered context_student.send (budget ledger, response cache,
    failure receipts). -> (response, cost_usd or None, cache_hit)."""
    from .context_student import send as ledgered

    def send(body):
        raw, receipt = ledgered(body, 'claude', ledger, Path(stack.receipts), parse=lambda r: r, timeout=SEND_TIMEOUT_S,
                                transport=TRANSPORT, max_bytes=REQUEST_MAX_BYTES)
        receipts.append(receipt)
        if receipt.get('cache_hit'):
            return raw, 0.0, True
        return raw, (receipt.get('cost_estimate') or {}).get('estimated_usd'), False
    return send


@contextmanager
def _own_ledger(stack):
    from ..gateway import BudgetLedger
    ledger = BudgetLedger(_ledger_path(stack))
    try:
        yield ledger
    finally:
        ledger.close()


@contextmanager
def _released(stack):
    """Release the service's global stack lock (stack.answer_lock) while this request only waits on other threads;
    re-acquire it before returning to handle_compose (its logging and learning writes run under the lock)."""
    lock = getattr(stack, 'answer_lock', None)
    released = False
    if lock is not None:
        try:
            lock.release()
            released = True
        except RuntimeError:  # not held by this thread
            released = False
    try:
        yield
    finally:
        if released:
            lock.acquire()


# --- the analyst ------------------------------------------------------------------------------------------------------------

def deferral_on():
    return os.environ.get('AGERE_DEFER_FAST', 'on').strip().lower() != 'off'


SPECIALIST_ROUTES = frozenset({'local_compiler', 'semantic_cache', 'exact_question_cache', 'learned_verified_sql',
                               'metric_router', 'operator_router', 'operator_router_requires_input',
                               'compact_retrieval_typed_router', 'local_model_rewrite_compiled', 'cohort_family_bottled',
                               'cohort_family'})  # cohort_families.py: code-rendered SQL for the stated definition


def engines_defer_on_definitions():
    """AGERE_ENGINES_DEFER_ON_DEFINITIONS (default on): on a question that states its own definition, analytic engines do
    not skip the analysts, and an analyst answer that executes the definition wins over a differing engine answer."""
    return os.environ.get('AGERE_ENGINES_DEFER_ON_DEFINITIONS', 'on').strip().lower() != 'off'


def specialists_first():
    """AGERE_SPECIALISTS_FIRST (default on): the compiler, caches, governed metrics, engines and the typed decider answer
    first; the language-model analysts start only when none of them claims the question."""
    return os.environ.get('AGERE_SPECIALISTS_FIRST', 'on').strip().lower() != 'off'


_DEFINITION = __import__('re').compile(r'\bdefinition\s*:|\bdefined as\b|\bcounts? as\b|\bmeans\b.{0,80}\b(within|days?|months?|code|icd|cpt)\b', __import__('re').I | __import__('re').S)


def _inline_definition(question):
    """The question carries its own explicit definition (\"Definition: ...\", \"defined as\", criteria with windows/codes)."""
    return bool(_DEFINITION.search(question or ''))


def _fast_route(fast):
    fast = fast or {}
    return (fast.get('planner_receipt') or {}).get('route') or fast.get('route')


def _specialist_answer(fast, answer_type, question=''):
    """A specialist claimed the question: its route is a governed/compiled/cached/engine route and it produced a
    non-empty answer of the requested shape (an unchecked zero or a missing patient list goes on to the analysts)."""
    route = _fast_route(fast)
    if route not in SPECIALIST_ROUTES:
        return False
    if route == 'metric_router' and _inline_definition(question):
        # The question states its own definition: an organization's standard metric answers a different question, so it
        # does not claim this one (the stated definition is followed by the path that reads it).
        return False
    answer = (fast or {}).get('answer')
    if not isinstance(answer, dict) or _empty_answer(answer):
        return False
    if answer_type == 'patient_set' and not isinstance(answer.get('patient_ids'), list):
        return False
    return True


def _certify_claim(fast):
    """Certified cascade (cascade.py): the claiming specialist's certificate, or None when its route is not a calibrated
    tier. cert['accept'] False = calibrated and below its certified threshold: the question goes on to the analysts.
    Missing / uncertifiable / stale records accept (today's behaviour) with status 'uncalibrated'."""
    try:
        from . import cascade
        return cascade.certify_route((fast or {}).get('planner_receipt') or {})
    except Exception:  # noqa: BLE001 - certification bookkeeping never fails an answer
        return None


def _stamp_certificate(result, cert, together):
    """Receipts gain certified {tier, alpha, delta, bound, n_calibration, ...} when a certified tier answered."""
    from . import cascade
    together['certificate'] = {k: cert.get(k) for k in ('tier', 'status', 'score', 'threshold', 'alpha', 'delta', 'bound',
                                                        'n_calibration', 'reason', 'statement') if cert.get(k) is not None}
    block = cascade.receipt_block(cert)
    if block is None:
        return result
    receipt = dict(result.get('planner_receipt') or {})
    receipt['certified'] = block
    answer = result.get('answer')
    if isinstance(answer, dict):
        answer = {**answer, 'certified': block}
    together['certified'] = block
    return {**result, 'answer': answer, 'planner_receipt': receipt, 'certified': block}


def _usable(analyst, state):
    """The analysts returned an answer or a stated decline (clarify / unsupported)."""
    return state == 'done' and isinstance((analyst or {}).get('answer'), dict) and not (analyst or {}).get('error')


def _compiled_engine(stack, question, as_of):
    """The analytic engine(s) Agere's own compiler routes this question to ($0, milliseconds), else []. An engine answer is
    never replaced by the analysts, so they are not started for it."""
    if os.environ.get('AGERE_SKIP_ANALYST_FOR_ENGINES', 'on').strip().lower() == 'off':
        return []
    try:
        from .economical_planner import local_plan
        from .local_compiler import compile as compile_locally
        from .source_profile import load as load_profile
        graph, _ = local_plan(question, as_of)
        if not graph:
            graph, _ = compile_locally(question, load_profile(stack.path), as_of)
        nodes = (graph or {}).get('nodes') or []
        return sorted({n.get('operation') for n in nodes if isinstance(n, dict)} & ENGINE_OPERATIONS)
    except Exception:  # noqa: BLE001 - detection never blocks the answer
        return []


def hybrid_on():
    return os.environ.get('AGERE_HYBRID', 'on').strip().lower() != 'off'


def _hybrid_contract(h):
    """The hybrid core's executed answer as the shared answer contract (None when it abstained)."""
    ans = h.get('answer')
    decline = h.get('decline') or {}
    if ans is None and decline.get('kind') == 'unsupported':
        return {'status': 'unsupported', 'answer_type': 'unsupported', 'count': None, 'patient_ids': None, 'value': None,
                'table': None, 'unknown_count': 0, 'missing': decline.get('missing') or [], 'grounding': [h['interpretation']]}
    if ans is None and decline.get('kind') == 'clarify' and decline.get('readings'):
        return {'status': 'clarify', 'answer_type': h.get('answer_type'), 'count': None, 'patient_ids': None, 'value': None,
                'table': None, 'unknown_count': 0, 'missing': [], 'readings': decline['readings'],
                'grounding': [h['interpretation'], 'The independent analysts found more than one defensible reading.']}
    if ans is None:
        return None
    at = h['answer_type']
    out = {'status': 'answered', 'answer_type': at, 'count': None, 'patient_ids': None, 'value': None, 'table': None,
           'unknown_count': 0, 'missing': [], 'grounding': [h['interpretation']]}
    if at in ('count', 'value'):
        out[at] = ans[at]
    elif at == 'patient_set':
        out['patient_ids'] = sorted({str(p) for p in ans['patient_ids']})
        out['count'] = len(out['patient_ids'])
    else:
        contracts = [c for c in (h.get('contracts') or {}).values() if c]
        keys = list((contracts[0] if contracts else {}).get('group_by') or [])
        out['table'] = [{**{(keys[i] if i < len(keys) else f'key{i + 1}'): k for i, k in enumerate(r['keys'])},
                         'value': r['value']} for r in ans['table']]
    return out


def _run_hybrid(stack, question, as_of, semantic_path, answer_type=None):
    """The hybrid core (clinical_core/agere/hybrid/live.py) in the analyst slot: three independent runs, executed and
    checked, voted, tie-broken by a stronger model. Same result shape as _run_analyst."""
    from ..hybrid import live
    started, receipts = time.perf_counter(), []
    wh = warehouse(semantic_path)

    @contextmanager
    def send_factory():
        with _own_ledger(stack) as ledger:
            yield _ledgered_send(stack, ledger, receipts)

    # A workspace stack (onboarding/workspace_stack.py) may carry its own sender and analyst docs; the service Stack has neither.
    own = getattr(stack, 'send_factory', None)
    h = live.answer(question, as_of or wh.as_of, wh, own or send_factory, docs_dir=getattr(stack, 'docs_dir', None),
                    answer_type=answer_type)
    answer = _hybrid_contract(h)
    tool_calls = [{'sql': r.get('sql')} for r in h['runs'].values() if r.get('sql')]
    return {'answer': answer, 'model': h['models']['analyst'], 'estimated_cost_usd': h['cost_usd'],
            'new_paid_api_calls': h['new_paid_api_calls'], 'tool_calls': tool_calls,
            'error': None if answer is not None else 'hybrid abstained: ' + h['interpretation'],
            'hybrid': {k: h.get(k) for k in ('decision', 'agree', 'passing', 'interpretation', 'runs', 'models', 'errors', 'decline',
                                             'readings_flagged')},
            'receipts': receipts, 'seconds': round(time.perf_counter() - started, 3)}


def _run_analyst(stack, question, as_of, semantic_path, model, answer_type=None):
    if hybrid_on():
        return _run_hybrid(stack, question, as_of, semantic_path, answer_type)
    started, receipts = time.perf_counter(), []
    wh = warehouse(semantic_path)
    own = getattr(stack, 'send_factory', None)  # a workspace stack's own sender (see _run_hybrid)
    if own is not None:
        with own() as send:
            result = semantic_analyst.analyze(question, as_of or wh.as_of, wh, send=send, model=model, format_retries=1)
    else:
        with _own_ledger(stack) as ledger:
            result = semantic_analyst.analyze(question, as_of or wh.as_of, wh, send=_ledgered_send(stack, ledger, receipts),
                                              model=model, format_retries=1)  # build log step 12
    result.pop('example_ids', None)
    result.update(receipts=receipts, seconds=round(time.perf_counter() - started, 3))
    return result


def _snapshot_as_of(stack):
    try:
        return stack.retriever.meta['snapshot']['as_of']
    except (AttributeError, KeyError, TypeError):
        return None


def _wait(future, deadline):
    """-> ('done', result) | ('timed_out', None) | ('failed', error)."""
    try:
        return 'done', future.result(timeout=max(0.0, deadline - time.perf_counter()))
    except FutureTimeout:
        return 'timed_out', None
    except Exception as exc:  # noqa: BLE001 - a failed analyst never fails the answer
        return 'failed', f'{type(exc).__name__}: {exc}'[:300]


# --- answer helpers ---------------------------------------------------------------------------------------------------------

def _contract(answer):
    """The analyst's / reconciler's answer object reduced to the answer contract, lightly normalized."""
    if not isinstance(answer, dict):
        return None
    out = {k: answer.get(k) for k in CONTRACT_KEYS}
    if out['status'] not in STATUSES:
        return None
    out['missing'] = list(out.get('missing') or [])
    out['grounding'] = list(out.get('grounding') or [])
    out['unknown_count'] = out.get('unknown_count') or 0
    if out.get('answer_type') == 'patient_set' and isinstance(out.get('patient_ids'), list):
        out['patient_ids'] = sorted({str(p) for p in out['patient_ids']})
        if out.get('count') is None:
            out['count'] = len(out['patient_ids'])
    return out


def _preview(answer):
    if not isinstance(answer, dict):
        return None
    out = {k: answer.get(k) for k in ('status', 'answer_type', 'count', 'value') if answer.get(k) is not None}
    if isinstance(answer.get('patient_ids'), list):
        out['patient_ids'] = answer['patient_ids'][:50]
        out['patient_ids_total'] = len(answer['patient_ids'])
    if isinstance(answer.get('table'), list):
        out['table'] = answer['table'][:20]
        out['table_rows'] = len(answer['table'])
    return out


def _fast_sql(answer, receipt):
    sql = []
    for source in (answer or {}, (receipt or {}).get('hosted_sql') or {}):
        q = source.get('query') if isinstance(source, dict) else None
        if isinstance(q, str) and q.strip():
            sql.append(q)
    return sql


def _fast_row(result):
    answer = (result or {}).get('answer') or {}
    view = {k: answer.get(k) for k in CONTRACT_KEYS}
    for k in ('computed', 'interpretation', 'metric'):
        if answer.get(k) is not None:
            view[k] = answer[k]
    return {'answer': view, 'tool_calls': [{'sql': s} for s in _fast_sql(answer, (result or {}).get('planner_receipt'))]}


def _engine(result):
    nodes = ((result or {}).get('graph') or {}).get('nodes') or []
    return sorted({n.get('operation') for n in nodes if isinstance(n, dict)} & ENGINE_OPERATIONS)


def _reading(label, answer, sql):
    return {'severity': 'warning', 'source': 'together', 'reading': label,
            'detail': f'{label} reading: ' + json.dumps(_preview(answer), default=str)[:400],
            'answer': _preview(answer), 'sql': (sql or [])[-3:]}


def _confidence(answer, level, reason, **detail):
    from .metric_router import set_confidence
    prior = {k: v for k, v in (answer.get('confidence_detail') or {}).items() if k in ('gates', 'rule', 'level', 'basis')}
    set_confidence(answer, level, reason, source='together', together=VERSION,
                   **({'prior': prior} if prior else {}), **detail)


def _note(answer, message, severity='info'):
    from .answer_checks import _add
    _add(answer, 'QC-TOGETHER', 'FM-13', message[:500], severity)


# --- composition ------------------------------------------------------------------------------------------------------------

def _part(result, receipts_key='receipts'):
    if not isinstance(result, dict):
        return None
    return {'model': result.get('model'), 'cost_usd': result.get('estimated_cost_usd'),
            'new_paid_api_calls': result.get('new_paid_api_calls', 0), 'cache_hits': result.get('cache_hits', 0),
            'tool_calls': len(result.get('tool_calls') or []), 'seconds': result.get('seconds'),
            'error': result.get('error'), 'sql': [c.get('sql') for c in result.get('tool_calls') or []][-3:]}


def _account(result, analyst, analyst_state, rec):
    """Add the analyst / reconcile calls to planner_receipt steps, planning_economics and new_paid_api_calls."""
    if analyst is None and analyst_state != 'timed_out' and rec is None:
        return result  # nothing ran (no semantic layer): the fast result's accounting is complete as it is
    receipt = result.get('planner_receipt')
    receipt = dict(receipt) if isinstance(receipt, dict) else {'steps': []}
    steps = list(receipt.get('steps') or [])
    added = 0
    if analyst is not None:
        step = {'stage': 'together_semantic_analyst', 'receipt': {'stages': list(analyst.get('receipts') or [])}}
        if analyst.get('estimated_cost_usd') is None:
            step['cost_unknown'] = True
        steps.append(step)
        added += analyst.get('new_paid_api_calls') or 0
    elif analyst_state == 'timed_out':
        # Still running in the background: its calls land in the ledger (and its cache) but are not known here.
        steps.append({'stage': 'together_semantic_analyst', 'receipt': {}, 'cost_unknown': True,
                      'note': 'analyst still running at the deadline'})
    if rec is not None and (rec.get('receipts') or rec.get('timed_out') or (rec.get('result') or {}).get('new_paid_api_calls')):
        # (the analyst disagreement policy makes no reconcile call, so it adds no step)
        r = rec.get('result') or {}
        step = {'stage': 'together_reconcile', 'receipt': {'stages': list(rec.get('receipts') or [])}}
        if rec.get('timed_out') or r.get('estimated_cost_usd') is None:
            step['cost_unknown'] = True
        steps.append(step)
        added += r.get('new_paid_api_calls') or 0
    receipt['steps'] = steps
    receipt['new_paid_api_calls'] = (receipt.get('new_paid_api_calls') or 0) + added
    from .economical_planner import economics
    return {**result, 'planner_receipt': receipt, 'planning_economics': economics(receipt),
            'new_paid_api_calls': (result.get('new_paid_api_calls') or 0) + added}


def _reconcile(stack, question, as_of, fast, analyst, semantic_path):
    """One reconcile turn in a worker thread, bounded by AGERE_RECONCILE_BUDGET_S. -> dict (picked, reason, answer, ...)."""
    model = reconcile_model()

    def run():
        receipts, started = [], time.perf_counter()
        wh = warehouse(semantic_path)
        with _own_ledger(stack) as ledger:
            out = semantic_analyst.reconcile(question, as_of or wh.as_of, _fast_row(fast),
                                             {'answer': analyst.get('answer'), 'tool_calls': analyst.get('tool_calls')},
                                             wh, send=_ledgered_send(stack, ledger, receipts), model=model,
                                             seed_key=f'together:{question}', labels=('fast', 'analyst'))
        out.update(receipts=receipts, seconds=round(time.perf_counter() - started, 3))
        out['result']['seconds'] = out['seconds']
        return out

    budget = reconcile_budget_s()
    state, value = _wait(_pool().submit(run), time.perf_counter() + budget)
    if state == 'done':
        return value
    return {'picked': 'abstain', 'reason': 'reconcile timed out' if state == 'timed_out' else value, 'answer': None,
            'timed_out': state == 'timed_out', 'reconcile_error': None if state == 'timed_out' else value,
            'result': {'model': model, 'estimated_cost_usd': None, 'new_paid_api_calls': 0, 'tool_calls': []},
            'receipts': []}


def _empty_answer(a):
    """A zero count/value or an empty patient set/table, answered as final."""
    if not isinstance(a, dict) or a.get('status') != 'answered':
        return False
    t = a.get('answer_type')
    if t in ('count', 'value'):
        return a.get(t) == 0
    if t == 'patient_set':
        return not a.get('patient_ids')
    if t == 'table':
        return not a.get('table')
    return False


def decide(stack, question, fast, fast_error, analyst, analyst_state, *, as_of=None, semantic_path=None, answer_type=None):
    """Combine the fast result and the analyst result. -> (result, together receipt, reconcile dict or None).
    Raises fast_error when the fast path failed and the analyst has no answer."""
    a_answer = _contract((analyst or {}).get('answer')) if analyst_state == 'done' and not (analyst or {}).get('error') else None
    together = {'version': VERSION, 'analyst_state': 'failed' if analyst_state == 'done' and a_answer is None else analyst_state,
                'budget_s': budget_s(), 'reconcile_budget_s': reconcile_budget_s(),
                # Both component answers, so one run can be scored as fast path alone, analyst alone and together.
                'fast_answer': (fast or {}).get('answer') if fast_error is None else None, 'analyst_answer': a_answer}
    rec = None
    if fast_error is not None:
        if a_answer is None:
            raise fast_error
        out = dict(a_answer)
        _confidence(out, 'unverified', 'Fast path failed; semantic analyst answer, not cross-checked',
                    evidence='semantic_analyst_only')
        _note(out, f'The fast path failed ({type(fast_error).__name__}); this is the semantic analyst\'s answer.')
        result = {'answer': out, 'question': question, 'route': 'together_semantic_analyst',
                  'planner_receipt': {'route': 'together_semantic_analyst', 'steps': [], 'new_paid_api_calls': 0,
                                      'fast_path_error': f'{type(fast_error).__name__}: {fast_error}'[:400]},
                  'new_paid_api_calls': 0, 'synthetic': True, 'clinical_execution_authorized': False}
        together.update(decision='fast_failed_analyst_answered', winner='analyst',
                        reason=f'fast path error: {type(fast_error).__name__}: {fast_error}'[:300])
        return result, together, rec
    f_answer = fast.get('answer') if isinstance(fast.get('answer'), dict) else None
    if answer_type == 'patient_set' and a_answer is not None and isinstance(a_answer.get('patient_ids'), list) \
            and not isinstance((f_answer or {}).get('patient_ids'), list) or (
            answer_type == 'patient_set' and a_answer is not None and isinstance(a_answer.get('patient_ids'), list) and _engine(fast)):
        # A patient list was asked for: the analysts' executed definition is the list; an engine worklist (ranked, capped)
        # or a bare count is kept only as a reading.
        out = dict(a_answer)
        if f_answer:
            out['grounding'] = list(out.get('grounding') or []) + [_reading('Agere fast path', f_answer, _fast_sql(f_answer, fast.get('planner_receipt')))]
        _confidence(out, 'unverified', 'Patient list from the independent analysts\' executed definition', evidence='independent_analysts_list')
        together.update(decision='analysts_answered_list', winner='analyst', reason='patient list requested')
        return {**fast, 'answer': out}, together, rec
    if fast.get('route') == 'deferred_to_analyst' and a_answer is not None:
        # The fast path deferred (no paid second ladder): the independent analysts' executed answer is the answer.
        out = dict(a_answer)
        h = (analyst or {}).get('hybrid') or {}
        label = 'clarify' if out.get('status') == 'clarify' else 'analysts_answered'
        _confidence(out, 'low' if label == 'clarify' else 'unverified',
                    f"Independent analysts ({h.get('interpretation') or 'hybrid core'}); the fast path deferred to them",
                    evidence='independent_analysts')
        together.update(decision=label, winner='analyst', reason=h.get('interpretation') or 'fast path deferred')
        return {**fast, 'answer': out, 'route': 'together_semantic_analyst'}, together, rec
    if a_answer is None or f_answer is None:
        reason = {'timed_out': 'analyst did not finish within the budget', 'unavailable': 'semantic layer not built',
                  'skipped_engine': 'analytic engine route: the analysts were not started (an engine answer is never replaced)',
                  'skipped_specialist': 'a specialist (compiler, cache, governed metric, engine or typed decider) answered; '
                                        'the analysts were not needed',
                  'failed': (analyst or {}).get('error') or 'analyst returned no valid answer'}.get(
                      together['analyst_state'], 'analyst returned no valid answer')
        if f_answer is None:
            reason = 'fast result has no answer object'
        elif (_empty_answer(f_answer) and together['analyst_state'] not in ('skipped_engine', 'skipped_specialist')
              and os.environ.get('AGERE_UNCHECKED_ZERO', 'abstain') == 'abstain'):
            # Build log step 10: a zero/empty result the analyst could not check would otherwise ship as a confident 0
            # (dev RX-10). Zero is the answer a missed source produces, so it is withheld unless confirmed.
            out = dict(f_answer)
            out['status'] = 'partial'
            _confidence(out, 'low', 'Unchecked zero: the independent analyst produced no answer to confirm it',
                        evidence='unchecked_zero')
            _note(out, 'Withheld as final: the fast path found nothing and the semantic analyst could not confirm it ('
                  + str(reason) + ').', 'warning')
            together.update(decision='unchecked_zero', winner='none', reason=str(reason)[:300])
            return {**fast, 'answer': out}, together, rec
        together.update(decision='fast_unchanged', winner='fast', reason=str(reason)[:300])
        return fast, together, rec
    out = dict(f_answer)
    fk, ak = semantic_analyst.canonical(f_answer), semantic_analyst.canonical(a_answer)
    engines = _engine(fast)
    if engines:
        if a_answer.get('status') in ('unsupported', 'clarify'):
            # The analyst works over the semantic layer only; "no forecasting model" is a capability gap, not a check.
            _note(out, f'Analytic engine ({", ".join(engines)}) answer; the semantic analyst cannot compute this '
                  'from the semantic layer, so it is not cross-checked.')
            together.update(decision='engine_only', winner='fast', reason='the semantic analyst has no engine for this')
        elif fk != ak and _inline_definition(question) and engines_defer_on_definitions():
            # The question states its own definition and the analysts executed it: their answer wins over the engine's
            # own reading (the engine's answer stays as a reading).
            out = dict(a_answer)
            out['grounding'] = list(out.get('grounding') or []) + [
                _reading(f'Analytic engine ({", ".join(engines)})', f_answer, _fast_sql(f_answer, fast.get('planner_receipt')))]
            _confidence(out, 'unverified', 'The stated definition, executed by the independent analysts; the engine reads it differently',
                        evidence='analysts_definition_over_engine')
            together.update(decision='analysts_over_engine', winner='analyst', reason='stated definition; engine differs')
            return {**fast, 'answer': out}, together, rec
        elif fk != ak:
            why = 'the semantic analyst reads it differently'
            _confidence(out, 'low', f'Analytic engine answer kept; {why}', evidence='engine_kept_analyst_differs',
                        analyst_answer=_preview(a_answer))
            _note(out, f'Analytic engine ({", ".join(engines)}) answer kept; {why}.', 'warning')
            together.update(decision='engine_kept_lowered', winner='fast', reason=why)
        else:
            together.update(decision='engine_kept', winner='fast', reason='the semantic analyst gives the same answer')
        return {**fast, 'answer': out}, together, rec
    if a_answer.get('status') == 'clarify':
        # The independent analysts could not settle what the question means: ask, rather than ship one reading as fact.
        out = dict(a_answer)
        out['grounding'] = list(out.get('grounding') or []) + [
            _reading('Agere fast path', f_answer, _fast_sql(f_answer, fast.get('planner_receipt')))]
        _confidence(out, 'low', 'The question has more than one defensible reading; clarification requested',
                    evidence='analysts_request_clarification')
        together.update(decision='clarify', winner='analyst', reason='; '.join(out.get('readings') or [])[:300])
        return {**fast, 'answer': out}, together, rec
    if f_answer.get('status') == 'unsupported' and a_answer.get('status') == 'unsupported':
        missing = list(out.get('missing') or [])
        missing += [m for m in a_answer.get('missing') or [] if m not in missing]
        out['missing'] = missing
        _note(out, 'Both independent paths (Agere and the semantic analyst) found this question unsupported.')
        together.update(decision='both_unsupported', winner='both', reason='both paths abstain')
        return {**fast, 'answer': out}, together, rec
    if fk is not None and fk == ak:
        _confidence(out, 'verified', 'Two independent paths (Agere and the semantic analyst) give the same answer',
                    evidence='two_independent_paths_agree')
        _note(out, 'Verified: the semantic analyst independently gives the same answer.')
        together.update(decision='agree', winner='both', reason='same canonical answer')
        return {**fast, 'answer': out}, together, rec
    if os.environ.get('AGERE_DISAGREE_POLICY', 'analyst').strip().lower() == 'analyst':
        # Build log step 6: on the dev set the analyst alone beat the Opus reconcile (14 vs 13 of 22) at a tenth of the
        # cost, so this policy ships the analyst's answer on disagreement without a reconcile call.
        rec = {'picked': 'analyst', 'answer': a_answer, 'reason': 'disagreement policy: the semantic analyst answers',
               'reconcile_cost_usd': 0.0, 'receipts': [],
               'result': {'model': None, 'estimated_cost_usd': 0.0, 'new_paid_api_calls': 0, 'tool_calls': []}}
    else:
        rec = _reconcile(stack, question, as_of, fast, analyst, semantic_path)
    picked = rec.get('picked')
    together['reconcile_choice'] = picked
    if picked == 'fast':
        level = 'low' if out.get('confidence') == 'low' else 'unverified'
        _confidence(out, level, 'The paths disagreed; the reconciler chose this answer: ' + str(rec.get('reason')),
                    evidence='reconciled_disagreement', other_reading=_preview(a_answer))
        _note(out, 'The semantic analyst disagreed; the reconciler kept this answer (' + str(rec.get('reason')) + ').')
        together.update(decision='reconciled', winner='fast', reason=str(rec.get('reason'))[:300])
        return {**fast, 'answer': out}, together, rec
    chosen = _contract(rec.get('answer')) if picked in ('analyst', 'neither') else None
    if chosen is not None:
        if picked == 'analyst':
            chosen = dict(a_answer)
        chosen['grounding'] = list(chosen.get('grounding') or []) + [
            _reading('Agere fast path', f_answer, _fast_sql(f_answer, fast.get('planner_receipt')))]
        _confidence(chosen, 'unverified', 'The paths disagreed; the reconciler chose '
                    + ('the semantic analyst\'s answer' if picked == 'analyst' else 'a corrected answer') + ': '
                    + str(rec.get('reason')), evidence='reconciled_disagreement', other_reading=_preview(f_answer))
        _note(chosen, 'Agere\'s fast path answered differently; the reconciler chose '
              + ('the semantic analyst\'s answer' if picked == 'analyst' else 'a corrected answer')
              + f" ({rec.get('reason')}).", 'warning')
        together.update(decision='reconciled', winner='analyst' if picked == 'analyst' else 'reconciler',
                        reason=str(rec.get('reason'))[:300])
        return {**fast, 'answer': chosen}, together, rec
    # abstain, timeout, error or an invalid verdict: both readings, partial, low
    if out.get('status') == 'answered':
        out['status'] = 'partial'
    out['grounding'] = list(out.get('grounding') or []) + [
        _reading('Agere fast path', f_answer, _fast_sql(f_answer, fast.get('planner_receipt'))),
        _reading('Semantic analyst', a_answer, [c.get('sql') for c in analyst.get('tool_calls') or []])]
    why = rec.get('reason') or rec.get('reconcile_error') or 'no verdict'
    _confidence(out, 'low', 'Two independent paths disagree and the reconciler could not resolve it: ' + str(why),
                evidence='unresolved_disagreement')
    _note(out, 'Low confidence: Agere and the semantic analyst disagree and the reconciler abstained; both readings are in '
          'grounding.', 'warning')
    together.update(decision='unresolved', winner='none', reason=str(why)[:300])
    return {**fast, 'answer': out}, together, rec


def _is_plan_request(question):
    try:
        from ..plans.request import is_plan_request
    except ImportError:  # pragma: no cover - the plans package ships with the product
        return False
    return is_plan_request(question)


def _compose_plan(stack, question, *, planning='auto', program_kind=None):
    """A plan request ("find HF patients not on full GDMT, then create a plan to sign them up for a pharmacy GDMT program"):
    the cohort through compose_together (answer_type patient_set), then the deterministic enrollment router
    (clinical_core/agere/plans). -> the cohort's result with a 'plan' block (workspace_stack.envelope shapes it)."""
    from ..plans import answer as plan_answer
    semantic_path = Path(stack.path) / 'semantic.sqlite3'
    held = {}

    def cohort(q):
        r = held['result'] = compose_together(stack, q, planning=planning, answer_type='patient_set')
        a = r.get('answer') if isinstance(r.get('answer'), dict) else {}
        ids = a.get('patient_ids') if a.get('status') in ('answered', 'partial') else None
        return {'answer': {'patient_ids': ids} if isinstance(ids, list) else None, 'pipeline': 'full', 'status': a.get('status'),
                'decision': (r.get('together') or {}).get('decision'),
                'cost_usd': (r.get('planning_economics') or {}).get('new_api_estimate_usd')}
    out = plan_answer.build(semantic_path, question, program_kind=program_kind, as_of=_snapshot_as_of(stack),
                            answer_type='plan', cohort=cohort)
    return {**held.get('result', {}), 'plan': out['plan']}


def compose_together(stack, question, *, planning='auto', answer_type=None):
    """Stack.compose for AGERE_TOGETHER=on: see the module docstring. A plan request (answer_type 'plan', or a question
    asking to create a plan / enroll / route patients) computes its cohort here and routes it (_compose_plan)."""
    if answer_type == 'plan' or (answer_type is None and _is_plan_request(question)):
        return _compose_plan(stack, question, planning=planning)
    t0 = time.perf_counter()
    deadline = t0 + budget_s()
    semantic_path = Path(stack.path) / 'semantic.sqlite3'
    as_of = _snapshot_as_of(stack)
    future, model = None, semantic_model()
    # A requested patient list is answered by the analysts' executed definition (engines return ranked, capped worklists).
    # A question that states its own definition is answered by that definition: engines do not skip the analysts there
    # (measured: engine routes on defined questions were right 1 of 8 times on the native bank).
    engine = [] if answer_type == 'patient_set' or (_inline_definition(question) and engines_defer_on_definitions()) \
        else _compiled_engine(stack, question, as_of)
    fast, fast_error = None, None
    from .economical_planner import defer_to_analyst
    first = specialists_first() and semantic_path.is_file() and not engine and deferral_on()
    if semantic_path.is_file() and not engine and not first:
        future = _pool().submit(_run_analyst, stack, question, as_of, semantic_path, model, answer_type)
    try:
        # The fast path keeps its free and governed rungs and skips the paid hosted ladder while the analysts may answer.
        with defer_to_analyst((future is not None or first) and deferral_on()):
            fast = stack._compose(question, planning=planning)
    except Exception as exc:  # noqa: BLE001 - the analyst may still answer; re-raised in decide otherwise
        fast_error = exc
    claimed = first and fast_error is None and _specialist_answer(fast, answer_type, question)
    cert = _certify_claim(fast) if fast_error is None and _specialist_answer(fast, answer_type, question) else None
    if cert is not None and cert.get('status') == 'not_certified' and not _inline_definition(question):
        # The not-certified verdicts were measured on questions that state their own definition; a bare question is
        # outside that calibration domain, so the governed / compiled answer stands (recorded as uncalibrated).
        cert = {**cert, 'accept': True, 'status': 'uncalibrated',
                'reason': 'outside the calibration domain (no stated definition); ' + str(cert.get('reason') or '')}
    deferred_by_certificate = bool(claimed and cert is not None and not cert['accept'])
    if deferred_by_certificate:
        claimed = False  # calibrated tier below its certified threshold: the next tier (the analysts) answers
    if first and not claimed:
        # No specialist claimed the question: now (and only now) the independent analysts answer it.
        future = _pool().submit(_run_analyst, stack, question, as_of, semantic_path, model, answer_type)
    with _released(stack):
        if future is None:
            state, analyst = ('skipped_engine' if engine else 'skipped_specialist' if claimed else 'unavailable'), None
        else:
            state, analyst = _wait(future, deadline)
            if state == 'failed':
                analyst = {'error': analyst, 'model': model, 'estimated_cost_usd': None, 'new_paid_api_calls': 0}
        wait_s = round(time.perf_counter() - t0, 3)
    fallback = None
    if (fast or {}).get('route') == 'deferred_to_analyst' and not _usable(analyst, state):
        # The analysts produced nothing usable: run the full fast path (hosted ladder, frontier) after all.
        deferred = fast
        try:
            fast, fast_error = stack._compose(question, planning=planning), None
        except Exception as exc:  # noqa: BLE001
            fast, fast_error = deferred, exc
        fallback = {'reason': 'analysts produced no usable answer', 'route': (fast or {}).get('route')}
    with _released(stack):
        result, together, rec = decide(stack, question, fast, fast_error, analyst, state,
                                       as_of=as_of, semantic_path=semantic_path, answer_type=answer_type)
    together.update(engine_route=engine or None, fallback=fallback, deferred=(fast or {}).get('route') == 'deferred_to_analyst' or bool(fallback),
                    hybrid={k: v for k, v in ((analyst or {}).get('hybrid') or {}).items() if k != 'runs'} or None,
                    analyst={**(_part(analyst) or {}), 'model': model, 'state': state,
                             'answer_status': ((analyst or {}).get('answer') or {}).get('status')
                             if isinstance((analyst or {}).get('answer'), dict) else None},
                    reconcile=({**(_part(rec.get('result')) or {}), 'picked': rec.get('picked'), 'reason': rec.get('reason'),
                                'timed_out': bool(rec.get('timed_out')), 'error': rec.get('reconcile_error')}
                               if rec is not None else None),
                    fast_error=f'{type(fast_error).__name__}: {fast_error}'[:300] if fast_error is not None else None,
                    analyst_wait_s=wait_s, elapsed_s=round(time.perf_counter() - t0, 3))
    result = _account(result, analyst if state in ('done', 'failed') else None, state, rec)
    if cert is not None:
        if deferred_by_certificate:
            together['deferred_by_certificate'] = True
            if together.get('winner') == 'fast' and isinstance(result.get('answer'), dict):
                # the analysts produced nothing usable, so the deferred specialist answer ships; it is not certified
                out = dict(result['answer'])
                if out.get('confidence') == 'verified':
                    _confidence(out, 'unverified', cert['statement'], evidence='below_certified_threshold')
                _note(out, 'Shipped after deferral: ' + cert['statement'], 'warning')
                result = {**result, 'answer': out}
            together['certificate'] = {k: cert.get(k) for k in ('tier', 'status', 'score', 'threshold', 'alpha', 'delta', 'bound',
                                                                'n_calibration', 'statement') if cert.get(k) is not None}
        elif together.get('winner') in ('fast', 'both'):
            result = _stamp_certificate(result, cert, together)
    receipt = result['planner_receipt']
    if receipt.get('route') == 'cohort_family' and together.get('winner') in (None, 'fast', 'both', 'agree'):
        # The family answer becomes a verified (question -> SQL) pair the learned cache reuses (cohort_families.record).
        try:
            from .cohort_families import record as record_family
            record_family(stack.path, question, receipt, result.get('answer'))
        except Exception:  # noqa: BLE001 - learning bookkeeping never fails an answer
            pass
    if isinstance(receipt.get('timings'), dict):
        receipt['timings'] = {**receipt['timings'], 'together_total_s': together['elapsed_s']}
    return {**result, 'together': together}
