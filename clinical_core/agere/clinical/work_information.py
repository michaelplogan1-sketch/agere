"""Explicit finite information scenarios attached to an existing review question."""
from pathlib import Path
import hashlib,json,sqlite3
from ... import information_value as math
from ...contracts import digest
from . import evidence_dispositions as D,reference_applicability as R,record_context

VERSION='work-information-scenario/1'
LIMITS={'probabilities_learned':False,'clinical_probability_calibrated':False,'clinical_recommendation':False,
        'automatic_acquisition':False,'question_resolved':False,'provider_calls':0,'external_actions':0}

def identity():
 return digest([VERSION,*[hashlib.sha256(Path(x).read_bytes()).hexdigest() for x in (__file__,math.__file__)]])

def database(path):
 db=sqlite3.connect(Path(path)/'work-information.sqlite3',timeout=30)
 db.execute('CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY,target TEXT,payload TEXT)')
 for op in ('UPDATE','DELETE'):
  db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_information_{op} BEFORE {op} ON scenarios BEGIN SELECT RAISE(ABORT,'Information scenarios are immutable'); END")
 return db

def assumptions(a):
 D.care.exact(a,'prior utilities channels utility_scale assumption_source')
 for key in ('prior','utilities','channels'):
  if not isinstance(a[key],dict) or not 1<=len(a[key])<=8:raise ValueError('Supply one to eight named '+key)
  for name in a[key]:D.care.text(name,'assumption name',160)
 D.care.text(a['utility_scale'],'common utility/cost scale',240);D.care.text(a['assumption_source'],'assumption provenance',3000)
 for c in a['channels'].values():
  D.care.exact(c,'available cost likelihoods basis');D.care.text(c['basis'],'channel assumption basis',2000)
  if not isinstance(c['likelihoods'],dict):raise ValueError('Explicit observation likelihoods required')
  for row in c['likelihoods'].values():
   if not isinstance(row,dict) or not 1<=len(row)<=8:raise ValueError('Supply one to eight named observations')
   for name in row:D.care.text(name,'observation name',160)
 return math.expected_information_value(**a)

def dependencies(path,target):
 t=D.resolve_target(path,target);review=D.inspect(path,target,resolved=t)
 if review['stale_reasons']:raise D.care.work.WorkConflict('Question review is stale; inspect the original work first')
 records,meta=record_context.received(path,t['patient_id'])
 task=R.care_readiness.task(path,t['task_id']) if t.get('task_id') else None
 acquisitions=[]
 if target.get('kind')==R.KIND:
  for a in R.acquisitions(path,target):
   c=record_context.verified(path,a['acquisition']['context_id'])
   if digest(c)!=a['context_hash']:raise D.care.work.WorkConflict('Prepared question evidence changed')
   acquisitions.append({'id':a['id'],'context_id':c['id'],'context_hash':a['context_hash']})
 return {'target':t,'review_revision':review['revision'],'review_state':review['state'],'review_hash':digest(review),
  'task_revision':task['revision'] if task else None,'task_hash':digest(task) if task else None,
  'received_source_hash':digest(sorted((r['record_id'],digest(r)) for r in records)),
  'source_snapshot':meta['snapshot'],'prepared_reference_contexts':acquisitions}

def snapshot(path,request):
 D.care.exact(request,'target rationale assumptions')
 D.care.text(request['rationale'],'scenario rationale',3000)
 result=assumptions(request['assumptions']);bound=dependencies(path,request['target'])
 a=request['assumptions']
 baseline=[{'action':action,'assumed_expected_utility':sum(a['prior'][s]*values[s] for s in a['prior'])} for action,values in sorted(a['utilities'].items())]
 out={'schema':VERSION,'request':request,'question':bound['target']['question'],'patient_id':bound['target']['patient_id'],
  'as_of':bound['target']['as_of'],'dependencies':bound,'calculation':result,'baseline_actions':baseline,
  'assumptions_hash':digest(a),'implementation':identity(),
  'meaning':'Hypothetical value of one acquisition at a time for this exact review question. Inputs are supplied assumptions, not probabilities inferred from patient records. No observation has occurred and no action is selected for execution.',
  'limitations':['Utilities and acquisition costs share the explicitly supplied scale; do not label them measured dollars or clinical benefit without supporting inputs.',
   'Joint latent states may be named explicitly. Separate channels are alternatives; correlated acquisitions must not have their values added.',
   'Every observation branch is hypothetical. Receiving a report does not automatically update the prior, resolve the question or authorize action.',
   'Exact ties use the existing calculator’s alphabetical action/channel ordering; ordering is not clinical preference.',
   'Source, question, review and linked work changes require a new explicit scenario; prior assumptions are never silently refreshed.'],**LIMITS}
 out['id']=digest(out);return out

def preview(path,request):return snapshot(path,request)

def prepare(path,request):
 D.care.exact(request,'scenario preview_id');out=snapshot(path,request['scenario'])
 if out['id']!=request['preview_id']:raise D.care.work.WorkConflict('Question, source or assumptions changed; preview again')
 db=database(path)
 try:
  with db:
   db.execute('BEGIN IMMEDIATE')
   if snapshot(path,request['scenario'])!=out:raise D.care.work.WorkConflict('Information dependencies changed while saving')
   db.execute('INSERT OR IGNORE INTO scenarios VALUES (?,?,?)',(out['id'],digest(request['scenario']['target']),json.dumps(out,allow_nan=False)))
   return R.verified(json.loads(db.execute('SELECT payload FROM scenarios WHERE id=?',(out['id'],)).fetchone()[0]))
 finally:db.close()

def listing(path,request):
 D.care.exact(request,'target');db=database(path)
 try:rows=[R.verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM scenarios WHERE target=? ORDER BY rowid',(digest(request['target']),))]
 finally:db.close()
 if not rows:D.resolve_target(path,request['target'])
 out=[]
 for r in rows:
  reasons=[]
  try:
   if snapshot(path,r['request'])!=r:reasons.append('Question, sources, assumptions or work dependencies changed; retain history and create a current scenario')
  except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
  out.append({**r,'current':not reasons,'stale_reasons':reasons})
 return {'target':request['target'],'scenarios':out,**LIMITS}
