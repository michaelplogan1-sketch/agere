"""Dated patient/support accounts carried into buyer review, never inferred consent."""
from collections import Counter,defaultdict
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import care_capabilities as care,record_context,context_builder,coverage_work,participation_work,care_readiness

VERSION='received-patient-context/1'
MARKER='Received patient context: '
# Only literal fields observed in public received-source contracts are projected.
FIELDS={
 'social_screens':{'respondent','transport','transport_difficulty_reported','transport_to_medical_visits_difficult_reported','difficulty_paying_for_care_reported','food_difficulty_reported','requests_resource_contact','work_or_school_scheduling_constraint'},
 'care_support_interviews':{'respondent','help_available_reported','helper_relationship_reported','permission_to_contact_helper','legal_surrogate_status','food_access_concern_reported','housing_safety_concern_reported','transport_to_medical_visits_difficult_reported'},
 'functional_assessments':{'respondent','assessment_type','help_with_appointments_or_medications_reported','bathing_help_reported','walking_aid_reported','falls_past_year_reported'},
 'care_management_contacts':{'basis_record_id','prior_document_date','purpose','respondent','status','reason_reported','text'},
 'caregiver_contacts':{'contact_completed','contact_method','legal_surrogate_status','patient_agrees_to_discussion','relationship_reported','reported_assistance'},
 'rehabilitation_access_contacts':{'communication_result','referral_document_record_id','response','treatment_received'},
 'diagnostic_access_contacts':{'order_record_id','packet_record_id','response','text','coverage_determination','payment_guaranteed'},
 'care_coordination_contacts':{'contact_method','respondent','resource_information_provided','service_receipt_confirmed','related_referral_record_id','followup_record_id'},
}
LIMITS=['Dated source accounts are not current preferences, patient goals, clinical need or consent.',
        'Acceptance or refusal applies only to the referenced conversation, referral or order; it is not a standing choice.',
        'Reported helper permission and assistance do not establish legal surrogate authority or permission to contact anyone now.',
        'No source is selected as definitive or latest clinical truth. No service eligibility, benefit, priority score or automatic clinical execution is established.',
        'This projection covers eight received source categories, not all patient preferences or the complete chart.']

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
def database(path):
    db=sqlite3.connect(Path(path)/'patient-context.sqlite3',timeout=30)
    db.executescript('CREATE TABLE IF NOT EXISTS contexts(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS matches(plan TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS handoffs(task TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(task,revision));')
    for table in ('contexts','matches','handoffs'):
        for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Patient context history is immutable'); END")
    return db

def load(path,table,key):
    if table not in ('contexts','matches'):raise ValueError('Unknown patient context receipt')
    db=database(path)
    try:row=db.execute('SELECT payload FROM '+table+' WHERE '+('id' if table=='contexts' else 'plan')+'=?',(key,)).fetchone()
    finally:db.close()
    return json.loads(row[0]) if row else None

