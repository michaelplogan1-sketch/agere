"""Patient-specific pre-index evidence, separate from subsequent outcome labels.

Consumes existing input mappings, narrative ledger, dispense reconciliation and
source-volume blocks. Does not refit or modify the attached research predictors.
"""
from collections import Counter, defaultdict
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
import zlib

from ...contracts import digest
from ..narrative_sources import text_fields
from .clinical_events import iso
from . import outcomes, longitudinal, clinical_inputs, source_structure
from . import medication_reconciliation, source_blocks, outcome_readiness

VERSION='pre-index-evidence/1'
LIMITS=[
    'Synthetic retrospective development only; index observations are not independently adjudicated episodes.',
    'Source and receipt dates must both precede the index day. Same-day records have no verified within-day ordering.',
    'Current snapshots omit superseded or quarantined records; this is not a complete historical warehouse reconstruction.',
    'Index selection can use later-arriving index records. Prospective event ascertainment and population selection are not established.',
    'Unvalidated narrative labels remain machine proposals; retrospective extraction is not evidence a deployed model knew the interpretation then.',
    'A zero source count means no matching received record in the declared window, not absent disease, treatment or care.',
    'Source blocks describe arrival intensity. Dispense supply is not actual medication use or maximum tolerated dose.',
    'No model fitting, clinical risk estimate, causal conclusion or treatment recommendation is authorized by these candidate inputs.',
]


def profiles():
    raw=json.loads(Path(__file__).with_name('baseline_profiles.json').read_text())
    if raw.get('schema')!='baseline-evidence-profiles/1':raise ValueError('Unknown baseline profile schema')
    out={}
    for p in raw['profiles']:
        if set(p)!={'id','version','title','lookback_days','recent_days','source_block_months','cutoff','purpose','authority'}:
            raise ValueError('Exact baseline profile required')
        if not re.fullmatch('[a-z][a-z0-9_]{0,63}',p['id']) or p['id'] in out:raise ValueError('Unique bounded profile ID required')
        if any(type(p[k]) is not int for k in ('lookback_days','recent_days','source_block_months')) or not 1<=p['recent_days']<=p['lookback_days']<=3650 or not 1<=p['source_block_months']<=24:
            raise ValueError('Bounded baseline windows required')
        if p['cutoff']!='previous_calendar_day' or p['authority']!='retrospective_observation_features_only':raise ValueError('Explicit baseline authority required')
        out[p['id']]={**p,'hash':digest(p)}
    return out


def definition(key):
    if not isinstance(key,str) or key not in profiles():raise ValueError('Unknown registered baseline profile')
    return profiles()[key]


def adapter():
    r=json.loads(Path(__file__).with_name('baseline_input_maps.json').read_text())
    if r.get('schema')!='baseline-input-maps/1':raise ValueError('Unknown baseline source adapter')
    for key in ('streams','medication_classes','block_streams'):
        if not isinstance(r[key],list) or not r[key] or len(r[key])!=len(set(r[key])) or any(not isinstance(v,str) or not v for v in r[key]):
            raise ValueError('Fixed source vocabulary required')
    if set(r['block_streams'])-set(r['streams']):raise ValueError('Source block stream absent from adapter')
    return r


def validate(p):
    if not isinstance(p,dict) or set(p)!={'profile'}:raise ValueError('Registered baseline profile required')
    definition(p['profile']);return dict(p)


def code_identity():
    from . import patient_input_index
    return digest([clinical_inputs.identity(),outcomes.code_identity(),patient_input_index.identity(),
        *[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in
          (__file__,Path(__file__).with_name('baseline_profiles.json'),Path(__file__).with_name('baseline_input_maps.json'),medication_reconciliation.__file__,source_blocks.__file__,outcome_readiness.__file__)]])


def source_ref(r):
    return {'record_id':r['record_id'],'patient_id':r['patient_id'],'source_table':r['source_table'],
            'record_hash':digest(r),'source_date':r.get('event_date'),'arrival_date':r.get('available_date')}


