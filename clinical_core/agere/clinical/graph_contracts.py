"""Shared graph types for planning and execution; semantic validators stay local.

Typed planning contracts cover the measured source-review path. Other operators
retain their explicit legacy JSON wire contract, never an invented generic type.
The runtime validators remain authoritative for dates, bounds and source meaning.
"""
import copy

VERSION = 'graph-operator-contracts/20'
SET_OPS = {'cohort', 'query_population', 'evidence', 'intersect', 'union', 'difference', 'sequence',
           'interpretation', 'source_state', 'pre_event_state', 'requirements', 'outcome',
           'protocol_review', 'program_document', 'functional_review', 'domain_review', 'appointment_followthrough'}
WORK_SUPPORT = ('accountability_review', 'program_acquisition', 'measure_review')
TYPED = {'aggregate', 'calibration', 'explain', 'sql', 'vbc', 'monitor', 'demand', 'program_effect', 'outreach', 'discover', 'trajectory', 'survival', 'observation_process', 'reference_review', 'cohort', 'evidence', 'intersect', 'union', 'difference', 'compare', 'medication_identity',
         'assertion_review', 'source_state', 'pre_event_state', 'context', 'context_passes', 'functional_review',
         'domain_review', 'worklist', 'program_acquisition', 'retrieve', 'forecast',
         'source_changes', 'review', 'measure_review', 'financial_accounts', 'accountability_review', 'outcome', 'appointment_followthrough', 'interpretation', 'requirements', 'protocol_review', 'program_document'}
TYPED |= {'governed_metric'}
TYPED |= {'market'}
OPTIONAL_POPULATION = ('market',)  # report operators that take zero or one population input
TYPED |= {'pathway'}
TYPED = TYPED | {'profile'}
TYPED |= {'scheduling'}
OPTIONAL_POPULATION += ('scheduling',)
TYPED |= {'uplift'}  # heterogeneous treatment effect / who benefits (uplift.py)
OPTIONAL_POPULATION += ('uplift',)
TYPED |= {'queueing'}  # queueing-theory staffing / bed sizing (queueing.py)
OPTIONAL_POPULATION += ('queueing',)
TYPED |= {'briefing'}
TYPED |= {'early_signal'}
OPTIONAL_POPULATION += ('early_signal',)
TYPED |= {'claims_review'}  # payment integrity and revenue leakage (claims_review.py)
OPTIONAL_POPULATION += ('claims_review',)
TYPED |= {'knowledge'}  # cited VBC knowledge base (vbc_knowledge.py); takes no population
TYPED |= {'risk_adjustment'}  # CMS-HCC RAF, coding gaps, recapture, suspects (risk_adjustment.py)
OPTIONAL_POPULATION += ('risk_adjustment',)
TYPED |= {'network'}  # provider patient-sharing network (network.py)
OPTIONAL_POPULATION += ('network',)
TYPED |= {'contract_strategy'}  # risk contracts as options, track selection, Nash negotiation (contract_strategy.py)
OPTIONAL_POPULATION += ('contract_strategy',)
TYPED |= {'target_trial', 'line_of_therapy', 'trial_feasibility'}  # life-sciences / RWE engines
OPTIONAL_POPULATION += ('target_trial', 'line_of_therapy', 'trial_feasibility')
TYPED |= {'loop_closure', 'diagnostic_delay', 'risk_outcomes'}  # quality & safety engines (loop_closure.py, diagnostic_delay.py, risk_outcomes.py)
OPTIONAL_POPULATION += ('loop_closure', 'diagnostic_delay', 'risk_outcomes')
TYPED |= {'medication_state', 'default_definition'}  # clinical logic: medication_state.py, default_definitions.py
OPTIONAL_POPULATION += ('medication_state', 'default_definition')
from .engine_suite import MODULES as _SUITE  # wave-1 decision engines (engine_suite.py); zero or one population input
TYPED |= set(_SUITE)
OPTIONAL_POPULATION += tuple(_SUITE)
TYPED |= {'atomic_questions'}  # atomic typed sub-questions via System One (atomic_questions.py); one population input


def _library_measures():
    """Measure-library ids and set names (config/agere/measures), for the vbc quality schema."""
    try:
        from . import measures
        return [*measures.library(), *measures.measure_sets()]
    except (OSError, ValueError):
        return []


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def string(values=None, description=None):
    result = {'type': 'string'}
    if values is not None:
        values=sorted(set(values))
        if values:result['enum'] = values
        else:result['description']='No registered value supplied; leave this operation unused and declare the requirement unmet.'
    if description:
        result['description'] = description
    return result


def array(items, description=None):
    result = {'type': 'array', 'items': items}
    if description:
        result['description'] = description
    return result


def input_contract(op):
    if op == 'knowledge':  # explanation from the cited knowledge base; never a population
        return {'count': [0, 0], 'type': 'membership', 'output': 'report'}
    if op == 'briefing':  # whole-population briefing; takes no population
        return {'count': [0, 0], 'type': 'membership', 'output': 'report'}
    if op == 'pathway':  # optional population; no population = everyone
        return {'count': [0, 1], 'type': 'membership', 'output': 'report'}
    lo, hi = ((0, 0) if op in ('cohort', 'query_population', 'evidence') else
              (2, 2) if op == 'difference' else
              (2, 10) if op in ('intersect', 'union') else
              (0, 1) if op in ('sql', 'vbc', 'program_effect', 'outreach', 'monitor', 'demand', 'profile', 'calibration') + OPTIONAL_POPULATION else (1, 10) if op == 'reference_review' else (3, 10) if op == 'compare' else (1, 4) if op == 'worklist' else (1, 1))
    result = {'count': [lo, hi], 'type': 'membership',
              'output': 'membership' if op in SET_OPS else 'report'}
    if op in ('baseline', 'pre_event_state'): result['first_operation'] = 'outcome'
    if op == 'worklist': result['optional_support_reports'] = list(WORK_SUPPORT)
    if op == 'reference_review':
        from .graph_references import EVIDENCE_OPERATIONS
        result['optional_evidence_inputs'] = list(EVIDENCE_OPERATIONS)
    return result


