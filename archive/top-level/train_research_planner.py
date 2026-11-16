"""Turn the VBC research into SFT-ready records and fit a local proposal router."""
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline,FeatureUnion
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold,cross_val_predict
from sklearn.metrics import accuracy_score,top_k_accuracy_score
from threadpoolctl import threadpool_limits

ROOT=Path(__file__).resolve().parent

def main():
    source=ROOT/'research/vbc-landscape/question_supplement.json'
    bank=json.loads(source.read_text());out=ROOT/'artifacts/research-training';out.mkdir(exist_ok=True,parents=True)
    rows=[];domains={}
    for g in bank['groups']:
        q0=g['questions'][0]
        domains[g['id']]=dict(title=g['title'],operators=q0['operators'],review_owner=q0['review_owner'],
            source_refs=q0['source_refs'],outcome_measure=q0['outcome_measure'],balancing_measure=q0['balancing_measure'])
        for q in g['questions']:
            answer=dict(domain=g['id'],operators=q['operators'],required_data=q['required_data'],
                review_owner=q['review_owner'],failure_to_catch=q['failure_to_catch'],
                outcome_measure=q['outcome_measure'],process_measure=q['process_measure'],
                balancing_measure=q['balancing_measure'],answer_contract=q['answer_contract'],
                clinical_answer=None,automatic_execution=False)
            rows.append(dict(id=q['id'],domain=g['id'],question=q['question'],
                source_refs=q['source_refs'],label_provenance='Original analyst-authored research curriculum; expert review pending',
                purpose='Question-to-analysis-plan supervision, not fabricated patient answers',
                messages=[{'role':'system','content':'Propose a structured analysis plan. Identify required evidence, methods and limitations. Do not answer an unprovided patient case or invent clinical thresholds.'},
                    {'role':'user','content':q['question']},{'role':'assistant','content':json.dumps(answer)}]))
    with (out/'planner_training.jsonl').open('w') as f:
        for row in rows:f.write(json.dumps(row)+'\n')
    model=Pipeline([('features',FeatureUnion([
        ('word',TfidfVectorizer(ngram_range=(1,2),sublinear_tf=True)),
        ('char',TfidfVectorizer(analyzer='char_wb',ngram_range=(3,5),min_df=2))])),
        ('classifier',LogisticRegression(C=5,max_iter=400,random_state=29))])
    text=[r['question'] for r in rows];labels=np.array([r['domain'] for r in rows]);classes=np.unique(labels)
    with threadpool_limits(limits=4):
        predictions=cross_val_predict(model,text,labels,cv=StratifiedKFold(n_splits=6,shuffle=True,random_state=29),method='predict_proba')
        model.fit(text,labels)
    joblib.dump(model,out/'planner.joblib',compress=3)
    card=dict(name='Research-derived analysis-family proposal router',training_examples=len(rows),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        sha256=hashlib.sha256((out/'planner.joblib').read_bytes()).hexdigest(),domains=domains,
        actual_training='Local TF-IDF/logistic regression weights fitted to analyst-authored domain labels',
        exported_curriculum='Full plan targets are exported for later SFT; this router predicts the family and retrieves its template.',
        developmental_cross_validation=dict(folds=6,top1=float(accuracy_score(labels,classes[predictions.argmax(1)])),
            top3=float(top_k_accuracy_score(labels,predictions,k=3,labels=classes)),
            caveat='Small related-question curriculum; not a held-out clinical or patient evaluation. Final artifact fitted on all curriculum records.'),
        acceptance='Proposal-only; no automatic SQL, clinical judgment or program eligibility',
        trained_frontier_or_medgemma=False,paid_calls=0)
    (out/'planner_card.json').write_text(json.dumps(card,indent=2)+'\n')
    (out/'cv_predictions.json').write_text(json.dumps([dict(id=r['id'],actual=r['domain'],predicted=str(classes[s.argmax()])) for r,s in zip(rows,predictions)],indent=2)+'\n')
    print(json.dumps({k:v for k,v in card.items() if k!='domains'},indent=2))

if __name__=='__main__':main()
