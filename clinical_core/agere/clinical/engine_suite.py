"""Decision engines registered on the planner menu from one place (wave-1 scorecard engines).

Each engine module declares its own menu entry and contract, so the shared hot files carry one hook each:
  OP                      operation name (analysis_graph operation, PRODUCT key, CONTRACTS key, graph_contracts TYPED)
  PRODUCT                 capability_registry entry {'population', 'view', 'use'}
  CONTRACT                graph_planner contract {'inputs', 'parameters', 'meaning'}
  NATIVE                  requested answer shapes the engine natively answers beyond table/patient_set
  schema(obj, string, array) -> graph_contracts parameter schema (typed, closed)
  validate(p) -> p        runtime validator (authoritative)
  execute(path_or_stack, patient_ids, params, as_of) -> report     (NEEDS_STACK = True: receives the Stack)
  answer(report, plan, question) -> answer fields for analysis_graph.build_answer_core
  route(question, profile, as_of) -> (graph, info) | None          free local-compiler route for regular phrasings
Engines read only synthetic warehouse data and state their assumptions in every answer.
"""
import importlib

VERSION = 'engine-suite/1'
# op -> module (clinical_core.agere.clinical.<module>)
MODULES = {
    'outreach_allocation': 'outreach_allocation',   # capacity-aware MILP allocation of outreach / slots (scipy HiGHS)
    'outreach_bandit': 'outreach_learning',         # Thompson-sampling bandit over outreach arms from logged outcomes
    'policy_search': 'policy_search',               # evolutionary outreach-policy search against the population simulator
    'staffing_plan': 'staffing_plan',               # demand forecast -> Erlang requirement -> shift MILP staffing decision
    'mlr': 'mlr',                                   # medical loss ratio by line of business, rebate, credibility
    'star_rating': 'star_ratings',                  # Medicare Star projection vs cut points, QBP
    'settlement_bridge': 'settlement_bridge',       # work -> measure -> settlement bridge, forecast/modeled/realized, deduped
    'change_attribution': 'change_attribution',     # joint patient-state / source-observation tensor model: real change vs feed outage
    'social_needs': 'social_needs',                 # corroborated social needs + referral (BSeR/Gravity) closure
    'saved_monitors': 'monitor_sweep',              # saved-answer monitors recomputed on new snapshots
    'care_gap_closure': 'care_gap_closure',         # payer care gaps (DEQM open/closed gap per member and measure) + closing actions
    'transitions_of_care': 'transitions_of_care',   # discharge follow-up (TEAM/TCM): visit, contact, med reconciliation, readmission
    'prior_auth_access': 'prior_auth_access',       # prior authorization / non-coverage rejects: resolve, appeal, Extra Help screen
}
ROUTE_ORDER = ('saved_monitors', 'change_attribution', 'social_needs', 'star_rating', 'mlr', 'settlement_bridge',
               'policy_search', 'outreach_bandit', 'outreach_allocation', 'staffing_plan',
               'prior_auth_access', 'transitions_of_care', 'care_gap_closure')


def module(op):
    return importlib.import_module(f'.{MODULES[op]}', __package__)


def available():
    """Modules that import (a missing optional dependency drops only that engine)."""
    out = {}
    for op in MODULES:
        try:
            out[op] = module(op)
        except ImportError:
            continue
    return out


def product():
    return {op: dict(m.PRODUCT) for op, m in available().items()}


def contracts():
    return {op: dict(m.CONTRACT) for op, m in available().items()}


def native_scalars():
    return {op: tuple(getattr(m, 'NATIVE', ())) for op, m in available().items()}


def parameter_schema(op, obj, string, array):
    return module(op).schema(obj, string, array)


def needs_stack(op):
    return bool(getattr(module(op), 'NEEDS_STACK', False))


def code_paths():
    from pathlib import Path
    here = Path(__file__)
    return [here, *[here.with_name(f'{m}.py') for m in MODULES.values()]]


def compile_route(question, profile, as_of):
    """First engine route that claims the question -> (graph, info); None when no engine phrasing matches."""
    for op in ROUTE_ORDER:
        m = available().get(op)
        if m is None or not hasattr(m, 'route'): continue
        try:
            out = m.route(question, profile, as_of)
        except (ValueError, KeyError, TypeError):
            continue  # a route that cannot parse declines; the next tier answers
        if out is not None:
            return out
    return None


def plan(op, params, purpose, question, recognised=()):
    """One-node typed plan for a route (validated by analysis_graph.validate downstream)."""
    info = {'compiler': VERSION, 'recognised': [f'{op} question', *recognised], 'paraphrased': [], 'route': 'local_compiler',
            'unexplained': [], 'operation': op}
    return {'interpretation': question, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': op, 'inputs': [], 'parameters': params, 'purpose': purpose}]}, info
