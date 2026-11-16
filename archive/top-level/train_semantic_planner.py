"""Fit a domain head on a frozen local encoder; never upload the curriculum."""
import hashlib
import json
import os
import time
from pathlib import Path

os.environ['HF_HUB_OFFLINE']='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
import joblib
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import accuracy_score, top_k_accuracy_score
from threadpoolctl import threadpool_limits

ROOT=Path(__file__).resolve().parent

def main():
    started=time.monotonic()
    baseline=ROOT/'artifacts/research-training'
    out=ROOT/'artifacts/research-training-semantic';out.mkdir(parents=True,exist_ok=True)
    encoder_path=ROOT/'models/minilm-l6-v2'
    encoder=SentenceTransformer(str(encoder_path),local_files_only=True,trust_remote_code=False,device='cpu')
    rows=[json.loads(line) for line in (baseline/'planner_training.jsonl').read_text().splitlines()]
    texts=[r['question'] for r in rows];labels=np.array([r['domain'] for r in rows]);classes=np.unique(labels)
    lengths=[len(encoder.tokenizer.encode(t,add_special_tokens=True,truncation=False)) for t in texts]
    if max(lengths)>encoder.max_seq_length:raise ValueError('Curriculum would be truncated')
    with threadpool_limits(limits=4):
        import torch
        torch.set_num_threads(4)
        embeddings=encoder.encode(texts,batch_size=32,normalize_embeddings=True,show_progress_bar=False)
        head=LogisticRegression(C=10,max_iter=400,random_state=29)
        scores=cross_val_predict(head,embeddings,labels,cv=StratifiedKFold(n_splits=6,shuffle=True,random_state=29),method='predict_proba')
        head.fit(embeddings,labels)
    joblib.dump(head,out/'planner.joblib',compress=3)
    card=json.loads((baseline/'planner_card.json').read_text())
    card.update(name='Frozen MiniLM with locally trained research-domain head',
        sha256=hashlib.sha256((out/'planner.joblib').read_bytes()).hexdigest(),
        actual_training='Logistic regression head trained locally on frozen MiniLM embeddings; encoder weights NOT fine-tuned',
        encoder={'path':str(encoder_path),'manifest_sha256':hashlib.sha256((encoder_path/'local_manifest.json').read_bytes()).hexdigest(),
            'model':'sentence-transformers/all-MiniLM-L6-v2','maximum_curriculum_tokens':max(lengths),'context_limit':encoder.max_seq_length},
        developmental_cross_validation={'folds':6,'top1':float(accuracy_score(labels,classes[scores.argmax(1)])),
            'top3':float(top_k_accuracy_score(labels,scores,k=3,labels=classes)),
            'caveat':'Same small related-question curriculum as lexical baseline; not external validation. Final head fitted on all records.'},
        elapsed_seconds=time.monotonic()-started)
    (out/'planner_card.json').write_text(json.dumps(card,indent=2)+'\n')
    (out/'cv_predictions.json').write_text(json.dumps([dict(id=r['id'],actual=r['domain'],predicted=str(classes[s.argmax()])) for r,s in zip(rows,scores)],indent=2)+'\n')
    print(json.dumps({k:v for k,v in card.items() if k!='domains'},indent=2))

if __name__=='__main__':main()
