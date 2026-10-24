"""Evaluation mode: answer a question with every learning write off, so a benchmark measures the stack as frozen.

A benchmark that goes through the normal compose route teaches the stack the test: learning_hooks appends successes to the
training traffic and failures to the gaps file, learning.sqlite3 keeps outcomes / routing / verified pairs, the shared planner
keeps typed contracts for paraphrase reuse, the cascade keeps tier timings and traces, and a deferred tier would finish in the
background and save its answer. Evaluation mode switches all of that off for one request. Reads of the certified stores stay
on (the contestant is the stack as it was frozen); paid-call accounting (the budget ledger) is not learning and stays on.

Switches (either one is enough; the header can only turn it on, never off):
  request header   X-Agere-Evaluation: 1            (service.py wraps /api/stack/compose in request_scope)
  environment      AGERE_EVALUATION=1               (the whole process; reaches worker threads a request spawns)
  X-Agere-Frozen-Learning: <snapshot-id>            recorded in the receipt: which frozen learning state this run claims to use.

One contextvar carries the flag (no per-function parameter); every writer asks `active()` first. A thread started by a
request does not inherit a contextvar, so a writer that can run on such a thread is also covered by the audit below.

Audit: install_audit() wraps sqlite3.connect once; while evaluation is active every INSERT / UPDATE / DELETE that a connection
to a store inside the stack directory attempts is counted per file (model-cache.sqlite3, the embedding / provider-response
cache, is counted apart as allowed). The count goes on the receipt, so a write path this module missed shows up in the first
evaluation run instead of silently contaminating it. AGERE_EVALUATION_STRICT=1 denies those statements instead of counting.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import sqlite3
import threading
from collections import Counter
from pathlib import Path

VERSION = 'evaluation-mode/1'
HEADER = 'X-Agere-Evaluation'
FROZEN_HEADER = 'X-Agere-Frozen-Learning'
ALLOWED_WRITES = frozenset({'model-cache.sqlite3'})
_STATE = contextvars.ContextVar('agere_evaluation', default=None)
_TRUE = ('1', 'on', 'true', 'yes')


def _env_on():
    return os.environ.get('AGERE_EVALUATION', '').strip().lower() in _TRUE


def active():
    """True while the current request (or the whole process, AGERE_EVALUATION=1) is an evaluation run."""
    return _STATE.get() is not None or _env_on()


def scratch_dir(stack_path):
    """Where an evaluation request keeps its conversation turns and saved cohorts (follow-ups need their parent): a directory OUTSIDE the
    stack, per stack, so a benchmark chain works like a real conversation while nothing reaches the stack's stores or learning."""
    import hashlib, tempfile
    base = Path(os.environ.get('AGERE_EVALUATION_SCRATCH') or Path(tempfile.gettempdir()) / 'agere-evaluation')
    d = base / hashlib.sha1(str(Path(stack_path).resolve()).encode()).hexdigest()[:12]
    d.mkdir(parents=True, exist_ok=True)
    return d


_OPERATIONAL_LOCK = threading.Lock()


def operational_path(stack_path, name):
    """Evaluation previews use a consistent private copy, including prior work state.

    Preparing analytical review proposals needs read-after-write semantics. Keep
    those drafts in evaluation scratch rather than changing the live work store.
    The frozen snapshot id isolates independent runs; follow-up requests in one
    run share their private proposals. SQLite backup includes committed WAL rows.
    """
    source = Path(stack_path) / name
    if not active():
        return source
    import hashlib
    run = hashlib.sha256(str(frozen_snapshot() or 'request-evaluation').encode()).hexdigest()[:16]
    target = scratch_dir(stack_path) / run / name
    with _OPERATIONAL_LOCK:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            real_connect = _original_connect.get('connect', sqlite3.connect)
            temporary = target.with_suffix('.copying')
            try:
                destination = real_connect(temporary)
                try:
                    if source.exists():
                        original = real_connect(source.resolve().as_uri() + '?mode=ro', uri=True)
                        try:
                            original.backup(destination)
                        finally:
                            original.close()
                finally:
                    destination.close()
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
    return target


