"""Fitted local predictors of explicitly defined future observed records.

Feature availability and source-event cutoffs are enforced. This is a research
forecasting component, not a clinically validated hospitalization-risk score.
"""
from collections import defaultdict, Counter
from datetime import date, timedelta
from pathlib import Path
import hashlib
import json
import math
import pickle
import sqlite3
import time

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ...contracts import digest
from .cohorts import compare, patient_value, record_value, validate, empty
from .clinical_events import iso
from .source_blocks import source_summary

VERSION='observed-record-forecast/1'
STREAMS=['adt','encounters','labs','pharmacy_claims','medication_history','notes','documents','referrals','financial']
CONCEPTS=['financial hardship','outside care','diagnosis heart failure','diagnosis ckd','symptom dyspnea','goals of care']
LABS={'egfr':{'units':{'ml/min/1.73m2','ml/min/1.73m²'},'codes':{'egfr','33914-3','62238-1'}},
      'potassium':{'units':{'mmol/l','meq/l'},'codes':{'potassium','2823-3'}},
      'hba1c':{'units':{'%'},'codes':{'hba1c','4548-4'}}}
BASE_FEATURES=['age']+[f'{s}:{k}' for s in STREAMS for k in ('log_count_30d','log_count_180d','days_since_received')]+[
    f'{c}:{k}' for c in LABS for k in ('last_recorded_value','source_date_slope_per_year')]+[
    f'text:{c}:affirmed_patient' for c in CONCEPTS]+['financial:latest_report']
BLOCK_FEATURES=[f'{s}:{k}' for s in ('notes','labs','pharmacy_claims') for k in ('arrival_blocks','last_block_rate','last_vs_full_rate')]
FEATURES=BASE_FEATURES+BLOCK_FEATURES
LIMITATIONS=[
 'The target is a future observed source record, not all actual hospitalizations, preventable admissions or intervention benefit.',
 'Unknown outside follow-up and source capture mean a zero label is no observed endpoint, not an established clinical negative.',
 'Only one outcome month exists in this snapshot; no independent temporal or site validation is available.',
 'Patient/household groups are separated; hidden source-library family ancestry is unavailable and independence is unverified.',
 'The current snapshot omits superseded/quarantined records; availability filtering cannot reconstruct every historical warehouse state.',
 'Existing narrative reader annotations are unvalidated machine features and were not trained independently of this warehouse.',
 'September-to-October predictions are unvalidated extrapolation from the July-to-August task, with possible source and population drift.',
 'No automatic treatment, referral, resource allocation or care recommendation follows from these predictions.',
]


def feature_code():
    return digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   hashlib.sha256(Path(__file__).with_name('source_blocks.py').read_bytes()).hexdigest()])


def available(records, cutoff):
    return [r for r in records if iso(r.get('available_date')) and iso(r.get('event_date'))
            and r['available_date']<=cutoff and r['event_date']<=cutoff]


