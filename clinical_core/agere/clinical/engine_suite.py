"""Decision engines registered on the planner menu from one place (wave-1 scorecard engines; wave-2 hospital operations).

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
    # wave 2: revenue cycle, payer, pharmacy and provider-network engines (shared helpers in revenue_common.py)
    'denial_prediction': 'denial_prediction',       # claim-level first-pass denial risk with CARC reason attribution, time-split validated
    'cdi_opportunity': 'cdi_opportunity',           # expected vs coded CC/MCC severity on inpatient claims from EHR evidence (CDI worklist)
    'contract_reimbursement': 'contract_reimbursement',  # expected reimbursement under observed or supplied contract terms; variance, repricing
    'price_benchmark': 'price_transparency',        # negotiated-rate benchmark across payers / providers (claims-derived or supplied TiC MRF)
    'prior_auth_rules': 'prior_auth_rules',         # prior-auth rule evaluation, auth-denial analytics, gold-card and auto-approval candidates
    'eligibility_340b': 'eligibility_340b',         # 340B patient/prescription eligibility and duplicate-discount / modifier compliance
    'ddi_screening': 'ddi_screening',               # concurrent-fill drug-drug interaction screen (cited rule table; System One review)
    'provider_network_graph': 'provider_network_graph',  # referral/billing graph: concentration, reciprocity, steering and kickback patterns
    'network_spread': 'network_spread',             # observed contagion + percolation/epidemic threshold of practice patterns on the sharing graph
    'upcoding_detection': 'upcoding_detection',     # upcoding detection (E/M, DRG severity, risk-adjustment coding) + incentive-compatibility design
    'benchmark_rebasing': 'benchmark_rebasing',     # MSSP/LEAD benchmark ratchet and rebasing scenarios ("should we renew early") from rulebooks
    'survey_adjustment': 'survey_adjustment',       # survey nonresponse raking, patient-mix adjustment, top-box, key drivers
    'scorecard_risk': 'scorecard_risk',             # auditable points-based (credit-scorecard style) risk models, line-by-line patient points
    # wave 2: hospital operations (shared readers in hospital_ops.py; visits from the governed visit_group table)
    'visit_groups': 'visit_groups',                 # visit / encounter grouper: one governed row per hospital visit with stage clocks
    'capacity_simulation': 'capacity_simulation',   # discrete-event simulation of ED boarding and inpatient beds, what-if scenarios
    'discharge_forecast': 'discharge_forecast',     # discharge-count / bed-day forecast from the in-house census + capacity twin
    'flow_decomposition': 'flow_decomposition',     # stage-level ED / inpatient interval decomposition, censoring-aware percentiles
    'or_block_optimization': 'or_block',            # OR block allocation from surgical claims (durations modelled, stated)
    'hai_surveillance': 'hai_surveillance',         # hospital-onset infection surveillance (NHSN day-3 window), rates and SPC
    'antimicrobial_use': 'antimicrobial_use',       # days of therapy per 1,000 days present, spectrum, IV-to-oral, de-escalation
    'bundle_compliance': 'bundle_compliance',       # SEP-1 all-or-none bundle compliance with element-level failure reasons
    'spatiotemporal_cluster': 'spatiotemporal_cluster',  # space-time permutation scan over ED syndromes by ZIP / county
    'overbooking_fairness': 'overbooking_fairness', # who bears overbooking burden by age band and payer (scheduling model replay)
    'network_adequacy': 'network_adequacy',         # specialty access per county / state: realized network, ratios, out-of-network
}
ROUTE_ORDER = ('saved_monitors', 'change_attribution', 'social_needs', 'star_rating', 'mlr', 'settlement_bridge',
               'bundle_compliance', 'hai_surveillance', 'antimicrobial_use', 'spatiotemporal_cluster', 'network_adequacy',
               'overbooking_fairness', 'or_block_optimization', 'discharge_forecast', 'capacity_simulation', 'flow_decomposition',
               'visit_groups', 'policy_search', 'outreach_bandit', 'outreach_allocation', 'staffing_plan',
               'benchmark_rebasing', 'eligibility_340b', 'ddi_screening', 'prior_auth_rules', 'cdi_opportunity', 'upcoding_detection',
               'denial_prediction', 'contract_reimbursement', 'price_benchmark', 'network_spread', 'provider_network_graph',
               'survey_adjustment', 'scorecard_risk')


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
