"""Live bounded synthetic API integration; no hidden truth or model training."""
import json
from pathlib import Path
from run_integrated_stack import keys
from clinical_core.agere.clinical import context_builder as context, case_synthesis as synthesis
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import source_synthesis as router

ROOT=Path(__file__).resolve().parent

def main():
 config=json.loads((ROOT/'config/agere/active_warehouse.json').read_text());path=Path(config['build'])/'stack'
 out=ROOT/'artifacts/agere-case-synthesis-v1';out.mkdir(exist_ok=True)
 parameters={'question':'What do the received pharmacy renewal requests and prescriber responses document, and what remains unknown about whether the medication regimen actually changed?',
  'streams':['pharmacy_renewal_requests','pharmacy_prescriber_responses'],'concepts':[],'contract':'','program_ids':[],'max_bytes':32000}
 prepared=context.execute(path,['P-1001'],parameters);context_id=prepared['patients'][0]['id']
 row=synthesis.verified_context(path,context_id);body,units=synthesis.request(row)
 print(json.dumps({'context_id':context_id,'source_units':len(units),'request_bytes':len(json.dumps(body).encode())}),flush=True)
 keys();ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
 before=ledger.receipt()
 try:
  answer=router.synthesize(path,context_id,ledger,allow_live=True)
  replay=router.synthesize(path,context_id,ledger,allow_live=False)
  assert answer['id']==replay['id'] and replay['new_paid_api_calls']==0 and replay['cache_hit']
  assert not answer['clinical_state_established']
  citations=[c for f in answer['findings'] for c in f['citations']]
  assert citations and all(c['citation_verified'] for c in citations)
  assert all(s['status']=='proposed' and not s['execution_authorized'] for s in answer['review_steps'])
  saved=synthesis.inspect(path,answer['id']);assert saved['question']==parameters['question']
  report={'synthetic':True,'snapshot':config['snapshot'],'context_id':context_id,'answer_id':answer['id'],
   'findings':len(answer['findings']),'exact_citations':len(citations),'review_proposals':len(answer['review_steps']),
   'unanswered':answer['unanswered'],'token_check':answer['token_check'],'usage':answer['usage'],
   'new_paid_api_calls':answer['new_paid_api_calls'],'new_token_count_calls':answer['new_token_count_calls'],
   'offline_replay':True,'clinical_accuracy_assessed':False,'ledger_before':before,'ledger_after':ledger.receipt()}
  (out/'answer.json').write_text(json.dumps(answer,indent=2));(out/'live-verification.json').write_text(json.dumps(report,indent=2))
  print(json.dumps(report),flush=True)
 finally:ledger.close()
if __name__=='__main__':main()
