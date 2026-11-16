"""Offline maximum-question transport probes against the active real catalog."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import graph_planner as G, analysis_graph as A

CASES = [
    (['cohort','assertion_review','context','worklist'],['medication_orders','pharmacy_dispenses','medication_followup_reports','notes']),
    (['cohort','context','domain_review','worklist'],['notes']),
    (['cohort','context','worklist'],['vitals','labs','conditions','notes']),
    (['cohort','functional_review','worklist'],[]),
    (['cohort','context','worklist'],['appointments']),
    (['cohort','context','source_state','worklist'],['coverage','coverage_verifications','notes'])]


def exercise():
    stack=stack_path();meta=json.loads((stack/'run.json').read_text());catalog=A.catalog(stack)
    observed=json.loads((stack/'cohort-values.json').read_text());results=[]
    for ops,streams in CASES:
        question='For patient P-1001, transport contract probe.'.ljust(1500);calls=[]
        def send(body,*args,parse,**kwargs):
            calls.append(len(json.dumps(body).encode()))
            schema=body['output_config']['format']['schema']
            if 'segments' in schema['properties']:
                allowed=set(schema['properties']['segments']['items']['properties']['source_streams']['items']['enum'])
                assert set(streams)<=allowed
                value={'segments':[{'text':question,'operations':ops,'source_streams':streams,'unmet':''}]}
            else:
                value={'interpretation':'Transport probe only','nodes':[{'id':'p','operation':'cohort','inputs':[],
                    'purpose':'Exact declared patient','parameters':{'specification':{
                        'all':[{'kind':'patient','field':'patient_id','op':'eq','value':'P-1001'}],'any':[],'exclude':[]}}}],
                    'requirement_coverage':[{'segment':0,'node_ids':['p'],'unmet':'Offline transport exercise; no workflow delivery asserted.'}]}
            if 'nodes' in value:
                value['nodes']=[{k:v for k,v in n.items() if k!='parameters'}|{'parameters_json':json.dumps(n['parameters'])} for n in value['nodes']]
            return parse({'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(value)}]}),{'cache_hit':True,'simulation':True}
        with patch.object(G,'send',side_effect=send):
            graph,receipt=G.propose(question,None,None,snapshot=meta['snapshot'],graph_catalog=catalog,observed_values=observed)
        results.append({'operations':ops,'streams':streams,'calls':calls,'plan_bytes':receipt['scope']['plan_request_bytes'],
                        'wire_passed':len(calls)==2,'simulation':True,'provider_called':False})
    return results


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    rows=exercise()
    with a.output.open('x') as f:json.dump(rows,f,indent=2);f.write('\n')
    print(json.dumps(rows,indent=2));assert all(r['wire_passed'] for r in rows)
