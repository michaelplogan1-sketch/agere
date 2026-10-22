"""Train and serve economical, source-bound semantic heads in shadow mode.

No pickle, foundation-weight tuning, generated reviews or automatic promotion.
Validation labels are read only after fitting and calibration have finished.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import math
import time

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import softmax
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits

from ...contracts import digest
from . import semantic_reader as reader

VERSION='semantic-student/1'
PARTITIONS=('train','calibration','validation')
POLICY={'minimum_accepted_rows':30,'minimum_accepted_families':5,
        'maximum_empirical_error_upper_95':0.10,'minimum_coverage':0.25,
        'temperature_bounds':[0.25,4.0], 'automatic_promotion':False}
PROVIDERS={'student_lexical':'lexical','student_minilm':'minilm','student_bioclinical':'bioclinical'}
ENCODERS={'minilm':'minilm-l6-v2','bioclinical':'bioclinical-modernbert'}  # frozen encoders under models/ (mean pooling)
# Students trained by tools/train_semantic_students_v2.py from the same authored curriculum; used when a stack has no
# explicit semantic-student-link.json (a stack link always wins).
DEFAULT_DIR=Path(__file__).resolve().parents[3]/'artifacts'/'agere-semantic-students-v2'


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_identity():
    from ... import local_encoders
    return digest([sha(__file__),sha(local_encoders.__file__)])


def model_text(p):
    text=p.get('text');a,b=p.get('target_start'),p.get('target_end')
    if not isinstance(text,str) or not text.strip() or type(a) is not int or type(b) is not int or not 0<=a<b<=len(text):
        raise ValueError('Exact nonempty source span required')
    if text[a:b]!=p.get('target_quote') or not isinstance(p.get('concept'),str):raise ValueError('Source target mismatch')
    if p.get('synthetic') is not True:raise ValueError('Synthetic-only student')
    if p.get('context_complete') is not True:raise ValueError('Student requires complete received text; route partial context to another reader')
    # Only source context enters the model. IDs, partitions, reviews and labels
    # never enter features. Escaping prevents literal marker collisions.
    escaped=lambda s:s.replace('[TARGET]','[literal target]').replace('[/TARGET]','[literal end target]')
    result='Concept: '+p['concept']+'\nSource: '+escaped(text[:a])+' [TARGET] '+escaped(text[a:b])+' [/TARGET] '+escaped(text[b:])
    if len(result.encode())>12000:raise ValueError('Student text bound exceeded; no truncation')
    return result


def validate_rows(rows):
    if not rows:raise ValueError('No training rows')
    seen=set();groups=defaultdict(set);inputs=defaultdict(set)
    for r in rows:
        if r['id'] in seen:raise ValueError('Duplicate example ID')
        seen.add(r['id']);part=r['partition']
        if part not in PARTITIONS:raise ValueError('Unknown partition')
        if set(r['labels'])!=set(reader.AXES) or any(r['labels'][a] not in reader.AXES[a] for a in reader.AXES):raise ValueError('Invalid semantic labels')
        text=model_text(r['input'])
        if not r['patient_family'] or not r['source_families']:raise ValueError('Explicit source families required')
        for key in [('patient',r['patient_family']),('source',r['source_hash']),('input',digest(text))]+[('template',x) for x in r['source_families']]:groups[key].add(part)
        inputs[text].add(digest(r['labels']))
    if any(len(s)>1 for s in groups.values()):raise ValueError('Related examples cross partitions')
    if any(len(s)>1 for s in inputs.values()):raise ValueError('Identical inputs have conflicting labels')
    for part in PARTITIONS:
        if not any(r['partition']==part for r in rows):raise ValueError('All three partitions required')
    for axis in reader.AXES:
        if len({r['labels'][axis] for r in rows if r['partition']=='train'})<2:raise ValueError('At least two training classes per axis required')


def logits(x,coef,intercept,classes):
    z=np.asarray(x@coef.T+intercept)
    return np.column_stack((-z[:,0]/2,z[:,0]/2)) if len(classes)==2 and z.shape[1]==1 else z


def temperature(z,labels,classes):
    if any(label not in classes for label in labels):return 1.0
    y=np.array([classes.index(label) for label in labels])
    def loss(log_t):
        p=softmax(z/math.exp(log_t),axis=1)
        return float(-np.log(np.clip(p[np.arange(len(y)),y],1e-12,1)).mean())
    result=minimize_scalar(loss,bounds=tuple(math.log(t) for t in POLICY['temperature_bounds']),method='bounded')
    return float(math.exp(result.x)) if result.success else 1.0


def predictions(features,heads,arrays):
    outputs=[{'labels':{},'probabilities':{}} for _ in range(features.shape[0])]
    for axis,h in heads.items():
        probs=softmax(logits(features,arrays[axis+'_coef'],arrays[axis+'_intercept'],h['classes'])/h['temperature'],axis=1)
        for row,p in zip(outputs,probs):
            row['labels'][axis]=h['classes'][int(p.argmax())]
            row['probabilities'][axis]={label:float(p[h['classes'].index(label)]) if label in h['classes'] else 0.0 for label in reader.AXES[axis]}
    for r in outputs:r['confidence']=min(max(p.values()) for p in r['probabilities'].values())
    return outputs


def error_upper(errors,n):
    if not n:return 1.0
    z=1.959963984540054;p=errors/n
    return (p+z*z/(2*n)+z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)


def selectable(pred):return 'unknown' not in pred['labels'].values()


def calibrate(rows,preds):
    # Threshold selected on calibration only. Wilson bounds here are descriptive
    # after threshold selection, not a distribution-free guarantee.
    for threshold in sorted({p['confidence'] for p in preds if selectable(p)}):
        pairs=[(r,p) for r,p in zip(rows,preds) if selectable(p) and p['confidence']>=threshold]
        n=len(pairs);errors=sum(r['labels']!=p['labels'] for r,p in pairs)
        families=len({r['patient_family'] for r,p in pairs})
        if n>=POLICY['minimum_accepted_rows'] and families>=POLICY['minimum_accepted_families'] and n/len(rows)>=POLICY['minimum_coverage'] and error_upper(errors,n)<=POLICY['maximum_empirical_error_upper_95']:
            return {'threshold':threshold,'accepted':n,'errors':errors,'families':families,'coverage':n/len(rows),
                    'error_upper_95':error_upper(errors,n),'status':'empirical_threshold_selected'}
    return {'threshold':None,'accepted':0,'errors':0,'families':0,'coverage':0,'error_upper_95':1.0,'status':'insufficient_calibration_evidence'}


def metrics(rows,preds,threshold):
    axes={}
    for axis,labels in reader.AXES.items():
        confusion={label:dict(Counter(p['labels'][axis] for r,p in zip(rows,preds) if r['labels'][axis]==label)) for label in labels}
        recalls=[confusion[y].get(y,0)/sum(confusion[y].values()) for y in labels if confusion[y]]
        axes[axis]={'accuracy':sum(r['labels'][axis]==p['labels'][axis] for r,p in zip(rows,preds))/len(rows),
                    'balanced_accuracy_over_observed_classes':sum(recalls)/len(recalls),'confusion':confusion}
    accepted=[(r,p) for r,p in zip(rows,preds) if threshold is not None and selectable(p) and p['confidence']>=threshold]
    errors=sum(r['labels']!=p['labels'] for r,p in accepted)
    return {'rows':len(rows),'source_families':len({r['patient_family'] for r in rows}),
        'exact_match':sum(r['labels']==p['labels'] for r,p in zip(rows,preds))/len(rows),'axes':axes,
        'selected_rows':len(accepted),'selected_errors':errors,'selected_coverage':len(accepted)/len(rows),
        'selected_precision':1-errors/len(accepted) if accepted else None,'selected_error_upper_95':error_upper(errors,len(accepted))}


def fit(rows,output,*,kind,supervision,provenance):
    if kind not in ('lexical',*ENCODERS):raise ValueError('Unknown student representation')
    if supervision not in ('authored_synthetic_development','source_reviewed_snapshot','machine_teacher_snapshot'):raise ValueError('Explicit supervision type required')
    validate_rows(rows)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter();encoder=None
    parts={p:[r for r in rows if r['partition']==p] for p in PARTITIONS}
    texts={p:[model_text(r['input']) for r in parts[p]] for p in PARTITIONS}
    representations={};arrays={};heads={}
    try:
        if kind=='lexical':
            vectorizer=TfidfVectorizer(analyzer='char_wb',ngram_range=(3,5),max_features=30000,sublinear_tf=True)
            representations['train']=vectorizer.fit_transform(texts['train'])
            representations.update({p:vectorizer.transform(texts[p]) for p in PARTITIONS[1:]})
            feature={'kind':kind,'vocabulary':{k:int(v) for k,v in vectorizer.vocabulary_.items()}}
            arrays['idf']=vectorizer.idf_
        else:
            from ...local_encoders import LocalEncoder
            encoder=LocalEncoder(reader.ROOT/'models'/ENCODERS[kind],pooling='mean',cache=output/'encoder-cache.sqlite3')
            feature={'kind':kind,'encoder_path':str(encoder.path),'encoder_identity':encoder.identity,'pooling':'mean'}
            representations={p:encoder.encode(texts[p],batch_size=16) for p in PARTITIONS}
        with threadpool_limits(limits=4):
            for axis in reader.AXES:
                head=LogisticRegression(C=1.0,class_weight='balanced',max_iter=2000,random_state=812)
                head.fit(representations['train'],[r['labels'][axis] for r in parts['train']])
                classes=list(head.classes_);arrays[axis+'_coef']=head.coef_;arrays[axis+'_intercept']=head.intercept_
                z=logits(representations['calibration'],head.coef_,head.intercept_,classes)
                heads[axis]={'classes':classes,'temperature':temperature(z,[r['labels'][axis] for r in parts['calibration']],classes)}
        cp=predictions(representations['calibration'],heads,arrays)
        selection=calibrate(parts['calibration'],cp)
        vp=predictions(representations['validation'],heads,arrays)
        validation=metrics(parts['validation'],vp,selection['threshold'])
        missing={a:sorted(set(reader.AXES[a])-set(heads[a]['classes'])) for a in reader.AXES}
        reasons=['Explicit clinical deployment authorization and independent representative validation are not supplied.']
        if supervision=='authored_synthetic_development':reasons.append('Sibling authored paraphrases establish development behavior only; no clinician-reviewed or real-world accuracy claim.')
        if supervision=='machine_teacher_snapshot':reasons.append('Targets are unreviewed machine suggestions. These scores measure teacher imitation, not clinical accuracy. Outer evaluation families remain untouched.')
        if any(missing.values()):reasons.append('Some task classes were absent from training.')
        if selection['threshold'] is None:reasons.append('Calibration did not support selective local acceptance.')
        if validation['selected_coverage']<POLICY['minimum_coverage'] or validation['selected_error_upper_95']>POLICY['maximum_empirical_error_upper_95']:
            reasons.append('Untouched validation did not meet declared selective accuracy/coverage criteria.')
        weights=output/'weights.npz';np.savez_compressed(weights,**arrays)
        feature_path=output/'features.json';feature_path.write_text(json.dumps(feature,sort_keys=True)+'\n')
        data=output/'examples.jsonl';data.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
        scored=output/'validation-predictions.jsonl'
        scored.write_text(''.join(json.dumps({'id':r['id'],'expected':r['labels'],**p},sort_keys=True)+'\n' for r,p in zip(parts['validation'],vp)))
        import sklearn,scipy
        manifest={'schema':VERSION,'synthetic':True,'kind':kind,'supervision':supervision,'reader_identity':reader.identity(),
            'code_identity':code_identity(),'provenance':provenance,'counts':{p:len(parts[p]) for p in PARTITIONS},
            'dataset_digest':digest(rows),'heads':heads,'policy':POLICY,'calibration':selection,
            'score_reference':'unreviewed_machine_teacher' if supervision=='machine_teacher_snapshot' else 'supplied_development_labels',
            'calibration_metrics':metrics(parts['calibration'],cp,selection['threshold']),'validation':validation,
            'unrepresented_training_classes':missing,'files':{p.name:sha(p) for p in (weights,feature_path,data,scored)},
            'runtime':{'numpy':np.__version__,'sklearn':sklearn.__version__,'scipy':scipy.__version__},
            'training_seconds':time.perf_counter()-started,'model_artifact_bytes':weights.stat().st_size+feature_path.stat().st_size,
            'encoder_stats':dict(encoder.stats) if encoder else None,'foundation_weights_updated':False,
            'release':{'status':'shadow_only','automatic_acceptance':False,'reasons':reasons},
            'limits':['Scores measure supplied source-language labels, not disease knowledge, clinical eligibility, quality of care or clinical outcomes.',
                'No retrieval is performed: these are interpretation results given exact target context, not retrieval recall.',
                'Confidence is calibrated within this development distribution; out-of-distribution confidence can be wrong.',
                'Empirical threshold selection and correlated paraphrases do not provide a clinical error guarantee.',
                'No universal model winner is selected from these development scores.'], 'new_paid_api_calls':0}
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        return manifest
    finally:
        if encoder:encoder.close()


def verify(path):
    path=Path(path);m=json.loads((path/'manifest.json').read_text())
    if m['schema']!=VERSION or m['code_identity']!=code_identity() or m['reader_identity']!=reader.identity():raise ValueError('Student code or source contract changed; rebuild required')
    if set(m['files'])!={'weights.npz','features.json','examples.jsonl','validation-predictions.jsonl'}:raise ValueError('Invalid student artifact files')
    for name,h in m['files'].items():
        if sha(path/name)!=h:raise ValueError('Student artifact checksum mismatch: '+name)
    return m


class Student:
    def __init__(self,path):
        self.path=Path(path);self.manifest=verify(path);self.identity=sha(self.path/'manifest.json');self.encoder=None
        self.feature=json.loads((self.path/'features.json').read_text())
        with np.load(self.path/'weights.npz',allow_pickle=False) as z:self.arrays={k:z[k] for k in z.files}
        if any(not np.isfinite(a).all() for a in self.arrays.values()):raise ValueError('Nonfinite model parameters')
        if self.feature['kind']=='lexical':
            self.vectorizer=TfidfVectorizer(analyzer='char_wb',ngram_range=(3,5),sublinear_tf=True,vocabulary=self.feature['vocabulary'])
            self.vectorizer.idf_=self.arrays['idf']
        elif self.feature['kind'] in ENCODERS:
            from ...local_encoders import LocalEncoder
            self.encoder=LocalEncoder(self.feature['encoder_path'],pooling=self.feature['pooling'],cache=self.path/'inference-cache.sqlite3')
            if self.encoder.identity!=self.feature['encoder_identity']:
                self.encoder.close();raise ValueError('Frozen encoder identity changed')
        else:raise ValueError('Unsupported student feature kind')

    def close(self):
        if self.encoder:self.encoder.close()

    def predict(self,context):
        started=time.perf_counter();text=model_text(context)
        x=self.encoder.encode([text]) if self.encoder else self.vectorizer.transform([text])
        p=predictions(x,self.manifest['heads'],self.arrays)[0];threshold=self.manifest['calibration']['threshold']
        p.update(local_seconds=time.perf_counter()-started,
            passes_development_threshold=threshold is not None and selectable(p) and p['confidence']>=threshold,
            automatic_acceptance=False,release_status='shadow_only')
        return p


def attach(stack,models):
    if not set(models)<=set(PROVIDERS) or not {'student_lexical','student_minilm'}<=set(models):raise ValueError('Attach both compared local students (BioClinical optional)')
    links={}
    for provider,path in models.items():
        path=Path(path).resolve();m=verify(path)
        if m['kind']!=PROVIDERS[provider]:raise ValueError('Student provider kind mismatch')
        links[provider]={'path':str(path),'manifest_sha256':sha(path/'manifest.json')}
    target=Path(stack)/'semantic-student-link.json';tmp=target.with_suffix('.tmp')
    tmp.write_text(json.dumps(links,indent=2)+'\n');tmp.replace(target)
    return links


def linked(stack,provider):
    if provider not in PROVIDERS:raise ValueError('Unknown local student')
    f=Path(stack)/'semantic-student-link.json'
    links=json.loads(f.read_text()) if f.exists() else {}
    if provider not in links:
        default=DEFAULT_DIR/PROVIDERS[provider]
        if not (default/'manifest.json').exists():raise ValueError('No trained local student attached for '+provider)
        links[provider]={'path':str(default),'manifest_sha256':sha(default/'manifest.json')}
    link=links[provider];path=Path(link['path'])
    if sha(path/'manifest.json')!=link['manifest_sha256']:raise ValueError('Attached student manifest changed')
    m=verify(path)
    if m['kind']!=PROVIDERS[provider]:raise ValueError('Wrong model linked to student provider')
    if m['supervision']=='source_reviewed_snapshot':
        from .semantic_learning import load_snapshot
        dataset=Path(m['provenance']['reviewed_dataset'])
        if sha(dataset/'manifest.json')!=m['provenance']['snapshot_sha256']:raise ValueError('Reviewed training snapshot changed')
        load_snapshot(stack,dataset)
    if m['supervision']=='machine_teacher_snapshot':
        from .source_distillation import load_snapshot
        dataset=Path(m['provenance']['machine_dataset'])
        if sha(dataset/'manifest.json')!=m['provenance']['snapshot_sha256']:raise ValueError('Machine-supervision snapshot changed')
        rows,_=load_snapshot(stack,dataset)
        if digest(rows)!=m['dataset_digest']:raise ValueError('Student does not match current machine-supervision snapshot')
    return path,m,link['manifest_sha256']


def status(stack):
    models={}
    for provider in PROVIDERS:
        try:path,m,h=linked(stack,provider)
        except ValueError:continue  # not attached / not trained: the other students still report
        models[provider]={'model_identity':h,'score_reference':m.get('score_reference','supplied_development_labels'),**{k:m[k] for k in ('kind','supervision','counts','calibration','validation','release','limits','model_artifact_bytes','foundation_weights_updated')}}
    return {'synthetic':True,'models':models,'status':'shadow_only' if models else 'not_attached','new_paid_api_calls':0}


def read(stack,target,provider):
    record,text,meta=reader.source(stack,target);p=reader.packet(record,text,target,meta['snapshot']['as_of'])
    model_text(p)  # Validate before cache/model loading.
    path,m,model_id=linked(stack,provider);rid=reader.identity()
    sid={k:meta[k] for k in ('source_digest','snapshot')}
    key=digest([rid,sid,target,digest(record),provider,model_id,p])
    database=reader.db(stack)
    try:
        cached=database.execute('SELECT payload FROM readings WHERE id=?',(key,)).fetchone()
        if cached:return {**json.loads(cached[0]),'cache_hit':True,'new_paid_api_calls':0}
        started=time.perf_counter();student=Student(path)
        try:result=student.predict(p)
        finally:student.close()
        distributions={a:{'choice':result['labels'][a],'probabilities':result['probabilities'][a]} for a in reader.AXES}
        receipt={'schema':reader.VERSION,'id':key,'synthetic':True,'target':target,'source_hash':digest(record),
            'source_identity':sid,'reader_identity':rid,'model_identity':model_id,'provider':provider,'context':p,
            'reading':{'labels':result['labels'],'distributions':distributions,'semantic_validation':'pending','clinical_authority':False,'automatic_acceptance':False},
            'requirement_bridge':reader.implications(result['labels'],target['concept']),
            'student':{'supervision':m['supervision'],'release':m['release'],'confidence':result['confidence'],'passes_development_threshold':result['passes_development_threshold']},
            'usage':{'local_seconds_including_load':time.perf_counter()-started,'prediction_seconds':result['local_seconds'],'paid_api_calls':0,'hosted_tokens':0},
            'cache_hit':False,'new_paid_api_calls':0,'current_clinical_state_established':False,'training_label_created':False}
        with database:database.execute('INSERT OR IGNORE INTO readings VALUES (?,?,?)',(key,target['patient_id'],json.dumps(receipt,allow_nan=False)))
        return receipt
    finally:database.close()
