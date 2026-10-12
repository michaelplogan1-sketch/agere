"""Exact rate/count explanation between compatible supplied payment scenarios."""
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import conditional_payment as P
VERSION='conditional-payment-comparison/1'

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),P.identity()])
def parent(path,key):
 row=P.inspect(path,key)
 if not row['current']:raise ValueError('Conditional payment scenario is stale: '+row['stale_reason'])
 return P.strip(row)

def calculate(left,right,question):
 question=P.C.R.base.care.text(question,'Comparison question',2000)
 if left['id']==right['id']:raise ValueError('Two distinct saved payment scenarios required')
 if left['cost_id']!=right['cost_id'] or left['cost_hash']!=right['cost_hash'] or left['buyer_id']!=right['buyer_id']:raise ValueError('Payment comparison requires the same exact cost question and buyer')
 for field in ('cost_window','cost_basis'):
  if left['request'][field]!=right['request'][field]:raise ValueError('Same cost window and basis required')
 a=left['request']['terms'];b=right['request']['terms']
 for field in ('currency','unit_kind','unit_definition','period_start','period_end'):
  if a[field]!=b[field]:raise ValueError('Compatible currency, unit definition and performance period required')
 if left['available_unit_ids']!=right['available_unit_ids'] or left['patient_ids']!=right['patient_ids']:raise ValueError('Same fixed selected population required')
 old=P.C.cents(a['payment_per_unit'],'Original rate');new=P.C.cents(b['payment_per_unit'],'Compared rate');n=left['assumed_payable_units'];m=right['assumed_payable_units']
 rate_effect=n*(new-old);count_effect=new*(m-n);total=new*m-old*n
 if rate_effect+count_effect!=total:raise ValueError('Rate/count arithmetic did not reconcile')
 comparisons={}
 for case,pair in left['comparisons'].items():
  comparisons[case]={}
  for side in ('baseline','proposed'):
   before=pair[side];after=right['comparisons'][case][side]
   if before['status']!=after['status']:raise ValueError('Cost availability changed between scenarios')
   if before['status']!='calculated_conditional_payment':comparisons[case][side]={'status':'unavailable_blocked_batch'};continue
   if before['modeled_cost']!=after['modeled_cost']:raise ValueError('Fixed-work costs changed')
   delta=P.C.exact(after['receipts_minus_modeled_cost'])-P.C.exact(before['receipts_minus_modeled_cost'])
   if delta!=total:raise ValueError('Conditional balance difference failed reconciliation')
   comparisons[case][side]={'status':'reconciled_conditional_arithmetic','unchanged_modeled_cost':before['modeled_cost'],
    'original_receipts_minus_cost':before['receipts_minus_modeled_cost'],'compared_receipts_minus_cost':after['receipts_minus_modeled_cost'],'difference':P.C.money(delta)}
 oldids=set(left['request']['assumed_payable_unit_ids']);newids=set(right['request']['assumed_payable_unit_ids'])
 row={'schema':VERSION,'implementation':identity(),'question':question,'cost_id':left['cost_id'],'buyer_id':left['buyer_id'],
  'left_id':left['id'],'right_id':right['id'],'left_hash':digest(left),'right_hash':digest(right),'original_terms':a,'compared_terms':b,
  'original_assumed_payable_units':n,'compared_assumed_payable_units':m,'added_assumed_units':sorted(newids-oldids),'removed_assumed_units':sorted(oldids-newids),
  'term_changes':{k:{'original':a[k],'compared':b[k]} for k in a if a[k]!=b[k]},
  'rate_effect_at_original_count':P.C.money(rate_effect),'count_effect_at_compared_rate':P.C.money(count_effect),'total_hypothetical_receipt_difference':P.C.money(total),
  'decomposition_order':'Change the supplied rate at the original unit count, then change the assumed count at the compared rate; the split depends on this stated order.',
  'comparisons':comparisons,'payment_readiness':'unresolved','program_revenue_established':False,'clinical_benefit_established':False,'external_actions':0,'tasks_changed':0,'provider_calls':0,
  'limits':['The exact saved cost question, selected population, unit definition, cost basis/window and stated performance dates must match.',
   'Changing which units are assumed payable without changing their count yields no rate/count arithmetic effect; membership changes are shown explicitly and do not establish eligibility.',
   'This decomposition explains supplied assumptions, not causal effects, expected receipts, observed revenue, realized savings or clinical benefit.',
   'Original saved terms and scenarios remain immutable. No automatic supersession, eligibility verification or external action occurs.']}
 row['id']=digest(row);return row

def database(path):
 db=sqlite3.connect(Path(path)/'payment-comparison.sqlite3',timeout=30);db.execute('CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,cost TEXT,payload TEXT)')
 for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS comparisons_{op} BEFORE {op} ON comparisons BEGIN SELECT RAISE(ABORT,'Payment comparison history is immutable'); END")
 return db

def compare(path,request):
 P.C.R.base.care.exact(request,'left_id right_id question');left=parent(path,request['left_id']);right=parent(path,request['right_id']);row=calculate(left,right,request['question']);db=database(path)
 try:
  with db:
   db.execute('BEGIN IMMEDIATE')
   if parent(path,left['id'])!=left or parent(path,right['id'])!=right:raise ValueError('Payment assumptions changed during comparison')
   db.execute('INSERT OR IGNORE INTO comparisons VALUES (?,?,?)',(row['id'],row['cost_id'],json.dumps(row)))
 finally:db.close()
 return row

def inspect(path,key):
 P.C.R.base.care.text(key,'Payment comparison ID',64);db=database(path)
 try:raw=db.execute('SELECT payload FROM comparisons WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if not raw:raise ValueError('Unknown payment comparison')
 row=json.loads(raw[0])
 if digest({k:v for k,v in row.items() if k!='id'})!=key:raise ValueError('Payment comparison integrity mismatch')
 try:
  if row['implementation']!=identity():raise ValueError('Payment comparison implementation changed')
  if calculate(parent(path,row['left_id']),parent(path,row['right_id']),row['question'])!=row:raise ValueError('Original payment assumptions changed')
  return {**row,'current':True,'stale_reason':None}
 except (ValueError,KeyError,FileNotFoundError) as e:return {**row,'current':False,'stale_reason':str(e)}

def history(path,key):
 P.C.R.base.care.text(key,'Cost ID',64);db=database(path)
 try:ids=[r[0] for r in db.execute('SELECT id FROM comparisons WHERE cost=? ORDER BY rowid',(key,))]
 finally:db.close()
 return {'cost_id':key,'comparisons':[inspect(path,k) for k in ids]}
