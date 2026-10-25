"""Independent synthetic claim source contracts, not protected evaluation keys."""
import copy
import sqlite3
import unittest
from unittest.mock import Mock
from clinical_core.agere.clinical import request_plan as R, request_language as L, shared_planner as S, sql_query
from clinical_core.agere.clinical import claim_count_policy as P
import test_payer_claims_source as fixtures
import test_shared_planner as shared

class ClaimCountPolicy(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PayerClaims()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.path = self.f.path
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript('''ALTER TABLE claim ADD COLUMN place_of_service TEXT DEFAULT '20';
            ALTER TABLE payer_claim ADD COLUMN place_of_service TEXT DEFAULT '20';
            ALTER TABLE claim ADD COLUMN payer TEXT DEFAULT 'P1';
            ALTER TABLE payer_claim ADD COLUMN payer_id TEXT DEFAULT 'P1';
            INSERT INTO claim VALUES ('outside','C','2028-02-01','paid','20','P1');
            INSERT INTO payer_claim VALUES ('outside','C','2028-02-01','paid',1,'20','P1');''')
        self.catalog = R.Catalog.load(self.path)

    def execute(self, q, population=None):
        plan = L.parse(self.catalog, q, bound=population is not None)
        before = copy.deepcopy(plan)
        c = R.compile_request(self.catalog, q, plan, population=population)
        self.assertEqual(plan, before)
        R.assert_sealed(c)
        report = sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, patient_ids=population, target=self.catalog.target)
        return c, report

    def test_ambiguous_variants_preserve_two_observed_readings(self):
        for lead in ('How many', 'Count', 'Number of'):
            for noun in ('claims', 'medical claims', 'urgent care claims'):
                with self.subTest(lead=lead,noun=noun):
                    c,r=self.execute(f'{lead} {noun} in 2029?')
                    self.assertEqual([x['count'] for x in r['rows']], [3,2])
                    self.assertEqual([x['claim_scope'] for x in r['rows']],list(P.LABELS.values()))
                    self.assertIsNone(c['membership_query'])

    def test_report_context_changes_the_source_not_the_time_or_filter(self):
        for kind,expected in [('payer',2),('insurer',2),('VBC contract',2),('value-based care',2),('clinical utilization',3),('clinical',3)]:
            for template in ('Count urgent care claims in 2029 for our {kind} report', 'In the {kind} report, count urgent care claims in 2029'):
                q=template.format(kind=kind)
                with self.subTest(q=q):
                    c,r=self.execute(q);self.assertEqual(r['value'],expected)
                    self.assertIn('2029-01-01',c['query'])
                    self.assertIn("'20'",c['query'])

    def test_grouping_by_payer_does_not_choose_insurer_source(self):
        c,r=self.execute('Count claims in 2029 by payer')
        self.assertEqual([x['count'] for x in r['rows']],[3,2])
        self.assertEqual([x['payer_id'] for x in r['rows']],['P1','P1'])

    def test_saved_empty_and_nonempty_cohorts_apply_to_both_readings(self):
        for ids,expected in [([], [0,0]),(['A'], [2,1]),(['B'],[1,1])]:
            with self.subTest(ids=ids):
                c,r=self.execute('Count claims in 2029',ids)
                self.assertEqual([x['count'] for x in r['rows']],expected)

    def test_explicit_payer_source_has_one_reading(self):
        c,r=self.execute('How many payer-feed medical claims were there in 2029?')
        self.assertEqual(r['value'],2)
        self.assertEqual(c['claim_count_policy']['readings'],['eligible_insurer'])

    def test_counts_not_rates_money_or_patient_counts(self):
        for plan in [dict(source={'table':'payer_claim'},select=[{'kind':'sum','field':'payer_claim.allowed_cents'}]),
                     dict(source={'table':'payer_claim'},select=[{'kind':'count_distinct','field':'payer_claim.patient_id'}])]:
            self.assertFalse(P.eligible(plan))

    def test_missing_insurer_source_is_disclosed_not_zero_or_duplicate(self):
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:db.execute('DROP TABLE payer_claim')
        self.catalog=R.Catalog.load(self.path)
        c,r=self.execute('Count claims in 2029')
        self.assertEqual(r['value'],3)
        self.assertEqual(len(c['claim_count_policy']['missing_readings']),1)

    def test_report_words_inside_literals_are_not_report_context(self):
        q = 'Count claims where final_status = "for our payer report" in 2029'
        self.assertEqual(P.without_report(q), q)
        self.assertEqual(P.context(q), ['all_received', 'eligible_insurer'])
        c,r=self.execute(q)
        self.assertEqual([x['count'] for x in r['rows']], [0,0])
        self.assertIn('for our payer report', c['query'])

    def test_source_specific_field_keeps_known_reading_and_discloses_unknown(self):
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute('ALTER TABLE payer_claim ADD COLUMN network_status TEXT DEFAULT "in_network"')
        self.catalog=R.Catalog.load(self.path)
        c,r=self.execute('Count claims where network_status = "in_network" in 2029')
        self.assertEqual(r['value'],2)
        self.assertEqual(c['claim_count_policy']['readings'],['eligible_insurer'])
        self.assertIn('network_status',c['claim_count_policy']['missing_readings'][0])


class ClaimCountsThroughSharedPlanner(unittest.TestCase):
    def test_context_and_both_readings_ship_as_executed_answer(self):
        f=shared.SharedPlanner();f.setUp();self.addCleanup(f.doCleanups)
        with sqlite3.connect(f.path/'semantic.sqlite3') as db:
            db.executescript('''CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, service_from TEXT,counts_in_metrics INTEGER);
            INSERT INTO payer_claim VALUES ('p1','A','2029-02-01',1),('p1old','A','2029-02-01',0);''')
        for q,expected in [('Count claims in 2029',None),('Count claims in 2029 for our payer report',1)]:
            with self.subTest(q=q):
                out=S.compose(f.stack,q,model=Mock(side_effect=AssertionError('No model allowed')))
                a=out['answer'];self.assertEqual(a['status'],'answered')
                if expected is None:self.assertEqual(len(a['table']),2)
                else:self.assertEqual(a['count'],expected)
