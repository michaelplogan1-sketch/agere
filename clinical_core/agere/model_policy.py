"""Sonnet entry / Opus escalation for frontier roles (Mike, 2026-11-06).

This supersedes the October Haiku-first preference. Existing native computation,
System One and qualified local models retain their roles. Every question-path
Claude request resolves to the current rung; a distinct nested role starts on
Sonnet and may escalate after its own failed attempt. The cascade retries only
tiers that actually attempted frontier work and failed. AGERE_ESCALATION=off
disables escalation; AGERE_QUESTION_OPUS=on explicitly pins an Opus evaluation.

Outside questions, named Sonnet/Opus requests remain as named. Legacy Haiku
requests resolve to Sonnet, including old deliverable call sites. The obsolete
AGERE_HAIKU_FIRST switch cannot re-enable Haiku or bypass the current ladder.
"""
import contextlib
import contextvars
import os

HAIKU = 'claude-haiku-5-5'
SONNET = 'claude-sonnet-5-5'
OPUS = 'claude-opus-5-5'
FAMILY = (HAIKU, SONNET, OPUS)
ROLES = ('planner', 'writer', 'reviewer', 'judge', 'reconcile', 'analyst', 'interpreter')
LADDER = (SONNET, OPUS)

_DELIVERABLE = contextvars.ContextVar('agere_deliverable', default=False)
_QUESTION = contextvars.ContextVar('agere_question_path', default=False)
_RUNG = contextvars.ContextVar('agere_ladder_rung', default=0)
_TRACK = contextvars.ContextVar('agere_ladder_track', default=None)


@contextlib.contextmanager
def deliverable():
    """Calls inside may use Opus (the deliverable composer)."""
    token = _DELIVERABLE.set(True)
    try:
        yield
    finally:
        _DELIVERABLE.reset(token)


@contextlib.contextmanager
def question_path():
    """Calls inside belong to one question: Claude requests resolve to the ladder's current rung."""
    token = _QUESTION.set(True)
    try:
        yield
    finally:
        _QUESTION.reset(token)


def mark_question():
    """Open the question scope without a with-block (the cost probe). -> token for unmark_question."""
    return _QUESTION.set(True)


def unmark_question(token):
    try:
        _QUESTION.reset(token)
    except ValueError:  # finished from another context: the scope simply ends with that context
        pass


def in_question():
    return _QUESTION.get() or os.environ.get('AGERE_QUESTION_LADDER', '') == 'always'


def ladder(role='planner'):
    """The frontier models for a role: entry Sonnet, escalation Opus."""
    if role not in ROLES: raise ValueError(f'unknown question-path role {role!r}; roles: {", ".join(ROLES)}')
    return list(LADDER)


def escalation_enabled():
    return os.environ.get('AGERE_ESCALATION', 'on') != 'off'


@contextlib.contextmanager
def first_rung():
    """A distinct role starts cheaply even when called by an escalated role."""
    token = _RUNG.set(0)
    try:
        yield
    finally:
        _RUNG.reset(token)


@contextlib.contextmanager
def escalation():
    """Use Opus after this role's entry attempt failed, when escalation is enabled."""
    token = _RUNG.set(1 if escalation_enabled() else 0)
    try:
        yield
    finally:
        _RUNG.reset(token)


def escalating():
    return _RUNG.get() > 0


def first(role='planner'):
    return ladder(role)[0]


def model_for(role='planner'):
    """The model a question-path call of this role uses at the current rung."""
    return ladder(role)[1 if escalating() else 0]


@contextlib.contextmanager
def tracking():
    """Collect the (resolved) models of every Claude request made inside, on this context (the cascade uses it to know which tiers
    spent a frontier attempt)."""
    seen = []
    token = _TRACK.set(seen)
    try:
        yield seen
    finally:
        _TRACK.reset(token)


def record(model):
    """Note that a Claude request was made with this model (for callers whose model object is not the shared sender)."""
    seen = _TRACK.get()
    if seen is not None: seen.append(model)


def claude_model(model, *, track=False):
    """The model a Claude request actually uses. track=True records it for tracking() (the send/classify request points)."""
    out = _resolve(model)
    if track: record(out)
    return out


def _resolve(model):
    if model == OPUS and os.environ.get('AGERE_QUESTION_OPUS', 'off') == 'on': return model
    if _DELIVERABLE.get(): return SONNET if model == HAIKU else model
    if in_question() and model in FAMILY: return model_for()
    if model == HAIKU: return SONNET
    return model
