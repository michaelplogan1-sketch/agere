"""Independent full-scan parity, measured cache reuse and real protocol/baseline path."""
import argparse,hashlib,json,sqlite3,time
from pathlib import Path
from collections import defaultdict
from clinical_core.contracts import digest
from clinical_core.agere.clinical import patient_input_index as index,care_protocols as protocols,baseline_evidence as baseline,outcomes
from clinical_core.agere.clinical import analysis_graph
from clinical_core.agere.clinical.graph_workbench import baseline_examples,protocol_examples,snapshot_examples
from clinical_core.agere.clinical.stack_runtime import Stack


def write(p,v):p.write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    meta=json.loads((a.stack/'run.json').read_text());first=index.snapshot(a.stack);write(a.out/'initial-index.json',{k:v for k,v in first.items() if k!='patients'});print(json.dumps(first['processing']),flush=True)
    t=time.perf_counter();db=sqlite3.connect((a.stack/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    patients={pid:digest(raw) for pid,raw in db.execute('SELECT id,payload FROM patients ORDER BY id')};records={};analyses=defaultdict(list);n=0
    for pid,raw in db.execute('SELECT patient,payload FROM records ORDER BY patient,id'):
        assert pid in patients;records.setdefault(pid,hashlib.sha256()).update(digest(raw).encode());n+=1
    for pid,op,raw in db.execute("SELECT patient,operator,payload FROM analyses WHERE operator IN ('heart_failure_review','affordability_review') ORDER BY patient,operator"):
        analyses[pid].append([op,digest(raw)])
    full={pid:digest([h,records[pid].hexdigest() if pid in records else '',analyses[pid]]) for pid,h in patients.items()};elapsed=time.perf_counter()-t
    assert full=={pid:v['protocol_hash'] for pid,v in first['patients'].items()};db.close()
    repeat=index.snapshot(a.stack);assert repeat['identity']==first['identity'];assert repeat['processing']['record_payloads_hashed']==0
    print(json.dumps({'independent_full_scan_parity':True,'records':n,'full_scan_seconds':elapsed,'repeat':repeat['processing']}),flush=True)
    stack=Stack(a.stack,allow_live=False);examples=snapshot_examples(protocol_examples()[:1]+baseline_examples()[:1],meta['snapshot']['as_of']);runs=[]
    try:
        for ex in examples:
            result=stack.compose(ex['question']);assert result['new_paid_api_calls']==0
            write(a.out/('protocol-question.json' if ex['graph']['nodes'][-1]['operation']=='protocol_review' else 'baseline-question.json'),result)
            detail=result['results'][-1]['result'];assert detail['patients_evaluated']==len(patients)
            if 'input_fingerprints' in detail:assert detail['input_fingerprints']['record_payloads_hashed']==0
            runs.append({'title':ex['title'],'result_id':result['result_id'],'status_counts':detail['status_counts'],'processing_counts':detail['processing_counts'],'input_fingerprints':detail.get('input_fingerprints')})
            print(json.dumps(runs[-1]),flush=True)
        # The former source-bound protocol positive criterion is unchanged.
        previous=json.loads((a.stack.parent/'diagnostic-verification.json').read_text())
        protocol_result=json.loads((a.out/'protocol-question.json').read_text())['results'][-1]['result']
        assert protocol_result['status_counts']==previous['protocol_status_counts']
        receipt={'schema':'patient-input-index-verification/1','synthetic':True,'snapshot':meta['snapshot'],'patients':len(patients),'records':n,
            'independent_full_scan_parity':True,'full_scan_seconds':elapsed,'initial_index':first['processing'],'repeat':repeat['processing'],
            'runs':runs,'protocol_counts_unchanged':True,'code_identity':index.identity(),'new_paid_api_calls':0,
            'limits':['Measured wall times describe this local run, not a general speed guarantee.','Baseline text-event hashes are still scanned; cached sources do not establish clinical truth.']}
        write(a.out/'verification.json',receipt)
    finally:stack.close()

if __name__=='__main__':main()
