"""care_gap_closure (DEQM), transitions_of_care (TEAM) and prior_auth_access: fixture semantics, routes, menu registration."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import care_gap_closure as G, transitions_of_care as T, prior_auth_access as PA, engine_suite as E
from clinical_core.agere.clinical.analysis_graph import validate as graph_validate
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS
from clinical_core.agere.clinical import graph_contracts

AS_OF = '2030-05-27'


def build(path):
    db = sqlite3.connect(Path(path)/'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE patient(patient_id TEXT, sex_reported TEXT, birth_date TEXT, age_latest INTEGER, coverage_status TEXT, line_of_business TEXT, pcp_practice_id TEXT);
    CREATE TABLE cancer_screening(record_id TEXT, patient_id TEXT, test TEXT, result_status TEXT, event_date TEXT, result_date TEXT);
    CREATE TABLE immunization(record_id TEXT, patient_id TEXT, vaccine TEXT, status TEXT, event_date TEXT);
    CREATE TABLE wellness_visit(record_id TEXT, patient_id TEXT, awv INTEGER, status TEXT, event_date TEXT);
    CREATE TABLE appointment(patient_id TEXT, scheduled_date TEXT, final_status TEXT, kept INTEGER);
    CREATE TABLE encounter(patient_id TEXT, type_concept TEXT, event_date TEXT);
    CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, payload TEXT);
    CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, organization_id TEXT, readmitted_30d INTEGER,
                      days_to_readmission INTEGER, discharge_known INTEGER);
    CREATE TABLE adt_visit(patient_id TEXT, admit_date TEXT, discharge_disposition TEXT);
    CREATE TABLE payer_pharmacy_claim(record_id TEXT, patient_id TEXT, fill_date TEXT, drug_name TEXT, ndc TEXT, claim_status TEXT, reject_code TEXT,
                                      payer_id TEXT, prescriber_npi TEXT, counts_as_fill INTEGER);
    CREATE TABLE enrollment_span(patient_id TEXT, line_of_business TEXT, dual_status TEXT, coverage_start TEXT, coverage_end TEXT);
    CREATE TABLE therapy_barrier(patient_id TEXT, drug_class TEXT, barrier_type TEXT, negated INTEGER, hypothetical INTEGER);
    ''')
    db.executemany('INSERT INTO patient VALUES(?,?,?,?,?,?,?)', [
        ('F1', 'F', '1970-01-01', 60, 'active', 'medicare', 'PR1'), ('F2', 'F', '1970-01-01', 60, 'active', 'medicare', 'PR1'),
        ('F3', 'F', '1970-01-01', 60, 'active', 'commercial', 'PR2'), ('M1', 'M', '1990-01-01', 40, 'active', 'commercial', 'PR2'),
        ('F4', 'F', '1970-01-01', 60, 'inactive', 'commercial', 'PR2')])
    db.executemany('INSERT INTO cancer_screening VALUES(?,?,?,?,?,?)', [
        ('c1', 'F1', 'bilateral_screening_mammography', 'final', '2029-06-01', '2029-06-01'),
        ('c2', 'F2', 'bilateral_screening_mammography', 'claim_billed', '2029-07-01', None)])
    db.execute("INSERT INTO appointment VALUES('F3','2030-06-10','scheduled',0)")
    db.execute("INSERT INTO immunization VALUES('i1','M1','influenza','completed','2029-10-01')")
    # transitions: D1 fully followed up, D2 nothing, D3 unknown discharge date
    db.executemany('INSERT INTO stay VALUES(?,?,?,?,?,?,?,?)', [
        ('D1', '2030-03-01', '2030-03-05', 'inpatient', 'H1', 0, None, 1), ('D2', '2030-03-10', '2030-03-14', 'inpatient', 'H2', 1, 12, 1),
        ('D3', '2030-03-10', None, 'inpatient', 'H2', 0, None, 0)])
    db.execute("INSERT INTO adt_visit VALUES('D2','2030-03-10','snf')")
    db.execute("INSERT INTO appointment VALUES('D1','2030-03-09','kept',1)")
    db.execute('''INSERT INTO record VALUES('t1','D1','telephone_messages','2030-03-06','{"direction":"outgoing","communication_result":"conversation_completed"}')''')
    db.execute('''INSERT INTO record VALUES('t2','D2','telephone_messages','2030-03-15','{"direction":"outgoing","communication_result":"voicemail"}')''')
    db.execute("INSERT INTO record VALUES('m1','D1','medication_reconciliation','2030-03-07','{}')")
    # prior auth: A resolved in 7 days, B lapsed, C open, N not covered
    c = lambda *a: db.execute('INSERT INTO payer_pharmacy_claim VALUES(?,?,?,?,?,?,?,?,?,?)', a)
    c('r1', 'F1', '2030-01-01', 'drugA', 'n1', 'rejected', '75', 'PAY1', '111', 1); c('p1', 'F1', '2030-01-08', 'drugA', 'n1', 'paid', None, 'PAY1', '111', 1)
    c('r2', 'F2', '2029-12-01', 'drugB', 'n2', 'rejected', '75', 'PAY1', '222', 1)
    c('r3', 'F3', '2030-05-10', 'drugC', 'n3', 'rejected', '75', 'PAY2', '333', 1)
    c('r4', 'F1', '2030-02-01', 'drugD', 'n4', 'rejected', '70', 'PAY1', '111', 1)
    c('r5', 'F3', '2030-02-01', 'drugE', 'n5', 'rejected', '79', 'PAY2', '333', 1)  # refill too soon is not an access case
    db.executemany('INSERT INTO enrollment_span VALUES(?,?,?,?,?)', [('F1', 'medicare', None, '2029-01-01', None), ('F2', 'medicare', 'full_benefit', '2029-01-01', None)])
    db.execute("INSERT INTO therapy_barrier VALUES('F1','statin','cost_access',0,0)")
    db.commit(); db.close()


class Families(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); build(cls.tmp.name); cls.path = cls.tmp.name

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_care_gaps_deqm_states_and_claim_only(self):
        r = G.execute(self.path, None, {'measures': ['bcs'], 'focus': 'all'}, AS_OF)
        st = {x['patient_id']: x for x in r['table']}
        self.assertEqual(set(st), {'F1', 'F2', 'F3'})  # F4 inactive, M1 male: not applicable
        self.assertEqual(st['F1']['gap_status'], 'closed-gap'); self.assertEqual(st['F1']['evidence'], 'result')
        self.assertEqual(st['F2']['evidence'], 'claim'); self.assertEqual(st['F2']['gap_status'], 'closed-gap')
        self.assertEqual(st['F3']['gap_status'], 'open-gap')
        self.assertIn('2030-06-10', st['F3']['next_action']['step']); self.assertEqual(st['F3']['next_action']['status'], 'proposed')
        strict = G.execute(self.path, None, {'measures': ['bcs'], 'closure_evidence': 'result_only'}, AS_OF)
        self.assertEqual({x['patient_id'] for x in strict['table']}, {'F2', 'F3'})
        self.assertEqual(strict['summary'][0]['claim_only_held_open'], 1)

    def test_missing_table_is_partial_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            db = sqlite3.connect(Path(d)/'semantic.sqlite3'); db.executescript('CREATE TABLE patient(patient_id TEXT, sex_reported TEXT, birth_date TEXT, age_latest INTEGER, coverage_status TEXT, line_of_business TEXT, pcp_practice_id TEXT)'); db.close()
            r = G.execute(d, None, {'measures': ['flu']}, AS_OF)
            self.assertEqual(r['unavailable_measures'], ['flu'])
            self.assertEqual(G.answer(r, {'coverage': 'supported_observation'}, 'q')['status'], 'partial')
            self.assertIn('absent', T.headline(T.execute(d, None, {}, AS_OF)))
            self.assertEqual(PA.execute(d, None, {}, AS_OF)['unavailable'], ['payer_pharmacy_claim'])

    def test_transitions(self):
        r = T.execute(self.path, None, {'focus': 'all'}, AS_OF)
        by = {x['patient_id']: x for x in r['table']}
        self.assertEqual(set(by), {'D1', 'D2'}); self.assertEqual(r['unknown_discharge_stays'], 1)
        self.assertTrue(by['D1']['contact_within_4d'] and by['D1']['visit_within_7d'] and by['D1']['med_rec_within_30d'] and not by['D1']['gap'])
        self.assertFalse(by['D2']['contact_within_4d'])  # a voicemail is not a reached contact
        self.assertEqual(len(by['D2']['next_actions']), 4)  # call, visit, med rec, post-acute handoff
        self.assertEqual(T.execute(self.path, None, {'focus': 'readmitted'}, AS_OF)['patient_ids'], ['D2'])

    def test_prior_auth_outcomes(self):
        r = PA.execute(self.path, None, {'focus': 'prior_auth'}, AS_OF)
        s = {c['patient_id']: c for c in r['table']}
        self.assertEqual((s['F1']['state'], s['F1']['days_to_resolution']), ('resolved_paid', 7)); self.assertNotIn('next_action', s['F1'])
        self.assertEqual(s['F2']['state'], 'lapsed'); self.assertIn('appeal', s['F2']['next_action']['step'])
        self.assertEqual(s['F3']['state'], 'open')
        self.assertEqual(r['cases'], 3)  # reject 79 refill-too-soon is not an access case
        self.assertEqual([c['patient_id'] for c in PA.execute(self.path, None, {'focus': 'not_covered'}, AS_OF)['table']], ['F1'])

    def test_extra_help_screen_skips_duals(self):
        r = PA.execute(self.path, None, {'focus': 'extra_help'}, AS_OF)
        self.assertEqual([c['patient_id'] for c in r['table']], ['F1'])  # F2 is a full-benefit dual
        self.assertIn('cost barrier', r['table'][0]['basis'])

    def test_work_queue_ranks_lapsed_first(self):
        r = PA.execute(self.path, None, {'focus': 'prior_auth'}, AS_OF)
        self.assertEqual([x['patient_id'] for x in r['work_queue']['next']], ['F2', 'F3'])  # lapsed appeal outranks the open PA; resolved rests nowhere
        self.assertEqual(r['work_queue']['resting'], 2)

    def test_validate_rejects_unknown(self):
        for m in (G, T, PA):
            with self.assertRaises(ValueError): m.validate({'bogus': 1})

    def test_routes(self):
        cases = {'care_gap_closure': ['Which members have an open breast cancer screening gap?', 'care gap closure rate by practice'],
                 'transitions_of_care': ['Which discharges did not get a follow-up visit within 7 days?', 'how many discharged members had a medication reconciliation'],
                 'prior_auth_access': ['Which prior authorization requests are still unresolved?', 'which Medicare members should we screen for Extra Help']}
        for op, qs in cases.items():
            for q in qs:
                g, _ = E.compile_route(q, {}, AS_OF)
                self.assertEqual(g['nodes'][0]['operation'], op, q)
                graph_validate(g, AS_OF)
        for q in ('Which patients with food insecurity have never been referred to a community resource?', 'what is a prior authorization',
                  'what drives readmission after discharge', 'how many patients have diabetes'):
            out = E.compile_route(q, {}, AS_OF)
            self.assertTrue(out is None or out[0]['nodes'][0]['operation'] not in cases, q)

    def test_every_suite_engine_is_on_the_menu(self):
        for op in ('care_gap_closure', 'transitions_of_care', 'prior_auth_access'):
            self.assertIn(op, PRODUCT, op); self.assertIn(op, CONTRACTS, op); self.assertIn(op, graph_contracts.TYPED, op)

    def test_templates_route_to_their_engine(self):
        for m in (G, T, PA):
            hits = sum(1 for q in m.TEMPLATES if (E.compile_route(q, {}, AS_OF) or [{'nodes': [{}]}])[0]['nodes'][0].get('operation') == m.OP)
            self.assertGreaterEqual(hits, len(m.TEMPLATES) - 2, m.OP)


if __name__ == '__main__':
    unittest.main()
