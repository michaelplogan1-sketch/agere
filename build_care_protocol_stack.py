"""Run full synthetic populations through source-bound protocol review."""
from clinical_core.agere.active import stack_path as active_stack_path
import argparse
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
from clinical_core.agere.clinical import care_protocols as cp, graph_planner, analysis_graph
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import protocol_examples


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stack',default=str(active_stack_path()))
    parser.add_argument('--output',required=True)
    args=parser.parse_args();path=Path(args.stack);out=Path(args.output)
    out.mkdir(parents=True,exist_ok=False);stack=Stack(path,allow_live=False);runs=[]
    try:
        for i,ex in enumerate(protocol_examples()):
            result=stack.compose(ex['question'])
            (out/f'graph-{i+1}.json').write_text(json.dumps(result,indent=2,allow_nan=False))
            replay=stack.compose(ex['question'])
            if not replay['cache_hit'] or replay['new_paid_api_calls']:raise ValueError('Protocol replay failed')
            r=next(x['result'] for x in result['results'] if x['node']['operation']=='protocol_review')
            summary={'question':ex['question'],'protocol':r['protocol']['id'],'patients_evaluated':r['patients_evaluated'],
                'statuses':r['status_counts'],'bindings':r['binding_status_counts'],
                'processing':r['processing_counts'],'elapsed_seconds':result['elapsed_seconds'],
                'replay_elapsed_seconds':replay['elapsed_seconds'],'replay_cache_hit':replay['cache_hit'],
                'new_paid_api_calls':result['new_paid_api_calls']}
            if r['patients']:
                row=cp.inspect(path,r['patients'][0]['id'])
                (out/f'patient-{i+1}.json').write_text(json.dumps(row,indent=2,allow_nan=False))
            if result['results'][-1]['node']['operation']=='worklist':
                w=result['results'][-1]['result'];summary['worklist']=w
                db=sqlite3.connect(path/'program-work.sqlite3')
                try:
                    proposal=json.loads(db.execute('SELECT payload FROM work_proposals WHERE id=?',(w['proposal_id'],)).fetchone()[0])
                    assessment=json.loads(db.execute('SELECT payload FROM work_assessments WHERE id=?',(proposal['assessment_ids'][0],)).fetchone()[0]) if proposal['assessment_ids'] else None
                finally:db.close()
                (out/'program-assessment.json').write_text(json.dumps(assessment,indent=2,allow_nan=False))
            runs.append(summary)
            print(json.dumps({k:v for k,v in summary.items() if k not in ('question','bindings','worklist')}),flush=True)
        sizes=[]
        def capture(body,*args,**kw):sizes.append(len(json.dumps(body,allow_nan=False).encode()));return None
        meta=json.loads((path/'run.json').read_text())
        with patch.object(graph_planner,'send',side_effect=capture):
            graph_planner.propose('x'*1500,None,None,snapshot=meta['snapshot'],
                graph_catalog=analysis_graph.catalog(path),observed_values=json.loads((path/'cohort-values.json').read_text()))
        if max(sizes)>20000:raise ValueError('Provider request exceeded existing byte bound')
        receipt={'schema':cp.VERSION,'synthetic':True,'code_identity':cp.code_identity(),'runs':runs,
            'frontier_request_bytes_1500_ascii_char_question':sizes[0],
            'new_paid_api_calls':sum(r['new_paid_api_calls'] for r in runs),'new_model_weights':False,
            'source_reviews_created':0,'program_items_materialized':0,'limits':cp.LIMITS}
        (out/'run.json').write_text(json.dumps(receipt,indent=2,allow_nan=False))
    finally:stack.close()


if __name__=='__main__':main()
