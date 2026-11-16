"""Connected evidence preserves literal source boundaries and exact reader targets."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from clinical_core.agere.clinical import evidence_threads as t, semantic_reader
from clinical_core.agere.clinical import warehouse_interpretation as native
from test_warehouse_interpretation import record, ASOF

class Threads(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
  (self.path/'run.json').write_text(json.dumps({'synthetic':True,'snapshot':{'as_of':ASOF},'source_digest':'fixture'}))
  self.db=sqlite3.connect(self.path/'stack.sqlite3');self.addCleanup(self.db.close)
  self.db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY);CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT);CREATE INDEX by_stream ON records(stream);')
  self.db.executemany('INSERT INTO patients VALUES (?)',[('P-1',),('P-2',)]);self.db.commit()
 def put(self,*rows):
  for r in rows:self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',(r['record_id'],r['patient_id'],r['source_table'],json.dumps(r)))
  self.db.commit()
 def chain(self):
  self.put(record('a','medication_information_requests',original_request_record_id='c',requested_information='Review outside records.'),record('b','pharmacy_information_responses',request_record_id='a'),record('c','medication_information_requests',original_request_record_id='a'),record('d','received_pharmacy_information',request_record_id='a',original_response_record_id='b'))
 def test_cyclic_component_is_bounded_and_directed(self):
  self.chain();x=t.inspect(self.path,'P-1','a',2)
  self.assertEqual((x['component_records'],x['displayed_records'],x['omitted_records']),(4,2,2));self.assertTrue(x['boundary_links'])
  self.assertTrue(all(e['from'] in {n['record_id'] for n in x['nodes']} and e['to'] in {n['record_id'] for n in x['nodes']} for e in x['edges']))
  self.assertFalse(x['clinical_truth_established']);self.assertEqual(x['new_paid_api_calls'],0)
  self.assertIn('all received records',x['context_scope'])
 def test_cross_patient_and_wrong_type_are_issues_not_evidence(self):
  self.put(record('a','received_pharmacy_information',request_record_id='b',original_response_record_id='c'),record('b','medication_information_requests',patient_id='P-2',requested_information='NEVER DISCLOSE'),record('c','financial_screens',difficulty_paying_medical_bills=True))
  x=t.inspect(self.path,'P-1','a');self.assertEqual({i['reason'] for i in x['unresolved_references']},{'cross_patient_link_rejected','wrong_linked_source_type'})
  self.assertEqual(len(x['nodes']),1);self.assertNotIn('NEVER DISCLOSE',json.dumps(x))
  identity=x['id'];self.put(record('b','medication_information_requests',patient_id='P-2',requested_information='CHANGED'))
  self.assertNotEqual(identity,t.inspect(self.path,'P-1','a')['id'])
 def test_unreceived_reference_remains_unknown(self):
  self.put(record('a','pharmacy_information_responses',request_record_id='missing'))
  x=t.inspect(self.path,'P-1','a');self.assertEqual(x['unresolved_references'][0]['reason'],'referenced_source_not_received');self.assertEqual(x['edges'],[])
 def test_mapped_nested_text_exact_offsets_and_hidden_fields(self):
  r=record('n','imported_documents',history_text='Old text.\\n  Held pending outside results.\nCare status unknown.',truth={'label':'SECRET'})
  self.put(r);x=t.inspect(self.path,'P-1','n');self.assertNotIn('SECRET',json.dumps(x));paragraphs=x['nodes'][0]['narrative']['paragraphs'];self.assertEqual(len(paragraphs),3)
  for p in paragraphs:self.assertEqual(r[p['field']][p['start']:p['end']],p['text'])
 def test_budget_omits_whole_units(self):
  r=record('n','imported_documents',history_text='x'*13000+'\nShort exact statement.')
  self.put(r);p=t.inspect(self.path,'P-1','n')['nodes'][0]['narrative'];self.assertEqual(p['omitted_paragraphs'],1);self.assertFalse(p['complete_narrative_display']);self.assertEqual(p['paragraphs'][0]['text'],'Short exact statement.');self.assertEqual(p['paragraphs'][0]['start'],13001)
 def test_source_mutation_changes_identity(self):
  self.chain();a=t.inspect(self.path,'P-1','a')['id'];self.put(record('b','pharmacy_information_responses',request_record_id='a',response_status='received'))
  self.assertNotEqual(a,t.inspect(self.path,'P-1','a')['id'])
 def test_invalid_scope_fails_closed(self):
  self.chain()
  for patient,rid in [('P-2','a'),('P-9','a'),('P-1','unknown')]:
   with self.assertRaises(ValueError):t.inspect(self.path,patient,rid)
  for count in [0,25,True]:
   with self.assertRaises(ValueError):t.inspect(self.path,'P-1','a',count)
  with self.assertRaises(ValueError):t.records(self.path,'invented')
  with self.assertRaises(ValueError):t.records(self.path,'medication_information_requests',offset=-1)
 def test_unmatched_or_future_rows_cannot_enter_patient_graph(self):
  for field,value in [('identity_status','ambiguous'),('available_date','2030-01-01'),('synthetic',False)]:
   self.put(record('a','medication_information_requests',**{field:value}))
   with self.assertRaises(ValueError):t.inspect(self.path,'P-1','a')
 def test_inventory_pagination_patient_filter(self):
  for i in range(15):self.put(record(str(i),'medication_information_requests'))
  self.put(record('other','medication_information_requests',patient_id='P-2'))
  a=t.atlas(self.path);self.assertEqual((a['records'],a['patients'],a['source_categories']),(16,2,1))
  p=t.records(self.path,'medication_information_requests','P-1');q=t.records(self.path,'medication_information_requests','P-1',12)
  self.assertEqual((p['total'],len(p['records']),len(q['records'])),(15,12,3));self.assertFalse({r['record_id'] for r in p['records']}&{r['record_id'] for r in q['records']})
 def test_registered_reference_types_and_dates_are_preserved(self):
  self.put(record('o','medication_orders',start_time='2028-08-28T12:00:00+00:00'),record('d','medication_administrations',order_record_id='o'))
  x=t.inspect(self.path,'P-1','d');self.assertEqual(x['edges'][0]['field'],'order_record_id');self.assertEqual(x['nodes'][1]['clinical_clock']['clinical_date'],'2028-08-28')

if __name__=='__main__':unittest.main()
