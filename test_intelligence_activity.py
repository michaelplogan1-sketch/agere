import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from clinical_core.agere.clinical import intelligence_activity as activity, learning, answer_log, semantic_layer

class IntelligenceActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        (self.path/'run.json').write_text(json.dumps({'source_digest':'source-a', 'synthetic':True,
          'snapshot':{'as_of':'2030-05-27'}, 'visible_records':1234}))
    def tearDown(self):
        self.temp.cleanup()
    def test_missing_is_unknown_and_read_only(self):
        before=set(self.path.iterdir())
        s=activity.snapshot(self.path)
        self.assertIsNone(s['answered']); self.assertIsNone(s['reusable_examples'])
        self.assertEqual(s['records'],1234)
        self.assertEqual(before,set(self.path.iterdir()))
    def test_answered_does_not_include_partial_failure_or_pending(self):
        for status in ('answered','answered','partial','error','not_understood'):
            answer_log.record(self.path,'private question',{'answer':{'status':status},'new_paid_api_calls':0})
        s=activity.snapshot(self.path)
        self.assertEqual(s['answered'],2)
        self.assertNotIn('private question',json.dumps(s))
        self.assertIsNone(s['reusable_examples'])
    def test_reusable_examples_exclude_corrections_and_wrong_source(self):
        learning.record_verified(self.path,'first','sql',{}, {},'test',[],'test')
        learning.record_verified(self.path,'flagged','sql',{}, {},'test',[],'test')
        learning.record_verified(self.path,'older source','sql',{}, {},'test',[],'test')
        learning.record_correction(self.path,' FLAGGED ','wrong')
        with sqlite3.connect(self.path/'learning.sqlite3') as db:
            db.execute("UPDATE verified SET context='old|version' WHERE question='older source'")
        s=activity.snapshot(self.path)
        self.assertEqual(s['reusable_examples'],1); self.assertEqual(s['corrections'],1)
        self.assertNotIn('flagged',json.dumps({k:v for k,v in s.items() if k not in ('scope','learning')}))
    def test_old_or_damaged_store_is_unavailable_without_migration(self):
        with sqlite3.connect(self.path/'learning.sqlite3') as db:
            db.execute('CREATE TABLE verified(question TEXT)')
        before=(self.path/'learning.sqlite3').read_bytes()
        self.assertIsNone(activity.snapshot(self.path)['reusable_examples'])
        self.assertEqual(before,(self.path/'learning.sqlite3').read_bytes())
    def test_source_change_preserves_new_identity_and_excludes_old_examples(self):
        learning.record_verified(self.path,'first','sql',{}, {},'test',[],'test')
        p=self.path/'run.json';d=json.loads(p.read_text());d.update(source_digest='source-b',visible_records=9999);p.write_text(json.dumps(d))
        s=activity.snapshot(self.path)
        self.assertEqual(s['source_id'],'source-b');self.assertEqual(s['records'],9999);self.assertEqual(s['reusable_examples'],0)
if __name__=='__main__':unittest.main()
