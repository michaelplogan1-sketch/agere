import json,sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical.evidence_queries import validate_plan,execute,VERSION

class EvidenceQueryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  (self.root/'run.json').write_text(json.dumps({'schema':VERSION,'synthetic':True,'source_run':'snapshot','model_identity':'model','counts':{'patients':3}}))
  db=sqlite3.connect(self.root/'evidence.sqlite3');db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY); CREATE TABLE evidence(id TEXT,patient TEXT,kind TEXT,concept TEXT,assertion TEXT,subject TEXT,time TEXT,relation TEXT,value_json TEXT,payload TEXT); CREATE TABLE query_cache(id TEXT PRIMARY KEY,payload TEXT);')
  db.executemany('INSERT INTO patients VALUES (?)',[('p1',),('p2',),('p3',)])
  rows=[('1','p1','text','care nephrology','affirmed','patient','current','record_request','null','{}'),('2','p2','text','care nephrology','affirmed','patient','current','record_request','null','{}'),('3','p2','structured','referral:nephrology','affirmed','patient','unspecified','referral_record_not_visit','"completed"','{}'),('4','p3','structured','financial_hardship','affirmed','patient','unspecified','','false','{}')]
  db.executemany('INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?)',rows);db.commit();db.close()
 def tearDown(self):self.tmp.cleanup()
 def test_absent_completion_stays_unknown_care(self):
  plan={'match':'all','clauses':[{'source':'text','concept':'care nephrology','relation':'record_request'},{'source':'structured','concept':'referral:nephro','concept_match':'prefix','value_equals':'completed','observation':'not_observed'}]}
  r=execute(self.root,plan);self.assertEqual([p['patient_id'] for p in r['patients']],['p1'])
  p=r['patients'][0];self.assertFalse(p['care_gap_established']);self.assertIn('care status remains unknown',p['evidence_checks'][1]['meaning'])
  warm=execute(self.root,plan);self.assertTrue(warm['cache_hit']);self.assertEqual(warm['patients'],r['patients'])
 def test_false_and_missing_differ(self):
  plan={'match':'all','clauses':[{'source':'structured','concept':'financial_hardship','value_equals':False}]}
  self.assertEqual(execute(self.root,plan)['patients_matching_evidence_plan'],1)
  plan['clauses'][0]['observation']='not_observed'
  self.assertEqual(execute(self.root,plan)['patients_matching_evidence_plan'],2)
 def test_any_vs_all(self):
  clauses=[{'source':'text','concept':'care nephrology'},{'source':'structured','concept':'financial_hardship'}]
  self.assertEqual(execute(self.root,{'match':'any','clauses':clauses})['patients_matching_evidence_plan'],3)
  self.assertEqual(execute(self.root,{'match':'all','clauses':clauses})['patients_matching_evidence_plan'],0)
 def test_query_strings_are_literals(self):
  plan={'match':'all','clauses':[{'source':'text','concept':"' OR 1=1 --"}]}
  self.assertEqual(execute(self.root,plan)['patients_matching_evidence_plan'],0)
 def test_unsupported_clinical_predicate_rejected(self):
  for c in [{'source':'text','concept':'heart failure','needs_treatment':True},{'source':'text','concept':'x','observation':'care_absent'},{'source':'text','concept':'x','value_equals':True}]:
   with self.assertRaises(ValueError):validate_plan({'match':'all','clauses':[c]})
 def test_bounds(self):
  with self.assertRaises(ValueError):validate_plan({'match':'all','clauses':[]})
  with self.assertRaises(ValueError):execute(self.root,{'match':'all','clauses':[{'source':'text','concept':'x'}]},limit=100000)
 def test_version_changes_invalidate_cache(self):
  plan={'match':'all','clauses':[{'source':'text','concept':'care nephrology'}]};execute(self.root,plan)
  meta=json.loads((self.root/'run.json').read_text());meta['model_identity']='new-model';(self.root/'run.json').write_text(json.dumps(meta))
  self.assertFalse(execute(self.root,plan)['cache_hit'])

if __name__=='__main__':unittest.main()
