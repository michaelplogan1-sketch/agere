"""Finite joint-observation value on exact existing documentary review requests.

Every subset is marginalized from ONE supplied joint experiment. Costs are
counted once per exact request; no independence or clinical utilities inferred.
"""
from pathlib import Path
import hashlib,itertools,json,math,sqlite3
from ...contracts import digest
from . import evidence_portfolio as P,evidence_acquisition as A,evidence_requirements as Q
from . import work_information as W,reference_applicability as R

VERSION='shared-review-information/1'
LIMITS={**W.LIMITS,'requests_sent':0,'clinical_execution_authorized':False}

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),P.identity(),W.identity()])

def names(value,label,limit):
 if not isinstance(value,list) or not 1<=len(value)<=limit or any(not isinstance(x,str) or not x.strip() for x in value) or len(set(value))!=len(value):raise ValueError('Explicit distinct '+label+' required within the calculation bound')
 return sorted(value)

def calculate(a):
 W.D.care.exact(a,'prior utilities requests outcomes likelihoods utility_scale assumption_source objective joint_basis')
 W.D.care.text(a['objective'],'one common decision objective',2000)
 W.D.care.text(a['joint_basis'],'joint experiment basis',3000)
 if not isinstance(a['requests'],dict):raise ValueError('Explicit request assumptions required')
 ids=names(list(a['requests']),'request identities',3)
 for rid,v in a['requests'].items():
  W.D.care.exact(v,'available cost basis')
  if type(v['available']) is not bool or type(v['cost']) not in (int,float) or not math.isfinite(v['cost']) or v['cost']<0:raise ValueError('Finite nonnegative cost and explicit availability required')
  W.D.care.text(v['basis'],'request cost/availability basis',2000)
 if not isinstance(a['outcomes'],dict) or not 1<=len(a['outcomes'])<=8:raise ValueError('One to eight joint outcomes required')
 vectors=[]
 for label,vector in a['outcomes'].items():
  W.D.care.text(label,'joint outcome name',160)
  if not isinstance(vector,dict) or set(vector)!=set(ids):raise ValueError('Each joint outcome must specify every selected request')
  for value in vector.values():W.D.care.text(value,'request observation',160)
  vectors.append(tuple(vector[r] for r in ids))
 if len(set(vectors))!=len(vectors):raise ValueError('Duplicate joint observation vector')
 # Validate the full experiment through the unchanged finite calculator first.
 common={k:a[k] for k in ('prior','utilities','utility_scale','assumption_source')}
 validation=W.assumptions({**common,'channels':{'joint':{'available':True,'cost':0,'basis':a['joint_basis'],'likelihoods':a['likelihoods']}}})
 if set(next(iter(a['likelihoods'].values())))!=set(a['outcomes']):raise ValueError('Likelihoods must match the explicit joint outcomes')
 bundles=[]
 for n in range(len(ids)+1):
  for subset in itertools.combinations(ids,n):
   # Project vectors and sum the supplied probability masses, never multiply marginals.
   observed={o:json.dumps([a['outcomes'][o][r] for r in subset],ensure_ascii=False) for o in a['outcomes']}
   likelihoods={s:{} for s in a['prior']}
   for s,dist in a['likelihoods'].items():
    total=math.fsum(dist.values())
    for v in sorted(set(observed.values())):
     likelihoods[s][v]=min(1.0,math.fsum(probability for o,probability in dist.items() if observed[o]==v)/total)
   cost=sum(a['requests'][r]['cost'] for r in subset)
   c=W.assumptions({**common,'channels':{'bundle':{'available':all(a['requests'][r]['available'] for r in subset),
       'cost':cost,'basis':a['joint_basis'],'likelihoods':likelihoods}}})['channels'][0]
   for branch in c['branches']:branch['request_observations']=dict(zip(subset,json.loads(branch.pop('observation'))))
   bundles.append({k:v for k,v in c.items() if k not in ('channel','basis')}|{'request_ids':list(subset),'marginal_likelihoods':likelihoods})
 order=lambda b:(-b['net_value'],len(b['request_ids']),b['request_ids'])
 feasible=[b for b in bundles if b['available']]
 preferred=min(feasible,key=order)
 singles=[b for b in feasible if len(b['request_ids'])<=1]
 best_single=min(singles,key=order)
 return {'baseline_action':validation['current_action_under_assumptions'],'baseline_utility':validation['current_expected_utility'],
  'bundles':bundles,'preferred_request_ids_under_assumptions':preferred['request_ids'],
  'best_single_or_none':best_single,'net_gain_over_best_single_or_none':preferred['net_value']-best_single['net_value'],
  'method':'Exhaustive subsets; marginalize one supplied joint observation distribution; reuse finite expected-utility calculator; additive cost once per exact request.',
  'tie_rule':'Higher net value, then fewer requests, then exact request IDs; no clinical preference implied.'}

def source(path,key):
 p=P.inspect(path,key);a=A.load(path,p['proposal_id'])
 if not p['complete']:raise ValueError('A complete structural portfolio is required')
 return p,a

def choices(path,request):
 W.D.care.exact(request,'portfolio_id');p,a=source(path,request['portfolio_id'])
 return {'portfolio_id':p['id'],'proposal_id':a['id'],'questions':[dict(r,title=Q.definition(r['contract'])['title']) for r in a['references']],
         'requests':a['items'],'limits':LIMITS}

