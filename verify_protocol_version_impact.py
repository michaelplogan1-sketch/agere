"""Independent full-population audit of persisted version transitions and receipts."""
import argparse,json,sqlite3,zlib
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output
 expected=json.loads((out/'independent-source-states.json').read_text());http=json.loads((out/'http-verification.json').read_text());key=http['version_impact']['id'];path=stack_path()
 db=sqlite3.connect((path/'protocol-changes.sqlite3').as_uri()+'?mode=ro',uri=True);cp=sqlite3.connect((path/'care-protocols.sqlite3').as_uri()+'?mode=ro',uri=True)
 try:
  row=json.loads(zlib.decompress(db.execute('SELECT payload FROM comparisons WHERE id=?',(key,)).fetchone()[0]))
  changes=row['patient_changes'];assert {r['patient_id'] for r in changes}==set(expected) and len(changes)==len(expected)
  statuses={'supported':'review_criterion_supported','contradicted':'criterion_not_supported','unresolved':'insufficient_evidence'};checks=0
  for change in changes:
   pid=change['patient_id'];assert change['before']['status']=='insufficient_evidence';assert change['after']['status']==statuses[expected[pid]]
   for side in ('before','after'):
    ref=change[side];receipt=json.loads(zlib.decompress(cp.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(ref['id'],pid)).fetchone()[0]))
    assert digest(receipt)==ref['receipt_hash'];assert receipt['protocol']['version']==('1' if side=='before' else '2')
    assert all(receipt[k]==ref[k] for k in ('status','trigger','documentary_readiness','review_candidate'));checks+=1
   assert change['entered_review']==(expected[pid]=='supported');assert not change['exited_review']
   assert change['criterion_value_changed']==(expected[pid]!='unresolved')
  result={'passed':True,'synthetic':True,'comparison_id':key,'complete_population_transitions_checked':len(changes),'exact_before_after_receipts_checked':checks,'independent_received_source_patient_sets_match':True,'new_paid_api_calls':0}
  (out/'version-impact-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
 finally:db.close();cp.close()


if __name__=='__main__':main()
