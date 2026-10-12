"""Dated reference assertions become explicit internal applicability questions.

Reference statements are not patient findings. All derived questions remain
unresolved; existing source/context/packet work can prepare evidence for review.
"""
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import care_capabilities as care,care_readiness,evidence_dispositions as D,packet_followup,record_context

VERSION='reference-applicability-work/1'
KIND='reference_applicability_question'
CATALOG=Path(__file__).with_name('reference_assertions.json')
LIMITS={'clinical_applicability_established':False,'clinical_execution_authorized':False,'care_delivered':False,
        'human_review_recorded':False,'new_paid_api_calls':0,'requests_sent':0}

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def references():
    rows=json.loads(CATALOG.read_text())
    for r in rows:
        if r['kind']!='guideline_recommendation' or r['review_status']!='machine_curated_unreviewed':raise ValueError('Unreviewed typed guideline reference required')
        if len({q['key'] for q in r['questions']})!=len(r['questions']):raise ValueError('Distinct applicability questions required')
        r['id']=digest(r)
    return rows

def reference(key):
    row=next((r for r in references() if r['id']==key),None)
    if row is None:raise ValueError('Reference definition changed or is unavailable; retain history and select a current version')
    return row

def db_open(path):
    db=sqlite3.connect(Path(path)/'reference-applicability.sqlite3',timeout=30)
    db.executescript('CREATE TABLE IF NOT EXISTS manifests(id TEXT PRIMARY KEY,task TEXT,payload TEXT); CREATE TABLE IF NOT EXISTS acquisitions(id TEXT PRIMARY KEY,target TEXT,payload TEXT);')
    for table in ('manifests','acquisitions'):
        for op in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Reference applicability history immutable'); END")
    return db

def verified(row):
    if row.get('id')!=digest({k:v for k,v in row.items() if k!='id'}):raise ValueError('Reference applicability integrity changed')
    return row

def origin_targets(path,task_id):
    # Only the task's original service requirements are anchors. This avoids
    # recursive derived questions and does not require a completed packet.
    task=care_readiness.task(path,task_id);plan=care.inspect_plan(path,task['plan_id'])
    option=next(o for o in plan['options'] if o['id']==task['option_id'])
    return [r for route in option['routes'] for r in care_readiness.requirements(plan,option,route)]

def snapshot(path,request):
    care.exact(request,'task_id expected_revision target reference_id reason')
    care.text(request['reason'],'reference selection reason',2000)
    if type(request['expected_revision']) is not int:raise ValueError('Exact task revision required')
    task=care_readiness.task(path,request['task_id'])
    if task['state']=='cancelled':raise ValueError('Task is cancelled')
    plan=care.inspect_plan(path,task['plan_id'])
    target=next((t for t in origin_targets(path,task['id']) if t['target']==request['target']),None)
    if target is None:raise ValueError('Select an original service requirement belonging to this task')
    ref=reference(request['reference_id'])
    if ref['published_on']>target['as_of']:raise ValueError('Reference was published after the original question cutoff')
    row={'schema':VERSION,'request':request,'task_id':task['id'],'patient_id':task['patient_id'],'owner':task['owner'],
         'plan_hash':digest(plan),'option_id':task['option_id'],'original_target':target,'reference':ref,
         'implementation':identity(),'questions':[],
         'reference_currency':{'checked_on':ref['checked_on'],'patient_decision_date':target['as_of'],
             'current_at_patient_decision':False,'reason':'Dated public inspection is not a systematic update or correction/retraction check at the patient decision date.'},**LIMITS}
    for q in ref['questions']:
        question={**q,'question':q['question']+' Reference: '+ref['citation']['doi']+'. Original service question: '+target['question']}
        question['id']=digest([task['id'],target,ref['id'],q]);row['questions'].append(question)
    row['id']=digest(row)
    return task,row

