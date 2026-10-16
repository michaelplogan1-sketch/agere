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
   - an analytic engine answer (survival, risk_adjustment, target_trial, forecast, program_effect, trajectory) is never
     replaced; its confidence is lowered to low only when the analyst says unsupported or differs;
   - both unsupported -> the abstention ships;
   - same canonical answer -> confidence verified, evidence 'two_independent_paths_agree';
   - different answers -> one reconcile turn (AGERE_RECONCILE_MODEL, default claude-opus-5-5; blind order; both answers
     with their SQL/grounding; at most 4 SQL checks; the question's stated definitions win over governed defaults),
     bounded by AGERE_RECONCILE_BUDGET_S (60 s). Pick 1/2 -> that answer, unverified; 'neither' -> the reconciler's
     corrected answer, unverified; abstain / unresolved / timeout -> the fast answer as partial, confidence low, both
     readings in grounding.
5. Receipts: result['together'] (decision, winner, reason, model / cost / calls / seconds per part). Analyst and reconcile
   calls are added to planner_receipt steps, planning_economics and new_paid_api_calls (and planner_receipt's count), so
   the bench adapter's cost completeness holds. An analyst still running at the deadline marks the cost unknown.

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
ENGINE_OPERATIONS = frozenset({'survival', 'risk_adjustment', 'target_trial', 'forecast', 'program_effect', 'trajectory'})
DEFAULT_SEMANTIC_MODEL = 'claude-haiku-5-5'   # semantic-analyst bake-off winner
DEFAULT_RECONCILE_MODEL = 'claude-opus-5-5'
DEFAULT_BUDGET_S = 90.0
DEFAULT_RECONCILE_BUDGET_S = 60.0
SEND_TIMEOUT_S = 180
# The documented semantic model is ~141K characters; the tool loop adds at most 8 tool results of <= 60K characters.
REQUEST_MAX_BYTES = 1_200_000
CONTRACT_KEYS = ('status', 'answer_type', 'count', 'patient_ids', 'value', 'table', 'unknown_count', 'missing', 'grounding')
STATUSES = ('answered', 'partial', 'unsupported', 'grounding_failed')
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
    return _seconds('AGERE_TOGETHER_BUDGET_S', DEFAULT_BUDGET_S)


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

def _run_analyst(stack, question, as_of, semantic_path, model):
    started, receipts = time.perf_counter(), []
    wh = warehouse(semantic_path)
    with _own_ledger(stack) as ledger:
        result = semantic_analyst.analyze(question, as_of or wh.as_of, wh, send=_ledgered_send(stack, ledger, receipts),
                                          model=model)
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


def decide(stack, question, fast, fast_error, analyst, analyst_state, *, as_of=None, semantic_path=None):
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
    if a_answer is None or f_answer is None:
        reason = {'timed_out': 'analyst did not finish within the budget', 'unavailable': 'semantic layer not built',
                  'failed': (analyst or {}).get('error') or 'analyst returned no valid answer'}.get(
                      together['analyst_state'], 'analyst returned no valid answer')
        if f_answer is None:
            reason = 'fast result has no answer object'
        elif _empty_answer(f_answer) and os.environ.get('AGERE_UNCHECKED_ZERO', 'abstain') == 'abstain':
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
        if a_answer.get('status') == 'unsupported' or fk != ak:
            why = 'the semantic analyst says unsupported' if a_answer.get('status') == 'unsupported' \
                else 'the semantic analyst reads it differently'
            _confidence(out, 'low', f'Analytic engine answer kept; {why}', evidence='engine_kept_analyst_differs',
                        analyst_answer=_preview(a_answer))
            _note(out, f'Analytic engine ({", ".join(engines)}) answer kept; {why}.', 'warning')
            together.update(decision='engine_kept_lowered', winner='fast', reason=why)
        else:
            together.update(decision='engine_kept', winner='fast', reason='the semantic analyst gives the same answer')
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


def compose_together(stack, question, *, planning='auto'):
    """Stack.compose for AGERE_TOGETHER=on: see the module docstring."""
    t0 = time.perf_counter()
    deadline = t0 + budget_s()
    semantic_path = Path(stack.path) / 'semantic.sqlite3'
    as_of = _snapshot_as_of(stack)
    future, model = None, semantic_model()
    if semantic_path.is_file():
        future = _pool().submit(_run_analyst, stack, question, as_of, semantic_path, model)
    fast, fast_error = None, None
    try:
        fast = stack._compose(question, planning=planning)
    except Exception as exc:  # noqa: BLE001 - the analyst may still answer; re-raised in decide otherwise
        fast_error = exc
    with _released(stack):
        if future is None:
            state, analyst = 'unavailable', None
        else:
            state, analyst = _wait(future, deadline)
            if state == 'failed':
                analyst = {'error': analyst, 'model': model, 'estimated_cost_usd': None, 'new_paid_api_calls': 0}
        wait_s = round(time.perf_counter() - t0, 3)
        result, together, rec = decide(stack, question, fast, fast_error, analyst, state,
                                       as_of=as_of, semantic_path=semantic_path)
    together.update(analyst={**(_part(analyst) or {}), 'model': model, 'state': state,
                             'answer_status': ((analyst or {}).get('answer') or {}).get('status')
                             if isinstance((analyst or {}).get('answer'), dict) else None},
                    reconcile=({**(_part(rec.get('result')) or {}), 'picked': rec.get('picked'), 'reason': rec.get('reason'),
                                'timed_out': bool(rec.get('timed_out')), 'error': rec.get('reconcile_error')}
                               if rec is not None else None),
                    fast_error=f'{type(fast_error).__name__}: {fast_error}'[:300] if fast_error is not None else None,
                    analyst_wait_s=wait_s, elapsed_s=round(time.perf_counter() - t0, 3))
    result = _account(result, analyst if state in ('done', 'failed') else None, state, rec)
    receipt = result['planner_receipt']
    if isinstance(receipt.get('timings'), dict):
        receipt['timings'] = {**receipt['timings'], 'together_total_s': together['elapsed_s']}
    return {**result, 'together': together}
