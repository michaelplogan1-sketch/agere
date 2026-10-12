"""Exercise complete original-question composition into existing decision/work APIs."""
import argparse
import json
from pathlib import Path
from verify_complete_plan_http import call,OUT
from verify_buyer_decision_http import BUYER


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--domain',default='payer',choices=['payer','cardiometabolic','medication'])
    parser.add_argument('--planner',choices=['baseline','buyer_documentary_candidate'],default='baseline');args=parser.parse_args()
    folder=OUT/args.domain
    snapshot=json.loads((folder/'snapshot.json').read_text())['snapshot']
    answer=call(folder/'complete-answer.json','source-plan-answers/compose',{'snapshot_id':snapshot['id']})
    canonical=call(folder/'canonical-answer.json','case-synthesis?id='+answer['id'])
    assert canonical==answer
    context=call(folder/'canonical-context.json','context?id='+answer['context_id'])
    binding=call(folder/'source-binding.json','decision/source?answer_id='+answer['id'])
    assert binding['binding']['question']==snapshot['original_question']
    assert len(context['model_context']['sources'])==snapshot['selected_records']
    request={'answer_id':answer['id'],'buyer_id':BUYER,'coverage':{'payer_type':'unknown','plan_id':'','region':'','as_of':'2029-08-15',
        'provenance':'Received source accounts reviewed; exact current benefits remain unverified.','preferences':''}}
    if args.planner!='baseline':request['planner']=args.planner
    decision=call(folder/('decision-'+args.planner+'.json'),'decision/prepare',request)
    assert decision['planning']['unresolved']==answer['unanswered']
    assert decision['question']==snapshot['original_question']
    assert not decision['clinical_execution_authorized']
    options=decision['matching']['options'] if decision['matching'] else []
    assert options,'No supported documentary review option; preserve as measured gap'
    for n,option in enumerate(options):
        assert option['status']=='requires_verification'
        assert all(not r['coverage_established'] for r in option['routes'])
        task=call(folder/f'queue-{n}.json','decision/queue',{'decision_id':decision['id'],'option_id':option['id'],'actor':'Complete-question synthetic integration'})['task']
        h=call(folder/f'handoff-{n}-bound.json','decision/event',{'decision_id':decision['id'],'task_id':task['id'],
            'expected_revision':task['revision'],'request_id':'complete-question-'+decision['id'][:32]+'-'+task['id'][:32],
            'action':'prepare_handoff','actor':'Complete-question synthetic integration','note':'Internal documentary review. No verified benefits, clinical approval or external dispatch.'})
        assert not h['task']['handoff']['human_review_completed'] and not h['task']['handoff']['external_dispatch_performed']
        follow=call(folder/f'followup-{n}.json','decision/followup',{'decision_id':decision['id'],'task_id':task['id'],'answer_id':answer['id']})
        assert follow['status']=='no_new_evidence' and not follow['care_outcome_established']
    view=call(folder/'reopened-decision.json','decision?id='+decision['id'])
    assert view['dependencies_current'] and all(t['binding_current'] for t in view['tasks'])
    summary={'domain':args.domain,'original_question':decision['question'],'source_categories':answer['source_scope'],
        'selected_records':len(answer['record_scope']['record_ids']),'group_answers':answer['coverage']['group_answers'],
        'source_units':answer['coverage']['submitted_units'],'unassessed_units':answer['coverage']['unassessed_units'],
        'cross_group_relationships_assessed':answer['coverage']['cross_group_relations_assessed'],
        'answer_id':answer['id'],'decision_id':decision['id'],'finding_count':len(answer['findings']),
        'review_topics':[t['need_tag'] for t in decision['planning']['review_topics']],
        'work_items':len(view['tasks']),'followups':len(view['followups']),'planner':args.planner,
        'planning_receipts':decision['planning'].get('receipts',[]),'composition_inference_calls':0,
        'clinical_or_benefit_validation':False}
    (folder/'decision.json').write_text(json.dumps(decision,indent=2)+'\n')
    (folder/'workflow-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)

if __name__=='__main__':main()