def input_hash(patient_raw,records_raw,analyses_raw,text_raw):
    from . import patient_input_index
    return digest([*patient_input_index.parts(patient_raw,records_raw,analyses_raw)['baseline_components'],
                   digest([digest(x) for x in text_raw])])


def prepare(path,parameters,outcome_prepared):
    path=Path(path);spec=definition(validate(parameters)['profile'])
    meta=json.loads((path/'run.json').read_text())
    if meta.get('synthetic') is not True or meta['snapshot']!=outcome_prepared['dependency']['snapshot'] or meta['source_digest']!=outcome_prepared['dependency']['source_digest']:
        raise ValueError('Baseline and outcome warehouses disagree')
    from . import patient_input_index
    inputs=patient_input_index.snapshot(path)
    ledger,lmeta=longitudinal.connect(path)
    try:
        patients=inputs['patients']
        if set(patients)!=outcome_prepared['patients']:raise ValueError('Baseline and outcome populations disagree')
        texts=defaultdict(list)
        for pid,raw in ledger.execute("SELECT patient,payload FROM events WHERE kind='text' ORDER BY patient,id"):texts[pid].append(digest(raw))
        if set(texts)-set(patients):raise ValueError('Baseline source patient absent from roster')
        hashes={pid:digest([*value['baseline_components'],digest(texts[pid])]) for pid,value in patients.items()}
    finally:ledger.close()
    base={'code':code_identity(),'profile_hash':spec['hash'],'snapshot':meta['snapshot'],
          'source_digest':meta['source_digest'],'source_run':meta['source_run'],'reader':lmeta['reader_model_identity']}
    return {'base':base,'profile':spec,'patient_inputs':hashes,'outcome':outcome_prepared,
            'identity':digest([base,hashes,outcome_prepared['identity']]),'input_fingerprints':inputs['processing']}


def anchor(outcome):
    """Only index information crosses into features; labels/endpoints do not."""
    if outcome['indexed'] is not True:return {'status':outcome['status'] if not outcome.get('index') else 'unresolved_index','index':None}
    idx=outcome['index'];ids=idx['evidence_ids']
    ev=[outcome['evidence'][i] for i in ids]
    return {'status':'indexed','index':idx,'basis':outcome['contract']['basis'],
        'contract_id':outcome['contract']['id'],'contract_hash':outcome['contract']['hash'],
        'source_evidence':ev,'prospective_ascertainment_established':False}


def arrival_blocks(records,cutoff,start,spec,mapping):
    labels,_=source_blocks.month_bins(cutoff,spec['source_block_months']);bins=[]
    for month in labels:
        y,m=map(int,month.split('-'));lo=max(start,month+'-01');hi=min(cutoff,f'{month}-{monthrange(y,m)[1]:02d}')
        if lo<=hi:bins.append((month,lo,hi,(date.fromisoformat(hi)-date.fromisoformat(lo)).days+1))
    reports=[]
    for stream in mapping['block_streams']:
        rows=[r for r in records if r['source_table']==stream and bins[0][1]<=r['available_date']<=cutoff]
        counts=Counter(r['available_date'][:7] for r in rows)
        series=[counts[b[0]] for b in bins];fitted=source_blocks.poisson_blocks(series,[b[3] for b in bins])
        reports.append({'source':stream,'bins':[{'month':m,'start':lo,'end':hi,'calendar_days':n} for m,lo,hi,n in bins],
            'record_counts':series,'source_record_ids':[r['record_id'] for r in rows],**fitted})
    return {'sources':reports,'meaning':'Arrival intensity among selected baseline records; exposure is clipped to the declared window.',
            'clinical_change_established':False,'source_capture_complete':False}


