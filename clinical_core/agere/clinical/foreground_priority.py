"""Foreground questions go first (2026-10-23 latency).

One refused foreground question took 42 s on a 30 s budget because its local-model tiers queued behind the background continuation
worker (background_continuation.py) on the one local-model lock. Two rules fix that:

  in-flight counter   foreground() wraps every Cascade.run; the background worker does not start a job or a tier while any
                      foreground question is in flight (wait_for_foreground), so its heavy tiers never compete with an answer someone waits for.
  priority lock       LOCAL_MODEL_LOCK is a PriorityLock: a foreground waiter is served before any background waiter, and a background
                      caller acquires only when the lock is free and no foreground caller is waiting. A tier already running is never
                      preempted (a model decode cannot be interrupted), so a foreground caller may still wait for the one background tier that
                      is mid-run; that tier is bounded by its own time share.

Receipts: the foreground seconds the background worker waited ride on background_continuation.recent() (foreground_wait_seconds).
"""
import contextlib
import contextvars
import threading
import time

VERSION = 'foreground-priority/1'
_cond = threading.Condition()
_state = {'foreground': 0}
_background = contextvars.ContextVar('agere_background_run', default=False)  # True inside the background worker's cascade


def in_background():
    return _background.get()


@contextlib.contextmanager
def background_run():
    token = _background.set(True)
    try: yield
    finally: _background.reset(token)


@contextlib.contextmanager
def foreground():
    """A foreground question is in flight for the duration of the block."""
    with _cond:
        _state['foreground'] += 1
    try: yield
    finally:
        with _cond:
            _state['foreground'] -= 1
            _cond.notify_all()


def foreground_in_flight():
    with _cond: return _state['foreground']


def wait_for_foreground(timeout=None, *, clock=time.monotonic):
    """Block while a foreground question is in flight. -> seconds waited (returns early after timeout; the caller re-checks)."""
    started = clock()
    end = None if timeout is None else started + timeout
    with _cond:
        while _state['foreground'] > 0:
            left = None if end is None else end - clock()
            if left is not None and left <= 0: break
            _cond.wait(0.25 if left is None else min(0.25, left))
    return round(clock() - started, 3)


class PriorityLock:
    """A mutex whose foreground waiters are served before background ones. `with lock:` is a foreground acquire (or a background one
    inside background_run()); acquire(background=..., timeout=...) -> True when held."""
    def __init__(self):
        self._c = threading.Condition()
        self._held = False
        self._fg_waiting = 0

    def acquire(self, background=None, timeout=None, *, clock=time.monotonic):
        background = in_background() if background is None else background
        end = None if timeout is None else clock() + timeout
        with self._c:
            if not background: self._fg_waiting += 1
            try:
                while self._held or (background and self._fg_waiting > 0):
                    left = None if end is None else end - clock()
                    if left is not None and left <= 0: return False
                    self._c.wait(left)
                self._held = True
                return True
            finally:
                if not background:
                    self._fg_waiting -= 1
                    self._c.notify_all()

    def release(self):
        with self._c:
            self._held = False
            self._c.notify_all()

    def waiting_foreground(self):
        with self._c: return self._fg_waiting

    def locked(self):
        with self._c: return self._held

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


LOCAL_MODEL_LOCK = PriorityLock()  # one local model on the GPU at a time across concurrent questions
