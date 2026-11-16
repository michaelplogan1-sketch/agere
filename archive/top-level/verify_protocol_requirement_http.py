"""Live documentary-to-rule-to-work and explicit version-impact evidence."""
import argparse,json,urllib.request
from pathlib import Path
from clinical_core.agere.clinical.graph_workbench import protocol_change_examples


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output
 def call(path,body=None):
  req=urllib.request.Request('http://127.0.0.1:8885'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=300) as r:return json.load(r)
 def save(name,data):(out/name).write_text(json.dumps(data,indent=2)+'\n')
 verified=[]
 for i in (1,2,3):
  previous=json.loads((out/f'question-{i}.json').read_text())
  # Saved examples carry the original question in a source field; use current
  # registered wording directly, independent of presentation receipt layout.
  from clinical_core.agere.clinical.graph_workbench import protocol_examples,protocol_work_examples
  examples=[next(e for e in protocol_examples() if e['graph']['nodes'][-1]['operation']=='protocol_review' and e['graph']['nodes'][-1]['parameters']['protocol']=='affordability_action_review'),protocol_examples()[-1],next(e for e in protocol_work_examples() if e['graph']['nodes'][-1]['parameters']['program']=='protocol:affordability_action_review')]
  result=call('/api/stack/compose',{'question':examples[i-1]['question']});save(f'http-question-{i}.json',result)
  assert result['new_paid_api_calls']==0
  detail=result['results'][-1]['result'];old=previous['results'][-1]['result']
  if i==1:
   assert detail['status_counts']==old['status_counts']
   patient=call('/api/stack/protocol-review?id='+next(p['id'] for p in detail['patients'] if p['trigger'] is True));ref=patient['bindings']['barrier']['requirement_reference']
   requirement=call('/api/stack/requirements?id='+ref['receipt_id']);assert requirement['patient_id']==patient['patient_id']
   assert requirement==patient['bindings']['barrier']['requirement_evidence'];save('http-patient.json',patient)
  else:assert detail['counts']==old['counts']
  verified.append({'question':i,'passed':True})
 board=call('/api/stack/work');program=next(p for p in board['programs'] if p['id']=='protocol:affordability_action_review');assert program['version']=='2'
 prepared=call('/api/stack/work/prepare',{'program':program['id']});assert prepared['counts']['assessed']==1614 and prepared['counts']['candidates']==115;save('http-program-proposal.json',prepared)
 versions=call('/api/stack/care-protocols');assert any(p['id']=='affordability_action_review' and p['version']=='1' for p in versions['archived_protocols'])
 question=protocol_change_examples()[0]['question'];result=call('/api/stack/compose',{'question':question});save('http-version-impact.json',result);detail=result['results'][-1]['result']
 assert detail['patients_evaluated']==1614 and detail['change_counts']=={'criterion_value_changed':449,'readiness_value_changed':449,'entered_review':115,'exited_review':0}
 patient=call('/api/stack/protocol-change?id='+detail['id']+'&patient='+detail['patients'][0]['patient_id']);save('http-version-impact-patient.json',patient)
 assert {r['protocol']['version'] for r in patient['protocol_receipts'].values()}=={'1','2'}
 replay=call('/api/stack/compose',{'question':question});assert replay['cache_hit'];save('http-version-impact-replay.json',replay)
 result={'passed':True,'synthetic':True,'questions':verified,'protocol_and_requirement_evidence_inspected':True,'current_and_archived_versions_exposed':True,'program_counts':prepared['counts'],'program_status_counts':prepared['assessment_status_counts'],'version_impact':{'id':detail['id'],'patients_evaluated':detail['patients_evaluated'],'change_counts':detail['change_counts'],'status_transitions':detail['status_transitions'],'replay_passed':True},'new_paid_api_calls':0,'work_items_created':0}
 save('http-verification.json',result);print(json.dumps(result,indent=2))


if __name__=='__main__':main()
