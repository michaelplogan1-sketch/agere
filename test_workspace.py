"""Customer workspace lifecycle on synthetic Epic-Clarity-like and claims-lake fixtures (no paid calls)."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from clinical_core.agere.onboarding import fixtures, fixtures_claims_lake
from clinical_core.agere.onboarding.workspace import Workspace, list_workspaces


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        fixtures.clarity_like(d / 'clarity.sqlite', n_patients=20)
        fixtures_claims_lake.claims_lake(d / 'lake.sqlite')
        cls.root = d / 'workspaces'
        cls.ws = Workspace.create('Acme Health', f"sqlite:///{d / 'clarity.sqlite'}", root=cls.root)
        cls.summary = cls.ws.run()
        cls.lake = Workspace.create('Lake Payer', f"sqlite:///{d / 'lake.sqlite'}", raw='unmapped', root=cls.root)
        cls.lake.run()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_day_one_ready_with_semantic_layer_raw_tables_and_docs(self):
        self.assertEqual(self.summary['status'], 'ready')
        db = sqlite3.connect(self.ws.semantic_path)
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('patient', names)
        self.assertTrue(any(n.startswith('raw_') for n in names))
        meta = dict(db.execute('SELECT key, value FROM meta'))
        self.assertTrue(meta.get('as_of'))
        self.assertEqual(meta.get('workspace'), 'acme-health')
        self.assertTrue((self.ws.docs_dir / 'catalog_index.md').is_file())
        index = (self.ws.docs_dir / 'catalog_index.md').read_text()
        self.assertIn('raw_', index)

    def test_every_step_recorded_and_listed(self):
        self.assertEqual(set(self.ws.state['steps']), {'profile', 'propose', 'approve', 'build', 'raw', 'verify', 'docs'})
        self.assertIn('acme-health', [w['slug'] for w in list_workspaces(self.root)])

    def test_unmapped_mode_copies_only_unused_tables(self):
        mapping = json.loads((self.lake.dir / 'mapping.json').read_text())
        expected = {'raw_' + t.lower() for t in mapping['unmapped_source_tables']}
        self.assertEqual(set(self.lake.state['steps']['raw']['tables']), expected)

    def test_review_decision_persists_through_rebuild(self):
        items = self.ws.review_items()
        field_items = [i for i in items if i['kind'] == 'field' and i.get('current')]
        if not field_items:
            self.skipTest('fixture produced no open field questions')
        item = field_items[0]
        self.ws.decide(item['id'], 'accept')
        self.ws.rebuild()
        mapping = json.loads((self.ws.dir / 'mapping.json').read_text())
        entity, field = item['id'].split('.')
        self.assertTrue(mapping['entities'][entity]['fields'][field]['accept'])
        self.assertTrue(mapping['approval']['approved'])
        self.assertNotIn(item['id'], [i['id'] for i in self.ws.review_items() if i['decided'] is None])

    def test_bad_decision_is_refused(self):
        with self.assertRaises(ValueError):
            self.ws.decide('patient.birth_date', {'drop': 'table'})

    def test_ask_runs_the_hybrid_core_against_the_workspace(self):
        calls = []

        def fake_send(body):  # one analyst run: count patients
            calls.append(body['model'])
            text = json.dumps({'contract': {'answer_type': 'count', 'grain': {'entity': 'person', 'key': 'patient_id'},
                                            'population': 'all patients', 'numerator': 'patients', 'denominator': None,
                                            'window': {'start': None, 'end': None, 'anchor': None},
                                            'as_of': self.ws.state['as_of'], 'perspective': 'all', 'exclusions': [],
                                            'group_by': [], 'row_limit': None, 'unit': 'count', 'metric_id': None,
                                            'unresolved': []},
                               'sql': 'SELECT count(DISTINCT patient_id) FROM patient'})
            return {'content': [{'type': 'text', 'text': text}], 'usage': {'input_tokens': 1, 'output_tokens': 1}}, 0.0, False

        @contextmanager
        def factory():
            yield fake_send

        out = self.ws.ask('How many patients do we have?', answer_type='count', send_factory=factory)
        self.assertEqual(out['decision'], 'unanimous')
        n = sqlite3.connect(self.ws.semantic_path).execute('SELECT count(DISTINCT patient_id) FROM patient').fetchone()[0]
        self.assertEqual(out['answer']['count'], n)
        self.assertTrue((self.ws.dir / 'questions.jsonl').is_file())


if __name__ == '__main__':
    unittest.main()
