"""Preserve a completed source stage after a later failed reconciliation; never resend."""
import argparse,json
from verify_complete_plan_http import call,OUT
p=argparse.ArgumentParser();p.add_argument('--domain',required=True);p.add_argument('--group',type=int,required=True);args=p.parse_args()
folder=OUT/args.domain;plan=json.loads((folder/'source-plan.json').read_text())['plan']
g=next(g for g in plan['groups'] if g['group_number']==args.group)
error=folder/f'group-{args.group}-answer-error.json'
if not error.exists():raise ValueError('An observed saved pipeline failure is required')
progress=call(folder/f'group-{args.group}-reopened-source-stage.json','context/source-stage?context_id='+g['context_id'])
stage=progress.get('answer')
if not stage or stage.get('status')!='complete':raise ValueError('Source stage is not complete; no substitute answer created')
key=stage['id']
answer=call(folder/f'group-{args.group}-source-stage.json','case-synthesis?id='+key)
assert answer['context_id']==g['context_id'] and answer['coverage']['complete_declared_scope']
attached=call(folder/f'group-{args.group}-source-stage-attachment.json','source-plan-answers/attach',{'plan_id':plan['id'],'answer_id':answer['id']})
print(json.dumps({'domain':args.domain,'group':args.group,'saved_source_stage':answer['id'],
    'source_units':answer['coverage']['submitted_units'],'unassessed_units':answer['coverage']['unassessed_units'],
    'cross_group_relations_assessed':answer['coverage']['cross_group_relations_assessed'],
    'reconciliation_failure_preserved':str(error),'new_model_calls':0,'plan_complete':False}))
