"""Claude model policy (Mike 2026-10-23): the question path uses Haiku, escalating to Sonnet; Opus is reserved for major
deliverables (HTML, interactive and presentation outputs). Every question-path Claude request passes claude_model(), which
turns an Opus request into Sonnet unless the call runs inside deliverable() or AGERE_QUESTION_OPUS=on."""
import contextlib
import contextvars
import os

OPUS = 'claude-opus-5-5'
SONNET = 'claude-sonnet-5-5'
_DELIVERABLE = contextvars.ContextVar('agere_deliverable', default=False)


@contextlib.contextmanager
def deliverable():
    """Calls inside may use Opus (the deliverable composer)."""
    token = _DELIVERABLE.set(True)
    try:
        yield
    finally:
        _DELIVERABLE.reset(token)


def claude_model(model):
    if model == OPUS and not _DELIVERABLE.get() and os.environ.get('AGERE_QUESTION_OPUS', 'off') != 'on':
        return SONNET
    return model
