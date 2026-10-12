"""Attach actual complete-source-selection evidence while retaining G2 retrieval misses."""
import json
from pathlib import Path
from clinical_core.agere.workflow_scorecard import build
from verify_buyer_decision_acceptance import read,write,trace
from verify_complete_plan_http import OUT

stages=read('artifacts/agere-complete-question-v1/stage-trace-map.json')
planning=read('artifacts/agere-complete-question-v1/planning-trace-map.json')
for domain in ('payer','cardiometabolic','medication'):
    folder=OUT/domain
    if not (folder/'reopened-decision.json').exists():continue
    d=read(folder/'decision.json');view=read(folder/'reopened-decision.json')
    a=read(folder/'canonical-answer.json');c=read(folder/'canonical-context.json');s=read(folder/'snapshot.json')
    h=read(folder/'handoff-0-bound.json');task_id=h['task']['id']
    paths={'complete_source_selection':trace(folder,'complete_source_selection',{'plan':s['plan'],'snapshot':s['snapshot'],'context':c}),
        'source_scope':trace(folder,'source_scope',{'schema':'question-source-scope/2','patient_id':d['patient_id'],'question':d['question'],
             'context_id':d['context_id'],'context_omissions':c['omissions'],'selected_categories':a['source_scope'],
             'selected_record_count':len(c['model_context']['sources']),'decision_id':d['id']}),
        'source_interpretation':trace(folder,'source_interpretation',a),'decision_context':trace(folder,'decision_context',d),
        'connected_matching':trace(folder,'connected_matching',d['matching']),'connected_handoff':trace(folder,'connected_handoff',h),
        'task_reopen':trace(folder,'task_reopen',{'task':next(t['task'] for t in view['tasks'] if t['task']['id']==task_id),'decision_id':d['id']}),
        'followup':trace(folder,'followup',next(f for f in view['followups'] if f['task_id']==task_id))}
    trace(folder,'planning',d['planning']);stages[domain].update(paths);planning[domain]=str(folder)
old=read('artifacts/agere-workflow-evaluation-v1/scorecard.json')
report=build(old['plan_path'],'artifacts/agere-workflow-evaluation-v1/baseline',planning,stages)
for k in ('previous_release_planning','previous_release_source_interpretation','usage_accounting'):
    if k in old:report[k]=old[k]
report['assessment_basis']='Actual original-question decision/work traces. Complete source-plan selections explicitly verify received category transport while historical G2 retrieval and ranking misses remain unchanged. Complete scope is not clinical completeness; cross-group unknowns persist.'
report['known_gaps']=[r['id']+': '+', '.join(x['id']+'='+x['status'] for x in r['acceptance']['checks'] if x['status']!='passed') for r in report['cases'] if r['acceptance']['status']!='passed']
write(OUT/'stage-trace-map.json',stages);write(OUT/'planning-trace-map.json',planning);write(OUT/'scorecard.json',report)
write('artifacts/agere-workflow-evaluation-v1/scorecard.json',report)
print(json.dumps({'complete_individual_workflows':report['complete_workflows'],'remaining':report['known_gaps'],
    'source_selection_decision_status':report.get('source_selection_decision',{}).get('status')}))
