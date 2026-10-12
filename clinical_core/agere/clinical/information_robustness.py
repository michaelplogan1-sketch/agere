"""Exact common review-policy comparison across explicit finite joint models.

A single acquisition bundle and observation-to-choice rule is evaluated in every
model. Model-specific optimal policies are benchmarks, never added or averaged.
"""
from pathlib import Path
import hashlib,itertools,json,math,sqlite3
from ...contracts import digest
from . import shared_information as S

VERSION='information-model-robustness/1'

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),S.identity()])

def compatible(rows):
 if not 2<=len(rows)<=4:raise ValueError('Select two to four distinct joint scenarios')
 base=rows[0];a=base['request']['assumptions']
 for row in rows[1:]:
  b=row['request']['assumptions']
  if row['dependencies']!=base['dependencies'] or set(row['request']['question_ids'])!=set(base['request']['question_ids']):raise ValueError('Scenarios must bind the same current source, questions and work proposal')
  if set(b['requests'])!=set(a['requests']) or set(b['prior'])!=set(a['prior']) or any(b[k]!=a[k] for k in ('utilities','utility_scale','objective','outcomes')):raise ValueError('Same request universe, named states, utilities, unit, objective and joint observation definitions required')
 if len(a['utilities'])>3 or len(a['outcomes'])>4:raise ValueError('Exact common-policy comparison supports at most three review choices and four joint observation rows; no policies are sampled')
 return a

def calculate(rows):
 a=compatible(rows);models={r['id']:S.calculate(r['request']['assumptions']) for r in rows}
 if len(models)!=len(rows):raise ValueError('Distinct saved scenarios required')
 actions=sorted(a['utilities']);ids=sorted(a['requests']);policies=[]
 benchmark={key:c['baseline_utility']+max(b['net_value'] for b in c['bundles'] if b['available']) for key,c in models.items()}
 for n in range(len(ids)+1):
  for subset in itertools.combinations(ids,n):
   bundles={key:next(b for b in c['bundles'] if b['request_ids']==list(subset)) for key,c in models.items()}
   available=all(b['available'] for b in bundles.values())
   observations=sorted(next(iter(bundles.values()))['marginal_likelihoods'][next(iter(a['prior']))])
   for choices in itertools.product(actions,repeat=len(observations)):
    rule=dict(zip(observations,choices));values={}
    for row in rows:
     key=row['id'];v=row['request']['assumptions'];b=bundles[key]
     value=math.fsum(v['prior'][state]*b['marginal_likelihoods'][state][obs]*v['utilities'][rule[obs]][state] for state in v['prior'] for obs in observations)-b['assumed_cost']
     if not math.isfinite(value):raise ValueError('Common policy value exceeds numeric range')
     values[key]=value
    regrets={key:max(0.0,benchmark[key]-v) for key,v in values.items()} if available else None
    policies.append({'request_ids':list(subset),'rule':[{'request_observations':dict(zip(subset,json.loads(obs))),'review_choice':rule[obs]} for obs in observations],
      'available_in_every_model':available,'expected_net_utilities':values,'worst_expected_net_utility':min(values.values()),
      'model_regrets':regrets,'worst_regret':max(regrets.values()) if regrets else None})
 def tie(p):return len(p['request_ids']),p['request_ids'],[b['review_choice'] for b in p['rule']]
 feasible=[p for p in policies if p['available_in_every_model']];none=[p for p in feasible if not p['request_ids']]
 best=lambda candidates:min(candidates,key=lambda p:(-p['worst_expected_net_utility'],*tie(p)))
 least=lambda candidates:min(candidates,key=lambda p:(p['worst_regret'],*tie(p)))
 return {'model_specific_benchmarks':benchmark,'maximin_policy':best(feasible),'minimax_regret_policy':least(feasible),
  'no_acquisition_maximin':best(none),'no_acquisition_minimax_regret':least(none),
  'policies_enumerated':len(policies),'universally_available_policies':len(feasible),
  'bundle_frontier':[{'request_ids':list(subset),'available_in_every_model':all(p['available_in_every_model'] for p in policies if p['request_ids']==list(subset)),
    'maximin':best([p for p in feasible if p['request_ids']==list(subset)]) if any(p['request_ids']==list(subset) for p in feasible) else None,
    'minimax_regret':least([p for p in feasible if p['request_ids']==list(subset)]) if any(p['request_ids']==list(subset) for p in feasible) else None}
    for n in range(len(ids)+1) for subset in itertools.combinations(ids,n)],
  'tie_rule':'Exact numeric criterion, then fewer requests, exact IDs and alphabetical review choices.',
  'policy_meaning':'One fixed bundle and one common observation-to-review-choice rule across all selected models; model identity is not observed.'}


