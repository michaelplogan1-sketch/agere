"""Verify full-population rule acquisition without new labels, calls or care actions."""
import argparse,hashlib,json,zlib
from collections import Counter
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical import program_documents as D,program_acquisition as A,program_work as W


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();out=args.output;out.mkdir(exist_ok=False)
 def write(name,obj):(out/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
 prior=Path('artifacts/agere-protocol-requirements-v1');states=prior/'independent-source-states.json';manifest=json.loads((prior/'implementation-receipt.json').read_text())
 assert hashlib.sha256(states.read_bytes()).hexdigest()==manifest['evidence_sha256'][str(states)]
 expected=json.loads(states.read_text());ids=sorted(expected);stack=Stack(stack_path(),allow_live=False)
 assert stack.retriever.meta['snapshot']==json.loads((prior/'verification.json').read_text())['active_source_snapshot']
 key='c8348279d698c1e568b6f9fe5e5b684ca71e839aae118957a95e982f58e4988d';checks=Counter();samples={}
 try:
  answer=stack.compose(A.question(key));write('question.json',answer);r=answer['results'][-1]['result'];assert answer['new_paid_api_calls']==0
  proposal=A.load(stack.path,r['id']);assert [x['patient_id'] for x in proposal['references']]==ids
  db=A.storage(stack.path);doc=D.storage(stack.path)
  try:
   for ref in proposal['references']:
    pid=ref['patient_id'];row=A.decode(db,'program_resolution_rows',ref['projection_id']);assert digest(row)==ref['projection_hash']
    source=json.loads(zlib.decompress(doc.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(ref['program_receipt_id'],pid)).fetchone()[0]));assert digest(source)==ref['program_receipt_hash']
    assert source['patient_id']==row['patient_id']==pid and source['inputs']['report']['evidence']['status']==expected[pid]
    shape=sorted((c['program_value'],len(c['source_values'])) for c in row['resolution']['cases'])
    want={'supported':[(False,1),(True,1)],'contradicted':[(False,0)],'unresolved':[(False,1),(False,1),(True,2)]}[expected[pid]]
    assert shape==want;checks['patient_conditional_shapes_verified']+=1;checks['source_and_projection_hashes_verified']+=2
    assert all(not x['request_sent'] for x in row['requests']);samples.setdefault(expected[pid],pid)
   specs=[x for x in proposal['items'] if x['scope']=='specification'];assert len(specs)==2
   binding=next(x for x in specs if x['kind']=='bind_rule_input');assert binding['patients_with_dependency']==ids
   assert set(binding['patients_with_conditional_impact'])=={pid for pid in ids if expected[pid]!='contradicted'}
   assert all(item['patient_id']==item['patients_with_dependency'][0] and len(item['patients_with_dependency'])==1 for item in proposal['items'] if item['scope']=='patient')
  finally:db.close();doc.close()
  for state,pid in samples.items():write('patient-'+state+'.json',A.inspect_patient(stack.path,r['id'],pid))
  again=A.prepare(stack.path,ids,{'package':key},selection=proposal['selection']);assert again['cache_hit'] and again['processing_counts']=={'patient_projection_cache_hits':len(ids)};write('projection-replay.json',again)
  work=stack.compose(A.question(key,'protocol:affordability_action_review@2'));write('work-question.json',work);w=work['results'][-1]['result'];db=W.database(stack.path)
  try:
   pr=W.load(db,'work_proposals',w['proposal_id']);assessments=[W.load(db,'work_assessments',rid) for rid in pr['assessment_ids']]
   assert sorted(a['patient_id'] for a in assessments)==ids
   for a in assessments:
    ctx=a['decision_context']['program_acquisition'];assert ctx['patient_id']==a['patient_id'] and ctx['proposal_id']==r['id'];checks['work_assessments_with_acquisition']+=1
   write('sample-work-assessment.json',assessments[0])
  finally:db.close()
  replay=stack.compose(A.question(key));assert replay['cache_hit'];write('question-replay.json',replay)
  verification={'passed':True,'synthetic':True,'patients':len(ids),'snapshot':stack.retriever.meta['snapshot'],'package':key,'proposal':r['id'],'counts':r['counts'],'status_counts':r['status_counts'],'checks':dict(checks),'work_counts':w['counts'],'samples':samples,'new_paid_api_calls':0,'new_weights':0,'live_reviews_created':0,'live_work_items_created':0,'requests_sent':0,'clinical_accuracy_measured':False,'full_stack_acceptance_complete':False}
  write('verification.json',verification);print(json.dumps(verification,indent=2),flush=True)
 finally:stack.close()

if __name__=='__main__':main()
