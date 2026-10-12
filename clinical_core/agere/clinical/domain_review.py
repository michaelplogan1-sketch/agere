"""Bounded domain source accounts with explicit diagnostic and action limits."""
from collections import Counter,defaultdict
from datetime import date
from pathlib import Path
import hashlib,json,re,math
from ...contracts import digest
from . import evidence_threads as threads, warehouse_interpretation as native, record_context, case_passes

VERSION='domain-source-review/1'
CATALOG=Path(__file__).resolve().parents[3]/'config/agere/domain_review_sources.json'
def catalog():return json.loads(CATALOG.read_text())
def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),catalog(),native.identity()])
def definition(domain):
    if not isinstance(domain,str) or domain not in catalog()['domains']:raise ValueError('Registered source-review domain required')
    return catalog()['domains'][domain]
def field(row,path):
    value=row
    for part in path.split('.'):
        if not isinstance(value,dict):return None
        value=value.get(part)
    # Whitelisted scalar fields only; unrecognized nested payloads never become evidence.
    if type(value) is float and not math.isfinite(value):raise ValueError('Finite domain source values required')
    return value if type(value) in (str,int,float,bool) or value is None else None
def dated(value,asof):
    try:return isinstance(value,str) and date.fromisoformat(value).isoformat()==value and value<=asof
    except ValueError:return False
def select(row,spec):
    return next((s for s in spec['selectors'] if row['source_table']==s['stream'] and field(row,s['field']) in s['values']),None)
def project(row,selector):
    return {'record_id':row['record_id'],'record_hash':digest(row),'selector':selector['id'],
        'source_table':row['source_table'],'source_date':row.get('event_date'),'recorded_date':row.get('recorded_date'),
        'available_date':row['available_date'],'encounter_id':row.get('encounter_id'),
        'fields':{k:field(row,k) for k in selector['fields']}}
def latest(rows,selector,asof):
    chosen=[r for r in rows if r['selector']==selector['id'] and dated(r['source_date'],asof)]
    if not chosen:return {'date':None,'record_ids':[],'fields':{},'status':'no_usable_dated_source'}
    day=max(r['source_date'] for r in chosen);chosen=[r for r in chosen if r['source_date']==day];fields={}
    for key in selector['fields']:
        values=[r['fields'][key] for r in chosen];conflict=len({digest(v) for v in values})>1
        fields[key]={'state':'conflicting' if conflict else 'not_recorded' if values[0] is None else 'literal_report',
            'value':None if conflict else values[0],
            'evidence':[{'record_id':r['record_id'],'field':key,'value':r['fields'][key]} for r in chosen]}
    return {'date':day,'record_ids':[r['record_id'] for r in chosen],'fields':fields,'status':'dated_source_accounts'}
def assess(domain,patient,records,asof):
    spec=definition(domain);rows=[]
    if len({r['record_id'] for r in records})!=len(records):raise ValueError('Duplicate domain source identity')
    for r in sorted(records,key=lambda r:r['record_id']):
        selector=select(r,spec)
        if selector is None or r['patient_id']!=patient or r.get('synthetic') is not True or r.get('identity_status')!='matched' or not native.visible(r,asof):
            raise ValueError('Visible matched same-patient synthetic domain sources required')
        rows.append(project(r,selector))
    accounts={s['id']:latest(rows,s,asof) for s in spec['selectors']};issues=[]
    def issue(kind,account,key,reason):
        v=accounts[account]['fields'].get(key,{})
        issues.append({'kind':kind,'reason':reason,'source_date':accounts[account]['date'],'evidence':v.get('evidence',[]),'authority':'source_review_question_only'})
    if domain=='respiratory':
        asthma=accounts['asthma_accounts']['fields'];spiro=accounts['spirometry']['fields']
        if asthma.get('reliever_directions_confirmed',{}).get('value') is False:
            issue('directions_unconfirmed_in_source','asthma_accounts','reliever_directions_confirmed','This dated source records reliever directions as unconfirmed. Reconcile current directions and actual use; no treatment change inferred.')
        if spiro.get('measurements.postbronchodilator_test_performed',{}).get('value') is False:
            issue('postbronchodilator_test_not_performed_in_source','spirometry','measurements.postbronchodilator_test_performed','This report records no postbronchodilator test; do not infer COPD or a reversibility result from the prebronchodilator ratio.')
        for key in ('measurements.percent_predicted','measurements.lower_limit_of_normal'):
            if spiro and spiro.get(key,{}).get('state')=='not_recorded':issue('reference_value_not_supplied','spirometry',key,'The selected report does not supply '+key+'; do not invent a severity classification.')
    unknowns=list(spec['unresolved'])
    for key,a in accounts.items():
        if not a['record_ids']:unknowns.append('No usable dated '+key+' source in this selection; record/arrival dates are not substituted for clinical dates.')
        for name,v in a['fields'].items():
            if v['state']=='conflicting':unknowns.append('Same-date conflicting '+key+'.'+name+' remains unresolved.')
    # Labels, including undated labels, nominate source review only. They do not establish disease or a treatment gap.
    selected_ids=sorted({r['record_id'] for r in rows if r['selector']=='condition_labels'}|{rid for a in accounts.values() for rid in a['record_ids']})
    return {'patient_id':patient,'domain':domain,'source_review_candidate':bool(rows),
        'status':'received_source_accounts_for_review' if rows else 'no_selected_source_accounts',
        'sources':rows,'latest_source_accounts':accounts,'issues':issues,'unknowns':unknowns,
        'record_ids':[r['record_id'] for r in rows],'suggested_record_ids':selected_ids,
        'records_outside_suggested_context':len(rows)-len(selected_ids),'review_topics':spec['review_topics'],
        'clinical_state_established':False,'clinical_eligibility_established':False,'clinical_execution_authorized':False}
