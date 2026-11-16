"""Actual saved questions -> exact units -> selected context, without model calls."""
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.gateway import BudgetLedger
from verify_domain_programs_http import snapshot

OUT=Path('artifacts/agere-need-references-v1')
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
def api(route,body=None):
 request=Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urlopen(request,timeout=180) as response:return json.load(response)
def main():
 ledger=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt();save('ledger-before',ledger)
 work=snapshot();save('functional-before',work);results=[]
 cases=[('stable','3c26f4b1afff1ff8048117a7c55bed945dcd40306ec1324c1240af2dd3cc8c36','c133bb401ec7c1c266f3c1fed229b705f620c88502cbe5aa937639abadfea4a9'),
  ('ambiguous','d7d96be6f304b5649e24c329e38648d1ef6475c579c1645498dcd7fd177c8799','55ba8b9e98d395a737654662a6a177e2b3703378481c5ba2b2aefe2e6667bd6b'),
  ('abbreviated','65b1b952c8c7de05bd8bf4a9cd2630ccc3262a2404450d1cfcecb78832b8046d','dd54e81571e7d047c65d16b269c45e55e5344fb30ad31d97a686074f246ec4da')]
 for name,answer_id,need_id in cases:
  answer=api('/api/stack/case-synthesis?id='+answer_id)
  receipt=api('/api/stack/evidence-needs',{'answer_id':answer_id,'selectors':[]});save(name+'-needs',receipt)
  request={'receipt_id':receipt['id'],'need_id':need_id}
  located=api('/api/stack/evidence-needs/references',request);save(name+'-references',located)
  assert api('/api/stack/evidence-needs/references',request)==located
  assert not located['question_resolved'] and not located['question_premise_endorsed'] and located['new_paid_api_calls']==0
  row={'case':name,'answer_id':answer_id,'needs_id':receipt['id'],'need_id':need_id,'reference_id':located['id'],'located':len(located['references']),'unresolved_mentions':len(located['unresolved_mentions'])}
  if name=='stable':
   wanted=['p1:u6','p2:u3'];units={x['name']:x for x in located['references']}
   assert all(n in units and units[n]['source_unit']['kind']=='narrative' for n in wanted)
   assert units['p1:u6']['record_id']=='WHM-R-f07f0c5291aacbe0494b18'
   assert units['p2:u3']['record_id']=='WHL-R-0201ad1bc2c0b50363c9ac'
   selection_request={'reference_id':located['id'],'reference_names':wanted}
   selected=api('/api/stack/evidence-needs/reference-context',selection_request);save(name+'-selection',selected)
   assert api('/api/stack/evidence-needs/reference-context',selection_request)==selected
   context=api('/api/stack/context?id='+selected['context_id']);save(name+'-context',context)
   assert context['parameters']['question']==located['original_question'] and not selected['meaning_resolved']
   assert set(units[n]['record_id'] for n in wanted)<=set(context['record_scope']['record_ids'])
   assert context['dispatch']['source_review_allowed'] and not context['omissions']
   row.update(context_id=context['id'],selection_id=selected['id'],selected_record_count=len(context['record_scope']['record_ids']),selected_references=wanted)
   for bad in ([],['p99:u1'],['p1:u6','p1:u6']):
    try:api('/api/stack/evidence-needs/reference-context',{'reference_id':located['id'],'reference_names':bad});raise AssertionError('Invalid selection accepted')
    except HTTPError as error:assert error.code==400
   try:api('/api/stack/evidence-needs/answer',{**request,'selection_kind':'exact_references','selection_id':selected['id'],'answer_id':answer_id});raise AssertionError('Wrong-context answer accepted')
   except HTTPError as error:assert error.code==400
   answers=api('/api/stack/evidence-needs/answers?receipt_id='+receipt['id']+'&need_id='+need_id);assert not answers['requirement_resolved'];save('existing-followup-list',answers)
  else:
   assert not located['references'] and located['unresolved_mentions']
  assert api('/api/stack/case-synthesis?id='+answer_id)==answer
  results.append(row);print(json.dumps(row),flush=True)
 assert BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt()==ledger
 assert snapshot()==work
 save('http-verification',results);print('Exact references, bounded context, ambiguity handling, wrong-context rejection and unchanged original answers/work/ledger passed.',flush=True)
if __name__=='__main__':main()
