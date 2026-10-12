"""Explicitly recheck predecessor source choices for a successor question.

No source or answer is automatically carried into replacement work. Exact
context reuse is measured only after rechecking the question and source contract.
"""
from pathlib import Path
import fcntl,hashlib,json,sqlite3
from ...contracts import digest
from . import reference_applicability as R,reference_lifecycle as L

VERSION='reference-source-reassessment/1'
LIMITS={'clinical_applicability_established':False,'answers_carried_forward':0,'clinical_review_recorded':False,
        'provider_calls':0,'external_actions':0}

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def database(path):
    db=sqlite3.connect(Path(path)/'reference-reassessment.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS preparations(id TEXT PRIMARY KEY,target TEXT,payload TEXT)')
    for op in ('UPDATE','DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_preparations_{op} BEFORE {op} ON preparations BEGIN SELECT RAISE(ABORT,'Reference reassessment history immutable'); END")
    return db

def links(path,target):
    if not isinstance(target,dict) or target.get('kind')!=R.KIND:raise ValueError('Reference applicability question required')
    rows=L.payloads(path,'reference-lifecycle.sqlite3','successors');out=[]
    for row in rows:
        R.verified(row)
        if row['new_manifest_id']!=target.get('manifest_id'):continue
        if row['implementation']!=L.identity():raise ValueError('Successor contract changed')
        mapping=next((q for q in row['question_map'] if q['new_target']==target),None)
        if mapping:out.append((row,mapping))
    return out

def candidates(path,target):
    current=R.resolve(path,target);rows=[]
    for link,mapping in links(path,target):
        old=R.load(path,link['old_manifest_id']);new=R.load(path,link['new_manifest_id'])
        if old['patient_id']!=current['patient_id'] or new['reference']['id']!=link['replacement_id']:raise ValueError('Successor patient or reference lineage changed')
        for acquisition in R.acquisitions(path,mapping['old_target']):
            problem=None;context=None
            try:
                if acquisition['implementation']!=R.identity():raise ValueError('Predecessor acquisition contract changed')
                context=R.record_context.verified(path,acquisition['acquisition']['context_id'])
                if digest(context)!=acquisition['context_hash']:raise ValueError('Predecessor context changed')
                old_question=next(q for q in old['questions'] if q['id']==mapping['old_target']['question_id'])
                if context['patient_id']!=current['patient_id'] or context['parameters']['question']!=old_question['question']:raise ValueError('Predecessor evidence patient or question changed')
            except (ValueError,KeyError,FileNotFoundError) as exc:problem=str(exc)
            row={'prior_acquisition_id':acquisition['id'],'prior_target':mapping['old_target'],'new_target':target,
                 'successor_link_id':link['id'],'successor_link_hash':digest(link),'prior_acquisition_hash':digest(acquisition),
                 'prior_context_id':acquisition['acquisition']['context_id'],'prior_context_hash':acquisition['context_hash'],
                 'prior_question':acquisition['acquisition']['target']['question'],'new_question':current['question'],
                 'wording_changed':mapping['wording_changed'],'record_ids':acquisition['acquisition']['selection']['retrieved_record_ids'],
                 'prior_connected_record_ids':acquisition['acquisition']['record_scope']['record_ids'],
                 'available_for_explicit_recheck':problem is None,'unavailable_reason':problem}
            rows.append(row)
    return current,rows

def preview(path,request):
    R.care.exact(request,'target prior_acquisition_id');current,rows=candidates(path,request['target'])
    row=next((r for r in rows if r['prior_acquisition_id']==request['prior_acquisition_id']),None)
    if row is None:raise ValueError('Select a predecessor acquisition linked to this exact successor question')
    if not row['available_for_explicit_recheck']:raise ValueError(row['unavailable_reason'])
    p={'schema':VERSION,'request':request,'current_target':current,'candidate':row,'implementation':identity(),
       'meaning':'Explicit reuse of a prior source selection; changed wording needs a new context. Neither source presence nor exact context reuse resolves applicability.',**LIMITS}
    p['id']=digest(p);return p

def prepare(path,request):
    # Share the reference-scope writer lock: deterministic context files must
    # not be replaced concurrently by these explicit source-selection paths.
    with (Path(path)/'reference-scope-context.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _prepare(path,request)

def _prepare(path,request):
    R.care.exact(request,'target prior_acquisition_id preview_id');selection={k:request[k] for k in ('target','prior_acquisition_id')}
    p=preview(path,selection)
    if p['id']!=request['preview_id']:raise R.care.work.WorkConflict('Predecessor or successor evidence changed; preview again')
    result=R.D.acquisition_context(path,{'target':request['target'],'record_ids':p['candidate']['record_ids']})
    context=R.record_context.verified(path,result['context_id'])
    if context['patient_id']!=p['current_target']['patient_id'] or context['parameters']['question']!=p['current_target']['question']:raise ValueError('Prepared successor patient or question changed')
    row={'schema':VERSION,'request':request,'preview':p,'acquisition':result,'context_hash':digest(context),
         'exact_context_reused':result['context_id']==p['candidate']['prior_context_id'],
         'connected_scope_changed':sorted(result['record_scope']['record_ids'])!=sorted(p['candidate']['prior_connected_record_ids']),
         'implementation':identity(),**LIMITS};row['id']=digest(row);db=database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if preview(path,selection)!=p or digest(R.record_context.verified(path,result['context_id']))!=row['context_hash']:raise R.care.work.WorkConflict('Evidence changed during successor source recheck')
            db.execute('INSERT OR IGNORE INTO preparations VALUES (?,?,?)',(row['id'],digest(request['target']),json.dumps(row,allow_nan=False)))
            saved=R.verified(json.loads(db.execute('SELECT payload FROM preparations WHERE id=?',(row['id'],)).fetchone()[0]))
            if saved!=row:raise ValueError('Reassessment differs from original receipt')
    finally:db.close()
    return row

def listing(path,request):
    R.care.exact(request,'target')
    try:current,choices=candidates(path,request['target']);problem=None
    except (ValueError,KeyError,FileNotFoundError) as exc:current=None;choices=[];problem=str(exc)
    rows=L.payloads(path,'reference-reassessment.sqlite3','preparations',' WHERE target=?',(digest(request['target']),))
    history=[]
    for row in rows:
        R.verified(row);reasons=[]
        try:
            if row['implementation']!=identity() or preview(path,row['preview']['request'])!=row['preview']:raise ValueError('Reassessment contract or evidence changed')
            if digest(R.record_context.verified(path,row['acquisition']['context_id']))!=row['context_hash']:raise ValueError('Prepared successor context changed')
        except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
        history.append({**row,'current':not reasons,'stale_reasons':reasons})
    return {'target':request['target'],'candidates':choices,'preparations':history,'unavailable_reason':problem,**LIMITS}