def materialize(path,request):
    task,row=snapshot(path,request)
    if task['revision']!=request['expected_revision']:raise care.work.WorkConflict('Task revision changed; reload')
    db=db_open(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if snapshot(path,request)!=(task,row):raise care.work.WorkConflict('Reference or task changed during preparation')
            db.execute('INSERT OR IGNORE INTO manifests VALUES (?,?,?)',(row['id'],task['id'],json.dumps(row,allow_nan=False)))
            return verified(json.loads(db.execute('SELECT payload FROM manifests WHERE id=?',(row['id'],)).fetchone()[0]))
    finally:db.close()

def load(path,key):
    care.text(key,'manifest ID',64);db=db_open(path)
    try:r=db.execute('SELECT payload FROM manifests WHERE id=?',(key,)).fetchone()
    finally:db.close()
    if r is None:raise ValueError('Unknown reference applicability question set')
    return verified(json.loads(r[0]))

def current(path,row):
    # Task revision is a save-time concurrency check; subsequent packet activity
    # alone does not change the question. Patient/plan/owner/reference changes do.
    if snapshot(path,row['request'])[1]!=row:raise care.work.WorkConflict('Reference, question or patient/service dependencies changed')

def target_for(row,q):return {'kind':KIND,'manifest_id':row['id'],'question_id':q['id']}

def resolved(row,q):
    target=target_for(row,q)
    return {'target':target,'id':digest(target),'parent_hash':digest(row),'patient_id':row['patient_id'],
            'task_id':row['task_id'],'question':q['question'],'source_selectors':row['original_target']['source_selectors'],
            'as_of':row['original_target']['as_of'],'reference_id':row['reference']['id'],'reference_role':q['basis'],
            'authority':'Unreviewed reference applicability question, not a patient fact or clinical recommendation',
            'documentary_resolution_supported':False}

def resolve(path,target):
    care.exact(target,'kind manifest_id question_id')
    if target['kind']!=KIND:raise ValueError('Exact reference applicability target required')
    row=load(path,target['manifest_id']);current(path,row)
    q=next((q for q in row['questions'] if q['id']==target['question_id']),None)
    if q is None:raise ValueError('Question does not belong to this reference set')
    return resolved(row,q)

def history(path,task_id):
    db=db_open(path)
    try:return [verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM manifests WHERE task=? ORDER BY rowid',(task_id,))]
    finally:db.close()

def acquisitions(path,target):
    db=db_open(path)
    try:return [verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM acquisitions WHERE target=? ORDER BY rowid',(digest(target),))]
    finally:db.close()

def inspect(path,task_id):
    care_readiness.task(path,task_id);sets=[]
    for row in history(path,task_id):
        try:current(path,row);reason=None
        except (ValueError,KeyError,FileNotFoundError) as exc:reason=str(exc)
        qs=[]
        for q in row['questions']:
            target=target_for(row,q);saved=[]
            for a in acquisitions(path,target):
                reasons=[reason] if reason else []
                try:
                    if digest(record_context.verified(path,a['acquisition']['context_id']))!=a['context_hash']:reasons.append('Selected source context changed')
                except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
                saved.append({**a,'current':not reasons,'stale_reasons':reasons})
            qs.append({**q,'target':target,'acquisitions':saved,'applicability':'unresolved'})
        sets.append({'manifest':row,'current':reason is None,'stale_reason':reason,'questions':qs})
    try:available=origin_targets(path,task_id);unavailable=None
    except (ValueError,KeyError,FileNotFoundError) as exc:available=[];unavailable=str(exc)
    return {'sets':sets,'references':references(),'original_targets':available,'unavailable_reason':unavailable,**LIMITS}

def install():
    if getattr(D.resolve_target,'reference_applicability_adapter',False):return
    prior_resolve=D.resolve_target;prior_save=D.save;prior_acquire=D.acquisition_context;prior_targets=packet_followup.targets
    def resolver(path,target):return resolve(path,target) if isinstance(target,dict) and target.get('kind')==KIND else prior_resolve(path,target)
    resolver.reference_applicability_adapter=True
    def save(path,request):
        if isinstance(request.get('target'),dict) and request['target'].get('kind')==KIND and request.get('disposition')=='addressed':
            raise ValueError('Clinical reference applicability cannot be established by documentary decisions')
        return prior_save(path,request)
    def acquire(path,request):
        if not isinstance(request.get('target'),dict) or request['target'].get('kind')!=KIND:return prior_acquire(path,request)
        target=resolve(path,request['target']);result=prior_acquire(path,request);context=record_context.verified(path,result['context_id'])
        if context['patient_id']!=target['patient_id'] or context['parameters']['question']!=target['question']:raise ValueError('Acquired reference question/patient changed')
        row={'schema':VERSION,'target':request['target'],'acquisition':result,'context_hash':digest(context),'implementation':identity(),**LIMITS};row['id']=digest(row)
        db=db_open(path)
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if resolve(path,request['target'])!=target or digest(record_context.verified(path,result['context_id']))!=row['context_hash']:raise care.work.WorkConflict('Reference question or sources changed during acquisition')
                db.execute('INSERT OR IGNORE INTO acquisitions VALUES (?,?,?)',(row['id'],digest(request['target']),json.dumps(row,allow_nan=False)))
        finally:db.close()
        return result
    def targets(path,task_id):
        rows=prior_targets(path,task_id)
        for m in history(path,task_id):
            try:current(path,m)
            except (ValueError,KeyError,FileNotFoundError):continue
            rows.extend({'target':target_for(m,q),'question':q['question'],'state':'applicability_unresolved','route':'Reference applicability: '+m['reference']['title']} for q in m['questions'])
        return rows
    D.resolve_target=resolver;D.save=save;D.acquisition_context=acquire;packet_followup.targets=targets
