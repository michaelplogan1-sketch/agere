"""Finish in the background what the answer deadline cut short (2026-10-23 latency).

When the cascade ran out of its answer budget with no accepted answer it replies early (best partial, or not-understood with
tiers_tried + deferred_tiers). enqueue() then hands the deferred tiers to ONE worker thread per server process: a bounded FIFO
queue, one continuation at a time (and the local-model lock, because the continuation asks tiers through Cascade.call_tier). When a
deferred tier produces an accepted answer (same checks and claim verification as in the foreground) it is saved through the verified
stores, so the next identical or reworded question is answered instantly:
  checked graph   learning_hooks.after_answer -> learning.record_verified (learned_verified_sql / semantic cache / template decoding),
                  corpus row, router trace; saved only when the answer verifies (answered, nothing missing, no check flags)
  typed contract  shared_planner.save_accepted -> question-plans.sqlite3 (exact replay and paraphrase reuse), as plan() saves it
Never runs two continuations at once; a new one is dropped when AGERE_BACKGROUND_QUEUE_MAX (default 4) are already waiting, or when
the same question is already queued or running. AGERE_BACKGROUND_CONTINUE=off disables it; AGERE_BACKGROUND_BUDGET_SECONDS (default 300)
bounds one continuation; AGERE_BACKGROUND_DELAY_SECONDS (default 1) lets the early reply go first. Heals, never blocks: a failure is recorded in recent() and nothing reaches the caller.
"""
import collections
import os
import threading
import time

VERSION = 'background-continuation/1'
_lock = threading.Lock()
_queue = collections.deque()
_state = {'worker': None, 'active': None}
_recent = collections.deque(maxlen=20)


def enabled():
    return os.environ.get('AGERE_BACKGROUND_CONTINUE', 'on').strip().lower() != 'off'


def max_queue():
    try:
        return max(0, int(os.environ.get('AGERE_BACKGROUND_QUEUE_MAX', '4')))
    except ValueError:
        return 4


def budget_seconds():
    try:
        return max(1.0, float(os.environ.get('AGERE_BACKGROUND_BUDGET_SECONDS', '300')))
    except ValueError:
        return 300.0


def delay_seconds():
    """Pause before a continuation starts, so the early reply (and the next question) is not slowed by it (AGERE_BACKGROUND_DELAY_SECONDS)."""
    try:
        return max(0.0, float(os.environ.get('AGERE_BACKGROUND_DELAY_SECONDS', '1')))
    except ValueError:
        return 1.0


def recent():
    """The last finished continuations: [{'question', 'tier', 'answered', 'saved', 'seconds', 'error'}]."""
    return list(_recent)


def depth():
    with _lock:
        return len(_queue) + (1 if _state['active'] else 0)


def wait_idle(timeout=30.0):
    """Block until the queue is drained and nothing runs (tests, graceful shutdown). -> True when idle."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            if not _queue and not _state['active'] and not _state['worker']: return True
        time.sleep(0.01)
    return False


def _key(job):
    return (str(getattr(job['stack'], 'path', '')), job['question'])


def enqueue(cascade):
    """Queue the cascade's deferred tiers for the background worker. -> receipt {'queued', 'reason', 'depth', 'tiers'}."""
    tiers = [d['tier'] for d in cascade.deferred]
    if os.environ.get('AGERE_BACKGROUND_CLAUDE', 'off') != 'on':  # token economics: background work is free / System One only unless asked
        from .proposers import STAGES, stage_of
        tiers = [t for t in tiers if stage_of(t) != len(STAGES) - 1]
    if not enabled(): return {'queued': False, 'reason': 'AGERE_BACKGROUND_CONTINUE=off', 'tiers': tiers}
    if not tiers: return {'queued': False, 'reason': 'nothing deferred', 'tiers': tiers}
    job = {'stack': cascade.stack, 'catalog': cascade.catalog, 'question': cascade.question, 'population': cascade.population,
           'hosted_model': cascade.hosted_model, 'memory_identity': cascade.memory_identity, 'tiers': tiers,
           'order': tuple(getattr(cascade, 'order_used', None) or tiers), 'log': list(cascade.log), 'claude_down': cascade.claude_down}
    with _lock:
        if any(_key(j) == _key(job) for j in _queue) or (_state['active'] == _key(job)):
            return {'queued': False, 'reason': 'this question is already queued or running', 'tiers': tiers}
        if len(_queue) >= max_queue():
            return {'queued': False, 'reason': f'queue full ({len(_queue)} waiting, AGERE_BACKGROUND_QUEUE_MAX={max_queue()})', 'tiers': tiers}
        _queue.append(job)
        if _state['worker'] is None:
            worker = threading.Thread(target=_work, name='agere-background-continuation', daemon=True)
            _state['worker'] = worker
            worker.start()
        return {'queued': True, 'depth': len(_queue), 'tiers': tiers, 'version': VERSION}


def _work():
    while True:
        with _lock:
            if not _queue:
                _state['worker'] = None; _state['active'] = None
                return
            job = _queue.popleft()
            _state['active'] = _key(job)
        if delay_seconds(): time.sleep(delay_seconds())
        started = time.perf_counter()
        row = {'question': job['question'], 'tier': None, 'answered': False, 'saved': False, 'error': None}
        try:
            row.update(run_job(job))
        except Exception as exc:  # noqa: BLE001 - a background failure never reaches a caller
            row['error'] = f'{type(exc).__name__}: {exc}'[:300]
        row['seconds'] = round(time.perf_counter() - started, 3)
        _recent.append(row)
        with _lock: _state['active'] = None


def run_job(job):
    """Run the deferred tiers (own thread, own budget); save an accepted answer. -> {'tier', 'answered', 'saved'}."""
    from .proposers import Cascade
    c = Cascade(job['stack'], job['catalog'], job['question'], population=job['population'], hosted_model=job['hosted_model'],
                memory_identity=job['memory_identity'])
    c.log, c.claude_down = list(job['log']), job['claude_down']
    c.order_used = job['order']
    try:
        got = c.continue_run(job['tiers'], budget_seconds())
        if got is None: return {'answered': False}
        contract, interpretation = got
        return {'answered': True, 'tier': (interpretation.get('proposer') or {}).get('name') or interpretation.get('interpreter'),
                'saved': save(c, contract, interpretation)}
    finally:
        c.flush_timings()
        c.close()


def save(cascade, contract, interpretation):
    """Store an accepted answer where the foreground stores verified ones. -> True when something was saved."""
    stack, question = cascade.stack, cascade.question
    if contract.get('kind') == 'checked_graph':
        from . import learning_hooks
        executed = contract.get('executed') or {}
        result = {'answer': dict(executed.get('answer') or {}), 'graph': contract.get('graph'), 'answer_checks': executed.get('answer_checks'),
                  'request_contract': {k: v for k, v in contract.items() if k != 'executed'},
                  'planner_receipt': {'interpretation': interpretation, 'answer_checks': executed.get('answer_checks'),
                                      'route': (contract.get('legacy_receipt') or {}).get('route')},
                  'new_paid_api_calls': cascade.calls}
        return bool((learning_hooks.after_answer(stack, question, result).get('saved_pair') or {}).get('saved'))
    from . import shared_planner
    return bool(shared_planner.save_accepted(stack, question, contract, interpretation, population=cascade.population,
                                             memory_identity=cascade.memory_identity))
