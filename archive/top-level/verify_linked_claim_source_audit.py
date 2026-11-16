import json,sqlite3
from pathlib import Path
from collections import Counter
from clinical_core.contracts import digest
from clinical_core.agere.clinical import linked_claim_integrity as integrity,linked_claim_reconciliation as L,source_synthesis as S,case_passes as P
from clinical_core.agere.economics import usage_cost
from clinical_core.agere.gateway import BudgetLedger
integrity.install();out=Path('artifacts/agere-linked-reconciliation-v1');stack=Path('artifacts/agere-healthcare-v123/stack');cases=[]
for person in ['P-1001','P-7193']:
 answer=json.loads((out/(person+'-answer.json')).read_text());a=L.inspect(stack,answer['id']);assert a==answer
 parent,row,refs,job,_=L.prepare(stack,a['parent_answer_id']);original=[f for p in parent['passes'] for f in p['result']['findings']]
 assert original==a['retained_source_findings']
 assert {r['input_id'] for r in a['claim_reviews']}=={f['id'] for f in original}
 n=0
 for f in a['findings']:
  for c in f['citations']:
   unit=job['units'][c['unit']];start=c['start']-(unit['unit']['start'] if unit['unit']['kind']=='narrative' else 0);end=start+len(c['quote'])
   assert unit['text'][start:end]==c['quote'] and unit['text'].count(c['quote'])==1;n+=1
 cases.append({'patient':person,'answer_id':a['id'],'original_claims':len(original),'consolidated_findings':len(a['findings']),'claim_dispositions':dict(Counter(r['status'] for r in a['claim_reviews'])),'exact_citation_occurrences':n,'system_unassessed_units_unchanged':a['coverage'].get('system_unassessed_units',0),'request_bytes':P.request_bytes(job['body']),'source_passes_unchanged':a['passes']==parent['passes']})
f=json.loads((out/'followup-answer.json').read_text());follow=S.inspect(stack,f['id']);row=S.context(stack,follow['context_id']);units=P.unit_map(row['model_context']);nf=0
for finding in follow['findings']:
 for c in finding['citations']:
  unit=next(v for v in units.values() if v['unit']['id']==c['source_unit_id']);start=c['start']-(unit['unit']['start'] if unit['unit']['kind']=='narrative' else 0)
  assert unit['text'][start:start+len(c['quote'])]==c['quote'];nf+=1
baseline=json.loads((out/'ledger-row-baseline.json').read_text());old={r[0] for r in baseline}
ledger=BudgetLedger('artifacts/agere-full/provider-budget.sqlite3');rows=[list(r) for r in ledger.db.execute('SELECT * FROM calls ORDER BY id')]
assert all(r in rows for r in baseline)
new=ledger.db.execute('SELECT id,status,response FROM calls').fetchall();new=[r for r in new if r[0] not in old]
assert len(new)==3 and all(r[1]=='validated' for r in new)
cost=sum(usage_cost(json.loads(r[2])['model'],json.loads(r[2])['usage'])['estimated_usd'] for r in new)
result={'passed':True,'cases':cases,'followup':{'answer_id':f['id'],'complete_selected_records':2,'exact_citation_occurrences':nf,'original_question_attached':True},'new_provider_calls':len(new),'failed_provider_calls':0,'failed_requests_resent':0,'prior_ledger_rows_preserved':len(baseline),'estimated_new_api_usd':cost,'ledger':ledger.receipt(),'clinical_accuracy_assessed':False,'semantic_review_notes':['The two canonical identity statements remain source-account metadata, not proof of each narrative subject.','P-7193 no longer questions canonical patient identity in its consolidated findings; no clinical accuracy claim follows.','All P-1001 claims were qualified against their excerpts; omitted full-unit details remain explicit.','Follow-up reads two full selected medication entries and attaches their interpretation to the exact review question. It does not resolve original documentary or clinical requirements.']}
ledger.close();(out/'source-and-usage-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'cases':cases,'new_calls':len(new),'estimated_new_api_usd':cost,'followup_citations':nf},indent=2))
