"""Dated functional/support source review across a population, before service selection."""
from collections import Counter,defaultdict
from datetime import date
from pathlib import Path
import hashlib,json,math,re
from ...contracts import digest
from . import evidence_threads as threads, record_context, warehouse_interpretation as native, case_passes

VERSION='functional-support-source-review/1'
CATALOG=Path(__file__).resolve().parents[3]/'config/agere/functional_review_sources.json'
STREAMS=('functional_assessments','functional_mobility_exams','care_management_contacts')
FIELDS={
 'functional_assessments':('respondent','falls_past_year_reported','help_with_appointments_or_medications_reported','bathing_help_reported','walking_aid_reported'),
 'functional_mobility_exams':('age_reported','timed_up_and_go_seconds','test_protocol','walking_support_observed','transfer_assistance_observed','skilled_need_assessed','homebound_assessed','homebound_basis','leaving_home_requires_another_person_reported','leaving_home_taxing_effort_reported'),
 'care_management_contacts':('basis_record_id','purpose','respondent','status','text')}
QUESTION='What do the received functional examination, daily-function interview and care-management accounts establish about mobility and support needs, what remains unresolved for home nursing or therapy, and which source-review steps are justified? Separate dated clinical accounts, received but unread records, current patient preferences, payer eligibility and execution authority.'


def guidance():return json.loads(CATALOG.read_text())
def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),guidance(),native.identity()])
def finite(v,low,high):return type(v) in (int,float) and math.isfinite(v) and low<=v<=high
def day(value):
    try:return isinstance(value,str) and date.fromisoformat(value).isoformat()==value
    except ValueError:return False


def latest(records,stream,asof):
    found=[r for r in records if r['source_table']==stream]
    valid=[r for r in found if day(r.get('event_date')) and r['event_date']<=asof]
    if not valid:return {'date':None,'record_ids':[],'fields':{},'status':'no_usable_dated_source','undated_or_future_records':[r['record_id'] for r in found]}
    when=max(r['event_date'] for r in valid);chosen=[r for r in valid if r['event_date']==when];fields={}
    for field in FIELDS[stream]:
        values=[r.get(field) for r in chosen];unique={digest(v) for v in values}
        fields[field]={'state':'conflicting' if len(unique)>1 else 'not_recorded' if values[0] is None else 'literal_report',
                       'value':values[0] if len(unique)==1 else None,
                       'evidence':[{'record_id':r['record_id'],'field':field,'value':r.get(field)} for r in chosen]}
    return {'date':when,'record_ids':[r['record_id'] for r in chosen],'fields':fields,'status':'dated_source_accounts',
            'undated_or_future_records':[r['record_id'] for r in found if r not in valid]}


def assess(patient,records,asof,config=None):
    config=config or guidance();rows=sorted(records,key=lambda r:r['record_id'])
    if len({r['record_id'] for r in rows})!=len(rows):raise ValueError('Duplicate functional source identity')
    if any(r['patient_id']!=patient or r.get('synthetic') is not True or r.get('identity_status')!='matched'
           or r['source_table'] not in STREAMS or not native.visible(r,asof) for r in rows):
        raise ValueError('Visible matched same-patient synthetic functional sources required')
    dated={s:latest(rows,s,asof) for s in STREAMS};signals=[];unresolved=[]
    def value(stream,field):return dated[stream]['fields'].get(field,{}).get('value')
    def signal(kind,stream,fields,reason,source):
        signals.append({'kind':kind,'source_date':dated[stream]['date'],'reason':reason,'guidance_id':source,
            'evidence':[e for f in fields for e in dated[stream]['fields'].get(f,{}).get('evidence',[])],
            'authority':'dated_source_review_signal_not_current_clinical_state'})
    falls=value('functional_assessments','falls_past_year_reported')
    if type(falls) is int and falls>0:signal('reported_fall_history','functional_assessments',['falls_past_year_reported'],'Latest received interview reports one or more prior-year falls. Reconcile its date and subsequent care.','source_report_only')
    support=value('functional_assessments','help_with_appointments_or_medications_reported')
    if support is True:signal('reported_support_need','functional_assessments',['help_with_appointments_or_medications_reported'],'Latest received interview reports appointment or medication support; current need and preferences require review.','source_report_only')
    age=value('functional_mobility_exams','age_reported');seconds=value('functional_mobility_exams','timed_up_and_go_seconds');protocol=value('functional_mobility_exams','test_protocol')
    rules=config['review_configuration']
    if dated['functional_mobility_exams']['record_ids']:
        compatible=finite(age,rules['minimum_reported_age_for_tug_screen'],130) and finite(seconds,.001,600) and protocol in rules['accepted_source_protocols']
        if compatible and seconds>=rules['tug_seconds_threshold_inclusive']:
            signal('tug_threshold_review','functional_mobility_exams',['age_reported','timed_up_and_go_seconds','test_protocol'],'Recorded TUG meets the inclusive CDC screening threshold in this configured older-adult source review; assess test quality and clinical context.','cdc_tug')
        elif not compatible:unresolved.append('Age, numeric test duration or protocol is missing, conflicting or outside this bounded TUG screen; no threshold conclusion.')
    for stream,row in dated.items():
        if row['status']=='no_usable_dated_source':unresolved.append('No usable dated source in '+stream+'; care status is unknown.')
        for field,v in row['fields'].items():
            if v['state']=='conflicting':unresolved.append('Same-date conflicting '+stream+'.'+field+' remains unresolved.')
        if row['undated_or_future_records']:unresolved.append('Undated or future-source '+stream+' records are retained but not used for dated signals.')
    if support is None:unresolved.append('Current appointment/medication support cannot be resolved from the latest interview field.')
    sources=[{'record_id':r['record_id'],'record_hash':digest(r),'source_table':r['source_table'],
        'source_date':r.get('event_date'),'available_date':r['available_date'],
        'fields':{k:r.get(k) for k in FIELDS[r['source_table']]}} for r in rows]
    return {'patient_id':patient,'source_review_candidate':bool(signals),'status':'source_review_signal' if signals else 'no_defined_signal_in_selected_sources' if rows else 'no_selected_sources_received',
        'signals':signals,'latest_source_accounts':dated,'sources':sources,'record_ids':[r['record_id'] for r in rows],
        'unknowns':unresolved,'clinical_state_established':False,'home_health_eligibility':'unresolved',
        'clinical_execution_authorized':False,'suggested_review_topics':sorted({'functional_recovery' for s in signals}|({'medication_review'} if support is True else set())),
        'topic_authority':'source_review_topics_not_validated_service_indications'}


