"""Execute and export the synthetic pre-index evidence integration."""
from clinical_core.agere.active import stack_path as active_stack_path
import argparse
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
from clinical_core.agere.clinical import baseline_evidence as B, graph_planner, analysis_graph
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import baseline_examples, snapshot_examples


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stack',default=str(active_stack_path()))
    parser.add_argument('--output',required=True);args=parser.parse_args();path=Path(args.stack);out=Path(args.output)
    out.mkdir(parents=True,exist_ok=False);stack=Stack(path,allow_live=False);runs=[];exports=[]
    try:
        db=sqlite3.connect(path/'stack.sqlite3')
        try:ids=[p[0] for p in db.execute('SELECT id FROM patients ORDER BY id')]
        finally:db.close()
        for i,ex in enumerate(snapshot_examples(baseline_examples(),stack.retriever.meta['snapshot']['as_of'])):
            result=stack.compose(ex['question']);(out/f'graph-{i+1}.json').write_text(json.dumps(result,indent=2,allow_nan=False))
            replay=stack.compose(ex['question'])
            if not replay['cache_hit'] or replay['new_paid_api_calls']:raise ValueError('Baseline replay failed')
            r=result['results'][-1]['result'];summary={'question':ex['question'],'patients_evaluated':r['patients_evaluated'],
                'statuses':r['status_counts'],'processing':r['processing_counts'],'excluded_sources':r['excluded_source_counts'],
                'elapsed_seconds':result['elapsed_seconds'],'replay_elapsed_seconds':replay['elapsed_seconds'],
                'replay_cache_hit':replay['cache_hit'],'new_paid_api_calls':result['new_paid_api_calls']}
            if r['patients']:
                sample=B.inspect(path,r['patients'][0]['id'])
                (out/f'patient-{i+1}.json').write_text(json.dumps(sample,indent=2,allow_nan=False))
            runs.append(summary);print(json.dumps({k:v for k,v in summary.items() if k!='question'}),flush=True)
            exports.append(B.export_dataset(path,ids,ex['graph']['nodes'][-1]['parameters'],
                ex['graph']['nodes'][1]['parameters'],out/('dataset-'+str(i+1))))
        sizes=[]
        def capture(body,*args,**kw):sizes.append(len(json.dumps(body,allow_nan=False).encode()));return None
        meta=json.loads((path/'run.json').read_text())
        with patch.object(graph_planner,'send',side_effect=capture):
            graph_planner.propose('x'*1500,None,None,snapshot=meta['snapshot'],graph_catalog=analysis_graph.catalog(path),
                observed_values=json.loads((path/'cohort-values.json').read_text()))
        if max(sizes)>20000:raise ValueError('Provider request exceeds current bound')
        receipt={'schema':B.VERSION,'synthetic':True,'code_identity':B.code_identity(),'runs':runs,'exports':exports,
            'frontier_request_bytes_1500_ascii_char_question':sizes[0],'new_paid_api_calls':0,'new_model_weights':False,
            'limits':B.LIMITS}
        (out/'run.json').write_text(json.dumps(receipt,indent=2,allow_nan=False)+'\n')
    finally:stack.close()


if __name__=='__main__':main()
