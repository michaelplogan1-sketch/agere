"""Semantic layer materialisation and governed read-only SQL."""
import sqlite3, unittest

from clinical_core.agere.clinical import semantic_layer, sql_query
from test_aggregate_explain import build, pcp, visit


class SemanticSQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patients = {'p1': {'age_reported': 70, 'sex_reported': 'F'}, 'p2': {'age_reported': 40, 'sex_reported': 'M'},
                    'p3': {'age_reported': 55, 'sex_reported': 'F'}}
        recs = [pcp('p1', 'drA', 'PR1'), ('p1', 'coverage', {'coverage_type': 'medicare', 'event_date': '2026-01-01'}),
                ('p1', 'conditions', {'description': 'T2DM', 'icd10': 'E11.9', 'event_date': '2027-01-01'}),
                ('p2', 'conditions', {'description': 'Diabetes mellitus type 2', 'snomed': '44054006'}),
                ('p3', 'conditions', {'description': 'Prediabetes'}),
                ('p1', 'labs', {'test': 'HbA1c', 'value': 9.4, 'unit': '%', 'event_date': '2028-01-01'}),
                ('p2', 'labs', {'test': 'Potassium', 'value': 5.8, 'unit': 'mEq/L', 'event_date': '2028-01-01'}),
                ('p2', 'labs', {'test': 'Potassium', 'value': None, 'unit': 'mmol/L', 'result_status': 'cancelled'}),
                ('p3', 'vitals', {'blood_pressure_systolic': 150, 'event_date': '2028-02-01'}),
                visit('p1', 'ED', '2028-03-01'), ('p1', 'adt', {'event': 'emergency_registration', 'event_date': '2028-03-01'}),
                visit('p2', 'inpatient', '2028-01-01', 'H1', '2028-01-03'), visit('p2', 'inpatient', '2028-01-20', 'H1', '2028-01-22'),
                ('p3', 'social_screens', {'food_difficulty_reported': True, 'event_date': '2028-01-01'})]
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)

    def q(self, sql, returns='table', population=None):
        return sql_query.execute(self.path, {'query': sql, 'returns': returns}, population)

    def test_concepts_cover_every_coding_system(self):
        r = self.q("SELECT patient_id FROM condition WHERE concept='diabetes'", 'patient_set')
        self.assertEqual(r['patient_ids'], ['p1', 'p2'])
        self.assertEqual(self.q("SELECT count(*) FROM concept_patient WHERE concept='prediabetes'", 'count')['value'], 1)

    def test_measurements_carry_canonical_units_and_cancelled_values(self):
        rows = self.q("SELECT value, unit, value_canonical FROM measurement WHERE concept='potassium' ORDER BY value_canonical")['rows']
        self.assertEqual([r['value_canonical'] for r in rows], [None, 5.8])
        bp = self.q("SELECT value_canonical FROM measurement WHERE concept='systolic_bp'", 'value')
        self.assertEqual(bp['value'], 150)

    def test_encounters_stays_and_patient_attributes(self):
        self.assertEqual(self.q("SELECT count(DISTINCT patient_id) FROM encounter WHERE type_concept='emergency_visit'", 'count')['value'], 1)
        self.assertEqual(self.q("SELECT sum(readmitted_30d) FROM stay", 'count')['value'], 1)
        row = self.q("SELECT coverage_type, pcp_clinician_id FROM patient WHERE patient_id='p1'")['rows'][0]
        self.assertEqual(row, {'coverage_type': 'medicare', 'pcp_clinician_id': 'drA'})

    def test_raw_records_reachable_with_json_extract(self):
        r = self.q("SELECT patient_id FROM record WHERE stream='social_screens' AND json_extract(payload,'$.food_difficulty_reported')=1", 'patient_set')
        self.assertEqual(r['patient_ids'], ['p3'])

    def test_population_cte(self):
        r = self.q("SELECT count(*) FROM population JOIN patient USING(patient_id) WHERE sex_reported='F'", 'count', ['p1', 'p2'])
        self.assertEqual(r['value'], 1)
        r = self.q("WITH f AS (SELECT patient_id FROM patient WHERE sex_reported='F') SELECT count(*) FROM population JOIN f USING(patient_id)", 'count', ['p1', 'p3'])
        self.assertEqual(r['value'], 2)

    def test_writes_and_escapes_are_refused(self):
        for bad in ("DELETE FROM patient", "SELECT 1; DROP TABLE patient", "PRAGMA table_info(patient)",
                    "WITH x AS (SELECT 1) DELETE FROM patient", "SELECT * FROM sqlite_master; ATTACH 'x' AS y"):
            with self.assertRaises(ValueError): self.q(bad)
        with self.assertRaises(ValueError): self.q("SELECT count(*) FROM population", 'count')  # population without input
        db = sqlite3.connect(self.path / 'semantic.sqlite3')
        self.assertEqual(db.execute('SELECT count(*) FROM patient').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
