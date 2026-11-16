"""Verify the population review enters the current answer and buyer-work path."""
import json
from pathlib import Path
from urllib.request import Request,urlopen
OUT=Path('artifacts/agere-functional-review-v1')
def api(route,body=None):
 req=Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urlopen(req,timeout=180) as response:return json.load(response)
def write(name,r):(OUT/(name+'.json')).write_text(json.dumps(r,indent=2)+'\n')
pop=api('/api/stack/functional-review');write('http-population',pop)
assert pop['counts']=={'population':1614,'patients_with_selected_sources':308,'no_selected_sources_received':1306,'source_review_candidates':256,'records':855,'signals':{'reported_fall_history':102,'reported_support_need':199,'tug_threshold_review':163}}
nextpage=api('/api/stack/functional-review?id='+pop['id']+'&offset=12')
assert not {p['patient_id'] for p in pop['patients']} & {p['patient_id'] for p in nextpage['patients']}
patient='SYN-69283717-971';detail=api('/api/stack/functional-review?id='+pop['id']+'&patient='+patient);write('http-patient',detail)
assert detail['patients'][0]['latest_source_accounts']['functional_mobility_exams']['fields']['homebound_assessed']['value'] is False
context=api('/api/stack/functional-review/context',{'review_id':pop['id'],'patient_id':patient});write('http-context',context)
prior=json.loads(Path('artifacts/agere-availability-synthesis-v1/answer.json').read_text())
assert context['context']['id']==prior['context_id']
plan=api('/api/stack/context/plan',{'context_id':context['context']['id']});assert plan['saved_answer_id']==prior['id']
answer=api('/api/stack/context/synthesize',{'context_id':context['context']['id']});assert answer['cache_hit'] and answer['new_paid_api_calls']==0
needs=api('/api/stack/evidence-needs',{'answer_id':answer['id'],'selectors':[]});assert needs['id']==json.loads(Path('artifacts/agere-availability-synthesis-v1/evidence-needs.json').read_text())['id']
task_id='85228500e2b2d835114e7eb80c762b0c7c129763a794a68e659b4aa4ba6a3c7c'
work=api('/api/stack/capabilities/availability?task_id='+task_id);assert work['answers'][0]['answer_id']==answer['id']
result={'population_receipt':pop['id'],'counts':pop['counts'],'patient_id':patient,'context_id':context['context']['id'],'saved_answer_id':answer['id'],'evidence_needs_id':needs['id'],'existing_buyer_task_id':task_id,'pagination_verified':True,'literal_false_preserved':True,'population_to_context_to_answer_to_needs_and_existing_work':True,'new_paid_api_calls':0,'clinical_quality_validated':False,'home_health_eligibility':'unresolved'}
write('http-verification',result);print(json.dumps(result,indent=2))
