"""Run connected source-interpretation populations without new model calls."""
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import analysis_graph as graph, interpretation_state as state
from clinical_core.agere.clinical.graph_workbench import interpretation_examples
from clinical_core.agere.clinical.graph_planner import propose
from clinical_core.agere.clinical.economical_planner import local_plan


def main():
    root=Path('artifacts/agere-population-stack-v3');out=Path('artifacts/agere-interpretation-state-v1');out.mkdir(exist_ok=True)
    meta=json.loads((root/'run.json').read_text());stack=SimpleNamespace(path=root,retriever=SimpleNamespace(meta=meta),allow_live=False)
    runs=[]
    for example in interpretation_examples():
        plan,route=local_plan(example['question'],meta['snapshot']['as_of']);assert route=='registered_example'
        with patch('urllib.request.urlopen',side_effect=AssertionError('No provider calls allowed')):
            result=graph.execute(stack,plan);replay=graph.execute(stack,plan)
        assert replay['cache_hit'] and replay['new_paid_api_calls']==0 and result['new_paid_api_calls']==0
        detail=result['results'][1]['result']
        assert detail['patients_evaluated']==detail['input_membership']['counts']['matched']
        assert sum(detail['counts'][k] for k in ('matched','not_matched','unknown'))==detail['counts']['evaluated']
        runs.append({'title':example['title'],'question':example['question'],'local_planner':route,'result':result,
                     'replay':{'cache_hit':True,'new_paid_api_calls':0,'result_id':replay['result_id']}})
        (out/'run.json').write_text(json.dumps({'synthetic':True,'runs':runs,'new_paid_api_calls':0},indent=2))
        print(json.dumps({'title':example['title'],'patients':detail['patients_evaluated'],'membership':detail['counts'],
                          'status_counts':detail['status_counts'],'processing':detail['processing_counts'],'seconds':result['elapsed_seconds']}),flush=True)
    sizes=[]
    with patch('clinical_core.agere.clinical.graph_planner.send',side_effect=lambda body,*a,**k:sizes.append(len(json.dumps(body).encode()))):
        propose('x'*1500,root,None,snapshot=meta['snapshot'],graph_catalog=graph.catalog(root),observed_values=json.loads((root/'cohort-values.json').read_text()))
    assert sizes[0]<=20000, sizes[0]
    (out/'run.json').write_text(json.dumps({'synthetic':True,'runs':runs,'new_paid_api_calls':0,'planner_max_ascii_request_bytes':sizes[0],
        'scope':'Full-population documentary interpretation/review accounting, not clinical accuracy, eligibility or model selection.'},indent=2))
    print('Bounded planner bytes: '+str(sizes[0]))

if __name__=='__main__':main()
