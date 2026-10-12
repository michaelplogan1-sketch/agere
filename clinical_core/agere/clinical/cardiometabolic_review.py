"""Received cardiometabolic accounts; descriptive arithmetic is not clinical inference."""
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
import hashlib, json, math, re
from ...contracts import digest
from . import domain_review as D, evidence_threads as T, warehouse_interpretation as native, case_passes, record_context

VERSION='cardiometabolic-source-review/1'
PROGRAM='cardiometabolic_source_review'
CONFIG=Path(__file__).resolve().parents[3]/'config/agere/cardiometabolic_sources.json'
BP_FIELDS=('blood_pressure_systolic','blood_pressure_diastolic','units.blood_pressure_systolic','units.blood_pressure_diastolic','measurement','value','unit','measurement_method','device_type','measurement_setting','position','cuff_size','rest_minutes')
FIELDS={'bp':BP_FIELDS,'glycemic':('test','name','value','unit','result_status','specimen_record_id','laboratory_order_record_id','resulted_time'),
 'diabetes_response':('condition','respondent','nausea_or_diarrhea_reported','vomiting_or_inability_to_drink_reported','new_polyuria_or_polydipsia_reported','home_glucose_log_available'),
 'condition_label':('description','icd10','snomed','onset')}
def config():return json.loads(CONFIG.read_text())
def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),config(),D.identity()])
def definition(program):
 if program!=PROGRAM:raise ValueError('Registered cardiometabolic program required')
 return {'id':PROGRAM,'name':'Cardiometabolic measurement and source review','operator':PROGRAM,'version':VERSION,'definition_identity':identity(),'purpose':'Review received measurements and unresolved context before clinical and service decisions.'}
def category(r):
 stream=r['source_table']
 if stream=='vitals' and (any(k in r for k in ('blood_pressure_systolic','blood_pressure_diastolic')) or r.get('measurement') in ('bp_systolic','bp_diastolic','bp_note')):return 'bp'
 if stream=='labs' and r.get('test',r.get('name')) in ('HbA1c','Glucose','glucose'):return 'glycemic'
 if stream=='condition_assessments' and r.get('condition')=='diabetes_treatment_response':return 'diabetes_response'
 if stream=='conditions' and re.fullmatch(r'(I10|I11(?:\.\d+)?|I12(?:\.\d+)?|I13(?:\.\d+)?|E11(?:\.\d+)?|E78(?:\.\d+)?)',str(r.get('icd10',''))):return 'condition_label'
 return None

