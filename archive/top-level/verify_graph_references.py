"""Actual free-form scientific/operational question through original review and work."""
import json,uuid
from pathlib import Path
import verify_graph_decision as V
OUT=Path(__file__).resolve().parent/'artifacts/agere-graph-references-v1';V.OUT=OUT
QUESTION=('For the entire original patient population as of 2029-08-15, prepare a review of whether the registered ATS pulmonary rehabilitation delivery-choice reference could apply, without assuming clinical eligibility. '
 'Retain every patient. Show received care requests and linked performed-service reports, with unobserved completion explicit. '
 'Alongside these, reconcile received financial accounts, payments and refunds as operational context. '
 'Attach the exact available dated scientific reference and its six applicability questions to the same patient evidence and original-question review. '
 'Link relevant care documentation and financial context to the reference questions where appropriate, but do not infer respiratory stability, assessment, preference, access, coverage or current guidance from their presence. '
 'Preserve missing applicability evidence, exact source/reference versions and receipt cutoffs. Keep financial observations separate from hardship, coverage and eligibility; keep guideline population statements separate from patient findings. '
 'Allow source-supported internal navigation review while leaving clinical applicability, individual benefit and reference currency unresolved.')

def main():
 graph=V.keep('final-question','ask',{'question':QUESTION,'live':True})
 ops={x['node']['operation'] for x in graph['results']};assert {'reference_review','source_state','financial_accounts'}<=ops,ops
 decision=V.keep('final-decision','decision/graph-prepare',{'result_id':graph['result_id'],'buyer_id':V.BUYER})
 assert decision['population']['evaluated']==1614 and all(n['patients_evaluated']==1614 for n in decision['nodes'])
 nav=next(s for s in decision['service_scope'] if s['capability']['id']=='navigation-review');assert nav['candidate_count']>0
 patient='P-1001';assert patient in nav['documentary_candidate_ids']
 member=V.keep('final-member','decision/graph-member',{'decision_id':decision['id'],'patient_id':patient})
 ref=next(f for f in member['findings'] if f['operation']=='reference_review')
 assert member['question']==QUESTION and len(ref['report']['questions'])==6 and not ref['report']['clinical_applicability_established']
 assert all(q['status']=='applicability_unresolved' for q in ref['report']['questions'])
 task=V.keep('final-queue','decision/queue',{'decision_id':member['id'],'option_id':member['matching']['options'][0]['id'],'actor':'Synthetic scientific-context review'})['task']
 V.keep('final-handoff','decision/event',{'decision_id':member['id'],'task_id':task['id'],'expected_revision':task['revision'],'request_id':str(uuid.uuid4()),'action':'prepare_handoff','actor':'Synthetic scientific-context review','note':'Review exact documentary navigation evidence and unresolved scientific applicability in the original population question. No clinical action.'})
 preview=V.keep('final-acquisition-preview','decision/acquisition-preview',{'decision_id':member['id'],'task_id':task['id']})
 dep=next(d for d in preview['dependencies'] if d['observation']['original_gap'].get('reference_id') and d['observation']['original_gap']['binding']=='access')
 request=V.keep('final-acquisition','decision/acquisition-create',{'decision_id':member['id'],'task_id':task['id'],'preview_id':preview['id'],'dependency_id':dep['id'],'actor':'Synthetic scientific-context review'})
 check=V.keep('final-acquisition-check','decision/acquisition-check',{'request_id':request['result_id']})
 assert check['status']=='applicability_unresolved' and not check['task_state_changed']
 refresh=V.keep('final-unchanged-refresh','decision/graph-refresh',{'decision_id':decision['id']});assert refresh['changes']==[]
 summary={'result_id':graph['result_id'],'decision_id':decision['id'],'member_id':member['id'],'patient_id':patient,'task_id':task['id'],'acquisition_id':request['result_id'],'reference_id':ref['report']['reference']['id'],'nodes':[{'operation':n['node']['operation'],'patients_evaluated':n['patients_evaluated'],'status_counts':n['status_counts'],'report_summary':n['report_summary']} for n in decision['nodes']],'services':[(s['capability']['id'],s['candidate_count']) for s in decision['service_scope']],'gaps':len(member['evidence_gaps']),'source_versions':len(member['source_versions']['records']),'answer_coverage':graph['answer_coverage'],'new_paid_api_calls':graph['new_paid_api_calls'],'planning_economics':graph.get('planning_economics')}
 (OUT/'final-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print('connected_scientific_reference_workflow_passed',flush=True)
if __name__=='__main__':main()
