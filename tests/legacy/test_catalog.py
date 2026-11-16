import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.hybrid import catalog


def make_db(path):
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE meta(key TEXT, value TEXT);
    CREATE TABLE onboarding_lineage(entity TEXT, target_field TEXT, source_table TEXT, source_column TEXT, transforms TEXT, confidence REAL);
    CREATE TABLE patient(patient_id TEXT, sex_reported TEXT, birth_date TEXT);
    CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, icd10 TEXT, event_date TEXT);
    CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, value REAL, unit TEXT, event_date TEXT, loinc TEXT);
    CREATE TABLE empty_t(a TEXT);
    CREATE TABLE raw_lab_extra(lab_id TEXT, flag TEXT);
    """)
    for i in range(40):
        db.execute('INSERT INTO patient VALUES (?,?,?)', (f'P{i}', 'F' if i % 2 else 'M', f'19{50 + i % 40}-01-15'))
        db.execute('INSERT INTO condition VALUES (?,?,?,?,?)', (f'c{i}', f'P{i}', 'conditions', ['E11.9', 'I10', 'J45.909'][i % 3], f'2025-0{1 + i % 9}-10'))
        db.execute('INSERT INTO measurement VALUES (?,?,?,?,?,?,?)', (f'm{i}', f'P{i}', 'hba1c', 5.0 + i / 10, '%', f'2025-03-{1 + i % 28:02d}', '4548-4'))
        db.execute('INSERT INTO raw_lab_extra VALUES (?,?)', (f'L{i}', 'HIGH' if i % 4 else 'LOW'))
    db.execute('INSERT INTO onboarding_lineage VALUES (?,?,?,?,?,?)', ('patient', 'sex_reported', 'src_pat', 'gender', json.dumps(['upper']), 0.97))
    db.execute('INSERT INTO meta VALUES (?,?)', ('receipt', json.dumps({'version': 'v1', 'source': 'synthetic', 'rows': {'patient': 40}})))
    db.commit()
    db.close()


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sem = self.root / 'semantic.sqlite3'
        make_db(self.sem)
        self.out = self.root / 'analyst_docs'
        self.res = catalog.build_docs(self.sem, self.out, extra_sections={'Local Notes': 'Fiscal year starts July.'})

    def tearDown(self):
        self.tmp.cleanup()

    def test_files_and_result(self):
        for name in ('catalog_index.md', 'table_patient.md', 'table_condition.md', 'table_measurement.md', 'table_raw_lab_extra.md',
                     'section_codes_and_values.md', 'section_lineage.md', 'section_local_notes.md'):
            self.assertTrue((self.out / name).is_file(), name)
        self.assertFalse((self.out / 'table_empty_t.md').exists())
        self.assertEqual(self.res['tables']['patient'], 40)
        self.assertNotIn('empty_t', self.res['tables'])
        self.assertEqual(self.res['files'], len(list(self.out.glob('*.md'))))
        self.assertGreater(self.res['chars'], 1000)

    def test_index_structure(self):
        idx = (self.out / 'catalog_index.md').read_text()
        self.assertIn('## Tables: name(columns).', idx)
        self.assertIn('- patient(patient_id, sex_reported, birth_date)', idx)
        modeled, raw = idx.split('## Raw source tables (not modeled; query directly when the modeled tables lack a field)')
        self.assertNotIn('raw_lab_extra', modeled)
        self.assertIn('- raw_lab_extra(lab_id, flag)', raw)
        self.assertNotIn('empty_t', idx)
        self.assertIn('section_local_notes.md', idx)
        self.assertIn('section_lineage.md', idx)
        self.assertIn('Canonical conventions', idx)

    def test_profiling_values_and_codes(self):
        cond = (self.out / 'table_condition.md').read_text()
        self.assertIn('### condition(record_id, patient_id, source_table, icd10, event_date)', cond)
        self.assertIn('E11.9 (14)', cond)
        self.assertIn('ICD-10-CM', cond)
        self.assertIn('range 2025-01-10 to', cond)
        vals = (self.out / 'section_codes_and_values.md').read_text()
        self.assertIn('patient.sex_reported: F (20), M (20)', vals)
        self.assertIn('raw_lab_extra.flag: HIGH (30), LOW (10)', vals)
        self.assertNotIn('patient.patient_id', vals)
        self.assertIn('LOINC', (self.out / 'table_measurement.md').read_text())

    def test_lineage(self):
        lin = (self.out / 'section_lineage.md').read_text()
        self.assertIn('patient.sex_reported <- src_pat.gender via upper', lin)
        self.assertIn('version: v1', lin)

    def test_sampling_bound(self):
        old = catalog.SAMPLE_ROWS
        catalog.SAMPLE_ROWS = 10
        try:
            catalog.build_docs(self.sem, self.root / 'small')
        finally:
            catalog.SAMPLE_ROWS = old
        self.assertIn('use the first 10 rows', (self.root / 'small' / 'table_patient.md').read_text())

    def test_docs_dir_for(self):
        other = self.root / 'ws2'
        other.mkdir()
        self.assertEqual(catalog.docs_dir_for(other / 'semantic.sqlite3'), catalog.PACKAGED_DOCS)
        self.assertEqual(catalog.docs_dir_for(self.sem), self.out)
        (other / 'analyst_docs').mkdir()
        self.assertEqual(catalog.docs_dir_for(other / 'semantic.sqlite3'), catalog.PACKAGED_DOCS)


if __name__ == '__main__':
    unittest.main()
