"""Offline appointment target qualification; no ancestry in runtime evidence."""
from collections import Counter
from pathlib import Path
import gzip, json, sqlite3
from ...contracts import digest
from . import appointment_followthrough as A, baseline_evidence as B, longitudinal as L
from . import outcome_learning as Q, prediction_registry as R, case_passes

VERSION = 'appointment-observation-qualification/1'
LINK = 'appointment-qualification-link.json'

def identity(): return digest([Q.sha(__file__), A.identity(), B.code_identity(), Q.identity()])

def inputs(path, review):
    # Reuse the complete input fingerprint index, with no fictitious outcome adapter.
    db, meta = A.T.connect(path)
    try: universe = {r[0] for r in db.execute('SELECT id FROM patients')}
    finally: db.close()
    dependency = {'dependency': {k:meta[k] for k in ('snapshot','source_digest')}, 'patients': universe, 'identity': review['id']}
    return B.prepare(path, {'profile': A.CONTRACT['profile']}, dependency)

def anchor(row):
    if not row['decision']: return {'status': row['status'], 'index': None}
    sources = {s['record_id']:s for s in row['sources']}
    return {'status': 'received_booking_decision', 'index': {'date': row['decision']['date']},
        'source_evidence': [{'patient_id': row['patient_id'], 'source': {'patient_id': row['patient_id'], 'record_id': key},
                             'record_hash': sources[key]['record_hash']} for key in row['index']['record_ids']]}

