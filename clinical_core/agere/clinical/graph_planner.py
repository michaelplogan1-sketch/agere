"""Frontier composition of locally validated evidence/analysis dependency graphs."""
import json

from ..gateway import PersistentGateway
from .context_student import send
from .analysis_graph import OPERATIONS, validate
from .cohorts import capabilities
from .analytic_registry import CATALOG
from .planner_catalog import serialize
from . import graph_contracts

SCHEMA = {'type': 'object', 'properties': {
    'interpretation': {'type': 'string'},
    'coverage': {'type': 'string', 'enum': ['supported_observation', 'partial', 'unsupported']},
    'unmet_requirements': {'type': 'array', 'items': {'type': 'string'}},
    'nodes': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'id': {'type': 'string'}, 'operation': {'type': 'string', 'enum': sorted(OPERATIONS)},
        'inputs': {'type': 'array', 'items': {'type': 'string'}},
        'parameters_json': {'type': 'string'}, 'purpose': {'type': 'string'}},
        'required': ['id', 'operation', 'inputs', 'parameters_json', 'purpose'], 'additionalProperties': False}}},
    'required': ['interpretation', 'coverage', 'unmet_requirements', 'nodes'], 'additionalProperties': False}

CONTRACTS = {
    'sql': {'inputs':['optional earlier_set'],'parameters':{'query':'one read-only SELECT over semantic_layer tables','returns':'count|value|patient_set|table'},
        'meaning':'Exact answers to literal data questions: counts/sums/maxima of records or fields, record-level filters, date arithmetic (julianday), arrival lags, joins between streams, '
                  'group-by tables. Query the typed semantic_layer tables (concept columns already normalise ICD-10/SNOMED/labels/units); use record.payload with json_extract for other streams. '
                  'Count records when asked about records, distinct patient_id when asked about patients. Read semantic_layer descriptions before writing SQL. '
                  'Prefer aggregate/explain for adjusted comparisons and why-questions.'},
    'explain': {'inputs':['earlier_set'],'parameters':{'outcome':{'type':'readmission|concept|measurement','concept':'clinical_concepts id ("" for readmission)','op':'lt|le|gt|ge (measurement)','value':0,'aggregate':'latest|any'},'window':{'start':'','end':''}},
        'meaning':'Answers WHY questions descriptively: compares units with vs without the outcome (index hospital stays for readmission, else patients) across received-data factors '
                  '(age, sex, payer, chronic-condition burden, social screens, PCP attribution, prior ED use; for stays: 7-day follow-up, discharge planning, observation vs inpatient). '
                  'Risk ratios, Fisher p, BH q; with <5 positive units it returns a case-by-case review instead. Associations, not causes. Use as the final node for why/what-drives/contributing-factor questions.'},
    'aggregate': {'inputs':['earlier_set'],'parameters':{'measure':'patients|events|value|readmissions','concept':'clinical_concepts id ("" = whole population for patients; "" for readmissions)',
        'statistic':'patients: count|share; events: count|rate_per_1000; value: mean|median; readmissions: count|rate (30-day readmissions / index hospital stays, lists each readmission)','group_by':'none|pcp|practice|coverage_type|age_band|sex|facility (where events occurred; index-stay hospital for readmissions)',
        'adjust':'none|age_sex|age_sex_conditions','window':{'start':'YYYY-MM-DD or ""','end':'YYYY-MM-DD or ""'},'value_aggregate':'latest|all','top':25},
        'meaning':'Counts, rates per 1000 patients, shares and mean/median values over the input population, optionally grouped by attributed PCP/practice (latest assigned primary-care relationship), payer type, age band or sex. '
                  'Events are distinct patient-days of a concept. adjust standardises indirectly (age band x sex [x chronic-condition burden]) and reports O/E with 95% CI; use it for any "adjusted"/"case-mix"/"fair comparison" request. '
                  'Use as the final node for how-many/rate/average/which-group questions. The LAST node must be the one that directly answers the question; '
                  'put any supporting counts before it. A share of a subgroup = aggregate(measure=patients, concept=<subgroup concept>, statistic=share) over the base population.'},
    'observation_process': {'inputs':['earlier_set'],'parameters':{'stream':'One exact received source stream','months':'2..24 calendar months ending at cutoff','available_by':'literal YYYY-MM-DD receipt cutoff'},'meaning':'Full-population record arrival/source-date comparison, literal source/organization strata, Poisson partitions vs constant rate, repeated-text sensitivity and correction pointers. Retains no-arrival members and unknown dates; competing explanations and evidence requests. Report only: no unique clinical events, clinical change, outage, attribution, quality, causality or service indication.'},
    'reference_review': {'inputs':['earlier_population','optional_earlier_evidence_nodes'],'parameters':{'reference_id':'Exact scientific_references id','available_by':'ISO receipt cutoff','bindings':[{'question_key':'Exact registered applicability question key','node_ids':['earlier evidence node id']}]},'meaning':'Dated scientific reference/applicability questions with exact patient evidence links. Population first; optional receipt inputs bound to question keys. Applicability, currency and benefit unresolved. Report only; no eligibility/service inference.'},
    'appointment_followthrough': {'inputs':['earlier_set'],'parameters':{'available_by':'literal YYYY-MM-DD','filter':'all or explicit_fulfilled or explicit_no_show or explicit_cancelled or unresolved'},'meaning':'Received first-booking follow-through for every selected patient. Earliest booking received since 2026-01-01; exact source identity, terminal event through scheduled +7d and receipt +14d. Explicit dispositions only; absent/conflicting/censored follow-up stays unknown. Not every appointment or arbitrary event-window analysis. all retains the full input denominator; explicit status filters keep unknowns. Current-cutoff membership can feed existing appointment review work; historical cutoffs cannot feed current work. No cause, current need, attendance verification or risk prediction.'},
    'evidence_portfolio': {'inputs':['earlier_set'],'parameters':{'contracts':['exact supplied documentary contract IDs, one to eight'],'review_slots':'integer 0..16, hypothetical equal request slots'},'meaning':'Exact structural request-bundle comparison for at most sixteen requests and 128 unresolved checks. No sampling on overflow. Counts checks with covered conditional request sets, not resolved checks, joint outcomes, probabilities or clinical benefit. No spending cap or actual capacity inferred.'},
    'domain_review': {'inputs':['earlier_set'],'parameters':{'domain':'respiratory or behavioral','filter':'source_review_candidates, source_available or source_absent'},'meaning':'Exact received domain accounts nominate review; non-nominations remain unknown need. Coverage filters describe observed sources only. Labels are not current diagnosis, safety assessment, service indication or benefit eligibility.'},
    'functional_review': {'inputs':['earlier_set'],'parameters':{'filter':'source_review_candidates, source_available or source_absent'},'meaning':'Dated functional/support sources nominate review, not current need, home-health eligibility or absence of care.'},
    'measure_review': {'inputs':['earlier_set'],'parameters':{'title':'descriptive measure title','components':'denominator/numerator/exclusion/attribution: {package: exact hash with matching role}; optional roles may be {unbound: reason} or explicit descriptive {not_applied: reason}'},'meaning':'Compose snapshot patient counts from quoted rules; retain unresolved denominator/numerator and logical sensitivity. No certified performance or payment.'},
    'program_acquisition': {'inputs':['earlier_set'],'parameters':{'package':'exact imported package hash'},'meaning':'Prepare internal source/specification requests with conditional input cases. No missing-care or probability inference.'},
    'program_document_change': {'inputs':['earlier_set'],'parameters':{'before':'exact imported package hash','after':'exact imported package hash'},'meaning':'Compare two versions of one quoted program on the same population; preserve changed inputs and unknowns. No activation or outcome claim.'},
    'program_document': {'inputs':['earlier_set'],'parameters':{'package':'exact supplied imported package hash','filter':'all'},'meaning':'Quoted rule simulation using pinned source requirements/protocols. Filters all/supported/contradicted/unresolved/not_effective. User-reported review and unbound inputs stay explicit. No certified measure, enrollment, attribution or payment.'},
    'protocol_change': {'inputs':['earlier_set'],'parameters':{'before':'supplied protocol_id@version','after':'another supplied version of the same protocol'},'meaning':'Compare explicit rule versions on the same full population/snapshot, keeping both receipts. Reports changes in review criteria/readiness, not patient outcomes or clinical accuracy. No activation.'},
    'accountability_review': {'inputs':['earlier_set'],'parameters':{'contract':'program_payment_evidence','available_by':'snapshot as_of'},'meaning':'Review program-payment prerequisites: effective contract, attribution, outcome links and payment rules, plus received financial account issues across the complete population. Coverage and PCP labels are not attribution. Produces source-review context, never membership or payment eligibility.'},
    'financial_accounts': {'inputs':['earlier_set'],'parameters':{'available_by':'snapshot as_of'},'meaning':'Reconcile received account stocks vs patient payments/refunds and payer cash. Exact integer-cent identities, source links and unresolved discrepancies. No contract settlement, hardship or savings inference.'},
    'decision_landmark': {'inputs':['earlier_set'],'parameters':{'contract':'exact supplied landmark contract ID','receipt_from':'YYYY-MM-DD','receipt_through':'YYYY-MM-DD','available_by':'snapshot as_of'},
        'meaning':'Next day after first index receipt; known prior inputs and future explicit clinical endpoints. Exclude past/already received endpoints. No fitted predictor or clinical negative.'},
    'baseline': {'inputs':['direct upstream outcome node'],'parameters':{'profile':'received_prior_180d'},
        'meaning':'180-day pre-index source-dated notes (machine), measurements, supply/arrival blocks. Separate labels; no fitting/risk claim. Outcome all retains missing indices.'},
    'protocol_review': {'inputs':['earlier_set'],'parameters':{'protocol':'exact supplied care protocol ID@version','filter':'all'},
        'meaning':'Versioned clinical documentary criteria; filters all/candidates/supported/source_review/unresolved. Preserve missing inputs, clocks and unreviewed extraction. No inferred clinical eligibility or payer entitlement.'},
    'outcome': {'inputs':['earlier_set'],'parameters':{'contract':'exact supplied outcome contract ID',
        'index_from':'YYYY-MM-DD','index_through':'YYYY-MM-DD','available_by':'snapshot as_of','filter':'all'},
        'meaning':'Filters all/indexed/mature_labeled/observed/not_observed_mature/unresolved. Fixed-horizon comparison uses mature_labeled base, excluding immature positives. Zero=no received match, not no care; no invented contracts/enrollment/index dates.'},
    'requirements': {'inputs':['earlier_set'],'parameters':{'contract':'exact supplied documentary contract ID','filter':'supported'},
        'meaning':'Documentary all/any/not; filters all/supported/contradicted/unresolved. No invented contracts or clinical eligibility.'},
    'pre_event_state': {'inputs':['direct upstream outcome with filter all'], 'parameters':{'streams':['social_screens'],'concept':'food difficulty','relation':'reported_barrier','clock':'source_statement_date','filter':'all'},
        'meaning':'Shared documentary state strictly before the upstream first event in its explicit index window and available_by cutoff. Same parameters as source_state. Retains missing/ambiguous indices and full denominator. Both report and receipt must precede index day; same-day or missing clocks are unresolved. Late reports excluded and counted. Current reviews are not time-qualified; reconstructs received evidence retrospectively, not historical system knowledge. No causal/risk/missing-care claim. Use all for reporting; affirmed/negated for predicates.'},
    'source_state': {'inputs':['earlier_set'],'parameters':{'streams':['notes','financial'],'concept':'financial hardship',
        'relation':'reported_barrier','clock':'source_statement_date','filter':'affirmed'},
        'meaning':'Documentary state, not clinical truth. Clocks source_statement_date/explicit_clinical_date/literal_reference_date; filters all/affirmed/negated/conflicting/unresolved. Unknowns persist. Medication relations reported_use/treatment_hold_stop/dose_change/prescription_order/dispense_record/administration_record/use_observed. Care request/status/booking/completion uses referrals,referral_status_updates,outside_report_status,imported_documents: concept care requested services covers all request types; care_completion filter all retains a separate account for every linked request, its statuses, bookings, received completion report and unresolved evidence. Other exact care concepts are supplied. False confirmation is unknown, not no attendance; documented limited service is not closure of all requested care. financial hardship and native social concepts use reported_barrier.'},
    'assertion_review': {'inputs':['earlier_set'],'parameters':{'streams':['notes'],'concepts':['outside care']},
        'meaning':'All assertions/relations/clocks/conflicts/copies/unmet evidence; no inference/calls.'},
    'context_passes': {'inputs':['earlier_set'],'parameters':{'question':'Documentary assertion','streams':['notes'],
        'concepts':[],'contract':'','program_ids':[],'max_bytes':10000},
        'meaning':'Partition all received units; retain indivisible overflow. No calls/clinical decisions.'},
    'context': {'inputs':['earlier_set'],'parameters':{'question':'Source review question','streams':['notes'],
        'concepts':[],'contract':'','program_ids':[],'max_bytes':24000},
        'meaning':'Budgeted context/clocks/history/gaps; omissions block complete-case dispatch. program_ids accepts imported quoted package hashes. No answer.'},
    'cohort': {'inputs': [], 'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}},
               'meaning': 'Full observed set; atoms follow supplied cohort schema. PREFERRED population filter for diagnoses, labs, vitals and encounter types: '
                          'concept/measurement atoms over clinical_concepts match every coding system and label received (ICD-10, SNOMED, free text, unit variants). '
                          'Use evidence nodes only for narrative-text assertions with no clinical concept.'},
    'evidence': {'inputs': [], 'parameters': {'plan': {'match': 'all or any', 'clauses': [
        {'source': 'text or structured', 'concept': 'exact observed vocabulary', 'observation': 'present or not_observed'}]}},
        'meaning': 'Full indexed set. Optional concept_match=equals/prefix, text assertion/subject/time/relation; structured value_equals string/bool. event_date_from/event_date_through use source dates.'},
    'intersect': {'inputs': ['earlier_set_a', 'earlier_set_b'], 'parameters': {}, 'meaning': 'All memberships; unknown stays unknown.'},
    'union': {'inputs': ['earlier_set_a', 'earlier_set_b'], 'parameters': {}, 'meaning': 'Any membership; unknown stays unknown.'},
    'difference': {'inputs': ['earlier_base', 'earlier_exclusion'], 'parameters': {}, 'meaning': 'Base minus exclusion; unknown exclusion does not become included.'},
    'compare': {'inputs': ['earlier_base', 'earlier_observed_endpoint', 'earlier_group_a', 'earlier_group_b'],
                'parameters': {}, 'meaning': '1-8 groups versus complements in base; nominal Fisher/BH and beta-binomial rates. Exploratory, no causal/risk-adjusted inference.'},
    'analyze': {'inputs': ['earlier_set'], 'parameters': {'operator': 'registered analytic operation',
                'drug_class': '', 'view': 'all', 'analysis_window': {'basis': 'all_observed', 'start': '', 'end': ''}},
                'meaning': 'Full-set operator; bb/arni/mra/sglt2 only for medication_evidence.'},
    'retrieve': {'inputs': ['earlier_set'], 'parameters': {'query': 'source evidence query',
                'analysis_window': {'basis': 'all_observed', 'start': '', 'end': ''}},
        'meaning': 'Ranked in-set examples, never denominator.'},
    'forecast': {'inputs':['earlier_set'],'parameters':{'model':'all'},
                 'meaning':'Attached research target/horizon; models all/logistic/boosted/boosted_blocks. Full set; not validated risk/benefit.'},
    'source_changes': {'inputs':['earlier_set'],'parameters':{'streams':['notes','labs','pharmacy_claims']},
                       'meaning':'Monthly received-record Poisson blocks; arrival intensity, not deterioration.'},
    'review': {'inputs':['earlier_set'],'parameters':{'providers':['jev','perplexity'],'question':'Bounded patient evidence assertion','maximum_patients':1},
               'meaning':'Requested paid sampled review; no automatic truth.'},
    'sequence': {'inputs':['earlier_set'],'parameters':{'first':{'kind':'record','event_type':'admission'},
                'second':{'kind':'record','event_type':'discharge'},'min_days':0,'max_days':30,
                'same_organization':True,'basis':'source_event','available_by':'snapshot as_of'},
                'meaning':'Distinct observed pairs; inclusive window. Text needs kind=text/concept. No clock fallback, verified episode or causality.'},
    'longitudinal': {'inputs':['earlier_set'],'parameters':{'anchor':{'kind':'record','event_type':'admission'},
                    'before_days':30,'after_days':30,'concepts':[],'basis':'source_event','available_by':'snapshot as_of'},
                    'meaning':'Surrounding records/copies/conflicts/provisional pairs. Empty concepts=all mapped text; no causes/risk/clinical state.'},
    'worklist': {'inputs':['earlier_set','optional distinct accountability_review, program_acquisition, measure_review covering selected patients'],'parameters':{'program':'hf_review, affordability, functional_support_review, respiratory_source_review, behavioral_source_review, or protocol:<exact supplied care protocol ID>'},
                 'meaning':'Prepare full review set; save separately. No enrollment/contact/treatment/completion.'},
    'interpretation': {'inputs':['earlier_set'],'parameters':{'streams':['notes','financial'],'concepts':['financial hardship'],'filter':'all, needs_review, model_disagreement, review_conflict, unresolved_review or reviewed_source'},
                       'meaning':'Source/review-status set; keep corrections/model-proposal authority. No calls/eligibility.'},
    'state_review': {'inputs':['earlier_set'],'parameters':{'contract':'all, hf_therapy, renal_review or affordability'},
                     'meaning':'Documentary prerequisites/doses. Conflicts, dates and unsupported bindings stay unresolved; no eligibility.'},
    'medication_identity': {'inputs':['earlier_set'],'parameters':{},
                            'meaning':'Offline RxNorm names, spans, doses/forms and RAAS alternatives. Not use/tolerance.'},
}


