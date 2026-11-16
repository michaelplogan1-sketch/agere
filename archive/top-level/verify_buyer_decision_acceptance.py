"""Actual original-question decision traces; mechanical acceptance only."""
import json
from pathlib import Path
import urllib.request
from clinical_core.contracts import digest
from clinical_core.agere.workflow_scorecard import build
from verify_buyer_decision_http import call,OUT,BUYER

def read(p):return json.loads(Path(p).read_text())
def write(p,v):Path(p).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')
def get(route):
    with urllib.request.urlopen('http://localhost:8885/api/stack/'+route,timeout=180) as r:return json.load(r)
def trace(folder,name,value):
    p=folder/(name+'.json');write(p,{'status':'executed','response':value});return str(p)

def main():
    if (OUT/'scorecard.json').exists():raise ValueError('Completed acceptance evidence already exists; preserve it')
    stages=read('artifacts/agere-question-decision-v1/stage-trace-map.json')
    planning=read('artifacts/agere-question-decision-v1/planning-trace-map.json')
    for domain in ['respiratory','payer','cardiometabolic','appointment']:
        d=read(OUT/(domain+'-decision.json'));view=get('decision?id='+d['id'])
        a=get('case-synthesis?id='+d['answer_id']);c=get('context?id='+d['context_id'])
        assert view['dependencies_current'] and digest(a)==d['answer_digest'] and digest(c)==d['source_versions']['context_digest']
        folder=OUT/domain;folder.mkdir(exist_ok=True);write(folder/'canonical-answer.json',a);write(folder/'canonical-context.json',c)
        handoff=read(OUT/(domain+'-handoff-0.json'));task_id=handoff['task']['id']
        reopened=next(t['task'] for t in view['tasks'] if t['task']['id']==task_id)
        reassessed=next(f for f in view['followups'] if f['task_id']==task_id)
        paths={'source_scope':trace(folder,'source_scope',{'schema':'question-source-scope/2','patient_id':d['patient_id'],
                'question':d['question'],'context_id':d['context_id'],'context_omissions':c['omissions'],
                'selected_categories':sorted({s['source']['source_table'] for s in c['model_context']['sources'].values()}),
                'selected_record_count':len(c['model_context']['sources']),'decision_id':d['id']}),
            'source_interpretation':trace(folder,'source_interpretation',a),
            'decision_context':trace(folder,'decision_context',d),
            'connected_matching':trace(folder,'connected_matching',d['matching']),
            'connected_handoff':trace(folder,'connected_handoff',handoff),
            'task_reopen':trace(folder,'task_reopen',{'task':reopened,'decision_id':d['id']}),
            'followup':trace(folder,'followup',reassessed)}
        trace(folder,'planning',d['planning']);stages[domain].update(paths);planning[domain]=str(folder)
    old=read('artifacts/agere-workflow-evaluation-v1/scorecard.json')
    report=build(old['plan_path'],'artifacts/agere-workflow-evaluation-v1/baseline',planning,stages)
    for k in ('previous_release_planning','previous_release_source_interpretation','usage_accounting'):
        if k in old:report[k]=old[k]
    report['assessment_basis']='Five individual paths use actual original-question decisions and reopened internal work. Functional uses the local source-category baseline; four domains use an explicit unqualified buyer-aware documentary candidate. Mechanical lineage and transport checks do not qualify semantic/clinical/service correctness.'
    report['known_gaps']=[r['id']+': '+', '.join(x['id']+'='+x['status'] for x in r['acceptance']['checks'] if x['status']!='passed') for r in report['cases'] if r['acceptance']['status']!='passed']
    write(OUT/'stage-trace-map.json',stages);write(OUT/'planning-trace-map.json',planning)
    write(OUT/'scorecard.json',report);write('artifacts/agere-workflow-evaluation-v1/scorecard.json',report)
    print(json.dumps({'complete_individual_workflows':report['complete_workflows'],'remaining':report['known_gaps']}))

    # Replay two actual saved interpretations of identical received evidence.
    # This demonstrates answer-version reassessment, not new source arrival.
    parent='b75723008578a17525394b8887799dd8e014c5209eb6619e8b9083caaa48fc6c'
    current=read(OUT/'appointment-decision.json')['answer_id']
    coverage=read(OUT/'appointment-decision.json')['request']['coverage']
    d=call('changed-parent-decision','decision/prepare',{'answer_id':parent,'buyer_id':BUYER,'coverage':coverage})
    option=d['matching']['options'][0]
    task=call('changed-parent-queue','decision/queue',{'decision_id':d['id'],'option_id':option['id'],'actor':'G3 documentary version replay'})['task']
    follow=call('changed-answer-followup','decision/followup',{'decision_id':d['id'],'task_id':task['id'],'answer_id':current})
    assert follow['interpretation_changed'] and follow['status']=='source_or_interpretation_changed_requires_review'
    assert follow['changes']==[] and follow['previous_answer_digest']==d['answer_digest']
    assert not follow['care_outcome_established']
    reopened=call('changed-answer-reopened','decision?id='+d['id'])
    assert reopened['tasks'][0]['task']==task and reopened['followups'][-1]==follow
    print(json.dumps({'changed_answer_reassessment':'passed','source_changes':0,'work_identity_unchanged':True,'new_inference_for_reassessment':False}))

if __name__=='__main__':main()
