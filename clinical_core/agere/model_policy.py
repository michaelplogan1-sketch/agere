"""Claude model policy.

Mike 2026-10-23: the question path uses Haiku, escalating to Sonnet; Opus is reserved for major deliverables (HTML,
interactive and presentation outputs).
Mike 2026-10-24: "use Haiku in our stack before Sonnet". 93% of one night's spend was Sonnet because several tiers started
on it. There is now ONE question-path ladder, [Haiku, Sonnet], for every question-path role:

    planner, writer, reviewer, judge, reconcile, analyst, interpreter

  * Haiku is the first call of every role. Sonnet is used ONLY as the escalation: after a Haiku result failed validation or
    its checks, or the Haiku call itself errored. Never as a first call.
  * The cascade (clinical/proposers.py) runs every Claude tier on the Haiku rung first; only when each of them failed does it
    run the failed tiers again on the Sonnet rung (`with escalation():`), so a Sonnet call never precedes another tier's
    Haiku attempt for the same question. A tier that escalates inside itself (a failed review, a tie-break) does so under
    the same primitive.
  * Opus stays deliverable-only (`deliverable()`), or `AGERE_QUESTION_OPUS=on`.

Every question-path Claude request passes claude_model(): inside a question (question_path(), opened by the cascade and the
cost-telemetry probe) any Claude family model a caller names resolves to the CURRENT RUNG of the ladder (Haiku, or Sonnet
inside escalation()), so a call site that still names Sonnet or Opus can never spend more than the ladder allows.
Outside a question (offline tools, evals) an Opus request still becomes Sonnet and other models are kept as named.
AGERE_HAIKU_FIRST=off restores that older behaviour inside questions too; AGERE_ESCALATION=off removes the Sonnet rung."""
import contextlib
import contextvars
import os

HAIKU = 'claude-haiku-5-5'
SONNET = 'claude-sonnet-5-5'
OPUS = 'claude-opus-5-5'
FAMILY = (HAIKU, SONNET, OPUS)
ROLES = ('planner', 'writer', 'reviewer', 'judge', 'reconcile', 'analyst', 'interpreter')
LADDER = (HAIKU, SONNET)

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
    return (_QUESTION.get() or os.environ.get('AGERE_QUESTION_LADDER', '') == 'always') \
        and os.environ.get('AGERE_HAIKU_FIRST', 'on') != 'off'


def ladder(role='planner'):
    """The model ladder for a question-path role: [first, escalation] = [Haiku, Sonnet] (Sonnet only after a Haiku failure)."""
    if role not in ROLES: raise ValueError(f'unknown question-path role {role!r}; roles: {", ".join(ROLES)}')
    return list(LADDER)


def escalation_enabled():
    return os.environ.get('AGERE_ESCALATION', 'on') != 'off'


@contextlib.contextmanager
def escalation():
    """Calls inside run on the ladder's second rung (Sonnet): used only after a Haiku attempt failed."""
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
    """The model a question-path call of this role uses right now: Haiku, or Sonnet inside escalation()."""
    return ladder(role)[1 if escalating() else 0]


@contextlib.contextmanager
def tracking():
    """Collect the (resolved) models of every Claude request made inside, on this context (the cascade uses it to know which tiers
    spent a Haiku attempt)."""
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
    if _DELIVERABLE.get(): return model
    if in_question() and model in FAMILY: return model_for()
    if model == OPUS: return SONNET
    return model
