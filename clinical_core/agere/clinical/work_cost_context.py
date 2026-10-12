"""Received financial accounts for the exact selected work population, never ROI."""
from collections import Counter
from pathlib import Path
import hashlib,json,sqlite3
from ...contracts import digest
from . import work_cost as C,financial_accounts as F
VERSION='work-cost-financial-context/1'
LIMITS=[
 'Patients come from the exact selected work tasks. Their accounts are patient-level received context, not established program, service, provider, plan or task attribution.',
 'Snapshot amounts, adjudication and received cash reports keep their original accounting definitions and source dates. Missing or inconsistent reports remain unresolved.',
 'No account is selected as program revenue, no cross-account or cross-currency amount is summed here, and no amount is netted against supplied work costs.',
 'This attachment does not establish benefits, payment entitlement, signed contract terms, measured program costs, realized savings, ROI or clinical outcomes.',
 'The explicit receipt cutoff controls source availability, not a verified financial service period. Complete outside capture and historical correction reconstruction remain unknown.'
]

def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),C.identity(),F.identity()])

def parent(path,key):
 r=C.inspect(path,key)
 if not r['current']:raise ValueError('Work cost scenario is stale: '+r['stale_reason'])
 return {k:v for k,v in r.items() if k not in ('current','stale_reason')}

def population(path,cost):
 work=C.parent(path,cost['parent_type'],cost['parent_id'])
 sim=work['comparisons']['central']['proposed' if cost['parent_type']=='resource' else 'with_dependencies']
 jobs=sim['schedule']+sim['blocked_tasks']
 if set(j['id'] for j in jobs)!=set(cost['selected_task_ids']):raise ValueError('Exact selected work population required')
 return sorted({j['patient_id'] for j in jobs})

def snapshot(path,request):
 C.R.base.care.exact(request,'cost_id available_by');cost=parent(path,request['cost_id']);ids=population(path,cost)
 params={'available_by':request['available_by']};prep=F.prepare(path,params)
 if set(ids)-set(prep['patients']):raise ValueError('Selected work patient lacks a financial source mapping')
 F.execute(path,ids,params,prepared=prep,preview_limit=0)
 reports=[]
 for pid in ids:
  key=F.receipt_id(prep,pid);report=F.inspect(path,key)
  if report['id']!=key or report['patient_id']!=pid or report['available_by']!=params['available_by'] or report['base']!=prep['base']:
   raise ValueError('Received financial report scope changed')
  reports.append({'patient_id':pid,'receipt_id':key,'receipt_hash':digest(report),'report':report})
 after=F.prepare(path,params)
 if after['base']!=prep['base'] or any(after['patients'].get(pid)!=prep['patients'][pid] for pid in ids):raise ValueError('Selected financial sources changed during assembly')
 statuses=Counter()
 for item in reports:statuses.update(item['report']['status_counts'])
 row={'schema':VERSION,'implementation':identity(),'request':dict(request),'cost_id':cost['id'],'cost_hash':digest(cost),'buyer_id':cost['buyer_id'],
  'question':cost['question'],'selected_task_ids':cost['selected_task_ids'],'patient_ids':ids,'available_by':request['available_by'],
  'financial_reports':reports,'summary':{'patients':len(ids),'accounts':sum(len(r['report']['accounts']) for r in reports),'account_status_counts':dict(statuses),
   'unassigned_sources':sum(len(r['report']['unassigned_sources']) for r in reports)},
  'population_basis':'Exact selected internal-task patient IDs; no clinical or payer attribution inferred',
  'program_account_attribution_established':False,'program_revenue_established':False,'realized_savings_established':False,'amounts_netted_against_cost':False,
  'limits':LIMITS,'tasks_changed':0,'provider_calls':0,'external_actions':0}
 if parent(path,cost['id'])!=cost:raise ValueError('Cost scenario changed during financial context assembly')
 row['id']=digest(row);return row

def database(path):
 db=sqlite3.connect(Path(path)/'work-cost-context.sqlite3',timeout=30);db.execute('CREATE TABLE IF NOT EXISTS contexts(id TEXT PRIMARY KEY,cost TEXT,payload TEXT)')
 for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS contexts_{op} BEFORE {op} ON contexts BEGIN SELECT RAISE(ABORT,'Cost financial context is immutable'); END")
 return db

def preview(path,request):return snapshot(path,request)

def prepare(path,request):
 C.R.base.care.exact(request,'cost_id available_by preview_id');selection={k:request[k] for k in ('cost_id','available_by')};row=snapshot(path,selection)
 if row['id']!=request['preview_id']:raise ValueError('Financial context changed; preview the exact current scope again')
 db=database(path)
 try:
  with db:
   db.execute('BEGIN IMMEDIATE')
   if snapshot(path,selection)!=row:raise ValueError('Financial context changed during save')
   db.execute('INSERT OR IGNORE INTO contexts VALUES (?,?,?)',(row['id'],row['cost_id'],json.dumps(row)))
 finally:db.close()
 return row

def history(path,key):
 C.R.base.care.text(key,'Cost ID',64);db=database(path)
 try:rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM contexts WHERE cost=? ORDER BY rowid',(key,))]
 finally:db.close()
 out=[]
 for row in rows:
  if digest({k:v for k,v in row.items() if k!='id'})!=row['id']:raise ValueError('Cost context integrity mismatch')
  reason=None
  try:
   if row['implementation']!=identity() or snapshot(path,row['request'])!=row:reason='Cost or received financial context changed'
  except (ValueError,KeyError,FileNotFoundError) as e:reason=str(e)
  out.append({**row,'current':reason is None,'stale_reason':reason})
 return {'cost_id':key,'contexts':out}