def validate_inputs(op, inputs, seen):
    contract = input_contract(op)
    from .graph_references import EVIDENCE_OPERATIONS
    if any(x not in seen or (seen[x] not in SET_OPS and not
           (op == 'reference_review' and i >= 1 and seen[x] in EVIDENCE_OPERATIONS) and not
           (op == 'worklist' and i >= 1 and seen[x] in WORK_SUPPORT)) for i, x in enumerate(inputs)):
        raise ValueError('Only earlier membership nodes may be inputs; no cycles or result-as-population coercion')
    lo, hi = contract['count']
    if not lo <= len(inputs) <= hi:
        raise ValueError(f'{op} requires {lo}..{hi} typed inputs')
    if op in ('baseline', 'pre_event_state') and seen[inputs[0]] != 'outcome':
        raise ValueError('Baseline/pre-event state requires a direct upstream outcome membership')
    if op == 'reference_review' and any(seen[x] not in EVIDENCE_OPERATIONS for x in inputs[1:]):
        raise ValueError('Reference inputs after the population must be supported evidence receipts')
    if op == 'worklist':
        support = [seen[x] for x in inputs[1:]]
        if any(x not in WORK_SUPPORT for x in support) or len(set(support)) != len(support):
            raise ValueError('Worklist support inputs must be distinct registered review reports')


