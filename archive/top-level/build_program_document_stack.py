"""Exercise synthetic quoted specifications against independent received-source sets.

No real payer rules or human approvals are supplied by this engineering receipt.
"""
import argparse,hashlib,json,sqlite3,zlib
from collections import Counter
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical.stack_runtime import Stack
from clinical_core.agere.clinical import program_documents as D,program_work as W
from test_program_documents import package


def write(path,obj):path.write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();out=args.output;out.mkdir(exist_ok=False)
 previous=Path('artifacts/agere-protocol-requirements-v1');manifest=json.loads((previous/'implementation-receipt.json').read_text());source_states=previous/'independent-source-states.json'
 assert hashlib.sha256(source_states.read_bytes()).hexdigest()==manifest['evidence_sha256'][str(source_states)]
 expected=json.loads(source_states.read_text());ids=sorted(expected);stack=Stack(stack_path(),allow_live=False)
 assert stack.retriever.meta['snapshot']==json.loads((previous/'verification.json').read_text())['active_source_snapshot']
 src=sqlite3.connect(Path(stack.retriever.meta['snapshot']['source_dataset']['source']).as_uri()+'?mode=ro',uri=True)
 try:
  assert ids==sorted(r[0] for r in src.execute('SELECT patient_id FROM patients'))
  keys=[];results=[];audit=Counter();db=D.storage(stack.path);checks=[]
  for n in (1,2):
   spec=package(str(n),extra=n==2);write(out/f'synthetic-package-{n}.json',spec);key=D.install(stack.path,spec)['id'];keys.append(key)
   answer=stack.compose(D.question(key));write(out/f'question-{n}.json',answer);assert answer['new_paid_api_calls']==0
   result=answer['results'][-1]['result'];results.append(result);assert result['patients_evaluated']==len(ids)
   replay=stack.compose(D.question(key));assert replay['cache_hit'];write(out/f'replay-{n}.json',replay)
   prep=D.prepare(stack.path,{'package':key,'filter':'all'});samples={};sets={}
   for pid in ids:
    rid=D.receipt_id(prep,pid);raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(rid,pid)).fetchone();assert raw
    row=json.loads(zlib.decompress(raw[0]));status=expected[pid] if n==1 or expected[pid]=='contradicted' else 'unresolved';assert row['status']==status;sets[pid]=status
    assert row['review']['status']=='unreviewed' and not row['clinical_eligibility_established'];audit['patient_rule_receipts_checked']+=1
    for name,binding in row['inputs'].items():
     quote=binding['quote'];assert spec['document'][quote['start']:quote['end']]==quote['quote']==spec['bindings'][name]['quote'];audit['exact_quote_spans_checked']+=1
    native=row['inputs']['report']['evidence'];assert native['patient_id']==pid and native['status']==expected[pid]
    assert digest(native)==row['inputs']['report']['requirement_reference']['receipt_hash']
    for b in native['bindings'].values():
     for statement in b['state']['selected_assertions']:
      for ev in statement['evidence']['fields']:
       source=src.execute('SELECT facts FROM source_records WHERE record_id=? AND patient_id=?',(ev['record_id'],pid)).fetchone();assert source and json.loads(source[0]).get(ev['field'])==ev['value'];audit['received_source_fields_checked']+=1
    if n==2:assert row['inputs']['attribution']['value'] is None and row['inputs']['attribution']['status']=='unbound'
    samples.setdefault(status,row)
   write(out/f'patient-states-{n}.json',sets)
   for state,row in samples.items():assert D.inspect(stack.path,row['id'])==row;write(out/f'patient-{n}-{state}.json',row)
   checks.append({'version':n,'package':key,'status_counts':dict(Counter(sets.values())),'replay_cache_hit':True,'full_patient_sets_match':True})
   print(json.dumps(checks[-1]),flush=True)
  db.close()
  work=stack.compose(D.question(keys[0],'protocol:affordability_action_review@2'));write(out/'work-question.json',work);assert work['new_paid_api_calls']==0
  result=work['results'][-1]['result'];db=W.database(stack.path)
  try:
   proposal=W.load(db,'work_proposals',result['proposal_id']);rows=[W.load(db,'work_assessments',key) for key in proposal['assessment_ids']]
   assert {r['patient_id'] for r in rows}=={pid for pid,s in expected.items() if s=='supported'}
   for row in rows:
    ctx=row['decision_context']['program_documents'];assert ctx['patient_id']==row['patient_id'];ref=ctx['references'][0]
    assert ref['package']==keys[0] and ref['status']=='supported';audit['work_selections_with_quoted_evidence']+=1
   if rows:write(out/'sample-work-assessment.json',rows[0])
  finally:db.close()
  result={'passed':True,'synthetic':True,'scope':'Synthetic engineering specifications only; no actual payer or clinical rule validation',
   'patients':len(ids),'source_snapshot':stack.retriever.meta['snapshot'],'independent_source_states_sha256':hashlib.sha256(source_states.read_bytes()).hexdigest(),
   'versions':checks,'checks':dict(audit),'work_counts':result['counts'],'new_paid_api_calls':0,'human_reviews_created':0,'work_items_created':0,
   'clinical_accuracy_measured':False,'full_stack_acceptance_complete':False}
  write(out/'verification.json',result);print(json.dumps(result,indent=2),flush=True)
 finally:src.close();stack.close()


if __name__=='__main__':main()
