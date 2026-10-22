"""CDI opportunity engine: EHR indicators -> uncoded CC/MCC, coded vs expected MS-DRG tier, dollar impact, route."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import cdi_opportunity as C, engine_suite

AS_OF = '2030-05-27'


def build(path):
    db = sqlite3.connect(Path(path) / 'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, product TEXT, admission_date TEXT, discharge_date TEXT, service_from TEXT,
      service_to TEXT, drg TEXT, principal_diagnosis TEXT, diagnosis_codes TEXT, billing_npi TEXT, allowed_cents INTEGER, paid_cents INTEGER,
      record_id TEXT, counts_in_metrics INTEGER);
    CREATE TABLE payer_claim_diagnosis(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, rank INTEGER, icd10 TEXT);
    CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL, excluded_reason TEXT, result_current INTEGER);
    CREATE TABLE condition(record_id TEXT, patient_id TEXT, concept TEXT, icd10 TEXT, onset TEXT, event_date TEXT, diagnosis_date TEXT);
    CREATE TABLE note_fact(patient_id TEXT, record_id TEXT, fact_type TEXT, value TEXT, negated INTEGER, event_date TEXT);
    ''')
    claims = [  # pneumonia family: 193 MCC / 194 CC / 195 without
        ('C1', 'P1', 'medicare_ffs', '2030-01-10', '2030-01-14', '2030-01-10', '2030-01-14', '195', 'J18.9', '["J18.9","I50.9","I10"]', 'N1', 600000, 500000, 'R-C1', 1),
        ('C2', 'P2', 'medicare_ffs', '2030-02-01', '2030-02-03', '2030-02-01', '2030-02-03', '690', 'N39.0', '["N39.0"]', 'N1', 700000, 600000, 'R-C2', 1),
        ('C3', 'P3', 'medicare_ffs', '2030-03-01', '2030-03-03', '2030-03-01', '2030-03-03', '195', 'J18.9', '["J18.9","E87.1"]', 'N1', 700000, 600000, 'R-C3', 1),
        ('C4', 'P4', 'medicare_ffs', '2030-03-05', '2030-03-07', '2030-03-05', '2030-03-07', '195', 'J18.9', '["J18.9"]', 'N1', 800000, 600000, 'R-C4', 1),
        ('OLD', 'P1', 'medicare_ffs', '2020-01-01', '2020-01-02', '2020-01-01', '2020-01-02', '293', 'I50.9', '[]', 'N1', 1, 1, 'R-OLD', 1)]
    for i in range(3):  # observed 193 claims give the dollar step from data
        claims.append((f'H{i}', f'PX{i}', 'medicare_ffs', '2029-06-01', '2029-06-05', '2029-06-01', '2029-06-05', '193', 'J18.9', '[]', 'N1', 1000000, 900000, f'R-H{i}', 1))
    for i in range(2):
        claims.append((f'L{i}', f'PY{i}', 'medicare_ffs', '2029-06-01', '2029-06-05', '2029-06-01', '2029-06-05', '195', 'J18.9', '[]', 'N1', 600000, 500000, f'R-L{i}', 1))
    db.executemany('INSERT INTO payer_claim VALUES(' + ','.join('?' * 15) + ')', claims)
    db.executemany('INSERT INTO measurement VALUES(?,?,?,?,?,?,?)', [
        ('M1', 'P1', 'nt_probnp', '2030-01-11', 4200, None, 1), ('M2', 'P1', 'sodium', '2030-01-11', 128, None, 1),
        ('M3', 'P1', 'creatinine', '2030-01-10', 1.0, None, 1), ('M4', 'P1', 'creatinine', '2030-01-11', 1.4, None, 1),
        ('M5', 'P2', 'spo2', '2030-02-01', 86, None, 1), ('M6', 'P2', 'heart_rate', '2030-02-01', 112, None, 1), ('M7', 'P2', 'wbc', '2030-02-01', 15.2, None, 1),
        ('M8', 'P3', 'sodium', '2030-03-01', 127, None, 1),          # hyponatremia already coded on C3
        ('M9', 'P4', 'sodium', '2030-03-05', 129, 'implausible', 1),  # excluded result is not evidence
        ('M10', 'P4', 'weight', '2030-01-20', 80, None, 1), ('M11', 'P4', 'weight', '2030-03-06', 72, None, 1)])  # 10% loss in 45 days -> E43
    db.executemany('INSERT INTO condition VALUES(?,?,?,?,?,?,?)', [('K1', 'P1', 'heart_failure_reduced_ef', 'I50.22', '2025-01-01', '2025-01-01', None)])
    db.commit(); db.close()


class CDI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        build(self.tmp.name)
        self.r = C.execute(self.tmp.name, None, {}, AS_OF)
        self.by = {x['claim_id']: x for x in self.r['rows']}

    def test_hf_stay(self):
        x = self.by['C1']
        rules = {o['rule']: o for o in x['opportunities']}
        self.assertEqual(set(rules), {'acute_heart_failure', 'hyponatremia', 'aki'})
        self.assertEqual(rules['acute_heart_failure']['icd10'], 'I50.23')  # reduced EF condition -> systolic
        self.assertEqual((x['expected_tier'], x['expected_drg'], x['tier_change']), ('MCC', '193', True))
        self.assertAlmostEqual(x['estimated_impact'], 10000 - 33000 / 5, places=1)  # observed 193 mean minus observed 195 mean (C1, C3, C4, L0, L1)
        self.assertIn('M1', x['record_ids'])

    def test_uti_sirs_and_step_fallback(self):
        x = self.by['C2']
        self.assertEqual({o['icd10'] for o in x['opportunities']}, {'A41.9'})  # SpO2 rule needs a respiratory/HF principal
        self.assertEqual(x['expected_drg'], '689')
        self.assertIn('step ratio', x['impact_basis'])  # 689 not observed -> stated MCC step on DRG 690 mean
        self.assertAlmostEqual(x['estimated_impact'], 7000 * 0.60, places=1)

    def test_coded_and_excluded_not_flagged(self):
        self.assertNotIn('C3', self.by)
        x = self.by['C4']
        self.assertEqual([o['icd10'] for o in x['opportunities']], ['E43'])
        self.assertNotIn('OLD', self.by)

    def test_answer_and_filters(self):
        a = C.answer(self.r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(a['status'], 'answered'); self.assertIn('CDI review', a['value'])
        self.assertTrue(any('query prompts' in l for l in a['limitations']))
        r = C.execute(self.tmp.name, None, {'rules': ['sepsis']}, AS_OF)
        self.assertEqual([x['claim_id'] for x in r['rows']], ['C2'])

    def test_validate(self):
        for bad in ({'rules': ['x']}, {'drgs': ['12a']}, {'top': 0}, {'bogus': 1}, {'tier_step': {'XX': 1}}):
            with self.assertRaises(ValueError): C.validate(bad)

    def test_route(self):
        g, _ = engine_suite.compile_route('Which heart failure admissions are missing an MCC we could capture?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'cdi_opportunity')
        self.assertEqual(g['nodes'][0]['parameters'], {'rules': ['acute_heart_failure']})
        g, _ = engine_suite.compile_route('Show CDI opportunities from the last 12 months', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters'], {'period_months': 12})
        self.assertIsNone(C.route('What is CDI?', {}, AS_OF))
        self.assertIsNone(C.route('How many heart failure admissions did we have?', {}, AS_OF))


if __name__ == '__main__':
    unittest.main()