def grounded_fields(fields, profile):
    """Offer only streams/fields the received data populates, with small value vocabularies."""
    from .source_profile import summary
    observed = summary(profile)
    # Field value: observed coded values, a [min, max] numeric range, or 'text'.
    record_fields = {}
    for stream, names in fields['record_fields'].items():
        if stream not in observed: continue
        present = {n: observed[stream]['fields'][n] for n in sorted(names) if n in observed[stream]['fields']}
        if present: record_fields[stream] = present
    patient_fields = [f for f in fields['patient_fields']
                      if profile['patients']['fields'].get('birth_date' if f == 'age' else f, {}).get('populated')]
    return {**fields, 'record_fields': record_fields, 'patient_fields': patient_fields,
            'age_basis': 'Use age_reported (reported age); birth dates are not received.'}


def propose(question, path, ledger, *, snapshot, graph_catalog, observed_values=None, profile=None, semantic=None):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1500:
        raise ValueError('Bounded population question required')
    fields = capabilities()
    fields.pop('schema')
    if profile is not None:
        from . import concepts
        fields = grounded_fields(fields, profile)
        fields['clinical_concepts'] = concepts.catalog()
    fields['operators'] = ['eq','ne','lt','le','gt','ge','contains','prefix']
    fields['record_atom_example'] = {'kind':'record','stream':'adt','conditions':[{'field':'event','op':'eq','value':'A01'}],
                                    'window':{'basis':'all_observed','start':'','end':''}}
    observed = None
    if observed_values:
        concepts_by_system={}
        for row in observed_values['documented_concepts'][:40]:
            concepts_by_system.setdefault(row['code_system'],[]).append([row['code'],row['name']])
        observed = {'roster': {k: sorted(v) for k, v in observed_values['roster'].items()},
                    'encounter_organizations': sorted(observed_values['encounter_organizations']),
                    'concept_columns':['code','name'],'documented_concepts_by_system':concepts_by_system}
    # Do not repeatedly pay for telemetry or duplicate concept/axis vocabularies.
    compact_catalog={k:graph_catalog[k] for k in ('text_axis_values','structured_scalar_values','boolean_valued_concepts','forecast')}
    compact_catalog['concepts']={kind:[r['concept'] for r in graph_catalog['evidence_concepts'] if r['source']==kind]
                                 for kind in ('text','structured')}
    longitudinal=graph_catalog.get('longitudinal',{})
    compact_catalog['longitudinal']={'available':longitudinal.get('available',False)}
    compact_catalog['source_review_programs']={k:graph_catalog[k] for k in ('domain_review','functional_review') if k in graph_catalog}
    from .program_work import registered_programs
    compact_catalog['work_programs'] = [{k:r[k] for k in ('id','name')} for r in registered_programs()]
    from . import native_assertions, native_care_assertions
    compact_catalog['native_assertion_bindings'] = native_assertions.contracts()
    compact_catalog['native_care_assertion_bindings'] = native_care_assertions.contracts()
    compact_catalog['assertion_concepts'] = sorted(set(longitudinal.get('vocabulary',{}).get('concept',[])) |
        native_assertions.CONCEPTS | set(graph_catalog.get('native_care_concepts', [])) | {'financial hardship','care nephrology'} | {'medication '+c for c in ('bb','acei','arb','arni','mra','sglt2')})
    compact_catalog['clinical_state_available']=graph_catalog.get('clinical_state_available',False)
    compact_catalog['medication_terminology_available']=graph_catalog.get('medication_terminology_available',False)
    compact_catalog['documentary_contracts']=[{k:s[k] for k in ('id','title')} for s in graph_catalog.get('documentary_contracts',[])]
    compact_catalog['baseline_profiles']=[{k:s[k] for k in ('id','title')} for s in graph_catalog.get('baseline_profiles',[])]
    compact_catalog['landmark_contracts']=[{k:s[k] for k in ('id','title')} for s in graph_catalog.get('landmark_contracts',[])]
    compact_catalog['care_protocols']=[{k:s[k] for k in ('id','title','version')} for s in graph_catalog.get('care_protocols',[])]
    compact_catalog['protocol_versions']=graph_catalog.get('protocol_versions',[])
    compact_catalog['scientific_references']=graph_catalog.get('scientific_references',[])
    compact_catalog['program_documents']=[{k:s[k] for k in ('id','program','version','role','purpose')} for s in graph_catalog.get('program_documents',[])]
    outcome_columns=['id','title','basis','min_days','max_days','source_scope','encounter_rule']
    compact_catalog['outcome_contracts']={'columns':outcome_columns,
        'rows':[[s[k] for k in outcome_columns] for s in graph_catalog.get('outcome_contracts',[])]}
    if longitudinal.get('available'):
        compact_catalog['longitudinal'].update(clocks=longitudinal['clocks'],
            event_types=longitudinal['vocabulary']['event_type'],streams=longitudinal['vocabulary']['stream'],
            text_selector_values='Use concepts.text and text_axis_values from this catalog.')
    instructions = (
        '<=16 ordered nodes; supplied contracts/vocabulary only. '
        'No invented SQL/code/thresholds/diagnoses/outcomes/attribution/models. Preserve population restrictions; '
        'unsupported restrictions => no nodes, unsupported coverage. Other unmet goals => partial. '
        'Denominators=full sets, not retrieved samples. Text labels unvalidated; missing evidence != absent disease/care. '
        'Use source_state with native_assertion_bindings for dated screen reports; raw field cohorts do not reconcile latest accounts. Different affordability questions remain distinct concepts. Supported plan coverage does not establish answer completeness. Reports/orders/fills/completed care differ. Cohort conditions: one record/window. Boolean evidence: value_equals true/false. '
        'Patient IDs from question only. Age=at snapshot; age_reported=reported. '
        'Coverage/codes/organizations != enrollment/disease/attribution. '
        'Clocks: source_event=record timestamp; arrival=received; clinical=explicit clinical date; reference=literal narrative date. '
        'No clock fallback; outcome index dates from question only; maturity != complete capture. '
        'ADT != verified stays/readmissions. Compare full families in one node. '
        'No causal/risk-adjusted inference. Clinical risk/benefit/treatment remain unmet; forecasts=research. '
        'All filters retain everyone including unknown states; never use source_state/pre_event_state/appointment_followthrough filter all as an overlap predicate or comparison endpoint/group. Use affirmative/negative source filters and explicit appointment dispositions for overlaps, plus separate all-state reporting if needed. '
        'Source_state uses snapshot.as_of. For state before an event use pre_event_state directly after an outcome node with filter all, its exact supplied contract and user-specified index window/available_by; never compare latest snapshot states as pre-event features. Pre-event care completion remains documentary, not a matched referral or established care gap. '
        'When the question asks for scientific applicability, use registered scientific_references and reference_review with explicit question-to-evidence bindings. Preserve missing evidence and distinguish guideline population statements from patient observations. Never claim clinical applicability, current guideline coverage or benefit from these links. '
        'Saved-question review preserves parallel measure_review, financial_accounts, accountability_review and outcome reports with full member evidence and exact unresolved inputs. Reports do not become cohort filters or service needs. Do not invent a worklist/program solely to attach these reports. '
        'Minimize nodes; examples/specialists only on request.'
    )
    # The cohort schema already specifies types; avoid duplicating example objects
    # and enumerated operators in the bounded provider request.
    fields.pop('record_atom_example',None)
    # The verbose JSON schema is removed above; retain the exact atom grammar.
    # Omitting this previously invited untyped patient filters that cannot execute.
    fields['predicate_contract']={
        'patient':{'kind':'patient','field':'exact patient_fields name','op':'exact supplied operator','value':'scalar'},
        'record':{'kind':'record','stream':'exact record_fields name','conditions':[{'field':'exact field','op':'exact supplied operator','value':'scalar'}],
                  'window':{'basis':'all_observed','start':'','end':''}},
        'concept':{'kind':'concept','concept':'clinical_concepts id (condition, or measurement = any result)',
                   'window':{'basis':'all_observed','start':'','end':''}},
        'measurement':{'kind':'measurement','concept':'clinical_concepts.measurements id','op':'lt|le|gt|ge|eq|ne',
                       'value':'number in the concept unit','aggregate':'any or latest',
                       'window':{'basis':'all_observed','start':'','end':''}},
        'concept_rule':'For diagnoses, labs and vitals use concept/measurement atoms: they match every coding system and label actually received. '
                       'Use raw record atoms only for non-clinical attributes listed in record_fields (values shown are the observed vocabulary).',
        'specification':{'all':[],'any':[],'exclude':[]},
        'rule':'Every atom must include kind. For one named patient put a patient_id eq atom in all. Never omit the patient restriction.'}
    catalog={'question': question, 'snapshot': {'as_of':snapshot['as_of']},
        'node_contracts': {op:{k:v for k,v in c.items() if k!='inputs' or v!=['earlier_set']} for op,c in CONTRACTS.items()},
        'graph_contract_version':graph_contracts.VERSION,
        'source_streams':sorted(set(fields['record_fields']) | set(longitudinal.get('vocabulary',{}).get('stream',[]))),
        'dependency_contract':{'membership_operations':sorted(graph_contracts.SET_OPS),
            'rule':'Only earlier membership outputs can feed population inputs. Reports cannot be unioned or used as populations. reference_review accepts population first, then distinct supported earlier evidence/report nodes declared in bindings; its output is a report. Baseline requires a direct outcome input. Worklist accepts membership first, then distinct optional accountability_review, program_acquisition, measure_review reports.'},
        'cohort_fields': fields, 'observed_values': observed, 'evidence_catalog': compact_catalog,
        'analytic_operators': [[k,v['purpose']] for k,v in CATALOG.items()]}
    if semantic is not None: catalog['semantic_layer'] = semantic
    catalog['node_contracts']['worklist']['parameters']={'program':'Exact work_programs id; all currently registered programs are supplied.'}
    body = {'model': PersistentGateway.MODELS['claude'], 'max_tokens': 6000, 'system': instructions,
        'messages': [{'role': 'user', 'content': serialize(catalog)}],
        'output_config': {'format': {'type': 'json_schema', 'schema': SCHEMA}}}

    def parse(raw):
        if raw.get('stop_reason') != 'end_turn':
            raise ValueError('Incomplete population graph')
        graph = json.loads(''.join(x['text'] for x in raw['content'] if x.get('type') == 'text'))
        nodes = []
        for wire in graph['nodes']:
            if set(wire) != {'id', 'operation', 'inputs', 'parameters_json', 'purpose'} or not isinstance(wire['parameters_json'], str) or len(wire['parameters_json']) > 12000:
                raise ValueError('Invalid wire graph node')
            parameters = json.loads(wire['parameters_json'])
            if wire['operation'] == 'cohort': normalize_specification(parameters.get('specification'))
            nodes.append({k: v for k, v in wire.items() if k != 'parameters_json'} | {'parameters': parameters})
        graph = validate({**graph, 'nodes': nodes}, snapshot['as_of'])
        for node in graph['nodes']:
            if node['operation'] == 'cohort':
                for atom in sum(node['parameters']['specification'].values(), []):
                    if atom.get('kind') == 'patient' and atom.get('field') == 'patient_id' and str(atom['value']) not in question:
                        raise ValueError('Planner supplied a patient ID absent from the question')
        return graph

    from .planner_scope import dispatch
    call = lambda b, **kw: send(b, 'claude', ledger, path, **kw)
    try:
        return dispatch(body, catalog, call, parse=parse)
    except (ValueError, KeyError, TypeError) as exc:
        if 'bound' in str(exc): raise
        # One repair attempt: the same request plus the validator's message.
        retry = {**body, 'system': body['system'] + f' A previous plan for this question failed validation with: {str(exc)[:400]}. Return a corrected graph that satisfies the contracts.'}
        graph, receipt = dispatch(retry, catalog, call, parse=parse)
        return graph, {**receipt, 'repaired_after_validation_error': str(exc)[:400]}


ATOM_ALIASES = {'where': 'conditions', 'filters': 'conditions', 'predicates': 'conditions', 'clauses': 'conditions'}


def normalize_specification(spec):
    """Heal common wire slips (alias keys, missing groups) before strict validation."""
    if not isinstance(spec, dict): return
    for group in ('all', 'any', 'exclude'):
        spec.setdefault(group, [])
        for atom in spec[group] if isinstance(spec[group], list) else []:
            if not isinstance(atom, dict): continue
            for alias, name in ATOM_ALIASES.items():
                if alias in atom and name not in atom: atom[name] = atom.pop(alias)
            if atom.get('kind') == 'record': atom.setdefault('window', {'basis': 'all_observed', 'start': '', 'end': ''})
            if atom.get('kind') == 'measurement': atom.setdefault('aggregate', 'any')