def features(patient, records, annotations, cutoff):
    rows=available(records,cutoff);now=date.fromisoformat(cutoff);v={};evidence={}
    v['age']=patient_value(patient,'age',cutoff)
    for stream in STREAMS:
        rs=[r for r in rows if r['source_table']==stream]
        ages=[(now-date.fromisoformat(r['available_date'])).days for r in rs]
        v[f'{stream}:log_count_30d']=math.log1p(sum(x<30 for x in ages))
        v[f'{stream}:log_count_180d']=math.log1p(sum(x<180 for x in ages))
        v[f'{stream}:days_since_received']=min(ages) if ages else None
        evidence[stream]=[r['record_id'] for r in rs]
    for concept,spec in LABS.items():
        bydate=defaultdict(list)
        for r in rows:
            if r['source_table']!='labs' or not {str(r.get('test','')).lower(),str(r.get('code','')).lower()}&spec['codes']:continue
            if r.get('status') not in ('final','corrected','amended'):continue
            unit=str(r.get('unit','')).lower().replace(' ','')
            value=r.get('value')
            if type(value) not in (int,float) or not math.isfinite(value) or value<0 or unit not in spec['units']:continue
            bydate[r['event_date']].append((float(value),r['record_id']))
        # Same-date disagreements are excluded rather than averaged.
        usable=[(day,rs[0][0],[r[1] for r in rs]) for day,rs in sorted(bydate.items()) if len({r[0] for r in rs})==1]
        v[f'{concept}:last_recorded_value']=usable[-1][1] if usable else None
        slopes=[(b[1]-a[1])*365.25/(date.fromisoformat(b[0])-date.fromisoformat(a[0])).days
                for i,a in enumerate(usable) for b in usable[i+1:]]
        v[f'{concept}:source_date_slope_per_year']=float(np.median(slopes)) if slopes else None
        evidence['lab:'+concept]=[rid for _,_,ids in usable for rid in ids]
    known_ids={r['record_id'] for r in rows}
    for concept in CONCEPTS:
        seen=set();ids=[]
        for a in annotations:
            p=a['payload'];s=p['source']
            if a['concept']!=concept or s['record_id'] not in known_ids:continue
            if p['axes'].get('assertion')!='affirmed' or p['axes'].get('subject')!='patient':continue
            # Copy-normalized matching text does not become independent evidence.
            key=' '.join(p['input']['text'].lower().split())
            if key not in seen:seen.add(key);ids.append(a['id'])
        v[f'text:{concept}:affirmed_patient']=math.log1p(len(seen))
        evidence['text:'+concept]=ids
    screens=[r for r in rows if r['source_table']=='financial' and type(r.get('difficulty_paying_medical_bills')) is bool]
    if screens:
        last=max(r['event_date'] for r in screens);vals={r['difficulty_paying_medical_bills'] for r in screens if r['event_date']==last}
        v['financial:latest_report']=float(next(iter(vals))) if len(vals)==1 else None
    else:v['financial:latest_report']=None
    blocks=source_summary(rows,cutoff,['notes','labs','pharmacy_claims'],months=6)
    for r in blocks['sources']:
        last=r['blocks'][-1]['records_per_day'];overall=sum(r['record_counts'])/sum(b['calendar_days'] for b in r['blocks'])
        v[r['source']+':arrival_blocks']=len(r['blocks'])
        v[r['source']+':last_block_rate']=last
        v[r['source']+':last_vs_full_rate']=last/overall if overall else 0.0
    return {'patient_id':patient['patient_id'],'as_of':cutoff,'values':{k:v[k] for k in FEATURES},
            'missing_features':[k for k in FEATURES if v[k] is None], 'feature_evidence':evidence,
            'input_record_digest':digest(sorted((r['record_id'],digest(r)) for r in rows)),
            'latest_feature_arrival':max((r['available_date'] for r in rows),default=None),
            'latest_feature_source_event':max((r['event_date'] for r in rows),default=None),
            'source_changes':blocks,
            'lab_time_basis':'Source dates describe available records, not verified physiological measurement times.'}


def matrix(rows,names):return np.array([[np.nan if r['values'][k] is None else r['values'][k] for k in names] for r in rows])


def observed_training_features(rows,names,training_mask):
    """Select available columns using training observations only, never outcomes."""
    present=np.any(np.isfinite(matrix(rows,names)[training_mask]),axis=0)
    retained=[name for name,keep in zip(names,present) if keep]
    omitted=[name for name,keep in zip(names,present) if not keep]
    if not retained:raise ValueError('No observed training features')
    return retained,omitted


def metrics(y,p):
    y=np.asarray(y);p=np.clip(np.asarray(p),1e-9,1-1e-9)
    return {'patients':len(y),'observed_endpoints':int(y.sum()),'brier':float(brier_score_loss(y,p)),
            'log_loss':float(log_loss(y,p,labels=[0,1])),
            'auroc':float(roc_auc_score(y,p)) if len(set(y))==2 else None,
            'average_precision':float(average_precision_score(y,p)) if y.sum() else None,
            'mean_predicted':float(p.mean()),'observed_rate':float(y.mean())}


def grouped_split(patient):
    identity=patient.get('household_id') or patient['patient_id']
    bucket=int(hashlib.sha256(('agere-forecast/1:'+identity).encode()).hexdigest()[:8],16)%100
    return 'train' if bucket<70 else 'calibration' if bucket<85 else 'test'


