import json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere import request_lineage as L


class ExecutionObservations(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.trace=self.root/'trace.jsonl'
        env=patch.dict(os.environ,{'AGERE_EXECUTION_TRACE':str(self.trace),'AGERE_EXECUTION_SQL_TRACE':'on'})
        env.start();self.addCleanup(env.stop)

    def events(self):return [json.loads(x) for x in self.trace.read_text().splitlines()]

    def test_loaded_model_is_not_usage_and_cached_use_is_distinct_from_inference(self):
        from types import SimpleNamespace
        from clinical_core.local_encoders import _model_use
        model=SimpleNamespace(identity='pinned',manifest={'model_id':'synthetic-encoder'},stats={'cache_hits':0,'texts_encoded':0,'inference_seconds':0.0})
        self.assertFalse(self.trace.exists())
        with _model_use(model,'retrieval_embedding'):model.stats['cache_hits']+=1
        event=self.events()[-1]
        self.assertEqual(event['metrics']['cache_hits'],1)
        self.assertEqual(event['metrics']['texts_encoded'],0)
        with _model_use(model,'retrieval_embedding'):model.stats['texts_encoded']+=2
        self.assertEqual(self.events()[-1]['metrics']['texts_encoded'],2)

    def test_failure_is_not_completed_and_preserves_original_exception(self):
        with self.assertRaisesRegex(ValueError,'fixture'):
            with L.component('fixture'):raise ValueError('fixture')
        event=self.events()[-1]
        self.assertEqual((event['status'],event['error_type']),('failed','ValueError'))

    def test_sql_start_is_expanded_but_does_not_claim_completion(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        L.trace_sql(db)
        self.assertEqual(db.execute('SELECT ? AS value',(7,)).fetchone(),(7,))
        event=self.events()[-1]
        self.assertEqual(event['sql'],'SELECT 7 AS value')
        self.assertTrue(event['completion_not_established'])

    def test_trace_does_not_override_existing_query_cancellation(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.set_progress_handler(lambda:1,1);L.trace_sql(db)
        with self.assertRaises(sqlite3.OperationalError):db.execute('SELECT 1')

    def test_disabled_diagnostics_have_no_file_side_effect_and_optional_sink_failure_does_not_block(self):
        with patch.dict(os.environ,{'AGERE_EXECUTION_TRACE':''}):
            with L.component('fixture'):pass
        self.assertFalse(self.trace.exists())
        with patch.dict(os.environ,{'AGERE_EXECUTION_TRACE':str(self.root)}):
            with L.component('fixture'):pass


class PlanCoverageBeforePopulationWork(unittest.TestCase):
    def test_source_scope_and_definitions_guard_ignores_only_execution_observations(self):
        from types import SimpleNamespace
        from clinical_core.agere.clinical import backstop_agent as B,decision_context as D
        tools=B.Tools.__new__(B.Tools);tools.stack=SimpleNamespace(path=Path('/fixture'));tools.as_of='2030-05-27';tools.cohort=['A'];tools.scope={'A'}
        with patch('clinical_core.agere.deliverables.answer_context.source_identity',return_value={'source_digest':'v1'}):
            with D.scope('all patients',tools.as_of) as state:
                first=tools.continuation_identity();state.note('fixture',{'observation':1})
                self.assertEqual(first,tools.continuation_identity())
                tools.cohort=[];tools.scope=set()
                self.assertNotEqual(first,tools.continuation_identity())

    def test_incomplete_plan_continues_before_any_patient_evidence_read(self):
        from types import SimpleNamespace
        from clinical_core.agere.clinical import proposers as P,atomic_questions as A,question_input as Q,system_one_gate as G
        cascade=P.Cascade(SimpleNamespace(allow_live=True),SimpleNamespace(),'GDMT medications',ledger=object())
        graph={'nodes':[{'operation':'atomic_questions','parameters':{}}]}
        def proposed(*args,early_check=None,**kwargs):
            early_check(graph)
            raise AssertionError('Population work must not start for an incomplete plan')
        with patch.object(A,'tier',side_effect=proposed),patch.object(cascade,'gaps',return_value=[]),patch.object(G,'check_plan',return_value={'reject':True,'label':'covers_part','calls':1,'cost_usd':.001}):
            with self.assertRaisesRegex(Q.QuestionNotUnderstood,'plan coverage'):cascade.tier_atomic_system_one()
        self.assertEqual(cascade.calls,1)

    def test_uncertain_plan_judgment_does_not_remove_reasoning_or_execution(self):
        from types import SimpleNamespace
        from clinical_core.agere.clinical import system_one_gate as G
        result={'label':'covers_part','decided_by':None,'probabilities':{'covers_part':.95},'steps':[]}
        with patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=SimpleNamespace(decide=lambda *a,**k:result)):
            review=G.check_plan('original request',{'nodes':[]},stack=SimpleNamespace())
        self.assertFalse(review['reject'])


if __name__=='__main__':unittest.main()