def build(path):
    db,meta=threads.connect(path)
    try:
        db.execute('BEGIN');patients=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')]
        grouped=defaultdict(list)
        for pid,raw in db.execute('SELECT patient,payload FROM records WHERE stream IN (?,?,?) ORDER BY patient,id',STREAMS):
            if pid not in patients:raise ValueError('Functional source patient is absent from roster')
            grouped[pid].append(json.loads(raw))
        asof=meta['snapshot']['as_of'];config=guidance();rows=[assess(p,grouped[p],asof,config) for p in patients]
        result={'schema':VERSION,'synthetic':True,'snapshot':meta['snapshot'],'warehouse_source_digest':meta['source_digest'],
            'code_identity':identity(),'guidance':config,'source_scope':list(STREAMS),'patients':rows,
            'counts':{'population':len(rows),'patients_with_selected_sources':sum(bool(r['sources']) for r in rows),
                'no_selected_sources_received':sum(not r['sources'] for r in rows),'source_review_candidates':sum(r['source_review_candidate'] for r in rows),
                'records':sum(len(r['sources']) for r in rows),'signals':dict(Counter(s['kind'] for r in rows for s in r['signals']))},
            'clinical_quality_validated':False,'coverage_verified':False,'new_paid_api_calls':0,'external_actions':0}
        result['id']=digest(result);return result
    finally:db.close()


def location(path,key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact functional review receipt ID required')
    return Path(path)/'functional-review'/(key+'.json')


def prepare(path):
    result=build(path);target=location(path,result['id'])
    if not target.exists():case_passes.atomic_write(target,result)
    return result


def inspect(path,key):
    result=json.loads(location(path,key).read_text())
    if result.get('id')!=key or digest({k:v for k,v in result.items() if k!='id'})!=key:raise ValueError('Functional review integrity changed')
    if build(path)['id']!=key:raise ValueError('Functional sources, roster or review definition changed')
    return result


def page(path,key='',patient='',offset=0):
    if type(offset) is not int or offset<0:raise ValueError('Nonnegative review page required')
    result=inspect(path,key) if key else prepare(path)
    rows=result['patients']
    if patient:
        rows=[r for r in rows if r['patient_id']==patient]
        if not rows:raise ValueError('Unknown patient in functional review')
    else:rows=[r for r in rows if r['source_review_candidate']]
    return {**{k:v for k,v in result.items() if k!='patients'},'patients':rows[offset:offset+12],
            'matching_patients':len(rows),'offset':offset,'limit':12,'view':'selected_patient' if patient else 'source_review_candidates'}


def context(path,request):
    if not isinstance(request,dict) or set(request)!={'review_id','patient_id'}:raise ValueError('Exact source review and patient required')
    review=inspect(path,request['review_id']);row=next((r for r in review['patients'] if r['patient_id']==request['patient_id']),None)
    if row is None or not row['record_ids']:raise ValueError('Received functional sources required for context')
    result=record_context.create(path,row['patient_id'],QUESTION,row['record_ids'])
    if inspect(path,review['id'])['id']!=review['id']:raise ValueError('Functional review changed during context preparation')
    return {'review_id':review['id'],'patient_id':row['patient_id'],'source_review':row,'context':result,
            'guidance':review['guidance'],'authority':'source_review_only','new_paid_api_calls':0}
