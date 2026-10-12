"""Bounded real interpretation of the explicit synthetic diagnostic scope."""
import json
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import source_synthesis,reference_scope_work,structured_transport,literal_reference_runtime,patient_fit_work,patient_context,answer_retirement
OUT=Path('artifacts/agere-structured-transport-v1')
def main():
 answer_retirement.install_origin_guard();patient_context.install_plan_guard();patient_fit_work.install();literal_reference_runtime.install();structured_transport.install()
 selected=json.loads((OUT/'selection.json').read_text());current=reference_scope_work.inspect_selection(stack_path(),selected['id']);assert current==selected
 assert set(current['selected_record_ids'])=={'WHL-R-2076245da12171489d7129','WHL-R-c50ceb6a28c9dd70b07da4','WHL-R-e0b62683927631842c90aa'}
 plan=source_synthesis.preview(stack_path(),selected['context_id']);assert plan['source_passes']==2 and max(plan['request_bytes'])<=20000
 ledger=BudgetLedger('artifacts/agere-full/provider-budget.sqlite3')
 try:
  answer=source_synthesis.synthesize(stack_path(),selected['context_id'],ledger,allow_live=True)
  attachment=reference_scope_work.attach(stack_path(),{'selection_id':selected['id'],'answer_id':answer['id']})
  result={'status':'complete','context_id':selected['context_id'],'answer_id':answer['id'],'attachment':attachment,'provider':'claude','new_inference_calls':answer['new_paid_api_calls'],'new_token_count_calls':answer['new_token_count_calls'],'clinical_validation':False,'human_review_recorded':False}
  (OUT/'live-answer.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
 finally:ledger.close()
if __name__=='__main__':main()
