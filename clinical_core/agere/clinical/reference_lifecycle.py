"""Append-only local reference changes and explicit downstream reassessment.

Publisher notices are recorded reports, not independently verified retractions.
Saved patient answers remain intact; only their scoped reference-work use changes.
"""
from contextvars import ContextVar
from datetime import date,datetime,timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
import copy,hashlib,json,re,sqlite3
from ...contracts import digest
from . import reference_applicability as R

VERSION='reference-lifecycle/1'
KINDS={'local_mapping_revision','local_withdrawal','publisher_correction_report','publisher_retraction_report'}
_PATH=ContextVar('reference_store_path',default=None)
_BASE_REFERENCES=R.references
LIMITS={'clinical_validation':False,'clinical_execution_authorized':False,'publisher_event_independently_verified':False,
        'reviewer_identity_verified':False,'patient_answers_deleted':False,'external_actions':0,'provider_calls':0}

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])

def database(path):
    db=sqlite3.connect(Path(path)/'reference-lifecycle.sqlite3',timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS definitions(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS events(reference TEXT,revision INTEGER,payload TEXT NOT NULL,PRIMARY KEY(reference,revision));
      CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS successors(id TEXT PRIMARY KEY,reference TEXT NOT NULL,payload TEXT NOT NULL);''')
    for table in ('definitions','events','requests','successors'):
        for op in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Reference lifecycle history is immutable'); END")
    return db

def payloads(path,filename,table,where='',args=()):
    file=Path(path)/filename
    if not file.exists():return []
    db=sqlite3.connect(file.resolve().as_uri()+'?mode=ro',uri=True,timeout=30)
    try:
        if not db.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?',(table,)).fetchone():return []
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM '+table+where,args)]
    finally:db.close()

def archived(path,key):
    row=next((r for r in _BASE_REFERENCES() if r['id']==key),None)
    if row is None:
        rows=payloads(path,'reference-lifecycle.sqlite3','definitions',' WHERE id=?',(key,));row=rows[0] if rows else None
    if row is None:raise ValueError('Unknown exact reference identity')
    return R.verified(row)

def events(path,key):
    rows=payloads(path,'reference-lifecycle.sqlite3','events',' WHERE reference=? ORDER BY revision',(key,))
    for i,row in enumerate(rows,1):
        R.verified(row)
        if row['revision']!=i or row['reference_id']!=key or row['previous_event_id']!=(rows[i-2]['id'] if i>1 else None):raise ValueError('Reference event chain changed')
        if row['implementation']!=identity():raise ValueError('Reference lifecycle interpretation changed; explicit migration required')
    return rows

def state(path,key):
    archived(path,key);rows=events(path,key);last=rows[-1] if rows else None
    return {'reference_id':key,'revision':len(rows),'status':last['status'] if last else 'available_unreviewed',
            'last_event_id':last['id'] if last else None,'replacement_id':last['replacement_id'] if last else None,'events':rows,**LIMITS}

def available(path):
    rows=_BASE_REFERENCES()+payloads(path,'reference-lifecycle.sqlite3','definitions')
    return [R.verified(r) for r in rows if state(path,r['id'])['status']=='available_unreviewed']

def impact(path,key):
    archived(path,key);manifests=payloads(path,'reference-applicability.sqlite3','manifests');affected=[]
    for m in manifests:
        R.verified(m)
        if m['reference']['id']!=key:continue
        questions=[]
        for q in m['questions']:
            t=R.target_for(m,q);tid=digest(t)
            acquisitions=R.acquisitions(path,t)
            selected=payloads(path,'reference-scope-work.sqlite3','selections',' WHERE target=?',(tid,))
            answers=payloads(path,'reference-scope-work.sqlite3','answers',' WHERE target=?',(tid,))
            decisions=payloads(path,'evidence-dispositions.sqlite3','decisions',' WHERE target=?',(tid,))
            normal=[x for x in payloads(path,'internal-execution.sqlite3','followups',' WHERE task=?',(m['task_id'],)) if x.get('request',{}).get('target')==t]
            expanded=[x for x in payloads(path,'internal-execution.sqlite3','reference_followups',' WHERE task=?',(m['task_id'],)) if x.get('target',{}).get('target')==t]
            normal_ids={x['id'] for x in normal};expanded_ids={x['id'] for x in expanded}
            normal_answers=[x for x in payloads(path,'followup-answers.sqlite3','answers',' WHERE task=?',(m['task_id'],)) if x.get('followup_id') in normal_ids]
            expanded_answers=[x for x in payloads(path,'internal-execution.sqlite3','reference_followup_answers',' WHERE task=?',(m['task_id'],)) if x.get('request',{}).get('followup_id') in expanded_ids]
            contexts=sorted({x['acquisition']['context_id'] for x in acquisitions+normal}|{x['context_id'] for x in selected+expanded})
            answer_ids=sorted({x['request']['answer_id'] for x in answers+expanded_answers}|{x['answer_id'] for x in normal_answers})
            dependencies={'acquisitions':acquisitions,'selections':selected,'answers':answers,'decisions':decisions,'packet_followups':normal+expanded,'packet_answers':normal_answers+expanded_answers}
            questions.append({'target':t,'question':q['question'],'context_ids':contexts,'answer_ids':answer_ids,'dependency_hash':digest(dependencies),
              'acquisition_ids':[x['id'] for x in acquisitions],'selection_ids':[x['id'] for x in selected],
              'answer_attachment_ids':[x['id'] for x in answers+normal_answers+expanded_answers],
              'documentary_revisions':[x['revision'] for x in decisions],'packet_followup_ids':[x['id'] for x in normal+expanded]})
        affected.append({'manifest_id':m['id'],'task_id':m['task_id'],'patient_id':m['patient_id'],'owner':m['owner'],
                         'original_question':m['original_target']['question'],'questions':questions})
    affected.sort(key=lambda m:m['manifest_id']);all_questions=[q for m in affected for q in m['questions']]
    result={'reference_id':key,'manifests':affected,'counts':{'question_sets':len(affected),'tasks':len({m['task_id'] for m in affected}),
            'questions':len(all_questions),'source_contexts':len({c for q in all_questions for c in q['context_ids']}),
            'saved_answers':len({a for q in all_questions for a in q['answer_ids']}),
            'packet_followups':len({a for q in all_questions for a in q['packet_followup_ids']})},
            'scope':'These scoped questions and attachments require reassessment. Original source records, patient answers, tasks and packets are retained; unrelated uses are not globally withdrawn.'}
    result['id']=digest(result);return result

def change_definition(path,change):
    R.care.exact(change,'reference_id expected_revision event_kind event_date actor actor_kind reason notice question_changes')
    ref=archived(path,change['reference_id']);s=state(path,ref['id'])
    if type(change['expected_revision']) is not int or change['expected_revision']!=s['revision']:raise R.care.work.WorkConflict('Reference revision changed; preview again')
    if s['status']!='available_unreviewed':raise ValueError('Reference is already superseded or withheld; select its available replacement')
    if change['event_kind'] not in KINDS or change['actor_kind'] not in {'machine','operator','synthetic_test'}:raise ValueError('Explicit reference change and actor kind required')
    for field in ('actor','reason'):R.care.text(change[field],field,2000 if field=='reason' else 120)
    if not isinstance(change['event_date'],str):raise ValueError('ISO event date required')
    day=date.fromisoformat(change['event_date'])
    if day.isoformat()!=change['event_date']:raise ValueError('Canonical ISO event date required')
    if day>datetime.now(timezone.utc).date() or change['event_date']<ref['published_on']:raise ValueError('Reference change date must be between publication and today')
    publisher=change['event_kind'].startswith('publisher_')
    if publisher:
        n=change['notice'];R.care.exact(n,'publisher url published_on locator summary')
        for k in n:R.care.text(n[k],k,1500)
        u=urlparse(n['url'])
        if u.scheme!='https' or not u.hostname or u.username or u.password:raise ValueError('Public HTTPS notice citation required; no credentials')
        if date.fromisoformat(n['published_on']).isoformat()!=n['published_on']:raise ValueError('Canonical notice date required')
        if not ref['published_on']<=n['published_on']<=change['event_date']:raise ValueError('Notice publication must precede its recorded event date')
    elif change['notice'] is not None:raise ValueError('Local mapping changes must not claim a publisher notice')
    edits=change['question_changes']
    if not isinstance(edits,dict):raise ValueError('Question-key to proposed wording map required')
    replacement=None
    if change['event_kind']=='local_mapping_revision':
        known={q['key']:q for q in ref['questions']}
        if not edits or set(edits)-set(known):raise ValueError('Change existing explicit applicability questions only')
        for k,v in edits.items():
            R.care.text(v,'revised question',1000)
            if v==known[k]['question']:raise ValueError('A revision must change the selected wording')
        replacement=copy.deepcopy(ref);replacement.pop('id');replacement['version']=ref['version']+'.local-'+digest(edits)[:12]
        for q in replacement['questions']:
            if q['key'] in edits:q['question']=edits[q['key']]
        replacement['id']=digest(replacement)
        status='superseded_local_mapping'
    else:
        if edits:raise ValueError('Only local mapping revisions may change question wording')
        status='withdrawn_locally' if not publisher else 'withheld_for_publisher_report'
    return ref,s,replacement,status

def preview(path,change):
    ref,s,replacement,status=change_definition(path,change)
    out={'schema':VERSION,'change':change,'reference_state':s,'reference':ref,'replacement':replacement,'resulting_status':status,
         'impact':impact(path,ref['id']),'implementation':identity(),**LIMITS};out['id']=digest(out);return out

def commit(path,request):
    R.care.exact(request,'change preview_id request_id');R.care.text(request['request_id'],'request ID',128)
    db=database(path);request_hash=digest(request)
    try:
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT request_hash,payload FROM requests WHERE id=?',(request['request_id'],)).fetchone()
        if prior:
            if prior[0]!=request_hash:raise R.care.work.WorkConflict('Request ID already has different reference-change content')
            return R.verified(json.loads(prior[1]))
        prepared=preview(path,request['change'])
        if prepared['id']!=request['preview_id']:raise R.care.work.WorkConflict('Reference state or affected work changed; preview again')
        replacement=prepared['replacement']
        if replacement:
            db.execute('INSERT OR IGNORE INTO definitions VALUES (?,?)',(replacement['id'],json.dumps(replacement,allow_nan=False)))
            if json.loads(db.execute('SELECT payload FROM definitions WHERE id=?',(replacement['id'],)).fetchone()[0])!=replacement:raise ValueError('Stored replacement differs from preview')
        row={'schema':VERSION,'reference_id':prepared['reference']['id'],'revision':prepared['reference_state']['revision']+1,
             'previous_event_id':prepared['reference_state']['last_event_id'],'request':request,'status':prepared['resulting_status'],
             'replacement_id':replacement['id'] if replacement else None,'impact_at_commit':prepared['impact'],
             'recorded_at':datetime.now(timezone.utc).isoformat(),'implementation':identity(),**LIMITS};row['id']=digest(row)
        # Recheck upstream dependencies immediately before append. Separate stores
        # can still change later: inspection dynamically recomputes all impacts.
        if impact(path,row['reference_id'])!=prepared['impact']:raise R.care.work.WorkConflict('Affected work changed during reference update')
        db.execute('INSERT INTO events VALUES (?,?,?)',(row['reference_id'],row['revision'],json.dumps(row,allow_nan=False)))
        db.execute('INSERT INTO requests VALUES (?,?,?)',(request['request_id'],request_hash,json.dumps(row,allow_nan=False)));db.commit();return row
    finally:db.close()

def inspect(path,key):
    links=payloads(path,'reference-lifecycle.sqlite3','successors',' WHERE reference=?',(key,))
    for link in links:R.verified(link)
    return {'reference':archived(path,key),'state':state(path,key),'impact':impact(path,key),'successor_links':links,'implementation':identity(),**LIMITS}

def successor(path,request):
    R.care.exact(request,'manifest_id event_id expected_task_revision reason')
    old=R.load(path,request['manifest_id']);s=state(path,old['reference']['id'])
    if s['last_event_id']!=request['event_id'] or not s['replacement_id']:raise ValueError('Exact replacement event required')
    # New source scope/answer attachments are never silently carried forward.
    fresh=R.materialize(path,{'task_id':old['task_id'],'expected_revision':request['expected_task_revision'],
           'target':old['request']['target'],'reference_id':s['replacement_id'],'reason':request['reason']})
    questions={q['key']:q for q in fresh['questions']}
    link={'schema':VERSION,'request':request,'old_manifest_id':old['id'],'new_manifest_id':fresh['id'],
          'task_id':old['task_id'],'reference_id':old['reference']['id'],'replacement_id':s['replacement_id'],
          'question_map':[{'key':q['key'],'old_target':R.target_for(old,q),'new_target':R.target_for(fresh,questions[q['key']]),
              'wording_changed':q['question']!=questions[q['key']]['question']} for q in old['questions']],
          'source_contexts_carried_forward':0,'answer_attachments_carried_forward':0,'implementation':identity(),**LIMITS}
    link['id']=digest(link);db=database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if state(path,old['reference']['id'])!=s:raise R.care.work.WorkConflict('Replacement event changed during successor preparation')
            R.current(path,fresh)
            db.execute('INSERT OR IGNORE INTO successors VALUES (?,?,?)',(link['id'],old['reference']['id'],json.dumps(link,allow_nan=False)))
    finally:db.close()
    return fresh

def install():
    if getattr(R.snapshot,'reference_lifecycle_adapter',False):return
    original_snapshot=R.snapshot;original_inspect=R.inspect
    def references():
        path=_PATH.get();return available(path) if path is not None else _BASE_REFERENCES()
    def scoped(fn):
        @wraps(fn)
        def call(path,*args,**kwargs):
            token=_PATH.set(Path(path).resolve())
            try:return fn(path,*args,**kwargs)
            finally:_PATH.reset(token)
        return call
    def checked_snapshot(path,request):
        if isinstance(request,dict) and 'reference_id' in request:
            status=state(path,request['reference_id'])
            if status['status']!='available_unreviewed':raise R.care.work.WorkConflict('Reference '+status['status']+'; affected applicability work requires reassessment')
        return original_snapshot(path,request)
    R.references=references;R.snapshot=scoped(checked_snapshot);R.snapshot.reference_lifecycle_adapter=True
    R.inspect=scoped(original_inspect)
