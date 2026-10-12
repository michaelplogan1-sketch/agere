"""Train real specialist heads on family-separated generator teaching labels."""
from __future__ import annotations
import argparse,json,time,re,hashlib
from pathlib import Path
from collections import Counter
import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score,balanced_accuracy_score,log_loss
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from scipy.optimize import minimize_scalar
from scipy.special import softmax
from .warehouse import lines,text_of
from .tensor_train import fit_tensor
from ..contracts import digest

ROOT=Path(__file__).resolve().parents[2]
# No patient/source IDs, scenario, target, hidden family or dates-as-identifiers in features.
FIELDS={'source_table','status','text','extracted_text','test','value','unit','drug_class','drug','dose',
        'days_supply','transaction_type','payment_channel','difficulty_paying_medical_bills','income_band_usd',
        'food_security','transport','caregiver_available','event','reason','service','procedure','document_type',
        'report_status','extraction_method','extraction_confidence','assertion_status','problems','identity_status'}

def sanitize(value):
    if isinstance(value,str):
        value=re.sub(r'\b(?:ECO|OBS|SYN|CLN|ORG|SYS|RX|DSP|TX|HH|TASK)-[\w-]+','[ID]',value)
        value=re.sub(r'(?m)^(?:Patient:|DOB:|Patient ID:|Facility:|Signed:).*$', '',value)
    return value

def feature_text(inp):
    records=inp['observed_records'];cut=inp['as_of'];chunks=[]
    for r in sorted(records,key=lambda x:(x['event_date'],x['record_id'])):
        if r['event_date']>cut or r['available_date']>cut:raise ValueError('Future evidence in training input')
        clean={k:sanitize(v) for k,v in r.items() if k in FIELDS}
        clean['age_days']=(__import__('datetime').date.fromisoformat(cut)-__import__('datetime').date.fromisoformat(r['event_date'])).days
        chunks.append(json.dumps(clean,sort_keys=True))
    return '\n'.join(chunks)

def numeric_features(inp):
    rs=inp['observed_records']
    return np.array([len(rs),sum(r['source_table']=='documents' for r in rs),
        sum(r.get('payment_channel') in {'cash','discount_card'} for r in rs),
        sum(r.get('identity_status')!='matched' for r in rs),
        sum(r.get('transaction_type') in {'rejected','reversed'} for r in rs),
        sum(bool(r.get('supersedes_record_id')) for r in rs),
        sum(r['source_table']=='referrals' for r in rs),
        sum(r.get('difficulty_paying_medical_bills') is True for r in rs)],dtype=np.float32)

def neural_text(inp):
    # Explicit economical selection; full source features remain in lexical control.
    selected=[]
    for table in ('notes','documents'):
        rs=[r for r in inp['observed_records'] if r['source_table']==table and r.get('identity_status')=='matched' and text_of(r)]
        if rs:selected.append(max(rs,key=lambda r:(r['event_date'],r['available_date'],r['record_id'])))
    return '\n'.join(sanitize(text_of(r)) for r in selected) or 'No usable narrative available.'

def split_data(root):
    data={name:list(lines(root/f'{name}.jsonl')) for name in ('train','calibration')}
    families={name:{r['family_id'] for r in rows} for name,rows in data.items()}
    if families['train'] & families['calibration']:raise ValueError('Family leakage')
    # Test is not opened for fitting, tuning, label selection or model choice.
    for rows in data.values():
        for r in rows:
            if r['label_origin']!='generator_proposal' or r['label_review_status']!='unreviewed_synthetic':
                raise ValueError('Unexpected training-label provenance')
    return data

def metrics(y,probs,classes):
    labels=np.array(classes)[probs.argmax(1)]
    return {'accuracy':float(accuracy_score(y,labels)),'balanced_accuracy':float(balanced_accuracy_score(y,labels)),
            'log_loss':float(log_loss(y,probs,labels=classes)),'rows':len(y)}

def temperature(logits,targets):
    opt=minimize_scalar(lambda t:-np.log(np.maximum(softmax(logits/t,axis=1)[np.arange(len(targets)),targets],1e-12)).mean(),bounds=(.1,10),method='bounded')
    return float(opt.x)

