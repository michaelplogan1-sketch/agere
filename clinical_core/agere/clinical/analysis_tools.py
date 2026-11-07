"""Existing analytical engines on the investigator's native tool boundary.

No new analytical formula or question handler. The shared graph validates and
executes the existing operators; exact request scope travels as membership.
"""
from ...contracts import digest
from ..request_lineage import component

# Analytical reports only. Stateful identity decisions, monitors and care work
# retain their own explicit workflows and are never certified read-only replay.
DOCUMENTARY={'assertion_review','source_state'}
ASSERTION_PROJECTIONS={
    'assertions':'Every bound assertion with its exact value/unit/domain, source, qualifiers, clocks and unresolved meaning.',
    'qualifier_effects':'Assertions with nonaffirmative, nonpatient, noncurrent, unresolved or repeated-source qualifiers. Candidate accounting effects, not verified clinical changes.',
    'dated_accounts':'Intact relation/domain-specific dated account groups. Opposing reports across time are not necessarily clinical contradictions.',
    'unbound_annotations':'Every selected or unknown-window annotation whose source binding failed; retain its original value and failure reason.'}
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
            **({'selection_contracts':{'inherited_annotations':{
                'meaning':'Native code binds every received annotation source stream and the exact inherited population. Do not supply patient_ids/patient_query_sql or invent a source subset.',
                'parameters':'Supply only the requested assertion_window. Code supplies streams, concepts=[] and received_annotations=true before normal shared graph validation.',
                'resolved_streams':received_annotation_sources(path),
                'scope':'Existing annotations only; no complete free-text or clinical-state claim.'}}}
               if operation=='assertion_review'else{}),
            **({'output_contracts':{'qualifier_accounts':{
                'meaning':'Complete qualifier-sensitive bound accounts plus every selected/unknown unbound annotation. Exact source/value/unit/clocks, native group references and provisional meanings remain explicit.',
                'usage':'Set output=qualifier_accounts to obtain the requested source-account audit as the primary native table. Keep every audit_account_id in a table answer; examples may supplement this rowset, not replace it.',
                'clinical_state_established':False}}}if operation=='assertion_review'else{}),
            'meaning':'The existing native validator remains authoritative. Parameters and assumptions are not inferred clinical facts.',
            **({'output_contract':{
                'columns':['patient_id','receipt_id','status','documentary_receipt'],
                'native_projections':ASSERTION_PROJECTIONS,
                'collections':{
                    'assertions':{'json_path':'$.assertions','fields':{
                        'id':'Stable source assertion identity','annotation_origin':'derived_note_fact only for derived note-fact proposals',
                        'concept':'Mapped concept or received fact-type label','relation':'Documentary relation',
                        'value':'Exact represented value; do not infer missing numerical values from concept labels',
                        'unit':'Exact derived fact unit when supplied','value_domain':'Derived fact type, entity qualifier, value and unit; keep domains separate',
                        'polarity':'Received/proposed polarity','subject':'Received/proposed subject','temporality':'Received/proposed time',
                        'clocks':'Separate source_date, arrival_date, clinical_date and reference_date',
                        'source':'Original record_id, patient_id, record_hash and source dates',
                        'evidence':'Exact field/start/end/quote for narrative assertions',
                        'assertion_window_state':'selected or unknown when an assertion window is supplied',
                        'semantic_validation':'Machine meanings remain pending; exact source binding does not qualify them',
                        'source_repetition':'Original anchors can precede the output window; repetition cannot establish independent corroboration',
                        'qualifier_effects':'Derived-annotation accounting effects, not clinical conclusions'}},
                    'groups':{'json_path':'$.groups','meaning':'Relation/domain-specific dated accounts, opposing reports, exclusions and unresolved clocks; no voting or clinical truth'},
                    'unbound_annotations':{'json_path':'$.coverage.unbound_annotation_rows',
                        'meaning':'Retained derived rows with failed source bindings, original annotation values/units and selected/unknown/outside window state'}},
                'summary_paths':{'assertions':'$.counts.assertions','selected_assertions':'$.coverage.assertion_window.assertions_selected',
                    'unknown_window_assertions':'$.coverage.assertion_window.assertions_with_unknown_date',
                    'outside_window_assertions':'$.coverage.assertion_window.assertions_outside_window',
                    'clinical_truth':'$.current_clinical_state_established'},
                'instruction':'Execution returns native_projections with executable query_sql inputs bound to its complete result_view. Use them to compose the full assertion/qualifier/domain audit rather than inferring meaning from broad note_fact groups. A patient preview never substitutes for the full population or assertion window. Preserve failed bindings and unvalidated meaning.'}}
               if operation=='assertion_review'else{}),
            **context,
            **({'documentary_scope':'Explicit streams and mapped concepts at the current snapshot. assertion_review optionally accepts assertion_window with an explicit date basis and received_annotations=true to include existing annotations with all values/units and failed bindings; concepts=[] then means all existing annotations in those streams, not all clinical knowledge. Selecting patients with a dated record alone does not restrict assertion dates. Unknown qualifiers and machine interpretations remain unresolved; repeated text is not independent corroboration.'}
               if operation in DOCUMENTARY else {})}


