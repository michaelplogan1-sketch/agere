"""Audit persisted graph membership bindings and exact member evidence."""
import json,sqlite3,zlib
from pathlib import Path
from collections import Counter
from clinical_core.contracts import digest
OUT=Path(__file__).resolve().parent/'artifacts/agere-graph-decision-v1'
PATH=Path(__file__).resolve().parent/'artifacts/agere-healthcare-v123/stack'

def main():
 db=sqlite3.connect(PATH/'interpretation-state.sqlite3');src=sqlite3.connect((PATH/'stack.sqlite3').as_uri()+'?mode=ro',uri=True)
 roster=sorted(r[0] for r in src.execute('SELECT id FROM patients'));checked=0;source_count=0;cases={}
 for case in ('patient','population','prior_population'):
  graph=json.loads((OUT/('final-question-'+case+'.json')).read_text())['response']
  decision=json.loads((OUT/('final-decision-'+case+'.json')).read_text())['response']
  member=json.loads((OUT/('final-member-'+case+'.json')).read_text())['response']
  assert graph['question']==decision['question']==member['question']
  assert decision['graph_result_id']==graph['result_id']==member['graph_result_id']
  assert graph['review_source_fingerprint']==decision['dependencies']['evaluated_source_inventory']
  totals={}
  for node in graph['results']:
   if node['node']['operation'] not in ('source_state','pre_event_state'):continue
   r=node['result'];ids=[];counts=Counter();table='pre_event_states' if node['node']['operation']=='pre_event_state' else 'source_states'
   for m in r['review_members']:
    raw=db.execute('SELECT payload FROM '+table+' WHERE id=?',(m['receipt_id'],)).fetchone();row=json.loads(zlib.decompress(raw[0]))
    assert row['patient_id']==m['patient_id'] and digest(row)==m['receipt_digest']
    assert row['care_account_counts']==m['care_account_counts'];counts.update(row['care_account_counts']);ids.append(m['patient_id']);checked+=1
   assert counts==r['care_account_counts'];assert digest(sorted(ids))==r['population_digest']
   if case!='patient':assert sorted(ids)==roster
   totals[node['node']['id']]=dict(counts)
  for rid,h in member['source_versions']['records'].items():
   row=src.execute('SELECT patient,payload FROM records WHERE id=?',(rid,)).fetchone();assert row[0]==member['patient_id'] and digest(json.loads(row[1]))==h;source_count+=1
  for f in member['findings']:
   row=json.loads(zlib.decompress(db.execute('SELECT payload FROM '+('pre_event_states' if f['operation']=='pre_event_state' else 'source_states')+' WHERE id=?',(f['receipt_id'],)).fetchone()[0]))
   assert f['care_accounts']==row['care_accounts'] and f['selected_assertions']==row['selected_assertions']
   assert f['temporal_scope']==row.get('temporal_scope')
  cases[case]={'population':decision['population'],'request_counts_by_node':totals,'all_receipt_hashes_match':True,'member_source_hashes_match':True}
 report={'status':'passed','checked_state_bindings':checked,'checked_member_source_hashes':source_count,'roster':len(roster),'roster_digest':digest(roster),'cases':cases,'authority':'Exact engineering provenance and denominator checks; not clinical qualification'}
 (OUT/'binding-audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='cases'}))

if __name__=='__main__':main()
