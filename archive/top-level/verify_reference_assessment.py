"""Live original-question evidence assessment, exact citations and cache replay."""
from pathlib import Path
import json
import verify_graph_decision as V

OUT=Path(__file__).resolve().parent/'artifacts/agere-reference-assessment-v1'
V.OUT=OUT


def main():
    s=json.loads((OUT.parent/'agere-graph-references-v1/final-summary.json').read_text())
    old=V.keep('delivered-member-before','decision?id='+s['member_id'])
    request={'request_id':s['acquisition_id'],'additional_record_ids':[],'previous_assessment_id':None}
    p=V.keep('delivered-preview','decision/reference-preview',request)
    prep=V.keep('delivered-prepared','decision/reference-prepare',{'request':request,'preview_id':p['id']})
    result=V.keep('delivered-assessment','decision/reference-assess',{'preparation_id':prep['result_id']})
    a=result['assessment']
    assert a['question']==old['decision']['question'] and a['task_id']==s['task_id']
    assert a['reference']['id']==s['reference_id'] and a['available_by']=='2029-08-15'
    assert a['coverage']['accounted_findings']==len(a['evidence']) and a['assessment']['missing']
    assert not a['clinical_applicability_established'] and not a['task_state_changed']
    citations=[c for f in a['evidence'] for c in f['exact_citations']]
    assert citations and all(c['citation_verified'] and c['source']['patient_id']==s['patient_id'] and c['source_unit_id'] and c['field'] is not None for c in citations)
    assert all(c['source']['record_hash']==p['received_inventory']['records'][c['record_id']]['record_hash'] for c in citations)
    current=V.keep('delivered-open','decision/reference-open',{'assessment_id':a['result_id']});assert current['current']
    replay=V.keep('delivered-replay','decision/reference-assess',{'preparation_id':prep['result_id']})
    assert replay['assessment']==a and replay['new_paid_api_calls']==0
    after=V.keep('delivered-member-after','decision?id='+s['member_id'])
    assert old['decision']==after['decision'] and old['tasks']==after['tasks'] and after['dependencies_current']
    parent=V.keep('delivered-parent','decision?id='+s['decision_id'])
    assert parent['dependencies_current'] and parent['decision']['population']['evaluated']==1614
    summary={'status':'passed','member_id':s['member_id'],'parent_id':s['decision_id'],'request_id':s['acquisition_id'],
        'task_id':s['task_id'],'preparation_id':prep['result_id'],'assessment_id':a['result_id'],
        'population_retained':1614,'original_question_and_cutoff_retained':True,'work_unchanged':True,
        'coverage':a['coverage'],'exact_citations_verified':len(citations),
        'current':current['current'],'same_answer_replay':True,'replay_paid_calls':0,'clinical_qualification':False}
    (OUT/'workflow-verification.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