def prepare(path,request):
    care.exact(request,'patient_id as_of');patient=care.text(request['patient_id'],'patient ID',150);asof=care.day(request['as_of'])
    received,meta=record_context.received(path,patient)
    if asof>meta['snapshot']['as_of']:raise ValueError('Decision date exceeds the received snapshot')
    candidates=[r for r in received if r['source_table'] in FIELDS]
    rows=[r for r in candidates if r.get('available_date') and r['available_date']<=asof]
    if len(rows)>200:raise ValueError('More than 200 patient/support reports; explicit paging is needed before binding')
    successors=defaultdict(list)
    for r in rows:
        if r.get('supersedes_record_id'):successors[r['supersedes_record_id']].append(r['record_id'])
    reports=[]
    for r in sorted(rows,key=lambda r:(r.get('available_date',''),r['record_id']),reverse=True):
        clean=context_builder.clean(r);declared=set(r.get('_warehouse_fields',r)) & FIELDS[r['source_table']]
        fields={k:clean[k] for k in sorted(declared) if k in clean and (clean[k] is None or type(clean[k]) in (str,bool,int,float))}
        # Refuse oversized text instead of silently supplying a clipped preference.
        if len(json.dumps(fields,ensure_ascii=False).encode())>16000:raise ValueError('Patient context record exceeds complete display bound')
        reports.append({'record_id':r['record_id'],'record_hash':digest(r),'patient_id':patient,'source_table':r['source_table'],
            'source_date':r.get('event_date'),'available_date':r.get('available_date'),'source_system':r.get('source_system'),
            'respondent':fields.get('respondent'),'fields':fields,'unrepresented_fields':sorted(declared-set(fields)),
            'superseded_by':sorted(successors[r['record_id']]),
            'temporal_scope':'source_date_unknown' if not r.get('event_date') else 'source_date_after_decision' if r['event_date']>asof else 'dated_report_not_current_confirmation'})
    # Literal disagreement is field/source/respondent scoped, not semantic adjudication.
    groups=defaultdict(list)
    for r in reports:
        if r['superseded_by']:continue
        for field,value in r['fields'].items():
            if field in ('respondent','text') or field.endswith('_id'):continue
            groups[(r['source_table'],r['respondent'],field)].append((r,value))
    differences=[{'source_table':stream,'respondent':respondent,'field':field,'record_ids':[r['record_id'] for r,v in group],
                  'meaning':'Different reported values across dated accounts; a change or contradiction is not adjudicated.'}
                 for (stream,respondent,field),group in sorted(groups.items(),key=lambda x:str(x[0])) if len({digest(v) for r,v in group})>1]
    result={'schema':VERSION,'parameters':request,'patient_id':patient,'as_of':asof,'source_snapshot':meta['snapshot'],'source_digest':meta['source_digest'],
        'reports':reports,'source_counts':dict(Counter(r['source_table'] for r in rows)),'source_fingerprint':digest(sorted((r['record_id'],digest(r)) for r in rows)),
        'later_or_undated_receipt_count':len(candidates)-len(rows),'literal_differences':differences,'code_identity':identity(),
        'current_patient_goals':'requires_direct_confirmation','current_service_preferences':'requires_direct_confirmation','current_consent':'not_established',
        'digital_access':'not_established_by_projected_fields','authority':'received_accounts_for_review_only','limits':LIMITS,'new_paid_api_calls':0,'clinical_execution_authorized':False}
    result['id']=digest(result);db=database(path)
    try:
        with db:db.execute('INSERT OR IGNORE INTO contexts VALUES (?,?)',(result['id'],json.dumps(result,allow_nan=False)))
    finally:db.close()
    return result

def inspect(path,key):
    row=load(path,'contexts',care.text(key,'patient context ID',64))
    if not row:raise ValueError('Unknown patient context')
    if row['id']!=digest({k:v for k,v in row.items() if k!='id'}) or prepare(path,row['parameters'])['id']!=key:
        raise care.work.WorkConflict('Patient/support reports or interpretation changed; reload context')
    return row

def review_checks(context,option):
    reports=context['reports'];checks=[]
    def add(kind,text,rows):checks.append({'kind':kind,'review_question':text,'record_ids':sorted({r['record_id'] for r in rows}),'status':'requires_confirmation'})
    add('patient_goals','Confirm current patient goals and whether this particular service is wanted.',[])
    add('service_consent','Confirm current service-specific consent and the authority of anyone speaking for the patient.',[])
    transport=[r for r in reports if any(k in r['fields'] for k in ('transport','transport_difficulty_reported','transport_to_medical_visits_difficult_reported'))]
    helpers=[r for r in reports if any(k in r['fields'] for k in ('permission_to_contact_helper','patient_agrees_to_discussion','help_available_reported','reported_assistance'))]
    schedule=[r for r in reports if 'work_or_school_scheduling_constraint' in r['fields']]
    responses=[r for r in reports if 'response' in r['fields']]
    if option['capability']['modality'] in ('in_person','hybrid'):add('travel_access','Review dated travel reports and confirm transportation for this service.',transport)
    if option['capability']['modality'] in ('virtual','hybrid'):add('digital_access','Confirm a usable device, connection, privacy and any assistance needed for this virtual service.',[])
    if option['capability']['modality']=='home':add('home_access','Confirm permission, access and practical arrangements for a home visit.',helpers)
    if helpers:add('helper_scope','Review the exact helper/contact account; reconfirm present permission and scope without inferring legal authority.',helpers)
    if schedule:add('scheduling','Confirm whether reported work or school scheduling constraints still apply.',schedule)
    if responses:add('prior_service_response','Review responses only for their named referral/order and date; ask about this service separately.',responses)
    return checks

