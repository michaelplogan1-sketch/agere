"""Connect actual decision receipts to the existing six-case acceptance report."""
import hashlib
import json
from pathlib import Path
import urllib.request
from urllib.error import HTTPError
from clinical_core.contracts import digest
from clinical_core.agere.workflow_scorecard import build

OUT=Path('artifacts/agere-question-decision-v1')
def read(p):return json.loads(Path(p).read_text())
def write(p,v):Path(p).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')
def get(route):
    with urllib.request.urlopen('http://localhost:8885/api/stack/'+route,timeout=180) as response:return json.load(response)
def trace(name,value):
    p=OUT/'functional'/(name+'.json');write(p,{'status':'executed','response':value});return str(p)

def main():
    (OUT/'functional').mkdir(exist_ok=True)
    d=read(OUT/'decision.json');view=get('decision?id='+d['id'])
    assert view['dependencies_current']
    a=get('case-synthesis?id='+d['answer_id']);c=get('context?id='+d['context_id'])
    assert digest(a)==d['answer_digest'] and digest(c)==d['source_versions']['context_digest']
    write(OUT/'context.json',c);write(OUT/'answer.json',a)
    paths={
        'source_scope':trace('source_scope',{'schema':'question-source-scope/2','patient_id':d['patient_id'],
            'question':d['question'],'context_id':d['context_id'],'context_omissions':c['omissions'],
            'selected_categories':sorted({s['source']['source_table'] for s in c['model_context']['sources'].values()}),
            'selected_record_count':len(c['model_context']['sources']),'decision_id':d['id']}),
        'source_interpretation':trace('source_interpretation',a),
        'decision_context':trace('decision_context',d),
        'connected_matching':trace('connected_matching',d['matching']),
        'connected_handoff':trace('connected_handoff',read(OUT/'handoff.json')),
        'task_reopen':trace('task_reopen',{'task':view['tasks'][0]['task'],'decision_id':d['id']}),
        'followup':trace('followup',view['followups'][-1]),
    }
    trace('planning',d['planning'])
    stages=read('artifacts/agere-release-acceptance-v1/stage-trace-map.json');stages['functional'].update(paths)
    planning=read('artifacts/agere-planner-contracts-v1/planning-trace-map.json');planning['functional']=str(OUT/'functional')
    write(OUT/'stage-trace-map.json',stages);write(OUT/'planning-trace-map.json',planning)
    old=read('artifacts/agere-workflow-evaluation-v1/scorecard.json')
    report=build(old['plan_path'],'artifacts/agere-workflow-evaluation-v1/baseline',planning,stages)
    f=next(r for r in report['cases'] if r['id']=='functional')
    assert f['acceptance']['status']=='passed', f['acceptance']
    assert report['complete_workflows']==1
    assert not f['stages']['matching']['manual_service_selection']
    for key in ('previous_release_planning','previous_release_source_interpretation','usage_accounting'):
        if key in old:report[key]=old[key]
    report['assessment_basis']='Functional case now uses an exact saved original-question decision, local review-topic baseline, existing service/work chain and documentary follow-up. Five remaining cases retain prior observations. Clinical and service suitability remain unqualified.'
    report['known_gaps']=[r['id']+': '+', '.join(x['id']+'='+x['status'] for x in r['acceptance']['checks'] if x['status']!='passed') for r in report['cases'] if r['acceptance']['status']!='passed']
    write(OUT/'scorecard.json',report);write('artifacts/agere-workflow-evaluation-v1/scorecard.json',report)
    # Cross-domain integration diagnostic uses saved IDs only; no target tags or
    # expectations enter the planner and no failed provider request is retried.
    ids={'respiratory':'ad5238131ce28df4455561c1175f8baea769de5f335ace78be4d5dceb585bf02',
        'cardiometabolic':'4830b515bc1aa797e812491a5128cf59dc12a8bd248c61666dbf534029795b60',
        'appointment':'3965a947d3dc01ec5a44aa376fca68901996b784f51486e4e9cf9c117fb869ba',
        'payer':'7391162c7b9e93ac82074b10504d9e90f4b3747fafbb7046c44629d3994e3a0b'}
    probes={}
    for name,key in ids.items():
        try:
            r=get('decision/source?answer_id='+key);write(OUT/(name+'-source-probe.json'),r)
            probes[name]={'status':'executed','topics':[t['need_tag'] for t in r['planning']['review_topics']],
                'unmapped_findings':len(r['planning']['unmapped_finding_ids']),'new_paid_api_calls':0}
        except HTTPError as exc:probes[name]={'status':'blocked','http_status':exc.code,'error':exc.read().decode()}
    probes['medication']={'status':'blocked','reason':'No complete answer in the prior six-case source-scope execution; historical blocked receipt retained. No retry.'}
    write(OUT/'cross-domain-probe.json',probes)
    print(json.dumps({'functional_acceptance':f['acceptance']['status'],'complete_individual_workflows':1,'g3_complete':False,'other_domains':probes}))

if __name__=='__main__':main()
