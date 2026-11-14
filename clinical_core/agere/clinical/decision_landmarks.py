"""Receipt-time decision windows with source-bound, clinically dated endpoints.

Separate from the existing index-day experiment. No underlying clock, original
outcome receipt or fitting artifact is changed to obtain a usable label.
"""
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
import zlib

from ...contracts import digest
from . import outcomes as O, baseline_evidence as B, longitudinal as L
from .clinical_events import iso

VERSION='receipt-decision-landmarks/2'
LIMITS=[
    'Synthetic retrospective receipt-time experiment; no clinical prediction or treatment authority.',
    'The decision day begins after the earliest index receipt day; no within-day ordering is invented.',
    'Only explicit clinical endpoint dates are used. Arrival, source and clinical dates are not interchangeable.',
    'A mature zero means no matching received future endpoint, not no actual care or complete capture.',
    'Missing encounter identity yields provisional links, not verified episodes or a CMS readmission measure.',
    'The current snapshot cannot reconstruct all superseded, quarantined or historically corrected records.',
    'Baseline narrative meanings remain unvalidated machine features. No model is fitted by this operator.',
]


def definitions():
    raw=json.loads(Path(__file__).with_name('landmark_contracts.json').read_text())
    if raw['schema']!='receipt-decision-contracts/1' or raw['authority']!='internal_synthetic_observation_definitions':raise ValueError('Unknown landmark authority')
    result={}
    for spec in raw['contracts']:
        if set(spec)!={'id','version','title','source_contract','decision_rule','endpoint_clock','min_days','max_days','profile','meaning'}:
            raise ValueError('Exact landmark definition required')
        if spec['decision_rule']!='next_calendar_day_after_first_index_receipt' or spec['endpoint_clock']!='clinical':raise ValueError('Explicit landmark clocks required')
        if type(spec['min_days']) is not int or type(spec['max_days']) is not int or not 0<=spec['min_days']<=spec['max_days']<=365:raise ValueError('Bounded landmark window required')
        if not re.fullmatch('[a-z][a-z0-9_]{0,63}',spec['id']) or spec['id'] in result:raise ValueError('Unique landmark ID required')
        O.definition(spec['source_contract']);B.definition(spec['profile'])
        result[spec['id']]={**spec,'hash':digest(spec),'authority':raw['authority']}
    return result


def validate(p,asof):
    if not isinstance(p,dict) or set(p)!={'contract','receipt_from','receipt_through','available_by'}:raise ValueError('Exact receipt-window parameters required')
    if not isinstance(p['contract'],str) or p['contract'] not in definitions():raise ValueError('Unknown landmark contract')
    if any(not iso(p[k]) for k in ('receipt_from','receipt_through','available_by')) or not p['receipt_from']<=p['receipt_through']<=p['available_by']<=asof:
        raise ValueError('Ordered receipt window and availability cutoff required')
    return dict(p)


def code_identity():
    return digest([B.code_identity(),O.code_identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   hashlib.sha256(Path(__file__).with_name('landmark_contracts.json').read_bytes()).hexdigest()])


def prepare(path,p):
    meta=json.loads((Path(path)/'run.json').read_text());p=validate(p,meta['snapshot']['as_of']);spec=definitions()[p['contract']]
    source=O.prepare(path,{'contract':spec['source_contract'],'index_from':p['receipt_from'],
        'index_through':p['receipt_through'],'available_by':p['available_by'],'filter':'all'})
    inputs=B.prepare(path,{'profile':spec['profile']},source)
    base={'code_identity':code_identity(),'parameters':p,'contract_hash':spec['hash'],
          'snapshot':meta['snapshot'],'source_digest':meta['source_digest'],'source_run':meta['source_run']}
    return {'identity':digest([base,source['identity'],inputs['identity']]),'base':base,'contract':spec,
        'source':source,'baseline':inputs,'patients':source['patients']}