def snapshot(path,request):
 W.D.care.exact(request,'portfolio_id question_ids rationale assumptions')
 selected=names(request['question_ids'],'documentary question receipts',8)
 W.D.care.text(request['rationale'],'shared decision rationale',3000)
 result=calculate(request['assumptions']);ids=set(request['assumptions']['requests'])
 p,a=source(path,request['portfolio_id']);refs={r['receipt_id']:r for r in a['references']};items={i['id']:i for i in a['items']}
 if set(selected)-set(refs) or ids-set(items):raise ValueError('Question or request outside the exact source portfolio')
 if any(not any(r['receipt_id'] in selected for r in items[rid]['requirements']) for rid in ids):raise ValueError('Every selected request must link to a selected question')
 if any(not any(r['receipt_id']==qid for rid in ids for r in items[rid]['requirements']) for qid in selected):raise ValueError('Every selected question must link to a selected request')
 questions=[Q.inspect(path,qid) for qid in selected]
 proofs=[v for v in a['conditional_resolution'] if v['requirement_receipt_id'] in selected]
 for b in result['bundles']:
  b['structurally_covered_question_ids']=[v['requirement_receipt_id'] for v in proofs if v['resolution']['current_value'] is None and any(set(c['request_ids'])<=set(b['request_ids']) for c in v['resolution']['cases'])]
 row={'schema':VERSION,'request':request,'calculation':result,'questions':[{'receipt_id':q['id'],'patient_id':q['patient_id'],'title':q['contract']['title'],'status':q['status'],'hash':digest(q)} for q in questions],
  'selected_requests':[items[rid] for rid in sorted(ids)],
  'dependencies':{'portfolio_id':p['id'],'portfolio_hash':digest(p),'proposal_id':a['id'],'proposal_hash':digest(a),'source_snapshot':a['source_snapshot'],'population_digest':a['population_digest'],
                'question_hashes':{q['id']:digest(q) for q in questions}},
  'structural_baseline':{'scope':'Original full portfolio; a different objective and possibly larger question/request scope','optimal':p['optimal'],'greedy_baseline':p['greedy_baseline'],'review_slots':p['review_slots']},
  'implementation':identity(),'meaning':'Hypothetical joint decision value for selected existing documentary questions. No observation, resolution, acquisition or clinical action has occurred.',
  'limitations':['All states, utilities, joint likelihoods, availability and costs are supplied assumptions; none are inferred or calibrated from the chart.',
    'States and choices describe one common objective for the selected questions. Separate question/channel values are never summed.',
    'Joint observations include every selected request. Omitted observation vectors are explicitly assigned zero probability; independence is never assumed.',
    'Marginalization assumes acquiring a subset does not change observation reliability, state or utility. Fixed additive costs, no bundling discounts or order effects.',
    'Validated likelihood rows are normalized within the existing calculator tolerance before marginalization; entered values remain recorded unchanged.',
    'Availability is a supplied deterministic feasibility assumption. Unavailable bundles remain visible but cannot be preferred.',
    'Structural coverage counts conditional possibilities only; modeled observations are not typed documentary facts and cannot resolve checks.',
    'Only exact request IDs are shared. Similar-looking requests are never merged. Linked checks do not multiply a request cost.',
    'This is a bounded simultaneous experiment, not a sequential acquisition policy, clinical priority, validated patient benefit or assigned buyer task.',
    'Source/interpretation/work-proposal or implementation changes require a new scenario; historical assumptions remain unchanged.'],**LIMITS}
 row['id']=digest(row);return row

def database(path):
 db=sqlite3.connect(Path(path)/'shared-information.sqlite3',timeout=30)
 db.execute('CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY,portfolio TEXT,payload TEXT)')
 for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_shared_information_{op} BEFORE {op} ON scenarios BEGIN SELECT RAISE(ABORT,'Shared information is immutable'); END")
 return db

def preview(path,request):return snapshot(path,request)

def prepare(path,request):
 W.D.care.exact(request,'scenario preview_id');out=snapshot(path,request['scenario'])
 if out['id']!=request['preview_id']:raise W.D.care.work.WorkConflict('Source, scope or assumptions changed; preview again')
 db=database(path)
 try:
  with db:
   db.execute('BEGIN IMMEDIATE')
   if snapshot(path,request['scenario'])!=out:raise W.D.care.work.WorkConflict('Shared information dependencies changed while saving')
   db.execute('INSERT OR IGNORE INTO scenarios VALUES (?,?,?)',(out['id'],out['request']['portfolio_id'],json.dumps(out,allow_nan=False)))
   return R.verified(json.loads(db.execute('SELECT payload FROM scenarios WHERE id=?',(out['id'],)).fetchone()[0]))
 finally:db.close()

def listing(path,request):
 W.D.care.exact(request,'portfolio_id');db=database(path)
 try:rows=[R.verified(json.loads(r[0])) for r in db.execute('SELECT payload FROM scenarios WHERE portfolio=? ORDER BY rowid',(request['portfolio_id'],))]
 finally:db.close()
 if not rows:source(path,request['portfolio_id'])
 out=[]
 for row in rows:
  reasons=[]
  try:
   if snapshot(path,row['request'])!=row:reasons.append('Source, question, work-proposal or implementation changed')
  except (ValueError,KeyError,FileNotFoundError) as exc:reasons.append(str(exc))
  out.append({**row,'current':not reasons,'stale_reasons':reasons})
 return {'portfolio_id':request['portfolio_id'],'scenarios':out,**LIMITS}
