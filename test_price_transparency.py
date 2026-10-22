"""Price transparency benchmark: claims-derived allowed-amount cells, % of Medicare, percentiles, TiC MRF parser, route."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import price_transparency as P, engine_suite

AS_OF = '2030-05-27'


def build(path):
    db = sqlite3.connect(Path(path) / 'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE organization(organization_id TEXT, npi TEXT, tin TEXT, display_name TEXT, organization_type TEXT);
    CREATE TABLE patient(patient_id TEXT, pcp_practice_id TEXT);
    CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, payer_id TEXT, product TEXT, billing_npi TEXT, service_from TEXT, counts_in_metrics INTEGER);
    CREATE TABLE payer_claim_line(payer_claim_id TEXT, cpt TEXT, hcpcs TEXT, units INTEGER, allowed_cents INTEGER, counts_in_metrics INTEGER);
    ''')
    db.executemany('INSERT INTO organization VALUES(?,?,?,?,?)', [('ORG-A', '111', '11-1', 'Alpha Clinic', 'primary_care_practice'),
                                                                  ('ORG-B', '222', '22-2', 'Beta Group', 'specialty_group'),
                                                                  ('ORG-C', '333', '33-3', 'Gamma Health', 'specialty_group')])
    db.execute("INSERT INTO patient VALUES('P1','ORG-A')")
    claims, lines = [], []
    k = 0
    for payer, product, npi, cents in (('PAY-1', 'commercial', '111', 10000), ('PAY-1', 'commercial', '222', 12000), ('PAY-1', 'commercial', '333', 14000),
                                       ('PAY-2', 'commercial', '111', 13000), ('MCR', 'medicare_ffs', '222', 9000)):
        for i in range(5):
            k += 1; cid = f'C{k}'
            claims.append((cid, 'P1', payer, product, npi, '2030-02-01', 1))
            lines.append((cid, '99213', None, 1, cents, 1))
    claims.append(('OLD', 'P1', 'PAY-1', 'commercial', '111', '2010-01-01', 1)); lines.append(('OLD', '99213', None, 1, 999999, 1))
    claims.append(('U2', 'P1', 'PAY-1', 'commercial', '111', '2030-02-01', 1)); lines.append(('U2', '99213', None, 2, 20000, 1))  # 2 units: $100 each
    db.executemany('INSERT INTO payer_claim VALUES(?,?,?,?,?,?,?)', claims)
    db.executemany('INSERT INTO payer_claim_line VALUES(?,?,?,?,?,?)', lines)
    db.commit(); db.close()


class PriceBenchmark(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        build(self.tmp.name)

    def test_cells_medicare_and_percentile(self):
        r = P.execute(self.tmp.name, None, {'codes': ['99213']}, AS_OF)
        c = r['codes'][0]
        self.assertEqual(c['medicare_ffs_median'], 90.0)
        self.assertEqual(c['payers']['PAY-1']['median'], 120.0)
        self.assertAlmostEqual(c['payers']['PAY-1']['pct_of_medicare'], round(120 / 90, 3))
        self.assertEqual(c['market_median'], 115.0)  # median of 100, 120, 140, 130 (Medicare cell is its own payer: 90)
        alpha = next(x for x in r['rows'] if x['payer'] == 'PAY-1' and x['npi'] == '111')
        self.assertEqual((alpha['lines'], alpha['median_allowed'], alpha['percentile_in_payer'], alpha['own']), (6, 100.0, 0.0, True))
        a = P.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertIn('99213 market', a['value']); self.assertEqual(a['status'], 'answered')

    def test_named_provider_and_min_lines(self):
        r = P.execute(self.tmp.name, None, {'codes': ['99213'], 'provider': 'gamma', 'focus': 'providers'}, AS_OF)
        self.assertEqual(r['own_organizations'], ['ORG-C'])
        r = P.execute(self.tmp.name, None, {'codes': ['99213'], 'min_lines': 6}, AS_OF)
        self.assertEqual({x['npi'] for x in r['rows']}, {'111'})

    def test_mrf_parser_and_merge(self):
        doc = {'reporting_entity_name': 'Fictional Health Plan', 'provider_references': [{'provider_group_id': 7, 'provider_groups': [{'npi': [222], 'tin': {'type': 'ein', 'value': '22-2'}}]}],
               'in_network': [{'negotiation_arrangement': 'ffs', 'billing_code_type': 'CPT', 'billing_code': '99213', 'negotiated_rates': [
                   {'provider_groups': [{'npi': [111], 'tin': {'type': 'ein', 'value': '11-1'}}], 'negotiated_prices': [{'negotiated_type': 'negotiated', 'negotiated_rate': 110.0, 'billing_class': 'professional'}]},
                   {'provider_references': [7], 'negotiated_prices': [{'negotiated_type': 'negotiated', 'negotiated_rate': 126.0, 'billing_class': 'professional'}]}]},
                   {'billing_code_type': 'CPT', 'billing_code': '99214', 'negotiated_rates': [{'provider_groups': [{'npi': [333]}], 'negotiated_prices': [{'negotiated_rate': 'x'}]}]}]}
        f = Path(self.tmp.name) / 'plan_in-network.json'; f.write_text(json.dumps(doc))
        rows = P.parse_mrf(f)
        self.assertEqual([(x['code'], x['rate'], x['npis']) for x in rows], [('99213', 110.0, ['111']), ('99213', 126.0, ['222'])])
        r = P.execute(self.tmp.name, None, {'codes': ['99213'], 'mrf_path': str(f)}, AS_OF)
        beta = next(m for m in r['mrf'] if m['rate'] == 126.0)
        self.assertEqual((beta['matched_organizations'], beta['observed_allowed_median']), (['ORG-B'], 120.0))
        self.assertAlmostEqual(beta['published_vs_observed'], 0.05)

    def test_validate(self):
        for bad in ({'codes': ['9921']}, {'focus': 'x'}, {'min_lines': 0}, {'mrf_path': 'a.csv'}, {'bogus': 1}):
            with self.assertRaises(ValueError): P.validate(bad)

    def test_route(self):
        g, _ = engine_suite.compile_route('How do our negotiated rates for 99213 compare across payers?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'price_benchmark')
        self.assertEqual(g['nodes'][0]['parameters'], {'codes': ['99213']})
        self.assertEqual(P.route('What percent of Medicare does each commercial payer pay for office visits?', {}, AS_OF)[0]['nodes'][0]['parameters'],
                         {'products': ['commercial']})
        self.assertIsNone(P.route('What is price transparency?', {}, AS_OF))
        self.assertIsNone(P.route('Which claims were underpaid versus our negotiated rates?', {}, AS_OF))
        self.assertIsNone(P.route('What was our denial rate by payer?', {}, AS_OF))


if __name__ == '__main__':
    unittest.main()
