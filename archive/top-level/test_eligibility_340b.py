"""340B eligibility gates on a tiny synthetic semantic layer."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import eligibility_340b as E, engine_suite

AS_OF = '2030-05-27'


def build(path):
    db = sqlite3.connect(Path(path) / 'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE organization(organization_id TEXT, npi TEXT, display_name TEXT, organization_type TEXT);
    CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT);
    CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, setting TEXT, type TEXT, event_date TEXT, organization_id TEXT);
    CREATE TABLE prescription(record_id TEXT, patient_id TEXT, organization_id TEXT, encounter_id TEXT, ordered_date TEXT, drug_name TEXT, rxcui TEXT);
    CREATE TABLE prescription_fill(order_record_id TEXT, fill_record_id TEXT);
    CREATE TABLE payer_pharmacy_claim(record_id TEXT, fill_date TEXT, claim_status TEXT, plan_paid_cents INTEGER, patient_pay_cents INTEGER, payer_id TEXT, pharmacy_name TEXT, counts_as_fill INTEGER);
    CREATE TABLE payer_claim(payer_id TEXT, line_of_business TEXT, product TEXT);
    CREATE TABLE patient(patient_id TEXT, line_of_business TEXT, coverage_type TEXT);
    ''')
    db.executemany('INSERT INTO organization VALUES(?,?,?,?)', [('H1', '1', 'Hosp', 'hospital'), ('PC', '2', 'Clinic', 'primary_care_practice')])
    db.execute("INSERT INTO stay VALUES('P4','2030-03-01','2030-03-10','inpatient')")
    db.executemany('INSERT INTO visit VALUES(?,?,?,?,?,?)', [('E1', 'P1', 'other', 'clinic', '2030-02-01', 'H1'), ('E2', 'P2', 'other', 'clinic', '2030-02-01', 'H1'),
                                                           ('E3', 'P3', 'other', 'clinic', '2030-02-01', 'H1'), ('E4', 'P4', 'other', 'clinic', '2030-02-20', 'H1'),
                                                           ('E5', 'P5', 'other', 'clinic', '2030-02-01', 'PC')])
    rx = [('R1', 'P1', 'H1', 'E1', '2030-03-01'), ('R2', 'P2', 'H1', 'E2', '2030-03-01'), ('R3', 'P3', 'H1', 'E3', '2030-03-01'),
          ('R4', 'P4', 'H1', 'E4', '2030-03-05'), ('R5', 'P5', 'PC', 'E5', '2030-03-01'), ('R6', 'P6', 'H1', 'E6', '2030-03-01')]
    db.executemany("INSERT INTO prescription VALUES(?,?,?,?,?,'drugx','1')", rx)
    db.executemany('INSERT INTO prescription_fill VALUES(?,?)', [('R1', 'F1'), ('R3', 'F3'), ('R4', 'F4'), ('R5', 'F5'), ('R2', 'F2'), ('R6', 'F6')])
    fills = [('F1', '2030-03-02', 'paid', 10000, 500, 'PAYC', 'rx'), ('F2', '2030-03-02', 'rejected', 10000, 0, 'PAYC', 'rx'), ('F3', '2030-03-02', 'paid', 4000, 0, 'PAYM', 'rx'),
             ('F4', '2030-03-06', 'paid', 4000, 0, 'PAYC', 'rx'), ('F5', '2030-03-02', 'paid', 4000, 0, 'PAYC', 'rx'), ('F6', '2030-03-02', 'paid', 2000, 0, 'PAYC', 'rx')]
    db.executemany('INSERT INTO payer_pharmacy_claim VALUES(?,?,?,?,?,?,?,1)', fills)
    db.execute("INSERT INTO payer_claim VALUES('PAYM','medicaid','medicaid')")
    db.commit(); db.close()


class Eligibility(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); build(self.tmp.name)

    def status(self, params):
        r = E.execute(self.tmp.name, None, params, AS_OF)
        return {x['record_id']: x['failed_gate'] for x in r['rows']}, r

    def test_gates(self):
        s, r = self.status({})
        self.assertIsNone(s['R1'])
        self.assertTrue(s['R2'].startswith('3_dispensed'))
        self.assertTrue(s['R3'].startswith('5_medicaid'))
        self.assertTrue(s['R4'].startswith('4_outpatient'))
        self.assertTrue(s['R5'].startswith('1_entity'))
        self.assertTrue(s['R6'].startswith('2_relationship'))
        self.assertEqual((r['eligible_orders'], r['eligible_spend'], r['est_savings']), (1, 105.0, 26.25))

    def test_carve_in_and_entity(self):
        s, r = self.status({'medicaid_carve_in': True})
        self.assertIsNone(s['R3']); self.assertEqual(r['eligible_orders'], 2)
        s, r = self.status({'entity': ['*']})
        self.assertIsNone(s['R5'])
        a = E.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertIn('340B-eligible', a['value']); self.assertTrue(a['limitations'])

    def test_validate_route(self):
        for bad in ({'view': 'x'}, {'discount': 0.99}, {'bogus': 1}, {'lookback_days': -1}):
            with self.assertRaises(ValueError): E.validate(bad)
        g, _ = engine_suite.compile_route('Which patients qualify for 340B at WH-HOSPITAL-0?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'eligibility_340b')
        self.assertEqual(g['nodes'][0]['parameters'], {'view': 'patients', 'entity': ['WH-HOSPITAL-0']})
        self.assertIsNone(E.route('What is 340B?', {}, AS_OF))


if __name__ == '__main__':
    unittest.main()
