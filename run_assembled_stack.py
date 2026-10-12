"""Exercise the connected local predictors, source mathematics and specialist routes."""
import argparse,json
from pathlib import Path
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import model_examples
from run_integrated_stack import keys


def main():
    p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,default=Path('artifacts/agere-population-stack-v3'))
    p.add_argument('--live',action='store_true');p.add_argument('--frontier',action='store_true');a=p.parse_args()
    if a.frontier and not a.live:p.error('--frontier requires --live')
    if a.live:keys()
    stack=Stack(a.stack,allow_live=a.live);root=a.stack/'examples';root.mkdir(exist_ok=True)
    try:
        examples=model_examples();results={}
        results['forecast-source-composition']=stack.run_graph(examples[0]['graph'])
        if a.live:results['specialist-composition']=stack.run_graph(examples[1]['graph'])
        if a.frontier:results['frontier-forecast-composition']=stack.compose(examples[0]['question'],planning='frontier')
        summary={}
        for name,result in results.items():
            (root/(name+'.json')).write_text(json.dumps(result,indent=2,allow_nan=False))
            replay=stack.run_graph(result['graph'])
            assert replay['cache_hit'] and replay['new_paid_api_calls']==0
            summary[name]={'result_id':result['result_id'],'new_paid_api_calls':result['new_paid_api_calls'],
                           'cache_replay_paid_calls':replay['new_paid_api_calls'],
                           'operations':[r['node']['operation'] for r in result['results']]}
        (root/'assembled-execution.json').write_text(json.dumps(summary,indent=2))
        print(json.dumps(summary,indent=2))
    finally:stack.close()


if __name__=='__main__':main()
