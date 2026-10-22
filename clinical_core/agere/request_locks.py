"""Per-resource request locks for the loopback service (replaces the one service-wide stack lock).

Before 2026-10-22 every POST took one RLock, so one question blocked every other question, search and cohort lookup
while it ran its free cascade (only paid interpretation released it). Now each request holds only the resources it
writes or that are not thread-safe:

  route kind   resources held                         why
  read         none                                   search / cohort / non-live ask: read-only; the retriever is thread-safe
  question     question:<conversation or question>    compose / live ask / execute-plan: concurrent across questions; the same
                                                      conversation (or the same standalone question) stays ordered so a
                                                      follow-up never races the turn it follows and an identical question
                                                      never double-writes its learning rows
  state        saved_state + family:<route family>    every other POST (work, decisions, reviews, corrections): mutations keep
                                                      the old mutual ordering among themselves but no longer block questions

Shared objects that are not thread-safe get their own guard instead of a service lock: the retriever's index connection
becomes one SQLite connection per thread (read-mostly; SQLite itself arbitrates), and the local encoder / reranker (one
model, one vector-cache connection) run under one model lock (thread_safe_retriever). Question-written state stores are
put in WAL mode (state_wal) so concurrent short commits wait on SQLite's busy timeout instead of failing.

stack.answer_lock stays the hook shared_planner.interpretation_window and together._released use; it is now a proxy over
the lock the *current* thread holds for writing (saved_state for state routes, nothing for questions), so releasing it
during interpretation never touches another request's lock.
Locks are always taken in sorted order (no lock-order deadlock). Synthetic data only.
"""
from contextlib import contextmanager
import hashlib
import re
import sqlite3
import threading
from pathlib import Path

VERSION = 'request-locks/1'
READ_ROUTES = frozenset({'/api/stack/search', '/api/stack/cohort'})
QUESTION_ROUTES = frozenset({'/api/stack/compose', '/api/stack/execute-plan'})
SAVED_STATE = 'saved_state'
# Stores a question may write (answers, learning, plans, caches). WAL lets a reader and a writer proceed together.
STATE_STORES = ('answers.sqlite3', 'learning.sqlite3', 'conversations.sqlite3', 'question-plans.sqlite3', 'model-cache.sqlite3',
                'outcomes.sqlite3', 'interpretation-state.sqlite3', 'semantic-readings.sqlite3', 'context-passes.sqlite3',
                'program-work.sqlite3', 'evidence-acquisition.sqlite3')


def _norm(text):
    return ' '.join(re.findall(r'[a-z0-9]+', str(text).lower()))


def resources(path, body=None):
    """(kind, sorted resource names) for one POST route."""
    body = body if isinstance(body, dict) else {}
    if path in READ_ROUTES or (path == '/api/stack/ask' and body.get('live') is False):
        return 'read', ()
    if path in QUESTION_ROUTES or path == '/api/stack/ask':
        if body.get('conversation_id'):
            key = 'conversation:' + str(body['conversation_id'])[:200]
        elif path == '/api/stack/execute-plan':
            key = 'plan:' + hashlib.sha256(repr(sorted(body.items())).encode()).hexdigest()[:16]
        else:
            key = 'question:' + hashlib.sha256(_norm(body.get('question', '')).encode()).hexdigest()[:16]
        return 'question', (key,)
    parts = [p for p in path.split('/') if p]
    family = parts[2] if len(parts) > 2 else 'stack'
    return 'state', tuple(sorted({SAVED_STATE, 'family:' + family}))


class RequestLocks:
    """Named re-entrant locks; hold() takes a set in sorted order and records which one the thread may release."""

    def __init__(self):
        self._locks, self._guard, self._local = {}, threading.Lock(), threading.local()
        self.counters = {'read': 0, 'question': 0, 'state': 0, 'max_concurrent': 0}
        self._active = 0

    def lock(self, name):
        with self._guard:
            return self._locks.setdefault(name, threading.RLock())

    def held(self):
        return list(getattr(self._local, 'held', []))

    def releasable(self):
        """The write lock this thread holds (answer_lock target), else None."""
        stack = getattr(self._local, 'writer', None)
        return stack[-1] if stack else None

    @contextmanager
    def hold(self, kind, names):
        names = tuple(sorted(set(names)))
        taken = []
        try:
            for n in names:
                lk = self.lock(n); lk.acquire(); taken.append((n, lk))
            self._local.held = [*getattr(self._local, 'held', []), *names]
            writer = self.lock(SAVED_STATE) if SAVED_STATE in names else None
            self._local.writer = [*getattr(self._local, 'writer', []), writer]
            with self._guard:
                self.counters[kind] = self.counters.get(kind, 0) + 1
                self._active += 1; self.counters['max_concurrent'] = max(self.counters['max_concurrent'], self._active)
            try:
                yield names
            finally:
                with self._guard: self._active -= 1
                self._local.writer = self._local.writer[:-1]
                self._local.held = self._local.held[:len(self._local.held) - len(names)]
        finally:
            for n, lk in reversed(taken): lk.release()

    def for_route(self, path, body=None):
        kind, names = resources(path, body)
        return self.hold(kind, names)

    def status(self):
        with self._guard:
            return {'schema': VERSION, 'global_lock': False, 'locks': len(self._locks), 'active': self._active, 'counters': dict(self.counters)}


