"""Public onboarding fixtures (DE-SynPUF foreign claims layout, Eunomia GiBleed OMOP) and the whole-dollar money check."""
import tempfile
import unittest
import zipfile
from pathlib import Path

from clinical_core.agere.onboarding import public_fixtures as P

class WholeDollarMoney(unittest.TestCase):
    def test_declared_conversion_wins_for_whole_dollar_columns(self):
        from clinical_core.agere.onboarding import verify
        whole = ['4000', '5100', '2600']
        self.assertFalse(verify.source_is_dollars(whole, {'transforms': []}))               # nothing declared: read as cents
        self.assertTrue(verify.source_is_dollars(whole, {'transforms': [{'op': 'dollars_to_cents'}]}))
        self.assertTrue(verify.source_is_dollars(['12.50', '3'], {}))                       # decimals reveal dollars


class Download(unittest.TestCase):
    def test_pinned_sources_and_bounds(self):
        for name, items in P.SOURCES.items():
            for url, fname, sha in items:
                self.assertTrue(url.startswith('https://')); self.assertEqual(len(sha), 64); self.assertTrue(fname.endswith('.zip'))

    def test_extract_refuses_unsafe_members(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); folder = d / 'eunomia'; folder.mkdir()
            url, fname, sha = P.SOURCES['eunomia'][0]
            z = folder / fname
            with zipfile.ZipFile(z, 'w') as zf:
                zf.writestr('../escape.csv', 'a'); zf.writestr('ok/person.csv', 'person_id\n1\n')
            old = P.SOURCES['eunomia']
            P.SOURCES['eunomia'] = [(url, fname, P._sha(z))]
            try:
                P.fetch(('eunomia',), d, download=False)
            finally:
                P.SOURCES['eunomia'] = old
            self.assertTrue((folder / 'x' / 'ok' / 'person.csv').exists()); self.assertFalse((d / 'escape.csv').exists())
            self.assertFalse((folder / 'escape.csv').exists())


def _have(name):
    return all((P.DEST / name / fname).exists() for _, fname, _ in P.SOURCES[name])


@unittest.skipUnless(_have('eunomia'), 'Eunomia GiBleed zip not downloaded (onboarding.public_fixtures.fetch)')
class Eunomia(unittest.TestCase):
    def test_gibleed_maps_core_omop_tables(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); info = P.eunomia_sqlite(d / 'e.sqlite')
            self.assertEqual(info['tables']['person'], 2694)
            r = P.onboard('eunomia', d / 'e.sqlite', d / 'w')
            got = {k: v['source_table'] for k, v in r['entities'].items() if v['source_table']}
            for entity, table in (('patient', 'person'), ('visit', 'visit_occurrence'), ('condition', 'condition_occurrence'),
                                  ('measurement', 'measurement'), ('medication', 'drug_exposure')):
                self.assertEqual(got.get(entity), table)
            self.assertEqual(r['built_rows']['patient'], 2694)


@unittest.skipUnless(_have('synpuf'), 'DE-SynPUF sample zips not downloaded (onboarding.public_fixtures.fetch)')
class SynPuf(unittest.TestCase):
    def test_foreign_claims_layout_onboards(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); info = P.synpuf_sqlite(d / 's.sqlite', beneficiaries=300)
            self.assertEqual(info['beneficiaries'], 300)
            r = P.onboard('synpuf', d / 's.sqlite', d / 'w')
            self.assertEqual(r['built_rows']['patient'], 300); self.assertEqual(r['built_rows']['claim_line'], info['inpatient_claims'])
            self.assertTrue(r['invariants_passed'], r['invariants_failed'])


if __name__ == '__main__':
    unittest.main()
