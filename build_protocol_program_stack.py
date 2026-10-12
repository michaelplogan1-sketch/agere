"""Exercise all registered protocol programs against the active synthetic cohort."""
import argparse,json
from collections import Counter
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import protocol_work_examples,snapshot_examples
from clinical_core.agere.clinical import program_work as W,care_protocols as C


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(exist_ok=False)
 stack=Stack(stack_path(),allow_live=False);reports=[]
 try:
  for i,ex in enumerate(snapshot_examples(protocol_work_examples(),stack.retriever.meta['snapshot']['as_of']),1):
   answer=stack.compose(ex['question']);(a.output/f'question-{i}.json').write_text(json.dumps(answer,indent=2)+'\n')
   result=answer['results'][-1]['result'];assert result['counts']['selected']==result['counts']['assessed']==1614 and answer['new_paid_api_calls']==0
   db=W.database(stack.path);cpdb=C.storage(stack.path);statuses=Counter();evidence_links=0
   try:
    proposal=W.load(db,'work_proposals',result['proposal_id']);assert len(proposal['assessment_ids'])==1614
    for key in proposal['assessment_ids']:
     row=W.load(db,'work_assessments',key);ctx=row['protocol_context'];raw=cpdb.execute('select payload from receipts where id=?',(ctx['receipt_id'],)).fetchone();assert raw
     protocol=json.loads(__import__('zlib').decompress(raw[0]));assert digest(protocol)==ctx['receipt_hash'] and protocol['patient_id']==row['patient_id']
     assert row['review_candidate']==protocol['review_candidate'] and row['status']==protocol['status']
     assert not row['clinical_eligibility_established'];statuses[row['status']]+=1;evidence_links+=len(row['evidence'])
    assert dict(statuses)==result['assessment_status_counts']
    sample=W.load(db,'work_assessments',result['patients'][0]['assessment_id'] if result['patients'] else proposal['assessment_ids'][0]);(a.output/f'assessment-{i}.json').write_text(json.dumps(sample,indent=2)+'\n')
   finally:db.close();cpdb.close()
   replay=stack.compose(ex['question']);assert replay['cache_hit'] and replay['new_paid_api_calls']==0
   (a.output/f'replay-{i}.json').write_text(json.dumps(replay,indent=2)+'\n')
   row={'program':result['program'],'patients_assessed':1614,'candidate_counts':result['counts'],'status_counts':dict(statuses),'exact_protocol_receipts_checked':1614,'candidate_source_links':evidence_links,'replay_passed':True}
   reports.append(row);print(json.dumps(row),flush=True)
  report={'passed':True,'synthetic':True,'source_snapshot':stack.retriever.meta['snapshot'],'programs':reports,'new_paid_api_calls':0,'work_items_created':0,'clinical_eligibility_established':False,'clinical_analyses_rewritten':False}
  (a.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print('All registered protocol programs verified',flush=True)
 finally:stack.close()


if __name__=='__main__':main()
