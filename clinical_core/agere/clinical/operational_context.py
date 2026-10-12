"""Received appointment links and encounter clocks for an exact work population."""
from collections import Counter, defaultdict
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path
import hashlib,json,re,sqlite3
from ...contracts import digest
from . import work_cost as C,evidence_threads as T,warehouse_interpretation as native
VERSION='received-operational-context/1'
STREAMS=('appointments','encounters','referrals')
FIELDS={
 'appointments':('appointment_id','scheduled_date','status','completed_encounter_id'),
 'encounters':('encounter_id','type','status','start_time','end_time'),
 'referrals':('referral_id','referral_type','requested_specialty','requested_service','status','completion_status')}
COMMON=('patient_id','record_id','source_table','source_system','organization_id','source_patient_id','event_date','event_date_precision','event_time','recorded_time','signed_time','available_date','available_time')
LIMITS=[
 'The exact selected task population supplies patient scope only. Received encounters and referrals are not attributed to these tasks, services, workers, plans or programs.',
 'The period selects appointment scheduled dates and referral event dates. Encounter intervals require explicit same-patient/source completed-encounter links; no nearest-date, name or specialty matching is used.',
 'Counts describe received records, not independent appointments, attendance rates or complete outside capture. Multiple fulfilled reports for one appointment, multiple completed reports for one encounter and repeated completion links are excluded from interval summaries.',
 'Earlier booked and in-progress reports remain visible. A unique fulfilled report and a unique completed report may supply a documented interval when their other same-identity reports have consistent dates/start clocks and no conflicting terminal status. No latest-state or supersession claim is made.',
 'End minus start is a documented encounter interval, not clinician labor, patient waiting, care quality, completed treatment or an estimate for a different task. End-to-availability delay is source arrival timing, not service delay.',
 'Only explicit offset-aware clocks with minute or second source precision are used. Missing, inconsistent or negative intervals are unresolved; dates never become times. No timestamps or staff effort are imputed.',
 'The receipt cutoff is an inclusive source-available calendar day. This snapshot cannot reconstruct earlier versions or corrections. Referral status is retained literally; fulfillment and waiting time are not inferred.',
 'Descriptive interval summaries retain raw intervals as baseline and stratify by patient, source organization/system and encounter type. They are not representative duration distributions or clinical validation.',
 'Original assumptions and task state remain unchanged. Any later duration sensitivity must explicitly supply its own assumptions and rationale; no observation automatically becomes staffing capacity.'
]
def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),C.identity()])
def day(v):
 if not isinstance(v,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v):return None
 try:return date.fromisoformat(v)
 except ValueError:return None

def clock(v):
 if not isinstance(v,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})',v):return None
 try:
  t=datetime.fromisoformat(v.replace('Z','+00:00'))
  return t if t.utcoffset() is not None else None
 except ValueError:return None

def exact(v):return {'numerator':v.numerator,'denominator':v.denominator,'display':round(float(v),3)}
def minutes(a,b):
 delta=b-a
 return Fraction((delta.days*86400+delta.seconds)*1000000+delta.microseconds,60000000)
def source_key(r,identifier):
 parts=tuple(r.get(k) for k in ('patient_id','source_system','organization_id','source_patient_id'))+(identifier,)
 return parts if all(isinstance(v,str) and v for v in parts) else None

def period_reason(v,start,end):
 d=day(v)
 return 'missing_or_invalid_period_date' if d is None else 'outside_selected_period' if not start<=d<=end else None