def fraction(v):return Fraction(str(v))
def number(v):return type(v) in (int,float) and math.isfinite(v)
def exact(v):return {'numerator':v.numerator,'denominator':v.denominator,'display':round(float(v),3)}
def bp_summary(sources,asof):
 observations=[];eligible=[];event_keys=Counter()
 for s in sources:
  f=s['fields'];a=f['blood_pressure_systolic'];b=f['blood_pressure_diastolic'];reason=None
  if a is None or b is None:reason='not_a_complete_pair_in_one_record'
  elif not (number(a) and number(b) and a>b>0):reason='invalid_numeric_pair'
  elif any(f['units.'+k]!='mmHg' for k in ('blood_pressure_systolic','blood_pressure_diastolic')):reason='units_not_explicit_mmHg'
  elif not D.dated(s['source_date'],asof):reason='source_event_date_unknown_or_unusable'
  key=(s['encounter_id'],s['event_time']) if s['encounter_id'] and s['event_time'] else None
  if reason is None and key:event_keys[key]+=1
  observations.append({'record_id':s['record_id'],'source_date':s['source_date'],'systolic':a,'diastolic':b,'excluded_reason':reason,'event_key':key})
 for o in observations:
  if o['excluded_reason'] is None and o['event_key'] and event_keys[o['event_key']]>1:o['excluded_reason']='multiple_source_records_for_same_encounter_time'
  if o['excluded_reason'] is None:eligible.append(o)
  del o['event_key']
 days=defaultdict(list)
 for o in eligible:days[o['source_date']].append(o)
 daily=[]
 for day,rr in sorted(days.items()):
  daily.append({'source_date':day,'record_ids':[r['record_id'] for r in rr],'record_count':len(rr),'mean_systolic':exact(sum((fraction(r['systolic']) for r in rr),Fraction())/len(rr)),
   'mean_diastolic':exact(sum((fraction(r['diastolic']) for r in rr),Fraction())/len(rr)),
   'minimum_systolic':min(r['systolic'] for r in rr),'maximum_systolic':max(r['systolic'] for r in rr)})
 delta=None
 if len(daily)>=2:
  prev,last=daily[-2:];delta={'from_date':prev['source_date'],'to_date':last['source_date'],'elapsed_days':(__import__('datetime').date.fromisoformat(last['source_date'])-__import__('datetime').date.fromisoformat(prev['source_date'])).days}
  for k in ('systolic','diastolic'):
   x,y=prev['mean_'+k],last['mean_'+k];delta[k]=exact(Fraction(y['numerator'],y['denominator'])-Fraction(x['numerator'],x['denominator']))
 return {'observations':observations,'daily_summaries':daily,'latest_dated_daily_summary':daily[-1] if daily else None,'difference_between_last_two_observed_days':delta,
  'counts':{'received_bp_records':len(sources),'included_records':len(eligible),'observed_days':len(daily),'excluded_by_reason':dict(Counter(o['excluded_reason'] for o in observations if o['excluded_reason']))},
  'method':'Exact arithmetic mean of explicit mmHg pairs within each received event date; raw individual records are the baseline. No pooling across dates, pairing separate records, receipt-date substitution or imputation.',
  'assumptions':['Record identity is not proof of an independent measurement; same encounter/time duplicates are excluded pending reconciliation.','Unknown device, technique, setting and rest can make observations incomparable. Missing encounters/times leave duplicate risk unresolved.','A numerical difference is descriptive and is not a treatment response, disease classification, forecast or clinically meaningful change.'],
  'diagnosis_established':False,'treatment_target_inferred':False}
def assess(pid,records,asof):
 sources=[]
 if len({r['record_id'] for r in records})!=len(records):raise ValueError('Distinct source records required')
 for r in sorted(records,key=lambda r:r['record_id']):
  cat=category(r)
  if not cat or r['patient_id']!=pid or r.get('synthetic') is not True or r.get('identity_status')!='matched' or not native.visible(r,asof):raise ValueError('Visible matched same-patient synthetic source required')
  s=D.project(r,{'id':cat,'fields':FIELDS[cat]});s['event_time']=r.get('event_time');s['source_system']=r.get('source_system');sources.append(s)
 accounts={cat:D.latest(sources,{'id':cat,'fields':FIELDS[cat]},asof) for cat in ('glycemic','diabetes_response')}
 bp=bp_summary([s for s in sources if s['selector']=='bp'],asof)
 selected={s['record_id'] for s in sources if s['selector']=='condition_label'}
 for a in accounts.values():selected.update(a['record_ids'])
 for day in bp['daily_summaries'][-2:]:selected.update(day['record_ids'])
 questions=[dict(q,status='unresolved') for q in config()['questions']]
 return {'patient_id':pid,'sources':sources,'record_ids':[s['record_id'] for s in sources],'source_review_candidate':bool(sources),'status':'received_cardiometabolic_sources_for_review' if sources else 'no_selected_sources',
  'bp':bp,'latest_source_accounts':accounts,'questions':questions,'suggested_record_ids':sorted(selected),
  'unknowns':[q['question'] for q in questions],'clinical_state_established':False,'clinical_eligibility_established':False,'clinical_execution_authorized':False}

