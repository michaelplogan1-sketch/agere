"""Spec workers retain request cost ownership without changing model policy."""
import contextvars
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import cost_telemetry as T, spec_verify as V, system_one as S
from clinical_core.agere.gateway import BudgetLedger
from test_cost_telemetry import paid


class SpecOwnership(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'ledger.sqlite3'
        ledger=BudgetLedger(self.path);ledger.close()

    def run_question(self,qid,decide,ledger=None):
        probe=T.start(qid,question_id=qid,ledger_path=self.path)
        try:
            result=V.verify(None,qid,{},SimpleNamespace(identity={'as_of':'2030-05-27'}),
                ledger=ledger if ledger is not None else self.path,decide=decide,workers=2,
                parts=[{'id':'predicate','kind':'predicate','text':'Authored fixture'}])
            return result,probe.finish()
        finally:
            if probe._token is not None:probe.finish()

    def test_two_overlapping_specs_own_only_their_attempts_and_cache_use_stays_free(self):
        # Both questions finish dispatch before either takes a final ledger snapshot.
        barrier=threading.Barrier(4)
        seen=[]
        sentinel=contextvars.ContextVar('spec-policy-sentinel',default='worker-default')
        def decide(question,options,evidence,**kwargs):
            qid=question.split('Question asked: ',1)[1].split('\n',1)[0]
            key=qid+'-'+evidence[0]['id']
            ledger,owned=S._open_ledger(kwargs['ledger'])
            try:
                seen.append((qid,T.CURRENT.get(),sentinel.get()))
                paid(ledger,key,'jev-1.13.0','{}',provider='jev')
                barrier.wait(timeout=5)
            finally:
                if owned:ledger.close()
            return {'label':'yes','probabilities':{'yes':1},'decided_by':'fixture','steps':[]}
        def run(qid):
            sentinel.set('parent-policy')
            return self.run_question(qid,decide)
        with patch.object(V.spec_readback,'read_back',return_value='Fixture complete statement'):
            with ThreadPoolExecutor(max_workers=2)as pool:
                results=list(pool.map(run,['question-A','question-B']))
        self.assertEqual(len(seen),4)
        self.assertTrue(all(qid==owner and policy=='worker-default'for qid,owner,policy in seen))
        for qid,(result,tel)in zip(['question-A','question-B'],results):
            self.assertTrue(result['confirmed'])
            self.assertEqual(set(tel['attempt_ids']),{qid+'-predicate',qid+'-completeness'})
            self.assertEqual(tel['api_calls'],2);self.assertTrue(tel['ownership_complete'])
        # A second request consumes the original answers without stealing charge ownership.
        def cached(question,options,evidence,**kwargs):
            ledger,owned=S._open_ledger(kwargs['ledger'])
            try:ledger.reserve('question-A-'+evidence[0]['id'],'jev','jev-1.13.0')
            finally:
                if owned:ledger.close()
            return {'label':'yes','probabilities':{'yes':1},'decided_by':'fixture','steps':[]}
        with patch.object(V.spec_readback,'read_back',return_value='Fixture complete statement'):
            result,tel=self.run_question('reuse',cached)
        self.assertTrue(result['confirmed']);self.assertEqual(tel['api_calls'],0)
        self.assertEqual(len(tel['attempts']),2);self.assertTrue(all(a['cache_hit']for a in tel['attempts']))

    def test_conflicting_bound_owner_is_not_erased_by_worker_dispatch(self):
        def decide(question,options,evidence,**kwargs):
            ledger,owned=S._open_ledger(kwargs['ledger'])
            try:paid(ledger,'conflict-'+evidence[0]['id'],'jev-1.13.0','{}',provider='jev')
            finally:
                if owned:ledger.close()
            return {'label':'yes','probabilities':{'yes':1},'decided_by':'fixture','steps':[]}
        with patch.object(V.spec_readback,'read_back',return_value='Fixture complete statement'):
            result,tel=self.run_question('current',decide,T.QuestionLedgerPath(self.path,'different'))
        self.assertTrue(result['confirmed'])
        self.assertFalse(tel['ownership_complete'])
        self.assertTrue(tel['attribution_issues'])
        self.assertTrue(all(x['reason']=='ledger_owner_context_conflict'for x in tel['attribution_issues']))


if __name__=='__main__':unittest.main()
