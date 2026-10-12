"""Immutable supplied payment terms over fixed selected work costs, not entitlement."""
from pathlib import Path
from fractions import Fraction
from datetime import date
import hashlib,json,sqlite3
from ...contracts import digest
from . import work_cost_context as X,program_accountability as A
C=X.C
VERSION='conditional-payment-scenario/1'
LIMITS=[
 'All payment terms and payable units are supplied hypothetical assumptions, not a signed payer contract, eligibility decision, claim or payment authorization.',
 'Only the original selected patients, tasks or one selected batch can be counted, once per unit. Completion, coverage and patient-account membership never determine payable units.',
 'This calculation holds the selected work and its costs fixed while varying assumed payable units. It does not estimate the cost of expanding the work, repeat payments, patient-months, risk adjustment, withholding, taxes or settlement.',
 'The stated performance dates describe the supplied terms. Alignment of the relative-minute cost window to that period is unverified; the supplied alignment rationale is not evidence of alignment.',
 'Receipts minus modeled cost is conditional arithmetic, not profit, realized savings, return on investment, clinical benefit or an expected value. No probability of payment is supplied or inferred.',
 'Low, central and high cases come from the original duration assumptions, not confidence intervals. The same supplied payable-unit assumption is used in both cost alternatives.',
 'Exact rational cents are retained. The minimum whole-cent rate is rounded upward so it covers the exact modeled cost at the assumed unit count; ordinary amount displays round half-up.',
 'Received financial accounts stay in the separate source attachment on the cost question. No observed amount is summed, attributed to this program or netted against the modeled costs.'
]

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),X.identity(),A.definition('program_payment_evidence')['hash']])
def strip(row):return {k:v for k,v in row.items() if k not in ('current','stale_reason')}
def ceil(n):
 n=Fraction(n);return -(-n.numerator//n.denominator)
def integer(value,label):
 if type(value) is not int or value<0:raise ValueError(label+' must be a nonnegative integer')
 return value

def scope(path,key):
 cost=X.parent(path,key);patients=X.population(path,cost)
 if len(patients)!=cost['unique_patients']:raise ValueError('Selected cost population differs')
 return cost,patients

def units(cost,patients,kind):
 if kind=='selected_patient':return patients
 if kind=='selected_task':return sorted(cost['selected_task_ids'])
 if kind=='selected_batch':return [cost['id']]
 raise ValueError('Explicit selected_patient, selected_task or selected_batch unit required')

def requirements(patients):
 out=[]
 for req in A.definition('program_payment_evidence')['requirements']:
  for pid in patients if req['scope']=='patient' else [None]:
   out.append({'id':digest([req['id'],pid]),'requirement_id':req['id'],'patient_id':pid,'scope':req['scope'],'needed':req['needed'],
    'status':'unresolved','evidence_bound':False,'semantic_validity_established':False})
 return out

def describe(path,key):
 cost,patients=scope(path,key)
 return {'cost_id':cost['id'],'cost_hash':digest(cost),'buyer_id':cost['buyer_id'],'patient_ids':patients,
  'units':{kind:units(cost,patients,kind) for kind in ('selected_patient','selected_task','selected_batch')},'requirements':requirements(patients)}

def validate(request,cost,patients):
 C.R.base.care.exact(request,'cost_id question terms cost_window cost_basis alignment_rationale assumed_payable_unit_ids eligibility_assumption')
 if request['cost_id']!=cost['id']:raise ValueError('Exact saved cost question required')
 if request['cost_window'] not in ('horizon','whole_batch') or request['cost_basis'] not in ('occupied_time','available_staff_time'):raise ValueError('Explicit cost window and basis required')
 out={k:C.R.base.care.text(request[k],k,2000) for k in ('question','alignment_rationale','eligibility_assumption')}
 out.update(cost_id=cost['id'],cost_window=request['cost_window'],cost_basis=request['cost_basis'])
 terms=request['terms'];C.R.base.care.exact(terms,'id version label source currency unit_kind unit_definition payment_per_unit period_start period_end')
 t={k:C.R.base.care.text(terms[k],k,2000 if k in ('source','unit_definition') else 150) for k in ('id','version','label','source','unit_definition')}
 if terms['currency']!='USD':raise ValueError('Explicit USD terms required; no currency conversion')
 t.update(currency='USD',unit_kind=terms['unit_kind'],payment_per_unit=C.money(C.cents(terms['payment_per_unit'],'Payment per unit'))['display_amount'])
 for k in ('period_start','period_end'):
  value=terms[k]
  try:
   if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:raise ValueError()
  except (ValueError,TypeError):raise ValueError('Exact YYYY-MM-DD performance dates required')
  t[k]=value
 if t['period_start']>t['period_end']:raise ValueError('Performance period end precedes start')
 available=units(cost,patients,t['unit_kind']);selected=request['assumed_payable_unit_ids']
 if not isinstance(selected,list) or any(not isinstance(v,str) for v in selected) or len(set(selected))!=len(selected) or set(selected)-set(available):raise ValueError('Distinct assumed payable units must belong to this exact selected work scope')
 out.update(terms=t,assumed_payable_unit_ids=sorted(selected));return out

def arithmetic(cost,rate,count,maximum):
 """Cost and rate in exact cents; counts vary inside a fixed cost scope."""
 cost=Fraction(cost);rate=Fraction(rate);integer(count,'Assumed units');integer(maximum,'Scope units')
 if cost<0 or rate<0 or count>maximum:raise ValueError('Nonnegative costs and rates and within-scope units required')
 receipts=rate*count
 needed=ceil(cost/rate) if rate else (0 if cost==0 else None)
 required=cost/count if count else None
 return {'modeled_cost':C.money(cost),'hypothetical_receipts':C.money(receipts),'receipts_minus_modeled_cost':C.money(receipts-cost),
  'required_rate_at_assumed_units':C.money(required) if required is not None else None,
  'minimum_whole_cent_rate':C.money(ceil(required)) if required is not None else None,
  'rate_status':'calculated_at_supplied_unit_count' if count else 'undefined_zero_assumed_units',
  'required_payable_units_at_supplied_rate':needed,'required_units_status':'calculated' if needed is not None else 'impossible_positive_cost_zero_rate',
  'within_selected_unit_scope':needed<=maximum if needed is not None else False,
  'sensitivity':[{'assumed_payable_units':n,'hypothetical_receipts':C.money(rate*n),'receipts_minus_modeled_cost':C.money(rate*n-cost)} for n in range(maximum+1)]}

def calculate(cost,patients,request):
 request=validate(request,cost,patients);t=request['terms'];available=units(cost,patients,t['unit_kind']);n=len(request['assumed_payable_unit_ids']);rate=C.cents(t['payment_per_unit'],'Payment rate');comparisons={}
 for case,pair in cost['comparisons'].items():
  result={}
  for side in ('baseline','proposed'):
   view=pair[side][request['cost_window']]
   result[side]=({'status':'calculated_conditional_payment',**arithmetic(C.exact(view['cost_views'][request['cost_basis']]['total']),rate,n,len(available))}
    if view['status']=='calculated_supplied_cost' else {'status':'unavailable_blocked_batch','reason':view['reason'],'blocked_task_ids':view['blocked_task_ids']})
  result['proposed_minus_baseline_balance']=(C.money(C.exact(result['proposed']['receipts_minus_modeled_cost'])-C.exact(result['baseline']['receipts_minus_modeled_cost']))
   if all(result[s]['status']=='calculated_conditional_payment' for s in ('baseline','proposed')) else None)
  comparisons[case]=result
 row={'schema':VERSION,'implementation':identity(),'request':request,'cost_id':cost['id'],'cost_hash':digest(cost),'buyer_id':cost['buyer_id'],
  'question':request['question'],'cost_question':cost['question'],'selected_task_ids':cost['selected_task_ids'],'patient_ids':patients,'available_unit_ids':available,
  'assumed_payable_units':n,'maximum_selected_units':len(available),'terms_hash':digest(t),'terms_authority':'supplied_hypothetical_terms',
  'comparisons':comparisons,'requirements':requirements(patients),'payment_readiness':'unresolved',
  'verified_eligible_units':None,'signed_contract_established':False,'period_alignment_established':False,'program_revenue_established':False,
  'realized_savings_established':False,'clinical_benefit_established':False,'observed_amounts_netted':False,
  'tasks_changed':0,'external_actions':0,'provider_calls':0,'limits':LIMITS}
 row['id']=digest(row);return row

def database(path):
 db=sqlite3.connect(Path(path)/'conditional-payment.sqlite3',timeout=30)
 db.execute('CREATE TABLE IF NOT EXISTS terms(buyer TEXT,name TEXT,version TEXT,hash TEXT,payload TEXT,PRIMARY KEY(buyer,name,version))')
 db.execute('CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY,cost TEXT,buyer TEXT,payload TEXT)')
 for table in ('terms','scenarios'):
  for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'Conditional payment history is immutable'); END")
 return db

