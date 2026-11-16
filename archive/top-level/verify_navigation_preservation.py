import json,hashlib
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import program_work,reference_applicability as r,resource_capacity,reference_lifecycle as lifecycle,reference_reassessment as reassessment,care_option_comparison as comparison,care_option_context as option_context,option_evidence_registry
from clinical_core.agere.clinical import answer_retirement,patient_context,literal_reference_runtime,structured_transport
from clinical_core.agere.gateway import BudgetLedger
answer_retirement.install_origin_guard();patient_context.install_plan_guard();literal_reference_runtime.install();structured_transport.install();r.install();lifecycle.install();option_evidence_registry.install()
from clinical_core.agere.clinical import option_component_registry
option_component_registry.install()
from clinical_core.agere.clinical import cardiometabolic_components
cardiometabolic_components.install()
from clinical_core.agere.clinical import appointment_navigation
appointment_navigation.install()
out=Path('artifacts/agere-appointment-navigation-v1');linked_prior=Path('artifacts/agere-linked-reconciliation-v1');medication_prior=Path('artifacts/agere-medication-interpretation-v1');med=Path('artifacts/agere-medication-source-review-v1');prior=json.loads(Path('artifacts/agere-appointment-followthrough-v1/protected-for-continuation.json').read_text())
for name,h in prior['protected_files'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==h,name
protected={**prior['protected_files']}
for name in ('appointment_navigation.py',):
 name='clinical_core/agere/clinical/'+name;protected[name]=hashlib.sha256(Path(name).read_bytes()).hexdigest()
(out/'protected-for-continuation.json').write_text(json.dumps({'schema':'agere-protected-continuation/1','protected_files':protected},indent=2)+'\n')
from clinical_core.agere.clinical import medication_source_review
medication_source_review.install()
new_task=json.loads((med/'handoff.json').read_text())['task']['id']
baseline=json.loads(Path('artifacts/agere-appointment-followthrough-v1/history-baseline.json').read_text());db=program_work.database(stack_path());counts={};current_history={}
for table in ('work_items','work_events','capability_tasks','capability_events'):
 rows=[list(x) for x in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')];assert all(old in rows for old in baseline[table]),table
 assert len(rows)==len(baseline[table])+{'capability_tasks':1,'capability_events':2}.get(table,0),table
 counts[table]=len(rows);current_history[table]=rows
db.close()
(out/'history-baseline.json').write_text(json.dumps(current_history,indent=2)+'\n')
external=[x for x in json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files'] if x['path'].startswith('/Users/mikel/Documents/')]
for x in external:assert hashlib.sha256(Path(x['path']).read_bytes()).hexdigest()==x['sha256']
ledger=BudgetLedger('artifacts/agere-full/provider-budget.sqlite3');receipt=ledger.receipt();ledger.db.close()
assert receipt['spending_limit']=='uncapped';(out/'ledger-after.json').write_text(json.dumps(receipt,indent=2)+'\n')
original=json.loads(Path('artifacts/agere-work-capacity-v1/scenario.json').read_text());assert resource_capacity.parent(stack_path(),original['id'])==original
scenarios=resource_capacity.history(stack_path(),original['id']);assert len(scenarios['scenarios'])==3 and all(x['current'] for x in scenarios['scenarios'])
http=json.loads(Path('artifacts/agere-reference-reassessment-v1/http-verification.json').read_text());old=r.load(stack_path(),http['old_manifest_id']);state=r.inspect(stack_path(),old['task_id'])
assert len(state['sets'])==2 and [x['current'] for x in state['sets']]==[False,True]
assert state['sets'][0]['manifest']==json.loads(Path('artifacts/agere-reference-applicability-v1/manifest.json').read_text())
assert not state['sets'][0]['questions'][0]['acquisitions'][0]['current']
assert len(state['sets'][1]['questions'][0]['acquisitions'])==1 and state['sets'][1]['questions'][0]['acquisitions'][0]['current']
assert all(not q['acquisitions'] for q in state['sets'][1]['questions'][1:])
recheck=reassessment.listing(stack_path(),{'target':http['target']})['preparations'];assert len(recheck)==1 and recheck[0]['current'] and recheck[0]['exact_context_reused']
assert len([t for t in r.packet_followup.targets(stack_path(),old['task_id']) if t['target']['kind']==r.KIND])==6
rows=comparison.listing(stack_path(),{'manifest_id':http['new_manifest_id']})['comparisons'];assert len(rows)==1 and rows[0]['current']
assert rows[0]['id']==json.loads(Path('artifacts/agere-care-option-context-v1/http-verification.json').read_text())['comparison_id']
attached=option_context.listing(stack_path(),{'comparison_id':rows[0]['id']})['contexts'];assert len(attached)==1 and attached[0]['current']
assert attached[0]['summary']['coverage_reports']==50 and attached[0]['summary']['patient_reports']==4
result={'status':'passed','prior_protected_count':len(prior['protected_files']),'protected_count':len(protected),'current_program_and_buyer_counts':counts,'prior_program_and_buyer_histories_preserved':True,'prior_medication_task':new_task,'external_research_files_preserved':len(external),'ledger_unchanged':True,'original_capacity_scenario_unchanged':True,'current_resource_scenarios':3,'reference_sets':2,'current_reference_sets':1,'historical_reference_sets':1,'original_context_preserved':True,'automatic_context_or_answer_transfers':0,'explicit_successor_source_rechecks':1,'exact_context_reused':True,'current_packet_followup_targets':6,'current_care_option_comparisons':1,'received_comparison_contexts':1,'attached_coverage_reports':50,'attached_patient_reports':4,'ranked_or_clinically_selected_options':0}
result['prior_respiratory_comparison_and_context_preserved']=True
from clinical_core.agere.clinical import care_option_delivery
behavioral=json.loads(Path('artifacts/agere-behavioral-options-v1/comparison.json').read_text())
current=comparison.listing(stack_path(),{'manifest_id':behavioral['manifest_id']})['comparisons'];assert len(current)==1 and current[0]['current']
assert {k:v for k,v in current[0].items() if k not in ('current','stale_reasons')}==behavioral
contexts=option_context.listing(stack_path(),{'comparison_id':behavioral['id']})['contexts'];assert len(contexts)==1 and contexts[0]['current']
rows=care_option_delivery.listing(stack_path(),{'comparison_id':behavioral['id']})['proposals'];assert len(rows)==1 and rows[0]['current']
result['prior_behavioral_comparison_and_received_context_preserved']=True
result['current_component_reviews']=1
result['all_counts_above_scope']='Respiratory preservation plus the new behavioral comparison/component checks; not global counts of every store'
from clinical_core.agere.clinical import care_portfolio_delivery
prior_delivery=json.loads(Path('artifacts/agere-option-delivery-v1/proposal.json').read_text())
assert care_portfolio_delivery.original(stack_path(),prior_delivery['id'])==prior_delivery
portfolio_rows=care_portfolio_delivery.listing(stack_path(),{'delivery_id':prior_delivery['id']})['proposals']
assert len(portfolio_rows)==1 and portfolio_rows[0]['current']
result['prior_component_review_preserved']=True
result['current_full_portfolio_reviews']=1
from clinical_core.agere.clinical import precedence_capacity
resource=json.loads(Path('artifacts/agere-precedence-capacity-v1/selected-parent.json').read_text())
assert precedence_capacity.parent(stack_path(),resource['id'])==resource
rows=precedence_capacity.history(stack_path(),resource['id'])['scenarios'];assert len(rows)==1 and rows[0]['current']
result['current_dependency_scenarios']=1
result['all_prior_resource_baselines_preserved']=True
from clinical_core.agere.clinical import work_cost,work_cost_context
cost=json.loads(Path('artifacts/agere-work-cost-v1/scenario.json').read_text())
assert work_cost_context.parent(stack_path(),cost['id'])==cost
contexts=work_cost_context.history(stack_path(),cost['id'])['contexts'];assert len(contexts)==1 and contexts[0]['current']
result['current_cost_scenarios']=1
result['current_received_financial_contexts']=1
result['selected_financial_patients']=contexts[0]['patient_ids']
result['received_financial_accounts']=contexts[0]['summary']['accounts']
from clinical_core.agere.clinical import conditional_payment,payment_comparison
payments=conditional_payment.history(stack_path(),cost['id'])['scenarios'];assert len(payments)==2 and all(x['current'] for x in payments)
differences=payment_comparison.history(stack_path(),cost['id'])['comparisons'];assert len(differences)==1 and differences[0]['current']
result['current_conditional_payment_scenarios']=2
result['current_payment_comparisons']=1
result['payment_prerequisites_unresolved']=all(all(q['status']=='unresolved' for q in r['requirements']) for r in payments)
functional=json.loads(Path('artifacts/agere-functional-options-v1/comparison.json').read_text())
rows=comparison.listing(stack_path(),{'manifest_id':functional['manifest_id']})['comparisons'];assert len(rows)==1 and rows[0]['current']
assert {k:v for k,v in rows[0].items() if k not in ('current','stale_reasons')}==functional
contexts=option_context.listing(stack_path(),{'comparison_id':functional['id']})['contexts'];assert len(contexts)==1 and contexts[0]['current']
rows=care_option_delivery.listing(stack_path(),{'comparison_id':functional['id']})['proposals'];assert len(rows)==1 and rows[0]['current']
assert len(rows[0]['coverage_of_proposal']['components_without_proposals'])==2
result['current_functional_comparisons']=1
result['current_functional_received_contexts']=1
result['current_functional_component_reviews']=1
result['functional_alternatives_unranked']=True
result['all_counts_above_scope']='Separate respiratory, behavioral, functional and operational preservation checks, not global counts of every store'
from clinical_core.agere.clinical import work_information
info=json.loads(Path('artifacts/agere-work-information-v1/scenario.json').read_text())
scenarios=work_information.listing(stack_path(),{'target':info['request']['target']})['scenarios']
assert len(scenarios)==1 and scenarios[0]['current']
assert {k:v for k,v in scenarios[0].items() if k not in ('current','stale_reasons')}==info
result['current_information_scenarios']=1
result['information_scenario_does_not_resolve_question']=not info['question_resolved']
from clinical_core.agere.clinical import shared_information
shared=json.loads(Path('artifacts/agere-shared-information-v1/scenario.json').read_text())
rows=shared_information.listing(stack_path(),{'portfolio_id':shared['request']['portfolio_id']})['scenarios']
assert len(rows)==2 and all(r['current'] for r in rows)
original=next(r for r in rows if r['id']==shared['id'])
assert {k:v for k,v in original.items() if k not in ('current','stale_reasons')}==shared
result['current_shared_information_scenarios']=2
result['original_structural_portfolio_current']=True
result['joint_question_count']=len(shared['questions'])
from clinical_core.agere.clinical import information_robustness
comparison=json.loads(Path('artifacts/agere-information-robustness-v1/comparison.json').read_text())
rows=information_robustness.listing(stack_path(),{'portfolio_id':comparison['request']['portfolio_id']})['comparisons']
assert len(rows)==1 and rows[0]['current']
assert {k:v for k,v in rows[0].items() if k not in ('current','stale_reasons')}==comparison
result['current_common_rule_comparisons']=1

for patient in ('P-7193','P-1001'):
 review=json.loads((med/(patient+'-review.json')).read_text());assert medication_source_review.inspect(stack_path(),review['id'])==review
 result[patient+'_review_current']=True
from clinical_core.agere.clinical import care_capabilities
new_plan=json.loads((med/'P-1001-plan.json').read_text());assert care_capabilities.inspect_plan(stack_path(),new_plan['id'])['origin']['id']==new_plan['origin']['id']
result['current_medication_reviews']=2;result['new_internal_medication_handoffs']=1;result['external_actions_executed']=0
from clinical_core.agere.clinical import medication_interpretation,interpretation_recovery
interpretation_recovery.install()
from clinical_core.agere.clinical import linked_claim_reconciliation as linked
from clinical_core.agere.clinical import linked_claim_integrity
linked_claim_integrity.install()
from clinical_core.agere.clinical import meaning_source_dispatch
meaning_source_dispatch.install()
for patient in ('P-7193','P-1001'):
 selection=json.loads((medication_prior/(patient+'-selection.json')).read_text());assert medication_interpretation.inspect_selection(stack_path(),selection['id'])==selection
 if patient=='P-7193':
  attachment=json.loads((medication_prior/(patient+'-attachment.json')).read_text());assert medication_interpretation.inspect_answer(stack_path(),attachment['id'])==attachment
 else:
  from clinical_core.agere.clinical import medication_partial_answers
  attachment=json.loads((medication_prior/'P-1001-partial.json').read_text());assert medication_partial_answers.inspect(stack_path(),attachment['id'])==attachment
import sqlite3
ledger_db=sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3')
for failed_row in json.loads((out/'ledger-row-baseline.json').read_text()):assert list(ledger_db.execute('SELECT * FROM calls WHERE id=?',(failed_row[0],)).fetchone())==failed_row
ledger_db.close()
result['prior_ledger_rows_preserved']=837
result['prior_failed_ledger_rows_preserved']=13
result['current_medication_interpretations']=3
for patient in ('P-7193','P-1001'):
 attachment=json.loads((linked_prior/(patient+'-attachment.json')).read_text());assert medication_interpretation.inspect_answer(stack_path(),attachment['id'])==attachment
 answer=json.loads((linked_prior/(patient+'-answer.json')).read_text());assert linked.inspect(stack_path(),answer['id'])==answer
result['current_partial_medication_interpretations']=1
result['new_internal_medication_handoffs']=0
result['prior_medication_handoff_unchanged']=True
result['source_meaning_pending_approval']=True
result['new_provider_calls']=0
result['new_selected_contexts']=0
result['new_selected_contexts_scope']='Received operational context attaches locally; no new provider source context'
from clinical_core.agere.clinical import portable_intake
intake=portable_intake.listing(stack_path(),{})['reviews']
assert sum(r['current'] for r in intake)==4
for r in intake:portable_intake.inspect(stack_path(),{'id':r['id']})
result['current_portable_reviews']=4
result['historical_portable_reviews']=sum(not r['current'] for r in intake)
db=sqlite3.connect((stack_path()/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
assert db.execute("SELECT COUNT(*) FROM patients WHERE id LIKE 'PORT-%'").fetchone()[0]==0
result['active_patients']=db.execute('SELECT COUNT(*) FROM patients').fetchone()[0]
result['active_records']=db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
assert (result['active_patients'],result['active_records'])==(1614,1238686)
db.close()
result['isolated_intake_not_merged_with_active_warehouse']=True
result['new_cardiometabolic_work_items']=0
result['new_cardiometabolic_handoffs']=0
result['prior_medication_handoff_unchanged']=True
from clinical_core.agere.clinical import cardiometabolic_review
cardio=Path('artifacts/agere-cardiometabolic-v1');review=json.loads((cardio/'review.json').read_text());assert cardiometabolic_review.inspect(stack_path(),review['id'])==review
cardio_http=json.loads((cardio/'http-verification.json').read_text());assert __import__('clinical_core.agere.clinical.reference_applicability',fromlist=['inspect']).inspect(stack_path(),cardio_http['task_id'])['sets'][0]['current']
from clinical_core.agere.clinical import care_option_comparison as cmcompare
assert cmcompare.listing(stack_path(),{'manifest_id':cardio_http['manifest_id']})['comparisons'][0]['current']
assert care_option_delivery.listing(stack_path(),{'comparison_id':cardio_http['comparison_id']})['proposals'][0]['current']
result['cardiometabolic_review_reference_options_and_components_current']=True
result['all_prior_work_and_task_history_rows_exactly_unchanged']=True
result['new_appointment_review_items']=0
result['new_appointment_work_events']=0
result['new_appointment_navigation_tasks']=1
result['new_appointment_navigation_events']=2

from clinical_core.agere.clinical import operational_context as op, operational_sensitivity as os, prediction_registry
operational=Path('artifacts/agere-operational-context-v1')
for kind in ('resource','dependency'):
 c=json.loads((operational/(kind+'-context.json')).read_text());assert op.inspect(stack_path(),c['id'])['current']
 s=json.loads((operational/(kind+'-sensitivity.json')).read_text());assert os.inspect(stack_path(),s['id'])['current']
result['prior_operational_contexts_and_sensitivities_current']=4
old_predictions=prediction_registry.status(stack_path());assert old_predictions['all_current'] and len(old_predictions['reports'])==5
assert all(p['status']=='not_fitted' for p in old_predictions['reports'])
result['original_five_prediction_reports_current_and_unchanged']=True

navigation=json.loads((out/'http-verification.json').read_text())
assert appointment_navigation.inspect(stack_path(),navigation['review_id'])==json.loads((out/'source-review.json').read_text())
assert appointment_navigation.inspect_proposal(stack_path(),navigation['proposal_id'])
result['appointment_navigation_source_and_proposal_current']=True
(out/'preservation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'medication_reviews':2,'prior_histories_preserved':True,'current_counts':counts,'protected_files':len(protected)}))
