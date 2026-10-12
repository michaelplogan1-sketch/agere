"""Full denominator/receipt/source audit and independent native-screen baseline."""
import json,sqlite3,zlib
from pathlib import Path
from collections import Counter,defaultdict
from clinical_core.contracts import digest
from clinical_core.agere.clinical import graph_criteria as C
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-graph-criteria-v1';STACK=ROOT/'artifacts/agere-healthcare-v123/stack'
def main():
 graph=json.loads((OUT/'final-question.json').read_text())['response'];decision=json.loads((OUT/'final-decision.json').read_text())['response']
 audits=[];criteria_count=0;rawstates=defaultdict(list)
 with sqlite3.connect(STACK/'stack.sqlite3') as db:
  roster=sorted(x[0] for x in db.execute('SELECT id FROM patients'))
  for pid,payload in db.execute("SELECT patient,payload FROM records WHERE stream='financial_screens'"):
   r=json.loads(payload);v=r.get('difficulty_paying_medical_bills')
   rawstates[pid].append(v if type(v) is bool and r.get('event_date') and r['event_date']<=graph['snapshot']['as_of'] else None)
  expected={pid:('supported' if any(v is True for v in rawstates[pid]) else 'contradicted' if rawstates[pid] and all(v is False for v in rawstates[pid]) else 'unresolved') for pid in roster}
  source_checks=0
  for item in graph['results']:
   n,r=item['node'],item['result']
   if n['operation'] not in C.OPERATIONS:continue
   assert len(r['review_members'])==1614 and digest(sorted(m['patient_id'] for m in r['review_members']))==digest(roster)
   module=C.MODULES[n['operation']];store=module.storage(STACK);statuses=Counter()
   try:
    for m in r['review_members']:
     row=json.loads(zlib.decompress(store.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(m['receipt_id'],m['patient_id'])).fetchone()[0]))
     assert digest(row)==m['receipt_digest'] and row['patient_id']==m['patient_id'] and row['status']==m['status']
     assert row['as_of']==graph['snapshot']['as_of'];statuses[row['status']]+=1;criteria_count+=1
     if n['operation']=='requirements':
      assert row['contract']['id']==n['parameters']['contract'] and row['contract']['hash']==C.R.definition(n['parameters']['contract'])['hash']
      assert row['status']==expected[m['patient_id']],m['patient_id']
     if n['operation']=='protocol_review':assert row['protocol']['hash']==C.C.definition(n['parameters']['protocol'])['hash']
     if n['operation']=='program_document':assert row['package_id']==n['parameters']['package']
   finally:store.close()
   assert dict(statuses)==r['status_counts'];audits.append({'node':n,'members':1614,'status_counts':dict(statuses)})
  for p in OUT.glob('final*member.json'):
   row=json.loads(p.read_text())['response']
   for rid,h in row['source_versions']['records'].items():
    pid,raw=db.execute('SELECT patient,payload FROM records WHERE id=?',(rid,)).fetchone()
    assert pid==row['patient_id'] and digest(json.loads(raw))==h;source_checks+=1
 report={'status':'passed','criterion_member_receipt_bindings':criteria_count,'member_source_hashes_checked':source_checks,
  'population':len(roster),'roster_digest':digest(roster),'criterion_nodes':audits,'independent_financial_screen_counts':dict(Counter(expected.values())),
  'baseline_scope':'Raw financial_screens Boolean reports with observed source dates; no clinical or payer eligibility inference.',
  'clinical_eligibility_established':False}
 (OUT/'binding-audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
if __name__=='__main__':main()