def association(path,plan):
    receipt=load(path,'matches',plan['id'])
    if receipt is None:
        if MARKER in plan['coverage']['preferences']:raise care.work.WorkConflict('Bound patient context missing; prepare a new match')
        return None
    if receipt['id']!=digest({k:v for k,v in receipt.items() if k!='id'}) or receipt['code_identity']!=identity() or receipt['plan_hash']!=digest(plan):
        raise care.work.WorkConflict('Patient context match changed; prepare again')
    current=inspect(path,receipt['context']['id'])
    if current!=receipt['context'] or current['patient_id']!=plan['patient_id'] or current['as_of']!=plan['coverage']['as_of']:
        raise care.work.WorkConflict('Patient context identity or date changed')
    return receipt

def prepare_match(path,request):
    if MARKER in (request.get('coverage') or {}).get('preferences',''):raise ValueError('Received patient context marker is reserved for verified source binding')
    if 'patient_context_id' not in request:return participation_work.prepare(path,request)
    care.exact(request,'buyer_id origin need_tags rationale coverage patient_context_id'+(' coverage_binding' if 'coverage_binding' in request else ''))
    context=inspect(path,request['patient_context_id']);origin=care.origin(path,request['origin'])
    if context['patient_id']!=origin['patient_id'] or context['as_of']!=request['coverage']['as_of']:raise ValueError('Patient context must match this patient and exact decision date')
    base={k:v for k,v in request.items() if k!='patient_context_id'}
    base['coverage']={**base['coverage'],'preferences':base['coverage']['preferences']+'\n'+MARKER+context['id']+'. Dated reports only; current goals, preferences and consent require confirmation.'}
    plan=coverage_work.prepare(path,base);core={k:v for k,v in plan.items() if k!='received_coverage'}
    receipt={'plan_id':core['id'],'plan_hash':digest(core),'request':request,'context':context,'code_identity':identity(),
             'option_review_checks':{o['id']:review_checks(context,o) for o in core['options']},'clinical_execution_authorized':False,'source_binding_is_patient_confirmation':False}
    receipt['id']=digest(receipt);db=database(path)
    try:
        with db:db.execute('INSERT OR IGNORE INTO matches VALUES (?,?)',(core['id'],json.dumps(receipt,allow_nan=False)))
    finally:db.close()
    association(path,core)
    return {**participation_work.prepare(path,base),'patient_context':receipt}

def task_view(path,key):
    task=care_readiness.task(path,key);db=care.db_open(path)
    try:plan=care.load(db,'capability_plans',task['plan_id'])
    finally:db.close()
    saved=load(path,'matches',plan['id'])
    try:care.inspect_plan(path,plan['id']);receipt=association(path,plan);current=True;reason=None
    except (ValueError,KeyError,FileNotFoundError) as exc:receipt=saved;current=False;reason=str(exc)
    db=database(path)
    try:row=db.execute('SELECT payload FROM handoffs WHERE task=? ORDER BY revision DESC LIMIT 1',(key,)).fetchone()
    finally:db.close()
    attached=json.loads(row[0]) if row else None
    return {'task_id':key,'patient_id':task['patient_id'],'patient_context':receipt,'dependencies_current':current,'stale_reason':reason,
            'handoff_context':attached,'handoff_context_current':bool(receipt and current and attached and attached['context_receipt_id']==receipt['id'] and attached['task_revision']==task['revision']),
            'clinical_execution_authorized':False,'current_patient_consent_established':False}

