"""Exact supplied USD cost scenarios on immutable work/resource comparisons."""
from fractions import Fraction
from pathlib import Path
import hashlib,json,re,sqlite3
from ...contracts import digest
from . import precedence_capacity as P
R=P.R
VERSION='work-cost-scenario/1'
LIMITS=[
 'Rates and fixed costs are explicit supplied assumptions, not measured payroll, purchased services, payer terms or a recommendation to spend.',
 'USD only; hourly rates and batch fixed costs are supplied to cents. Calculations retain exact rational cents and round aggregate displays half-up to cents. Displayed rounded rows may not sum exactly.',
 'Occupied-time and available-staff-time costs are alternative views and must not be added. Occupied time prices modeled task work; available time prices every declared worker from pool availability until the end of the calculation window, including idle time.',
 'A fixed cost is counted once for this selected batch in each alternative view/window. Horizon and whole-batch figures are alternative windows and must not be added.',
 'Whole-batch costs are unavailable when any selected task is blocked. A horizon cost never establishes that the work, clinical care or program is complete.',
 'Per-selected-task and per-selected-patient figures are simple averages over the exact selected scope, not attributed patient charges, payments or clinical outcomes. Fixed and idle capacity costs are not assigned to individuals.',
 'No observed payments, revenues, benefits, savings, contract settlement, attributable outcomes or ROI are inferred. No tasks, staffing assignments, reservations or external actions change.'
]

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),P.identity(),R.identity()])

def cents(value,label):
 if not isinstance(value,str) or not re.fullmatch(r'(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?',value):raise ValueError(label+' requires a nonnegative USD decimal string with at most two decimal places')
 whole,_,part=value.partition('.');return int(whole)*100+int(part.ljust(2,'0') or '0')

