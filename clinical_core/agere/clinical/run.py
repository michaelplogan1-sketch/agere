"""Incremental clinical fact/reconciliation pipeline over an observed warehouse snapshot."""
from pathlib import Path
from collections import Counter
import argparse,hashlib,json,time
from ..warehouse import Warehouse
from ..operators import patient_analysis
from ..policy import Program
from ...contracts import digest
from .facts import extract
from .reconcile import reconcile
from .workflows import execute,NAMES

ROOT=Path(__file__).resolve().parents[3]


def run(*,warehouse_path,release,as_of,output,document_extractions=None):
    output=Path(output)
    if output.exists():raise ValueError('Fresh output directory required')
    output.mkdir(parents=True)
    t=time.perf_counter();w=Warehouse(warehouse_path)
    try:
        patients,snapshot=w.snapshot(release,as_of)
        if not patients:raise ValueError('Unknown or empty release')
        if document_extractions:
            bindings={e['record_id']:e for e in json.loads(Path(document_extractions).read_text())['records']}
            for p in patients.values():
                for r in p['records']:
                    if r['record_id'] in bindings:
                        e=bindings[r['record_id']]
                        if r['source_table']!='documents' or r['patient_id']!=e['patient_id']:raise ValueError('OCR identity mismatch')
                        r.update(extracted_text=e['text'],extraction_method=e['method'])
        specs=json.loads((ROOT/'config/agere/programs.json').read_text());programs=[Program(p) for p in specs]
        dependencies=[*(Path(__file__).parent/n for n in ('facts.py','reconcile.py','workflows.py','run.py')),*(Path(__file__).parent.parent/n for n in ('operators.py','narrative.py','narrative_sources.py','policy.py'))]
        code={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in dependencies}
        code_hash=digest(code);counts={k:Counter() for k in NAMES};ids={k:[] for k in NAMES};factcounts=Counter();hits=0
        examples={k:{} for k in NAMES};cachekeys=[]
        with (output/'patients.jsonl').open('w') as stream:
            for pid,p in patients.items():
                key=digest({'namespace':'clinical-review/1','records':p,'as_of':as_of,'rules':specs,'code':code_hash})
                cachekeys.append(key);a=w.cached(key)
                if a is None:
                    native=patient_analysis(p['patient'],p['records'],as_of,programs)
                    bundle=extract(pid,p['records'],as_of,narrative=native['clinical']['narrative']);state=reconcile(bundle)
                    a={'patient_id':pid,'synthetic':True,'state':state,'workflows':execute(state,p['records'],native),'coverage':bundle['coverage']}
                    w.save(key,a)
                else:hits+=1
                for f in a['state']['facts']:factcounts[f['concept'].split(':')[0]]+=1
                for k,v in a['workflows'].items():
                    counts[k][v['status']]+=1
                    if v['review_candidate']:ids[k].append(pid)
                    if v['status'] not in examples[k]:examples[k][v['status']]={'patient_id':pid,'workflow':v,'facts':a['state']['facts']}
                stream.write(json.dumps(a,allow_nan=False)+'\n')
        for k in NAMES:
            (output/(k+'.json')).write_text(json.dumps({'workflow':k,'name':NAMES[k],'synthetic':True,
                'patients_in_snapshot':len(patients),'review_candidates':len(ids[k]),'patient_ids':ids[k],
                'status_counts':dict(counts[k]),'clinical_action_authorized':False,
                'scope':'Explicit review workflow across the full snapshot; no automatic care-gap or eligibility determination'},indent=2))
        report={'schema':'clinical-workflow-run/1','synthetic':True,'snapshot':snapshot,'patients':len(patients),
                'fact_counts_by_kind':dict(factcounts),'workflows':{k:{'review_candidates':len(ids[k]),'status_counts':dict(counts[k])} for k in NAMES},
                'compute':{'elapsed_seconds':time.perf_counter()-t,'patient_cache_hits':hits,'new_paid_api_calls':0},
                'source_code_hashes':code,'cache_key_digest':digest(cachekeys),'policy_hashes':[p.hash for p in programs],
                'model_extraction_applied_to_warehouse':False,'rules_vs_models':'Warehouse fact extraction uses structured adapters and bounded text rules; live model comparison is separate',
                'limitations':['Synthetic observational sources only','No general clinical extraction model promoted',
                               'No care execution','No model superiority or clinical accuracy claim','Source event dates may differ from true observation dates']}
        (output/'run.json').write_text(json.dumps(report,indent=2));(output/'examples.json').write_text(json.dumps(examples,indent=2))
        return report
    finally:w.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--warehouse',type=Path,required=True);p.add_argument('--release',required=True);p.add_argument('--as-of',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--document-extractions',type=Path)
    a=p.parse_args();print(json.dumps(run(warehouse_path=a.warehouse,release=a.release,as_of=a.as_of,output=a.output,document_extractions=a.document_extractions),indent=2))
