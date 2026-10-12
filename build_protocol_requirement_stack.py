"""Original received-source parity for typed protocol inputs and persistent work."""
import argparse,hashlib,json,sqlite3,zlib
from collections import Counter
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical.graph_workbench import protocol_examples,protocol_work_examples
from clinical_core.agere.clinical import care_protocols as C,evidence_requirements as R,program_work as W
from verify_native_requirements import expected,CASES


def write(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();out=args.output;out.mkdir(exist_ok=False)
 stack=Stack(stack_path(),allow_live=False);src=sqlite3.connect(Path(stack.retriever.meta['snapshot']['source_dataset']['source']).as_uri()+'?mode=ro',uri=True)
 try:
  ids=sorted(r[0] for r in src.execute('SELECT patient_id FROM patients'));assert len(ids)==1614
  independent=expected(src,ids,stack.retriever.meta['snapshot']['as_of'],CASES['native_affordability_report'])
  write(out/'independent-source-states.json',independent)
  targets={pid for pid,s in independent.items() if s=='supported'};unknown={pid for pid,s in independent.items() if s=='unresolved'}
  examples=[next(e for e in protocol_examples() if e['graph']['nodes'][-1]['operation']=='protocol_review' and e['graph']['nodes'][-1]['parameters']['protocol']=='affordability_action_review'),protocol_examples()[-1],next(e for e in protocol_work_examples() if e['graph']['nodes'][-1]['parameters']['program']=='protocol:affordability_action_review')]
  answers=[]
  for n,ex in enumerate(examples,1):
   answer=stack.compose(ex['question']);assert answer['new_paid_api_calls']==0;write(out/f'question-{n}.json',answer)
   replay=stack.compose(ex['question']);assert replay['cache_hit'];write(out/f'replay-{n}.json',replay);answers.append(answer)
   print(json.dumps({'question':n,'result_id':answer['result_id'],'replay_cache_hit':True}),flush=True)
  params={'protocol':'affordability_action_review','filter':'supported'};yes,maybe,summary=C.execute(stack.path,ids,params)
  assert yes==targets and maybe==unknown;write(out/'protocol-summary.json',summary)
  prep=C.prepare(stack.path,params);db=C.storage(stack.path);rdb=R.storage(stack.path);checks=0;source_links=0;samples={};ic=C.interpretation.code_identity()
  try:
   for pid in ids:
    rid=C.receipt_id(prep,pid,ic);row=json.loads(zlib.decompress(db.execute('SELECT payload FROM receipts WHERE id=?',(rid,)).fetchone()[0]));b=row['bindings']['barrier'];ref=b['requirement_reference'];requirement=json.loads(zlib.decompress(rdb.execute('SELECT payload FROM receipts WHERE id=?',(ref['receipt_id'],)).fetchone()[0]))
    assert ref['receipt_hash']==digest(requirement) and requirement==b['requirement_evidence'] and requirement['patient_id']==row['patient_id']==pid
    assert requirement['status']==independent[pid] and row['trigger'] is requirement['value'];assert row['documentary_readiness'] is row['trigger']
    assert not row['clinical_eligibility_established'];checks+=1
    state=requirement['bindings']['barrier']['state']
    for statement in state['selected_assertions']:
     for ev in statement['evidence']['fields']:
      received=src.execute('SELECT facts FROM source_records WHERE record_id=? AND patient_id=?',(ev['record_id'],pid)).fetchone();assert received
      assert json.loads(received[0]).get(ev['field'])==ev['value'];source_links+=1
    if row['status'] not in samples:samples[row['status']]=row
  finally:db.close();rdb.close()
  for status,row in samples.items():
   assert C.inspect(stack.path,row['id'])==row;write(out/f'patient-{status}.json',row)
  work_checks=[]
  for n in (2,3):
   result=answers[n-1]['results'][-1]['result'];wdb=W.database(stack.path)
   try:
    proposal=W.load(wdb,'work_proposals',result['proposal_id']);rows=[W.load(wdb,'work_assessments',key) for key in proposal['assessment_ids']]
    assert {r['patient_id'] for r in rows}==(targets if n==2 else set(ids))
    assert {r['patient_id'] for r in rows if r['review_candidate']}==targets
    for row in rows:
     assert not row['clinical_eligibility_established']
     if row['review_candidate']:
      receipt=row['review']['protocol_evidence'];assert receipt['bindings']['barrier']['requirement_reference']['contract_id']=='native_affordability_report'
      assert row['evidence']
    work_checks.append({'question':n,'assessments':len(rows),'candidates':sum(r['review_candidate'] for r in rows),'status_counts':dict(Counter(r['status'] for r in rows))})
   finally:wdb.close()
  old={'protocol':'affordability_action_review@1','filter':'all'};_,_,legacy=C.execute(stack.path,ids,old);write(out/'legacy-version-1.json',legacy)
  assert legacy['status_counts']=={'insufficient_evidence':1614}
  historical=json.loads(Path('artifacts/agere-protocol-programs-v1/assessment-3.json').read_text())
  assert historical['program']['protocol_hash']==C.definition('affordability_action_review@1')['hash']
  result={'passed':True,'synthetic':True,'patients':len(ids),'active_source_snapshot':stack.retriever.meta['snapshot'],'independent_received_source_status_counts':dict(Counter(independent.values())),'protocol_status_counts':summary['status_counts'],'exact_requirement_receipts_checked':checks,'received_source_fields_checked':source_links,'full_patient_sets_match':True,'program_checks':work_checks,'version_1_definition_hash_preserved':True,'version_1_status_counts':legacy['status_counts'],'new_paid_api_calls':0,'work_items_created':0,'clinical_accuracy_measured':False,'clinical_eligibility_established':False}
  write(out/'verification.json',result);print(json.dumps(result,indent=2),flush=True)
 finally:src.close();stack.close()


if __name__=='__main__':main()
