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

# Normal application entry uses the role selection exercised by the ordinary
# source-navigation workflow. Availability of installed experimental weights is
# separate from permission to load them. Exact execution reuse is unaffected.
ECONOMICAL_RUNTIME = {
    'AGERE_DOCUMENT_LOCAL_READERS': '',
    'AGERE_LOCAL_TYPED': 'off', 'AGERE_LOCAL_TYPED_FALLBACK': 'off',
    'AGERE_LOCAL_QWEN': 'off', 'AGERE_LOCAL_MODEL': 'off',
    'AGERE_LOCAL_SQL': 'off', 'AGERE_LOCAL_NORMALIZER': 'off',
    'AGERE_BOTTLED': 'off', 'AGERE_PROPOSER_ON': '',
    'AGERE_DOCUMENT_REVIEW': 'typed_spans', 'AGERE_DOCUMENT_RANKING': 'off',
    'AGERE_NOTE_NER': 'off', 'AGERE_THERAPY_BARRIERS_LLM': 'off',
    'AGERE_SYSTEM_ONE_STUDENT': 'on',
    # Designed order (Mike 2026-11-13): code, then System One choices, the System One spec path and
    # System One evidence tiers, and only then the Claude analyst as the backstop. 'adaptive'
    # (26118516, 2026-11-10) sent any question the free reuse tiers did not recognise straight to
    # the Claude analyst, so every one of 23 live runs on 2026-11-12/13 was answered by it.
    'AGERE_NONFRONTIER_FIRST': 'on',
    'AGERE_FRONTIER_AGGREGATE_REVIEW': 'off',
    'AGERE_FRONTIER_SOURCE_REVIEW': 'on',
    'AGERE_EXECUTION_WIRE_ENCODING': 'on',
    'AGERE_ANSWER_BUDGET_SECONDS': 'off', 'AGERE_BACKSTOP_SECONDS': 'off',
    # One ordinary path (Mike, 2026-11-12): native code first, System One next, the frontier
    # only for what remains. Recovery point before activation: commit 121dee40.
    'AGERE_TIERED_CORE': 'on',                    # questions try the tiered core first (clinical_core/agere/tiered)
    'AGERE_NATIVE_ROUTING': 'on',                 # follow-up facts go to the native loop-closure engine
    'AGERE_DOCUMENT_STRUCTURED_LINKS': 'on',      # reader links a note through its recorded author
    'AGERE_NATIVE_DOCUMENTARY_ABSENCE': 'on',     # bare empty documentary fields located by code
    'AGERE_NATIVE_DOCUMENTARY_COPY': 'on',        # exact copies of a document review proven by code
    'AGERE_PACKET_COMPOSE_CLOSURE': 'on',         # patient packets carry only their own dependencies
    'AGERE_SETTLED_PACKET_COMPOSE': 'on',         # settled rows are composed, not sent to the frontier
    'AGERE_FRONTIER_CLAIM_SCOPE': 'on',           # frontier sees only the unsettled claims
    'AGERE_FRONTIER_REVIEW_LEAD': 'off',          # rounds are bounded, so every row is checked before delivery (run 8 left 38 held back)
    'AGERE_SUBSCRIPTION_PREFIX_CACHE': 'on',      # analyst turns reuse the cached state prefix
}
APPLICATION_RUNTIME_KEYS = frozenset(ECONOMICAL_RUNTIME) | {
    'AGERE_CLAUDE', 'AGERE_ADAPTER_CANDIDATES', 'AGERE_SECOND_ENCODER',
    'AGERE_SECOND_ENCODER_CACHE', 'AGERE_LEARNED_ROUTER',
    'AGERE_RECURSIVE_IMPROVEMENT', 'AGERE_COMPONENT_LEARNING',
    'AGERE_COMPONENT_RELEASE_ROOT', 'AGERE_SYSTEM_ONE_DISTILL',
    'AGERE_SYSTEM_ONE_STUDENTS', 'AGERE_TRAINING_DIR', 'AGERE_CONTRACT_INPUTS',
    'AGERE_LEARNING_PROTECTION_PLAN', 'AGERE_LEARNING_PROTECTION_PLAN_SHA256',
    'AGERE_TYPED_LEARNING_LINEAGE',
    'AGERE_EXECUTION_TRACE', 'AGERE_EXECUTION_SQL_TRACE',
    'AGERE_LOCAL_INTERPRET_SECONDS', 'AGERE_QUANTITATIVE_ROLE_CHECK',
    'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY',
    'TOKENIZERS_PARALLELISM', 'AGERE_COMPONENT_LEARNING_PLAN',
    'AGERE_ANALYST_STATE', 'AGERE_DOCUMENT_CROSS_BATCH',
    'AGERE_DOCUMENT_FRONTIER', 'AGERE_DOCUMENT_TYPED_ORDER',
    'AGERE_BASE_MODEL', 'AGERE_ATOMIC_HOSTED_PLANNER',
    'AGERE_ATOMIC_MAX_PATIENTS', 'AGERE_ANALYST_CONTEXT', 'AGERE_SYSTEM_ONE_WORKERS',
}


def application_runtime(config, *, profile=None):
    """Pure, inspectable startup settings; credentials and process controls stay outside.

    The configured profile preserves explicit operator choices. Selecting it is
    required to opt into local generative/readers excluded by economical startup.
    Resource paths remain explicit configuration, rather than inferred from an
    installation layout or copied into another model/result store.
    """
    runtime = config.get('runtime', {})
    if not isinstance(runtime, dict) or set(runtime) - {'profile', 'environment'}:
        raise ValueError('runtime requires profile and an optional environment mapping')
    name = profile or runtime.get('profile', 'economical')
    if name not in ('economical', 'configured'):
        raise ValueError('runtime profile must be economical or configured')
    environment = runtime.get('environment', {})
    if not isinstance(environment, dict) or set(environment) - APPLICATION_RUNTIME_KEYS:
        raise ValueError('Unsupported runtime environment key; credentials and process controls are not runtime configuration')
    if any(not isinstance(v, str) or '\x00' in v for v in environment.values()):
        raise ValueError('Runtime environment values must be strings without null bytes')
    environment = dict(environment)
    if name == 'economical':
        environment.update(ECONOMICAL_RUNTIME)
    return {'profile': name, 'environment': environment}

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
