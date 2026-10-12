"""Real received sources and exact saved answers, no provider calls in preparation."""
import json
from pathlib import Path
from clinical_core.agere.clinical import source_synthesis, evidence_needs, record_context, question_context
from clinical_core.agere.clinical.warehouse_retrieval import Retriever
from clinical_core.agere.gateway import BudgetLedger
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/agere-record-evidence-v1'
PATH=ROOT/'artifacts/agere-healthcare-v123/stack'

def main():
 old=json.loads((ROOT/'artifacts/agere-multipass-synthesis-v1/answer.json').read_text())
 assert source_synthesis.inspect(PATH,old['id'])['findings']==old['findings']
 retriever=Retriever(PATH)
 try:scope=question_context.prepare(PATH,'P-1001','What do the pharmacy renewal requests and prescriber responses document about expired prescriptions, reassessment and replacement orders?',retriever)
 finally:retriever.close()
 row=record_context.verified(PATH,scope['context_id'])
 assert set(row['model_context']['sources'])==set(scope['connected_record_ids'])
 assert row['record_scope']['excluded_from_selected_categories']>0
 needs=evidence_needs.prepare(PATH,old['id'])
 bound=next(n for n in needs['needs'] if n['selectors'])
 connected=evidence_needs.context_for_need(PATH,needs['id'],bound['id'])
 plan=source_synthesis.preview(PATH,connected['context_id'])
 ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
 try:budget=ledger.receipt()
 finally:ledger.close()
 for name,value in [('question-scope',scope),('context',row),('evidence-needs',needs),('followup-context',connected),('followup-plan',plan),('ledger-before',budget)]:
  (OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
 print(json.dumps({'selected_records':scope['selected_record_count'],'category_records':scope['records_in_selected_categories'],
  'units':row['budget']['selected_units'],'context_bytes':row['budget']['used'],'exact_bound_questions':needs['summary']['questions_with_exact_located_records'],
  'followup_records':len(connected['record_scope']['record_ids']),'followup_units':plan['source_units'],'call_ceiling':plan['maximum_inference_calls'],
  'reservation_ceiling':plan['reservation_ceiling_usd'],'remaining_reservation':budget['remaining_reservation_usd']}))
if __name__=='__main__':main()
