"""Add cohort/event/math execution without re-embedding or altering frozen receipts."""
import argparse,json,shutil,sqlite3,time
from collections import Counter,defaultdict
from pathlib import Path
from clinical_core.agere.clinical.clinical_events import build as build_events
from clinical_core.agere.clinical.analytic_registry import compute_all

def backup(source,target):
    src=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(target)
    try:src.backup(dst)
    finally:src.close();dst.close()

def values(path):
    db=sqlite3.connect(path/'stack.sqlite3');roster=defaultdict(Counter);sites=Counter();concepts=set()
    try:
        for payload, in db.execute('SELECT payload FROM patients'):
            p=json.loads(payload)
            for k in ('coverage','region','sex','preferred_language'):
                if isinstance(p.get(k),str):roster[k][p[k]]+=1
        for stream,payload in db.execute("SELECT stream,payload FROM records WHERE stream IN ('encounters','problem_lists')"):
            r=json.loads(payload)
            if stream=='encounters' and r.get('organization_id'):sites[r['organization_id']]+=1
            if stream=='problem_lists':
                for p in r.get('problems',[]):
                    if p.get('code') and p.get('name'):concepts.add((p['code'],p['name'],p.get('system')))
        result={'roster':{k:dict(v) for k,v in roster.items()},'encounter_organizations':dict(sites),
                'documented_concepts':[{'code':c,'name':n,'code_system':s} for c,n,s in sorted(concepts)],
                'scope':'Observed values and source-authored code/name pairs; no clinical ontology inference.'}
        (path/'cohort-values.json').write_text(json.dumps(result,indent=2));return result
    finally:db.close()

def build(source,output):
    if output.exists():raise ValueError('Fresh build directory required')
    started=time.perf_counter();output.mkdir(parents=True)
    for name in ('stack.sqlite3','model-cache.sqlite3'):
        if (source/name).exists():backup(source/name,output/name)
    for name in ('run.json','vectors.npy','dense-content-indices.npy','annotation-link.json','clinical-review-link.json'):
        if (source/name).exists():shutil.copy2(source/name,output/name)
    if (source/'programs').exists():shutil.copytree(source/'programs',output/'programs')
    # Program packages live in SQLite; provider replay receipts may be reused by exact request hash only.
    if (source/'provider-receipts').exists():shutil.copytree(source/'provider-receipts',output/'provider-receipts')
    receipt={'schema':'cohort-stack-build/1','synthetic':True,'source':str(source.resolve()),
             'output':str(output.resolve()),'retrieval_vectors_reused':True,'new_embedding_tokens':0,
             'new_paid_api_calls':0,'events':build_events(output),'operators':compute_all(output)}
    receipt['observed_values']=values(output);receipt['elapsed_seconds']=time.perf_counter()-started
    (output/'build.json').write_text(json.dumps(receipt,indent=2));return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('artifacts/agere-integrated-stack-v1'))
    p.add_argument('--output',type=Path,default=Path('artifacts/agere-cohort-stack-v2'))
    a=p.parse_args();r=build(a.source,a.output)
    print(json.dumps({k:v for k,v in r.items() if k not in ('operators','observed_values')},indent=2))
