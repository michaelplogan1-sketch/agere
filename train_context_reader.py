"""Train v4 with explicit reuse of exposed diagnostics and controlled features."""
from pathlib import Path
from collections import Counter
import hashlib,json,time
import joblib
from threadpoolctl import threadpool_limits
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from clinical_core.contracts import digest
from clinical_core.agere.clinical.evidence_reader import AXES,input_feature
from clinical_core.agere.clinical import context_features,scoped_reader

ROOT=Path(__file__).resolve().parent
V2=ROOT/'artifacts/agere-evidence-reader-v2';V3=ROOT/'artifacts/agere-evidence-reader-v3'
OUT=ROOT/'artifacts/agere-evidence-reader-v4'

def training_rows():
    original=[json.loads(s) for s in (V2/'examples.jsonl').read_text().splitlines()]
    for p in sorted(V2.glob('repair-*.json')):original.extend(json.loads(p.read_text()))
    labels=json.loads((V2/'labels.json').read_text())
    excluded=set(json.loads((V2/'leakage-audit.json').read_text())['quarantined'])
    rows=[];quarantine=[]
    for r in original:
        if r['split']!='train' or r['example_id'] in excluded:continue
        agreed={a:r['authored_labels'][a] for a in AXES if r['authored_labels'][a]==labels[r['example_id']]['jev'][a]['label']==labels[r['example_id']]['perplexity'][a]['label']}
        rows.append({'example_id':r['example_id'],'input':r['input'],'labels':agreed,'origin':'authored_three_way','synthetic':True})
    for cases_path,labels_path in [(V2/'warehouse-transfer-cases.json',V2/'warehouse-transfer-labels.json'),(V3/'fresh-cases.json',V3/'fresh-labels.json')]:
        cases=json.loads(cases_path.read_text());votes=json.loads(labels_path.read_text())
        for r in cases:
            v=votes[r['example_id']];agreed={}
            for a in AXES:
                label=v['jev'][a]['label']
                if label is None or label!=v['perplexity'][a]['label']:continue
                # Quarantine contradictory metadata labels for affected axes.
                conflict=False
                if a in ('assertion','subject','relation'):
                    for p in ('jev','perplexity'):
                        t=v[p];is_meta=t['relation']['label']=='document_metadata'
                        if a=='assertion' and ((label=='not_applicable')!=is_meta):conflict=True
                        if a=='subject' and ((label=='nonclinical')!=is_meta):conflict=True
                        if a=='relation' and is_meta and (t['assertion']['label']!='not_applicable' or t['subject']['label']!='nonclinical'):conflict=True
                if conflict:quarantine.append({'example_id':r['example_id'],'axis':a,'label':label,'reason':'within-teacher metadata inconsistency'});continue
                agreed[a]=label
            rows.append({'example_id':r['example_id'],'input':r['input'],'labels':agreed,'origin':'exposed_warehouse_two_teacher','synthetic':True,'patient_id':r['patient_id'],'source':r['source']})
    if len({r['example_id'] for r in rows})!=len(rows):raise ValueError('Duplicate training ID')
    return rows,quarantine

def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'model-card.json').exists():raise ValueError('Frozen v4 exists; use a new version')
    plan={'primary':'typed_context','controls':['v3_hybrid','lexical_retrained','scoped_retrained'],
        'training':'Original agreed training rows plus both previously exposed 64-excerpt warehouse diagnostics. Per-axis teacher agreement only, contradictory metadata labels quarantined. No clinician adjudication.',
        'hypothesis':'Source-style supervision plus explicit target-local cue features improve negation and chronology; ablations separate data from representation.',
        'evaluation':'Freeze weights before selecting new 96 cases; exclude all prior diagnostic patients and normalized target-clause/concept groups. Same synthetic world; not independent clinical validation.',
        'selection':'Primary typed_context fixed before new labels. Connect only as review-only if no axis loses teacher-consensus matches versus v3 hybrid; otherwise retain v3 live and publish failure.',
        'authority':'All predictions abstain. No automatic action or clinical threshold.'}
    (OUT/'plan.json').write_text(json.dumps(plan,indent=2))
    rows,quarantine=training_rows();(OUT/'training.json').write_text(json.dumps(rows,indent=2));(OUT/'label-quarantine.json').write_text(json.dumps(quarantine,indent=2))
    hashes={};metrics={};started=time.perf_counter()
    with threadpool_limits(limits=4):
        for a in AXES:
            selected=[r for r in rows if a in r['labels']];inputs=[r['input'] for r in selected];y=[r['labels'][a] for r in selected]
            if set(y)!=set(AXES[a]):raise ValueError('Missing class '+a)
            metrics[a]={'examples':len(y),'classes':dict(Counter(y)),'origin':dict(Counter(r['origin'] for r in selected))}
            for variant in ('lexical_retrained','scoped_retrained','typed_context'):
                if variant=='lexical_retrained':
                    model=Pipeline([('tfidf',TfidfVectorizer(analyzer='char_wb',ngram_range=(3,5),max_features=40000,sublinear_tf=True)),('classifier',LogisticRegression(C=4.,class_weight='balanced',max_iter=2000,random_state=813))]);x=[input_feature(v) for v in inputs]
                else:model=context_features.pipeline() if variant=='typed_context' else scoped_reader.pipeline();x=inputs
                model.fit(x,y);path=OUT/f'{a}-{variant}.joblib';joblib.dump(model,path,compress=3);hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    card={'schema':'context-evidence-reader/4','synthetic':True,'decision_authority':False,'runtime_axes':{a:'v4 typed target-context head' for a in AXES},
        'scope_applies_to_axes':list(AXES),'weight_hashes':hashes,'runtime_hashes':{Path(m.__file__).name:hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (context_features,scoped_reader)},
        'training':metrics,'training_digest':digest(rows),'quarantined_axes':len(quarantine),'plan':plan,'fit_seconds':time.perf_counter()-started,
        'foundation_model_finetuned':False,'acceptance_threshold':None,'limitations':['All inputs synthetic','Machine consensus is not clinical truth','Shared templates and one warehouse','Prior diagnostics explicitly repurposed for training','No automated clinical decisions']}
    (OUT/'model-card.json').write_text(json.dumps(card,indent=2));print(json.dumps({'fit_seconds':card['fit_seconds'],'training':metrics,'quarantined_axes':len(quarantine)},indent=2))

if __name__=='__main__':main()
