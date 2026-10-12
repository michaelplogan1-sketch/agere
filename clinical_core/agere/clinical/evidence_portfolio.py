"""Exact, bounded comparison of documentary request bundles, without priors.

A covered check has at least one conditional case whose requests are all in the
bundle. This is structural coverage, NOT a predicted or guaranteed resolution.
Different checks' hypothetical values need not be jointly achievable.
"""
from pathlib import Path
import hashlib,json,re,sqlite3,zlib
from ...contracts import digest
from . import evidence_acquisition as acquisition,evidence_requirements as requirements

VERSION='evidence-review-portfolio/1'
MAX_REQUESTS=16
MAX_CHECKS=128
LIMITS=[
 'Coverage counts distinct patient-contract checks with at least one complete conditional request set; it does not count cases or duplicate request uses.',
 'Each request uses one hypothetical review slot. Slots are not dollars, elapsed time, actual staff capacity or a spending cap.',
 'Completing requests does not establish binding values. Covered checks may stay unresolved; their hypothetical outcomes need not be jointly achievable.',
 'No probabilities, clinical benefits, information availability, care priority or service eligibility are estimated.',
 'Exact optimality applies only to this structural objective, selected population, registered contracts and complete request universe.',
 'Source changes invalidate the comparison. No request is sent and no clinical action is authorized.'
]


def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),acquisition.identity()])


def validate(p):
 if not isinstance(p,dict) or set(p)!={'contracts','review_slots'}:raise ValueError('Explicit contracts and hypothetical review slots required')
 cs=p['contracts'];slots=p['review_slots']
 if not isinstance(cs,list) or not 1<=len(cs)<=8 or any(not isinstance(c,str) for c in cs) or len(set(cs))!=len(cs):raise ValueError('One to eight distinct registered contracts required')
 for c in cs:requirements.definition(c)
 if type(slots) is not int or not 0<=slots<=MAX_REQUESTS:raise ValueError('Review slots must be an integer from zero through sixteen')
 return {'contracts':sorted(cs),'review_slots':slots}


def dependency(path,p):
 p=validate(p)
 return digest([identity(),[requirements.snapshot(path,{'contract':c,'filter':'all'})['identity'] for c in p['contracts']]])


def compare(proposal,review_slots):
 """OR subset transform, then exhaustive selection; deterministic greedy baseline."""
 if type(review_slots) is not int or not 0<=review_slots<=MAX_REQUESTS:raise ValueError('Bounded integer review slots required')
 items=sorted(proposal['items'],key=lambda r:r['id']);ids=[r['id'] for r in items]
 if len(set(ids))!=len(ids):raise ValueError('Duplicate request identifier')
 proofs=proposal['conditional_resolution'];keys=[(p['patient_id'],p['contract_id']) for p in proofs]
 if len(set(keys))!=len(keys):raise ValueError('Duplicate patient-contract check')
 unresolved=[p for p in proofs if p['resolution']['current_value'] is None]
 base={'review_slots':review_slots,'request_count':len(ids),'unresolved_check_count':len(unresolved),
       'patient_contracts_evaluated':len(proposal['references']),'population_digest':proposal['population_digest'],
       'objective':'distinct_checks_with_complete_conditional_request_set','slot_assumption':'one request = one hypothetical review slot',
       'limits':LIMITS,'optimal':None,'greedy_baseline':None,'frontier':[],
       'requests_sent':0,'clinical_execution_authorized':False,'new_paid_api_calls':0}
 if len(ids)>MAX_REQUESTS or len(unresolved)>MAX_CHECKS:
  return {**base,'status':'scope_exceeds_exact_bound','complete':False,'reason':'Narrow the explicit population or contracts; no requests were sampled or silently discarded.',
          'bounds':{'requests':MAX_REQUESTS,'checks':MAX_CHECKS}}
 if any(not p['resolution']['complete'] for p in unresolved):
  return {**base,'status':'incomplete_conditional_cases','complete':False,'reason':'An upstream expression was not fully enumerated; no optimality claim.'}
 positions={key:i for i,key in enumerate(ids)};checks=[];coverage=[0]*(1<<len(ids))
 for p in sorted(unresolved,key=lambda p:(p['patient_id'],p['contract_id'])):
  cases=p['resolution']['cases']
  if not cases or any(not c['request_ids'] or set(c['request_ids'])-set(ids) for c in cases):
   return {**base,'status':'unbound_conditional_case','complete':False,'reason':'A conditional case lacks a complete linked request set.'}
  idx=len(checks);checks.append({'patient_id':p['patient_id'],'contract_id':p['contract_id'],'requirement_receipt_id':p['requirement_receipt_id'],'cases':cases})
  for c in cases:
   mask=sum(1<<positions[key] for key in set(c['request_ids']));coverage[mask]|=1<<idx
 # coverage[S] = union of checks with a case request set contained in S.
 for i in range(len(ids)):
  bit=1<<i
  for mask in range(len(coverage)):
   if mask&bit:coverage[mask]|=coverage[mask^bit]
 def names(mask):return [key for i,key in enumerate(ids) if mask&(1<<i)]
 def order(mask):return (-coverage[mask].bit_count(),mask.bit_count(),names(mask))
 def describe(mask):
  selected=set(names(mask));witnesses=[]
  for i,c in enumerate(checks):
   if coverage[mask]&(1<<i):
    cases=[v for v in c['cases'] if set(v['request_ids'])<=selected]
    witnesses.append({k:v for k,v in c.items() if k!='cases'}|{'conditional_cases':cases})
  return {'request_ids':names(mask),'slots_used':mask.bit_count(),'covered_checks':coverage[mask].bit_count(),'checks':witnesses}
 limit=min(review_slots,len(ids));best=[None]*(limit+1)
 for mask in range(len(coverage)):
  n=mask.bit_count()
  if n<=limit and (best[n] is None or order(mask)<order(best[n])):best[n]=mask
 chosen=0;frontier=[]
 for slots in range(limit+1):
  if best[slots] is not None and order(best[slots])<order(chosen):chosen=best[slots]
  frontier.append({'review_slots':slots,**describe(chosen)})
 greedy=0
 for _ in range(limit):
  options=[greedy|(1<<i) for i in range(len(ids)) if not greedy&(1<<i)]
  if not options:break
  greedy=min(options,key=order)
 # Baseline keeps filling slots even when immediate gain is zero; ties by exact IDs.
 return {**base,'status':'exact_structural_comparison','complete':True,'optimal':describe(chosen),'greedy_baseline':describe(greedy),
         'baseline_gap':coverage[chosen].bit_count()-coverage[greedy].bit_count(),'frontier':frontier,
         'subsets_evaluated':len(coverage),'candidate_requests':items,
         'method':'Exact subset-union transform and exhaustive cardinality frontier; greedy marginal-coverage comparator. No submodularity assumption.'}


