"""Execute real synthetic warehouse questions through the running integrated stack."""
from pathlib import Path
import json,time,urllib.request

OUT=Path('artifacts/agere-integrated-stack-v1/examples');OUT.mkdir(exist_ok=True)
BASE='http://127.0.0.1:8884'
def post(name,path,body):
    t=time.perf_counter();req=urllib.request.Request(BASE+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=150) as r:result=json.loads(r.read())
    (OUT/(name+'.json')).write_text(json.dumps(result,indent=2,allow_nan=False))
    summary={'name':name,'seconds':time.perf_counter()-t,'new_paid_api_calls':result.get('new_paid_api_calls',0)}
    if 'plan' in result:summary.update(coverage=result['plan']['coverage'],operations=[s['operation'] for s in result['plan']['steps']],unmet_requirements=result['plan']['unmet_requirements'])
    if 'retrieval' in result:summary['retrieved_sources']=len(result['retrieval']['evidence'])
    if 'summary' in result:summary['calculation']=result['summary']
    print(json.dumps(summary),flush=True);return result,summary

if __name__=='__main__':
    summaries=[]
    r,s=post('outside-nephrology','/api/stack/search',{'question':'Outside nephrology records requested and nephrology attendance','patient':''});summaries.append(s)
    for name,q in [('renal-question','Whose kidney function is declining, and what data is missing to understand their kidney failure risk?'),
                   ('hf-question','Find HFrEF patients not on GDMT or maximum tolerated beta-blocker doses, including treatment holds and outside medication use.')]:
        r,s=post(name,'/api/stack/ask',{'question':q,'live':True});summaries.append(s)
    r,s=post('configured-programs','/api/stack/operator',{'operator':'program_rule_review','view':'all','patient':'','drug_class':None});summaries.append(s)
    sources=json.loads((OUT/'outside-nephrology.json').read_text())['retrieval']['evidence'];pid=sources[0]['source']['patient_id']
    for provider in ('perplexity','jev'):
        r,s=post(provider+'-patient-review','/api/stack/review',{'question':'Outside nephrology records were requested for this patient.','patient':pid,'provider':provider});summaries.append(s)
    (OUT/'summary.json').write_text(json.dumps({'synthetic':True,'executions':summaries,
       'scope':'Live connector and operator execution on actual synthetic warehouse evidence; not a model accuracy benchmark.'},indent=2))