def task_event(path,request):
    task=care_readiness.task(path,request.get('task_id'));plan=care.inspect_plan(path,task['plan_id']);receipt=association(path,plan)
    result=participation_work.task_event(path,request)
    if receipt is None or request.get('action')!='prepare_handoff':return result
    if association(path,care.inspect_plan(path,result['task']['plan_id']))!=receipt:raise care.work.WorkConflict('Patient context changed during handoff preparation')
    attachment={'task_id':task['id'],'task_revision':result['task']['revision'],'context_receipt_id':receipt['id'],
                'context':receipt['context'],'review_checks':receipt['option_review_checks'][task['option_id']], 'clinical_execution_authorized':False,'current_patient_consent_established':False}
    db=database(path)
    try:
        with db:
            old=db.execute('SELECT payload FROM handoffs WHERE task=? AND revision=?',(task['id'],attachment['task_revision'])).fetchone()
            if old and json.loads(old[0])!=attachment:raise care.work.WorkConflict('Patient handoff context differs from saved revision')
            db.execute('INSERT OR IGNORE INTO handoffs VALUES (?,?,?)',(task['id'],attachment['task_revision'],json.dumps(attachment)))
    finally:db.close()
    return {**result,'patient_context_attachment':attachment}

def refresh_match(path,request):
    from . import action_work_state
    care.exact(request,'task_id expected_revision actor')
    if type(request['expected_revision']) is not int:raise ValueError('Exact task revision required')
    task=care_readiness.task(path,request['task_id']);old=action_work_state.stored_plan(path,task['plan_id']);receipt=association(path,old)
    if receipt is None:return action_work_state.refresh(path,request)
    actor=care.text(request['actor'],'refresh actor')
    if task['revision']!=request['expected_revision'] or task['state']=='cancelled':raise care.work.WorkConflict('Task changed or cancelled; reload')
    plan=prepare_match(path,receipt['request'])
    option=next((o for o in plan['options'] if o['capability']['id']==task['capability_id']),None)
    if not option or option['status']=='blocked':raise care.work.WorkConflict('This service no longer has a potentially applicable route')
    current=care_readiness.task(path,task['id'])
    if current['revision']!=task['revision'] or current['state']=='cancelled':raise care.work.WorkConflict('Task changed during matching refresh')
    result=participation_work.enqueue(path,{'plan_id':plan['id'],'option_id':option['id'],'actor':actor,'participation_context_id':plan['participation_contexts'][option['id']]['id']})
    if result['task']['id']!=task['id']:raise care.work.WorkConflict('Matching refresh changed the internal task identity')
    return {**result,'work_state':action_work_state.inspect(path,task['id']),'paid_calls':0,'external_actions_executed':0}


def install_plan_guard():
    if getattr(care.inspect_plan,'patient_context_guard',False):return
    original=care.inspect_plan
    def guarded(path,key):
        plan=original(path,key);association(path,plan);return plan
    guarded.patient_context_guard=True;care.inspect_plan=guarded
    # The existing packet worker binds the combined work-state hash. Only new
    # source-bound plans gain this layer; historical packet identities stay exact.
    from . import action_work_state
    original_state=action_work_state.inspect
    def guarded_state(path,key):
        state=original_state(path,key);info=task_view(path,key)
        if info['patient_context'] is None:return state
        current=info['handoff_context_current']
        state['layers']['patient_context']={'state':'received_context_current' if current else 'patient_context_review_required',
            'reason':info['stale_reason'] or 'Dated patient/support reports require current goals and consent confirmation.',
            'packet_current':current,'context_receipt_id':info['patient_context']['id'],'current_consent_established':False}
        state['packet_matches_recorded_dependencies']=state['packet_matches_recorded_dependencies'] and current
        if not info['dependencies_current']:
            state['local_matching_refresh_available']=False;state['refresh_unavailable_reason']=info['stale_reason']
        if not current:state['next_steps'].insert(0,'Review current patient/support sources and prepare their exact internal packet attachment.')
        return state
    action_work_state.inspect=guarded_state
