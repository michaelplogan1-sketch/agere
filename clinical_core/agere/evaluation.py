"""One-time held-out action-head audit. Prediction input is target-free; labels loaded afterwards."""
import argparse,json,time
from pathlib import Path
import numpy as np
import joblib
from scipy.special import softmax
from .training import ActionSpecialist,feature_text,neural_text,numeric_features,metrics
from .warehouse import lines
from .tensor_train import TensorTrain
from ..contracts import digest

ROOT=Path(__file__).resolve().parents[2]

def evaluate(training,models,output):
    if output.exists():raise ValueError('Do not silently overwrite a held-out audit')
    model=ActionSpecialist(models);classes=model.manifest['classes'];predictions={};start=time.perf_counter()
    rows=list(lines(training/'inference/test.jsonl'))
    # Export may wrap the target-free input or store it directly.
    inputs=[r.get('input',r) for r in rows]
    if any('target' in r for r in rows):raise ValueError('Inference file contains targets')
    lexical=model.pipeline['head'].decision_function(model.pipeline['vectorizer'].transform([feature_text(x) for x in inputs]))
    predictions['lexical']=softmax(lexical/model.manifest['models']['lexical']['temperature'],axis=1)
    numeric=joblib.load(models/'numeric.joblib');x=numeric['scaler'].transform(np.stack([numeric_features(i) for i in inputs])).clip(-5,5)
    predictions['numeric_linear']=numeric['linear'].predict_proba(x)
    import torch
    tt=TensorTrain(x.shape[1],len(classes));tt.load_state_dict(torch.load(models/'tensor-train.pt',map_location='cpu',weights_only=True));tt.eval()
    with torch.inference_mode():predictions['tensor_train']=softmax(tt(torch.tensor(x,dtype=torch.float32)).numpy(),axis=1)
    from ..local_encoders import LocalEncoder
    encoder=LocalEncoder(ROOT/'models/bioclinical-modernbert',pooling='mean',cache=ROOT/'artifacts/agere-full/encoder-cache.sqlite3')
    vectors=[]
    try:
        for i,inp in enumerate(inputs):
            windows=encoder.windows(neural_text(inp));vectors.append(encoder.encode([w['text'] for w in windows]).mean(axis=0))
            if i%500==0:print(json.dumps({'phase':'test_features','completed':i,'total':len(inputs)}),flush=True)
        head=joblib.load(models/'bioclinical-head.joblib')
        predictions['bioclinical']=softmax(head.decision_function(np.stack(vectors))/model.manifest['models']['bioclinical']['temperature'],axis=1)
    finally:encoder.close()
    # Only now open the evaluator targets and family provenance.
    truth=list(lines(training/'test.jsonl'));families={r['family_id'] for r in truth}
    used={r['family_id'] for split in ('train','calibration') for r in lines(training/f'{split}.jsonl')}
    if families & used:raise ValueError('Held-out family overlap')
    if len(truth)!=len(rows):raise ValueError('Prediction/label count mismatch')
    for inp,r in zip(inputs,truth):
        if digest(inp)!=digest(r['input']):raise ValueError('Input alignment mismatch')
    y=[r['target']['allowed_actions'][0] for r in truth]
    results={name:metrics(y,p,classes) for name,p in predictions.items()}
    result={'synthetic':True,'model_id':model.manifest['model_id'],'family_disjoint':True,'test_rows':len(rows),
            'test_families':len(families),'models':results,'elapsed_seconds':time.perf_counter()-start,
            'scope':'Agreement with machine-authored action targets, not independent clinical accuracy',
            'limitations':['Same generator mechanisms and templates','No model or threshold changed using this audit','No treatment benefit or real-world cost claim']}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--training',type=Path,required=True);p.add_argument('--models',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();evaluate(a.training,a.models,a.output)
