"""Received claim identity, not patient/drug/date collisions, governs reversals."""
import copy,sqlite3,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import source_scope as S,backstop_agent as B,system_one_gate as G

QUESTION='Show paid unreversed payer pharmacy fills and plan-paid cents.'
PREFIX="SELECT p.patient_id,SUM(p.plan_paid_cents) AS paid_cents FROM payer_pharmacy_claim p WHERE p.counts_as_fill=1 AND "
BROAD="r.patient_id=p.patient_id AND r.ndc=p.ndc AND r.fill_date=p.fill_date"
BAD=PREFIX+"NOT EXISTS (SELECT 1 FROM payer_pharmacy_claim r WHERE r.claim_status='reversed' AND "+BROAD+") GROUP BY p.patient_id"
GOOD="SELECT patient_id,SUM(plan_paid_cents) AS paid_cents FROM payer_pharmacy_claim WHERE counts_as_fill=1 GROUP BY patient_id"

def call(sql,view='agere_result_1',dependencies=()):
    return {'name':'query_sql','input':{'sql':sql},'result':{'result_view':view,'executed_query':sql,
        'columns':['patient_id','paid_cents'],'rows':[{'patient_id':'A','paid_cents':200}],
        'read_views':list(dependencies),'truncated':False}}

class ReversalBinding(unittest.TestCase):
    def test_unrelated_same_day_fill_survives_registered_reversal_handling(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('''CREATE TABLE payer_pharmacy_claim(record_id,patient_id,ndc,fill_date,claim_status,
            plan_paid_cents,counts_as_fill,reversal_of_record_id,reversed_by_record_id);
          INSERT INTO payer_pharmacy_claim VALUES
            ('paid1','A','drug','2029-01-01','paid',100,0,NULL,'rev1'),
            ('paid2','A','drug','2029-01-01','paid',200,1,NULL,NULL),
            ('rev1','A','drug','2029-01-01','reversed',100,0,'paid1',NULL);''')
        self.assertEqual(db.execute(BAD).fetchall(),[])
        self.assertEqual(db.execute(GOOD).fetchall(),[('A',200)])
        self.assertTrue(S.pharmacy_reversal_problem(QUESTION,BAD))
        self.assertIsNone(S.pharmacy_reversal_problem(QUESTION,GOOD))

    def test_explicit_record_links_are_accepted_in_either_direction(self):
        for link in ['r.reversal_of_record_id=p.record_id','p.reversed_by_record_id=r.record_id']:
            self.assertIsNone(S.pharmacy_reversal_problem(QUESTION,BAD.replace(BROAD,link)))

    def test_identity_must_constrain_every_or_branch(self):
        link='r.reversal_of_record_id=p.record_id'
        self.assertTrue(S.pharmacy_reversal_problem(QUESTION,BAD.replace(BROAD,'('+link+' OR '+BROAD+')')))
        self.assertIsNone(S.pharmacy_reversal_problem(QUESTION,BAD.replace(BROAD,'('+link+' OR p.reversed_by_record_id=r.record_id)')))
        self.assertTrue(S.pharmacy_reversal_problem(QUESTION,BAD.replace(BROAD,BROAD+' AND EXISTS(SELECT 1 FROM payer_pharmacy_claim x WHERE x.reversal_of_record_id=p.record_id)')))

    def test_renamed_cte_columns_cannot_launder_broad_matching(self):
        q="WITH rev AS (SELECT patient_id AS who,ndc AS drug,fill_date AS day,reversal_of_record_id AS target FROM payer_pharmacy_claim WHERE claim_status='reversed') "+PREFIX+"NOT EXISTS(SELECT 1 FROM rev r WHERE r.who=p.patient_id AND r.drug=p.ndc AND r.day=p.fill_date) GROUP BY p.patient_id"
        self.assertTrue(S.pharmacy_reversal_problem(QUESTION,q))
        self.assertIsNone(S.pharmacy_reversal_problem(QUESTION,q.replace('r.who=p.patient_id AND r.drug=p.ndc AND r.day=p.fill_date','r.target=p.record_id')))

    def test_left_antijoin_and_not_in_require_record_identity(self):
        left="SELECT p.patient_id,SUM(p.plan_paid_cents) AS paid_cents FROM payer_pharmacy_claim p LEFT JOIN payer_pharmacy_claim r ON r.claim_status='reversed' AND "+BROAD+" WHERE p.counts_as_fill=1 AND r.record_id IS NULL GROUP BY p.patient_id"
        self.assertTrue(S.pharmacy_reversal_problem(QUESTION,left))
        self.assertIsNone(S.pharmacy_reversal_problem(QUESTION,left.replace(BROAD,'r.reversal_of_record_id=p.record_id')))
        negative=PREFIX+"p.patient_id NOT IN(SELECT r.patient_id FROM payer_pharmacy_claim r WHERE r.claim_status='reversed') GROUP BY p.patient_id"
        self.assertTrue(S.pharmacy_reversal_problem(QUESTION,negative))
        self.assertIsNone(S.pharmacy_reversal_problem(QUESTION,negative.replace('p.patient_id NOT IN(SELECT r.patient_id','p.record_id NOT IN(SELECT r.reversal_of_record_id')))

    def test_reversal_status_lists_in_ctes_preserve_identity_requirement(self):
        prefix="WITH rev AS (SELECT record_id,reversal_of_record_id,rx_claim_id FROM payer_pharmacy_claim WHERE claim_status IN ('reversed','recouped')) "
        query=prefix+PREFIX+"p.rx_claim_id NOT IN(SELECT r.rx_claim_id FROM rev r) GROUP BY p.patient_id"
        self.assertTrue(S.pharmacy_reversal_problem(QUESTION,query))
        self.assertIsNone(S.pharmacy_reversal_problem(QUESTION,query.replace('p.rx_claim_id NOT IN(SELECT r.rx_claim_id','p.record_id NOT IN(SELECT r.reversal_of_record_id')))

    def test_exploration_and_unused_ctes_do_not_poison_corrected_results(self):
        self.assertIsNone(S.pharmacy_reversal_problem('Inspect potential same-patient same-drug date collisions.',BAD))
        self.assertIsNone(S.pharmacy_reversal_problem(QUESTION,'WITH unused AS ('+BAD+') '+GOOD))
        calls=[call(BAD),call(GOOD,'agere_result_2')]
        answer={'status':'answered','answer_type':'table','table_from_tool':'agere_result_2'}
        with patch.object(G,'ready',return_value=False):
            verdict=B.review_final(SimpleNamespace(),QUESTION,answer,calls,SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
        self.assertNotEqual(verdict.get('repair_kind'),'deterministic_compiler')

    def test_final_repair_precedes_any_paid_review_and_keeps_native_descendants_bound(self):
        for calls in [[call(BAD)],[call(BAD),call('SELECT * FROM agere_result_1','agere_result_2',['agere_result_1'])]]:
            answer={'status':'answered','answer_type':'table','table_from_tool':calls[-1]['result']['result_view']}
            with patch.object(G,'ready',side_effect=AssertionError('Reject unsupported relation before model checks')):
                verdict=B.review_final(SimpleNamespace(),QUESTION,answer,calls,SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
            self.assertEqual(verdict['status'],'repair')
            self.assertEqual(verdict['calls'],0)
            self.assertIn('unproven reversal relationship',verdict['feedback'])
            self.assertEqual(answer['table'][0]['paid_cents'],200)

if __name__=='__main__':unittest.main()
