"""Contract reimbursement: observed leave-one-out rates, supplied terms, short-pay flags, repricing, route."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import contract_reimbursement as C, engine_suite

AS_OF = '2030-05-27'


def build(path):
    db = sqlite3.connect(Path(path) / 'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, payer_id TEXT, product TEXT, service_from TEXT, counts_in_metrics INTEGER);
    CREATE TABLE payer_claim_line(payer_claim_id TEXT, cpt TEXT, hcpcs TEXT, units INTEGER, allowed_cents INTEGER, counts_in_metrics INTEGER);''')
    claims, lines, k = [], [], 0
    for payer, product, cents_list in (('PAY-1', 'commercial', [10000] * 9 + [6000]), ('MCR', 'medicare_ffs', [10000] * 6)):
        for cents in cents_list:
            k += 1
            claims.append((f'C{k}', 'P1', payer, product, '2030-03-01', 1)); lines.append((f'C{k}', '99213', None, 1, cents, 1))
    db.executemany('INSERT INTO payer_claim VALUES(?,?,?,?,?,?)', claims)
    db.executemany('INSERT INTO payer_claim_line VALUES(?,?,?,?,?,?)', lines)
    db.commit(); db.close()


class Contract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); build(self.tmp.name)

    def test_observed_short_pay(self):
        r = C.execute(self.tmp.name, None, {'payers': ['PAY-1']}, AS_OF)
        self.assertEqual(r['totals']['short_paid'], 40.0)
        self.assertEqual([(x['direction'], x['variance']) for x in r['rows']], [('short_paid', -40.0)])
        a = C.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertIn('short-paid $40', a['value']); self.assertTrue(a['limitations'])

    def test_supplied_terms_override(self):
        r = C.execute(self.tmp.name, None, {'payers': ['PAY-1'], 'terms': {'PAY-1': {'rates': {'99213': 120.0}}}}, AS_OF)
        self.assertEqual(r['totals']['expected'], 1200.0)
        self.assertEqual(len(r['rows']), 10)  # every line is 17-50% under the supplied $120
        r = C.execute(self.tmp.name, None, {'payers': ['PAY-1'], 'terms': {'*': {'pct_of_medicare': 1.0}}}, AS_OF)
        self.assertEqual(r['basis_lines'], {'supplied percent of Medicare': 10})

    def test_reprice(self):
        r = C.execute(self.tmp.name, None, {'view': 'reprice', 'reprice': {'PAY-1': 1.2}, 'payers': ['PAY-1']}, AS_OF)
        self.assertEqual(r['reprice'][0]['repriced'], 1200.0)
        self.assertEqual(r['reprice'][0]['delta'], 240.0)

    def test_validate_and_route(self):
        for bad in ({'view': 'x'}, {'tolerance': 0.9}, {'terms': {'A': {'bogus': 1}}}, {'zzz': 1}, {'reprice': {'A': 0}}):
            with self.assertRaises(ValueError): C.validate(bad)
        g, _ = engine_suite.compile_route('Which payers are underpaying us versus our contracted rates?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'contract_reimbursement')
        p = C.route('What if we renegotiate WH-PAYER-C1 to 130% of Medicare?', {}, AS_OF)[0]['nodes'][0]['parameters']
        self.assertEqual((p['view'], p['reprice']), ('reprice', {'WH-PAYER-C1': 1.3}))
        self.assertIsNone(C.route('What is a contract?', {}, AS_OF))
        self.assertIsNone(C.route('benchmark our negotiated rates against competitors', {}, AS_OF))


if __name__ == '__main__':
    unittest.main()
