"""Documented record clocks and observation-process alternatives on graph populations.

Reuse the existing descriptive source partitioner. No clinical event deduplication,
feed outage inference, calibrated latent state or provider-performance ranking.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib,json,sqlite3,zlib
from ...contracts import digest
from . import population_discovery as D, evidence_threads, question_decision as Q

VERSION='graph-observation-process/1'
OPERATION='observation_process'
REQUEST='graph-observation-request/1'
MAX_RECORDS=100000
MAX_GROUPS=32
QUESTIONS=[
 {'id':'feed_history','question':'What source delivery, outage and backfill logs cover this stream and observation window?',
  'effect':'Could distinguish delayed or missing delivery from a change in documented activity; no-record days alone cannot do so.'},
 {'id':'record_lineage','question':'Which received records amend, replace or copy earlier records, and which describe separate events?',
  'effect':'Could distinguish changed documentation and repeated accounts from separate care; exact text or a pointer alone does not establish duplicate care.'},
 {'id':'independent_events','question':'What independent event ascertainment and source coverage support this population and period?',
  'effect':'Could support an event denominator and distinguish capture changes from care changes; source counts alone cannot establish either.'}]


def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),D.identity()])


def validate(p,asof):
    Q.care.exact(p,'stream months available_by')
    if not isinstance(p['stream'],str) or not 1<=len(p['stream'])<=128:raise ValueError('One exact received stream required')
    if type(p['months']) is not int or not 2<=p['months']<=24:raise ValueError('Two to 24 calendar months required')
    if D.day(p['available_by']) is None or p['available_by']>asof:raise ValueError('Exact receipt cutoff no later than source snapshot required')
    return p


def storage(path):
    db=sqlite3.connect(Path(path)/'graph-observation.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,payload BLOB NOT NULL)')
    return db


def prepare(path,parameters):
    db,meta=evidence_threads.connect(path);p=validate(parameters,meta['snapshot']['as_of']);records=[];by_patient=defaultdict(list)
    try:
        db.execute('BEGIN')
        if not db.execute('SELECT 1 FROM records WHERE stream=? LIMIT 1',(p['stream'],)).fetchone():raise ValueError('Unknown received observation stream')
        roster=sorted(r[0] for r in db.execute('SELECT id FROM patients'));people=set(roster)
        for rid,pid,raw in db.execute('SELECT id,patient,payload FROM records WHERE stream=? ORDER BY id',(p['stream'],)):
            r=json.loads(raw)
            if (r.get('record_id')!=rid or r.get('patient_id')!=pid or pid not in people or r.get('source_table')!=p['stream']
                    or r.get('synthetic') is not True or r.get('identity_status')!='matched'):
                raise ValueError('Exact matched same-patient synthetic received sources required')
            records.append(r);by_patient[pid].append([rid,digest(r)])
            if len(records)>MAX_RECORDS:raise ValueError('Stream exceeds bounded 100000-record investigation; no sampling or truncation')
    finally:db.close()
    hashes={pid:digest(by_patient[pid]) for pid in roster}
    binding={'implementation':identity(),'parameters':p,'snapshot':meta['snapshot'],'roster_digest':digest(roster),'patient_sources':hashes}
    return {**binding,'identity':digest(binding),'records':records}


def receipt_id(prep,pid):
    if pid not in prep['patient_sources']:raise ValueError('Observation member outside received roster')
    return digest([VERSION,prep['implementation'],prep['parameters'],pid,prep['patient_sources'][pid]])


def clocks(records,selected,p):
    r=D.analyze(records,selected,p['available_by'],p['months']);labels=r['months'];start=r['window_start'];end=p['available_by']
    byid={x['record_id']:x for x in records};dated=Counter();exclusions=Counter();corrections=[];sources=defaultdict(list)
    for x in records:
        arrival=D.day(x.get('available_date'));event=D.day(x.get('event_date'))
        if arrival is None or arrival>end:continue
        source=(x.get('source_system'),x.get('organization_id'))
        if any(v is not None and not isinstance(v,str) for v in source):raise ValueError('Literal source organization identifiers required')
        sources[source].append(x)
        if event is None:exclusions['unknown_event_date']+=1
        elif event>end:exclusions['event_after_cutoff']+=1
        elif event<start:exclusions['event_before_window']+=1
        else:dated[event[:7]]+=1
        target=x.get('supersedes_record_id')
        if target:
            prior=byid.get(target)
            reason=('target_not_in_scoped_stream' if prior is None else 'cross_patient_pointer' if prior['patient_id']!=x['patient_id'] else
                'cross_source_pointer' if (prior.get('source_system'),prior.get('organization_id'))!=source else
                'target_not_visible_at_cutoff' if not D.day(prior.get('available_date')) or prior['available_date']>end else 'same_patient_source_pointer')
            corrections.append({'record_id':x['record_id'],'record_hash':digest(x),'target_record_id':target,
                'target_hash':digest(prior) if prior else None,'status':reason,'replacement_applied':False})
    if len(sources)>MAX_GROUPS:raise ValueError('More than 32 source groups; no top-source sampling')
    groups=[]
    for (system,org),items in sorted(sources.items(),key=lambda x:str(x[0])):
        g=D.analyze(items,selected,end,p['months'])
        groups.append({'source_system':system,'organization_id':org,'denominators':g['denominators'],
            'arrival_counts':g['arrival_comparison']['counts'],'same_arrivals_source_date_counts':g['source_date_sensitivity']['counts'],
            'lag_days_median':g['source_date_sensitivity']['lag_days_median'],'arrival_day_counts':g['arrival_day_counts']})
    r.update(documented_date_comparison={**D.compare([dated[m] for m in labels],r['calendar_days']),
        'excluded_visible_records':dict(exclusions),
        'meaning':'Record source dates among all records received by cutoff, including arrivals before the window. Not unique clinical events or verified admissions.'},
        source_groups=groups,correction_pointers=corrections,
        source_group_meaning='Literal source-system/organization strata. No patient attribution, provider quality ranking or verified source coverage.',
        evidence_questions=QUESTIONS,
        alternatives=[{'hypothesis':'Delivery delay or batching','evidence':r['source_date_sensitivity'],'needed_question':'feed_history'},
          {'hypothesis':'Changed documentation or repeated accounts','evidence':{'identical_text_records':sum(r['repeat_sensitivity']['repeat_counts']),'correction_pointer_count':len(corrections)},'needed_question':'record_lineage'},
          {'hypothesis':'Changed care or changed capture','evidence':{'members_without_in_window_arrivals':r['denominators']['members_without_included_records']},'needed_question':'independent_events'}],
        clinical_change_established=False,feed_outage_established=False,provider_performance_established=False,
        causal_effect=None,clinical_execution_authorized=False,new_paid_api_calls=0,
        interpretation='Compare record clocks before interpreting a pattern. A change in source arrivals can coexist with unchanged dated records. These observations cannot identify a latent clinical state or an unobserved feed.',
        search_scope={'streams':1,'windows':1,'partition_candidates':2**(p['months']-1),'penalty':6.0,'p_value':None,'qualification':'Exploratory in-sample descriptive partitions; source stratification is not independent replication.'})
    return r


def execute(path,patients,parameters,prep=None):
    prep=prep or prepare(path,parameters);ids=sorted(patients)
    if len(ids)!=len(set(ids)) or set(ids)-set(prep['patient_sources']):raise ValueError('Exact distinct selected population required')
    if prep['parameters']!=parameters:raise ValueError('Observation preparation scope differs')
    selected=set(ids);records=[r for r in prep['records'] if r['patient_id'] in selected]
    result=clocks(records,ids,parameters);rows=[]
    sources={r['record_id']:r for r in records}
    for member in result.pop('members'):
        for ref in member['records']:
            raw=sources[ref['record_id']]
            ref.update(source_system=raw.get('source_system'),organization_id=raw.get('organization_id'),supersedes_record_id=raw.get('supersedes_record_id'))
        rows.append({'schema':VERSION,'id':receipt_id(prep,member['patient_id']),'parameters':parameters,
            **member,'status':'received_arrivals' if member['included_records'] else 'no_arrivals_in_window',
            'source_coverage':'unknown','clinical_state':'unassessed','interpretation':result['interpretation']})
    if prepare(path,parameters)['identity']!=prep['identity']:raise ValueError('Sources changed during observation analysis; no receipts saved')
    with storage(path) as db:
        db.executemany('INSERT OR IGNORE INTO receipts VALUES (?,?)',[(r['id'],zlib.compress(json.dumps(r,allow_nan=False).encode())) for r in rows])
    return {'schema':VERSION,'patients_evaluated':len(ids),'parameters':parameters,**result}


def request(retriever,body):
    from . import graph_decision as G
    Q.care.exact(body,'decision_id node_id question_id owner actor')
    d=Q.inspect(retriever,body['decision_id'])
    if d['schema']!=G.VERSION:raise ValueError('Current original graph population required')
    n=next((n for n in d['nodes'] if n['node']['id']==body['node_id'] and n['node']['operation']==OPERATION),None)
    q=next((q for q in QUESTIONS if q['id']==body['question_id']),None)
    if not n or not q:raise ValueError('Exact observation node and evidence question required')
    return Q.save_followup(retriever.path,{'schema':REQUEST,'implementation':identity(),'decision_id':d['id'],
        'question':d['question'],'population':d['population'],'node_id':body['node_id'],'node_digest':digest(n),
        'parameters':n['node']['parameters'],'evidence_question':q,
        'owner':Q.care.text(body['owner'],'Declared internal evidence owner',120),
        'actor':Q.care.text(body['actor'],'Evidence request actor',120),
        'status':'internal_population_evidence_request','identity_verified':False,'request_sent':False,
        'task_state_changed':False,'clinical_execution_authorized':False,'new_paid_api_calls':0})


def open_request(retriever,body):
    Q.care.exact(body,'request_id');r=Q.saved_followup(retriever.path,body['request_id'])
    if r.get('schema')!=REQUEST:raise ValueError('Saved population evidence request required')
    try:
        if r['implementation']!=identity():raise ValueError('Observation method changed')
        d=Q.inspect(retriever,r['decision_id']);n=next(n for n in d['nodes'] if n['node']['id']==r['node_id'])
        if digest(n)!=r['node_digest']:raise ValueError('Observation evidence changed')
        current=True;reason=None
    except (ValueError,KeyError,StopIteration) as e:current=False;reason=str(e)
    return {'request':r,'current':current,'stale_reason':reason,'new_paid_api_calls':0}
