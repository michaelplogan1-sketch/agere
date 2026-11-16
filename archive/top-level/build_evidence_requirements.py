"""Execute connected documentary contracts on the synthetic warehouse; no API calls."""
from clinical_core.agere.active import stack_path as active_stack_path
import argparse
import json
from pathlib import Path
import sqlite3

from clinical_core.agere.clinical import evidence_requirements as er, source_structure
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import requirement_examples
from clinical_core.agere.clinical import semantic_reader


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stack',default=str(active_stack_path()))
    parser.add_argument('--output',required=True)
    args=parser.parse_args();path=Path(args.stack);out=Path(args.output)
    out.mkdir(parents=True,exist_ok=False)
    stack=Stack(path,allow_live=False);summaries=[]
    try:
        for i,example in enumerate(requirement_examples()):
            result=stack.compose(example['question'])
            (out/f'graph-{i+1}.json').write_text(json.dumps(result,indent=2,allow_nan=False))
            contract=next(r['result'] for r in result['results'] if r['node']['operation']=='requirements')
            replay=stack.compose(example['question'])
            if not replay['cache_hit'] or replay['new_paid_api_calls']!=0:raise ValueError('Exact replay failed')
            first=contract['patients'][0] if contract['patients'] else None
            if first:er.inspect(path,first['id'])
            summary={'question':example['question'],'contract':contract['contract']['id'],
                'patients_evaluated':contract['patients_evaluated'],'statuses':contract['status_counts'],
                'membership':contract['counts'],'acquisition_priority':contract['acquisition_priority'],
                'processing_counts':contract['processing_counts'],
                'paid_calls':result['new_paid_api_calls'],'replay_cache_hit':replay['cache_hit']}
            if result['results'][-1]['node']['operation']=='worklist':
                summary['downstream_worklist']=result['results'][-1]['result']['counts']
            summaries.append(summary);print(json.dumps(summary),flush=True)
        db=semantic_reader.db(path)
        try:readings=[json.loads(row[0]) for row in db.execute('SELECT payload FROM readings')]
        finally:db.close()
        roles=[]
        for reading in readings:
            target=reading['target'];_,text,_=semantic_reader.source(path,target)
            structural=source_structure.locate(text,target['start'],target['end'])
            if structural['clinical_account_excluded']:
                roles.append({'reading_id':reading['id'],'target':target,'structure':structural,
                              'original_model_labels':reading['reading']['labels'],
                              'original_reading_changed':False})
        (out/'source-role-audit.json').write_text(json.dumps(roles,indent=2))
        receipt={'schema':er.VERSION,'synthetic':True,'code_identity':er.code_identity(),
            'contracts':list(er.definitions().values()),'runs':summaries,
            'saved_readings_with_nonclinical_roles':len(roles),'new_paid_api_calls':sum(s['paid_calls'] for s in summaries),
            'new_model_weights':False,'human_reviews_created':0,'limits':er.LIMITS}
        (out/'run.json').write_text(json.dumps(receipt,indent=2))
    finally:stack.close()


if __name__=='__main__':main()
