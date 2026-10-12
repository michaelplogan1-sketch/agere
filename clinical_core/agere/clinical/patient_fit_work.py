"""Exact patient-fit questions and received-source work; never current consent.

Additive target adapters keep the original packet/review/answer engines intact.
Local source preparation is tracked separately from authenticated packet review.
"""
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import patient_context as patient,care_capabilities as care,care_readiness
from . import evidence_dispositions as dispositions,record_context,packet_followup

VERSION='patient-fit-work/1'
KIND='patient_fit_question'
LIMITS={'requirement_resolved':False,'current_patient_goals_confirmed':False,
        'current_patient_consent_established':False,'clinical_execution_authorized':False,
        'care_delivered':False,'requests_sent':0,'new_paid_api_calls':0}

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def database(path):
    db=sqlite3.connect(Path(path)/'patient-fit-work.sqlite3',timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS manifests(id TEXT PRIMARY KEY,task TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS manifests_task ON manifests(task);
      CREATE TABLE IF NOT EXISTS acquisitions(id TEXT PRIMARY KEY,target TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS acquisitions_target ON acquisitions(target);''')
    for table in ('manifests','acquisitions'):
        for op in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Patient-fit work history is immutable'); END")
    return db

def verified(row):
    if row.get('id')!=digest({k:v for k,v in row.items() if k!='id'}):raise ValueError('Patient-fit work receipt integrity changed')
    return row

def snapshot(path,task_id):
    task=care_readiness.task(path,task_id)
    if task['state']=='cancelled':raise care.work.WorkConflict('Patient-fit task is cancelled')
    plan=care.inspect_plan(path,task['plan_id']);receipt=patient.association(path,plan)
    if receipt is None:raise ValueError('This task has no bound patient/support context')
    option=next((o for o in plan['options'] if o['id']==task['option_id']),None)
    if option is None:raise care.work.WorkConflict('Patient-fit service option changed')
    result={'schema':VERSION,'task_id':task_id,'plan_id':plan['id'],'option_id':option['id'],
        'plan_hash':digest(plan),'patient_context_receipt_id':receipt['id'],
        'patient_id':task['patient_id'],'as_of':receipt['context']['as_of'],
        'owner':task['owner'],'service':option['capability'],'implementation':identity(),
        'questions':[],**LIMITS}
    reports={r['record_id']:r for r in receipt['context']['reports']}
    for check in receipt['option_review_checks'][option['id']]:
        question={**check,'source_reports':[reports[key] for key in check['record_ids']],
            'authority':'current_confirmation_required; dated accounts are evidence for review only'}
        question['id']=digest([task_id,plan['id'],option['id'],receipt['id'],question])
        result['questions'].append(question)
    result['id']=digest(result)
    return task,result

def materialize(path,request):
    care.exact(request,'task_id expected_revision')
    if type(request['expected_revision']) is not int:raise ValueError('Exact task revision required')
    task,result=snapshot(path,request['task_id'])
    if task['revision']!=request['expected_revision']:raise care.work.WorkConflict('Task changed; reload before tracking questions')
    db=database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if snapshot(path,task['id'])!=(task,result):raise care.work.WorkConflict('Patient-fit dependencies changed during preparation')
            db.execute('INSERT OR IGNORE INTO manifests VALUES (?,?,?)',(result['id'],task['id'],json.dumps(result,allow_nan=False)))
            saved=verified(json.loads(db.execute('SELECT payload FROM manifests WHERE id=?',(result['id'],)).fetchone()[0]))
            if saved!=result:raise ValueError('Patient-fit manifest differs from original receipt')
            return saved
    finally:db.close()

def load(path,key):
    care.text(key,'patient-fit manifest ID',64);db=database(path)
    try:row=db.execute('SELECT payload FROM manifests WHERE id=?',(key,)).fetchone()
    finally:db.close()
    if not row:raise ValueError('Unknown patient-fit manifest')
    return verified(json.loads(row[0]))

def target_for(manifest,question):return {'kind':KIND,'manifest_id':manifest['id'],'question_id':question['id']}

def resolve(path,target):
    care.exact(target,'kind manifest_id question_id')
    if target['kind']!=KIND:raise ValueError('Exact patient-fit target required')
    manifest=load(path,target['manifest_id'])
    if snapshot(path,manifest['task_id'])[1]!=manifest:raise care.work.WorkConflict('Patient-fit question dependencies changed; track the current service questions')
    question=next((q for q in manifest['questions'] if q['id']==target['question_id']),None)
    if question is None:raise ValueError('Question does not belong to this patient-fit manifest')
    return resolved(manifest,question)

def resolved(manifest,question):
    target=target_for(manifest,question)
    return {'target':target,'id':digest(target),'parent_hash':digest(manifest),'patient_id':manifest['patient_id'],
        'task_id':manifest['task_id'],'question':question['review_question'],'as_of':manifest['as_of'],
        'source_selectors':[{'record_id':key} for key in question['record_ids']],
        'question_kind':question['kind'],'service_id':manifest['service']['id'],
        'authority':'Patient/service fit confirmation question; historical sources cannot establish current goals or consent',
        'documentary_resolution_supported':False}

def scope_ids(target,ids):
    ids=care.strings(ids,'record IDs',12,empty=False)
    allowed={s['record_id'] for s in target['source_selectors']}
    if not allowed:raise ValueError('No confirming source is bound to this question; direct current confirmation remains required')
    if not set(ids)<=allowed:raise ValueError('Select exact dated records bound to this patient-fit question')
    return ids

def acquisitions(path,target):
    db=database(path)
    try:return [verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM acquisitions WHERE target=? ORDER BY rowid',(digest(target),))]
    finally:db.close()

def inspect(path,task_id):
    care_readiness.task(path,task_id);db=database(path)
    try:manifests=[verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM manifests WHERE task=? ORDER BY rowid',(task_id,))]
    finally:db.close()
    try:current=snapshot(path,task_id)[1];reason=None
    except (ValueError,KeyError,FileNotFoundError) as exc:current=None;reason=str(exc)
    rows=[]
    for manifest in manifests:
        fresh=manifest==current;questions=[]
        for q in manifest['questions']:
            target=target_for(manifest,q);history=[]
            for a in acquisitions(path,target):
                reasons=[] if fresh else [reason or 'Patient-fit question belongs to an earlier service context']
                if a['implementation']!=identity():reasons.append('Patient-fit work contract changed')
                try:
                    context=record_context.inspect(path,a['acquisition']['context_id'])
                    if context['patient_id']!=manifest['patient_id'] or digest(context)!=a['context_hash']:reasons.append('Selected evidence context changed')
                except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
                history.append({**a,'current':not reasons,'stale_reasons':reasons})
            state='source_context_prepared' if any(a['current'] for a in history) else 'dated_sources_available' if q['record_ids'] else 'current_confirmation_needed'
            questions.append({**q,'target':target,'work_state':state if fresh else 'stale',
                'acquisitions':history,'current':fresh,'requirement_resolved':False})
        rows.append({'manifest':manifest,'current':fresh,'stale_reason':None if fresh else reason or 'Service context changed','questions':questions})
    return {'schema':VERSION,'task_id':task_id,'sets':rows,'current_manifest_id':current['id'] if current and any(m==current for m in manifests) else None,
        'prepare_available':current is not None,'unavailable_reason':reason,
        'scope':'Internal source-acquisition work assigned to the existing task owner. Preparation is not contact, confirmation, packet review or completed care.',**LIMITS}

def install():
    """Service bootstrap adapter; standalone callers must install explicitly."""
    if getattr(dispositions.resolve_target,'patient_fit_adapter',False):return
    original_resolve=dispositions.resolve_target;original_acquire=dispositions.acquisition_context
    original_save=dispositions.save;original_targets=packet_followup.targets
    def resolve_target(path,target):
        return resolve(path,target) if isinstance(target,dict) and target.get('kind')==KIND else original_resolve(path,target)
    resolve_target.patient_fit_adapter=True
    def acquire(path,request):
        if not isinstance(request.get('target'),dict) or request['target'].get('kind')!=KIND:return original_acquire(path,request)
        care.exact(request,'target record_ids');t=resolve(path,request['target']);scope_ids(t,request['record_ids'])
        result=original_acquire(path,request);context=record_context.verified(path,result['context_id'])
        if context['patient_id']!=t['patient_id'] or context['parameters']['question']!=t['question']:raise ValueError('Patient-fit acquisition question or patient changed')
        row={'schema':VERSION,'target':request['target'],'request':request,'acquisition':result,
            'context_hash':digest(context),'implementation':identity(),'actor_kind':'local_source_preparation',
            'human_review_recorded':False,**LIMITS};row['id']=digest(row)
        db=database(path)
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if resolve(path,request['target'])!=t or digest(record_context.verified(path,result['context_id']))!=row['context_hash']:
                    raise care.work.WorkConflict('Patient-fit sources changed during acquisition')
                db.execute('INSERT OR IGNORE INTO acquisitions VALUES (?,?,?)',(row['id'],t['id'],json.dumps(row,allow_nan=False)))
        finally:db.close()
        return result
    def save(path,request):
        if isinstance(request.get('target'),dict) and request['target'].get('kind')==KIND:
            t=resolve(path,request['target'])
            if request.get('disposition')=='addressed':raise ValueError('Current patient-fit confirmation cannot be established by this documentary workflow')
            for b in request.get('bindings',[]):scope_ids(t,[dispositions.binding(path,t,b)['record_id']])
        return original_save(path,request)
    def targets(path,task_id):
        rows=original_targets(path,task_id)
        try:manifest=snapshot(path,task_id)[1]
        except (ValueError,KeyError,FileNotFoundError):return rows
        db=database(path)
        try:exists=db.execute('SELECT 1 FROM manifests WHERE id=?',(manifest['id'],)).fetchone()
        finally:db.close()
        if exists:
            rows += [{'target':target_for(manifest,q),'question':q['review_question'],
                'state':'current_confirmation_required','route':'Patient fit: '+manifest['service']['name']} for q in manifest['questions']]
        return rows
    dispositions.resolve_target=resolve_target;dispositions.acquisition_context=acquire
    dispositions.save=save;packet_followup.targets=targets
