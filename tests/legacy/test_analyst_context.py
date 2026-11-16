"""Native documentation discovery, exact preservation and read-only boundaries."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.clinical import analyst_context as C, semantic_analyst as S, execution_state as E, backstop_agent as B


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

    def test_governed_owner_definitions_stay_resident_with_exact_versioned_bytes(self):
        docs = DOCS + 'CLINICAL OWNER DEFINITIONS:\nCurrent means the decided window, not all-history.\n'
        resident = C.install(self.wh, docs)
        _, sections = C.partition(docs)
        for section in sections:
            if section['title'].startswith(('GOVERNED SQL MACROS', 'CLINICAL OWNER DEFINITIONS')):
                self.assertIn(section['documentation_text'], resident)
                self.assertIn(section['sha256'], resident)

    def native_tools(self):
        tools = B.Tools.__new__(B.Tools)
        tools.semantic, tools.as_of, tools.cohort = self.wh, '2030-05-27', None
        tools.seen, tools.rows_by_record, tools.result_sources, tools.executed_results = [], {}, {}, {}
        tools.native_calls, tools.question = [], ''
        return tools

    def test_long_loaded_section_survives_later_clinical_work_once_and_exactly(self):
        text = 'Full contract — O\'Brien units.\n' * 1500
        C.install(self.wh, DOCS + '### long_table(value)\n' + text)
        section = next(r for r in self.wh._local.documentation_sections.values() if r['title'].startswith('### long_table'))
        tools = self.native_tools()
        lookup = {'name': 'query_sql', 'input': {'sql': f"SELECT section_id,title,documentation_text FROM {C.VIEW} WHERE section_id='{section['section_id']}'", 'complete': True}}
        loaded = tools(lookup)
        self.assertEqual(loaded['loaded_documentation']['sections'], [section])
        self.assertTrue(S.actor_result(loaded).get('preview_only'))
        tools(lookup)
        tools({'name': 'query_sql', 'input': {'sql': 'SELECT * FROM patient'}})
        state = json.loads(E.messages('q', tools.native_calls, recent_results=tools.native_calls[-1:],
            documentation_identity=self.wh._local.documentation_identity)[0]['content'])
        self.assertEqual(state['loaded_documentation']['sections'], [section])
        self.assertNotIn('computed_rows', state['executed_results'][0])
        self.assertNotIn('patient_id', state['loaded_documentation']['sections'][0])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_partial_text_forged_hash_and_clinical_lookalikes_are_not_retained(self):
        C.install(self.wh, DOCS)
        for sql in (
            f'SELECT section_id,substr(documentation_text,1,3) AS documentation_text FROM {C.VIEW}',
            f"SELECT section_id,documentation_text,'wrong' AS sha256 FROM {C.VIEW}",
            "SELECT 'doc_0' AS section_id,'# Semantic model documentation' AS documentation_text FROM patient",
            f'SELECT d.section_id,d.documentation_text,p.patient_id FROM {C.VIEW} d JOIN patient p'):
            result = self.wh.query(sql, max_rows=None, max_chars=None)
            self.assertNotIn('loaded_documentation', C.bind_loaded(self.wh, sql, result))

    def test_changed_identity_removes_old_loaded_definitions_from_current_state(self):
        C.install(self.wh, DOCS)
        tools = self.native_tools()
        tools({'name': 'query_sql', 'input': {'sql': f'SELECT * FROM {C.VIEW}'}})
        old_identity = self.wh._local.documentation_identity
        C.install(self.wh, DOCS.replace('Amounts are cents', 'Amounts are dollars'))
        new_identity = self.wh._local.documentation_identity
        self.assertNotEqual(old_identity, new_identity)
        state = json.loads(E.messages('q', tools.native_calls, documentation_identity=new_identity)[0]['content'])
        self.assertNotIn('loaded_documentation', state)
        tools({'name': 'query_sql', 'input': {'sql': f'SELECT * FROM {C.VIEW}'}})
        state = json.loads(E.messages('q', tools.native_calls, documentation_identity=new_identity)[0]['content'])
        self.assertIn('Amounts are dollars', ''.join(r['documentation_text'] for r in state['loaded_documentation']['sections']))
        self.assertNotIn('Amounts are cents', ''.join(r['documentation_text'] for r in state['loaded_documentation']['sections']))

    def test_only_actually_returned_complete_rows_are_loaded(self):
        C.install(self.wh, DOCS)
        sql = f'SELECT * FROM {C.VIEW}'
        out = self.wh.query(sql, max_rows=1, max_chars=None)
        self.assertTrue(out['truncated'])
        bound = C.bind_loaded(self.wh, sql, out)
        self.assertEqual(len(bound['loaded_documentation']['sections']), 1)
        self.assertEqual(bound['rows'], out['rows'])

    def test_compiled_actor_continuation_receives_loaded_contract_after_clinical_query(self):
        C.install(self.wh, DOCS)
        tools = self.native_tools()
        sent = []
        def send(body):
            sent.append(body)
            if len(sent) <= 2:
                sql = f'SELECT * FROM {C.VIEW}' if len(sent) == 1 else 'SELECT * FROM patient'
                return {'content': [{'type': 'tool_use', 'id': str(len(sent)), 'name': 'query_sql', 'input': {'sql': sql}}]}, 0, False
            state = json.loads(body['messages'][0]['content'])
            self.assertEqual(state['loaded_documentation']['identity'], self.wh._local.documentation_identity)
            block=body['system'][-1]
            loaded=json.loads(block['text'].split('\n',1)[1])
            self.assertEqual(loaded['sections'], list(self.wh._local.documentation_sections.values()))
            self.assertEqual(block['cache_control'],{'type':'ephemeral'})
            self.assertEqual(block,sent[1]['system'][-1])
            self.assertTrue(all('documentation_text'not in r for r in state['loaded_documentation']['sections']))
            self.assertEqual(state['latest_results'][0]['result']['rows'], [{'patient_id': 'A', 'birth_date': '1960-01-01'}])
            return {'content': [{'type': 'text', 'text': '{"status":"answered","value":1}'}]}, 0, False
        out = S.tool_loop('q', 'sys', B.TOOLS, tools, send=send, model='scripted', max_tools=None, compact_execution_state=True)
        self.assertIsNone(out.get('error'))
        self.assertEqual(out['answer']['value'], 1)

    def test_conflicting_content_cannot_share_an_installed_identity(self):
        import copy
        C.install(self.wh, DOCS)
        tools = self.native_tools()
        tools({'name': 'query_sql', 'input': {'sql': f'SELECT * FROM {C.VIEW}'}})
        forged = copy.deepcopy(tools.native_calls[0])
        section = forged['result']['loaded_documentation']['sections'][0]
        section['documentation_text'] += 'changed'
        section['sha256'] = hashlib.sha256(section['documentation_text'].encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'Conflicting documentation'):
            E.messages('q', [*tools.native_calls, forged], documentation_identity=self.wh._local.documentation_identity)

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
