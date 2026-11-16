"""Actual original-question conditional map, comparison, request and replay."""
from pathlib import Path
import json
import verify_graph_decision as V
OUT=Path(__file__).resolve().parent/'artifacts/agere-decision-information-v1'
V.OUT=OUT

def main():
    source=json.loads((OUT.parent/'agere-reference-assessment-v1/workflow-verification.json').read_text())
    member=V.keep('member-before','decision?id='+source['member_id'])
    a=V.keep('assessment-before','decision/reference-open',{'assessment_id':source['assessment_id']})
    assert a['current']
    p=V.keep('proposal','decision/information-propose',{'assessment_id':source['assessment_id']})['proposal']
    assert p['question']==member['decision']['question'] and p['task_id']==source['task_id']
    c=V.keep('comparison','decision/information-compare',{'proposal_id':p['result_id'],'review_slots':1})
    assert c['calculation']['comparison']['complete']
    chosen=c['calculation']['comparison']['optimal']['request_ids'];assert chosen
    r=V.keep('request','decision/information-request',{'comparison_id':c['result_id'],'request_id':chosen[0],'actor':'Synthetic decision-information verifier'})
    assert r['owner']==p['owner'] and r['selected_by_structural_comparison'] and not r['request_sent']
    replay=V.keep('proposal-replay','decision/information-propose',{'assessment_id':source['assessment_id']})
    assert replay['proposal']==p and replay['new_paid_api_calls']==0
    opened=V.keep('information-open','decision/information-open',{'result_id':r['result_id']});assert opened['current']
    after=V.keep('member-after','decision?id='+source['member_id']);assert after['decision']==member['decision'] and after['tasks']==member['tasks']
    parent=V.keep('parent','decision?id='+source['parent_id']);assert parent['dependencies_current'] and parent['decision']['population']['evaluated']==1614
    summary={'status':'passed',**{k:source[k] for k in ['member_id','parent_id','task_id','assessment_id','request_id']},
        'proposal_id':p['result_id'],'comparison_id':c['result_id'],'information_request_id':r['result_id'],
        'facts':len(p['model']['facts']),'review_choices':len(p['model']['choices']),'missing_questions':len(p['requests']),
        'unmapped':p['model']['unmapped'],'selected_question':r['request']['question'],
        'covered_choices':c['calculation']['comparison']['optimal']['covered_checks'],
        'question_order_baseline':c['calculation']['original_question_order_baseline']['covered_checks'],
        'greedy_baseline':c['calculation']['comparison']['greedy_baseline']['covered_checks'],
        'original_population_retained':1614,'original_work_unchanged':True,'replay_calls':0,
        'clinical_qualification':False,'baseline_scope':'Conditional coverage only; no expected benefit or real-world acquisition advantage claimed'}
    (OUT/'workflow-verification.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)

if __name__=='__main__':main()