def build(path,domain):
    spec=definition(domain);db,meta=threads.connect(path)
    try:
        db.execute('BEGIN');patients=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')];universe=set(patients);groups=defaultdict(list)
        for selector in spec['selectors']:
            placeholders=','.join('?' for _ in selector['values'])
            for pid,raw in db.execute('SELECT patient,payload FROM records WHERE stream=? AND json_extract(payload,?) IN ('+placeholders+') ORDER BY patient,id',[selector['stream'],'$.'+selector['field'],*selector['values']]):
                if pid not in universe:raise ValueError('Domain source patient absent from roster')
                groups[pid].append(json.loads(raw))
        rows=[assess(domain,p,groups[p],meta['snapshot']['as_of']) for p in patients];cat=catalog()
        r={'schema':VERSION,'synthetic':True,'domain':domain,'definition':spec,'code_identity':identity(),
            'snapshot':meta['snapshot'],'source_digest':meta['source_digest'],'patients':rows,
            'guidance':[cat['guidance'][key] for key in spec['sources']],'guidance_checked_on':cat['checked_on'],'limits':cat['limits'],
            'counts':{'population':len(rows),'source_review_candidates':sum(bool(r['sources']) for r in rows),
                'no_selected_source_accounts':sum(not r['sources'] for r in rows),'records':sum(len(r['sources']) for r in rows),
                'records_without_usable_source_date':sum(not dated(s['source_date'],meta['snapshot']['as_of']) for r in rows for s in r['sources']),
                'issues':dict(Counter(i['kind'] for r in rows for i in r['issues']))},
            'clinical_quality_validated':False,'new_paid_api_calls':0,'external_actions':0}
        r['id']=digest(r);return r
    finally:db.close()
def location(path,key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact domain-review receipt ID required')
    return Path(path)/'domain-review'/(key+'.json')
def prepare(path,domain):
    r=build(path,domain);p=location(path,r['id'])
    if not p.exists():case_passes.atomic_write(p,r)
    return r
def inspect(path,key):
    p=location(path,key)
    if not p.exists():raise ValueError('Unknown domain-review receipt')
    r=json.loads(p.read_text())
    if r.get('id')!=key or digest({k:v for k,v in r.items() if k!='id'})!=key:raise ValueError('Domain-review receipt integrity failed')
    if build(path,r['domain'])['id']!=key:raise ValueError('Domain source or configuration changed; prepare a current review')
    return r
def page(path,domain,key='',patient='',offset=0):
    definition(domain)
    if type(offset) is not int or offset<0:raise ValueError('Nonnegative page offset required')
    r=inspect(path,key) if key else prepare(path,domain)
    if r['domain']!=domain:raise ValueError('Domain-review mismatch')
    rows=[p for p in r['patients'] if p['patient_id']==patient] if patient else [p for p in r['patients'] if p['source_review_candidate']]
    if patient and not rows:raise ValueError('Patient absent from domain population')
    return {**r,'patients':rows[offset:offset+10],'offset':offset,'limit':10,'matching_patients':len(rows)}
def context(path,request):
    if not isinstance(request,dict) or set(request)!={'review_id','patient_id','record_ids'}:raise ValueError('Exact domain receipt, patient and record selection required')
    r=inspect(path,request['review_id']);row=next((p for p in r['patients'] if p['patient_id']==request['patient_id']),None);ids=request['record_ids']
    if row is None or not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids) or not set(ids)<=set(row['record_ids']):raise ValueError('Distinct received same-patient domain source selection required')
    ctx=record_context.create(path,row['patient_id'],r['definition']['question'],sorted(ids))
    inspect(path,r['id'])
    return {'review_id':r['id'],'domain':r['domain'],'patient_id':row['patient_id'],'source_review':row,'context':ctx,
        'selected_record_ids':sorted(ids),'domain_records_outside_context':len(row['record_ids'])-len(ids),
        'guidance_in_model_input':False,'authority':'source_review_only','new_paid_api_calls':0}
