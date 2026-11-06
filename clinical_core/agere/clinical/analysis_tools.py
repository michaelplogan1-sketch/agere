"""Existing analytical engines on the investigator's native tool boundary.

No new analytical formula or question handler. The shared graph validates and
executes the existing operators; exact request scope travels as membership.
"""
from ...contracts import digest
from ..request_lineage import component

# Analytical reports only. Stateful identity decisions, monitors and care work
# retain their own explicit workflows and are never certified read-only replay.
DOCUMENTARY={'assertion_review','source_state'}
CRITERIA={'requirements','protocol_review','program_document'}
PERSISTED_REPORTS=DOCUMENTARY | CRITERIA | {'financial_accounts','accountability_review'}
ANALYTICAL=PERSISTED_REPORTS | {
    'aggregate','trajectory','survival','queueing','profile','calibration','demand','program_effect','uplift',
    'monitor','market','pathway','scheduling','early_signal','claims_review','network','contract_strategy',
    'target_trial','line_of_therapy','trial_feasibility','risk_outcomes','diagnostic_delay','loop_closure','vbc',
    'risk_adjustment','outreach_allocation','staffing_plan','mlr','star_rating','settlement_bridge',
    'change_attribution','social_needs','denial_prediction','cdi_opportunity','contract_reimbursement',
    'price_benchmark','prior_auth_rules','eligibility_340b','provider_network_graph','network_spread',
    'upcoding_detection','benchmark_rebasing','survey_adjustment','scorecard_risk','visit_groups',
    'capacity_simulation','discharge_forecast','flow_decomposition','or_block_optimization','hai_surveillance',
    'antimicrobial_use','bundle_compliance','spatiotemporal_cluster','overbooking_fairness','network_adequacy',
    'care_gap_closure','transitions_of_care','policy_evaluation','guideline_conformance','outreach_bandit','policy_search',
    'library_measure'}


def list_analyses():
    from .capability_registry import PRODUCT
    from .graph_planner import CONTRACTS
    return {'analyses':[{'operation':op,**(PRODUCT[op] if op in PRODUCT else
                      {'population':'required','view':'table','use':CONTRACTS[op]['meaning']})}
                      for op in sorted(ANALYTICAL & (PRODUCT.keys() | PERSISTED_REPORTS))],
            'instruction':'Describe an operation for its typed parameters; execute it with the exact inherited or supplied population. No clinical actions are dispatched.'}


def describe(operation, *, path=None):
    from . import graph_contracts as G
    if operation not in ANALYTICAL:raise ValueError('Choose an analytical operation from list_analyses')
    catalog={'cohort_fields':{'record_fields':{}},'evidence_catalog':{},'source_streams':[]}
    if operation in DOCUMENTARY:
        if path is None:raise ValueError('Documentary discovery requires the current source path')
        catalog=G.documentary_parameter_catalog(path)
    context={}
    if operation in CRITERIA or operation=='accountability_review':
        if path is None:raise ValueError('Criteria discovery requires the current source path')
        import json
        from pathlib import Path
        from . import care_protocols as C,evidence_requirements as R,program_documents as D,care_capabilities
        as_of=json.loads((Path(path)/'run.json').read_text())['snapshot']['as_of']
        programs=D.discovery(path,as_of)
        protocols={**C.read_catalog(C.HISTORY),**C.read_catalog(C.CATALOG)}
        catalog['evidence_catalog'].update(documentary_contracts=list(R.definitions().values()),
            program_documents=programs['quoted_packages'],protocol_versions=sorted(protocols))
        context={'criteria_context':programs,'buyer_context':care_capabilities.declared_context(path,as_of)}
        if operation=='protocol_review':
            context['protocol_definitions']=[{'id':key,**s,'effective_period_established':False,
                'scope':'Guideline-informed documentary review definition. Source checked_on is not an effective or expiry date; no dated payer/program applicability is inferred.'}
                for key,s in sorted(protocols.items())]
        elif operation=='requirements':context['requirement_definitions']=list(R.definitions().values())
    return {'operation':operation,'parameters':G.parameter_schema(operation,catalog),
            'population_contract':G.input_contract(operation),
            'meaning':'The existing native validator remains authoritative. Parameters and assumptions are not inferred clinical facts.',
            **context,
            **({'documentary_scope':'Only explicitly mapped concepts and streams at the current snapshot. Selecting patients with a dated record does not restrict every assertion to that date window. Unknown qualifiers and machine interpretations remain unresolved; repeated text is not independent corroboration.'}
               if operation in DOCUMENTARY else {})}


