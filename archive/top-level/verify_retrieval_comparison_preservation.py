import hashlib,json,sqlite3
from pathlib import Path
from collections import Counter
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import program_work
from clinical_core.agere.gateway import BudgetLedger
OUT=Path('artifacts/agere-retrieval-comparison-v1'); PRIOR=Path('artifacts/agere-appointment-navigation-v1')
verification=json.loads((OUT/'http-verification.json').read_text());assert verification['status']=='passed'
protected=json.loads((PRIOR/'protected-for-continuation.json').read_text())['protected_files']
for p,h in protected.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h,p
for name in ('retrieval_comparison.py','retrieval_coverage.py'):
 p='clinical_core/agere/clinical/'+name;protected[p]=hashlib.sha256(Path(p).read_bytes()).hexdigest()
(OUT/'protected-for-continuation.json').write_text(json.dumps({'schema':'agere-protected-continuation/1','protected_files':protected},indent=2)+'\n')
baseline=json.loads((PRIOR/'history-baseline.json').read_text());db=program_work.database(stack_path());counts={}
for table,old in baseline.items():
 rows=[list(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')];assert rows==old,table;counts[table]=len(rows)
db.close();(OUT/'history-baseline.json').write_text(json.dumps(baseline,indent=2)+'\n')
ledger=sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3');oldledger=json.loads((PRIOR/'ledger-row-baseline.json').read_text())
assert ledger.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==len(oldledger)
for old in oldledger:assert list(ledger.execute('SELECT * FROM calls WHERE id=?',(old[0],)).fetchone())==old
ledger.close();(OUT/'ledger-row-baseline.json').write_text(json.dumps(oldledger,indent=2)+'\n')
b=BudgetLedger('artifacts/agere-full/provider-budget.sqlite3');receipt=b.receipt();b.db.close();assert receipt['spending_limit']=='uncapped'
(OUT/'ledger-after.json').write_text(json.dumps(receipt,indent=2)+'\n')
external=[r for r in json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files'] if r['path'].startswith('/Users/mikel/Documents/')]
for r in external:assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256']
db=sqlite3.connect((stack_path()/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
assert db.execute('SELECT COUNT(*) FROM patients').fetchone()[0]==1614
assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0]==1238686
source_checks=0;record_ids=set();categories={}
for name in ('medication','respiratory','cardiometabolic','functional','appointment','payer'):
 r=json.loads((OUT/(name+'.json')).read_text());assert r['identity']['comparison_code']==hashlib.sha256(Path('clinical_core/agere/clinical/retrieval_comparison.py').read_bytes()).hexdigest()
 assert r['id']==digest({k:v for k,v in r.items() if k!='id'})
 for item in r['candidates']:
  s=item['source'];patient,payload=db.execute('SELECT patient,payload FROM records WHERE id=?',(s['record_id'],)).fetchone();raw=json.loads(payload)
  assert patient==s['patient_id']==r['request']['patient'];assert digest(raw)==s['record_hash']
  assert all(raw.get(k)==s.get(k) for k in ('source_table','source_system','organization_id','event_date','available_date'))
  if s['field']=='structured_fields':text=raw['source_table']+' '+json.dumps({k:raw[k] for k in raw['_warehouse_fields']},ensure_ascii=False,sort_keys=True)
  else:text=raw[s['field']]
  assert digest(text)==s['text_field_hash'];assert text[s['start']:s['end']]==s['quote']==item['text'];source_checks+=1;record_ids.add(s['record_id'])
 coverage=json.loads((OUT/(name+'-coverage.json')).read_text());actual=Counter(x[0] for x in db.execute('SELECT stream FROM records WHERE patient=?',(coverage['patient_id'],)))
 assert actual=={x['source_category']:x['received_records'] for x in coverage['categories']}
 categories[name]={'received_categories':len(actual),'outside_candidates':coverage['categories_outside_candidates'],'outside_selected':coverage['categories_outside_final_selection']}
 if name=='payer':
  omitted=[x for x in coverage['categories'] if any(t in x['source_category'] for t in ('coverage','eligibility','enrollment'))]
  categories[name]['payer_category_inventory']=omitted
  assert any(x['received_records']>0 and x['candidates']==0 for x in omitted)
db.close()
result={'status':'passed','prior_protected_files':len(protected)-2,'protected_files':len(protected),'all_prior_history_rows_unchanged':True,'history_counts':counts,'exact_ledger_rows_preserved':len(oldledger),'separate_research_files_preserved':len(external),'independent_exact_passage_checks':source_checks,'distinct_candidate_source_records':len(record_ids),'category_inventory':categories,'clinical_accuracy_assessed':False,'provider_calls':0}
(OUT/'preservation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
