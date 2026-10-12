"""Synthetic multi-source live exercise with exact replay and per-call receipts."""
import json
from pathlib import Path
from run_integrated_stack import keys
from clinical_core.agere.clinical import case_passes as cp, case_synthesis as single
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import source_synthesis as router

ROOT=Path(__file__).resolve().parent

def main():
 config=json.loads((ROOT/'config/agere/active_warehouse.json').read_text());path=Path(config['build'])/'stack'
 out=ROOT/'artifacts/agere-multipass-synthesis-v1';context_id=(out/'context-id.txt').read_text().strip()
 row=single.verified_context(path,context_id)
 plan=cp.preview(path,context_id);(out/'transport-plan.json').write_text(json.dumps(plan,indent=2))
 print(json.dumps({k:plan[k] for k in ('source_passes','source_units','request_bytes','reservation_ceiling_usd')}),flush=True)
 keys();ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3');before=ledger.receipt()
 (out/'ledger-before.json').write_text(json.dumps(before,indent=2))
 try:
  answer=router.synthesize(path,context_id,ledger,allow_live=True)
  replay=router.synthesize(path,context_id,ledger,allow_live=False)
  assert answer['id']==replay['id'] and replay['cache_hit'] and replay['new_paid_api_calls']==0
  assert answer['coverage']['submitted_units']==row['budget']['candidate_units']
  assert all(p['request_bytes']<=20000 for p in answer['passes']) and answer['reconciliation']['request_bytes']<=20000
  citations=[c for f in answer['findings'] for c in f['citations']]
  assert citations and all(c['citation_verified'] for c in citations)
  report={'synthetic':True,'context_id':context_id,'answer_id':answer['id'],'source_units':answer['coverage']['submitted_units'],
   'source_passes':len(answer['passes']),'request_bytes':[p['request_bytes'] for p in answer['passes']]+[r['request_bytes'] for r in answer['reconciliation']['groups']],
   'findings':len(answer['findings']),'reconciliation_findings':len(answer['reconciliation']['result']['findings']),
   'exact_citations':len(citations),'cross_pass_dependencies':len(answer['dependencies']['cross_pass_edges']),
   'unit_dispositions':answer['coverage']['unit_dispositions'],'new_paid_api_calls':answer['new_paid_api_calls'],
   'new_token_count_calls':answer['new_token_count_calls'],'offline_replay':True,'clinical_accuracy_assessed':False,
   'estimated_api_usd':sum(u.get('cost_estimate',{}).get('estimated_usd',0) for u in answer['usage']),
   'ledger_before':before,'ledger_after':ledger.receipt()}
  (out/'answer.json').write_text(json.dumps(answer,indent=2));(out/'live-verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
 finally:
  (out/'ledger-after.json').write_text(json.dumps(ledger.receipt(),indent=2));ledger.close()
if __name__=='__main__':main()
