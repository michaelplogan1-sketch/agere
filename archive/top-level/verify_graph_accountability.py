"""Free-form synthetic ACO/provider planning through the shared question/work flow."""
import json,uuid
from pathlib import Path
import verify_graph_decision as V
OUT=Path(__file__).resolve().parent/'artifacts/agere-graph-accountability-v1';V.OUT=OUT
QUESTION=('For ACO and provider population planning, use the entire original population without assuming anyone is attributed. '
 'Show received care requests and linked performed-service reports, retaining unobserved completion and every patient. '
 'Alongside that, calculate admission records within 30 days after the first discharge notification from 2029-01-01 through 2029-06-30, using records received by 2029-08-15. '
 'Reconcile received financial account snapshots with actual payment and refund reports as of 2029-08-15, and inspect program-payment evidence prerequisites. '
 'Also compose the existing imported synthetic affordability measure denominator and numerator packages, leaving exclusions and attribution explicitly unbound. '
 'Keep these as separate evidence panels in the same original-question review and follow-up. Preserve exact source and specification versions, full denominators and unresolved inputs. '
 'Explain what remains unsupported for provider comparison or an outcome-aligned payment decision; do not infer verified readmissions, clinical need, payer attribution or savings.')

def main():
 graph=V.keep('final-question','ask',{'question':QUESTION,'live':True})
 ops={x['node']['operation'] for x in graph['results']}
 assert {'source_state','measure_review','financial_accounts','accountability_review','outcome'}<=ops,ops
 decision=V.keep('final-decision','decision/graph-prepare',{'result_id':graph['result_id'],'buyer_id':V.BUYER})
 assert decision['population']['evaluated']==1614
 for n in decision['nodes']:
  assert n['patients_evaluated']==1614
 navigator=next(s for s in decision['service_scope'] if s['capability']['id']=='navigation-review')
 assert navigator['candidate_count']>0
 # Prefer actual received financial accounts on a legitimately nominated member.
 patient=next(m['patient_id'] for m in decision['members'] if m['patient_id'] in navigator['documentary_candidate_ids'] and any(r['operation']=='financial_accounts' and r['status']=='review_required' for r in m['states']))
 member=V.keep('final-member','decision/graph-member',{'decision_id':decision['id'],'patient_id':patient})
 assert member['question']==QUESTION and {'measure_review','financial_accounts','accountability_review','outcome'}<={f['operation'] for f in member['findings']}
 assert member['service_selection']['need_tags']==['care_navigation']
 measure=next(f for f in member['findings'] if f['operation']=='measure_review');assert {'attribution','exclusion'}<=set(measure['report']['unresolved_components'])
 task=V.keep('final-queue','decision/queue',{'decision_id':member['id'],'option_id':member['matching']['options'][0]['id'],'actor':'Synthetic ACO source review'})['task']
 V.keep('final-handoff','decision/event',{'decision_id':member['id'],'task_id':task['id'],'expected_revision':task['revision'],'request_id':str(uuid.uuid4()),'action':'prepare_handoff','actor':'Synthetic ACO source review','note':'Review source-supported navigation with the original question’s separate financial, outcome and attribution evidence; no payment or clinical decision.'})
 preview=V.keep('final-acquisition-preview','decision/acquisition-preview',{'decision_id':member['id'],'task_id':task['id']})
 dep=next(d for d in preview['dependencies'] if d['observation']['original_gap'].get('binding')=='patient_attribution')
 request=V.keep('final-acquisition','decision/acquisition-create',{'decision_id':member['id'],'task_id':task['id'],'preview_id':preview['id'],'dependency_id':dep['id'],'actor':'Synthetic ACO source review'})
 check=V.keep('final-acquisition-check','decision/acquisition-check',{'request_id':request['result_id']})
 assert check['status']=='accountability_input_requires_review' and not check['task_state_changed']
 refresh=V.keep('final-unchanged-refresh','decision/graph-refresh',{'decision_id':decision['id']});assert refresh['changes']==[]
 summary={'result_id':graph['result_id'],'decision_id':decision['id'],'member_id':member['id'],'patient_id':patient,'task_id':task['id'],'acquisition_id':request['result_id'],'nodes':[{'operation':n['node']['operation'],'patients_evaluated':n['patients_evaluated'],'status_counts':n['status_counts'],'report_summary':n['report_summary']} for n in decision['nodes']],'services':[(s['capability']['id'],s['candidate_count']) for s in decision['service_scope']],'gaps':len(member['evidence_gaps']),'source_versions':len(member['source_versions']['records']),'answer_coverage':graph['answer_coverage'],'new_paid_api_calls':graph['new_paid_api_calls'],'planning_economics':graph.get('planning_economics')}
 (OUT/'final-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('connected_accountability_workflow_passed',flush=True)
if __name__=='__main__':main()