def frozen_snapshot():
    state = _STATE.get()
    return (state or {}).get('frozen_learning') or os.environ.get('AGERE_FROZEN_LEARNING') or None


@contextlib.contextmanager
def scope(frozen_learning=None, stack_path=None):
    """Run a block in evaluation mode (tests, tools). Yields the state dict."""
    state = {'frozen_learning': frozen_learning, 'stack_path': str(stack_path) if stack_path else None,
             'write_attempts': Counter(), 'allowed_writes': Counter(), 'denied': Counter()}
    token = _STATE.set(state)
    try:
        yield state
    finally:
        _STATE.reset(token)


def requested(headers):
    """Does this request ask for evaluation mode? `headers` is any mapping with .get (http.server's message works)."""
    try:
        value = headers.get(HEADER)
    except AttributeError:
        return False
    return str(value or '').strip().lower() in _TRUE


@contextlib.contextmanager
def request_scope(headers, stack_path=None):
    """service.py: evaluation scope when the request carries X-Agere-Evaluation, otherwise a no-op."""
    if not requested(headers):
        yield None
        return
    frozen = str(headers.get(FROZEN_HEADER) or '').strip()[:120] or None
    install_audit()
    with scope(frozen, stack_path) as state:
        yield state


def receipt():
    """What the response says about isolation. None when the request was a normal one."""
    if not active():
        return None
    state = _STATE.get() or {}
    return {'version': VERSION, 'learning_writes': 'off', 'background_continuation': 'off',
            'frozen_learning': frozen_snapshot(), 'scope': 'request' if state else 'process',
            'operational_previews': 'isolated_scratch',
            'write_attempts_by_store': dict(state.get('write_attempts') or {}),
            'allowed_writes_by_store': dict(state.get('allowed_writes') or {}),
            'writes_denied': bool(os.environ.get('AGERE_EVALUATION_STRICT', '') in _TRUE)}


# -- sqlite audit ------------------------------------------------------------------------------------------------------
_DML = {getattr(sqlite3, n) for n in ('SQLITE_INSERT', 'SQLITE_UPDATE', 'SQLITE_DELETE')}
_installed = threading.Lock()
_original_connect = {}


def _file_of(database):
    text = str(database)
    if text.startswith('file:'):
        text = text[5:].split('?', 1)[0]
    return Path(text) if text and text != ':memory:' else None


def _authorizer(state, name):
    strict = os.environ.get('AGERE_EVALUATION_STRICT', '') in _TRUE

    def check(action, arg1, arg2, dbname, source):
        if action in _DML and dbname == 'main' and not str(arg1).startswith('sqlite_'):  # schema bookkeeping is not a row write
            if name in ALLOWED_WRITES:
                state['allowed_writes'][name] += 1
            elif strict:
                state['denied'][name] += 1
                state['write_attempts'][name] += 1
                return sqlite3.SQLITE_DENY
            else:
                state['write_attempts'][name] += 1
        return sqlite3.SQLITE_OK
    return check


def install_audit():
    """Wrap sqlite3.connect (once per process) so evaluation requests count writes into the stack's stores."""
    with _installed:
        if _original_connect:
            return False
        real = sqlite3.connect
        _original_connect['connect'] = real

        def connect(database, *args, **kwargs):
            db = real(database, *args, **kwargs)
            state = _STATE.get()
            if state is not None and state.get('stack_path'):
                path = _file_of(database)
                try:
                    inside = path is not None and path.resolve().parent == Path(state['stack_path']).resolve()
                except OSError:
                    inside = False
                if inside:
                    try:
                        db.set_authorizer(_authorizer(state, path.name))
                    except sqlite3.Error:
                        pass
            return db
        sqlite3.connect = connect
        return True


def uninstall_audit():
    with _installed:
        real = _original_connect.pop('connect', None)
        if real is not None:
            sqlite3.connect = real
            return True
        return False
