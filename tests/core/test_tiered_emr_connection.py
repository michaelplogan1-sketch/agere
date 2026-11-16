"""Agere's EMR connection end to end against the local stand-in: SMART Backend Services authorization, search, Bulk export,
import, and a live single-patient answer."""
import json
import os
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from clinical_core.agere.tiered import fhir_client, fhir_live, fhir_server, proof, recipes


@unittest.skipUnless(fhir_server.EXPORT.exists(), 'FHIR export not built (python -m clinical_core.agere.tiered.fhir_synth)')
class EmrConnection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pem, cls.jwk = fhir_client.new_keypair()
        cls.server = fhir_server.Server(clients={'agere-test': cls.jwk}, export_delay=0.5).start()
        cls.conn = fhir_client.Connection(cls.server.base, 'agere-test', cls.pem, cls.jwk['kid'])

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_data_needs_a_token(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.server.base + '/Patient/P-1001')
        self.assertEqual(caught.exception.code, 401)

    def test_a_client_signing_with_another_key_is_refused(self):
        with self.assertRaises(PermissionError):
            fhir_client.token(fhir_client.Connection(self.server.base, 'agere-test', fhir_client.new_keypair()[0]))
        with self.assertRaises(PermissionError):
            fhir_client.token(fhir_client.Connection(self.server.base, 'someone-else', self.pem))

    def test_search_follows_pages(self):
        got = fhir_client.search(self.conn, 'Observation', patient='P-1001', _count=2)
        every = [r for r in self.server.store.by_patient['P-1001']['Observation']]
        self.assertEqual(len(got), len(every))

    def test_bulk_export_then_import_gives_the_same_measures(self):
        with tempfile.TemporaryDirectory() as d:
            db = fhir_client.sync(self.conn, Path(d) / 'export')
            old = os.environ.get('AGERE_TIERED_DB'); os.environ['AGERE_TIERED_DB'] = str(db)
            try:
                self.assertEqual(recipes.execute('harm_opioid_adverse_events', {'year': '2028'})[2][0][1], proof.ref_fh_orae_events(None, '2028')[0][0])
            finally:
                if old is None: os.environ.pop('AGERE_TIERED_DB', None)
                else: os.environ['AGERE_TIERED_DB'] = old
            manifest = json.loads((Path(d) / 'export' / 'manifest.json').read_text())
            self.assertTrue(all(not o['url'].startswith('http') for o in manifest['output']))

    def test_live_single_patient_answer(self):
        o = fhir_live.answer(self.conn, 'WHP-90A1F7047375', 'How many inpatient stays were discharged in 2029, counting each stay once?', tiers=('recipes',))
        self.assertEqual(o['status'], 'answered')
        self.assertEqual(o['rows'][0][1], 7)
        self.assertEqual(o['read']['Patient'], 1)


if __name__ == '__main__':
    unittest.main()