def clinical_clock_available(event,cutoff):
    # Native source clocks can depend on an encounter row that arrived later.
    refs=event.get('clinical_date_evidence',[])
    if any(e['patient_id']!=event['patient_id'] for e in refs):raise ValueError('Cross-patient clock dependency')
    if not iso(event.get('clinical_date')):return False
    return all(iso(e.get('arrival_date')) and e['arrival_date']<=cutoff for e in refs)


def evaluate(pid,indices,endpoints,spec,p):
    parent=O.definition(spec['source_contract'])
    if any(e['patient_id']!=pid or e['source']['patient_id']!=pid for e in indices+endpoints):raise ValueError('Cross-patient landmark evidence')
    for event in indices+endpoints:
        clinical_clock_available(event,p['available_by'])  # validate every clinical clock/patient dependency, even an unused copy
    row={'schema':VERSION,'synthetic':True,'patient_id':pid,'contract':spec,'parameters':p,
         'status':'no_received_index','index':None,'decision':None,'window':None,'recorded_label':None,
         'calendar_mature':False,'mature_recorded_label_usable':False,'matches':[],
         'excluded_endpoints':[],'unresolved_endpoints':[],'index_issues':[],
         'evidence':{e['id']:e for e in indices+endpoints},'capture_status':'unknown',
         'clinical_outcome_established':False,'clinical_prediction_authorized':False,'limits':LIMITS}
    unknown=[e['id'] for e in indices if not iso(e.get('arrival_date'))]
    if unknown:row.update(status='unresolved_index_arrival',index_issues=unknown);return row
    candidates=[e for e in indices if p['receipt_from']<=e['arrival_date']<=p['receipt_through']]
    if not candidates:return row
    receipt=min(e['arrival_date'] for e in candidates)
    first=[e for e in candidates if e['arrival_date']==receipt]
    groups=O.deduplicate(first,'clinical_date')
    if len(groups)!=1:row.update(status='ambiguous_first_received_index',index_issues=[e['id'] for e in first]);return row
    clock_witnesses=[e for e in groups[0]['evidence'] if clinical_clock_available(e,receipt)]
    index=min(clock_witnesses,key=lambda e:e['id']) if clock_witnesses else groups[0]['event']
    clinical=index.get('clinical_date')
    row['index']={'event_id':index['id'],'receipt_date':receipt,'clinical_date':clinical,
                  'evidence_ids':[e['id'] for e in groups[0]['evidence']],
                  'clock_available_witness_ids':sorted(e['id'] for e in clock_witnesses)}
    if not clinical_clock_available(index,receipt):row['status']='index_clinical_clock_unavailable_at_receipt';return row
    if clinical>receipt:row['status']='index_clinical_event_after_receipt';return row
    decision=(date.fromisoformat(receipt)+timedelta(days=1)).isoformat()
    end=(date.fromisoformat(decision)+timedelta(days=spec['max_days'])).isoformat()
    start=(date.fromisoformat(decision)+timedelta(days=spec['min_days'])).isoformat()
    row.update(decision={'date':decision,'feature_cutoff':receipt,'clinical_index_date':clinical,
        'receipt_lag_calendar_days':(date.fromisoformat(receipt)-date.fromisoformat(clinical)).days,
        'semantics':'Beginning of next calendar day; an analytical landmark, not a clinical event.'},
        window={'start':start,'end':end,'endpoint_clock':'clinical','available_by':p['available_by']},
        calendar_mature=end<=p['available_by'])
    if decision>p['available_by']:row['status']='decision_after_available_cutoff';return row
    ek=O.encounter_key(index)
    for group in O.deduplicate(endpoints,'clinical_date'):
        ids=[x['id'] for x in group['evidence']];assessments=[]
        for e in group['evidence']:
            reason=unresolved=None
            clinical=e.get('clinical_date');arrival=e.get('arrival_date');other=O.encounter_key(e)
            source_a=tuple(index['source'].get(k) for k in ('source_system','organization_id'))
            source_b=tuple(e['source'].get(k) for k in ('source_system','organization_id'))
            if iso(arrival) and arrival>p['available_by']:reason='not_received_by_evaluation_cutoff'
            elif clinical and clinical<start:reason='clinical_event_before_decision_window'
            elif clinical and clinical>end:reason='clinical_event_after_decision_window'
            elif clinical and clinical>p['available_by']:reason='clinical_event_after_evaluation_cutoff'
            elif iso(arrival) and arrival<=receipt:reason='endpoint_record_already_available_at_decision'
            elif e['id']==index['id']:reason='same_event'
            elif parent['source_scope']=='same_source_organization' and all(source_a+source_b) and source_a!=source_b:reason='different_source_organization'
            elif ek and other and parent['encounter_rule']=='same_when_present' and ek!=other:reason='different_encounter'
            elif ek and other and parent['encounter_rule']=='different_when_present' and ek==other:reason='same_encounter'
            elif not iso(arrival):unresolved='unknown_endpoint_arrival'
            elif not clinical_clock_available(e,p['available_by']):unresolved='endpoint_clinical_clock_unavailable'
            elif parent['source_scope']=='same_source_organization' and not all(source_a+source_b):unresolved='unknown_source_organization'
            assessments.append({'event_id':e['id'],'clinical_date':clinical,'arrival_date':arrival,
                'disposition':'excluded' if reason else 'unresolved' if unresolved else 'qualifying',
                'reason':reason or unresolved,
                'clinical_clock_available_at_decision':clinical_clock_available(e,receipt),
                'clinical_clock_available_at_evaluation':clinical_clock_available(e,p['available_by'])})
        # Novel receipt is a group property. A post-decision copy cannot turn an
        # already-received exact endpoint into a new future observation.
        prior=[a['event_id'] for a in assessments if a['reason']=='endpoint_record_already_available_at_decision']
        if prior:
            for a in assessments:
                if a['disposition'] in ('qualifying','unresolved'):
                    a.update(disposition='excluded',reason='duplicate_of_endpoint_record_available_at_decision',
                             prior_receipt_witness_ids=prior)
        qualifying=[a for a in assessments if a['disposition']=='qualifying']
        witness_ids=sorted(a['event_id'] for a in qualifying)
        if qualifying:
            chosen=min(qualifying,key=lambda a:(a['arrival_date'],a['event_id']))
            e=row['evidence'][chosen['event_id']];other=O.encounter_key(e)
            row['matches'].append({'event_id':e['id'],'clinical_date':e['clinical_date'],'arrival_date':e['arrival_date'],
                'evidence_ids':ids,'qualifying_witness_ids':witness_ids,'copy_assessments':assessments,
                'link':'explicit_source_scoped_encounters' if ek and other else 'provisional_observation_link'})
        for disposition,field in [('excluded','excluded_endpoints'),('unresolved','unresolved_endpoints')]:
            for why in sorted({a['reason'] for a in assessments if a['disposition']==disposition}):
                row[field].append({'evidence_ids':[a['event_id'] for a in assessments if a['disposition']==disposition and a['reason']==why],
                    'group_evidence_ids':ids,'reason':why,'qualifying_witness_ids':witness_ids,
                    'matched_by_other_witness':bool(qualifying),'copy_assessments':assessments})
    if row['matches']:row.update(status='future_endpoint_observed',recorded_label=1)
    elif row['unresolved_endpoints']:row['status']='unresolved_future_endpoint'
    elif not row['calendar_mature']:row['status']='incomplete_followup'
    else:row.update(status='no_future_endpoint_observed_mature',recorded_label=0)
    row['mature_recorded_label_usable']=row['calendar_mature'] and row['recorded_label'] is not None
    return row


