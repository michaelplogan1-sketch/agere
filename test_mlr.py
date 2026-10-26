"""MLR engine: incurred claims over net premium by product line, thresholds, credibility, rebate, break-even premium, route."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import mlr, engine_suite

AS_OF = '2030-05-27'


def build(path):
    db = sqlite3.connect(Path(path)/'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE meta(key, value); INSERT INTO meta VALUES('as_of','2030-05-27');
    CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, payload TEXT, available_date TEXT);
    CREATE TABLE member_month(patient_id TEXT, month TEXT, product TEXT, line_of_business TEXT);
    CREATE TABLE payer_claim(patient_id TEXT, service_from TEXT, allowed_cents INTEGER, paid_cents INTEGER, last_available_date TEXT,
      final_status TEXT, counts_in_metrics INTEGER);
    CREATE TABLE payer_pharmacy_claim(patient_id TEXT, fill_date TEXT, plan_paid_cents INTEGER, patient_pay_cents INTEGER, available_date TEXT,
      claim_status TEXT, counts_as_fill INTEGER);
    ''')
    rows = []
    for m in range(1, 13):
        month = f'2029-{m:02d}'
        rows += [('C1', month, 'commercial', 'commercial'), ('C2', month, 'commercial', 'commercial'), ('M1', month, 'medicare_advantage', 'medicare')]
    db.executemany('INSERT INTO member_month VALUES(?,?,?,?)', rows)
    claims = []
    for m in range(1, 13):
        d = f'2029-{m:02d}-15'
        claims += [('C1', d, 40000, 30000, d, 'paid', 1), ('M1', d, 150000, 120000, d, 'paid', 1)]
    claims += [('C2', '2029-03-01', 99999, 99999, '2029-03-02', 'denied', 1),       # Mike 2026-10-25: the received paid amount on a counted claim contributes, irrespective of final status
               ('C2', '2029-03-01', 50000, 50000, '2029-03-02', 'paid', 0),         # superseded version: not counted
               ('C1', '2028-06-01', 70000, 70000, '2028-06-02', 'paid', 1)]         # outside the year
    db.executemany('INSERT INTO payer_claim VALUES(?,?,?,?,?,?,?)', claims)
    db.executemany('INSERT INTO payer_pharmacy_claim VALUES(?,?,?,?,?,?,?)', [
        ('C2', '2029-05-05', 12000, 1000, '2029-05-06', 'paid', 1), ('C2', '2029-05-06', 50000, 0, '2029-05-06', 'paid', 0)])
    # Mike 2026-10-25: payer pharmacy spend nets all transactions; the replaced payment has a matching reversal.
    db.executescript("CREATE TABLE pharmacy_claim_event(patient_id TEXT, event_date TEXT, payer_paid_delta_cents INTEGER); "
                     "INSERT INTO pharmacy_claim_event VALUES ('C2','2029-05-05',12000),('C2','2029-05-06',50000),('C2','2029-05-06',-50000);")
    db.commit(); db.close()


class MLR(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        build(self.tmp.name)

    def run_(self, **p):
        return mlr.execute(self.tmp.name, None, {'year': 2029, 'completion': False, **p}, AS_OF)

    def test_break_even_without_premium(self):
        r = self.run_()
        com = next(x for x in r['rows'] if x['product'] == 'commercial')
        # Mike 2026-10-25: all counted payer paid amounts count, including the received $999.99 on the denied counted row.
        self.assertEqual(com['member_months'], 24)
        self.assertAlmostEqual(com['claims_pmpm'], round((3720 + 999.99) / 24, 2))
        self.assertAlmostEqual(com['break_even_premium_pmpm'], round((3720 + 999.99) / 24 / 0.85, 2))
        self.assertIsNone(com['mlr'])
        self.assertTrue(any('Premium revenue is not received' in a for a in r['assumptions']))
        ma = next(x for x in r['rows'] if x['product'] == 'medicare_advantage')
        self.assertEqual((ma['claims_pmpm'], ma['threshold']), (1200.0, 0.85))

    def test_mlr_rebate_and_non_credible(self):
        r = self.run_(premium_pmpm={'commercial': 250, 'medicare_advantage': 1500}, qi_expense_pmpm=5)
        com = next(x for x in r['rows'] if x['product'] == 'commercial')
        self.assertAlmostEqual(com['mlr'], round(((3720 + 999.99) / 24 + 5) / 250, 4))
        self.assertFalse(com['credible'])  # 2 life-years < 1,000: deemed to meet, no rebate
        self.assertEqual(com['rebate_or_remittance'], 0.0)
        ma = next(x for x in r['rows'] if x['product'] == 'medicare_advantage')
        self.assertAlmostEqual(ma['mlr'], round(1205 / 1500, 4))
        self.assertAlmostEqual(ma['rebate_or_remittance'], round((0.85 - 1205 / 1500) * 1500 * 12, 2), places=0)
        self.assertEqual(ma['payee'], 'remittance to CMS')
        ans = mlr.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(ans['status'], 'answered'); self.assertIn('MLR', ans['value'])

    def test_small_group_threshold_and_taxes(self):
        r = self.run_(premium_pmpm={'all': 200}, commercial_market='small_group', taxes_fees_pct=0.1, products=['commercial'])
        com = r['rows'][0]
        self.assertEqual(com['threshold'], 0.80)
        self.assertAlmostEqual(com['mlr'], round((3720 + 999.99) / 24 / 180, 4))

    def test_credibility_table(self):
        self.assertIsNone(mlr.credibility_adjustment(999))
        self.assertEqual(mlr.credibility_adjustment(1000), 0.083)
        self.assertAlmostEqual(mlr.credibility_adjustment(1750), 0.0675)
        self.assertEqual(mlr.credibility_adjustment(80000), 0.0)

    def test_validate(self):
        for bad in ({'years': 2}, {'products': ['x']}, {'premium_pmpm': {'commercial': -1}}, {'bogus': 1}, {'commercial_market': 'mid'}):
            with self.assertRaises(ValueError): mlr.validate(bad)
        self.assertEqual(mlr.validate({'premium_pmpm': 400})['premium_pmpm'], {'all': 400.0})

    def test_route(self):
        g, info = engine_suite.compile_route('Will the Medicare Advantage book owe an MLR remittance if premium is $1,150 PMPM?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'mlr')
        self.assertEqual(g['nodes'][0]['parameters'], {'products': ['medicare_advantage'], 'premium_pmpm': {'all': 1150.0}})
        mlr.validate(g['nodes'][0]['parameters'])
        self.assertIsNone(mlr.route('What is MLR?', {}, AS_OF))
        self.assertIsNone(mlr.route('How is the medical loss ratio calculated?', {}, AS_OF))
        self.assertIsNone(mlr.route('What is the MSR under MSSP?', {}, AS_OF))


if __name__ == '__main__':
    unittest.main()