def fit(stack_path, output, feature_cutoff, label_through, target):
    stack_path,output=Path(stack_path),Path(output)
    if output.exists():raise ValueError('Fresh model artifact directory required')
    meta=json.loads((stack_path/'run.json').read_text());asof=meta['snapshot']['as_of']
    if not iso(feature_cutoff) or not iso(label_through) or not feature_cutoff<label_through<=asof:raise ValueError('Valid past feature/outcome cutoffs required')
    atom={'kind':'record',**target,'window':{'basis':'source_event','start':feature_cutoff,'end':label_through}}
    validate({**empty(),'all':[atom]},asof)
    start=time.perf_counter();db=sqlite3.connect(stack_path/'stack.sqlite3')
    patients={pid:json.loads(raw) for pid,raw in db.execute('SELECT id,payload FROM patients ORDER BY id')}
    records=defaultdict(list)
    for pid,raw in db.execute('SELECT patient,payload FROM records ORDER BY id'):records[pid].append(json.loads(raw))
    db.close()
    link=json.loads((stack_path/'annotation-link.json').read_text());ep=Path(link['path'])
    em=json.loads((ep/'run.json').read_text())
    if em['source_run']!=meta['source_run'] or em['model_identity']!=link['model_identity']:raise ValueError('Reader snapshot mismatch')
    db=sqlite3.connect(ep/'evidence.sqlite3');annotations=defaultdict(list)
    for ident,pid,concept,raw in db.execute("SELECT id,patient,concept,payload FROM evidence WHERE kind='text'"):
        annotations[pid].append({'id':ident,'concept':concept,'payload':json.loads(raw)})
    db.close()
    training=[];targets=[];splits=[];excluded=Counter()
    for pid,p in patients.items():
        if patient_value(p,'age',feature_cutoff) is None or not available(records[pid],feature_cutoff):
            excluded['not_observed_or_not_born_at_feature_cutoff']+=1;continue
        f=features(p,records[pid],annotations[pid],feature_cutoff)
        label_records=[r for r in records[pid] if r['source_table']==target['stream'] and iso(r.get('event_date'))
                       and feature_cutoff<r['event_date']<=label_through and iso(r.get('available_date')) and r['available_date']<=asof
                       and all(compare(record_value(r,c['field']),c['op'],c['value']) is True for c in target['conditions'])]
        f.update(split=grouped_split(p),label=bool(label_records),label_source_ids=[r['record_id'] for r in label_records])
        training.append(f);targets.append(int(bool(label_records)));splits.append(f['split'])
    y=np.array(targets);splits=np.array(splits)
    for split in ('train','calibration','test'):
        if len(set(y[splits==split]))<2:raise ValueError('Both observed endpoint classes required in '+split)
    output.mkdir(parents=True)
    report={'schema':VERSION,'synthetic':True,'source_digest':meta['source_digest'],'source_run':meta['source_run'],
            'snapshot':meta['snapshot'],'feature_cutoff':feature_cutoff,'label_through':label_through,'label_arrival_cutoff':asof,
            'target':target,'target_meaning':'At least one matching observed record with source-event date in the future label window.',
            'feature_code':feature_code(),'reader_model_identity':link['model_identity'],
            'feature_names':FEATURES,'splits':{s:{'patients':int(sum(splits==s)),'observed_endpoints':int(y[splits==s].sum())} for s in set(splits)},
            'excluded':dict(excluded),'models':{},'clinically_validated':False,'deployment_status':'research_only',
            'temporal_holdout_available':False,'limitations':LIMITATIONS,'new_paid_api_calls':0,
            'sources':['https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html',
                       'https://arxiv.org/html/1207.5578v3#S3.SS2']}
    candidates={
        'logistic':(BASE_FEATURES,make_pipeline(SimpleImputer(strategy='median',add_indicator=True,keep_empty_features=True),
                                               StandardScaler(),LogisticRegression(C=1,max_iter=1000))),
        'boosted':(BASE_FEATURES,HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=7,min_samples_leaf=40,
                   l2_regularization=2.0,learning_rate=.05,early_stopping=False,random_state=1729)),
        'boosted_blocks':(FEATURES,HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=7,min_samples_leaf=40,
                   l2_regularization=2.0,learning_rate=.05,early_stopping=False,random_state=1729))}
    bundles={};test_predictions={};tr=splits=='train';cal=splits=='calibration';test=splits=='test'
    report['test_training_prevalence_baseline']=metrics(y[test],np.full(sum(test),float(y[tr].mean())))
    for name,(names,model) in candidates.items():
        names,omitted=observed_training_features(training,names,tr)
        x=matrix(training,names);model.fit(x[tr],y[tr])
        raw=np.clip(model.predict_proba(x[cal])[:,1],1e-7,1-1e-7)
        calibration=LogisticRegression(C=1,max_iter=1000).fit(np.log(raw/(1-raw)).reshape(-1,1),y[cal])
        rawtest=model.predict_proba(x[test])[:,1];bounded=np.clip(rawtest,1e-7,1-1e-7)
        predicted=calibration.predict_proba(np.log(bounded/(1-bounded)).reshape(-1,1))[:,1]
        bundle={'features':names,'model':model,'calibration':calibration,'feature_code':feature_code()}
        data=pickle.dumps(bundle);(output/(name+'.pkl')).write_bytes(data);bundles[name]=bundle
        report['models'][name]={'weight_sha256':hashlib.sha256(data).hexdigest(),'test_raw':metrics(y[test],rawtest),
                               'test_calibrated':metrics(y[test],predicted),'feature_count':len(names),
                               'omitted_all_missing_training_features':omitted,
                               'selection':'Retained research candidate; no automatic winner or clinical promotion.'}
        test_predictions[name]=iter(map(float,predicted))
    with (output/'training-features.jsonl').open('w') as f:
        for row in training:f.write(json.dumps(row,allow_nan=False)+'\n')
    with (output/'test-predictions.jsonl').open('w') as f:
        for row in training:
            if row['split']=='test':f.write(json.dumps({'patient_id':row['patient_id'],'label':row['label'],
                  'models':{name:next(values) for name,values in test_predictions.items()}})+'\n')
    del training
    future=[features(p,records[pid],annotations[pid],asof) for pid,p in patients.items()
            if patient_value(p,'age',asof) is not None and available(records[pid],asof)]
    for name,bundle in bundles.items():
        raw=np.clip(bundle['model'].predict_proba(matrix(future,bundle['features']))[:,1],1e-7,1-1e-7)
        p=bundle['calibration'].predict_proba(np.log(raw/(1-raw)).reshape(-1,1))[:,1]
        for row,value in zip(future,p):row.setdefault('model_scores',{})[name]=float(value)
    with (output/'predictions.jsonl').open('w') as f:
        for row in future:f.write(json.dumps(row,allow_nan=False)+'\n')
    report['prediction_as_of']=asof
    report['prediction_horizon_days']=(date.fromisoformat(label_through)-date.fromisoformat(feature_cutoff)).days
    report['prediction_through']=(date.fromisoformat(asof)+timedelta(days=report['prediction_horizon_days'])).isoformat()
    report['patients_predicted']=len(future);report['elapsed_seconds']=time.perf_counter()-start
    report['predictions_sha256']=hashlib.sha256((output/'predictions.jsonl').read_bytes()).hexdigest()
    (output/'model-card.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    (stack_path/'forecast-link.json').write_text(json.dumps({'path':str(output.resolve()),'source_digest':meta['source_digest'],
                                                         'model_card_sha256':hashlib.sha256((output/'model-card.json').read_bytes()).hexdigest()},indent=2))
    return report


