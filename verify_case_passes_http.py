"""Actual loopback routes, cached inference and immutable assessment attachment."""
import json
from pathlib import Path
from urllib.error import HTTPError
from verify_case_synthesis_http import request

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/agere-multipass-synthesis-v1'

def main():
 answer=json.loads((OUT/'answer.json').read_text());key=answer['id'];context=answer['context_id']
 replay=request('/api/stack/context/synthesize',{'context_id':context});assert replay['cache_hit'] and replay['new_paid_api_calls']==0
 saved=request('/api/stack/case-synthesis?id='+key);assert saved['findings']==answer['findings']
 old=json.loads((ROOT/'artifacts/agere-case-synthesis-v1/answer.json').read_text())
 legacy=request('/api/stack/case-synthesis?id='+old['id']);assert legacy['id']==old['id']
 scope=request('/api/stack/context/from-question',{'patient':'P-1001','question':'What do the pharmacy renewal requests and prescriber responses document about expired prescriptions, reassessment and replacement orders?'})
 assert scope['status']=='ready_for_source_review' and scope['scope_sufficiency']=='unassessed'
 assert scope['selected_categories'] and scope['unselected_categories'] and scope['new_paid_api_calls']==0
 malformed=[]
 for route,body in [('/api/stack/context/from-question',{'patient':'P-999999','question':'What is documented?'}),('/api/stack/context/plan',{'context_id':'wrong'}),('/api/stack/context/synthesize',{'context_id':context,'maximum_passes':999})]:
  try:request(route,body)
  except HTTPError as e:assert e.code==400;malformed.append(e.code)
  else:raise AssertionError('Invalid input accepted')
 before=request('/api/stack/work?program=hf_review&offset=0')['summary']
 proposal=request('/api/stack/work/prepare',{'program':'hf_review','patient':'P-1001','synthesis_ids':[key]})
 after=request('/api/stack/work?program=hf_review&offset=0')['summary']
 assert before==after and proposal['synthesis_assessments']==1
 (OUT/'question-source-plan.json').write_text(json.dumps(scope,indent=2));(OUT/'work-proposal.json').write_text(json.dumps(proposal,indent=2))
 report={'synthetic':True,'answer_id':key,'post_replay':True,'saved_answer_inspected':True,'legacy_answer_preserved':True,
  'automatic_source_categories':scope['selected_categories'],'retrieved_records':len(scope['retrieved_record_ids']),
  'connected_records':len(scope['connected_record_ids']),'selected_category_records':scope['records_in_selected_categories'],
  'unselected_categories':len(scope['unselected_categories']),'source_units':scope['context_budget']['candidate_units'],
  'invalid_requests_rejected':malformed,'synthesis_assessments':proposal['synthesis_assessments'],
  'candidates':proposal['counts']['candidates'],'work_counts_unchanged':True,'new_paid_api_calls':0}
 (OUT/'http-verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
if __name__=='__main__':main()