def load_rows(path,patients=None):
 db,meta=T.connect(path)
 try:
  db.execute('BEGIN');universe={r[0] for r in db.execute('SELECT id FROM patients')};ids=sorted(universe if patients is None else set(patients))
  if not set(ids)<=universe:raise ValueError('Unknown source-review patient')
  groups=defaultdict(list)
  # Patient index avoids unrelated records for the usual one-patient review.
  for pid in ids:
   for raw, in db.execute("SELECT payload FROM records WHERE patient=? AND stream IN ('vitals','labs','conditions','condition_assessments') ORDER BY id",(pid,)):
    r=json.loads(raw)
    if category(r):groups[pid].append(r)
  return meta,[assess(pid,groups[pid],meta['snapshot']['as_of']) for pid in ids]
 finally:db.close()
def rows(path,spec,patients=None):
 if spec!=definition(PROGRAM):raise ValueError('Cardiometabolic definition changed')
 meta,rr=load_rows(path,patients)
 return {r['patient_id']:json.dumps({'synthetic':True,'patient_id':r['patient_id'],'source_run':meta['source_run'],'operator':PROGRAM,'adapter_identity':identity(),'cardiometabolic_source_review':r,
  'workflow':{'as_of':meta['snapshot']['as_of'],'review_candidate':r['source_review_candidate'],'status':r['status'],'missing_information':r['unknowns'],'clinical_eligibility_established':False,'authority':'received_source_review_only'}},sort_keys=True,allow_nan=False) for r in rr if r['source_review_candidate']}
def build(path,patient):
 if not isinstance(patient,str) or not patient or len(patient)>200:raise ValueError('Explicit bounded patient ID required')
 meta,rr=load_rows(path,[patient]);cfg=config();r={'schema':VERSION,'synthetic':True,'code_identity':identity(),'patient_id':patient,'source':meta,'review':rr[0],'guidance':cfg['guidance'],'checked_on':cfg['checked_on'],'question':cfg['question'],'limits':cfg['limits'],'clinical_quality_validated':False,'new_paid_api_calls':0,'external_actions':0};r['id']=digest(r);return r
def location(path,key):
 if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact cardiometabolic review ID required')
 return Path(path)/'cardiometabolic-review'/(key+'.json')
def prepare(path,request):
 if not isinstance(request,dict) or set(request)!={'patient_id'}:raise ValueError('Exact source-review patient required')
 r=build(path,request['patient_id']);p=location(path,r['id'])
 if not p.exists():case_passes.atomic_write(p,r)
 return r
def inspect(path,key):
 p=location(path,key)
 if not p.exists():raise ValueError('Unknown cardiometabolic review')
 r=json.loads(p.read_text())
 if r.get('id')!=key or digest({k:v for k,v in r.items() if k!='id'})!=key:raise ValueError('Review integrity failed')
 if build(path,r['patient_id'])['id']!=key:raise ValueError('Source or implementation changed; prepare current review')
 return r
def prepare_work(path,request):
 from . import program_work
 if not isinstance(request,dict) or set(request)!={'review_id'}:raise ValueError('Exact source review required')
 r=inspect(path,request['review_id']);p=program_work.prepare(path,PROGRAM,[r['patient_id']],input_membership={'review_id':r['id'],'selection_authority':'explicit_source_review_not_clinical_eligibility'});inspect(path,r['id'])
 return {**p,'domain':'cardiometabolic','source_review_id':r['id']}
def context(path,request):
 if not isinstance(request,dict) or set(request)!={'review_id','record_ids'}:raise ValueError('Exact review and selected records required')
 r=inspect(path,request['review_id']);ids=request['record_ids']
 if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)) or not set(ids)<=set(r['review']['record_ids']):raise ValueError('Distinct same-review source records required')
 ctx=record_context.create(path,r['patient_id'],r['question'],sorted(ids));inspect(path,r['id'])
 return {'review_id':r['id'],'context':ctx,'selected_record_ids':sorted(ids),'records_outside_context':len(r['review']['record_ids'])-len(ids),'guidance_in_model_input':False,'new_paid_api_calls':0}