class ThreadAnswerLock:
    """stack.answer_lock: the write lock the current thread holds (or nothing). Implements the RLock surface the
    interpretation window (_is_owned/_release_save/_acquire_restore) and together._released (release/acquire) use."""

    def __init__(self, locks):
        self.locks = locks

    def _target(self):
        return self.locks.releasable()

    def _is_owned(self):
        t = self._target()
        return bool(t is not None and t._is_owned())

    def _release_save(self):
        return self._target()._release_save()

    def _acquire_restore(self, state):
        self._target()._acquire_restore(state)

    def release(self):
        t = self._target()
        if t is None or not t._is_owned(): raise RuntimeError('cannot release un-acquired lock')
        t.release()

    def acquire(self, blocking=True, timeout=-1):
        t = self._target()
        return True if t is None else t.acquire(blocking, timeout)

    __enter__ = acquire

    def __exit__(self, *exc):
        self.release()


# ---- thread-safe shared objects ---------------------------------------------------------------------------------------
class ThreadLocalConnection:
    """One sqlite3 connection per thread to the same database (same connect arguments); attribute access goes to the
    calling thread's connection."""

    def __init__(self, *args, **kwargs):
        kwargs.pop('check_same_thread', None)
        kwargs.setdefault('timeout', 30)
        self._args, self._kwargs, self._local, self._all, self._guard = args, kwargs, threading.local(), [], threading.Lock()

    def _conn(self):
        c = getattr(self._local, 'conn', None)
        if c is None:
            c = sqlite3.connect(*self._args, **self._kwargs)
            self._local.conn = c
            with self._guard: self._all.append(c)
        return c

    def __getattr__(self, name):
        return getattr(self._conn(), name)

    def __enter__(self):
        return self._conn().__enter__()

    def __exit__(self, *exc):
        return self._conn().__exit__(*exc)

    def close(self):
        with self._guard:
            for c in self._all:
                try: c.close()
                except sqlite3.Error: pass
            self._all.clear()
        self._local = threading.local()


def _locked_class(cls, methods):
    def wrap(fn):
        def locked(self, *a, **k):
            with self._agere_model_lock:
                return fn(self, *a, **k)
        locked.__name__, locked.__doc__ = fn.__name__, fn.__doc__
        return locked
    ns = {m: wrap(getattr(cls, m)) for m in methods if callable(getattr(cls, m, None))}
    return type('Locked' + cls.__name__, (cls,), ns)


def lock_model(obj, lock, methods=('encode', 'windows', 'rank', 'encode_queries', 'encode_documents')):
    """Serialize one model object's inference/cache calls under `lock` (class swap, so shallow copies keep their own db)."""
    if obj is None or getattr(obj, '_agere_model_lock', None) is not None: return obj
    obj.__class__ = _locked_class(type(obj), methods)
    obj._agere_model_lock = lock
    return obj


def thread_safe_retriever(retriever, model_lock=None):
    """Make a Retriever safe for concurrent request threads (what the global lock used to guarantee)."""
    if getattr(retriever, '_agere_thread_safe', False): return retriever
    model_lock = model_lock or threading.RLock()
    old = retriever.db
    target = old.execute('PRAGMA database_list').fetchone()[2]
    old.close()
    retriever.db = ThreadLocalConnection(target)
    if getattr(retriever, 'annotation_db', None) is not None:
        ann = retriever.annotation_db.execute('PRAGMA database_list').fetchone()[2]
        retriever.annotation_db.close()
        retriever.annotation_db = ThreadLocalConnection(Path(ann).resolve().as_uri() + '?mode=ro', uri=True)
    lock_model(getattr(retriever, 'encoder', None), model_lock)
    lock_model(getattr(retriever, 'reranker', None), model_lock)
    retriever._agere_thread_safe = True
    retriever._agere_model_lock = model_lock
    return retriever


def state_wal(path, stores=STATE_STORES):
    """Put existing question-written stores in WAL mode (persistent per file). Returns {store: journal_mode}."""
    out = {}
    for name in stores:
        f = Path(path) / name
        if not f.is_file(): continue
        try:
            db = sqlite3.connect(f, timeout=30)
            try:
                mode = db.execute('PRAGMA journal_mode').fetchone()[0]
                out[name] = mode if mode == 'wal' else db.execute('PRAGMA journal_mode=WAL').fetchone()[0]
            finally:
                db.close()
        except sqlite3.Error as exc:  # a busy or read-only store keeps its mode; questions still work
            out[name] = f'unchanged ({type(exc).__name__})'
    return out


def install(stack):
    """Service start: per-resource locks for this stack. Returns the RequestLocks the handler uses."""
    locks = RequestLocks()
    stack.answer_lock = ThreadAnswerLock(locks)
    stack.request_locks = locks
    thread_safe_retriever(stack.retriever, locks.lock('model'))
    stack.state_journal = state_wal(stack.path)
    return locks
