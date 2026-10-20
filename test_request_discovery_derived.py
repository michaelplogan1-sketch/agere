"""Derived-relation value discovery fixtures; no provider, hidden references or generator truth."""
import sqlite3
import unittest
from unittest.mock import patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import request_discovery as D, request_plan as R


class DerivedValueDiscovery(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)
        D._VALUE_CACHE.clear()
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript("""
                CREATE TABLE extracted_observation(record_id TEXT, patient_id TEXT, fact_type TEXT, reading REAL);
                INSERT INTO extracted_observation VALUES ('O1','A','ejection_fraction',30),('O2','B','walk_distance',200);
                CREATE TABLE care_roster(clinician_id TEXT, specialty TEXT, member_name TEXT, panel_size INTEGER);
                INSERT INTO care_roster VALUES ('DR1','Cardiology','Ada Quill',300),('DR2','Family Medicine','Bo Wren',900);
                CREATE TABLE remit_line(record_id TEXT, patient_id TEXT, adjustment_category TEXT, free_text TEXT);
                INSERT INTO remit_line VALUES ('R1','A','contractual_obligation','see attached'),('R2','B','patient_responsibility',NULL);
                CREATE TABLE lab_code(code TEXT);
            """)
            db.executemany('INSERT INTO lab_code VALUES (?)', [(f'zeta{i:03d}',) for i in range(D.VALUE_LIMIT + 6)])
        self.catalog = R.Catalog.load(self.path)

    def names(self, found):
        return [c['name'] for c in found['candidates']]

    def test_meaning_carried_in_values_is_discoverable_across_domains(self):
        cases = [(['ejection fraction'], 'extracted_observation', 'fact_type', 'ejection_fraction'),
                 (['cardiology'], 'care_roster', 'specialty', 'Cardiology'),
                 (['contractual obligation'], 'remit_line', 'adjustment_category', 'contractual_obligation')]
        for terms, table, column, value in cases:
            found = D.search(self.catalog, terms)
            hit = next(c for c in found['candidates'] if c['name'] == table)
            self.assertEqual(hit['matched_values'], {column: [value]})
            self.assertIn(column, hit['fields'])
            self.assertEqual(self.names(found)[0], table)
            self.assertFalse(found['data_absence_established'])
            self.assertTrue(found['relation_value_scan']['complete'])

    def test_related_word_forms_are_candidates_not_equivalence(self):
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("INSERT INTO extracted_observation VALUES ('O3','C','fracture_follow_up',NULL)")
        self.catalog = R.Catalog.load(self.path)
        D._VALUE_CACHE.clear()
        self.assertEqual(next(c for c in D.search(self.catalog, ['ejection fraction'])['candidates']
                              if c['name'] == 'extracted_observation')['matched_values'],
                         {'fact_type': ['ejection_fraction']})  # 'fracture' is not a value hit for 'fraction'
        found = D.search(self.catalog, ['cardiologist'])
        hit = next(c for c in found['candidates'] if c['name'] == 'care_roster')
        self.assertEqual(hit['matched_values'], {'specialty': ['Cardiology']})
        self.assertEqual(hit['matched_terms'], [])
        self.assertEqual(hit['approximate_terms'], ['cardiologist'])

    def test_identifiers_free_text_numbers_and_high_cardinality_are_not_value_indexed(self):
        scan = D.relation_values(self.catalog)
        fields = scan['fields']
        for name in ('care_roster.clinician_id', 'care_roster.member_name', 'remit_line.free_text',
                     'care_roster.panel_size', 'extracted_observation.reading', 'remit_line.patient_id'):
            self.assertNotIn(name, fields)
        self.assertIn('lab_code.code', scan['high_cardinality'])
        self.assertNotIn('lab_code.code', fields)
        self.assertNotIn('care_roster', self.names(D.search(self.catalog, ['quill'])))
        self.assertNotIn('lab_code', self.names(D.search(self.catalog, ['zeta001'])))
        self.assertEqual(D.search(self.catalog, ['zeta001'])['relation_value_scan']['high_cardinality_fields'],
                         len(scan['high_cardinality']))

    def test_values_are_bound_to_source_identity(self):
        first = D.relation_values(self.catalog)
        self.assertIs(D.relation_values(self.catalog), first)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("INSERT INTO care_roster VALUES ('DR3','Nephrology','Cy Fenn',100)")
            db.execute("UPDATE meta SET value='independent-shared-2' WHERE key='source_digest'")
        with self.assertRaises(R.PlanError):
            D.relation_values(self.catalog)  # stale catalog never reads the changed warehouse
        current = R.Catalog.load(self.path)
        refreshed = D.relation_values(current)
        self.assertIsNot(refreshed, first)
        self.assertIn('Nephrology', refreshed['fields']['care_roster.specialty'])
        self.assertNotEqual(refreshed['source_identity_digest'], first['source_identity_digest'])

    def test_timeouts_report_incomplete_scope_without_absence(self):
        budget = D.relation_values(self.catalog, total_seconds=-1)
        self.assertEqual(budget['fields'], {})
        self.assertIn('care_roster.specialty', budget['incomplete'])
        self.assertEqual(D._VALUE_CACHE, {})  # incomplete scans are not cached as the identity's scope
        with patch.object(D, 'relation_values', return_value=budget):
            found = D.search(self.catalog, ['cardiology'])
        self.assertFalse(found['relation_value_scan']['complete'])
        self.assertIn('care_roster.specialty', found['relation_value_scan']['incomplete_fields'])
        self.assertFalse(found['data_absence_established'])
        self.assertNotIn('care_roster', self.names(found))
        view = D.prompt_view([{'catalog_search': found}])[0]['catalog_search']
        self.assertFalse(view['relation_value_scan']['complete'])
        self.assertTrue(D.search(self.catalog, ['cardiology'])['relation_value_scan']['complete'])

if __name__ == '__main__':
    unittest.main()
