"""Offline provider-wire exercise with the actual warehouse catalog, no API calls."""
import argparse
import copy
import json
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import analysis_graph as A, graph_planner as G, planner_scope as S, graph_contracts as C
from clinical_core.agere.clinical.graph_workbench import landmark_examples,snapshot_examples
from clinical_core.agere.clinical.context_student import REQUEST_MAX_BYTES  # shared provider request bound


def exercise(stack):
    stack=Path(stack);meta=json.loads((stack/'run.json').read_text());catalog=A.catalog(stack)
    observed=json.loads((stack/'cohort-values.json').read_text())
    ex=snapshot_examples(landmark_examples(),meta['snapshot']['as_of'])[0]
    question=ex['question'].ljust(1500);calls=[]
    def wire(body,*args,parse,**kwargs):
        size=len(json.dumps(body).encode());assert size<=REQUEST_MAX_BYTES
        schema=body['output_config']['format']['schema'];select='segments' in schema['properties']
        calls.append({'stage':'selection' if select else 'planning','request_bytes':size,
            'request_sha256':S.digest(body),'simulation':True,'provider_called':False})
        if select:
            content=body['messages'][0]['content']; text=content if isinstance(content,str) else ''.join(b.get('text','') for b in content if isinstance(b,dict))
            for op in G.CONTRACTS:assert op in text,op
            result={'segments':[{'text':question,'operations':['cohort','decision_landmark'],'source_streams':[],'unmet':''}]}
        else:
            result=copy.deepcopy(ex['graph'])
            result['nodes']=[{k:v for k,v in n.items() if k!='parameters'}|{'parameters_json':json.dumps(n['parameters'])} for n in result['nodes']]
            if 'requirement_coverage' in schema['properties']:
                result['requirement_coverage']=[{'segment':0,'node_ids':['population','decision'],'unmet':''}]
                if 'coverage' not in schema['properties']:
                    result.pop('coverage');result.pop('unmet_requirements')
        return parse({'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(result)}]}),{'cache_hit':True,'simulation':True}
    with patch.object(G,'send',side_effect=wire):
        graph,receipt=G.propose(question,None,None,snapshot=meta['snapshot'],graph_catalog=catalog,observed_values=observed)
    assert graph==A.validate(ex['graph'],meta['snapshot']['as_of'])
    # Two-stage (capability selection, then planning) when the direct request exceeds REQUEST_MAX_BYTES; one direct
    # planning call when it fits (128 KB bound). Each request is held to the bound inside wire().
    stages=[c['stage'] for c in calls]
    assert stages in (['planning'],['selection','planning']),stages
    assert receipt['new_paid_api_calls']==0 if len(calls)==2 else (receipt.get('simulation') is True and receipt.get('new_paid_api_calls',0)==0)
    result={'synthetic':True,'passed':True,'question_characters':len(question),'calls':calls,'receipt':receipt,
        'provider_called':False,'semantic_model_quality_tested':False,'new_paid_api_calls':0}
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path)
    p.add_argument('--stack',type=Path);args=p.parse_args()
    root=args.output;root.mkdir(exist_ok=False)
    result=exercise(args.stack or stack_path())
    (root/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('passed','question_characters','calls','new_paid_api_calls')},indent=2))


if __name__=='__main__':main()
