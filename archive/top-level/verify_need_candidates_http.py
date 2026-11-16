"""Exercise bounded discovery and explicit selection across three source domains."""
import json
from pathlib import Path
from verify_stable_references_http import api

OUT=Path('artifacts/agere-need-candidates-v1')


if __name__=='__main__':
 results=[]
 for name in ['medication','financial','outside-care']:
  needs=json.loads((OUT/(name+'-needs.json')).read_text());need=needs['needs'][0]
  request={'receipt_id':needs['id'],'need_id':need['id'],'query':need['question']}
  candidates=api('/api/stack/evidence-needs/candidates',request)
  (OUT/(name+'-candidates.json')).write_text(json.dumps(candidates,indent=2))
  assert candidates['summary']['ranked_passages']<=8 and candidates['new_paid_api_calls']==0
  replay=api('/api/stack/evidence-needs/candidates',request);assert replay['cache_hit'] and replay['id']==candidates['id']
  assert not candidates['need_binding_changed'] and candidates['semantic_resolution']=='pending'
  assert api('/api/stack/evidence-needs/candidates?id='+candidates['id'])['result_digest']==candidates['result_digest']
  before=api('/api/stack/evidence-needs',{'answer_id':needs['answer_id'],'selectors':[]})
  try:api('/api/stack/evidence-needs/select-candidates',{'candidate_id':candidates['id'],'record_ids':['not-a-candidate']})
  except RuntimeError as exc:assert str(exc).startswith('400 ')
  else:raise AssertionError('Unknown candidate accepted')
  chosen=candidates['candidates'][0]['record_id']
  selected=api('/api/stack/evidence-needs/select-candidates',{'candidate_id':candidates['id'],'record_ids':[chosen]})
  (OUT/(name+'-selection.json')).write_text(json.dumps(selected,indent=2))
  assert api('/api/stack/evidence-needs/candidate-context?id='+selected['id'])==selected
  assert api('/api/stack/evidence-needs/select-candidates',{'candidate_id':candidates['id'],'record_ids':[chosen]})==selected
  after=api('/api/stack/evidence-needs',{'answer_id':needs['answer_id'],'selectors':[]});assert before==after
  context=api('/api/stack/context?id='+selected['context_id']);assert context['record_scope']==selected['record_scope']
  plan=api('/api/stack/context/plan',{'context_id':selected['context_id']}) if selected['dispatch']['source_review_allowed'] else None
  (OUT/(name+'-selected-plan.json')).write_text(json.dumps(plan,indent=2))
  summary={'domain':name,'candidate_id':candidates['id'],'selection_id':selected['id'],'summary':candidates['summary'],
   'selected_root':chosen,'connected_records':len(selected['selection']['connected_record_ids']),
   'context_id':selected['context_id'],'dispatch_allowed':selected['dispatch']['source_review_allowed'],
   'source_passes':plan['source_passes'] if plan else None,'replay_uses_local_cache':True,'original_needs_unchanged':True,
   'invalid_selection_rejected':True,'new_paid_api_calls_for_discovery':0}
  results.append(summary);print(json.dumps(summary),flush=True)
 (OUT/'http-verification.json').write_text(json.dumps({'actual_http_verified':True,'domains':results,'clinical_accuracy_validated':False,'retrieval_recall_assessed':False},indent=2))
