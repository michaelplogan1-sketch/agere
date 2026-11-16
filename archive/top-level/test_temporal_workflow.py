"""Current-state uncertainty through the existing saved question/work contract."""
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tools.learning_loop import temporal_development as T
from clinical_core.agere.clinical import request_plan as R, shared_planner as S
from clinical_core.agere.deliverables import answer_context as A, workflow as W
import test_general_analysis_workflow as existing_workflow


Q = ('List patients and their latest documented carvedilol dose in the last 180 days; '
     'simulate evidence review for patients with uncertain regimens')


def spec():
    return dict(steps=[dict(name='selected',purpose='Full population with documented dose and explicit uncertainty',sql=
        "SELECT p.patient_id,d.daily_mg,d.dose_status,d.statement_state,d.source_record_ids, "
        "CASE WHEN d.dose_status IN ('at_target','below_target','not_active') THEN 0 ELSE 1 END AS uncertain "
        "FROM patient p LEFT JOIN {{current_dose:180}} d ON d.patient_id=p.patient_id AND d.drug_key='carvedilol' ORDER BY p.patient_id")],
        final='selected',returns='table',patient_selection={'column':'patient_id','quote':'List patients'},
        requirements=[dict(quote='List patients and their latest documented carvedilol dose in the last 180 days',
            applied_in=['selected'],how='Latest regimen documentation, not ingestion'),
            dict(quote='patients with uncertain regimens',applied_in=['selected'],how='Only uncertainty selects review work',outputs=['uncertain'])],
        unresolved=[], workflow=[dict(kind='simulation',title='Synthetic evidence review',program='custom',
            quote='simulate evidence review for patients with uncertain regimens',unresolved=[],
            selection={'output':'uncertain','quote':'patients with uncertain regimens'})])


class TemporalWorkflow(unittest.TestCase):
    execute = existing_workflow.GeneralAnalysisWorkflow.execute

    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path=T.fixture(Path(temp.name)/'fixture')
        self.catalog=R.Catalog.load(self.path)
        self.stack=SimpleNamespace(path=self.path,allow_live=False,shared_request_planning=True)

    def test_complete_population_and_only_uncertainty_review_work(self):
        result,ctx=self.execute(spec(),Q)
        self.assertEqual(result['answer']['patient_ids'],['A','B','C','D'])
        rows={r['patient_id']:r for r in result['answer']['table']}
        self.assertEqual([rows[p]['dose_status'] for p in ('A','B','C','D')],['not_active','below_target','conflicting',None])
        self.assertIsNone(rows['C']['daily_mg'])
        first = W.materialize(self.path,ctx['id'])
        self.assertEqual({r['patient_id'] for r in A.inspect(self.path,ctx['id'])['items']},{'C','D'})
        self.assertEqual(W.materialize(self.path,ctx['id']), first)

    def test_saved_population_boundaries_and_source_invalidation(self):
        result,ctx=self.execute(spec(),Q,population=['A','C'])
        self.assertEqual(result['answer']['patient_ids'],['A','C'])
        W.materialize(self.path,ctx['id'])
        self.assertEqual({r['patient_id'] for r in A.inspect(self.path,ctx['id'])['items']},{'C'})
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("UPDATE record SET payload=json_set(payload,'$.list_status','stopped') WHERE record_id='m3'")
        with self.assertRaises(ValueError): A.load(self.path,ctx['id'],current=True)

    def test_unqualified_interpretation_cannot_create_work(self):
        result,ctx=self.execute(spec(),Q,accepted=False)
        self.assertEqual(result['answer']['status'],'partial')
        flow=W.materialize(self.path,ctx['id'])
        self.assertEqual(flow['steps'][0]['status'],'blocked')
        self.assertEqual(A.inspect(self.path,ctx['id'])['items'],[])

    def test_received_relation_names_use_the_local_parser_without_paid_rescue(self):
        for question,expected in [
                ('Count social_need where positive = 1',2),
                ('Count social needs where positive = 0',1),
                ('Count therapy_barrier where assertion = "historical"',1),
                ('Count therapy barriers where assertion = "affirmed"',1)]:
            with self.subTest(question=question):
                model=Mock(side_effect=AssertionError('Unexpected provider fallback'))
                result=S.compose(self.stack,question,model=model)
                self.assertEqual(result['answer']['count'],expected)
                model.assert_not_called()


if __name__=='__main__': unittest.main()
