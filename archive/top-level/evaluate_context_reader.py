"""Frozen v4 comparison with a new, source-group-separated warehouse diagnostic."""
from pathlib import Path
from collections import defaultdict
import argparse,hashlib,json,os,sqlite3
import joblib
from threadpoolctl import threadpool_limits
from clinical_core.contracts import digest
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical.evidence_reader import AXES,input_feature
from clinical_core.agere.clinical.factorized_training import check
from clinical_core.agere.clinical.relation_learning import normalize,write
from clinical_core.agere.clinical.scoped_reader import ScopedReader

ROOT=Path(__file__).resolve().parent
V2=ROOT/'artifacts/agere-evidence-reader-v2';V3=ROOT/'artifacts/agere-evidence-reader-v3';OUT=ROOT/'artifacts/agere-evidence-reader-v4'
VARIANTS=('v3_hybrid','lexical_retrained','scoped_retrained','typed_context')

def group(r):return digest([r['input']['concept'],normalize(r['source']['context'])])

def select():
    old=json.loads((V2/'warehouse-transfer-cases.json').read_text())+json.loads((V3/'fresh-cases.json').read_text())
    forbidden={group(r) for r in old};patients={r['patient_id'] for r in old}
    grouped={};db=sqlite3.connect((V2/'warehouse/annotations.sqlite3').as_uri()+'?mode=ro',uri=True)
    try:
        for (payload,) in db.execute('SELECT payload FROM annotations'):
            r=json.loads(payload);g=group(r)
            if g in forbidden or r['patient_id'] in patients:continue
            key=digest(['v4-fresh/1',r['input'],r['source']])
            if g not in grouped or key<grouped[g][0]:grouped[g]=(key,r)
    finally:db.close()
    strata=defaultdict(list)
    for key,r in sorted(grouped.values(),key=lambda v:v[0]):strata[r['input']['concept']].append((key,r))
    rows=[]
    while len(rows)<96 and any(strata.values()):
        for concept in sorted(strata):
            if strata[concept] and len(rows)<96:
                key,r=strata[concept].pop(0)
                # Drop old model predictions from the frozen evaluation inputs.
                rows.append({k:v for k,v in {**r,'example_id':key}.items() if k not in ('annotation','scope_applies_to_axes')})
    assert len(rows)==96 and not ({r['patient_id'] for r in rows}&patients)
    assert not ({group(r) for r in rows}&forbidden)
    return rows

def predictions(rows):
    card=json.loads((OUT/'model-card.json').read_text());inputs=[r['input'] for r in rows]
    result={v:{r['example_id']:{} for r in rows} for v in VARIANTS}
    base=ScopedReader(ROOT/'artifacts/agere-evidence-reader-v3-hybrid',OUT/'baseline-evaluation-cache.sqlite3')
    try:
        for r,p in zip(rows,base.predict(inputs)):result['v3_hybrid'][r['example_id']]={a:p['axes'][a]['label'] for a in AXES}
    finally:base.close()
    with threadpool_limits(limits=4):
        for a in AXES:
            for v in VARIANTS[1:]:
                path=OUT/f'{a}-{v}.joblib'
                assert hashlib.sha256(path.read_bytes()).hexdigest()==card['weight_hashes'][path.name]
                model=joblib.load(path);x=[input_feature(i) for i in inputs] if v=='lexical_retrained' else inputs
                for r,p in zip(rows,model.predict(x)):result[v][r['example_id']][a]=str(p)
    return result

def score(rows,labels,pred):
    metrics={}
    for a in AXES:
        agreed=[r for r in rows if labels[r['example_id']]['jev'][a]['label'] is not None and labels[r['example_id']]['jev'][a]['label']==labels[r['example_id']]['perplexity'][a]['label']]
        counts={v:sum(pred[v][r['example_id']][a]==labels[r['example_id']]['jev'][a]['label'] for r in agreed) for v in VARIANTS}
        transitions={'repaired':[],'regressed':[],'still_wrong':[]}
        for r in agreed:
            rid=r['example_id'];truth=labels[rid]['jev'][a]['label'];old=pred['v3_hybrid'][rid][a]==truth;new=pred['typed_context'][rid][a]==truth
            if new and not old:transitions['repaired'].append(rid)
            if old and not new:transitions['regressed'].append(rid)
            if not old and not new:transitions['still_wrong'].append(rid)
        metrics[a]={'teacher_consensus_cases':len(agreed),'matches':counts,'primary_transitions':transitions}
    return metrics

def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');args=p.parse_args()
    if not args.live:raise ValueError('Explicit --live needed for paid synthetic label calls')
    card_hash=hashlib.sha256((OUT/'model-card.json').read_bytes()).hexdigest()
    if (OUT/'fresh-selection.json').exists():
        manifest=json.loads((OUT/'fresh-selection.json').read_text());assert manifest['model_card_sha256']==card_hash
        rows=json.loads((OUT/'fresh-cases.json').read_text());assert manifest['cases_digest']==digest(rows)
    else:
        rows=select();write(OUT/'fresh-cases.json',rows);write(OUT/'fresh-selection.json',{'model_card_sha256':card_hash,'cases_digest':digest(rows),'examples':len(rows),'synthetic':True,'selection':'Weights frozen first; exclude both earlier diagnostic patient sets and normalized target-clause/concept groups; minhash then concept round robin.','limitations':'Same synthetic generator and warehouse; semantic templates can overlap.'})
    pred=predictions(rows)
    path=OUT/'fresh-predictions-before-labels.json'
    if path.exists():assert json.loads(path.read_text())==pred
    else:write(path,pred)
    for line in Path('/Users/mikel/insight-engine/.env').read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):continue
        k,v=line.split('=',1);k=k.strip().removeprefix('export ')
        if k in {'TYPESAFE_API_KEY','PERPLEXITY_API_KEY'}:os.environ.setdefault(k,v.strip().strip('"').strip("'"))
    receipts=OUT/'receipts';receipts.mkdir(exist_ok=True)
    ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3');labels={};calls=[]
    try:
        for i in range(0,len(rows),2):
            for provider in ('jev','perplexity'):
                answers,receipt=check(rows[i:i+2],provider,ledger,receipts);calls.append({'provider':provider,**receipt})
                for r in answers:labels.setdefault(r['example_id'],{})[provider]=r
            ledger.settle_completed();write(OUT/'fresh-labels.partial.json',labels)
            print(json.dumps({'fresh_cases_labeled':len(labels),'total':len(rows)}),flush=True)
        write(OUT/'fresh-labels.json',labels);metrics=score(rows,labels,pred)
        passes=all(m['matches']['typed_context']>=m['matches']['v3_hybrid'] for m in metrics.values())
        write(OUT/'fresh-evaluation.json',{'synthetic':True,'examples':len(rows),'metrics':metrics,'review_only_connection_gate_passed':passes,'model_card_sha256':card_hash,'automatic_clinical_promotion':False,'scope':'Fresh source-group diagnostic against two machine teachers; no clinical validation','requests':calls,'budget_after':ledger.receipt()})
        write(OUT/'fresh-review-queue.json',[{**r,'teachers':labels[r['example_id']],'readers':{v:pred[v][r['example_id']] for v in VARIANTS}} for r in rows if any(pred['typed_context'][r['example_id']][a]!=labels[r['example_id']][p][a]['label'] for a in AXES for p in ('jev','perplexity'))])
        print(json.dumps({'metrics':metrics,'review_only_gate_passed':passes},indent=2))
    finally:ledger.close()

if __name__=='__main__':main()
