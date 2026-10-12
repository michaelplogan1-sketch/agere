"""Authenticated packet follow-through for an explicitly expanded evidence scope."""
from pathlib import Path
import hashlib,json
from ...contracts import digest
from . import reference_scope_work as scope,internal_execution as execution,packet_followup
VERSION='reference-scope-packets/1'
LIMITS={**scope.LIMITS,**execution.LIMITS,'care_delivered':False}
def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),scope.identity()])
def database(path):
 db=execution.database(path)
 for table in ('reference_followups','reference_followup_answers'):
  db.execute('CREATE TABLE IF NOT EXISTS '+table+'(id TEXT PRIMARY KEY,task TEXT NOT NULL,payload TEXT NOT NULL)')
  for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Expanded packet follow-up history is immutable'); END")
 return db

def dependencies(path,db,request):
 row=db.execute('SELECT payload FROM runs WHERE id=?',(request['run_id'],)).fetchone()
 if not row:raise ValueError('Unknown packet completion')
 run=json.loads(row[0]);task=execution.care_readiness.task(path,run['task_id']);binding=execution.binding(path,task['id'])[2]
 if binding!=run['binding']:raise execution.care.work.WorkConflict('Packet completion is stale')
 latest=db.execute('SELECT payload FROM reviews WHERE run=? ORDER BY revision DESC LIMIT 1',(run['id'],)).fetchone();review=json.loads(latest[0]) if latest else None
 if not review or review['revision']!=request['review_revision']:raise execution.care.work.WorkConflict('Exact current packet review required')
 selected=scope.inspect_selection(path,request['selection_id']);scope.record_context.verified(path,selected['context_id'])
 target=selected['target']['target']
 allowed=packet_followup.targets(path,task['id'])
 if not any(t['target']==target for t in allowed):raise ValueError('Selected evidence must belong to an unresolved question on this task')
 return task,selected,{'run_id':run['id'],'review_id':review['id'],'review_hash':digest(review),'binding_id':binding['id'],'selection_hash':digest(selected)}

def prepare(path,request,bearer):
 execution.care.exact(request,'run_id review_revision selection_id note request_id')
 for k in ('run_id','selection_id'):execution.care.text(request[k],k,64)
 execution.care.text(request['note'],'follow-up note',2000);execution.care.text(request['request_id'],'request ID',120)
 if type(request['review_revision']) is not int or request['review_revision']<1:raise ValueError('Exact packet review revision required')
 db=database(path)
 try:
  with db:
   db.execute('BEGIN IMMEDIATE');principal=execution.authenticate(db,bearer,'packet_reviewer')
   runrow=db.execute('SELECT task FROM runs WHERE id=?',(request['run_id'],)).fetchone()
   if not runrow:raise ValueError('Unknown packet completion')
   task=execution.care_readiness.task(path,runrow[0]);execution.authorize(principal,task)
   key=digest([VERSION,request,principal['id']]);prior=execution.replay(db,request['request_id'],key)
   if prior:return prior
   task,selected,binding=dependencies(path,db,request)
   result={'schema':VERSION,'task_id':task['id'],'request':request,'binding':binding,'implementation':identity(),
    'principal':principal,'recorded_at':execution.now(),'target':selected['target'],'selection_id':selected['id'],
    'context_id':selected['context_id'],'selected_record_ids':selected['selected_record_ids'],
    'unselected_record_ids':selected['unselected_record_ids'],'note':request['note'],
    'status':'expanded_evidence_linked_for_packet_review',**LIMITS}
   if dependencies(path,db,request)[2]!=binding:raise execution.care.work.WorkConflict('Expanded evidence changed during packet follow-up')
   result['id']=digest(result)
   db.execute('INSERT INTO reference_followups VALUES (?,?,?)',(result['id'],task['id'],json.dumps(result)))
   return execution.remember(db,request['request_id'],key,result)
 finally:db.close()

def inspect_one(path,key):
 db=database(path)
 try:
  raw=db.execute('SELECT payload FROM reference_followups WHERE id=?',(key,)).fetchone()
  if not raw:raise ValueError('Unknown expanded packet follow-up')
  row=scope.verified(json.loads(raw[0]))
  if row['implementation']!=identity() or dependencies(path,db,row['request'])[2]!=row['binding']:raise ValueError('Expanded packet follow-up dependencies changed')
  return row
 finally:db.close()

def answer_binding(path,request):
 execution.care.exact(request,'followup_id answer_id');row=inspect_one(path,request['followup_id'])
 selected,answer,deps=scope.answer_binding(path,{'selection_id':row['selection_id'],'answer_id':request['answer_id']})
 return row,answer,{'followup_hash':digest(row),'answer_binding':deps}
def attach(path,request):
 row,answer,binding=answer_binding(path,request)
 # Idempotent question attachment is recoverable if the packet changes before
 # the second store commits. It is not a packet acknowledgement on its own.
 question=scope.attach(path,{'selection_id':row['selection_id'],'answer_id':answer['id']})
 result={'schema':VERSION,'request':request,'task_id':row['task_id'],'target':row['target'],'implementation':identity(),
  'binding':binding,'question_answer_id':question['id'],'question_answer_hash':digest(question),
  'context_id':row['context_id'],'summary':question['summary'],'status':'machine_answer_returned_to_packet_review',**LIMITS}
 if answer_binding(path,request)[2]!=binding:raise ValueError('Expanded packet answer changed during attachment')
 result['id']=digest(result);db=database(path)
 try:
  with db:db.execute('INSERT OR IGNORE INTO reference_followup_answers VALUES (?,?,?)',(result['id'],row['task_id'],json.dumps(result)))
  saved=scope.verified(json.loads(db.execute('SELECT payload FROM reference_followup_answers WHERE id=?',(result['id'],)).fetchone()[0]))
  if saved!=result:raise ValueError('Expanded packet answer integrity changed')
 finally:db.close()
 return result

def inspect(path,task_id):
 execution.care_readiness.task(path,task_id);db=database(path)
 try:rows={kind:[scope.verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM '+table+' WHERE task=? ORDER BY rowid',(task_id,))] for kind,table in [('followups','reference_followups'),('answers','reference_followup_answers')]}
 finally:db.close()
 for kind,items in rows.items():
  for row in items:
   reasons=[]
   try:
    if kind=='followups':inspect_one(path,row['id'])
    else:
     if row['implementation']!=identity() or answer_binding(path,row['request'])[2]!=row['binding']:raise ValueError('Expanded packet answer dependencies changed')
     question=scope.load(path,'answers',row['question_answer_id'])
     if digest(question)!=row['question_answer_hash']:raise ValueError('Original question attachment changed')
     scope.evidence_needs.inspect(path,question['followup_needs_id'])
   except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
   row['current']=not reasons;row['stale_reasons']=reasons
 return {'schema':VERSION,'task_id':task_id,**rows,**LIMITS}