def export_dataset(path, review_id, output):
    output = Path(output)
    if output.exists(): raise ValueError('Fresh appointment dataset required')
    review = A.inspect(path, review_id); prep = inputs(path, review)
    src, meta = A.T.connect(path); ledger, _ = L.connect(path); output.mkdir(parents=True)
    try:
        src.execute('BEGIN'); ledger.execute('BEGIN')
        with (output/'features.jsonl').open('x') as xf, (output/'targets.jsonl').open('x') as yf, gzip.open(output/'baselines.jsonl.gz','wt') as bf:
            for row in review['patients']:
                pid = row['patient_id']; ar = anchor(row)
                # Full narrative family metadata protects related records even when
                # a later source is excluded from every predictor.
                texts = [r[0] for r in ledger.execute("SELECT payload FROM events WHERE patient=? AND kind='text' ORDER BY id",(pid,))]
                parsed_text = [json.loads(r) for r in texts]
                records = []; analyses = []
                if ar['index']:
                    raw = [r[0] for r in src.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
                    aa = list(src.execute("SELECT operator,payload FROM analyses WHERE patient=? AND operator IN ('heart_failure_review','affordability_review') ORDER BY operator",(pid,)))
                    patient = src.execute('SELECT payload FROM patients WHERE id=?',(pid,)).fetchone()[0]
                    if B.input_hash(patient, raw, aa, texts) != prep['patient_inputs'][pid]: raise ValueError('Appointment baseline input changed')
                    records = list(map(json.loads,raw)); analyses = [json.loads(v) for _,v in aa]
                    if any(a.get('source_run')!=meta['source_run'] or a['workflow']['as_of']!=meta['snapshot']['as_of'] for a in analyses): raise ValueError('Baseline analysis source mismatch')
                baseline = B.evaluate(pid, records, analyses, parsed_text if ar['index'] else [], ar, prep['profile'])
                key = digest([review_id,pid,digest(row),prep['patient_inputs'][pid],baseline]); reasons=[]
                if row['recorded_label'] is None: reasons.append(row['status'])
                if baseline['status']!='baseline_observations_available': reasons.append('no_baseline_observations')
                if row['decision'] and baseline['cutoff']!=row['decision']['feature_cutoff']: reasons.append('invalid_feature_cutoff')
                # Target/history IDs are linkage protection metadata, never values.
                x={'patient_id':pid,'baseline_receipt_id':key,'baseline_status':baseline['status'],'feature_cutoff':baseline['cutoff'],
                   'values':baseline['features'],'input_record_ids':sorted({r['record_id'] for r in baseline['included_sources']} | set(row['history_record_ids'])),
                   'household_groups':baseline['household_groups'],'source_template_families':sorted({r['repeated_text_family'] for r in parsed_text})}
                qualification={'patient_id':pid,'baseline_receipt_id':key,'recorded_label':row['recorded_label'],
                    'timing_eligible_observed_target':not reasons,'exclusion_reasons':reasons,'target_meaning':'Explicit fulfilled versus explicit no_show report; cancelled and unobserved follow-through excluded.'}
                y={'patient_id':pid,'baseline_receipt_id':key,'outcome_receipt_id':review_id,'recorded_label':row['recorded_label'],'clinical_label':None,'qualification':qualification}
                xf.write(json.dumps(x,allow_nan=False)+'\n'); yf.write(json.dumps(y,allow_nan=False)+'\n')
                bf.write(json.dumps({'id':key,'patient_id':pid,'review_id':review_id,'baseline':baseline},allow_nan=False)+'\n')
    finally: src.close(); ledger.close()
    if inputs(path,A.inspect(path,review_id))['identity']!=prep['identity']: raise ValueError('Appointment dataset changed during export')
    manifest={'schema':VERSION,'synthetic':True,'review_id':review_id,'identity':prep['identity'],'implementation_identity':identity(),
        'source_identity':{k:review['source'][k] for k in ('snapshot','source_digest','source_run')},'profile':prep['profile'],
        'contract':A.CONTRACT,'files':{n:Q.sha(output/n) for n in ('features.jsonl','targets.jsonl','baselines.jsonl.gz')},
        'features_contract':'Only values are predictors. All IDs, families, households and targets are linkage/protection metadata.',
        'clinical_prediction_authorized':False}
    Q.write(output/'manifest.json',manifest); return manifest

def qualify(features,targets,policy,protected):
    q=Q.qualify(features,targets,policy,protected)
    counts={part:{str(label):len({r['component'] for r in q['rows'] if r['partition']==part and r['recorded_label']==label}) for label in (0,1)} for part in Q.SPLITS}
    q['independent_component_class_counts']=counts
    for part in Q.SPLITS:
        if any(counts[part][str(label)]<Q.PLAN['minimum_per_class'][part] for label in (0,1)):
            q['readiness_gaps'].append(part+': insufficient distinct source-family components per recorded class')
    q['fit_input_contract_ready']=not q['readiness_gaps']; return q

def verify_dataset(path, dataset):
    dataset=Path(dataset); m=R.read(dataset/'manifest.json')
    if m['implementation_identity']!=identity() or m['schema']!=VERSION or m['contract']!=A.CONTRACT: raise ValueError('Appointment dataset implementation changed')
    if set(m['files'])!={'features.jsonl','targets.jsonl','baselines.jsonl.gz'} or any(Q.sha(dataset/n)!=h for n,h in m['files'].items()): raise ValueError('Appointment dataset files changed')
    review=A.inspect(path,m['review_id'])
    if any(review['source'][k]!=m['source_identity'][k] for k in m['source_identity']) or inputs(path,review)['identity']!=m['identity']: raise ValueError('Appointment evidence changed')
    return m

def build(path,lineage,dataset,output):
    output=Path(output); dataset=Path(dataset).resolve()
    if output.exists(): raise ValueError('Fresh appointment qualification output required')
    m=verify_dataset(path,dataset); meta=R.read(Path(path)/'run.json')
    policy,lm=Q.learning_queue.lineage(lineage,meta); protected=Q.semantic_learning.protections()
    features,targets=[[json.loads(v) for v in (dataset/n).read_text().splitlines()] for n in ('features.jsonl','targets.jsonl')]
    q=qualify(features,targets,policy,protected); output.mkdir(parents=True); Q.write(output/'qualification.json',q)
    model=Q.fit_rows(q,output/'models')
    r={'schema':VERSION,'implementation_identity':identity(),'review_id':m['review_id'],'dataset':str(dataset),
       'manifest_sha256':Q.sha(dataset/'manifest.json'),'lineage':str(Path(lineage).resolve()),'policy_sha256':lm['policy_sha256'],
       'protection_digest':digest(protected),'qualification_sha256':Q.sha(output/'qualification.json'),
       'model_sha256':Q.sha(output/'models/run.json'),'status':model['status'],'source_qualification':R.source_summary(features,targets),
       'counts':{k:q[k] for k in ('patients_assessed','eligible_rows','component_count','partition_class_counts','independent_component_class_counts','excluded_reason_counts')},
       'readiness_gaps':q['readiness_gaps'],'class_deficits':R.class_deficits(q),'fit_input_contract_ready':q['fit_input_contract_ready'],
       'clinical_prediction_authorized':False,'automatic_promotion':False,'new_paid_api_calls':0,'limits':A.LIMITS+R.LIMITS}
    if m!=verify_dataset(path,dataset) or digest(Q.semantic_learning.protections())!=r['protection_digest'] or Q.learning_queue.lineage(lineage,meta)[1]['policy_sha256']!=r['policy_sha256']: raise ValueError('Qualification source changed during build')
    r['id']=digest(r); Q.write(output/'run.json',r); return r

def status(path,entry=None):
    link=Path(path)/LINK
    if entry is None:
        if not link.exists(): return {'available':False,'clinical_prediction_authorized':False}
        entry=R.read(link)
    root=Path(entry['path']); r=R.read(root/'run.json')
    if Q.sha(root/'run.json')!=entry['sha256'] or r['id']!=digest({k:v for k,v in r.items() if k!='id'}) or r['implementation_identity']!=identity(): raise ValueError('Appointment qualification report changed')
    m=verify_dataset(path,r['dataset'])
    if Q.sha(Path(r['dataset'])/'manifest.json')!=r['manifest_sha256'] or m['review_id']!=r['review_id']: raise ValueError('Qualification dataset binding changed')
    if Q.learning_queue.lineage(r['lineage'],R.read(Path(path)/'run.json'))[1]['policy_sha256']!=r['policy_sha256'] or digest(Q.semantic_learning.protections())!=r['protection_digest']: raise ValueError('Qualification protections changed')
    if Q.sha(root/'qualification.json')!=r['qualification_sha256'] or Q.sha(root/'models/run.json')!=r['model_sha256']: raise ValueError('Qualification or comparison changed')
    model=R.read(root/'models/run.json'); q=R.read(root/'qualification.json')
    if model['qualification_digest']!=digest(q) or model['status']!=r['status'] or model['automatic_promotion'] or model['clinical_prediction_authorized']: raise ValueError('Invalid appointment comparison binding')
    for name,payload in model['models'].items():
        if name not in Q.PLAN['candidates'] or Q.sha(root/'models'/(name+'.pkl'))!=payload['weights_sha256']: raise ValueError('Comparison weights changed')
    if model.get('predictions_sha256') and Q.sha(root/'models/development-predictions.json')!=model['predictions_sha256']: raise ValueError('Comparison predictions changed')
    return {k:r[k] for k in ('id','review_id','status','source_qualification','counts','readiness_gaps','class_deficits','fit_input_contract_ready','limits','clinical_prediction_authorized','automatic_promotion')} | {'available':True,'current':True,'model_report':model}

def attach(path,root):
    root=Path(root).resolve(); entry={'path':str(root),'sha256':Q.sha(root/'run.json')}
    result=status(path,entry)
    case_passes.atomic_write(Path(path)/LINK,entry)
    return result
