"""Scorecard risk engine: fixed bins, WoE/IV, monotone points, PDO / integer scaling, out-of-time validation, patient lines, route."""
import json
import math
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import scorecard_risk as S, engine_suite

AS_OF = '2030-05-27'


def build(path, n=400, seed=3):
    rng = random.Random(seed)
    db = sqlite3.connect(Path(path) / 'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE patient(patient_id TEXT, birth_date TEXT, sex_reported TEXT, line_of_business TEXT);
    CREATE TABLE enrollment_span(patient_id TEXT, dual_status TEXT);
    CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, readmitted_30d INTEGER);
    CREATE TABLE encounter(patient_id TEXT, event_date TEXT, type TEXT);
    CREATE TABLE adt_visit(patient_id TEXT, admit_date TEXT, patient_class TEXT);
    CREATE TABLE condition(patient_id TEXT, icd10 TEXT, icd10_category TEXT, chronic INTEGER, available_date TEXT, recorded_date TEXT, event_date TEXT);
    CREATE TABLE fill(patient_id TEXT, fill_date TEXT, drug_class TEXT, drug_name TEXT);
    CREATE TABLE social_need(patient_id TEXT, event_date TEXT, positive INTEGER);
    CREATE TABLE death(patient_id TEXT, death_date TEXT);
    ''')
    for i in range(n):
        pid = f'P-{1000 + i}'
        hf = i % 4 == 0
        old = i % 3 == 0
        db.execute('INSERT INTO patient VALUES(?,?,?,?)', (pid, '1945-03-01' if old else '1985-03-01', 'F' if i % 2 else 'M', 'medicare' if old else 'commercial'))
        for y in (2027, 2028, 2029, 2030):
            db.execute('INSERT INTO encounter VALUES(?,?,?)', (pid, f'{y}-01-10', 'primary_care'))
        if hf: db.execute("INSERT INTO condition VALUES(?,?,?,?,?,?,?)", (pid, 'I50.9', 'I50', 1, '2026-06-01', '2026-06-01', '2026-06-01'))
        # admissions: heart failure drives risk in each follow-up year
        for y in (2028, 2029):
            if rng.random() < (0.45 if hf else 0.05) + (0.1 if old else 0):
                db.execute("INSERT INTO stay VALUES(?,?,?,?,?)", (pid, f'{y}-09-01', f'{y}-09-04', 'inpatient', int(rng.random() < 0.3)))
        if i % 10 == 0: db.execute("INSERT INTO stay VALUES(?,?,?,?,?)", (pid, '2030-03-01', '2030-03-03', 'observation', 0))
    db.execute("INSERT INTO condition VALUES('P-1001','I50.1','I50',1,'2031-01-01','2031-01-01','2026-01-01')")  # known after the as-of date: excluded
    db.execute("INSERT INTO death VALUES('P-1003','2027-01-01')")  # died before every index: never sampled
    db.commit(); db.close()


class Scorecard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        build(self.tmp.name)

    def run_(self, **p):
        return S.execute(self.tmp.name, None, {'outcome': 'admission', **p}, AS_OF)

    def test_points_and_validation(self):
        r = self.run_()
        self.assertEqual(r['status'], 'answered')
        hf = {x['bin']: x for x in r['scorecard'] if x['feature'] == 'condition_heart_failure'}
        self.assertEqual(hf['no']['points'], 0)            # reference bin
        self.assertGreater(hf['yes']['points'], 20)        # strong risk factor
        self.assertGreater(hf['yes']['woe'], 0)
        self.assertGreater(r['information_value']['condition_heart_failure'], 0.3)
        self.assertGreater(r['validation']['validation_auroc'], 0.7)
        self.assertNotIn('P-1003', [x['patient_id'] for x in r['patients']])
        self.assertEqual(r['train_n'], 399)
        top = r['patients'][0]
        self.assertEqual(top['score'], r['base_points'] + sum(x['points'] for x in top['lines']))
        # PDO: score 100 at base odds 1:19, +20 points doubles the odds
        self.assertAlmostEqual(S.predicted(100, *S.points_for(0.0, {}, S.validate({}))[2:]), 0.05, places=6)
        self.assertAlmostEqual(S.predicted(120, *S.points_for(0.0, {}, S.validate({}))[2:]) / (1 - S.predicted(120, *S.points_for(0.0, {}, S.validate({}))[2:])),
                               2 / 19, places=6)

    def test_feature_timing(self):
        data = S.Data(sqlite3.connect(Path(self.tmp.name) / 'semantic.sqlite3'), None)
        from datetime import date
        f, _ = data.features('P-1001', date(2030, 5, 27))
        self.assertEqual(f['condition_heart_failure'], 'no')   # its I50 record became available only in 2031
        f, _ = data.features('P-1000', date(2030, 5, 27))
        self.assertEqual(f['condition_heart_failure'], 'yes')

    def test_integer_scale_and_patient(self):
        r = self.run_(scale='integer', patient='P-1000')
        self.assertEqual(r['base_points'], 0)
        e = r['explained'][0]
        ans = S.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(ans['patient_ids'], ['P-1000'])
        self.assertEqual(ans['table'][-1]['points'], e['score'])
        self.assertIn('P-1000 scores', ans['value'])

    def test_pav(self):
        self.assertEqual(S.pav([0, 2, 1, 3], [1, 1, 1, 1]), [0, 1.5, 1.5, 3])
        self.assertEqual(S.pav([0, -2, -1], [1, 1, 1], increasing=False), [0, -1.5, -1.5])

    def test_too_few_events(self):
        r = S.execute(self.tmp.name, ['P-1001', 'P-1002'], {'outcome': 'death'}, AS_OF)
        self.assertEqual(r['status'], 'partial')
        self.assertIn('Too few training events', r['assumptions'][0])

    def test_validate(self):
        for bad in ({'outcome': 'x'}, {'pdo': 0}, {'features': ['bogus']}, {'bogus': 1}, {'horizon_months': 99}):
            with self.assertRaises(ValueError): S.validate(bad)

    def test_route(self):
        g, _ = engine_suite.compile_route('Build an auditable points-based risk model for 12-month hospital admission', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'scorecard_risk')
        self.assertEqual(g['nodes'][0]['parameters'], {'outcome': 'admission', 'horizon_months': 12})
        g, _ = engine_suite.compile_route('Which patients have the highest ED visit risk on the scorecard?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters'], {'outcome': 'ed_visit', 'view': 'patients'})
        g, _ = engine_suite.compile_route("Show the scorecard points behind WHP-20BC580B73BB's readmission risk", {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters'], {'outcome': 'readmission', 'patient': 'WHP-20BC580B73BB'})
        for t in S.TEMPLATES:
            g, _ = engine_suite.compile_route(t, {}, AS_OF)
            self.assertEqual(g['nodes'][0]['operation'], 'scorecard_risk', t)
            S.validate(g['nodes'][0]['parameters'])
        self.assertIsNone(S.route('What is our Star rating scorecard?', {}, AS_OF))
        self.assertIsNone(S.route('Which patients are at highest risk of admission?', {}, AS_OF))
        self.assertIsNone(S.route('Show the HEDIS quality scorecard', {}, AS_OF))


if __name__ == '__main__':
    unittest.main()