def evaluate(pid,records,analyses,text_events,index,profile,mapping=None):
    mapping=mapping or adapter()
    if len({r['record_id'] for r in records})!=len(records):raise ValueError('Duplicate baseline record ID')
    if any(r['patient_id']!=pid or r.get('synthetic') is not True or r.get('identity_status')!='matched' for r in records):
        raise ValueError('Matched synthetic patient sources required')
    base={'schema':VERSION,'synthetic':True,'patient_id':pid,'profile':profile,'anchor':index,
          'status':'no_resolved_index','cutoff':None,'window_start':None,'features':{},'feature_evidence':{},
          'record_accounting':{},'included_sources':[],'excluded_sources':[],
          'measurements':{},'medications':{},'narrative':{},'source_changes':None,
          'clinical_state_established':False,'model_training_authorized':False,'limits':LIMITS,
          'household_groups':sorted({str(r['household_id']) for r in records if r.get('household_id')})}
    if index['index'] is None:return base
    idx=index['index'];day=idx['date']
    if not iso(day) or day=='0001-01-01':raise ValueError('Usable canonical index day required')
    if any(e['patient_id']!=pid or e['source']['patient_id']!=pid for e in index['source_evidence']):raise ValueError('Cross-patient index evidence')
    cutoff=(date.fromisoformat(day)-timedelta(days=1)).isoformat()
    try:start=(date.fromisoformat(cutoff)-timedelta(days=profile['lookback_days']-1)).isoformat()
    except OverflowError:raise ValueError('Index predates supported baseline window') from None
    index_record_ids={e['source']['record_id'] for e in index['source_evidence']}
    known={r['record_id']:r for r in records};selected=[];excluded=[]
    for e in index['source_evidence']:
        r=known.get(e['source']['record_id'])
        if r is None or digest(r)!=e['record_hash']:raise ValueError('Index source differs from warehouse record')
    for r in records:
        reason=None
        if r['record_id'] in index_record_ids:reason='index_record'
        elif not iso(r.get('available_date')):reason='arrival_date_unresolved'
        elif r['available_date']>=day:reason='received_on_or_after_index_day'
        elif not iso(r.get('event_date')):reason='source_date_unresolved'
        elif r['event_date']>=day:reason='source_dated_on_or_after_index_day'
        elif r['source_table']=='pharmacy_claims' and iso(r.get('recorded_date')) and r['recorded_date']>=day:
            reason='adjudication_recorded_on_or_after_index_day'
        elif r['event_date']<start:reason='before_declared_lookback'
        if reason:excluded.append({'reason':reason,'source':source_ref(r)})
        else:selected.append(r)
    ids={r['record_id'] for r in selected};features={};evidence={}
    # The set of feature columns comes from the fixed profile/source mapping,
    # never from a patient's future-only record types or narrative vocabulary.
    streams=mapping['streams']
    for stream in streams:
        rows=[r for r in selected if r['source_table']==stream]
        features[stream+':received_records']=len(rows)
        features[stream+':received_recent']=sum((date.fromisoformat(cutoff)-date.fromisoformat(r['available_date'])).days<profile['recent_days'] for r in rows)
        features[stream+':days_since_receipt']=min(((date.fromisoformat(cutoff)-date.fromisoformat(r['available_date'])).days for r in rows),default=None)
        evidence[stream]=[r['record_id'] for r in rows]
    safe_analyses=[];fact_exclusions=[]
    for a in analyses:
        if a.get('patient_id')!=pid:raise ValueError('Cross-patient source interpretation')
        facts=[]
        for f in a['source_bound_facts']:
            if f['patient_id']!=pid:raise ValueError('Cross-patient source fact')
            refs=f.get('evidence',[])
            if refs and all(e['record_id'] in ids for e in refs):facts.append(f)
            else:fact_exclusions.append({'fact_id':f['fact_id'],'reason':'missing_or_nonbaseline_source_dependency'})
        safe_analyses.append({**a,'source_bound_facts':facts})
    measurements=clinical_inputs.measurements(pid,selected,safe_analyses,cutoff)
    states={}
    for concept in clinical_inputs.mappings()['measurements']:
        states[concept]={}
        for clock in ('source_statement_date','explicit_clinical_date'):
            b={'clock':clock,'selection':'latest','op':'ge','value':0,'expected':'known'}
            state=clinical_inputs.measurement_binding(measurements.get(concept,[]),b,cutoff)
            states[concept][clock]=state;prefix=concept+':'+clock
            features[prefix+':status']=state['status']
            selected_obs=state['selected_observations']
            for end in ('lower','upper'):
                features[prefix+':'+end]=selected_obs[0][end] if state['value'] is True else None
            evidence[prefix]=sorted({e['record_id'] for o in selected_obs for e in o['evidence']})
    # All class mappings are source fields. This does not infer equivalence or use.
    meds={}
    for drug_class in mapping['medication_classes']:
        meds[drug_class]=medication_reconciliation.reconcile(selected,drug_class,cutoff)
        features['medication:'+drug_class+':supply_status']=meds[drug_class]['status']
        features['medication:'+drug_class+':dispense_conflicts']=len(meds[drug_class]['source_disagreements'])
    text=[];text_exclusions=[]
    for e in text_events:
        if e['patient_id']!=pid or e['source']['patient_id']!=pid:raise ValueError('Cross-patient baseline annotation')
        s=e['source'];r=known.get(s['record_id'])
        if r is None:raise ValueError('Narrative source absent from warehouse')
        raw=next((p['text'] for p in text_fields(r) if p['field']==s['field']),None)
        if digest(r)!=e['record_hash'] or not isinstance(raw,str) or raw[s['start']:s['end']]!=s['quote']:
            raise ValueError('Narrative baseline evidence changed')
        if r['record_id'] not in ids:reason='nonbaseline_source'
        elif source_structure.locate(raw,s['start'],s['end'])['clinical_account_excluded']:reason='nonclinical_source_role'
        else:reason=None
        if reason:text_exclusions.append({'event_id':e['id'],'reason':reason});continue
        # Recompute relative/historical context at the feature cutoff. The
        # original full-snapshot event is retained as an explicitly machine label.
        text.append({**e,**longitudinal.narrative_reference(raw,s['start'],s['end'],cutoff)})
    families={e['repeated_text_family'] for e in text}
    features['narrative:distinct_received_text_families']=len(families)
    features['narrative:machine_annotations']=len(text)
    accounts=longitudinal.reconcile_accounts(text)
    changes=arrival_blocks(selected,cutoff,start,profile,mapping)
    for r in changes['sources']:
        features['source_blocks:'+r['source']+':count']=len(r['blocks'])
        features['source_blocks:'+r['source']+':last_arrival_rate']=r['blocks'][-1]['records_per_day'] if r['blocks'] else None
    base.update(status='baseline_observations_available' if selected else 'no_baseline_observations',cutoff=cutoff,window_start=start,
        features=features,feature_evidence=evidence,measurements=states,medications=meds,source_changes=changes,
        included_sources=[source_ref(r) for r in selected],excluded_sources=excluded,
        record_accounting={'considered':len(records),'included':len(selected),'excluded':dict(Counter(r['reason'] for r in excluded))},
        unmapped_sources=[source_ref(r) for r in selected if r['source_table'] not in mapping['streams']],
        unmapped_medication_classes=[source_ref(r) for r in selected if r.get('drug_class') and r['drug_class'] not in mapping['medication_classes']],
        narrative={'events':text,'excluded':text_exclusions,'accounts':accounts,'fact_exclusions':fact_exclusions,
                   'authority':'unvalidated_machine_proposals','model_was_deployed_at_index':False})
    return base


