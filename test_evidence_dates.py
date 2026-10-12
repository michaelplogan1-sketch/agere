import json,sqlite3
import unittest
import test_evidence_queries as fixtures
from clinical_core.agere.clinical.evidence_queries import execute

class EvidenceDateTests(unittest.TestCase):
 def setUp(self):
  fixtures.EvidenceQueryTests.setUp(self)
  p=self.root/'run.json';r=json.loads(p.read_text());r['snapshot']={'as_of':'2026-09-30'};p.write_text(json.dumps(r))
  db=sqlite3.connect(self.root/'evidence.sqlite3')
  db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps({'source':{'event_date':'2025-06-01','available_date':'2026-05-01'}}),'1'))
  db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps({'source':{'event_date':'2026-02-01'}}),'2'))
  db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps({'fact':{'evidence':[{'event_date':'2025-08-01'}]}}),'3'))
  db.commit();db.close()
 def tearDown(self):
  self.tmp.cleanup()
 def test_event_date_not_arrival(self):
  plan={'match':'all','clauses':[{'source':'text','concept':'care nephrology','event_date_from':'2025-01-01','event_date_through':'2025-12-31'}]}
  r=execute(self.root,plan);self.assertEqual([p['patient_id'] for p in r['patients']],['p1'])
 def test_structured_source_date_supported(self):
  plan={'match':'all','clauses':[{'source':'structured','concept':'referral:nephrology','event_date_through':'2025-12-31'}]}
  self.assertEqual(execute(self.root,plan)['patients_matching_evidence_plan'],1)
 def test_unknown_date_does_not_match(self):
  plan={'match':'all','clauses':[{'source':'structured','concept':'financial_hardship','event_date_from':'2025-01-01'}]}
  self.assertEqual(execute(self.root,plan)['patients_matching_evidence_plan'],0)
 def test_future_range_not_silent_empty_answer(self):
  for date_field in ('event_date_from','event_date_through'):
   plan={'match':'all','clauses':[{'source':'text','concept':'care nephrology',date_field:'2027-01-01'}]}
   with self.assertRaisesRegex(ValueError,'beyond'):execute(self.root,plan)
 def test_date_changes_cache_identity(self):
  a={'match':'all','clauses':[{'source':'text','concept':'care nephrology','event_date_from':'2025-01-01','event_date_through':'2025-12-31'}]}
  b={'match':'all','clauses':[{'source':'text','concept':'care nephrology','event_date_from':'2026-01-01','event_date_through':'2026-09-30'}]}
  x=execute(self.root,a);y=execute(self.root,b);self.assertNotEqual(x['patients'],y['patients']);self.assertFalse(y['cache_hit'])