def money(value):
 value=Fraction(value);n,d=abs(value.numerator),value.denominator
 rounded=(2*n+d)//(2*d);signed=-rounded if value<0 else rounded
 display=('-' if signed<0 else '')+str(abs(signed)//100)+'.'+str(abs(signed)%100).zfill(2)
 return {'currency':'USD','exact_cents':{'numerator':value.numerator,'denominator':value.denominator},'display_amount':display,'rounding':'half_up_to_cents'}

def exact(value):return Fraction(value['exact_cents']['numerator'],value['exact_cents']['denominator'])

def parent(path,kind,key):
 if kind not in ('resource','dependency'):raise ValueError('Resource or dependency scenario parent required')
 row=(R if kind=='resource' else P).inspect(path,key)
 if not row['current']:raise ValueError('Original work scenario is stale: '+row['stale_reason'])
 return {k:v for k,v in row.items() if k not in ('current','stale_reason')}

def pools(original,kind):return original['request']['pools'] if kind=='resource' else original['resource_assumptions']['pools']

def validate(request,original):
 R.base.care.exact(request,'parent_type parent_id question assumption_source currency rates fixed_costs')
 if request['parent_type'] not in ('resource','dependency') or request['parent_id']!=original['id']:raise ValueError('Exact work-scenario parent required')
 if request['currency']!='USD':raise ValueError('This version requires explicit USD costs; no currency conversion is available')
 out={k:R.base.care.text(request[k],k,2000 if k=='assumption_source' else 1000) for k in ('question','assumption_source')}
 out.update(parent_type=request['parent_type'],parent_id=original['id'],currency='USD')
 rates=request['rates'];pool_ids={p['id'] for p in pools(original,request['parent_type'])}
 if not isinstance(rates,list) or len(rates)!=len(pool_ids):raise ValueError('An explicit hourly rate and source are required for every resource pool')
 out['rates']=[]
 for rate in rates:
  R.base.care.exact(rate,'pool_id hourly_rate source');key=R.base.care.text(rate['pool_id'],'Pool ID',150)
  if key not in pool_ids:raise ValueError('Cost pool must belong to the exact work scenario')
  out['rates'].append({'pool_id':key,'hourly_rate':money(cents(rate['hourly_rate'],'Hourly rate'))['display_amount'],'source':R.base.care.text(rate['source'],'Rate source',2000)})
 if len({r['pool_id'] for r in out['rates']})!=len(pool_ids):raise ValueError('Duplicate or missing cost pool')
 out['rates'].sort(key=lambda r:r['pool_id']);fixed=request['fixed_costs']
 if not isinstance(fixed,list) or len(fixed)>32:raise ValueError('Explicit batch fixed-cost list required (zero to 32 items)')
 out['fixed_costs']=[]
 for item in fixed:
  R.base.care.exact(item,'id label amount source')
  row={k:R.base.care.text(item[k],k,2000 if k=='source' else 150) for k in ('id','label','source')}
  row['amount']=money(cents(item['amount'],'Fixed cost'))['display_amount'];out['fixed_costs'].append(row)
 if len({r['id'] for r in out['fixed_costs']})!=len(out['fixed_costs']):raise ValueError('Distinct fixed-cost IDs required')
 out['fixed_costs'].sort(key=lambda r:r['id']);return out

def window_cost(sim,resources,rates,fixed,end,patients):
 if end is None:return {'status':'unavailable_blocked_batch','reason':'Selected tasks are blocked; no whole-batch cost or completion is inferred','blocked_task_ids':[j['id'] for j in sim['blocked_tasks']]}
 rows=[];tasks=[];busy=Fraction(0);staff=Fraction(0)
 for p in resources:
  used=0;available=p['workers']*max(0,end-p['available_from_minute']);rate=rates[p['id']]
  for j in sim['schedule']:
   if j['pool_id']!=p['id']:continue
   minutes=min(j['duration_minutes'],max(0,end-j['start_minute']));used+=minutes
   tasks.append({'task_id':j['id'],'patient_id':j['patient_id'],'pool_id':p['id'],'occupied_minutes':minutes,'occupied_labor_cost':money(Fraction(minutes*rate,60))})
  if used>available:raise ValueError('Scheduled work exceeds declared available worker time')
  occupied=Fraction(used*rate,60);capacity=Fraction(available*rate,60);busy+=occupied;staff+=capacity
  rows.append({'pool_id':p['id'],'name':p['name'],'workers':p['workers'],'occupied_worker_minutes':used,'available_worker_minutes':available,'idle_worker_minutes':available-used,'occupied_labor_cost':money(occupied),'available_staff_labor_cost':money(capacity)})
 result={'status':'calculated_supplied_cost','end_minute':end,'completed_tasks':sum(j['finish_minute']<=end for j in sim['schedule']),
  'selected_task_count':sim['task_count'],'selected_patient_count':patients,'blocked_task_ids':[j['id'] for j in sim['blocked_tasks']],
  'fixed_batch_cost':money(fixed),'occupied_labor_cost':money(busy),'available_staff_labor_cost':money(staff),
  'idle_staff_labor_cost':money(staff-busy),'pools':rows,'task_occupied_costs':tasks,'cost_views':{}}
 for key,amount in [('occupied_time',busy+fixed),('available_staff_time',staff+fixed)]:
  result['cost_views'][key]={'total':money(amount),'per_selected_task':money(amount/sim['task_count']) if sim['task_count'] else None,'per_selected_patient':money(amount/patients) if patients else None}
 return result

def calculate(original,request):
 request=validate(request,original);kind=request['parent_type'];resources=pools(original,kind)
 baseline_resources=[{**p,'workers':min(1,p['workers'])} for p in resources] if kind=='resource' else resources
 rates={r['pool_id']:cents(r['hourly_rate'],'Hourly rate') for r in request['rates']};fixed=sum(cents(r['amount'],'Fixed cost') for r in request['fixed_costs'])
 comparisons={}
 for case,c in original['comparisons'].items():
  baseline=c['one_worker_per_active_pool'] if kind=='resource' else c['without_dependencies'];proposed=c['proposed'] if kind=='resource' else c['with_dependencies']
  pair={}
  for label,sim,ps in [('baseline',baseline,baseline_resources),('proposed',proposed,resources)]:
   pair[label]={'horizon':window_cost(sim,ps,rates,fixed,sim['horizon_minutes'],original['unique_patients']),
                'whole_batch':window_cost(sim,ps,rates,fixed,sim['whole_batch_clearance_minutes'],original['unique_patients'])}
  pair['proposed_minus_baseline']={}
  for window in ('horizon','whole_batch'):
   a=pair['proposed'][window];b=pair['baseline'][window]
   pair['proposed_minus_baseline'][window]={key:money(exact(a['cost_views'][key]['total'])-exact(b['cost_views'][key]['total'])) for key in ('occupied_time','available_staff_time')} if a['status']==b['status']=='calculated_supplied_cost' else None
  comparisons[case]=pair
 row={'schema':VERSION,'implementation':identity(),'parent_type':kind,'parent_id':original['id'],'parent_hash':digest(original),
  'buyer_id':original['buyer_id'],'original_question':original['question'],'question':request['question'],'request':request,
  'selected_task_ids':original['selected_task_ids'],'unique_patients':original['unique_patients'],
  'baseline_description':'One worker per active pool; same original readiness and durations' if kind=='resource' else 'Exact original resource scenario without the supplied task dependencies',
  'comparisons':comparisons,'limits':LIMITS,'authority':'supplied_operational_cost_scenario','measured_costs':False,'revenue_established':False,'realized_savings_established':False,
  'tasks_changed':0,'service_slots_reserved':0,'new_paid_api_calls':0,'external_actions_executed':0}
 row['id']=digest(row);return row

def database(path):
 db=sqlite3.connect(Path(path)/'work-cost.sqlite3',timeout=30)
 db.execute('CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY,parent_type TEXT,parent TEXT,buyer TEXT,payload TEXT)')
 for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS scenarios_{op} BEFORE {op} ON scenarios BEGIN SELECT RAISE(ABORT,'Work cost history is immutable'); END")
 return db

def compare(path,request):
 if not isinstance(request,dict):raise ValueError('Explicit work-cost scenario required')
 original=parent(path,request.get('parent_type'),request.get('parent_id'));row=calculate(original,request);db=database(path)
 try:
  with db:
   db.execute('BEGIN IMMEDIATE')
   if parent(path,row['parent_type'],row['parent_id'])!=original:raise ValueError('Work scenario changed during cost comparison')
   db.execute('INSERT OR IGNORE INTO scenarios VALUES (?,?,?,?,?)',(row['id'],row['parent_type'],row['parent_id'],row['buyer_id'],json.dumps(row)))
 finally:db.close()
 return row

def inspect(path,key):
 R.base.care.text(key,'Cost scenario ID',64);db=database(path)
 try:found=db.execute('SELECT payload FROM scenarios WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if not found:raise ValueError('Unknown cost scenario')
 row=json.loads(found[0])
 if digest({k:v for k,v in row.items() if k!='id'})!=key:raise ValueError('Cost scenario integrity mismatch')
 try:
  if row['implementation']!=identity():raise ValueError('Cost implementation changed')
  original=parent(path,row['parent_type'],row['parent_id'])
  if digest(original)!=row['parent_hash'] or calculate(original,row['request'])!=row:raise ValueError('Original scenario or cost calculation changed')
  return {**row,'current':True,'stale_reason':None}
 except (ValueError,KeyError,FileNotFoundError) as e:return {**row,'current':False,'stale_reason':str(e)}

def history(path,kind,key):
 if kind not in ('resource','dependency'):raise ValueError('Resource or dependency parent required')
 R.base.care.text(key,'Parent scenario ID',64);db=database(path)
 try:keys=[r[0] for r in db.execute('SELECT id FROM scenarios WHERE parent_type=? AND parent=? ORDER BY rowid',(kind,key))]
 finally:db.close()
 return {'parent_type':kind,'parent_id':key,'scenarios':[inspect(path,k) for k in keys]}
