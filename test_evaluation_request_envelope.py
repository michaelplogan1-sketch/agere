import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere import request_lineage as L
class EvaluationEnvelope(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def events(self,execution):
  with L.database(self.root) as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM question_request_events WHERE execution=? ORDER BY sequence',(execution.id,))]
 def test_evaluation_dispatch_envelope_keeps_started_and_finished_events_private(self):
  from clinical_core.agere.clinical import evaluation
  from email.message import Message
  headers=Message();headers[evaluation.HEADER]='1';headers[evaluation.FROZEN_HEADER]='dispatch-evaluation'
  # Establish normal operational schema and a pre-existing journal row.
  with L.begin(self.root,'/api/stack/compose',{'question':'seed'}) as old:old.finish({'route':'local'})
  with L.database(self.root) as db:before=db.execute('SELECT * FROM question_request_events ORDER BY rowid').fetchall()
  with patch.dict('os.environ',{'AGERE_EVALUATION_SCRATCH':str(self.root/'scratch')}):
   with evaluation.request_scope(headers,self.root):
    with L.begin(self.root,'/api/stack/compose',{'question':'evaluated'}) as current:
     L.observe('marker',marker='private');current.finish({'route':'local'})
    self.assertEqual([e['kind'] for e in self.events(current)],['started','marker','completed'])
    self.assertEqual(evaluation.receipt()['write_attempts_by_store'],{})
  with L.database(self.root) as db:self.assertEqual(db.execute('SELECT * FROM question_request_events ORDER BY rowid').fetchall(),before)