def baseline_anchor(row):
    usable=row['decision'] and row['decision']['date']<=row['parameters']['available_by']
    return {'status':'receipt_decision' if usable else row['status'],
        'index':({'date':row['decision']['date'],'evidence_ids':row['index']['evidence_ids'],
                  'role':'analytical_decision_landmark'} if usable else None),
        'basis':'receipt_decision','source_evidence':([row['evidence'][e] for e in row['index']['evidence_ids']] if usable else []),
        'contract_id':row['contract']['id'],'contract_hash':row['contract']['hash'],
        'prospective_deployment_established':False}


def storage(path):
    db=sqlite3.connect(Path(path)/'decision-landmarks.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,patient TEXT,payload BLOB)');return db


def receipt_id(prep,pid):
    return digest([prep['base'],prep['baseline']['patient_inputs'][pid],
        prep['source']['index'].get(pid,[]),prep['source']['endpoint'].get(pid,[])])


def execute(path,patient_ids,p,*,prepared=None,preview_limit=6):
    started=time.perf_counter();prep=prepared or prepare(path,p);p=validate(p,prep['base']['snapshot']['as_of'])
    if p!=prep['base']['parameters']:raise ValueError('Landmark preparation mismatch')
    ids=sorted(set(patient_ids))
    if set(ids)-prep['patients']:raise ValueError('Unknown landmark patient')
    if type(preview_limit) is not int or not 0<=preview_limit<=20:raise ValueError('Bounded landmark preview required')
    src=sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True);ledger,_=L.connect(path);db=storage(path)
    statuses=Counter();processing=Counter();labels=Counter();exclusions=Counter();preview=[]
    try:
        src.execute('BEGIN')
        with db:
            for pid in ids:
                key=receipt_id(prep,pid);old=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
                if old:row=json.loads(zlib.decompress(old[0]));processing['patient_cache_hits']+=1
                else:
                    row=evaluate(pid,prep['source']['index'].get(pid,[]),prep['source']['endpoint'].get(pid,[]),prep['contract'],p)
                    anchor=baseline_anchor(row);rr=[];aa=[];tt=[]
                    if anchor['index']:
                        pr=src.execute('SELECT payload FROM patients WHERE id=?',(pid,)).fetchone()[0]
                        rr=[r[0] for r in src.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
                        aa=list(src.execute("SELECT operator,payload FROM analyses WHERE patient=? AND operator IN ('heart_failure_review','affordability_review') ORDER BY operator",(pid,)))
                        tt=[r[0] for r in ledger.execute("SELECT payload FROM events WHERE patient=? AND kind='text' ORDER BY id",(pid,))]
                        if B.input_hash(pr,rr,aa,tt)!=prep['baseline']['patient_inputs'][pid]:raise ValueError('Landmark baseline inputs changed during assembly')
                        for _,raw in aa:
                            analysis=json.loads(raw)
                            if analysis.get('source_run')!=prep['base']['source_run'] or analysis['workflow']['as_of']!=prep['base']['snapshot']['as_of']:
                                raise ValueError('Landmark analysis provenance differs from active source')
                        processing['patient_source_payloads_loaded']+=len(rr)
                    baseline=B.evaluate(pid,list(map(json.loads,rr)),[json.loads(raw) for _,raw in aa],list(map(json.loads,tt)),anchor,prep['baseline']['profile'])
                    # The index notification selects the landmark but is deliberately
                    # not a predictor; later target evidence stays out of features.
                    row.update(id=key,baseline=baseline,base=prep['base'])
                    row['candidate_observed_target']=bool(row['mature_recorded_label_usable'] and baseline['status']=='baseline_observations_available')
                    db.execute('INSERT INTO receipts VALUES (?,?,?)',(key,pid,zlib.compress(json.dumps(row,allow_nan=False).encode())))
                    processing['patients_computed']+=1
                statuses[row['status']]+=1
                if row['candidate_observed_target']:labels[str(row['recorded_label'])]+=1
                exclusions.update(x['reason'] for x in row['excluded_endpoints'])
                sample={k:row[k] for k in ('id','patient_id','status','index','decision','window','recorded_label','calendar_mature','candidate_observed_target')}
                sample['baseline_source_count']=len(row['baseline']['included_sources'])
                preview=sorted(preview+[sample],key=lambda r:(r['decision'] is None,r['status']!='future_endpoint_observed',r['patient_id']))[:preview_limit]
        return {'schema':VERSION,'synthetic':True,'contract':prep['contract'],'parameters':p,'identity':prep['identity'],
            'patients_evaluated':len(ids),'status_counts':dict(statuses),'candidate_label_counts':dict(labels),
            'excluded_endpoint_counts':dict(exclusions),'patients':preview,'processing_counts':dict(processing),
            'input_fingerprints':prep['baseline']['input_fingerprints'],'all_patient_receipts_persisted':True,
            'model_fitted':False,'clinical_prediction_authorized':False,'new_paid_api_calls':0,
            'elapsed_seconds':time.perf_counter()-started,'limits':LIMITS}
    finally:src.close();ledger.close();db.close()


def inspect(path,key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact landmark receipt required')
    db=storage(path)
    try:raw=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
    finally:db.close()
    if raw is None:raise ValueError('Unknown landmark receipt')
    row=json.loads(zlib.decompress(raw[0]));prep=prepare(path,row['parameters'])
    if key!=receipt_id(prep,row['patient_id']):raise ValueError('Landmark evidence or definition changed')
    return row


def export_dataset(path,patient_ids,p,output):
    """Separate observed targets from predictors, preserving exact receipt joins."""
    output=Path(output)
    if output.exists():raise ValueError('Fresh landmark dataset required')
    prep=prepare(path,p);ids=sorted(set(patient_ids));summary=execute(path,ids,p,prepared=prep,preview_limit=0)
    output.mkdir(parents=True);db=storage(path);labels=Counter()
    try:
        with (output/'features.jsonl').open('x') as xf,(output/'targets.jsonl').open('x') as yf:
            for pid in ids:
                key=receipt_id(prep,pid);row=json.loads(zlib.decompress(db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()[0]));b=row['baseline']
                reasons=[]
                if not row['decision']:reasons.append(row['status'])
                elif row['decision']['date']>p['available_by']:reasons.append('decision_after_available_cutoff')
                else:
                    if not row['calendar_mature']:reasons.append('incomplete_calendar_followup')
                    if row['recorded_label'] is None:reasons.append('unresolved_recorded_target')
                if b['status']!='baseline_observations_available':reasons.append('no_baseline_observations')
                if row['decision'] and b['cutoff']!=row['decision']['feature_cutoff']:reasons.append('invalid_decision_feature_cutoff')
                if not reasons:labels[str(row['recorded_label'])]+=1
                x={'patient_id':pid,'baseline_receipt_id':key,'baseline_status':b['status'],'feature_cutoff':b['cutoff'],
                    'values':b['features'],'input_record_ids':[r['record_id'] for r in b['included_sources']],
                    'household_groups':b.get('household_groups',[]),
                    'source_template_families':sorted({e['repeated_text_family'] for e in b.get('narrative',{}).get('events',[])})}
                q={'patient_id':pid,'baseline_receipt_id':key,'recorded_label':row['recorded_label'],
                   'timing_eligible_observed_target':not reasons,'exclusion_reasons':reasons,
                   'decision':row['decision'],'target_meaning':'Received future clinical-date observation; not a clinical negative or adjudicated stay.'}
                y={'patient_id':pid,'baseline_receipt_id':key,'outcome_receipt_id':key,'recorded_label':row['recorded_label'],
                   'clinical_label':None,'qualification':q}
                xf.write(json.dumps(x,allow_nan=False)+'\n');yf.write(json.dumps(y,allow_nan=False)+'\n')
    finally:db.close()
    manifest={'schema':VERSION,'synthetic':True,'identity':prep['identity'],'source_identity':prep['base'],
        'contract':prep['contract'],'parameters':p,'profile':prep['baseline']['profile'],'summary':summary,
        'candidate_label_counts':dict(labels),'feature_columns_only':'values; all other fields are join/protection metadata, never predictors',
        'files':{n:hashlib.sha256((output/n).read_bytes()).hexdigest() for n in ('features.jsonl','targets.jsonl')},
        'model_fitting_performed':False,'clinical_prediction_authorized':False}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n');return manifest
