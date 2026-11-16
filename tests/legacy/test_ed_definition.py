"""One governed ED visit definition across EHR / ADT / claims with availability status (synthetic, hand-checkable)."""
import sqlite3
import unittest

from clinical_core.agere.clinical import ed_definition as ED

PLAN = {'coverage': 'supported_observation', 'unmet_requirements': []}


def warehouse():
    db = sqlite3.connect(':memory:')
    db.executescript('''
    CREATE TABLE patient(patient_id TEXT, pcp_practice_id TEXT, payer_id TEXT);
    INSERT INTO patient VALUES('A','ORG1','P1'),('B','ORG1','P1'),('C','ORG2','P2'),('D','ORG2','P2'),('E','ORG2','P2');
    CREATE TABLE encounter(record_id TEXT, patient_id TEXT, source_table TEXT, encounter_id TEXT, type_concept TEXT, status TEXT, event_date TEXT);
    INSERT INTO encounter VALUES
      ('r1','A','encounters','e1','emergency_visit','completed','2029-03-01T10:00'),
      ('r2','A','encounters','e1','emergency_visit','in_progress','2029-03-01T08:00'),  -- a version of the same visit
      ('r3','B','encounters','e2','emergency_visit','cancelled','2029-04-01'),          -- cancelled: not a visit
      ('r4','B','adt_messages','x9','emergency_visit','completed','2029-05-01'),        -- client_data copy of an ADT row: ignored
      ('r5','C','encounters','e5','primary_care_visit','completed','2029-02-01');
    CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type_concept TEXT, event_date TEXT, principal_icd10 TEXT);
    INSERT INTO visit VALUES('e1','A','emergency_visit','2029-03-01T10:00','J06.9');
    CREATE TABLE adt_visit(visit_number TEXT, patient_id TEXT, patient_class TEXT, final_patient_class TEXT, admit_date TEXT, record_id TEXT);
    INSERT INTO adt_visit VALUES('V1','A','E','E','2029-03-01','a1'),   -- same day as the EHR visit: one visit
                                ('V2','B','E','E','2029-05-01','a2'),   -- ADT only
                                ('V3','D','E','I','2029-06-01','a3');   -- E->I with no other evidence: unresolved
    CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, place_of_service TEXT, type_of_bill TEXT,
                             principal_diagnosis TEXT, final_status TEXT, counts_in_metrics INTEGER, record_id TEXT);
    INSERT INTO payer_claim VALUES('pc1','C','2029-07-01','23',NULL,'R0789','denied',1,'c1'),  -- denied still proves the visit (HOS-09)
                                  ('pc2','C','2029-08-01','23',NULL,'R0789','voided',1,'c2'),  -- voided: not a visit
                                  ('pc3','C','2029-09-01','11',NULL,'R51','paid',1,'c3'),      -- ED CPT on the line
                                  ('pc4','B','2029-05-01','23','0111','I21.4','paid',1,'c4');  -- same day as ADT; admitted
    CREATE TABLE payer_claim_line(payer_claim_id TEXT, revenue_code TEXT, cpt TEXT);
    INSERT INTO payer_claim_line VALUES('pc3',NULL,'99284');
    CREATE TABLE member_month(patient_id TEXT, month TEXT);
    ''')
    db.executemany('INSERT INTO member_month VALUES(?,?)', [(p, f'2029-{m:02d}') for p in 'ABCDE' for m in range(1, 13)])
    return db