def storage(path):
 db=sqlite3.connect(Path(path)/'evidence-portfolios.sqlite3',timeout=30)
 db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,payload BLOB NOT NULL)')
 for op in ('UPDATE','DELETE'):db.execute(f'CREATE TRIGGER IF NOT EXISTS immutable_portfolio_{op} BEFORE {op} ON receipts BEGIN SELECT RAISE(ABORT,"Immutable evidence portfolio"); END;')
 return db


def save(path,proposal_id,review_slots):
 source=acquisition.load(path,proposal_id);result=compare(source,review_slots);code=identity()
 key=digest([code,proposal_id,digest(source),review_slots]);row={**result,'schema':VERSION,'id':key,'synthetic':True,'code_identity':code,
  'proposal_id':proposal_id,'proposal_hash':digest(source),'selection':source['selection'],'source_snapshot':source['source_snapshot'],'contracts':source['contracts']}
 # Recheck at the write boundary; historical source is never promoted to current.
 if digest(acquisition.load(path,proposal_id))!=row['proposal_hash']:raise ValueError('Acquisition changed during comparison')
 db=storage(path)
 try:
  old=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
  if old and json.loads(zlib.decompress(old[0]))!=row:raise ValueError('Portfolio identity collision')
  if not old:
   with db:db.execute('INSERT INTO receipts VALUES (?,?)',(key,zlib.compress(json.dumps(row,allow_nan=False).encode())))
 finally:db.close()
 return {**row,'cache_hit':old is not None}


def inspect(path,key):
 if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact portfolio ID required')
 db=storage(path)
 try:raw=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if raw is None:raise ValueError('Unknown evidence portfolio')
 row=json.loads(zlib.decompress(raw[0]))
 if row['code_identity']!=identity():raise ValueError('Comparison logic changed; refresh portfolio')
 if digest(acquisition.load(path,row['proposal_id']))!=row['proposal_hash']:raise ValueError('Portfolio source changed')
 return row


def execute(path,patients,p,*,selection=None):
 p=validate(p);proposal=acquisition.prepare(path,p['contracts'],sorted(patients),selection=selection)
 return save(path,proposal['id'],p['review_slots'])


def question(patient,contracts,slots):
 return f'Compare evidence-review bundles for patient {patient} using contracts {",".join(sorted(contracts))} with {slots} review slots.'


def local_question(text):
 m=re.fullmatch(r'Compare evidence-review bundles for patient ([A-Za-z0-9_-]{1,150}) using contracts ([a-z0-9_,]+) with ([0-9]{1,2}) review slots\.',text)
 if not m:return None
 patient,cs,slots=m.groups();p=validate({'contracts':cs.split(','),'review_slots':int(slots)})
 from .cohorts import empty
 cohort=empty();cohort['all']=[{'kind':'patient','field':'patient_id','op':'eq','value':patient}]
 return {'interpretation':'Compare hypothetical evidence-review bundles for the exact patient and registered documentary checks. Coverage is not actual resolution or clinical benefit.',
  'coverage':'supported_observation','unmet_requirements':[],
  'nodes':[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':cohort},'purpose':'Exact patient scope'},
           {'id':'bundles','operation':'evidence_portfolio','inputs':['population'],'parameters':p,'purpose':'Compare shared conditional request sets and open their existing source-review queue'}]}