def population(tools,patient_ids=None,patient_query_sql=None):
    if patient_ids is not None and patient_query_sql is not None:raise ValueError('Choose patient_ids or patient_query_sql')
    if patient_query_sql is not None:
        from .sql_query import validate as validate_sql
        validate_sql({'query':patient_query_sql,'returns':'patient_set'})
        out=tools._call({'name':'query_sql','input':{'sql':patient_query_sql,'complete':True}})
        if out.get('columns')!=['patient_id'] or out.get('truncated'):raise ValueError('Complete population query must return exactly patient_id')
        patient_ids=[r['patient_id'] if isinstance(r,dict) else r[0] for r in out['rows']]
    if patient_ids is None:return tools.cohort
    if not isinstance(patient_ids,list) or any(not isinstance(p,str) or not p for p in patient_ids):raise ValueError('Use received patient IDs')
    for pid in patient_ids:
        tools.in_scope(pid)
        if tools.db.execute('SELECT 1 FROM patient WHERE patient_id=?',(pid,)).fetchone() is None:raise ValueError('Unreceived patient: '+pid)
    return list(dict.fromkeys(patient_ids))


def documentary_rows(path,operation,report):
    """Complete, hash-bound patient receipts, not the engine's small preview."""
    import json,zlib
    from . import assertion_reconciliation,source_state,financial_accounts,program_accountability,graph_criteria
    modules={'assertion_review':(assertion_reconciliation,'receipts'),
             'source_state':(source_state,'source_states'),'financial_accounts':(financial_accounts,'receipts'),
             'accountability_review':(program_accountability,'receipts')}
    modules.update({op:(module,'receipts') for op,module in graph_criteria.MODULES.items()})
    if operation not in modules:raise ValueError('Unknown persisted native report')
    module,table=modules[operation]
    members=report['review_members']
    if len(members)!=report['patients_evaluated'] or len({m['patient_id'] for m in members})!=len(members):
        raise ValueError('Incomplete documentary population receipt')
    db=module.storage(path);rows=[]
    try:
        for member in members:
            raw=db.execute('SELECT payload FROM '+table+' WHERE id=?',(member['receipt_id'],)).fetchone()
            if raw is None:raise ValueError('Missing documentary patient receipt')
            row=json.loads(zlib.decompress(raw[0]))
            if row['patient_id']!=member['patient_id'] or row['id']!=member['receipt_id'] or digest(row)!=member['receipt_digest']:
                raise ValueError('Documentary patient receipt mismatch')
            if operation=='accountability_review'and row['payment_readiness']!=member['status']:
                raise ValueError('Accountability readiness differs from its source receipt')
            if operation in CRITERIA and row['status']!=member['status']:
                raise ValueError('Criterion status differs from its source receipt')
            rows.append({'patient_id':row['patient_id'],'receipt_id':row['id'],'status':member['status'],
                         'documentary_receipt':row})
    finally:db.close()
    return rows


def execute(tools,operation,parameters,patient_ids=None,patient_query_sql=None):
    from . import analysis_graph as A,graph_contracts as G,cohorts
    if operation not in ANALYTICAL:raise ValueError('Choose an analytical operation from list_analyses')
    if not isinstance(parameters,dict):raise ValueError('Supply typed analytical parameters')
    ids=population(tools,patient_ids,patient_query_sql)
    snapshot=tools.stack.retriever.meta['snapshot']['as_of']
    if str(snapshot)[:10]!=tools.as_of:raise ValueError('Analytical snapshot differs from request date')
    if G.input_contract(operation)['count'][1]<1:raise ValueError('This operation cannot inherit a patient population')
    guard=digest(tools.continuation_identity())
    plan={'interpretation':'Received-source analytical tool execution','coverage':'supported_observation','unmet_requirements':[],
        'nodes':[{'id':'input','operation':'cohort','inputs':[],
                  'parameters':{'specification':cohorts.empty(),**({'patient_ids':ids} if ids is not None else {})},'purpose':'Exact request population'},
                 {'id':'analysis','operation':operation,'inputs':['input'],'parameters':parameters,'purpose':'Requested analytical calculation'}]}
    plan=A.validate(plan,tools.as_of)  # even an empty input must not accept malformed parameters
    if ids==[]:
        return {'analysis':operation,'status':'no_matches','columns':[],'rows':[],'input_patient_ids':[],
                'method':'Explicit empty population; no estimate computed and engine not executed'}
    with component('analytical_engine',operation=operation,population_size=None if ids is None else len(ids)):
        result=A.execute(tools.stack,plan)
    answer=result.get('answer') or {};rows=answer.get('table') or []
    if operation in PERSISTED_REPORTS:
        rows=documentary_rows(tools.stack.path,operation,result['results'][-1]['result'])
    if digest(tools.continuation_identity())!=guard:raise ValueError('Source/scope/definitions changed during analysis')
    if not isinstance(rows,list):raise ValueError('Native analysis did not return a table')
    columns=list(dict.fromkeys(k for row in rows for k in row)) if all(isinstance(r,dict) for r in rows) else []
    from .harness_improvement import semantic_result
    return {'analysis':operation,'status':answer.get('status'),'columns':columns,'rows':rows,'answer':answer,
            'input_patient_ids':ids,'parameters':plan['nodes'][-1]['parameters'],'engine_result':result,
            'method':answer.get('method'),'limitations':answer.get('limitations') or [],
            'receipt_digest':digest(semantic_result(result)),'new_paid_api_calls':result.get('new_paid_api_calls',0)}
