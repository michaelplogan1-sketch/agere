import hashlib,sqlite3,tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from clinical_core.agere.clinical import note_questions as N, cost_telemetry as C, evaluation as E
from clinical_core.agere.gateway import BudgetLedger

class WorkerAttribution(unittest.TestCase):
 def test_parallel_note_calls_keep_question_and_evaluation_context(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);path=root/'ledger.sqlite3';stack=root/'stack';stack.mkdir()
   BudgetLedger(path).close();E.install_audit();self.addCleanup(E.uninstall_audit)
   def parent(question):
    token=C.CURRENT.set(question)
    try:
     with E.scope(question,stack):
      def read(i):
       ledger=BudgetLedger(path)
       try:ledger.reserve(hashlib.sha256((question+str(i)).encode()).hexdigest(),'jev','fixture-model')
       finally:ledger.close()
       with sqlite3.connect(stack/'model-cache.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS cache(x)');db.execute('INSERT INTO cache VALUES (?)',(i,))
       return (i,C.CURRENT.get(),E.frozen_snapshot())
      with ThreadPoolExecutor(max_workers=2) as pool:rows=N._map_context(pool,read,range(4))
      self.assertEqual(rows,[(i,question,question)for i in range(4)])
      self.assertEqual(E.receipt()['allowed_writes_by_store'],{'model-cache.sqlite3':4})
      self.assertEqual(E.receipt()['write_attempts_by_store'],{})
    finally:C.CURRENT.reset(token)
   with patch.dict('os.environ',{'AGERE_EVALUATION':''}),ThreadPoolExecutor(max_workers=2) as parents:
    list(parents.map(parent,['question-a','question-b']))
   with sqlite3.connect(path) as db:
    self.assertEqual(dict(db.execute('SELECT question_id,count(*) FROM call_questions GROUP BY question_id')),{'question-a':4,'question-b':4})
   self.assertIsNone(C.CURRENT.get())
