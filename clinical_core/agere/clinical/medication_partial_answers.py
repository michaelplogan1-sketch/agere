"""Partial source-pass handoff when whole-component reconciliation cannot fit.

Kept separate from completed interpretation attachments. Never changes the
original handoff, task revision, documentary state or clinical authority.
"""
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import medication_interpretation as I, source_coverage_recovery as U
from . import citation_context_recovery as C, interpretation_recovery as recovery

VERSION='medication-partial-answers/1'
def identity():return digest([VERSION,I.identity(),U.identity(),C.identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])
def database(path):
 db=sqlite3.connect(Path(path)/'medication-partial-answers.sqlite3',timeout=30)
 db.execute('CREATE TABLE IF NOT EXISTS partials(id TEXT PRIMARY KEY,review TEXT,payload TEXT)')
 for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_partials_{op} BEFORE {op} ON partials BEGIN SELECT RAISE(ABORT,'Partial answer history is immutable'); END")
 return db
def binding(path,request):
 I.C.exact(request,'selection_id');s=I.inspect_selection(path,request['selection_id']);recovery.install()
 _,_,_,_,key=U.prepare(path,s['context_id']);a=U.inspect(path,key)
 if a['patient_id']!=s['patient_id'] or a['context_id']!=s['context_id']:raise ValueError('Partial answer patient or context mismatch')
 if len(a['passes'])<2:raise ValueError('Partial multi-pass evidence required')
 try:C.preview(path,a['id'])
 except ValueError as exc:
  if 'reconciliation request bound' not in str(exc):raise
  reason=str(exc)
 else:raise ValueError('Reconciliation is available; use the completed interpretation path')
 return s,a,{'selection_hash':digest(s),'answer_hash':digest(a),'implementation':identity(),'reconciliation_blocker':reason}
def attach(path,request):
 s,a,b= binding(path,request)
 row={'schema':VERSION,'request':request,'review_id':s['review_id'],'patient_id':s['patient_id'],
  'question_receipt_ids':s['request']['question_receipt_ids'],'answer_id':a['id'],'context_id':s['context_id'],'binding':b,
  'status':'partial_source_passes_for_review','reconciliation_complete':False,
  'summary':{'findings':len(a['findings']),'exact_citations':sum(len(f['citations']) for f in a['findings']),
   'submitted_units':a['coverage']['submitted_units'],'model_accounted_units':a['coverage']['model_accounted_units'],
   'system_unassessed_units':a['coverage'].get('system_unassessed_units',0),'quarantined_passes':len(a.get('quarantined_source_passes',[]))},
  'limits':['Partial source-pass findings only; relationships across passes have not been reconciled.',
   'Omitted or rejected model coverage is explicitly unassessed; rejected findings are not accepted.',
   'This attachment adds review evidence without resolving a question or revising the original pharmacy handoff.'],**I.LIMITS}
 if binding(path,request)[2]!=b:raise ValueError('Partial evidence changed during attachment')
 row['id']=digest(row);db=database(path)
 try:
  with db:db.execute('INSERT OR IGNORE INTO partials VALUES (?,?,?)',(row['id'],row['review_id'],json.dumps(row,allow_nan=False)))
 finally:db.close()
 return row
def inspect(path,key):
 db=database(path)
 try:r=db.execute('SELECT payload FROM partials WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if not r:raise ValueError('Unknown partial medication answer')
 row=I.verified(json.loads(r[0]))
 if binding(path,row['request'])[2]!=row['binding']:raise ValueError('Partial medication sources or dependencies changed')
 return row
def listing(path,request):
 I.C.exact(request,'review_id');db=database(path)
 try:rows=[I.verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM partials WHERE review=? ORDER BY rowid',(request['review_id'],))]
 finally:db.close()
 for row in rows:
  try:inspect(path,row['id']);row.update(current=True,stale_reasons=[])
  except (ValueError,KeyError,FileNotFoundError) as exc:row.update(current=False,stale_reasons=[str(exc)])
 return {'schema':VERSION,'partials':rows,**I.LIMITS}
def task_availability(path,task_id):
 result=I.task_availability(path,task_id)
 if result.get('schema')!=I.VERSION:return result
 task,review=I.task_review(path,task_id);state=listing(path,{'review_id':review['id']})
 if task['revision']!=result['task_revision']:raise ValueError('Task changed while reading partial evidence')
 for row in state['partials']:
  if not row['current']:continue
  a=I.S.inspect(path,row['answer_id']);result['answers'].append({'answer_id':a['id'],'answer_hash':digest(a),'question':a['question'],
   'status':'partial_source_passes_for_review','reconciliation_complete':False,'question_receipt_ids':row['question_receipt_ids'],
   'review_attachment_id':row['id'],'availability':a.get('source_availability'),'coverage':a['coverage'],
   'source_scope':a['source_scope'],'record_scope':a['record_scope'],'clinical_meaning_validated':False})
 if I.R.task(path,task_id)!=task or listing(path,{'review_id':review['id']})!=state:raise ValueError('Partial evidence changed while reading task')
 result.update(partial_history=state['partials'],partial_implementation=identity());result.pop('id',None);result['id']=digest(result)
 return result