def forecast(path, patient_ids, model='all', limit=10):
    path=Path(path);link=json.loads((path/'forecast-link.json').read_text());root=Path(link['path'])
    card_bytes=(root/'model-card.json').read_bytes();card=json.loads(card_bytes)
    meta=json.loads((path/'run.json').read_text())
    if card['feature_code']!=feature_code() or card['source_digest']!=meta['source_digest'] or card['snapshot']!=meta['snapshot']:
        raise ValueError('Forecast model/source code or snapshot changed; rebuild required')
    if hashlib.sha256(card_bytes).hexdigest()!=link['model_card_sha256']:raise ValueError('Forecast manifest changed')
    raw=(root/'predictions.jsonl').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=card['predictions_sha256']:raise ValueError('Forecast predictions changed')
    if model!='all' and model not in card['models']:raise ValueError('Unregistered forecast model')
    ids=set(patient_ids);rows=[json.loads(r) for r in raw.splitlines() if r]
    rows=[r for r in rows if r['patient_id'] in ids]
    sort_model='boosted_blocks' if model=='all' else model
    rows.sort(key=lambda r:(-r['model_scores'][sort_model],r['patient_id']))
    means={name:float(np.mean([r['model_scores'][name] for r in rows])) if rows else None for name in card['models']}
    return {'operator':'research_forecast','synthetic':True,'status':'research_predictions_available',
            'target':card['target'],'target_meaning':card['target_meaning'],'models':card['models'],
            'as_of':card['prediction_as_of'],'through':card['prediction_through'],
            'patients_evaluated':len(rows),'input_patients':len(ids),'patients_without_prediction':len(ids)-len(rows),
            'mean_model_scores':means,'patient_preview_order':sort_model+' research score; not a clinical priority list',
            'patients':rows[:limit],'model_card':str(root/'model-card.json'),'model_card_sha256':link['model_card_sha256'],
            'clinical_validity_established':False,'clinical_execution_authorized':False,
            'new_paid_api_calls':0,'limitations':LIMITATIONS}