class Definition(unittest.TestCase):
    def setUp(self):
        self.db = warehouse(); self.addCleanup(self.db.close)

    def test_one_visit_per_patient_day_across_sources(self):
        ev = ED.events(self.db, '2029-01-01', '2029-12-31', include_unresolved=True)
        got = {(e['patient_id'], e['day']): (e['sources'], e['classification'], e['admitted']) for e in ev}
        self.assertEqual(set(got), {('A', '2029-03-01'), ('B', '2029-05-01'), ('C', '2029-07-01'), ('C', '2029-09-01'), ('D', '2029-06-01')})
        self.assertEqual(sorted(got[('A', '2029-03-01')][0].split(',')), ['adt', 'ehr'])
        self.assertEqual(got[('B', '2029-05-01')][2], True)               # inpatient bill on the same claim
        self.assertEqual(got[('D', '2029-06-01')][1], 'unresolved')
        principal = {e['patient_id']: e['principal'] for e in ev}
        self.assertEqual(principal['A'], 'J06.9')                          # EHR principal wins

    def test_engine_counts_rate_and_status(self):
        r = ED.run(self.db, None, {'period': {'start': '2029-01-01', 'end': '2029-12-31'}}, '2030-05-27')
        self.assertEqual((r['ed_visits'], r['unresolved_registrations'], r['ed_status']), (4, 1, 'observed'))
        self.assertEqual(r['ed_per_1000'], 800.0)                          # 4 visits / 5 member-years
        a = ED.answer(r, PLAN, 'What is our ED visit rate per 1,000 in 2029?')
        self.assertEqual((a['status'], a['value']), ('answered', 800.0))
        self.assertTrue(any('unresolved' in x for x in a['limitations']))
        p = ED.run(self.db, None, {'period': {'start': '2029-01-01', 'end': '2029-12-31'}, 'output': 'patients', 'min_visits': 2}, '2030-05-27')
        self.assertEqual(p['patient_ids'], ['C'])
        tr = ED.run(self.db, None, {'period': {'start': '2029-01-01', 'end': '2029-12-31'}, 'admitted': 'exclude'}, '2030-05-27')
        self.assertEqual(tr['ed_visits'], 3)

    def test_true_zero_not_delivered_and_unresolved(self):
        z = ED.run(self.db, ['E'], {'period': {'start': '2029-01-01', 'end': '2029-12-31'}}, '2030-05-27')
        self.assertEqual(z['ed_status'], 'true_zero')                      # enrolled, claims delivered, no visit
        self.assertIn('true zero', ED.answer(z, PLAN, 'q')['value'])
        n = ED.run(self.db, None, {'period': {'start': '2019-01-01', 'end': '2019-12-31'}}, '2030-05-27')
        self.assertEqual(n['ed_status'], 'source_not_delivered')
        self.assertEqual(ED.answer(n, PLAN, 'q')['status'], 'unsupported')
        u = ED.run(self.db, ['D'], {'period': {'start': '2029-06-01', 'end': '2029-06-30'}}, '2030-05-27')
        self.assertEqual(u['ed_status'], 'definition_unresolved')

    def test_undelivered_gate_names_a_missing_ed_source(self):
        import tempfile, os
        from clinical_core.agere.clinical import delivered_inputs as DI
        with tempfile.TemporaryDirectory() as d:
            none = sqlite3.connect(os.path.join(d, 'semantic.sqlite3'))
            none.executescript('CREATE TABLE patient(patient_id TEXT); INSERT INTO patient VALUES("A");'); none.commit(); none.close()
            miss = DI.undelivered(d, 'How many ED visits did we have in 2029?')
            self.assertTrue(any(m['source'] == 'ed_definition' for m in miss))
            self.assertFalse(any(m['source'] == 'ed_definition' for m in DI.undelivered(d, 'How many patients are there?')))
            with_ed = sqlite3.connect(os.path.join(d, 'semantic.sqlite3'))
            with_ed.executescript("CREATE TABLE adt_visit(patient_id TEXT, admit_date TEXT, patient_class TEXT); INSERT INTO adt_visit VALUES('A','2029-01-01','O');")
            with_ed.commit(); with_ed.close()
            self.assertFalse(any(m['source'] == 'ed_definition' for m in DI.undelivered(d, 'How many ED visits did we have in 2029?')))

    def test_relation_sql_is_the_shared_definition(self):
        rel = ED.relation_sql(ED.columns(self.db))
        n = self.db.execute(f"SELECT count(*) FROM {rel} WHERE classification = 'ed'").fetchone()[0]
        self.assertEqual(n, 4)
        self.assertEqual(self.db.execute(f'SELECT count(*) FROM ({ED.visits_sql(self.db)})').fetchone()[0], 4)


class Routing(unittest.TestCase):
    def test_exact_questions_claimed_and_qualified_ones_declined(self):
        q = lambda s: (ED.local_question(s, '2030-05-27') or {}).get('nodes', [{}])[0].get('parameters')
        self.assertEqual(q('How many ED visits were there in 2029?')['period'], {'start': '2029-01-01', 'end': '2029-12-31'})
        self.assertEqual(q('Which patients had 3 or more ED visits in the last 12 months?')['min_visits'], 3)
        self.assertEqual(q('How many treat-and-release ED visits in 2029?')['admitted'], 'exclude')
        self.assertEqual(q('How many ER visits by month in 2029?')['output'], 'by_month')
        self.assertEqual(q('ED visits per 1,000 members last year')['output'], 'summary')
        for s in ('How many ED visits for chest pain in 2029?', 'What share of ED visits were avoidable?',
                  'How many ED visits were followed by a PCP visit?', 'How many diabetic patients had an ED visit?'):
            self.assertIsNone(q(s), s)

    def test_registered_on_the_planner_menu(self):
        from clinical_core.agere.clinical import analysis_graph, capability_registry, graph_planner, graph_contracts, economical_planner
        self.assertIn('ed_visits', analysis_graph.OPERATIONS)
        self.assertIn('ed_visits', capability_registry.PRODUCT)
        self.assertIn('ed_visits', graph_planner.CONTRACTS)
        self.assertIn('ed_visits', graph_contracts.TYPED)
        graph, recipe = economical_planner.local_plan('How many ED visits were there in 2029?', '2030-05-27')
        self.assertEqual((recipe, graph['nodes'][0]['operation']), ('exact_foundation_ed_visits', 'ed_visits'))


if __name__ == '__main__':
    unittest.main()
