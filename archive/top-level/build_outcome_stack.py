"""Execute the connected outcome layer and export auditable synthetic targets."""
from clinical_core.agere.active import stack_path as active_stack_path
import argparse
import json
from pathlib import Path
import sqlite3

from clinical_core.agere.clinical import outcomes
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import outcome_examples, snapshot_examples


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stack',default=str(active_stack_path()))
    parser.add_argument('--output',required=True)
    args=parser.parse_args();path=Path(args.stack);out=Path(args.output)
    out.mkdir(parents=True,exist_ok=False);stack=Stack(path,allow_live=False);runs=[];exports=[]
    try:
        for i,example in enumerate(snapshot_examples(outcome_examples(),stack.retriever.meta['snapshot']['as_of'])):
            result=stack.compose(example['question'])
            (out/f'graph-{i+1}.json').write_text(json.dumps(result,indent=2,allow_nan=False))
            replay=stack.compose(example['question'])
            if not replay['cache_hit'] or replay['new_paid_api_calls']:raise ValueError('Outcome replay failed')
            results=[r['result'] for r in result['results'] if r['node']['operation']=='outcome']
            run={'question':example['question'],'statuses':[r['status_counts'] for r in results],
                 'outcome_counts':[r['outcome_counts'] for r in results],
                 'patients_evaluated':[r['patients_evaluated'] for r in results],
                 'replay_cache_hit':replay['cache_hit'],'new_paid_api_calls':result['new_paid_api_calls'],
                 'elapsed_seconds':result['elapsed_seconds']}
            if result['results'][-1]['node']['operation']=='compare':run['comparison']=result['results'][-1]['result']
            first=next((r['patients'][0] for r in results if r['patients']),None)
            if first:
                receipt=outcomes.inspect(path,first['id'])
                (out/f'patient-{i+1}.json').write_text(json.dumps(receipt,indent=2))
            runs.append(run);print(json.dumps({k:v for k,v in run.items() if k!='comparison'}),flush=True)
        db=sqlite3.connect(path/'stack.sqlite3')
        try:ids=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')]
        finally:db.close()
        for example in snapshot_examples(outcome_examples(),stack.retriever.meta['snapshot']['as_of'])[:3]:
            p=example['graph']['nodes'][1]['parameters']
            exports.append(outcomes.export_labels(path,ids,p,out/(p['contract']+'-labels.jsonl')))
        # Contrast calendar maturity at an earlier receipt cutoff using the same
        # definition. Current-snapshot historical reconstruction limits still apply.
        p={**outcome_examples()[1]['graph']['nodes'][1]['parameters'],
           'index_through':'2026-08-31','available_by':'2026-08-31'}
        earlier=outcomes.execute(path,ids,p)[2]
        (out/'earlier-cutoff.json').write_text(json.dumps(earlier,indent=2))
        receipt={'schema':outcomes.VERSION,'synthetic':True,'code_identity':outcomes.code_identity(),
            'runs':runs,'label_exports':exports,'earlier_cutoff_statuses':earlier['status_counts'],
            'new_paid_api_calls':sum(r['new_paid_api_calls'] for r in runs),'new_model_weights':False,
            'limits':outcomes.LIMITS}
        (out/'run.json').write_text(json.dumps(receipt,indent=2,allow_nan=False))
    finally:stack.close()


if __name__=='__main__':main()