def storage(path):
    db=sqlite3.connect(Path(path)/'baseline-evidence.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,patient TEXT,payload BLOB)')
    return db


def receipt_id(prepared,pid):
    # Anchor/index preparation is bound here. Outcomes remain separate payloads.
    return digest([prepared['base'],prepared['patient_inputs'][pid],outcomes.receipt_id(prepared['outcome'],pid)])


def execute(path,patient_ids,parameters,*,prepared,preview_limit=4):
    t=time.perf_counter();p=validate(parameters)
    if definition(p['profile'])['hash']!=prepared['profile']['hash']:raise ValueError('Baseline preparation mismatch')
    ids=sorted(set(patient_ids))
    if set(ids)-set(prepared['patient_inputs']):raise ValueError('Unknown baseline patient')
    if type(preview_limit) is not int or not 0<=preview_limit<=20:raise ValueError('Bounded baseline preview required')
    source=sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    ledger,_=longitudinal.connect(path);odb=outcomes.storage(path);db=storage(path)
    statuses=Counter();counts=Counter();excluded=Counter();preview=[];readiness=[]
    try:
        source.execute('BEGIN')
        with db:
            for pid in ids:
                key=receipt_id(prepared,pid);old=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
                if old:row=json.loads(zlib.decompress(old[0]));counts['patient_cache_hits']+=1
                else:
                    oid=outcomes.receipt_id(prepared['outcome'],pid)
                    raw=odb.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(oid,pid)).fetchone()
                    if raw is None:raise ValueError('Execute the upstream outcome node before baseline assembly')
                    outcome=json.loads(zlib.decompress(raw[0]))
                    patient_raw=source.execute('SELECT payload FROM patients WHERE id=?',(pid,)).fetchone()[0]
                    rr=[r[0] for r in source.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
                    aa=list(source.execute("SELECT operator,payload FROM analyses WHERE patient=? AND operator IN ('heart_failure_review','affordability_review') ORDER BY operator",(pid,)))
                    tt=[r[0] for r in ledger.execute("SELECT payload FROM events WHERE patient=? AND kind='text' ORDER BY id",(pid,))]
                    if input_hash(patient_raw,rr,aa,tt)!=prepared['patient_inputs'][pid]:raise ValueError('Baseline inputs changed during assembly')
                    analyses=[json.loads(raw) for _,raw in aa]
                    if any(a.get('source_run')!=prepared['base']['source_run'] or a['workflow']['as_of']!=prepared['base']['snapshot']['as_of'] for a in analyses):
                        raise ValueError('Baseline interpretation snapshot mismatch')
                    row=evaluate(pid,list(map(json.loads,rr)),analyses,list(map(json.loads,tt)),anchor(outcome),prepared['profile'])
                    row.update(id=key,base=prepared['base'],input_hash=prepared['patient_inputs'][pid],outcome_receipt_id=oid,
                               outcome_parameters=prepared['outcome']['parameters'])
                    db.execute('INSERT INTO receipts VALUES (?,?,?)',(key,pid,zlib.compress(json.dumps(row,allow_nan=False).encode())))
                    counts['patient_baselines_computed']+=1
                target=json.loads(zlib.decompress(odb.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(row['outcome_receipt_id'],pid)).fetchone()[0]))
                readiness.append(outcome_readiness.assess(row,target))
                statuses[row['status']]+=1;counts['included_source_records']+=row['record_accounting'].get('included',0)
                excluded.update(row['record_accounting'].get('excluded',{}))
                counts['baseline_machine_annotations']+=len(row.get('narrative',{}).get('events',[]))
                sample={k:row[k] for k in ('id','patient_id','status','cutoff','window_start','record_accounting')}
                preview=sorted(preview+[sample],key=lambda r:(r['cutoff'] is None,r['status']!='baseline_observations_available',r['patient_id']))[:preview_limit]
    finally:source.close();ledger.close();odb.close();db.close()
    return {'schema':VERSION,'synthetic':True,'profile':prepared['profile'],'identity':prepared['identity'],
        'patients_evaluated':len(ids),'population_digest':digest(ids),'status_counts':dict(statuses),
        'processing_counts':dict(counts),'input_fingerprints':prepared.get('input_fingerprints'),
        'excluded_source_counts':dict(excluded),'patients':preview,
        'prediction_readiness':outcome_readiness.summarize(readiness),
        'all_patient_receipts_persisted':True,'new_paid_api_calls':0,'elapsed_seconds':time.perf_counter()-t,'limits':LIMITS}


