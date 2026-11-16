"""Domain packs: industry-neutral core, healthcare unchanged, logistics example pack answered through the planner menu."""
import os
import tempfile
import unittest
from unittest import mock

from clinical_core.agere import packs
from clinical_core.agere.packs import logistics_sample
from clinical_core.agere.clinical import domain_pack as DP, engine_suite as E
from clinical_core.agere.clinical.analysis_graph import validate as graph_validate

AS_OF = '2030-05-27'


class Packs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); logistics_sample.build(cls.tmp.name); cls.path = cls.tmp.name

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_packs_validate_and_core_has_no_industry_words(self):
        self.assertEqual(set(packs.available()), {'healthcare', 'logistics'})
        for p in packs.available(): packs.validate(packs.load(p))
        import inspect
        src = inspect.getsource(DP) + inspect.getsource(packs)
        for word in ('patient', 'icd10', 'medicare', 'claim_status'): self.assertNotIn(word, src.lower().replace("'patient_ids'", '').replace('patient_ids', ''), word)

    def test_bad_pack_is_refused(self):
        bad = {**packs.load('logistics'), 'measures': [{**packs.load('logistics')['measures'][0], 'expression': 'count(*); drop table shipment'}]}
        with self.assertRaises(packs.PackError): packs.validate(bad)
        bad = {**packs.load('logistics'), 'dimensions': [{'id': 'x', 'table': 'nope', 'column': 'a', 'synonyms': []}]}
        with self.assertRaises(packs.PackError): packs.validate(bad)

    def test_healthcare_default_is_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('AGERE_PACK', None)
            self.assertEqual(packs.active_id(), 'healthcare')
            self.assertIsNone(DP.route('what is our on-time delivery rate by carrier', {}, AS_OF))  # never claims under healthcare
            g, _ = E.compile_route('Which patients with food insecurity have never been referred to a community resource?', {}, AS_OF)
            self.assertEqual(g['nodes'][0]['operation'], 'social_needs')

    def test_logistics_routes_and_answers_through_the_menu(self):
        with mock.patch.dict(os.environ, {'AGERE_PACK': 'logistics'}):
            g, _ = E.compile_route('What is our on-time delivery rate by carrier?', {}, AS_OF)
            self.assertEqual(g['nodes'][0]['operation'], 'domain_pack'); graph_validate(g, AS_OF)
            self.assertEqual(g['nodes'][0]['parameters'], {'measure': 'on_time_rate', 'statistic': 'share_of_denominator', 'group_by': 'carrier'})
            r = DP.execute(self.path, None, g['nodes'][0]['parameters'], AS_OF)
            by = {x['group']: x['value'] for x in r['table']}
            self.assertGreater(by['Atlas Freight'], by['Cobalt Rail'])  # the generator's carrier reliability ordering is recovered
            a = DP.answer(r, g, 'q'); self.assertEqual(a['status'], 'answered'); self.assertIn('late_days = 0', a['definitions'][0]['basis'])
            for q in DP.TEMPLATES: self.assertEqual(E.compile_route(q, {}, AS_OF)[0]['nodes'][0]['operation'], 'domain_pack', q)
            from clinical_core.agere.clinical import literal_check
            for q in DP.TEMPLATES: self.assertEqual(literal_check.missing(q, literal_check.executable_text(E.compile_route(q, {}, AS_OF)[0])), [], q)
            for q, m in (('How many shipments are late by lane?', 'late_shipments'), ('how many shipments are still in transit', 'in_transit')):
                self.assertEqual(E.compile_route(q, {}, AS_OF)[0]['nodes'][0]['parameters']['measure'], m, q)
            tot = DP.execute(self.path, None, {'measure': 'shipments'}, AS_OF)
            self.assertEqual(tot['table'][0]['value'], 3000)

    def test_validate_is_closed(self):
        with mock.patch.dict(os.environ, {'AGERE_PACK': 'logistics'}):
            for bad in ({'measure': 'patients'}, {'measure': 'shipments', 'group_by': 'zip'}, {'measure': 'shipments', 'sql': 'x'}):
                with self.assertRaises(ValueError): DP.validate(bad)

    def test_status(self):
        self.assertTrue(packs.status()['core_is_industry_neutral'])


if __name__ == '__main__':
    unittest.main()
