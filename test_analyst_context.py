"""Native documentation discovery, exact preservation and read-only boundaries."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import analyst_context as C, semantic_analyst as S


DOCS = ('OWNER DEFAULTS: all requested patients; unknown is not no.\n'
        '# Semantic model documentation\n'
        '## Tables\n'
        '### patient(patient_id, birth_date)\nOne row per person; dates are received facts.\n'
        '### pharmacy(patient_id, drug_name, amount_cents)\n'
        'Amounts are cents; O\'Brien and naïve Unicode stay exact.\n'
        'GOVERNED SQL MACROS:\nCare roles are dated; a referral is not current attendance.\n')


class NativeDocumentation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'source.sqlite3'
        db = sqlite3.connect(self.path)
        db.execute('CREATE TABLE meta(key,value)')
        db.execute("INSERT INTO meta VALUES ('as_of','2030-05-27')")
        db.execute('CREATE TABLE patient(patient_id,birth_date)')
        db.execute("INSERT INTO patient VALUES ('A','1960-01-01')")
        db.commit(); db.close()
        self.original = self.path.read_bytes()
        self.wh = S.SemanticWarehouse(self.path)

    def tearDown(self):
        self.wh.close(); self.temp.cleanup()

    def test_every_section_is_available_without_source_or_documentation_changes(self):
        resident = C.install(self.wh, DOCS)
        prefix, expected = C.partition(DOCS)
        rows = self.wh.query(f'SELECT * FROM {C.VIEW}', max_rows=None, max_chars=None)['rows']
        self.assertEqual(rows, expected)
        self.assertEqual(prefix + ''.join(r['documentation_text'] for r in rows), DOCS)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertIn('OWNER DEFAULTS: all requested patients; unknown is not no.', resident)
        self.assertIn('### pharmacy', resident)
        self.assertNotIn('Amounts are cents', resident)
        self.assertNotIn('patient(patient_id, birth_date)', resident)

    def test_lookup_returns_complete_meaning_units_and_literal_quoting(self):
        C.install(self.wh, DOCS)
        _, expected = C.partition(DOCS)
        target = next(r for r in expected if r['title'].startswith('### pharmacy'))
        rows = self.wh.query(f'SELECT * FROM {C.VIEW} WHERE section_id=:as_of',
                             as_of=target['section_id'])['rows']
        self.assertEqual(rows, [target])
        self.assertIn("O'Brien", rows[0]['documentation_text'])
        self.assertIn('naïve', rows[0]['documentation_text'])

    def test_native_lookup_has_no_document_or_patient_limit(self):
        docs = '# Semantic model documentation\n' + ''.join(
            f'### table_{i}(patient_id, value)\n' + 'complete native documentation. ' * 1000 + '\n'
            for i in range(12))
        C.install(self.wh, docs)
        rows = self.wh.query(f'SELECT * FROM {C.VIEW}', max_rows=None, max_chars=None)['rows']
        self.assertEqual(''.join(r['documentation_text'] for r in rows), docs)
        self.assertEqual(len(rows), 13)

    def test_changed_documentation_replaces_the_view_and_changes_identity(self):
        C.install(self.wh, DOCS)
        old = self.wh._local.documentation_identity
        changed = DOCS.replace('Amounts are cents', 'Amounts are dollars')
        C.install(self.wh, changed)
        self.assertNotEqual(self.wh._local.documentation_identity, old)
        rows = self.wh.query(f'SELECT * FROM {C.VIEW}', max_rows=None, max_chars=None)['rows']
        self.assertEqual(''.join(r['documentation_text'] for r in rows), changed[len(C.partition(changed)[0]):])
        self.assertTrue(all(r['sha256'] == hashlib.sha256(r['documentation_text'].encode()).hexdigest() for r in rows))

    def test_documentation_view_does_not_allow_writes_or_arbitrary_temp_objects(self):
        C.install(self.wh, DOCS)
        for statement in (f'DROP VIEW {C.VIEW}', 'DELETE FROM patient',
                          'CREATE TEMP VIEW injected AS SELECT * FROM patient'):
            with self.assertRaises(sqlite3.DatabaseError):
                self.wh.db.execute(statement)
        self.assertEqual(self.wh.query('SELECT patient_id FROM patient')['rows'], [{'patient_id': 'A'}])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_plain_documents_empty_documents_and_repeated_headings_round_trip(self):
        for docs in ('', 'Unstructured complete contract.', '# Same\n# Same\n'):
            prefix, rows = C.partition(docs)
            self.assertEqual(prefix + ''.join(r['documentation_text'] for r in rows), docs)
            self.assertEqual(len({r['section_id'] for r in rows}), len(rows))

    def test_fresh_native_executor_restores_the_same_lookup(self):
        C.install(self.wh, DOCS)
        query = f'SELECT section_id, documentation_text FROM {C.VIEW} WHERE section_id=\'doc_2\''
        expected = self.wh.query(query)
        fresh = S.SemanticWarehouse(self.path)
        try:
            C.install(fresh, DOCS)
            self.assertEqual(fresh.query(query)['rows'], expected['rows'])
            self.assertEqual(fresh._local.documentation_identity, self.wh._local.documentation_identity)
        finally:
            fresh.close()


if __name__ == '__main__':
    unittest.main()