def parameter_schema(op, catalog):
    """Selected vocabulary shapes, derived from actual runtime registries."""
    from . import cohorts, source_state, functional_programs, domain_programs
    from .clinical_events import WINDOW_SCHEMA
    streams = sorted(catalog['cohort_fields']['record_fields'])
    evidence = catalog['evidence_catalog']
    stream_list = array(string(catalog.get('source_streams', streams)), '1..30 distinct explicitly loaded source streams; never widen scope.')
    stream_list['minItems']=1
    if op == 'aggregate':
        from . import aggregate, concepts as library
        return obj({'measure': string(aggregate.STATISTICS),
            'concept': string(['', *library.CONDITIONS, *library.MEASUREMENTS, *library.ENCOUNTERS],
                              'What is counted/averaged. patients: any concept or "" (population size); events: encounter or condition concept; value: measurement concept.'),
            'statistic': string(sorted({s for v in aggregate.STATISTICS.values() for s in v}),
                                'patients: count|share; events: count|rate_per_1000; value: mean|median'),
            'group_by': string(aggregate.GROUP_BY), 'adjust': string(aggregate.ADJUSTMENTS, 'Case-mix standardisation for counts/shares'),
            'window': obj({'start': {'type': 'string', 'description': 'YYYY-MM-DD or empty'}, 'end': {'type': 'string', 'description': 'YYYY-MM-DD or empty'}}),
            'value_aggregate': string(['latest', 'all']), 'top': {'type': 'integer', 'minimum': 1, 'maximum': 200}})
    if op == 'trajectory':
        from . import trajectory
        return obj({'concept': string(trajectory.NOISE), 'direction': string(['worse', 'better', 'up', 'down', 'any']),
                    'min_points': {'type': 'integer', 'minimum': 3, 'maximum': 50}, 'top': {'type': 'integer', 'minimum': 1, 'maximum': 200}})
    if op == 'survival':
        from . import survival, concepts as library
        return obj({'index': obj({'type': string(survival.INDEX_TYPES, 'Time zero: discharge (each stay with a received discharge), '
                                                 'condition_first_record (first dated record of concept), date (fixed date)'),
                                  'concept': string(['', *library.CONDITIONS], 'condition_first_record only'),
                                  'date': {'type': 'string', 'description': 'YYYY-MM-DD for date index, else empty'},
                                  'window': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}})}),
                    'event': obj({'type': string(survival.EVENT_TYPES, 'readmission/followup need a discharge index'),
                                  'concept': string(['', *library.ENCOUNTERS, *library.MEASUREMENTS], 'encounter or measurement concept'),
                                  'op': string(['', *survival.OPS], 'measurement threshold comparison'), 'value': {'type': 'number'}}),
                    'horizon_days': {'type': 'integer', 'minimum': 1, 'maximum': 3650},
                    'time_points': array({'type': 'integer'}, '1..12 days within the horizon'),
                    'group_by': string(survival.GROUP_BY), 'group_concept': string(['', *library.CONDITIONS], 'group_by condition only'),
                    'cox': {'type': 'boolean'}, 'covariates': array(string([*survival.COVARIATES, *library.CONDITIONS]), 'Cox covariates; empty for defaults'),
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 200}})
    if op == 'discover':
        from . import discover
        spec = parameter_schema('cohort', catalog)['properties']['specification']
        return obj({'outcome': spec, 'segments': array(string(discover.SEGMENTS)), 'pairs': {'type': 'boolean'},
                    'direction': string(['high', 'low', 'both'])})
    if op == 'governed_metric':
        from .request_vocabulary import definitions, GROUPS
        return obj({'id': string(definitions()), 'version': {'type': 'integer'},
                    'definition_digest': string(), 'period': obj({'start': string(), 'end': string()}),
                    'group_by': array(string(GROUPS))})
    if op == 'vbc':
        from . import vbc
        return obj({'metric': string(vbc.METRICS), 'group_by': string(vbc.GROUP_BY),
                    'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                    'measure': string(['', 'diabetes_a1c_poor_control', 'bp_control', 'statin_therapy_diabetes', *_library_measures()],
                                      'quality only: internal measure, a measure-library id (config/agere/measures) or a measure set'),
                    'output': string(['rates', 'gaps'], 'quality library measures: rates (default) or the care-gap patient list'),
                    'attribution': string(['hybrid', 'prospective', 'plurality']),
                    'terms': {'type': 'object', 'description': 'shared_savings only: benchmark_pmpm, sharing_rate, msr, mlr, quality_score, two_sided, loss_rate, savings_cap_pct, loss_cap_pct, loss_cap_revenue_pct, participant_revenue, risk_adjust. Rates are fractions 0..1. Downside requires explicit loss_rate or a cited program. A revenue cap requires participant_revenue. Never infer missing contract terms.'},
                    'program': {'type': 'string', 'description': 'shared_savings only: a named program whose cited rulebook terms apply, e.g. "MSSP ENHANCED 2026", '
                                '"MSSP BASIC level E", "ACO REACH Global 2026", "LEAD Professional 2027"; "" when none is named. Terms stated in the question still go in terms and override.'}})
    if op == 'program_effect':
        from . import program_effect, concepts as library
        from .drug_classes import CLASSES
        return obj({'program': string([*program_effect.PROGRAMS, 'medication_start'], 'Exposure identified from received records; never an unnamed program'),
                    'drug_class': string(['', *CLASSES], 'medication_start only; "" otherwise'),
                    'outcome': string(program_effect.OUTCOMES), 'lab': string(['', *library.MEASUREMENTS], 'lab outcome only; "" otherwise'),
                    'pre_days': {'type': 'integer', 'minimum': 30, 'maximum': 730}, 'post_days': {'type': 'integer', 'minimum': 14, 'maximum': 365},
                    'method': string(program_effect.METHODS, 'matched_did (default); sdid = synthetic difference-in-differences; callaway_santanna = staggered adoption DiD; auto chooses by how spread out adoption dates are'),
                    'control_group': string(program_effect.CONTROL_GROUPS, 'callaway_santanna comparison units: never_treated (default) or not_yet_treated'),
                    'estimator': string(program_effect.CS_ESTIMATORS, 'callaway_santanna estimator (default doubly_robust)'),
                    'covariates': array(string(program_effect.PANEL_COVARIATES), 'callaway_santanna conditional parallel-trends covariates; [] = unconditional')})
    if op == 'outreach':
        from . import outreach
        return obj({'capacity_patients': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 5000, 'description': 'Outreach slots (patients).'},
                    'capacity_hours': {'type': ['number', 'null'], 'minimum': 0.25, 'maximum': 2000, 'description': 'Nurse/care-manager hours; tasks carry minute estimates.'},
                    'require': array(string(outreach.CATEGORIES), 'Only patients with at least one reason in these categories; [] = any reason.'),
                    'group_by': string(outreach.GROUP_BY), 'min_per_practice': {'type': 'integer', 'minimum': 0, 'maximum': 1000},
                    'max_per_practice': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 5000},
                    'method': string(outreach.METHODS), 'overlap_discount': {'type': 'number', 'minimum': 0, 'maximum': 1},
                    'saturation': {'type': 'number', 'minimum': 0, 'maximum': 1000, 'description': '0 = additive; >0 = diminishing returns per practice x reason category'},
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 5000}})
    if op == 'early_signal':
        from . import early_signal
        return obj({'outcome': string(early_signal.OUTCOMES, 'acute event the precursor leads to; acute_care = ED visit or admission'),
                    'window_months': {'type': 'integer', 'minimum': 3, 'maximum': 12, 'description': 'pre-event template length (default 6)'},
                    'horizon_months': {'type': 'integer', 'minimum': 1, 'maximum': 6, 'description': 'how far ahead the event may fall (default 3)'},
                    'false_alarm_rate': {'type': 'number', 'minimum': 0.001, 'maximum': 0.5, 'description': 'default 0.05'},
                    'train_cutoff': {'type': 'string', 'description': 'YYYY-MM time-split cutoff or "" (automatic)'},
                    'channels': array(string(early_signal.CHANNELS), 'subset of channels; empty = all'),
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 200}})
    if op == 'profile':
        from . import profile
        return obj({'metric': string(profile.METRICS, 'What is compared across units (rates per 1,000 member-years, measure shares, or PMPM)'),
                    'unit': string(profile.UNITS, 'Profiled unit: attributed PCP or practice'),
                    'adjust': string(profile.ADJUSTMENTS, 'Case mix: auto = age_sex for rates/readmissions/PMPM, none for quality shares'),
                    'method': string(profile.METHODS, 'auto/bayes = hierarchical Bayes posterior drives flags and ranks; credibility = Buhlmann-Straub'),
                    'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                    'threshold': {'type': ['number', 'null'], 'description': 'Optional stated threshold in display units (per 1,000, share 0..1 or percent, $ PMPM)'},
                    'focus': {'type': 'string', 'description': 'One unit id the question asks about (Dr X / practice X), else empty'},
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})
    if op == 'uplift':
        from . import uplift, program_effect
        n = lambda lo, hi, d: {'type': ['number', 'null'], 'minimum': lo, 'maximum': hi, 'description': d}
        return obj({'program': string(list(program_effect.PROGRAMS), 'Program whose benefit is estimated (outreach / care management = care_management)'),
                    'outcome': string(uplift.OUTCOMES, 'Event the program should reduce; ed_or_admission when not specific'),
                    'outcome_type': string(uplift.OUTCOME_TYPES, 'binary = any event (default); count = number of events'),
                    'pre_days': {'type': 'integer', 'minimum': 30, 'maximum': 730, 'description': 'Covariate window before the index date (default 180)'},
                    'post_days': {'type': 'integer', 'minimum': 14, 'maximum': 365, 'description': 'Outcome window after the index date (default 90)'},
                    'capacity_patients': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 5000, 'description': 'Outreach slots (patients), only if stated'},
                    'capacity_hours': n(0.25, 2000, 'Nurse / care-manager hours, only if stated'),
                    'minutes_per_contact': {'type': 'integer', 'minimum': 1, 'maximum': 480, 'description': 'Minutes per contact (default 30)'},
                    'value_per_event_usd': n(0, 1e7, 'Stated value of one avoided event in dollars; null = the outreach planning assumption'),
                    'cost_per_contact_usd': n(0, 1e6, 'Stated cost of one contact in dollars (default 0)'),
                    'learners': string(['both', *uplift.LEARNERS], 'Meta-learners to fit; x = X-learner, t = T-learner'),
                    'primary_learner': string(list(uplift.LEARNERS), 'Learner whose ranking drives targeting (default x)'),
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 5000, 'description': 'Members listed (default 200)'}})
    if op == 'queueing':
        from . import queueing
        n = lambda lo, hi, d: {'type': ['number', 'null'], 'minimum': lo, 'maximum': hi, 'description': d}
        return obj({'source': string(queueing.SOURCES, 'ed = emergency visits, clinic = outpatient visits, appointment = kept appointments, beds = hospital stays (arrival = admission, service = length of stay); default ed'),
                    'organization_id': {'type': 'string', 'description': 'One organisation the question names, else empty'},
                    'window': obj({'start': string(description='Inclusive arrival date YYYY-MM-DD, or empty for available history'),
                                   'end': string(description='Inclusive arrival date YYYY-MM-DD, or empty for snapshot date; cannot exceed snapshot')}),
                    'objective': string(queueing.OBJECTIVES, 'wait: P(wait > threshold) <= target (staff); blocking: Erlang B blocking <= target (beds)'),
                    'wait_threshold_minutes': n(0, 1e5, 'Wait the patient must not exceed, in minutes (default 30)'),
                    'target_wait_probability': n(1e-6, 1, 'Allowed chance of exceeding the threshold, 0..1 (default 0.10)'),
                    'target_blocking': n(1e-9, 1, 'Allowed blocking probability for beds, 0..1 (default 0.02)'),
                    'abandonment_rate_per_hour': n(0, 1e4, 'Patients leaving the queue per hour of waiting (Erlang A), only if stated'),
                    'servers': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 5000, 'description': 'Staff or beds on hand when the question asks about a stated number, else null'},
                    'mean_service_minutes': n(0.01, 1e6, 'Stated mean service time or length of stay in minutes; null to estimate from visit start/end times'),
                    'demand_forecast': {'type': 'boolean', 'description': 'Automatic next-week ED forecast uses the same cohort and snapshot; organization/window restrictions or other event definitions remain unresolved'},
                    'arrival_scale': n(0, 100, 'Multiplier on arrivals, e.g. 1.1 for +10%'),
                    'sensitivity_pct': n(0, 100, 'Arrival sensitivity band in percent (default 10)')})
    if op == 'scheduling':
        from . import scheduling
        n = lambda lo, hi, d: {'type': ['number', 'null'], 'minimum': lo, 'maximum': hi, 'description': d}
        return obj({'focus': string(scheduling.FOCUS, 'overbooking: extra bookings per session; no_show_risk: ranked upcoming appointments; '
                                                      'access: lead times; reminders: who to call; all: everything'),
                    'practice': {'type': 'string', 'description': 'One scheduling organisation the question names (e.g. "3" or WH-PRACTICE-3), else empty'},
                    'horizon_days': {'type': 'integer', 'minimum': 1, 'maximum': 120, 'description': 'Upcoming window: tomorrow = 1, next week = 7 (default 14)'},
                    'value_per_visit': n(0.01, 1e6, 'Value of a served visit, only if stated (default 1)'),
                    'overflow_cost': n(0, 1e6, 'Cost per patient beyond capacity, same units, only if stated (default 2)'),
                    'reminder_effect': n(0, 1, 'Share of targeted no-shows a reminder prevents, only if stated (default 0.3)'),
                    'reminder_capacity': {'type': 'integer', 'minimum': 0, 'maximum': 5000, 'description': 'Reminder calls available (default 25)'},
                    'capacity': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 200, 'description': 'Slots per session, only if stated'},
                    'max_overflow_probability': n(0, 1, '"safely": cap on P(more shows than slots), e.g. 0.1'),
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 1000}})
    if op == 'calibration':
        from . import calibration, trajectory
        return obj({'target': string(calibration.TARGETS, 'all = every prediction type; forecasts = Kalman lab/vital forecasts'),
                    'concept': string(['', *sorted(trajectory.NOISE)], 'One forecast concept to check; "" = the outreach rising-risk set'),
                    'threshold': {'type': ['number', 'null'], 'description': 'Crossing threshold for concept'},
                    'direction': string(['below', 'above']), 'horizon_days': {'type': 'integer', 'minimum': 30, 'maximum': 730},
                    'level': {'type': 'number', 'minimum': 0.5, 'maximum': 0.99, 'description': 'Nominal interval coverage (default 0.9)'}})
    if op == 'network':
        from . import network as nw
        return obj({'focus': string(nw.FOCUS, 'usage: which organisations/specialists patients use; hubs: centrality; communities; pcp_sharing; cohesion: '
                                              'care density; concentration: referral HHI; leakage: outside-network share by specialty/practice; '
                                              'steering: reassign leaked visits (needs steer_specialty); spread: diffusion model over PCPs; all'),
                    'level': string(nw.LEVELS), 'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                    'min_shared': {'type': 'integer', 'minimum': 1, 'maximum': 100}, 'filter': string(nw.FILTERS),
                    'leakage': string(nw.LEAKAGE, 'organization = OUTSIDE organisations (pathway definition); tins = outside network_tins'),
                    'network_tins': array({'type': 'string'}, 'In-network TINs, only if the user lists them'),
                    'group_by': string(nw.GROUP_BY), 'specialty': {'type': 'string', 'description': 'Service line filter, e.g. cardiology; else empty'},
                    'steer_specialty': {'type': 'string', 'description': 'Service line to steer, e.g. cardiology'},
                    'steer_to': {'type': 'string', 'description': 'Target organisation id or name; empty = busiest in-network'},
                    'steer_share': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'steer_from': string(nw.STEER_FROM),
                    'spread_model': string(nw.SPREAD_MODELS), 'seeds': {'type': 'integer', 'minimum': 1, 'maximum': 10},
                    'transmission': {'type': 'number', 'minimum': 0.001, 'maximum': 1}, 'steps': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 200}})
    if op in _SUITE:
        from . import engine_suite
        return engine_suite.parameter_schema(op, obj, string, array)
    if op == 'medication_state':
        from . import medication_state as ms
        return obj({'subjects': array(string(None, 'gdmt (all four pillars), a pillar (' + ', '.join(ms.PILLARS) + ') or an exact drug class'),
                                      'What to classify; default gdmt.'),
                    'states': array(string(ms.STATES), 'Select patients with any subject in these states; empty = every evaluated patient.'),
                    'output': string(ms.OUTPUTS, 'patients: one row per patient with a state column per subject; summary: counts; rows: patient x subject'),
                    'grace_days': {'type': 'integer', 'minimum': 0, 'maximum': 365, 'description': f'Fill-coverage grace (default {ms.GRACE_DAYS})'}})
    if op == 'atomic_questions':
        from . import atomic_questions as aq
        option = {'type': 'object', 'additionalProperties': {'type': 'string'}, 'description': "label -> description; must include 'unknown'"}
        sub = obj({'key': string(None, 'snake_case id'), 'atom': string(None, 'One question about ONE patient'), 'options': option,
                   'subject': string(None, 'medication_state subject (pillar or drug class) or empty'), 'task_key': string(None, 'calibration key')})
        return obj({'question': string(None, 'The original question verbatim'), 'sub_questions': array(sub, '1-8 atomic typed sub-questions'),
                    'sub_question_count': {'type': 'integer', 'minimum': 1, 'maximum': 8},
                    'aggregation': string(aq.AGGREGATIONS), 'returns': string(aq.RETURNS), 'unit': string(aq.UNITS),
                    'select_labels': array(string(), 'Option labels that select a patient'),
                    'maximum_patients': {'type': 'integer', 'minimum': 1, 'maximum': aq.HARD_MAX_PATIENTS},
                    'window': WINDOW_SCHEMA, 'sources': array(string(aq.SOURCES)), 'providers': array(string(aq.PROVIDERS))})
    if op == 'default_definition':
        from . import default_definitions as dd
        return obj({'definition': string(sorted(dd.DEFINITIONS), 'Named standard used when the question states no definition: '
                                         + '; '.join(f"{k} = {v['name']}" for k, v in dd.DEFINITIONS.items())),
                    'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                    'measure': string(None, 'measure_gap: a patient-unit measure-library id; else empty'),
                    'drug_classes': array(string(), 'pdc_adherence: drug classes; else empty'),
                    'threshold': {'type': 'number', 'minimum': 0.01, 'maximum': 1, 'description': f'PDC threshold (default {dd.PDC_THRESHOLD})'},
                    'group_by': string(dd.GROUP_BY), 'select': string(dd.SELECT, 'Patient list for measure_gap/pdc_adherence (default gap)')})
    if op == 'loop_closure':
        from . import loop_closure as lc
        return obj({'focus': string(lc.FOCUS, 'results: abnormal + critical labs; critical; screening: positive mammogram/FIT; recommendations: report or '
                                              'note follow-up recommendations; orders: ordered tests never resulted; referrals; reports: abnormal reports; all'),
                    'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                    'window_days': {'type': 'integer', 'minimum': 30, 'maximum': 3650, 'description': 'Look-back for item start dates (default 365)'},
                    'windows': {'type': 'object', 'description': 'Follow-up window override in days per item type, e.g. {"abnormal_result": 14}'},
                    'group_by': string(lc.GROUP_BY), 'evidence': string(lc.EVIDENCE, 'specific = visits/calls that do not cite the item do not close it'),
                    'abnormal_basis': string(lc.BASIS), 'concept': string(['', *sorted(lc.ACTIONABLE)], 'One lab, else empty'),
                    'owner': {'type': 'string'}, 'practice': {'type': 'string'}, 'top': {'type': 'integer', 'minimum': 1, 'maximum': 1000}})
    if op == 'diagnostic_delay':
        from . import diagnostic_delay as dd
        return obj({'target': string(sorted(dd.TARGETS)), 'mode': string(dd.MODES, 'lookback: delays before diagnosis; undiagnosed: signals without a '
                                                                                  'diagnosis (review prompts); look_forward: ED discharge -> admission; all'),
                    'lookback_days': {'type': 'integer', 'minimum': 90, 'maximum': 3650}, 'washout_days': {'type': 'integer', 'minimum': 0, 'maximum': 1825},
                    'horizon_days': {'type': 'integer', 'minimum': 1, 'maximum': 365}, 'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                    'group_by': string(dd.GROUP_BY), 'symptom_only': {'type': 'boolean'}, 'top': {'type': 'integer', 'minimum': 1, 'maximum': 1000}})
    if op == 'risk_outcomes':
        from . import risk_outcomes as ro
        return obj({'outcome': string(ro.OUTCOMES, 'mortality needs death records'), 'unit': string(ro.UNITS),
                    'window_days': {'type': 'integer', 'minimum': 1, 'maximum': 365}, 'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                    'period_days': {'type': 'integer', 'minimum': 90, 'maximum': 3650}, 'include_observation': {'type': 'boolean'},
                    'adjust': string(ro.ADJUST), 'focus': {'type': 'string', 'description': 'One unit id, else empty'},
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})
    if op == 'risk_adjustment':
        from . import risk_adjustment as ra
        return obj({'focus': string(ra.FOCUS, 'raf: patient RAF/HCCs; recapture: HCCs coded last year not yet this year; suspects: evidence without a '
                                              'code (documentation prompts); population: RAF distribution, completeness; all'),
                    'group_by': string(ra.GROUP_BY), 'payment_year': {'type': 'integer', 'enum': [2025, 2026]},
                    'year': {'type': ['integer', 'null'], 'description': 'Data collection year; null = last complete year'},
                    'coverage': string(ra.COVERAGE, 'medicare (default; CMS-HCC is a Medicare model) or all'),
                    'base_rate_pmpm': {'type': ['number', 'null'], 'description': 'User-stated $ PMPM for 1.0 RAF; null if not stated (never invent)'},
                    'patient': {'type': 'string', 'description': 'One patient id, else empty'}, 'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})
    if op == 'target_trial':
        from . import target_trial as tt, concepts as library
        outcome = obj({'type': string(tt.OUTCOME_TYPES, 'lab_change: change in a lab at the horizon; lab_threshold / lab_decline_pct: time to '
                                                        'crossing; admission / ed_visit / condition / encounter: time to the event'),
                       'lab': string(['', *library.MEASUREMENTS], 'lab outcomes only'), 'op': string(['', *tt.OPS], 'lab_threshold only'),
                       'value': {'type': ['number', 'null']}, 'pct': {'type': ['number', 'null'], 'description': 'lab_decline_pct only, e.g. 0.4'},
                       'concept': string(['', *library.CONDITIONS, *library.ENCOUNTERS], 'condition / encounter outcomes only'),
                       'horizon_days': {'type': 'integer', 'minimum': 30, 'maximum': 1825}})
        return obj({'arms': array(string(tt.STRATEGIES), 'Exactly two strategies as named: [treatment, active comparator]; never invent a comparator'),
                    'outcome': outcome,
                    'population_conditions': array(string(library.CONDITIONS), 'Conditions required by time zero, e.g. type_2_diabetes'),
                    'exclude_conditions': array(string(library.CONDITIONS), 'Conditions that exclude when documented before time zero'),
                    'min_age': {'type': 'integer', 'minimum': 0, 'maximum': 120},
                    'adjustment': string(tt.ADJUSTMENTS, 'overlap weights (default), iptw or matching'),
                    'washout_days': {'type': 'integer', 'minimum': 90, 'maximum': 1095}})
    if op == 'line_of_therapy':
        from . import line_of_therapy as lot
        return obj({'area': string(['', *lot.AREAS], 'Therapy area named in the question'),
                    'components': array(string(lot.COMPONENTS), 'Override the area\'s drug classes / ingredient groups; [] for the area default'),
                    'focus': string(lot.FOCUS, 'lines: per-line regimens and durations; switching: switch matrix; persistence; sequences'),
                    'gap_days': {'type': 'integer', 'minimum': 7, 'maximum': 365}, 'require_new_user': {'type': 'boolean'},
                    'add_on_new_line': {'type': 'boolean'}})
    if op == 'trial_feasibility':
        from . import trial_feasibility as tf, concepts as library
        from .drug_classes import CLASSES, GROUPS
        criterion = obj({'type': string(tf.TYPES), 'kind': string(['inclusion', 'exclusion']),
                         'concept': string(['', *library.CONDITIONS, *library.MEASUREMENTS, *library.ENCOUNTERS], 'condition / lab / encounter concept'),
                         'min': {'type': ['integer', 'null']}, 'max': {'type': ['integer', 'null']}, 'value': {'type': ['number', 'string', 'null']},
                         'low': {'type': ['number', 'null']}, 'high': {'type': ['number', 'null']}, 'op': string(['', *tf.OPS]),
                         'classes': array(string([*CLASSES, *GROUPS])), 'recency_days': {'type': ['integer', 'null']},
                         'min_fills': {'type': ['integer', 'null']}, 'text': {'type': 'string', 'description': 'unevaluated criteria: the stated words'}})
        return obj({'criteria': array(criterion, 'Every stated inclusion/exclusion criterion, in order; a stated criterion the data cannot compute '
                                                 'is type unevaluated; never invent criteria'),
                    'trial': {'type': 'string'}, 'group_by': string(tf.GROUP_BY),
                    'consent_rate': {'type': ['number', 'null'], 'description': 'Stated consent/enrolment rate 0..1; null if not stated'}})
    if op == 'contract_strategy':
        from . import contract_strategy as cs
        rate = lambda d: {'type': ['number', 'null'], 'minimum': 0, 'maximum': 1, 'description': d}
        terms = {'type': 'object', 'description': 'Offer terms as stated (rates as fractions): sharing_rate, msr, mlr, two_sided, loss_rate, '
                                                  'savings_cap_pct, loss_cap_pct, loss_cap_revenue_pct, participant_revenue, quality_score; override a named program\'s terms. '
                                                  'Downside loss_rate must be supplied or come from a cited program. Revenue-based caps require participant_revenue.'}
        option = obj({'label': {'type': 'string', 'description': 'Short name, e.g. "Offer A" or the program'},
                      'program': {'type': 'string', 'description': 'Named track whose cited rulebook terms apply, e.g. "MSSP ENHANCED 2026", "MSSP BASIC" '
                                                                   '(all levels), "MSSP BASIC level E", "ACO REACH" (both options), "ACO REACH Global"; "" for a plain offer'},
                      'terms': terms})
        return obj({'focus': string(cs.FOCUS, 'compare: rank tracks/offers; value: what one contract is worth; negotiate: Nash terms / break-even '
                                              'sharing rate; capacity: downside at a confidence level'),
                    'target': string(cs.TARGETS, 'negotiate only: nash (default) or break_even (needs care_management_pmpm)'),
                    'benchmark_pmpm': {'type': ['number', 'null'], 'description': 'User-stated benchmark $ PMPM; null if not stated (never invent)'},
                    'options': array(option, 'Contract options named or stated in the question; [] if none'),
                    'care_management_pmpm': {'type': ['number', 'null'], 'description': 'User-stated care-management spend $ PMPM; null if not stated'},
                    'risk_aversion': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 10, 'description': 'lambda in E[net] - lambda x CVaR (default 0.5)'},
                    'confidence': {'type': ['number', 'null'], 'minimum': 0.5, 'maximum': 0.999, 'description': 'VaR/CVaR confidence (default 0.95)'},
                    'trend_pct': {'type': ['number', 'null'], 'description': 'Stated cost trend as a fraction (0.04 = 4%); null = none stated'},
                    'risk_capacity': {'type': ['number', 'null'], 'description': 'Stated maximum tolerable loss in dollars; null if not stated'},
                    'counterparty': obj({'sharing_rate_min': rate('Lowest sharing rate the payer offers, if stated'),
                                         'sharing_rate_max': rate('Highest sharing rate the payer will concede, if stated'),
                                         'msr_min': rate('Lowest MSR the payer accepts, if stated'), 'msr_max': rate('Highest MSR, if stated'),
                                         'reservation_value': {'type': ['number', 'null'], 'description': 'Payer walk-away value $, if stated'}}),
                    'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}})})
    if op == 'claims_review':
        from . import claims_review as cr
        return obj({'focus': string(cr.FOCUS, 'providers: peer billing outliers; benford; duplicates: duplicates, resubmissions, reversals; '
                                              'underpayment: paid vs comparable claims; denials: CARC Pareto, overturns, write-offs; leakage: '
                                              'duplicates + underpayment + denials; all'),
                    'by': string(cr.BY, 'Denial table grouping'), 'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                    'fdr': {'type': 'number', 'minimum': 0.001, 'maximum': 0.25, 'description': 'Benjamini-Hochberg false-discovery rate (default 0.05)'},
                    'min_claims': {'type': 'integer', 'minimum': 5, 'maximum': 5000}, 'window_days': {'type': 'integer', 'minimum': 0, 'maximum': 30},
                    'writeoff_days': {'type': 'integer', 'minimum': 30, 'maximum': 730},
                    'payer': {'type': 'string', 'description': 'One payer id, else empty'}, 'provider': {'type': 'string', 'description': 'One service organization id, else empty'},
                    'rework_cost': {'type': ['number', 'null'], 'description': 'User-stated $ per rework touch; null if not stated (never invent)'},
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})
    if op == 'monitor':
        from . import monitor
        return obj({'check': string(monitor.CHECKS, 'control_chart: SPC charts; feed: source-system volume; claims: claim series + integrity; '
                                                     'benford / peer_outliers / duplicates: claims review flags; all: everything'),
                    'series': array(string(monitor.SERIES), '[] = the defaults for the check'),
                    'group_by': string(monitor.GROUP_BY, 'auto = overall plus each series\' natural grouping'),
                    'method': string(monitor.METHODS), 'amount': string(monitor.AMOUNTS),
                    'stream': {'type': 'string', 'description': 'One source stream for feed checks, or empty'},
                    'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                    'chart': string(monitor.CHART_OPTIONS, 'control = SPC charts (default); ra_cusum = risk-adjusted CUSUM; vlad = variable life-adjusted display of observed minus expected outcomes'),
                    'outcome': string(monitor.RA_OUTCOMES, 'ra_cusum / vlad outcome: readmission, post_discharge_ed, ed_revisit or mortality'),
                    'unit': string(monitor.RA_UNITS, 'ra_cusum / vlad charted unit: hospital, practice, pcp or none (network)'),
                    'or_shift': {'type': ['number', 'null'], 'minimum': 1.01, 'maximum': 19.99, 'description': 'Odds-ratio deterioration the CUSUM should detect (default 2)'},
                    'or_improve': {'type': ['number', 'null'], 'minimum': 0.01, 'maximum': 0.99, 'description': 'Odds-ratio improvement to detect, only if stated'},
                    'arl0': {'type': 'integer', 'minimum': 100, 'maximum': 100000, 'description': 'In-control average run length in cases (default 5000)'},
                    'window_days': {'type': 'integer', 'minimum': 1, 'maximum': 365, 'description': 'Outcome window in days, e.g. 30-day readmission (default 30)'},
                    'adjust': string(monitor.RA_ADJUST, 'Expected-risk covariates (default age, sex, conditions)'),
                    'min_cases': {'type': 'integer', 'minimum': 10, 'maximum': 100000, 'description': 'Smallest unit charted (default 30)'}})
    if op == 'market':
        from . import market
        return obj({'unit': string(market.UNITS, 'Ticker unit: attributed practice, attributed PCP, or payer'),
                    'metrics': array(string(market.METRICS), '[] = every metric with data'),
                    'direction': string(market.DIRECTIONS, 'worse/better keeps only tickers whose latest move went that way'),
                    'period': obj({'start': {'type': 'string', 'description': 'YYYY-MM-DD or empty'},
                                   'end': {'type': 'string', 'description': 'YYYY-MM-DD or empty (last complete month by default)'}}),
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 500}})
    if op == 'demand':
        from . import demand
        return obj({'measure': string(demand.MEASURES, 'Volume series: visits by setting, scheduled appointments, discharges or bed-days'),
                    'frequency': string(demand.FREQUENCIES), 'horizon': {'type': 'integer', 'minimum': 1, 'maximum': 104, 'description': 'Periods ahead (months 1..24, weeks 1..104)'},
                    'group_by': string(demand.GROUP_BY, 'practice = attributed PCP practice (appointments: scheduling practice); facility = where it occurred'),
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 30},
                    'target_occupancy': {'type': 'number', 'minimum': 0.3, 'maximum': 1.0, 'description': 'bed_days only; default 0.85'},
                    'peak_quantile': {'type': 'number', 'minimum': 0.5, 'maximum': 0.999, 'description': 'bed_days peak-day coverage; default 0.95'},
                    'staffing': {'type': 'boolean', 'description': 'true when the question asks for staff/FTE needs'},
                    'minutes_per_unit': {'type': ['number', 'null'], 'description': 'User-supplied staff minutes per unit; null if not stated (never invent)'},
                    'fte_hours_per_week': {'type': ['number', 'null'], 'description': 'User-supplied productive hours per FTE-week; null if not stated'},
                    'hierarchy': array(string([g for g in demand.GROUP_BY if g != 'none']), 'Ordered levels (outermost first) forecast together as a hierarchy; [] = none'),
                    'reconcile': string(demand.RECONCILE, 'Make child forecasts add up to parents: mint_shrink (default with a hierarchy), ols, wls, none')})
    if op == 'knowledge':
        from . import vbc_knowledge
        return obj({'query': {'type': 'string', 'description': 'The explanation question, verbatim'},
                    'top': {'type': 'integer', 'minimum': 1, 'maximum': 10, 'description': 'Entries to retrieve (default 5)'},
                    'topic': string([*vbc_knowledge.TOPICS, 'any'], 'Restrict to one topic, else any')})
    if op == 'briefing':
        return obj({'top': {'type': 'integer', 'minimum': 1, 'maximum': 25, 'description': 'Cards to return (default 10)'}})
    if op == 'pathway':
        from . import pathway, concepts as library
        return obj({'start': string(['', *pathway.EVENT_STATES], 'Anchor state paths start from ("" = each patient\'s first event); diagnosis needs index_concept'),
                    'index_concept': string(['', *library.CONDITIONS], 'start diagnosis only: first documented record of this condition'),
                    'anchor': string(pathway.ANCHORS, 'each occurrence of the start state, or only the first per patient'),
                    'window_days': {'type': 'integer', 'minimum': 1, 'maximum': 730, 'description': 'a next event later than this = no further contact'},
                    'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}),
                    'targets': array(string([s for s in pathway.EVENT_STATES if s != 'diagnosis']), 'absorbing outcome states, e.g. inpatient, emergency'),
                    'max_steps': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                    'horizons_days': array({'type': 'integer'}, 'days within window_days for empirical reach; [] for defaults'),
                    'expected': obj({'from': string(['', *pathway.EVENT_STATES]), 'to': array(string(pathway.EVENT_STATES)),
                                     'within_days': {'type': 'integer', 'minimum': 0, 'maximum': 730}}),
                    'steps': {'type': 'integer', 'minimum': 1, 'maximum': 6}, 'top': {'type': 'integer', 'minimum': 1, 'maximum': 50},
                    'group_by': string(pathway.GROUP_BY), 'leakage': string(pathway.LEAKAGE),
                    'order': {'type': 'integer', 'minimum': 1, 'maximum': 2}, 'discharge_events': {'type': 'boolean'},
                    'referral_events': {'type': 'boolean'}, 'include_other': {'type': 'boolean'}})
    if op == 'sql':
        from . import sql_query
        return obj({'query': {'type': 'string', 'description': 'One read-only SELECT/WITH over the semantic_layer tables. Reference the upstream population (if an input is given) as table population(patient_id).'},
                    'returns': string(sql_query.RETURNS, 'count/value: first column of first row; patient_set: first column = patient_id; table: all rows')})
    if op == 'explain':
        from . import explain, concepts as library
        return obj({'outcome': obj({'type': string(explain.OUTCOME_TYPES),
                'concept': string(['', *library.CONDITIONS, *library.MEASUREMENTS, *library.ENCOUNTERS], 'Outcome concept for concept/measurement types; "" for readmission'),
                'op': string(['', 'lt', 'le', 'gt', 'ge'], 'measurement outcomes only'), 'value': {'type': 'number'},
                'aggregate': string(['latest', 'any'])}),
            'window': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}})})
    if op == 'observation_process':
        return obj({'stream':string(catalog.get('source_streams',streams)),
            'months':{'type':'integer','minimum':2,'maximum':24},
            'available_by':{'type':'string','format':'date'}})
    if op == 'reference_review':
        refs=evidence.get('scientific_references',[])
        return obj({'reference_id':string(r['id'] for r in refs),'available_by':{'type':'string','format':'date'},
            'bindings':array(obj({'question_key':string(q['key'] for r in refs for q in r['questions']),
                'node_ids':{**array(string(), 'Earlier evidence input IDs; empty explicitly means no linked evidence. Context is not an applicability judgment.'),'maxItems':9,'uniqueItems':True}}))})
    if op == 'accountability_review':
        from .program_accountability import definitions
        return obj({'contract':string(definitions()),'available_by':{'type':'string','format':'date'}})
    if op == 'outcome':
        from .outcomes import FILTERS
        contracts=evidence.get('outcome_contracts',[])
        ids=([r[contracts['columns'].index('id')] for r in contracts['rows']]
             if isinstance(contracts,dict) else [r['id'] for r in contracts])
        return obj({'contract':string(ids),
                    **{k:{'type':'string','format':'date'} for k in ('index_from','index_through','available_by')},
                    'filter':string(FILTERS)})
    if op == 'requirements':
        from .evidence_requirements import FILTERS
        return obj({'contract':string(r['id'] for r in evidence.get('documentary_contracts',[])), 'filter':string(FILTERS)})
    if op == 'protocol_review':
        from .care_protocols import FILTERS
        return obj({'protocol':string(evidence.get('protocol_versions',[]), 'Exact registered id@version; current unversioned aliases are accepted by runtime but explicit versions preserve the requested rule.'), 'filter':string(FILTERS)})
    if op == 'program_document':
        from .program_documents import FILTERS
        return obj({'package':string(r['id'] for r in evidence.get('program_documents',[])), 'filter':string(FILTERS)})
    if op == 'cohort':
        spec=copy.deepcopy(cohorts.SCHEMA)
        from . import concepts as concept_library
        for prop in spec['properties'].values():
            patient,record,concept,measurement=prop['items']['anyOf']
            patient['properties']['field']=string(cohorts.PATIENT_FIELDS)
            concept['properties']['concept']=string([*concept_library.CONDITIONS,*concept_library.MEASUREMENTS,*concept_library.ENCOUNTERS])
            measurement['properties']['concept']=string(concept_library.MEASUREMENTS)
            if streams:record['properties']['stream']=string(streams)
            else:prop['items']={'anyOf':[patient,concept,measurement]}
        return obj({'specification':spec})
    if op == 'evidence':
        from .evidence_queries import FIELDS
        props={'source':string(['text','structured']), 'concept':{'type':'string','minLength':1,'maxLength':120},
            'observation':string(['present','not_observed']), 'concept_match':string(['equals','prefix']),
            'value_equals':{'anyOf':[{'type':'string','maxLength':120},{'type':'boolean'}]},
            'event_date_from':{'type':'string','format':'date'},'event_date_through':{'type':'string','format':'date'}}
        for key in sorted(FIELDS-{'concept'}):props[key]=string(evidence.get('text_axis_values',{}).get(key))
        clause={'type':'object','properties':props,'required':['source','concept'],'additionalProperties':False}
        clauses={'type':'array','items':clause,'minItems':1,'maxItems':8,
            'description':'An empty evidence query is invalid. If no mapped concept supports the request, omit this node and declare the unmet requirement.'}
        return obj({'plan':obj({'match':string(['all','any']),'clauses':clauses})})
    if op in ('intersect', 'union', 'difference', 'compare', 'medication_identity'):
        return obj({})
    if op == 'assertion_review':
        concepts=array(string(evidence['assertion_concepts']), '1..20 distinct mapped concepts required. Empty does not mean all concepts.')
        concepts['minItems']=1
        return obj({'streams': stream_list, 'concepts': concepts})
    if op == 'interpretation':
        from .interpretation_state import FILTERS
        schema=parameter_schema('assertion_review',catalog)
        schema['properties']['filter']=string(FILTERS)
        schema['required'].append('filter')
        return schema
    if op in ('source_state', 'pre_event_state'):
        concepts=[c for c in evidence['assertion_concepts'] if
            (c.split(' ',1)[0] if c.startswith(('medication ','care ')) else c) in source_state.RELATIONS]
        return obj({'streams':stream_list,'concept':string(concepts),
            'relation':string([r for values in source_state.RELATIONS.values() for r in values],
                'Must match the concept family in the supplied source_state relation contract; runtime checks the pair.'),
            'clock':string(source_state.CLOCKS),'filter':string(source_state.FILTERS)})
    if op in ('context', 'context_passes'):
        from .state_contracts import CONTRACTS as state_contracts
        return obj({'question': string(description='Exact bounded source question, 1..1500 characters.'),
            'streams': stream_list, 'concepts': array(string(), '0..20 exact supplied narrative concepts; [] means unfiltered.'),
            'contract': string(['', *state_contracts],
                'Optional clinical evidence contract; use empty string for an open source question. Never use a graph/schema version or invent a contract.'),
            'program_ids': array(string(r['id'] for r in evidence.get('program_documents', [])),
                '0..5 supplied imported package hashes; [] when no quoted program is requested.'),
            'max_bytes': {'type': 'integer', 'description': '2048..60000 UTF-8 bytes; omissions must remain explicit.'}})
    if op == 'functional_review':
        return obj({'filter': string(functional_programs.FILTERS)})
    if op == 'domain_review':
        return obj({'domain': string(domain_programs.PROGRAMS.values()), 'filter': string(domain_programs.FILTERS)})
    if op == 'worklist':
        return obj({'program': string(r['id'] for r in evidence['work_programs'])})
    if op == 'program_acquisition':
        return obj({'package': string(r['id'] for r in evidence['program_documents'])})
    if op == 'appointment_followthrough':
        from .appointment_graph import FILTERS
        cutoff={'type':'string','format':'date','minLength':10,'maxLength':10,
            'description':'Literal YYYY-MM-DD receipt cutoff, no later than snapshot.as_of.'}
        return obj({'available_by':cutoff,'filter':string(FILTERS)})
    if op == 'financial_accounts':
        cutoff=copy.deepcopy(WINDOW_SCHEMA['properties']['start'])
        cutoff.update(type='string',format='date',minLength=10,maxLength=10,
            description='Literal YYYY-MM-DD receipt cutoff, no later than snapshot.as_of. Use that actual date when no earlier cutoff is requested; never the text snapshot as_of.')
        return obj({'available_by':cutoff})
    if op == 'measure_review':
        components={}
        for role in ('denominator','numerator','exclusion','attribution'):
            packages=[r['id'] for r in evidence.get('program_documents',[]) if r.get('role')==role]
            package=obj({'package': string(packages,
                'Exact supplied package of this component role, with a definition relevant to the question. Unrelated packages cannot substitute for missing definitions.')})
            if not packages:
                # Empty enums are invalid provider schemas. A false schema makes
                # the impossibility explicit without authorizing an invented ID.
                package['properties']['package']={'not':{}}
            if role in ('denominator','numerator'):
                components[role]=package
            else:
                components[role]={'anyOf':[package,obj({'unbound':string()}),obj({'not_applied':string()})]}
        result=obj({'title':string(),'components':obj(components)})
        result['description']='Denominator and numerator require relevant quoted packages. If either is unavailable, omit this operation and declare that requirement unmet; preserve other supported operations.'
        return result
    if op == 'retrieve':
        return obj({'query': string(), 'analysis_window': copy.deepcopy(WINDOW_SCHEMA)})
    if op == 'forecast':
        return obj({'model': string(['all', 'logistic', 'boosted', 'boosted_blocks'])})
    if op == 'source_changes':
        return obj({'streams': array(string(streams), '1..5 distinct loaded source streams.')})
    if op == 'review':
        return obj({'providers': array(string(['jev', 'perplexity']), '1..2 distinct providers.'),
                    'question': string(), 'maximum_patients': {'type': 'integer', 'description': '1..3'}})
    return None


def validate_parameters(op, p):
    """Reuse the same semantic validators as direct graph execution."""
    from . import assertion_reconciliation, source_state, context_builder, functional_programs, domain_programs, evidence_queries
    if op=='evidence':
        if not isinstance(p,dict) or set(p)!={'plan'}:raise ValueError('Evidence node needs its explicit plan')
        return {'plan':evidence_queries.validate_plan(p['plan'])}
    if op == 'governed_metric':
        from .request_vocabulary import validate
        return validate(p)
    validator = {'assertion_review': assertion_reconciliation.validate, 'source_state': source_state.validate, 'pre_event_state': source_state.validate,
                 'context': context_builder.validate, 'context_passes': context_builder.validate,
                 'functional_review': functional_programs.validate, 'domain_review': domain_programs.validate}.get(op)
    return validator(p) if validator else p