def interval(e,cutoff):
 start,end=clock(e.get('start_time')),clock(e.get('end_time'));reason=None
 if e.get('status')!='completed':reason='encounter_not_reported_completed'
 elif e.get('event_date_precision') not in ('minute','second'):reason='source_clock_precision_not_minute_or_second'
 elif start is None or end is None:reason='missing_or_imprecise_offset_clock'
 elif end<start:reason='negative_encounter_interval'
 elif end.date()>cutoff:reason='encounter_end_after_receipt_cutoff'
 if reason:return {'interval_minutes':None,'interval_issue':reason,'arrival_delay_minutes':None,'arrival_issue':'encounter_interval_unresolved'}
 arrival=clock(e.get('available_time'));arrival_issue=None
 if arrival is None:arrival_issue='missing_or_imprecise_arrival_clock'
 elif arrival.date()!=day(e.get('available_date')):arrival_issue='arrival_clock_date_disagrees'
 elif arrival<end:arrival_issue='arrival_before_encounter_end'
 return {'interval_minutes':exact(minutes(start,end)),'interval_issue':None,'arrival_delay_minutes':None if arrival_issue else exact(minutes(end,arrival)),'arrival_issue':arrival_issue}

def assess(pid,records,available_by,period_start,period_end):
 cutoff,start,end=day(available_by),day(period_start),day(period_end)
 if not cutoff or not start or not end or not start<=end<=cutoff:raise ValueError('Canonical period start <= end <= receipt cutoff required')
 if len({r['record_id'] for r in records})!=len(records):raise ValueError('Distinct received source records required')
 sources=[]
 for r in sorted(records,key=lambda x:x['record_id']):
  if r.get('patient_id')!=pid or r.get('source_table') not in STREAMS or r.get('synthetic') is not True or r.get('identity_status')!='matched' or not native.visible(r,available_by):raise ValueError('Visible matched same-patient synthetic sources required')
  sources.append({**{k:r.get(k) for k in COMMON},'fields':{k:r.get(k) for k in FIELDS[r['source_table']]},'record_hash':digest(r)})
 apps=[r for r in records if r['source_table']=='appointments'];encs=[r for r in records if r['source_table']=='encounters'];refs=[r for r in records if r['source_table']=='referrals']
 enc_index=defaultdict(list);appkeys=Counter();app_index=defaultdict(list);linked=Counter()
 for r in encs:
  key=source_key(r,r.get('encounter_id'))
  if key:enc_index[key].append(r)
 for r in apps:
  key=source_key(r,r.get('appointment_id'))
  if key:
   app_index[key].append(r)
   if r.get('status')=='fulfilled':appkeys[key]+=1
  key=source_key(r,r.get('completed_encounter_id'))
  if key and r.get('status')=='fulfilled':linked[key]+=1
 observations=[];used=set();groups=defaultdict(list)
 for a in sorted(apps,key=lambda x:x['record_id']):
  reason=period_reason(a.get('scheduled_date'),start,end);link=source_key(a,a.get('completed_encounter_id'));matches=enc_index.get(link,[]);appkey=source_key(a,a.get('appointment_id'))
  o={'appointment_record_id':a['record_id'],'appointment_id':a.get('appointment_id'),'scheduled_date':a.get('scheduled_date'),'reported_status':a.get('status'),'period_issue':reason,
   'completed_encounter_id':a.get('completed_encounter_id'),'encounter_record_ids':[e['record_id'] for e in matches],'appointment_history_record_ids':[r['record_id'] for r in app_index.get(appkey,[])],'interval_encounter_record_id':None,'interval_minutes':None,'interval_issue':reason,'arrival_delay_minutes':None,'arrival_issue':None}
  if reason is None:
   if not appkey:reason='appointment_identity_scope_incomplete'
   elif a.get('status')!='fulfilled':reason='appointment_not_reported_fulfilled'
   elif appkeys[appkey]>1:reason='duplicate_appointment_identity'
   elif any(r.get('scheduled_date')!=a.get('scheduled_date') or r.get('status') not in ('booked','arrived','fulfilled') or (r.get('completed_encounter_id') and r['completed_encounter_id']!=a.get('completed_encounter_id')) for r in app_index[appkey]):reason='conflicting_appointment_reports'
   elif not a.get('completed_encounter_id'):reason='no_explicit_completed_encounter_link'
   elif not link:reason='link_source_scope_incomplete'
   elif not matches:reason='linked_encounter_not_received_in_same_scope'
   elif len([e for e in matches if e.get('status')=='completed'])>1:reason='ambiguous_encounter_identity'
   elif not any(e.get('status')=='completed' for e in matches):reason='encounter_not_reported_completed'
   elif linked[link]>1:reason='encounter_linked_by_multiple_appointment_records'
   else:
    e=next(e for e in matches if e.get('status')=='completed');o['interval_encounter_record_id']=e['record_id']
    if any(r.get('start_time')!=e.get('start_time') or r.get('status') not in ('in_progress','completed') or (r.get('end_time') and r['end_time']!=e.get('end_time')) for r in matches):reason='conflicting_encounter_reports'
    else:o.update(interval(e,cutoff));reason=o['interval_issue']
    if reason is None:
     used.add(e['record_id']);group=(e['source_system'],e['organization_id'],e.get('type'))
     groups[group].append({'appointment_record_id':a['record_id'],'encounter_record_id':e['record_id'],'interval_minutes':o['interval_minutes'],'arrival_delay_minutes':o['arrival_delay_minutes']})
  o['interval_issue']=reason;observations.append(o)
 summaries=[]
 for group,obs in sorted(groups.items(),key=lambda x:tuple(str(v) for v in x[0])):
  values=sorted(Fraction(x['interval_minutes']['numerator'],x['interval_minutes']['denominator']) for x in obs);n=len(values)
  summaries.append({'source_system':group[0],'organization_id':group[1],'encounter_type':group[2],'count':n,'mean_minutes':exact(sum(values,Fraction())/n),'median_minutes':exact((values[(n-1)//2]+values[n//2])/2),'min_minutes':exact(values[0]),'max_minutes':exact(values[-1]),'raw_intervals':obs})
 referrals=[{'record_id':r['record_id'],'period_issue':period_reason(r.get('event_date'),start,end),'reported_status':r.get('status'),'reported_completion_status':r.get('completion_status'),'fulfillment_established':False,'wait_minutes':None} for r in sorted(refs,key=lambda x:x['record_id'])]
 return {'patient_id':pid,'sources':sources,'appointment_observations':observations,'interval_summaries':summaries,'referral_reports':referrals,
  'denominators':{'received_appointment_records':len(apps),'appointment_records_in_period':sum(o['period_issue'] is None for o in observations),'included_unique_linked_encounters':len(used),'interval_exclusions':dict(sorted(Counter(o['interval_issue'] for o in observations if o['interval_issue']).items())),
   'received_encounter_records':len(encs),'encounter_records_not_in_interval_summary':len(encs)-len(used),'received_referral_records':len(refs),'referral_records_in_period':sum(r['period_issue'] is None for r in referrals)},
  'staff_effort_minutes':None,'patient_wait_minutes':None,'task_attribution_established':False}

def population(original,kind):
 sim=original['comparisons']['central']['proposed' if kind=='resource' else 'with_dependencies'];jobs=sim['schedule']+sim['blocked_tasks']
 if len(jobs)!=len(original['selected_task_ids']) or set(j['id'] for j in jobs)!=set(original['selected_task_ids']):raise ValueError('Exact selected work population required')
 return sorted({j['patient_id'] for j in jobs})

def received(path,ids,cutoff):
 db,meta=T.connect(path)
 try:
  db.execute('BEGIN')
  if not day(cutoff) or not day(meta['snapshot']['as_of']) or cutoff>meta['snapshot']['as_of']:raise ValueError('Receipt cutoff must be within the active snapshot')
  result={}
  for pid in ids:
   if not db.execute('SELECT 1 FROM patients WHERE id=?',(pid,)).fetchone():raise ValueError('Selected patient absent from received snapshot')
   rows=[json.loads(x[0]) for x in db.execute('SELECT payload FROM records WHERE patient=? AND stream IN (?,?,?) ORDER BY id',(pid,*STREAMS))]
   result[pid]=[r for r in rows if native.visible(r,cutoff)]
  return {'source_digest':meta['source_digest'],'snapshot':meta['snapshot'],'patients':result}
 finally:db.close()

def snapshot(path,request):
 C.R.base.care.exact(request,'parent_type parent_id available_by period_start period_end')
 original=C.parent(path,request['parent_type'],request['parent_id']);ids=population(original,request['parent_type']);raw=received(path,ids,request['available_by'])
 if set(raw['patients'])!=set(ids):raise ValueError('Exact selected received population required')
 reports=[assess(pid,raw['patients'][pid],request['available_by'],request['period_start'],request['period_end']) for pid in ids]
 row={'schema':VERSION,'implementation':identity(),'request':dict(request),'parent_id':original['id'],'parent_type':request['parent_type'],'parent_hash':digest(original),'buyer_id':original['buyer_id'],'question':original['question'],'selected_task_ids':original['selected_task_ids'],'patient_ids':ids,
  'source_digest':raw['source_digest'],'snapshot':raw['snapshot'],'reports':reports,'limits':LIMITS,'tasks_changed':0,'provider_calls':0,'external_actions':0,'duration_assumptions_changed':False}
 if C.parent(path,request['parent_type'],original['id'])!=original or received(path,ids,request['available_by'])!=raw:raise ValueError('Work or received sources changed during context assembly')
 row['id']=digest(row);return row

def database(path):
 db=sqlite3.connect(Path(path)/'operational-context.sqlite3',timeout=30);db.execute('CREATE TABLE IF NOT EXISTS contexts(id TEXT PRIMARY KEY,parent_type TEXT,parent TEXT,payload TEXT)')
 for op in ('UPDATE','DELETE'):db.execute(f"CREATE TRIGGER IF NOT EXISTS contexts_{op} BEFORE {op} ON contexts BEGIN SELECT RAISE(ABORT,'Operational context is immutable'); END")
 return db

def preview(path,request):return snapshot(path,request)
def prepare(path,request):
 C.R.base.care.exact(request,'parent_type parent_id available_by period_start period_end preview_id');selection={k:v for k,v in request.items() if k!='preview_id'};row=snapshot(path,selection)
 if row['id']!=request['preview_id']:raise ValueError('Operational context changed; preview exact current scope again')
 db=database(path)
 try:
  with db:
   db.execute('BEGIN IMMEDIATE')
   if snapshot(path,selection)!=row:raise ValueError('Operational context changed during save')
   db.execute('INSERT OR IGNORE INTO contexts VALUES (?,?,?,?)',(row['id'],row['parent_type'],row['parent_id'],json.dumps(row)))
 finally:db.close()
 return row

def inspect(path,key):
 C.R.base.care.text(key,'Context ID',64);db=database(path)
 try:found=db.execute('SELECT payload FROM contexts WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if not found:raise ValueError('Unknown operational context')
 row=json.loads(found[0])
 if digest({k:v for k,v in row.items() if k!='id'})!=key:raise ValueError('Operational context integrity mismatch')
 reason=None
 try:
  if snapshot(path,row['request'])!=row:reason='Operational source, work or implementation changed'
 except (ValueError,KeyError,FileNotFoundError) as e:reason=str(e)
 return {**row,'current':reason is None,'stale_reason':reason}

def history(path,kind,key):
 if kind not in ('resource','dependency'):raise ValueError('Resource or dependency parent required')
 C.R.base.care.text(key,'Parent ID',64);db=database(path)
 try:ids=[r[0] for r in db.execute('SELECT id FROM contexts WHERE parent_type=? AND parent=? ORDER BY rowid',(kind,key))]
 finally:db.close()
 return {'parent_type':kind,'parent_id':key,'contexts':[inspect(path,i) for i in ids]}