def snapshot(path,request):
 S.W.D.care.exact(request,'portfolio_id scenario_ids rationale')
 ids=S.names(request['scenario_ids'],'saved model scenarios',4)
 if len(ids)<2:raise ValueError('Two to four distinct scenarios required')
 S.W.D.care.text(request['rationale'],'uncertainty-set rationale',3000)
 history=S.listing(path,{'portfolio_id':request['portfolio_id']})['scenarios'];rows=[]
 for key in ids:
  row=next((r for r in history if r['id']==key),None)
  if row is None or not row['current']:raise ValueError('Every selected saved joint scenario must be current in this portfolio')
  rows.append({k:v for k,v in row.items() if k not in ('current','stale_reasons')})
 result=calculate(rows);a=rows[0]['request']['assumptions']
 out={'schema':VERSION,'request':request,'scenario_hashes':{r['id']:digest(r) for r in rows},'dependencies':rows[0]['dependencies'],
  'objective':a['objective'],'utility_scale':a['utility_scale'],'question_ids':rows[0]['request']['question_ids'],
  'calculation':result,'implementation':identity(),
  'meaning':'Finite model-uncertainty comparison for documentary review. Maximin protects the lowest modeled expected net utility; minimax regret limits loss relative to a strategy chosen with model identity known.',
  'limitations':['The selected models are an explicit uncertainty set, not a confidence interval, exhaustive set or probability distribution over models.',
   'The same observation-to-choice rule is evaluated in every model. Averaging model-specific optimal values would not establish an executable common rule.',
   'Only deterministic simultaneous acquisition bundles and terminal review choices are enumerated; no randomization or sequential acquisition.',
   'All model assumptions remain unvalidated. Robustness within this set is not clinical validity, worst-case patient safety, benefit or care priority.',
   'Costs and utilities use the same supplied unit. No-acquisition baselines choose a common review action, not a different action for each model.',
   'A bundle must be assumed available in every selected model to be considered. Infeasible bundles remain visible without a robust policy.',
   'Source/model changes invalidate current comparison. No source acquisition, task mutation, clinical recommendation or external action occurs.'],**S.LIMITS}
 out['id']=digest(out);return out

def database(path):
 db=sqlite3.connect(Path(path)/'information-robustness.sqlite3',timeout=30)
 db.execute('CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,portfolio TEXT,payload TEXT)')
 for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_robustness_{op} BEFORE {op} ON comparisons BEGIN SELECT RAISE(ABORT,'Robustness comparisons are immutable'); END")
 return db

def preview(path,request):return snapshot(path,request)

def prepare(path,request):
 S.W.D.care.exact(request,'comparison preview_id');out=snapshot(path,request['comparison'])
 if out['id']!=request['preview_id']:raise S.W.D.care.work.WorkConflict('Models or sources changed; preview again')
 db=database(path)
 try:
  with db:
   db.execute('BEGIN IMMEDIATE')
   if snapshot(path,request['comparison'])!=out:raise S.W.D.care.work.WorkConflict('Model dependencies changed while saving')
   db.execute('INSERT OR IGNORE INTO comparisons VALUES (?,?,?)',(out['id'],out['request']['portfolio_id'],json.dumps(out,allow_nan=False)))
   return S.R.verified(json.loads(db.execute('SELECT payload FROM comparisons WHERE id=?',(out['id'],)).fetchone()[0]))
 finally:db.close()

def listing(path,request):
 S.W.D.care.exact(request,'portfolio_id');db=database(path)
 try:rows=[S.R.verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM comparisons WHERE portfolio=? ORDER BY rowid',(request['portfolio_id'],))]
 finally:db.close()
 out=[]
 for row in rows:
  reasons=[]
  try:
   if snapshot(path,row['request'])!=row:reasons.append('Source, models or implementation changed')
  except (ValueError,KeyError,FileNotFoundError) as e:reasons.append(str(e))
  out.append({**row,'current':not reasons,'stale_reasons':reasons})
 return {'portfolio_id':request['portfolio_id'],'comparisons':out,**S.LIMITS}
