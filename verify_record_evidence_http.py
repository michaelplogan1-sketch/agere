"""Loopback integration, one authorized record-scope run and immutable replay."""
import json
from pathlib import Path
import urllib.request
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import program_work
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-record-evidence-v1';STACK=ROOT/'artifacts/agere-healthcare-v123/stack'
def request(route,body=None):
 data=json.dumps(body).encode() if body is not None else None
 with urllib.request.urlopen(urllib.request.Request('http://localhost:8885'+route,data=data,headers={'Content-Type':'application/json'}),timeout=240) as r:return json.loads(r.read())
def main():
 c=json.loads((OUT/'followup-context.json').read_text());plan=request('/api/stack/context/plan',{'context_id':c['context_id']})
 assert plan['can_dispatch'] and (plan['saved_answer_id'] or plan['remaining_reservation_usd'] is None or plan['reservation_ceiling_usd']<=plan['remaining_reservation_usd'])
 print('Dispatching explicit scope:',plan['source_units'],'units; ceiling',plan['maximum_inference_calls'],'calls',flush=True)
 answer=request('/api/stack/case-synthesis?id='+plan['saved_answer_id']) if plan['saved_answer_id'] else request('/api/stack/context/synthesize',{'context_id':c['context_id']})
 (OUT/'answer.json').write_text(json.dumps(answer,indent=2)+'\n')
 assert answer['coverage']['complete_declared_scope'] and not answer['coverage']['complete_categories']
 row=request('/api/stack/context?id='+c['context_id']);sources=set(row['model_context']['sources'])
 assert sources==set(answer['record_scope']['record_ids'])
 for f in answer['findings']:
  for cite in f['citations']:
   assert cite['source']['record_id'] in sources and cite['citation_verified']
 replay=request('/api/stack/context/synthesize',{'context_id':c['context_id']});assert replay['cache_hit'] and replay['new_paid_api_calls']==0
 before=request('/api/stack/work?program=hf_review&offset=0')['summary']
 proposal=request('/api/stack/work/prepare',{'program':'hf_review','patient':'P-1001','synthesis_ids':[answer['id']]})
 after=request('/api/stack/work?program=hf_review&offset=0')['summary'];assert before==after
 db=program_work.database(STACK)
 try:assessment=program_work.load(db,'work_assessments',proposal['patients'][0]['assessment_id'])
 finally:db.close()
 assert assessment['synthesis_context']['answers'][0]['evidence_needs_id']
 old=json.loads((ROOT/'artifacts/agere-multipass-synthesis-v1/answer.json').read_text())
 assert request('/api/stack/case-synthesis?id='+old['id'])['findings']==old['findings']
 needs=request('/api/stack/evidence-needs',{'answer_id':old['id'],'selectors':[]})
 assert needs['summary']['questions_with_exact_located_records']==3
 ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
 try:budget=ledger.receipt()
 finally:ledger.close()
 report={'synthetic':True,'answer_id':answer['id'],'context_id':c['context_id'],'selected_records':len(sources),'source_units':answer['coverage']['submitted_units'],
  'source_passes':len(answer['passes']),'reconciliation_groups':answer['coverage']['reconciliation_groups'],
  'findings':len(answer['findings']),'exact_citations':sum(len(f['citations']) for f in answer['findings']),
  'new_paid_api_calls':answer['new_paid_api_calls'],'new_token_count_calls':answer['new_token_count_calls'],
  'estimated_api_usd':round(sum(u['cost_estimate']['estimated_usd'] for u in answer['usage']),6),
  'replay_no_calls':True,'legacy_answer_preserved':True,'evidence_needs_attached_to_assessment':True,
  'work_counts_unchanged':True,'clinical_accuracy_assessed':False,'remaining_reservation_usd':None if budget['remaining_reservation_usd'] is None else round(budget['remaining_reservation_usd'],2)}
 for name,value in [('http-verification',report),('work-proposal',proposal),('assessment',assessment),('ledger-after',budget)]:
  (OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
 print(json.dumps(report),flush=True)
if __name__=='__main__':main()
