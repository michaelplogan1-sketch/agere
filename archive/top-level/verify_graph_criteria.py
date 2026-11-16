"""Actual free-form synthetic full-population criterion-to-work development check."""
import json,uuid
from pathlib import Path
import verify_graph_decision as V
OUT=Path(__file__).resolve().parent/'artifacts/agere-graph-criteria-v1'
V.OUT=OUT
QUESTION=('Across the entire original population, assess the existing version 2 reduced-EF therapy review criterion, '
          'received affordability-screen requirements, and reported transport difficulty separately. '
          'Also evaluate version 2 of the imported synthetic affordability review program, retaining its unbound attribution input. '
          'Show supported, contradicted and unresolved evidence with each exact criterion version and full population denominator. '
          'Keep the transport and affordability context attached to therapy review without asserting causation, current clinical need, '
          'payer eligibility or program enrollment; retain unsupported inputs rather than dropping people.')
def main():
 graph=V.keep('final-question','ask',{'question':QUESTION,'live':True})
 ops={x['node']['operation'] for x in graph['results']}
 assert {'protocol_review','requirements','program_document','source_state'}<=ops,ops
 protocol=next(x for x in graph['results'] if x['node']['operation']=='protocol_review')
 assert protocol['node']['parameters']['protocol']=='reduced_ef_therapy_review@2'
 decision=V.keep('final-decision','decision/graph-prepare',{'result_id':graph['result_id'],'buyer_id':V.BUYER})
 assert decision['population']['evaluated']==1614 and decision['population']['roster']==1614
 pharmacy=next(x for x in decision['service_scope'] if x['capability']['id']=='pharmacy-review')
 assert pharmacy['candidate_count']==15
 candidates=[m for m in decision['members'] if m['patient_id'] in pharmacy['documentary_candidate_ids']]
 # Select received evidence for the changed-source regression, never a target label.
 patient=next(m['patient_id'] for m in candidates if any(r['operation']=='requirements' and r['status'] in ('supported','contradicted') for r in m['states']))
 member=V.keep('final-member','decision/graph-member',{'decision_id':decision['id'],'patient_id':patient})
 assert member['question']==QUESTION and len(member['findings'])==4
 assert member['service_selection']['need_tags']==['medication_review']
 assert any(g['status']=='clinical_or_program_requirement_unimplemented' for g in member['evidence_gaps'])
 program=next(f for f in member['findings'] if f['operation']=='program_document')
 assert program['criteria']['inputs']['attribution']['value'] is None
 task=V.keep('final-queue','decision/queue',{'decision_id':member['id'],'option_id':member['matching']['options'][0]['id'],'actor':'Synthetic G3 criterion review'})['task']
 handoff=V.keep('final-handoff','decision/event',{'decision_id':member['id'],'task_id':task['id'],'expected_revision':task['revision'],
  'request_id':str(uuid.uuid4()),'action':'prepare_handoff','actor':'Synthetic G3 criterion review','note':'Review exact criteria and unresolved clinical/program prerequisites in the original question.'})
 preview=V.keep('final-acquisition-preview','decision/acquisition-preview',{'decision_id':member['id'],'task_id':task['id']})
 dep=next(d for d in preview['dependencies'] if d['observation']['original_gap'].get('binding')=='attribution')
 request=V.keep('final-acquisition','decision/acquisition-create',{'decision_id':member['id'],'task_id':task['id'],'preview_id':preview['id'],'dependency_id':dep['id'],'actor':'Synthetic G3 criterion review'})
 check=V.keep('final-acquisition-check','decision/acquisition-check',{'request_id':request['result_id']})
 assert check['status']=='criterion_input_unresolved' and not check['task_state_changed']
 evidence={}
 for status in ('supported','contradicted','unresolved'):
  pid=next(m['patient_id'] for m in decision['members'] if any(r['operation']=='requirements' and r['status']==status for r in m['states']))
  m=V.keep('final-'+status+'-member','decision/graph-member',{'decision_id':decision['id'],'patient_id':pid})
  assert next(f for f in m['findings'] if f['operation']=='requirements')['status']==status
  evidence[status]={'patient_id':pid,'member_id':m['id']}
 refresh=V.keep('final-unchanged-refresh','decision/graph-refresh',{'decision_id':decision['id']})
 assert refresh['changes']==[] and refresh['current_decision_id']==decision['id']
 summary={'result_id':graph['result_id'],'decision_id':decision['id'],'member_id':member['id'],'patient_id':patient,
  'task_id':task['id'],'acquisition_id':request['result_id'],'status_examples':evidence,'criteria_counts':{n['node']['id']:n['status_counts'] for n in decision['nodes']},
  'services':[(r['capability']['id'],r['candidate_count']) for r in decision['service_scope']],'new_paid_api_calls':graph['new_paid_api_calls'],
  'question_coverage':graph['answer_coverage'],'planning_economics':graph.get('planning_economics')}
 (OUT/'final-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