def fit(root,output,*,bio=True):
    started=time.perf_counter();output.mkdir(parents=True,exist_ok=False)
    data=split_data(root);train,cal=data['train'],data['calibration']
    y={k:[r['target']['allowed_actions'][0] for r in rows] for k,rows in data.items()}
    if set(y['calibration'])-set(y['train']):raise ValueError('Calibration contains unseen action classes')
    texts={k:[feature_text(r['input']) for r in rows] for k,rows in data.items()}
    vector=TfidfVectorizer(ngram_range=(1,2),max_features=50000,sublinear_tf=True,min_df=2)
    xt=vector.fit_transform(texts['train']);xc=vector.transform(texts['calibration'])
    head=LogisticRegression(C=4,max_iter=600,class_weight='balanced').fit(xt,y['train'])
    joblib.dump({'vectorizer':vector,'head':head},output/'lexical.joblib')
    classes=head.classes_.tolist();indices={v:i for i,v in enumerate(classes)}
    ci=np.array([indices[v] for v in y['calibration']]);cal_logits=head.decision_function(xc)
    t=temperature(cal_logits,ci);probs=softmax(cal_logits/t,axis=1)
    reports={'lexical':{**metrics(y['calibration'],probs,classes),'temperature':t}}
    # Tensor train uses the same eight observable numeric summaries as its linear comparator.
    nt=np.stack([numeric_features(r['input']) for r in train]);nc=np.stack([numeric_features(r['input']) for r in cal])
    scaler=StandardScaler().fit(nt);nt=scaler.transform(nt).clip(-5,5);nc=scaler.transform(nc).clip(-5,5)
    linear=LogisticRegression(max_iter=500,class_weight='balanced').fit(nt,y['train'])
    reports['numeric_linear']=metrics(y['calibration'],linear.predict_proba(nc),classes)
    tensor,loss=fit_tensor(nt,[indices[v] for v in y['train']],len(classes),epochs=200)
    import torch
    with torch.inference_mode():tt=softmax(tensor(torch.tensor(nc,dtype=torch.float32)).numpy(),axis=1)
    reports['tensor_train']={**metrics(y['calibration'],tt,classes),'training_loss':loss,'rank':tensor.rank,
                              'parameters':sum(p.numel() for p in tensor.parameters())}
    torch.save(tensor.state_dict(),output/'tensor-train.pt');joblib.dump({'scaler':scaler,'linear':linear},output/'numeric.joblib')
    bio_identity=None;bio_stats={}
    if bio:
        from ..local_encoders import LocalEncoder
        encoder=LocalEncoder(ROOT/'models/bioclinical-modernbert',pooling='mean',cache=ROOT/'artifacts/agere-full/encoder-cache.sqlite3')
        try:
            matrices={}
            for split,rows in data.items():
                vectors=[]
                for i,row in enumerate(rows):
                    windows=encoder.windows(neural_text(row['input']),size=256,overlap=32)
                    vectors.append(encoder.encode([w['text'] for w in windows]).mean(axis=0))
                    if i%500==0:print(json.dumps({'phase':'clinical_encoder','split':split,'completed':i,'total':len(rows)}),flush=True)
                matrices[split]=np.stack(vectors)
            bio_head=LogisticRegression(C=4,class_weight='balanced',max_iter=800).fit(matrices['train'],y['train'])
            logits=bio_head.decision_function(matrices['calibration']);bt=temperature(logits,ci)
            reports['bioclinical']={**metrics(y['calibration'],softmax(logits/bt,axis=1),classes),'temperature':bt,
                'input_policy':'Latest matched note and document; all their token windows pooled; other records not encoded',
                'base_weights_updated':False,'trained_component':'multiclass action head'}
            joblib.dump(bio_head,output/'bioclinical-head.joblib');bio_identity=encoder.identity;bio_stats=dict(encoder.stats)
            np.savez_compressed(output/'clinical-features.npz',**matrices)
        finally:encoder.close()
    # Cheap deployable head selected on calibration; final evaluation must use untouched families.
    confidence=probs.max(1);correct=probs.argmax(1)==ci
    from .mathematics import route_search
    routing=route_search(confidence,correct,np.full(len(ci),.00001),np.full(len(ci),.005),max_error=.05)
    # Split-conformal set using calibration nonconformity. Same calibration also tuned temperature:
    # retain empirical sets only; do not claim nominal coverage until independent conformal split.
    q=float(np.quantile(1-probs[np.arange(len(ci)),ci],.9,method='higher'))
    manifest={'schema':'agere-action-specialists/1','synthetic':True,'label_origin':'unreviewed_synthetic_generator_proposal',
        'clinical_definition_approval':'User clinician approval in conversation; no fabricated row-level review',
        'counts':{k:len(v) for k,v in data.items()},'family_disjoint':True,'test_read':False,
        'classes':classes,'models':reports,'encoder_identity':bio_identity,'bio_compute':bio_stats,
        'route_search':routing,'empirical_set_quantile':q,'nominal_conformal_coverage_claimed':False,
        'source_hashes':{k:digest([(r['example_id'],r['input_hash'],r['target']) for r in v]) for k,v in data.items()},
        'elapsed_seconds':time.perf_counter()-started,
        'limitations':['Shared generator mechanisms/templates across splits','Labels are simulator proposals, not clinical truth',
                       'Only the first allowed action is the training target','Model action is a proposal requiring source support, not execution']}
    manifest['model_id']=digest(manifest)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False))
    integrity={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in output.iterdir() if f.suffix in {'.json','.joblib','.pt'}}
    (output/'integrity.json').write_text(json.dumps(integrity,indent=2))
    print(json.dumps({'output':str(output),'counts':manifest['counts'],'models':reports},indent=2),flush=True)
    return manifest

class ActionSpecialist:
    def __init__(self,path):
        self.path=Path(path);self.manifest=json.loads((self.path/'manifest.json').read_text())
        integrity=json.loads((self.path/'integrity.json').read_text())
        for name,sha in integrity.items():
            if Path(name).name!=name or hashlib.sha256((self.path/name).read_bytes()).hexdigest()!=sha: raise ValueError('Specialist artifact checksum mismatch')
        # Local, generated training artifacts only; do not load untrusted pickle files.
        self.pipeline=joblib.load(self.path/'lexical.joblib')
    def predict(self,inputs):
        features=self.pipeline['vectorizer'].transform([feature_text(x) for x in inputs])
        probs=softmax(self.pipeline['head'].decision_function(features)/self.manifest['models']['lexical']['temperature'],axis=1)
        threshold=self.manifest['route_search']['selected']['threshold'];classes=self.manifest['classes']
        return [{'proposed_action':classes[int(p.argmax())],'score':float(p.max()),
                 'candidate_set':[c for c,v in zip(classes,p) if 1-v<=self.manifest['empirical_set_quantile']],
                 'route':'local_proposal' if p.max()>=threshold and sum(1-p<=self.manifest['empirical_set_quantile'])==1 else 'specialist_or_frontier_review',
                 'model_id':self.manifest['model_id'],'evidence_entailment':'not_established',
                 'execution':'none'} for p in probs]

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--training',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--no-bio',action='store_true')
    a=p.parse_args();fit(a.training,a.output,bio=not a.no_bio)
