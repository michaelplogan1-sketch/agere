"""Actual local source review -> review work -> buyer task -> dated questions/options."""
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
OUT=Path('artifacts/agere-cardiometabolic-v1');ACTOR='Agere automated synthetic build';BUYER='synthetic-cardiometabolic-review';PID='P-1007'
def api(route,body=None):
 with urlopen(Request('http://localhost:8885/api/stack/'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=180) as r:return json.load(r)
def save(name,v):(OUT/(name+'.json')).write_text(json.dumps(v,indent=2)+'\n')
def reject(route,body):
 try:api(route,body)
 except HTTPError as e:assert e.code in (400,409);return
 raise AssertionError('Unsupported request accepted')
def main():
 review=api('cardiometabolic/prepare',{'patient_id':PID});assert api('cardiometabolic?id='+review['id'])==review;save('review',review)
 proposal=api('cardiometabolic/work',{'review_id':review['id']});save('work-proposal',proposal)
 saved=api('work/save',{'proposal_id':proposal['proposal_id'],'actor':ACTOR});save('work-save',saved)
 assert api('work/save',{'proposal_id':proposal['proposal_id'],'actor':ACTOR})['changes']['unchanged']==1
 work=api('work?program=cardiometabolic_source_review')['items'][0];inspected=api('work?item='+work['id']);save('work',inspected)
 assert work['patient_id']==PID and work['state']=='review_needed' and work['owner'] is None
 assert len(inspected['assessment']['evidence'])==len(review['review']['sources'])==61
 context=api('cardiometabolic/context',{'review_id':review['id'],'record_ids':review['review']['suggested_record_ids']});save('context',context)
 assert context['context']['patient_id']==PID and context['context']['parameters']['question']==review['question']
 from clinical_core.agere.clinical import care_capabilities as care
 from clinical_core.agere.active import stack_path
 portfolio=care.portfolio(stack_path(),BUYER)
 if portfolio['revision']==0:
  cap={'id':'cardiometabolic-evidence-review','name':'Synthetic cardiometabolic evidence review','description':'Internal synthetic evidence-question review only. This declaration does not supply devices, licensed clinical care, titration or an ACCESS enrollment service.','need_tags':['cardiometabolic','hypertension','diabetes','medication_review'],'modality':'virtual','regions':[],'payer_types':[],'plan_ids':[],'pathway_ids':[],'owner':{'kind':'agent','id':'synthetic-local-evidence-review'},'available':True,'capacity_slots':None,'capacity_as_of':'2029-08-15','capacity_until':'2029-08-15','effective_start':'2029-01-01','effective_end':'2029-12-31','prerequisites':['Clinical assessment, measurement context, exact benefits, team scope and patient agreement remain unresolved'],'provenance':'Engineering-only synthetic internal evidence-review capability; not a real buyer service or care-delivery claim.'}
  portfolio=api('capabilities/save',{'buyer_id':BUYER,'expected_revision':0,'actor':ACTOR,'capabilities':[cap]})
 save('portfolio',portfolio)
 request={'buyer_id':BUYER,'origin':{'kind':'assessment','id':proposal['patients'][0]['assessment_id'],'finding_ids':[]},'need_tags':['medication_review'],'rationale':'Review received cardiometabolic measurements and missing context. No diagnosis, treatment need, titration or entitlement is established.','coverage':{'payer_type':'unknown','plan_id':'','region':'','as_of':'2029-08-15','provenance':'Exact plan selection and benefits remain unresolved; received context will be attached separately.','preferences':'Goals and service-specific consent unresolved.'}}
 plan=api('capabilities/prepare',request);save('plan',plan);option=plan['options'][0]
 task=api('capabilities/queue',{'plan_id':plan['id'],'option_id':option['id'],'actor':ACTOR})['task']
 if task['state']=='queued_for_internal_review':task=api('capabilities/event',{'task_id':task['id'],'expected_revision':task['revision'],'request_id':'cardiometabolic-source-handoff-v1','action':'prepare_handoff','actor':ACTOR,'note':'Internal synthetic evidence handoff. No human clinical review, device delivery, patient contact or treatment action.'})['task']
 save('task',task);assert task['state']=='handoff_prepared_for_review'
 state=api('reference-applicability/inspect',{'task_id':task['id']});ref=next(r for r in state['references'] if r['citation']['doi']=='10.1161/CIR.0000000000001356');target=next(t for t in state['original_targets'] if 'clinical' in t['question'].lower() or 'need' in t['question'].lower())
 req={'task_id':task['id'],'expected_revision':task['revision'],'target':target['target'],'reference_id':ref['id'],'reason':'Connect the exact internal service question to dated AHA summary process guidance. Seven clinical, patient, coverage and currency questions remain unresolved; CMS notice is public background only.'}
 manifest=api('reference-applicability/prepare',req);save('manifest',manifest);assert len(manifest['questions'])==7
 q=next(q for q in manifest['questions'] if q['key']=='assessment');selection={'target':{'kind':'reference_applicability_question','manifest_id':manifest['id'],'question_id':q['id']},'record_ids':review['review']['suggested_record_ids']}
 acquired=api('readiness/context',selection);save('acquisition',acquired);assert not api('readiness/inspect',{'target':selection['target']})['documentary_question_addressed']
 req={'manifest_id':manifest['id'],'question':'What measurement review approach could the buyer support, and what remains unresolved before clinical care?','rationale':'Compare an explicit review baseline with a conditional home measurement and team follow-up proposal. No clinical superiority, individual effect or service fulfillment is inferred.','service_bindings':[]}
 preview=api('care-options/preview',req);comparison=api('care-options/prepare',{'comparison':req,'preview_id':preview['id']});save('comparison',comparison)
 assert comparison['reported_comparative_estimates']==[] and all(a['rank'] is None for a in comparison['alternatives'])
 context=api('care-options/context-preview',{'comparison_id':comparison['id']});context=api('care-options/context-prepare',{'comparison_id':comparison['id'],'preview_id':context['id']});save('received-context',context)
 delivery_req={'comparison_id':comparison['id'],'alternative_key':'home_team_review','component_bindings':[],'coordination_note':'All three responsibilities require actual qualified delivery arrangements. This synthetic buyer declares only internal evidence review; no care component is assigned.'}
 preview=api('care-options/delivery-preview',delivery_req);delivery=api('care-options/delivery-prepare',{'proposal':delivery_req,'preview_id':preview['id']});save('delivery',delivery)
 assert delivery['coverage_of_proposal']['components_without_proposals']==['clinical_review','measurement_support','followup']
 assert api('care-options/list',{'manifest_id':manifest['id']})['comparisons'][0]['current']
 assert api('care-options/context-list',{'comparison_id':comparison['id']})['contexts'][0]['current']
 assert api('care-options/delivery-list',{'comparison_id':comparison['id']})['proposals'][0]['current']
 reject('cardiometabolic/context',{'review_id':review['id'],'record_ids':['wrong-patient-record']});reject('cardiometabolic/prepare',{'patient_id':PID,'diagnosis':'hypertension'})
 result={'status':'passed','patient_id':PID,'review_id':review['id'],'source_records':61,'bp':review['review']['bp']['counts'],'work_item_id':work['id'],'task_id':task['id'],'task_revision':task['revision'],'manifest_id':manifest['id'],'unresolved_applicability_questions':7,'comparison_id':comparison['id'],'delivery_id':delivery['id'],'source_context_prepared_not_sent':True,'received_context':context['summary'],'unassigned_components':delivery['coverage_of_proposal']['components_without_proposals'],'clinical_review_recorded':False,'provider_calls':0,'external_actions':0}
 save('http-verification',result);print(json.dumps(result,indent=2))
if __name__=='__main__':main()
