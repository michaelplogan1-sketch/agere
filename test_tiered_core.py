"""Tiered core, no model calls: recipe matching, validation, bottle matching and promotion status, eval scoring."""
import os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest import mock
from clinical_core.agere.tiered import bottles as BS,evaluate as EV,recipes as R,router,store


class Core(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir=tempfile.TemporaryDirectory();path=Path(cls.dir.name)/'semantic.sqlite3'
        db=sqlite3.connect(path)
        db.executescript('''CREATE TABLE meta(key,value);INSERT INTO meta VALUES('version','t/1'),('source_digest','abc');
            CREATE TABLE utilization_event(patient_id,kind,event_date);
            INSERT INTO utilization_event VALUES('p1','ed_visit','2029-01-03'),('p1','ed_visit','2029-02-03'),('p2','ed_visit','2028-02-03'),('p2','inpatient','2029-02-03');
            CREATE TABLE concept_patient(concept,kind,patient_id);INSERT INTO concept_patient VALUES('heart_failure','condition','p1'),('diabetes','condition','p2');''')
        db.commit();db.close()
        cls.env=mock.patch.dict(os.environ,{'AGERE_TIERED_DB':str(path)});cls.env.start();R._vocab.clear()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop();cls.dir.cleanup();R._vocab.clear()

    def test_tier1_answers_a_matching_question_with_no_model_and_validates_it(self):
        out=router.answer('How many ED visits were there in 2029?',tiers=('recipes',))
        self.assertEqual((out['status'],out['final_tier'],out['rows']),('answered','recipes',[[2]]))
        self.assertTrue(out['validation']['ok']);self.assertEqual(sum(s.get('tokens',0)for s in out['steps']),0)
        monthly=router.answer('Show monthly ED visits in 2029',tiers=('recipes',))
        self.assertEqual(monthly['rows'],[['2029-01',1],['2029-02',1]])
        self.assertIn('total_reconciles',[c['name']for c in monthly['validation']['checks']])

    def test_unmatched_wording_and_untyped_values_do_not_guess(self):
        self.assertIsNone(R.match('Count of emergency visits during 2029'))
        self.assertIsNone(R.match('How many patients have unicorn syndrome?'))
        self.assertIsNone(R.normalize('utilization_count',{'kind':'ed_visit','year':'29'}))
        out=router.answer('Count of emergency visits during 2029',tiers=('recipes',))
        self.assertEqual((out['status'],out['final_tier']),('unanswered',None));self.assertTrue(out['escalations'])

    def test_validation_catches_duplicate_grain_and_unbound_period_and_only_reads(self):
        v=R.validate('Show x in 2029','SELECT 1',{},['month','n'],[['a',1],['a',2]],grain=['month'])
        self.assertEqual({c['name']for c in v['checks']if not c['ok']},{'grain_unique','time_window_bound'})
        for sql in ('DELETE FROM meta','SELECT 1; SELECT 2','PRAGMA table_info(meta)'):
            with self.assertRaises(store.RecipeError):store.run(sql)

    def test_bottle_is_exact_typed_provisional_by_default_and_schema_bound(self):
        with tempfile.TemporaryDirectory() as d:
            b=BS.Bottles(Path(d)/'b.sqlite3')
            recipe={'sql':"SELECT COUNT(*) AS n FROM utilization_event WHERE kind='inpatient' AND substr(event_date,1,4)=:year",
                    'params':{'year':{'type':'year','value':'2029'}},'grain':[],'question_template':'How many admissions happened in {year}?'}
            bid,why=b.add('How many admissions happened in 2029?',recipe,{'ok':True},{'tier':'llm'});self.assertIsNone(why)
            self.assertIsNone(b.match('How many admissions happened in 2028?',statuses=('promoted',)))
            out=router.answer('How many admissions happened in 2028?',tiers=('bottles',),bottles=b)
            self.assertEqual(out['status'],'unanswered')
            b.set_status(bid,'promoted','test')
            out=router.answer('How many admissions happened in 2029?',tiers=('bottles',),bottles=b)
            self.assertEqual((out['final_tier'],out['rows']),('bottles',[[1]]))
            self.assertIsNone(b.match('How many admissions happened in twenty29?'))
            self.assertIsNone(b.match('How many admissions happened in 2029 at night?'))
            bad={**recipe,'question_template':'How many admissions happened?'}
            self.assertEqual(b.add('How many admissions happened in 2029?',bad,{},{})[0],None)
            with mock.patch.object(store,'schema_version',return_value='t/2:other'):self.assertIsNone(b.match('How many admissions happened in 2029?'))

    def test_app_bridge_answers_in_the_app_shape_and_otherwise_steps_aside(self):
        from clinical_core.agere.tiered import app as A
        stack=mock.Mock(spec=[])
        with mock.patch.dict(os.environ,{'AGERE_TIERED_CORE':'off'}):self.assertIsNone(A.try_answer(stack,'How many ED visits were there in 2029?'))
        with tempfile.TemporaryDirectory() as d,mock.patch.dict(os.environ,{'AGERE_TIERED_CORE':'on','AGERE_TIERED_BOTTLES':d+'/b.sqlite3','AGERE_TIERED_LEDGER':d+'/l.sqlite3'}):
            out=A.try_answer(stack,'How many ED visits were there in 2029?')
            self.assertEqual((out['route'],out['answer']['status'],out['answer']['verification'],out['answer']['table']),('tiered_core','answered','checked',[{'events':2}]))
            self.assertEqual((out['new_paid_api_calls'],out['planner_receipt']['final_tier']),(0,'recipes'))
            for status in ('unanswered','partial'):
                with mock.patch.object(router,'answer',return_value={'status':status,'final_tier':None,'steps':[],'escalations':[],'seconds':0.1}):
                    self.assertIsNone(A.try_answer(stack,'something else'))
            llm={'status':'answered','final_tier':'llm','columns':['n'],'rows':[[1]],'steps':[{'tier':'llm','model':'m','usd':0.02,'tokens':9}],
                 'escalations':[],'seconds':1.0,'recipe':{'sql':'SELECT 1 AS n'},'validation':{'ok':True,'checks':[{'name':'rows_returned','ok':True}]}}
            with mock.patch.object(router,'answer',return_value=llm):
                self.assertEqual(A.try_answer(stack,'new shape')['answer']['verification'],'unverified')
            with mock.patch.object(router,'answer',side_effect=RuntimeError('boom')):self.assertIsNone(A.try_answer(stack,'x'))

    def test_eval_scoring_ignores_names_and_order_but_not_values_or_row_count(self):
        ref=[['a',1],['b',2]]
        self.assertTrue(EV.correct(ref,[[2,'b','extra'],[1.0,'a','x']]))
        self.assertFalse(EV.correct(ref,[['a',1]]));self.assertFalse(EV.correct(ref,[['a',1],['b',3]]));self.assertFalse(EV.correct(ref,None))


if __name__=='__main__':unittest.main()
