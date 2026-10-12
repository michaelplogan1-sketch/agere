"""Explicit duration sensitivity beside received clocks; never empirical labor fitting."""
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import operational_context as O
C=O.C
VERSION='operational-duration-sensitivity/1'
def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),O.identity()])
def context(path,key):
 r=O.inspect(path,key)
 if not r['current']:raise ValueError('Received operational context is stale: '+r['stale_reason'])
 return {k:v for k,v in r.items() if k not in ('current','stale_reason')}

def calculate(ctx,original,request):
 C.R.base.care.exact(request,'context_id question assumption_source applicability_rationale durations')
 if request['context_id']!=ctx['id'] or digest(original)!=ctx['parent_hash']:raise ValueError('Exact operational context and parent required')
 durations=request['durations'];required=set(original['duration_assumptions'])
 if not isinstance(durations,dict) or set(durations)!=required:raise ValueError('Explicit durations required for every original service and no others')
 for d in durations.values():
  C.R.base.care.exact(d,'low central high')
  for v in d.values():C.R.base.integer(v,'Duration minutes',1,43200)
  if not d['low']<=d['central']<=d['high']:raise ValueError('Require low <= central <= high duration')
 request={**request,'question':C.R.base.care.text(request['question'],'Sensitivity question',1000),'assumption_source':C.R.base.care.text(request['assumption_source'],'Assumption source',2000),'applicability_rationale':C.R.base.care.text(request['applicability_rationale'],'Applicability rationale and limitations',3000)}
 kind=ctx['parent_type'];pools=C.pools(original,kind);edges=original['request']['dependencies'] if kind=='dependency' else None;comparisons={}
 for case in ('low','central','high'):
  baseline=original['comparisons'][case]['with_dependencies' if kind=='dependency' else 'proposed']
  jobmap={j['id']:{k:j[k] for k in ('id','patient_id','service_id','duration_minutes','ready_minute')} for j in baseline['schedule']+baseline['blocked_tasks']}
  jobs=[jobmap[i] for i in original['selected_task_ids']]
  def run(js):return C.P.simulate(js,pools,baseline['horizon_minutes'],edges) if kind=='dependency' else C.R.simulate(js,pools,baseline['horizon_minutes'])
  if run(jobs)!=baseline:raise ValueError('Original duration baseline does not reproduce')
  proposed=run([{**j,'duration_minutes':durations[j['service_id']][case]} for j in jobs]);a=proposed['whole_batch_clearance_minutes'];b=baseline['whole_batch_clearance_minutes']
  comparisons[case]={'original':baseline,'supplied_duration_scenario':proposed,'completion_count_difference':proposed['completed_by_horizon']-baseline['completed_by_horizon'],'whole_batch_minutes_difference':a-b if a is not None and b is not None else None}
 row={'schema':VERSION,'implementation':identity(),'request':request,'context_id':ctx['id'],'context_hash':digest(ctx),'parent_type':kind,'parent_id':original['id'],'parent_hash':digest(original),'buyer_id':original['buyer_id'],'question':request['question'],'original_question':original['question'],'selected_task_ids':original['selected_task_ids'],'patient_ids':ctx['patient_ids'],
  'original_duration_assumptions':original['duration_assumptions'],'original_duration_assumption_source':original['duration_assumption_source'],'comparisons':comparisons,
  'assumptions':['Only the explicitly supplied duration scenarios change. Selected task order, patient scope, service compatibility, pools, ready times, dependency edges and horizon remain exactly the original baseline.',
   'Received encounter intervals are attached context, not fitted task durations. No clock interval is automatically converted to labor or assigned to a current service.',
   'Low, central and high are hypothetical cases, not confidence intervals. Differences show sensitivity to assumptions, not observed performance, causality, an optimal schedule or a staffing recommendation.',
   'Source capture, task comparability, staffing effort and interruption/arrival processes remain unknown. Any additional duration assumptions require independent justification.'],
  'empirically_calibrated':False,'staff_effort_established':False,'clinical_benefit_established':False,'tasks_changed':0,'service_slots_reserved':0,'provider_calls':0,'external_actions':0}
 row['id']=digest(row);return row

def database(path):
 db=sqlite3.connect(Path(path)/'operational-sensitivity.sqlite3',timeout=30);db.execute('CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY,context TEXT,payload TEXT)')
 for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS scenarios_{op} BEFORE {op} ON scenarios BEGIN SELECT RAISE(ABORT,'Operational sensitivity is immutable'); END")
 return db

def compare(path,request):
 if not isinstance(request,dict):raise ValueError('Explicit duration scenario required')
 ctx=context(path,request.get('context_id'));original=C.parent(path,ctx['parent_type'],ctx['parent_id']);row=calculate(ctx,original,request);db=database(path)
 try:
  with db:
   db.execute('BEGIN IMMEDIATE')
   if context(path,ctx['id'])!=ctx or C.parent(path,ctx['parent_type'],ctx['parent_id'])!=original:raise ValueError('Operational context changed during comparison')
   db.execute('INSERT OR IGNORE INTO scenarios VALUES (?,?,?)',(row['id'],ctx['id'],json.dumps(row)))
 finally:db.close()
 return row

def inspect(path,key):
 C.R.base.care.text(key,'Sensitivity ID',64);db=database(path)
 try:found=db.execute('SELECT payload FROM scenarios WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if not found:raise ValueError('Unknown operational sensitivity')
 row=json.loads(found[0])
 if digest({k:v for k,v in row.items() if k!='id'})!=key:raise ValueError('Operational sensitivity integrity mismatch')
 reason=None
 try:
  ctx=context(path,row['context_id']);original=C.parent(path,ctx['parent_type'],ctx['parent_id'])
  if calculate(ctx,original,row['request'])!=row:reason='Operational sensitivity no longer reproduces'
 except (ValueError,KeyError,FileNotFoundError) as e:reason=str(e)
 return {**row,'current':reason is None,'stale_reason':reason}

def history(path,key):
 C.R.base.care.text(key,'Context ID',64);db=database(path)
 try:ids=[r[0] for r in db.execute('SELECT id FROM scenarios WHERE context=? ORDER BY rowid',(key,))]
 finally:db.close()
 return {'context_id':key,'scenarios':[inspect(path,i) for i in ids]}