def compare(path,request):
 if not isinstance(request,dict):raise ValueError('Explicit payment scenario required')
 cost,patients=scope(path,request.get('cost_id'));row=calculate(cost,patients,request);t=row['request']['terms'];db=database(path)
 try:
  with db:
   db.execute('BEGIN IMMEDIATE')
   if scope(path,cost['id'])!=(cost,patients):raise ValueError('Selected cost or work scope changed during payment comparison')
   old=db.execute('SELECT hash,payload FROM terms WHERE buyer=? AND name=? AND version=?',(cost['buyer_id'],t['id'],t['version'])).fetchone()
   if old and (old[0]!=row['terms_hash'] or json.loads(old[1])!=t):raise ValueError('Supplied terms version is immutable; use a new version for changed terms')
   db.execute('INSERT OR IGNORE INTO terms VALUES (?,?,?,?,?)',(cost['buyer_id'],t['id'],t['version'],row['terms_hash'],json.dumps(t)))
   db.execute('INSERT OR IGNORE INTO scenarios VALUES (?,?,?,?)',(row['id'],cost['id'],cost['buyer_id'],json.dumps(row)))
 finally:db.close()
 return row

def inspect(path,key):
 C.R.base.care.text(key,'Payment scenario ID',64);db=database(path)
 try:
  found=db.execute('SELECT payload FROM scenarios WHERE id=?',(key,)).fetchone()
  if not found:raise ValueError('Unknown conditional payment scenario')
  row=json.loads(found[0]);t=row['request']['terms'];registered=db.execute('SELECT hash,payload FROM terms WHERE buyer=? AND name=? AND version=?',(row['buyer_id'],t['id'],t['version'])).fetchone()
 finally:db.close()
 if digest({k:v for k,v in row.items() if k!='id'})!=key:raise ValueError('Payment scenario integrity mismatch')
 if not registered or registered[0]!=digest(t) or json.loads(registered[1])!=t:raise ValueError('Supplied terms registration integrity mismatch')
 try:
  if row['implementation']!=identity():raise ValueError('Payment implementation changed')
  cost,patients=scope(path,row['cost_id'])
  if calculate(cost,patients,row['request'])!=row:raise ValueError('Cost, scope or payment assumptions changed')
  return {**row,'current':True,'stale_reason':None}
 except (ValueError,KeyError,FileNotFoundError) as e:return {**row,'current':False,'stale_reason':str(e)}

def history(path,key):
 C.R.base.care.text(key,'Cost ID',64);db=database(path)
 try:keys=[r[0] for r in db.execute('SELECT id FROM scenarios WHERE cost=? ORDER BY rowid',(key,))]
 finally:db.close()
 return {'cost_id':key,'scenarios':[inspect(path,k) for k in keys]}
