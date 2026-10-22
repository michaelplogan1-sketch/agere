"""End-to-end answer time budget (AGERE_ANSWER_BUDGET_SECONDS, default 120).

The cascade starts one budget per question and consults it between proposers. When time runs out it stops asking new
proposers and returns the best candidate that already executed, marked partial with what was not tried. A budget never
blocks an answer that is ready: it only decides whether to START more work. 0, 'off' or 'none' means no time limit.

    budget = answer_budget.start()            # per question; also becomes current() for code deeper in the call
    budget.remaining() -> seconds (inf when unlimited); budget.expired() -> bool
    budget.offer(candidate, score=..., label=...)   # an executed candidate that failed a later check
    budget.best() -> candidate | None;  budget.partial(interpretation) -> interpretation marked partial
"""
import contextvars
import math
import os
import time

DEFAULT_SECONDS = 120.0
VERSION = 'answer-time-budget/1'
_current = contextvars.ContextVar('agere_answer_budget', default=None)
_cancel = contextvars.ContextVar('agere_tier_cancel', default=None)      # threading.Event set when the running tier passed its time share
_deadline = contextvars.ContextVar('agere_tier_deadline', default=None)  # time.monotonic() by which the running tier's share ends


def cancelled():
    """True inside a tier the cascade has abandoned for passing its time share (loops stop; nothing is recorded)."""
    event = _cancel.get()
    return bool(event is not None and event.is_set())


def tier_remaining(default):
    """Seconds left in the running tier's share (capped at default); default when no share applies."""
    deadline = _deadline.get()
    return default if deadline is None else max(1.0, min(default, deadline - time.monotonic()))


def default_seconds():
    raw = os.environ.get('AGERE_ANSWER_BUDGET_SECONDS', '').strip().lower()
    if raw in ('', 'default'): return DEFAULT_SECONDS
    if raw in ('0', 'off', 'none', 'inf', 'unlimited'): return math.inf
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SECONDS  # a malformed setting heals to the default, never to "no answer"
    return value if math.isfinite(value) and value > 0 else (math.inf if value == math.inf else DEFAULT_SECONDS)


class AnswerBudget:
    def __init__(self, seconds=None, *, clock=time.monotonic):
        self.clock, self.seconds, self.started = clock, None, None
        self.candidates, self.skipped = [], []
        if seconds is not None: self.start(seconds)

    def start(self, seconds=None):
        """(Re)start the clock; seconds=None reads AGERE_ANSWER_BUDGET_SECONDS. Returns self."""
        self.seconds = default_seconds() if seconds is None else (math.inf if not seconds or seconds <= 0 else float(seconds))
        self.started = self.clock()
        self.candidates, self.skipped = [], []
        return self

    def elapsed(self):
        return 0.0 if self.started is None else self.clock() - self.started

    def remaining(self):
        if self.started is None or self.seconds == math.inf: return math.inf
        return max(0.0, self.seconds - self.elapsed())

    def expired(self):
        return self.started is not None and self.seconds != math.inf and self.elapsed() >= self.seconds

    def allows(self, estimate_seconds=0.0):
        """True when work expected to take estimate_seconds can still start (a zero estimate only needs time left)."""
        return not self.expired() and (estimate_seconds <= 0 or self.remaining() >= estimate_seconds)

    def skip(self, name):
        self.skipped.append(name)

    def offer(self, candidate, *, score=0.0, label=None, reasons=()):
        """Record an executed candidate (e.g. a literal-checked plan whose output shape check failed). Highest score wins;
        ties keep the earliest (cheapest, best-calibrated proposer first)."""
        self.candidates.append({'candidate': candidate, 'score': float(score), 'label': label, 'reasons': list(reasons),
                                'order': len(self.candidates)})

    def best_entry(self):
        return max(self.candidates, key=lambda c: (c['score'], -c['order'])) if self.candidates else None

    def best(self):
        entry = self.best_entry()
        return entry['candidate'] if entry else None

    def receipt(self):
        entry = self.best_entry()
        return {'schema': VERSION, 'budget_seconds': None if self.seconds in (None, math.inf) else self.seconds,
                'elapsed_seconds': round(self.elapsed(), 3), 'expired': self.expired(), 'not_tried': list(self.skipped),
                'candidates_offered': len(self.candidates), 'partial_from': entry['label'] if entry else None}

    def partial(self, interpretation):
        """Mark an interpretation receipt partial: the answer is the best executed candidate when time ran out."""
        entry = self.best_entry()
        note = (f"Answer time budget ({self.seconds:g}s) ran out; this is the best executed candidate"
                + (f" from {entry['label']}" if entry and entry['label'] else '')
                + (f" (open checks: {'; '.join(entry['reasons'])[:300]})" if entry and entry['reasons'] else '')
                + (f"; not tried: {', '.join(self.skipped)}" if self.skipped else '') + '.')
        return {**interpretation, 'partial': True, 'partial_reason': 'answer_time_budget', 'answer_budget': self.receipt(),
                'limitations': [*(interpretation.get('limitations') or []), note]}


def start(seconds=None, *, clock=time.monotonic):
    """A started budget for this question, also installed as current() for this thread/context."""
    budget = AnswerBudget(clock=clock).start(seconds)
    _current.set(budget)
    return budget


def current():
    """The budget started for the current question, or an unlimited (never-expiring) one."""
    return _current.get() or AnswerBudget(math.inf)


def remaining():
    return current().remaining()


def expired():
    return current().expired()