def inspect(path,key):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact baseline receipt required')
    db=storage(path)
    try:raw=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
    finally:db.close()
    if raw is None:raise ValueError('Unknown baseline receipt')
    row=json.loads(zlib.decompress(raw[0]));op=outcomes.prepare(path,row['outcome_parameters'])
    fresh=prepare(path,{'profile':row['profile']['id']},op)
    if receipt_id(fresh,row['patient_id'])!=key:raise ValueError('Baseline inputs or definition changed; reevaluate')
    odb=outcomes.storage(path)
    try: target=json.loads(zlib.decompress(odb.execute('SELECT payload FROM receipts WHERE id=?',(row['outcome_receipt_id'],)).fetchone()[0]))
    finally: odb.close()
    return {**row,'prediction_readiness':outcome_readiness.assess(row,target)}


def export_dataset(path,patient_ids,parameters,outcome_parameters,output):
    """Candidate X/y files with separate content, exact joins and no fitting."""
    output=Path(output)
    if output.exists():raise ValueError('Fresh baseline dataset directory required')
    ids=sorted(set(patient_ids));op=outcomes.prepare(path,outcome_parameters)
    outcomes.execute(path,ids,outcome_parameters,prepared=op,preview_limit=0)
    prep=prepare(path,parameters,op);summary=execute(path,ids,parameters,prepared=prep,preview_limit=0)
    output.mkdir(parents=True);db=storage(path);odb=outcomes.storage(path);counts=Counter();labels=Counter()
    try:
        with (output/'features.jsonl').open('x') as xf,(output/'targets.jsonl').open('x') as yf:
            for pid in ids:
                key=receipt_id(prep,pid);oid=outcomes.receipt_id(op,pid)
                row=json.loads(zlib.decompress(db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()[0]))
                target=json.loads(zlib.decompress(odb.execute('SELECT payload FROM receipts WHERE id=?',(oid,)).fetchone()[0]))
                eligible=row['status']=='baseline_observations_available' and target['mature_recorded_label_usable']
                counts['rows']+=1;counts['baseline_and_mature_recorded_label']+=eligible
                if eligible:labels[str(target['recorded_label'])]+=1
                # Export X deliberately omits outcome ID, status, endpoints and
                # selection reasons. Join IDs and features are explicitly named.
                xf.write(json.dumps({'patient_id':pid,'baseline_receipt_id':key,'baseline_status':row['status'],
                    'feature_cutoff':row['cutoff'],'values':row['features'],
                    'input_record_ids':[r['record_id'] for r in row['included_sources']],
                    'household_groups':row.get('household_groups',[]),
                    'source_template_families':sorted({e['repeated_text_family'] for e in row.get('narrative',{}).get('events',[])})},allow_nan=False)+'\n')
                yf.write(json.dumps({'patient_id':pid,'baseline_receipt_id':key,'outcome_receipt_id':oid,
                    'recorded_label':target['recorded_label'],'outcome_status':target['status'],
                    'mature_recorded_label_usable':target['mature_recorded_label_usable'],
                    'baseline_and_mature_recorded_label':eligible,'clinical_label':None,
                    'qualification':outcome_readiness.assess(row,target)},allow_nan=False)+'\n')
    finally:db.close();odb.close()
    reasons=['Independent family/template partitions and protected-cohort exclusions are not assigned by this export.',
             'Index ascertainment, source capture and independent temporal/clinical validation remain unestablished.']
    if len(labels)<2:reasons.append('Usable rows do not contain both recorded outcome classes.')
    result={'schema':VERSION,'synthetic':True,'identity':prep['identity'],'profile':prep['profile'],
        'outcome_parameters':outcome_parameters,'counts':dict(counts),'usable_recorded_label_counts':dict(labels),
        'feature_columns_only':'values; patient IDs and receipt IDs are join keys, never predictors',
        'model_fitting_performed':False,'fit_authorized':False,'readiness_gaps':reasons,
        'files':{name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in ('features.jsonl','targets.jsonl')},
        'baseline_status_counts':summary['status_counts'],'prediction_readiness':summary['prediction_readiness'],
        'source_identity':prep['base'],'new_paid_api_calls':0,'limits':LIMITS}
    (output/'manifest.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result