def received_annotation_sources(path):
    """Exact native source inventory, independent of familiar stream names."""
    import sqlite3
    from pathlib import Path
    from . import longitudinal
    with sqlite3.connect((Path(path)/'semantic.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)as db:
        streams={r[0]for r in db.execute('SELECT DISTINCT stream FROM note_fact')}
    events,_=longitudinal.connect(path)
    try:streams|={r[0]for r in events.execute("SELECT DISTINCT stream FROM events WHERE kind='text'")}
    finally:events.close()
    if not streams:raise ValueError('No received annotation source streams established')
    return sorted(streams)


def inherited_annotation_parameters(path,parameters):
    if set(parameters)-{'streams','concepts','received_annotations','assertion_window'}:
        raise ValueError('Unknown inherited annotation parameter')
    if (parameters.get('streams')not in (None,[])or parameters.get('concepts')not in (None,[])
        or 'received_annotations'in parameters and parameters['received_annotations']is not True):
        raise ValueError('Inherited annotations bind all sources and existing annotations; use selection=parameters for an explicit restricted audit')
    return {**parameters,'streams':received_annotation_sources(path),'concepts':[],'received_annotations':True}


def assertion_projections(view):
    """Read-only projections of checked receipts; no new reading or inference.

    Bind to the actual request-local result, never a model-supplied table or an
    arbitrary source. All evidence objects and account groups remain intact.
    """
    import re
    if not isinstance(view,str) or not re.fullmatch(r'agere_result_[1-9][0-9]*',view):
        raise ValueError('Native assertion projections require an executed result_view')
    fields={
        'assertion_id':'id','concept':'concept','relation':'relation','value':'value','unit':'unit',
        'value_domain':'value_domain','polarity':'polarity','subject':'subject','temporality':'temporality',
        'authority':'authority','semantic_validation':'semantic_validation','annotation_origin':'annotation_origin',
        'assertion_window_state':'assertion_window_state','source_record_id':'source.record_id',
        'source_date':'clocks.source_date','arrival_date':'clocks.arrival_date',
        'clinical_date':'clocks.clinical_date','reference_date':'clocks.reference_date',
        'source':'source','evidence':'evidence','source_repetition':'source_repetition',
        'repeated_statement_family':'repeated_statement_family',
        'qualifier_effects':'qualifier_effects','positive_under_received_qualifiers':'positive_under_received_qualifiers',
        'current_clinical_state_established':'current_clinical_state_established'}
    columns=', '.join(f"json_extract(a.value,'$.{path}') AS {key}"for key,path in fields.items())
    assertions=f"SELECT r.patient_id,r.receipt_id,{columns} FROM {view} r JOIN json_each(r.documentary_receipt,'$.assertions') a"
    effects=(f'WITH assertions AS ({assertions}) SELECT * FROM assertions WHERE '
        "COALESCE(polarity,'unknown')<>'affirmed' OR COALESCE(subject,'unknown')<>'patient' "
        "OR COALESCE(temporality,'unknown')<>'current' OR assertion_window_state='unknown' "
        "OR COALESCE(json_array_length(qualifier_effects),0)>0 "
        "OR source_repetition IS NOT NULL")
    groups=(f"SELECT r.patient_id,r.receipt_id,json_extract(g.value,'$.concept') AS concept,"
        "json_extract(g.value,'$.relation') AS relation,json_extract(g.value,'$.value_domain') AS value_domain,"
        "json_extract(g.value,'$.status') AS account_status,g.value AS dated_account_group "
        f"FROM {view} r JOIN json_each(r.documentary_receipt,'$.groups') g")
    unbound=(f"SELECT r.patient_id,r.receipt_id,json_extract(u.value,'$.id') AS annotation_id,"
        "json_extract(u.value,'$.reason') AS binding_failure,json_extract(u.value,'$.assertion_window_state') AS assertion_window_state,"
        "json_extract(u.value,'$.source_date') AS source_date,json_extract(u.value,'$.arrival_date') AS arrival_date,"
        "json_extract(u.value,'$.annotation') AS received_annotation,u.value AS unbound_account,"
        "0 AS source_binding_established,0 AS current_clinical_state_established "
        f"FROM {view} r JOIN json_each(r.documentary_receipt,'$.coverage.unbound_annotation_rows') u "
        "WHERE COALESCE(json_extract(u.value,'$.assertion_window_state'),'selected') IN ('selected','unknown')")
    return {key:{'meaning':ASSERTION_PROJECTIONS[key],'query_sql':{'sql':sql,'complete':True}}
            for key,sql in {'assertions':assertions,'qualifier_effects':effects,
                            'dated_accounts':groups,'unbound_annotations':unbound}.items()}


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


def qualifier_account_rows(patients):
    """Native documentary effects, never diagnosed clinical consequences.

    The original complete patient receipts remain hash-addressed in the engine
    report/cache. This primary output carries each affected bound assertion and
    every selected/unknown failed binding, without inventing clinical state.
    """
    rows=[];assertions=0;group_count=0;unbound_count=0
    fields=('concept','relation','value','unit','value_domain','polarity','subject','temporality','authority',
        'semantic_validation','annotation_origin','assertion_window_state','evidence','source_repetition','qualifier_effects',
        'positive_under_received_qualifiers','source_structure','reader_review_reasons')
    for patient in patients:
        receipt=patient['documentary_receipt'];pid=patient['patient_id'];groups={};exclusions={}
        assertions+=len(receipt['assertions']);group_count+=len(receipt['groups'])
        for group in receipt['groups']:
            ids={i for a in group['dated_accounts']for i in a['assertion_ids']}
            ids.update(a['assertion_id']for a in group['excluded_from_dated_accounts'])
            for item in group['excluded_from_dated_accounts']:
                exclusions.setdefault(item['assertion_id'],[]).append(item['reason'])
            ids.update(i for family in group['repeated_statement_groups']for i in family['assertion_ids'])
            ref={'group_digest':digest(group),'status':group['status']}
            for ident in ids:
                if ident in groups and groups[ident]!=ref:raise ValueError('Ambiguous native assertion group')
                groups[ident]=ref
        for a in receipt['assertions']:
            if not (a.get('polarity','unknown')!='affirmed'or a.get('subject','unknown')!='patient'
                or a.get('temporality','unknown')!='current'or a.get('assertion_window_state')=='unknown'
                or a.get('qualifier_effects')or a.get('source_repetition')is not None or exclusions.get(a['id'])
                or (groups.get(a['id'])or{}).get('status')in ('same_date_conflict','opposing_reports_over_time_or_unresolved_clock')):continue
            effects=[]
            if a.get('polarity')=='negated':effects.append('negated_proposal_not_affirmed_evidence')
            elif a.get('polarity')!='affirmed':effects.append('polarity_unresolved')
            if a.get('temporality')!='current':effects.append('not_established_as_current')
            if a.get('subject')!='patient':effects.append('patient_subject_not_established')
            if a.get('source_repetition')is not None:effects.append('repetition_not_independent_corroboration')
            if (a.get('source_repetition')or{}).get('cannot_refresh_state'):effects.append('repetition_cannot_refresh_state')
            if a.get('assertion_window_state')=='unknown':effects.append('assertion_window_date_unresolved')
            if (groups.get(a['id'])or{}).get('status')in ('same_date_conflict','opposing_reports_over_time_or_unresolved_clock'):
                effects.append('opposing_documentary_accounts_need_review')
            row={k:a.get(k)for k in fields}
            row.update(audit_account_id=digest([receipt['id'],a['id']]),patient_id=pid,assertion_id=a['id'],
                annotation_id=None,receipt_id=receipt['id'],source_binding_established=True,
                source=a['source'],source_record_id=a['source']['record_id'],reported_record_id=None,
                **a['clocks'],analytical_effects=effects,account_group=groups.get(a['id']),
                native_exclusion_reasons=exclusions.get(a['id'],[]),
                binding_failure=None,received_annotation=a.get('received_annotation'),
                current_clinical_state_established=False)
            rows.append(row)
        for item in receipt['coverage'].get('unbound_annotation_rows',[]):
            if item.get('assertion_window_state','selected')not in ('selected','unknown'):continue
            a=item['annotation'];row={k:None for k in fields}
            row.update(audit_account_id=digest([receipt['id'],item['id']]),patient_id=pid,assertion_id=None,
                annotation_id=item['id'],receipt_id=receipt['id'],source_binding_established=False,
                source=None,source_record_id=None,reported_record_id=a.get('record_id'),
                source_date=item.get('source_date'),arrival_date=item.get('arrival_date'),clinical_date=None,reference_date=None,
                value=a.get('value'),unit=a.get('unit'),received_annotation=a,
                assertion_window_state=item.get('assertion_window_state'),semantic_validation='unbound_proposal',
                analytical_effects=['source_binding_unresolved'],account_group=None,binding_failure=item['reason'],
                native_exclusion_reasons=[],
                current_clinical_state_established=False)
            rows.append(row);unbound_count+=1
    ids=[r['audit_account_id']for r in rows]
    if len(set(ids))!=len(ids):raise ValueError('Duplicate native qualifier account')
    scope={'schema':'native-qualifier-account-output/1','account_key':'audit_account_id','accounts':len(rows),
        'account_ids_digest':digest(sorted(ids)),'input_patients':len(patients),'input_assertions':assertions,
        'input_dated_groups':group_count,'unbound_selected_or_unknown':unbound_count,
        'whole_free_text_interpretation_established':False,'clinical_state_established':False,
        'meaning':'Complete received-qualifier accounting, conditional on unvalidated machine meanings; not established clinical changes. Full original receipts remain in the native engine report/cache.'}
    return rows,scope


def qualifier_output_scope_errors(answer,calls):
    """A table can explain an audit but cannot silently replace its rowset."""
    if answer.get('answer_type')!='table':return []
    expected=set();views=[]
    for call in calls:
        result=call.get('result')or{}
        if not isinstance(result,dict)or result.get('analysis')!='assertion_review'or result.get('output')!='qualifier_accounts':continue
        scope=result.get('native_output_scope')or{};rows=result.get('rows')
        if (scope.get('schema')!='native-qualifier-account-output/1'or scope.get('account_key')!='audit_account_id'
            or not isinstance(rows,list)):return ['Native audit output scope is unavailable; restore its original receipt.']
        if any(not isinstance(r,dict)for r in rows):return ['Native audit rows are unavailable.']
        keys=[r.get('audit_account_id')for r in rows]
        if(any(not isinstance(k,str)or not k for k in keys)or len(set(keys))!=len(keys)
            or len(keys)!=scope.get('accounts')or digest(sorted(keys))!=scope.get('account_ids_digest')):
            return ['Native audit account identities changed; restore the original complete result.']
        expected.update(keys);views.append(result.get('result_view'))
    if not views:return []
    table=answer.get('table')or[]
    actual=[r.get('audit_account_id')for r in table if isinstance(r,dict)and r.get('audit_account_id')is not None]
    if any(not isinstance(k,str)or not k for k in actual):return ['Keep literal native audit_account_id values in the complete audit table.']
    if len(actual)!=len(set(actual))or set(actual)!=expected:
        return ['Preserve every native audit_account_id exactly once in the table. Missing='+str(len(expected-set(actual)))+
            ', extra='+str(len(set(actual)-expected))+', duplicates='+str(len(actual)-len(set(actual)))+
            '. Use the complete native audit result '+', '.join(str(v)for v in views)+
            ' as table_from_tool, or a source-equivalent projection retaining its account IDs. Do not substitute unrequested clinical themes or examples.']
    return []


def qualifier_coverage_review(answer,calls,tools,as_of):
    """Prove a complete native audit copy for coverage, never source meaning.

    Rebuild the primary projection from every hash-checked original receipt.
    A matching table may be represented by its complete field/role contract in
    the coverage question. All patient source-support obligations remain.
    """
    import copy,hashlib
    from pathlib import Path
    from collections import Counter
    from . import assertion_reconciliation as AR
    from .harness_improvement import semantic_result
    from .result_assembly import incomplete_reason
    root=answer.get('table_from_tool');matches=[c for c in calls if isinstance(c.get('result'),dict)
        and c['result'].get('result_view')==root]
    if not root:return None
    if len(matches)!=1:
        if any(c['result'].get('output')=='qualifier_accounts'for c in matches):
            raise ValueError('Native audit coverage binding: ambiguous original view')
        return None
    call=matches[0];result=call['result']
    if(call.get('name')=='query_sql'and answer.get('answer_type')=='table'
        and any(isinstance(row,dict)and'audit_account_id'in row for row in answer.get('table')or[])):
        # Until its complete projection contract is checked, a SQL descendant
        # must not silently fall back to the large general review of this audit.
        # Actual SQLite read bindings avoid guessing dependencies from literals.
        by={c['result']['result_view']:c['result']for c in calls if isinstance(c.get('result'),dict)
            and c['result'].get('result_view')};pending=list(result.get('read_views')or[]);seen=set()
        while pending:
            view=pending.pop()
            if view in seen:continue
            seen.add(view);parent=by.get(view)or{}
            if parent.get('analysis')=='assertion_review'and parent.get('output')=='qualifier_accounts':
                raise ValueError('Native audit coverage binding: SQL projection is not yet covered by the checked native audit role contract. '
                    'Retain this projection and its original evidence. Use the complete primary native audit '+view+
                    ' for the verified copy path; additional interpreted fields still require their own review. Do not dispatch the unchanged large general coverage input.')
            pending.extend(parent.get('read_views')or[])
    if(call.get('name')!='execute_analysis'or result.get('analysis')!='assertion_review'
        or result.get('output')!='qualifier_accounts'or not result.get('rows')):return None
    def require(condition,message):
        if not condition:raise ValueError('Native audit coverage binding: '+message)
    require(not result.get('error')and not incomplete_reason(result),'complete original output required')
    require((answer.get('table_binding')or{}).get('result_view')==root,'native table binding required')
    require(not (answer.get('table_binding')or{}).get('interpreted_columns'),'interpreted columns require their own source review')
    require(digest(answer.get('table'))==digest(result['rows']),'every original cell and row order must remain exact')
    require(not qualifier_output_scope_errors(answer,calls),'complete account identities required')
    registered=getattr(getattr(getattr(tools,'semantic',None),'_local',None),'native_results',{}).get(root.casefold())or{}
    original_digest=digest({k:v for k,v in result.items()if k!='result_view'})
    require(registered.get('receipt_digest')==original_digest,'registered native origin changed or unavailable')
    engine=result.get('engine_result')or{}
    require(result.get('receipt_digest')==digest(semantic_result(engine)),'engine provenance changed')
    report=(engine.get('results')or[{}])[-1].get('result')or{}
    parameters=AR.validate(result.get('parameters'))
    require(report.get('parameters')==parameters,'native parameters changed')
    identity,meta,_=AR.dependency(tools.stack.path,parameters)
    require(meta['snapshot']['as_of']==as_of and report.get('identity')==identity,'source, clock or native operator changed')
    ids=result.get('input_patient_ids');members=report.get('review_members')or[]
    if ids is None:
        require(getattr(tools,'cohort',None)is None,'implicit population differs from inherited scope')
        ids=[r[0]for r in tools.semantic.db.execute('SELECT patient_id FROM patient')]
    require(isinstance(ids,list)and len(ids)==len(set(ids))==len(members)==report.get('patients_evaluated')
        and set(ids)=={m['patient_id']for m in members},'native population changed')
    inherited=getattr(tools,'cohort',None)
    require(inherited is None or set(ids)<=set(inherited),'native population is outside inherited scope')
    if result.get('input_selection')=='inherited_annotations':
        require(inherited is None or set(ids)==set(inherited),'inherited native population narrowed')
    code=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    key=digest([original_digest,digest(answer['table']),identity,code,as_of])
    cached=getattr(tools,'_qualifier_coverage_reviews',{})
    if key in cached:return copy.deepcopy(cached[key])
    originals=documentary_rows(tools.stack.path,'assertion_review',report)
    expected,scope=qualifier_account_rows(originals)
    require(digest(expected)==digest(result['rows'])and digest(scope)==digest(result.get('native_output_scope')),
        'audit values, qualifiers, effects, domains or clocks differ from original receipts')
    require(AR.dependency(tools.stack.path,parameters)[0]==identity,'source changed during proof')
    fields=list(dict.fromkeys(k for row in expected for k in row))
    require(result.get('columns')==fields,'complete original field manifest required')
    effect_meanings={
        'negated_proposal_not_affirmed_evidence':'Conditional on the received polarity label, this proposal cannot be counted as affirmative evidence.',
        'polarity_unresolved':'The received polarity is unresolved; neither affirmation nor negation is established.',
        'not_established_as_current':'The received temporality does not establish a current assertion at the source statement.',
        'patient_subject_not_established':'The received subject does not establish that this assertion concerns the patient.',
        'repetition_not_independent_corroboration':'Repeated source text cannot be counted as independent corroboration.',
        'repetition_cannot_refresh_state':'A repeated source statement cannot acquire a newer clinical date from its transport.',
        'assertion_window_date_unresolved':'The source date does not establish whether this assertion belongs to the requested window.',
        'opposing_documentary_accounts_need_review':'Native relation/domain grouping contains opposing accounts; this is not itself an established clinical contradiction.',
        'source_binding_unresolved':'The annotation did not bind to a verified source assertion; retain its failure and value without a source/clinical claim.'}
    effects=Counter(e for row in expected for e in row['analytical_effects'])
    require(set(effects)<=set(effect_meanings),'unknown conditional effect')
    proof={'schema':'native-qualifier-coverage/1','result_view':root,'rows':len(expected),'columns':fields,
        'table_digest':digest(expected),'registered_origin_digest':original_digest,
        'source_digest':meta['source_digest'],'as_of':as_of,'native_dependency_identity':identity,
        'projection_code_digest':code,'parameters':parameters,
        'population':{'input_patients':len(ids),'patient_ids_digest':digest(sorted(ids)),
            'patients_with_audit_accounts':len({r['patient_id']for r in expected})},
        'original_patient_receipts':{'count':len(members),'manifest_digest':digest(members),'all_hash_checked':True},
        'output_scope':scope,'conditional_effect_counts':dict(effects),'conditional_effect_definitions':effect_meanings,
        'qualifier_account_distributions':{field:dict(Counter(str(r.get(field))for r in expected))
            for field in ('source_binding_established','polarity','subject','temporality','assertion_window_state','semantic_validation')},
        'mechanically_checked':['Every original patient receipt hash','Every projected account, cell, object, row order and account ID',
            'Exact native conditional-effect transformation','Exact source/value/unit/domain and separate clocks','Complete population and parameter binding'],
        'not_established':['Machine annotation meanings','Complete free-text interpretation','Clinical consequences or eligibility',
            'Snapshot-current care','A clinical contradiction from opposing reports alone'],
        'separate_source_support_required':True,'clinical_semantic_acceptance':False,
        'scope':'Coverage-only representation of a complete native received-label audit. Conditional effect enums explain how received qualifiers affect counting; their presence does not prove that machine labels or clinical interpretations are correct. All actual rows and original evidence remain native. Source support still receives full patient dependencies. Assess whether the requested interpretation is actually complete; a faithful provisional audit may still be an incomplete answer.'}
    cached[key]=copy.deepcopy(proof);tools._qualifier_coverage_reviews=cached
    return proof


def execute(tools,operation,parameters,patient_ids=None,patient_query_sql=None,selection='parameters',output='operator_default'):
    from . import analysis_graph as A,graph_contracts as G,cohorts
    if operation not in ANALYTICAL:raise ValueError('Choose an analytical operation from list_analyses')
    if not isinstance(parameters,dict):raise ValueError('Supply typed analytical parameters')
    guard=digest(tools.continuation_identity())
    if output not in ('operator_default','qualifier_accounts')or output=='qualifier_accounts'and operation!='assertion_review':
        raise ValueError('Qualifier account output requires assertion_review')
    if selection not in ('parameters','inherited_annotations'):raise ValueError('Unknown native input selection')
    if selection=='inherited_annotations':
        if operation!='assertion_review' or patient_ids is not None or patient_query_sql is not None:
            raise ValueError('Inherited annotation selection requires assertion_review and the exact inherited population without overrides')
        parameters=inherited_annotation_parameters(tools.stack.path,parameters)
    ids=population(tools,patient_ids,patient_query_sql)
    snapshot=tools.stack.retriever.meta['snapshot']['as_of']
    if str(snapshot)[:10]!=tools.as_of:raise ValueError('Analytical snapshot differs from request date')
    if G.input_contract(operation)['count'][1]<1:raise ValueError('This operation cannot inherit a patient population')
    plan={'interpretation':'Received-source analytical tool execution','coverage':'supported_observation','unmet_requirements':[],
        'nodes':[{'id':'input','operation':'cohort','inputs':[],
                  'parameters':{'specification':cohorts.empty(),**({'patient_ids':ids} if ids is not None else {})},'purpose':'Exact request population'},
                 {'id':'analysis','operation':operation,'inputs':['input'],'parameters':parameters,'purpose':'Requested analytical calculation'}]}
    plan=A.validate(plan,tools.as_of)  # even an empty input must not accept malformed parameters
    if ids==[]:
        if digest(tools.continuation_identity())!=guard:raise ValueError('Source/scope/definitions changed during selection')
        empty_scope=qualifier_account_rows([])[1]if output=='qualifier_accounts'else None
        return {'analysis':operation,'status':'no_matches','columns':['audit_account_id','patient_id']if empty_scope is not None else[],
                'rows':[],'input_patient_ids':[],
                **({'input_selection':selection,'parameters':plan['nodes'][-1]['parameters']}if selection!='parameters'else{}),
                **({'output':output,'native_output_scope':empty_scope}if empty_scope is not None else{}),
                'method':'Explicit empty population; no estimate computed and engine not executed'}
    with component('analytical_engine',operation=operation,population_size=None if ids is None else len(ids)):
        result=A.execute(tools.stack,plan)
    answer=result.get('answer') or {};rows=answer.get('table') or []
    if operation in PERSISTED_REPORTS:
        rows=documentary_rows(tools.stack.path,operation,result['results'][-1]['result'])
    scope=None
    if output=='qualifier_accounts':rows,scope=qualifier_account_rows(rows)
    if digest(tools.continuation_identity())!=guard:raise ValueError('Source/scope/definitions changed during analysis')
    if not isinstance(rows,list):raise ValueError('Native analysis did not return a table')
    columns=list(dict.fromkeys(k for row in rows for k in row)) if all(isinstance(r,dict) for r in rows) else []
    if scope is not None and not columns:columns=['audit_account_id','patient_id']
    from .harness_improvement import semantic_result
    return {'analysis':operation,'status':answer.get('status'),'columns':columns,'rows':rows,'answer':answer,
            'input_patient_ids':ids,'parameters':plan['nodes'][-1]['parameters'],'engine_result':result,
            **({'input_selection':selection}if selection!='parameters'else{}),
            **({'output':output,'native_output_scope':scope}if scope is not None else{}),
            'method':answer.get('method'),'limitations':answer.get('limitations') or [],
            'receipt_digest':digest(semantic_result(result)),'new_paid_api_calls':result.get('new_paid_api_calls',0)}
