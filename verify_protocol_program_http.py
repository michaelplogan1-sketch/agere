"""Live question and program-workspace registration for generic review protocols."""
import argparse,json,urllib.request
from pathlib import Path
from clinical_core.agere.clinical.graph_workbench import protocol_work_examples,snapshot_examples
from clinical_core.agere.clinical import protocol_programs as P


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();rows=[]
 def call(path,body=None):
  req=urllib.request.Request('http://127.0.0.1:8885'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=180) as r:return json.load(r)
 board=call('/api/stack/work');assert {r['id'] for r in P.catalog()}<={r['id'] for r in board['programs']}
 for i,ex in enumerate(snapshot_examples(protocol_work_examples(),'2029-02-15'),1):
  answer=call('/api/stack/compose',{'question':ex['question']});result=answer['results'][-1]['result'];assert answer['new_paid_api_calls']==0
  assert result['counts']['selected']==result['counts']['assessed']==1614
  prepared=call('/api/stack/work/prepare',{'program':result['program']['id']})
  assert prepared['counts']==result['counts'] and prepared['assessment_status_counts']==result['assessment_status_counts']
  rows.append({'program':result['program']['id'],'counts':result['counts'],'assessment_status_counts':result['assessment_status_counts'],'question_and_workspace_agree':True})
  (a.output/f'http-question-{i}.json').write_text(json.dumps(answer,indent=2)+'\n')
 combined=call('/api/stack/work/prepare',{'program':'protocol:low_egfr_specialist_review','accountability':'program_payment_evidence'})
 assert combined['accountability_assessments']==1614 and combined['counts']['candidates']==1
 (a.output/'combined-workspace-proposal.json').write_text(json.dumps(combined,indent=2)+'\n')
 result={'passed':True,'synthetic':True,'generic_program_with_accountability_verified':True,'dynamic_program_registration':True,'programs':rows,'new_paid_api_calls':0,'work_items_created':0}
 (a.output/'http-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
