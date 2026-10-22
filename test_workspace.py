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
        self.assertEqual(set(self.ws.state['steps']), {'detect', 'profile', 'propose', 'approve', 'build', 'raw', 'verify', 'canonical', 'docs'})
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


class WorkspaceApiTests(unittest.TestCase):
    def test_create_build_review_and_errors_through_the_api(self):
        import time
        from unittest.mock import patch
        from clinical_core.agere import workspace_api
        from clinical_core.agere.onboarding import workspace as wsmod
        with tempfile.TemporaryDirectory() as tmp, patch.object(wsmod, 'WORKSPACES', Path(tmp) / 'ws'):
            fixtures.clarity_like(Path(tmp) / 'c.sqlite', n_patients=10)
            code, w = workspace_api.handle('POST', '/api/workspaces', {'name': 'Beta Clinic', 'source': f"sqlite:///{tmp}/c.sqlite"})
            self.assertEqual(code, 202)
            for _ in range(200):
                if not workspace_api._JOBS['beta-clinic'].is_alive():
                    break
                time.sleep(0.05)
            code, d = workspace_api.handle('GET', '/api/workspaces/beta-clinic')
            self.assertEqual((code, d['status']), (200, 'ready'))
            self.assertEqual(workspace_api.handle('GET', '/api/workspaces/beta-clinic/review')[0], 200)
            self.assertEqual(workspace_api.handle('GET', '/api/workspaces/nope')[0], 404)
            self.assertEqual(workspace_api.handle('POST', '/api/workspaces', {'name': 'x'})[0], 400)
            self.assertEqual(workspace_api.handle('POST', '/api/workspaces/beta-clinic/ask', {})[0], 400)
            self.assertIn('beta-clinic', [x['slug'] for x in workspace_api.handle('GET', '/api/workspaces')[1]['workspaces']])


class BatchTests(unittest.TestCase):
    def test_ask_many_writes_submission_rows(self):
        from clinical_core.agere.onboarding import workspace as wsmod

        class FakeWs:
            dir = Path(tempfile.mkdtemp())

            def ask(self, question, answer_type=None, send_factory=None):
                if 'fail' in question:
                    raise RuntimeError('boom')
                return {'answer': {'count': 7}, 'answer_type': 'count', 'interpretation': 'hybrid unanimous', 'cost_usd': 0.01,
                        'decision': 'unanimous'}
        rows = wsmod.ask_many(FakeWs(), [{'id': 'A', 'question': 'how many'}, {'id': 'B', 'question': 'fail please'}])
        self.assertEqual(rows[0]['count'], 7)
        self.assertEqual(rows[1]['status'], 'abstain')
        self.assertTrue(any(p.name.startswith('answers-') for p in FakeWs.dir.iterdir()))


class CustomerIdTests(unittest.TestCase):
    def test_native_id_preference(self):
        from clinical_core.agere.onboarding.workspace import native_id
        self.assertEqual(native_id(['member:WH-PAYER-1:M1', 'epic:EPIC-CA:Z1']), 'epic:EPIC-CA:Z1')
        self.assertEqual(native_id(['mrn:WHN-PRACTICE-CA-5:ICE-CA-5-1']), 'mrn:ICE-CA-5-1')
        self.assertEqual(native_id(['hl7:HOSPAK0:L-AK-0-9']), 'mrn:L-AK-0-9')
        self.assertEqual(native_id(['mbi:2DX0U77CP79']), 'member:2DX0U77CP79')
        self.assertEqual(native_id(['member:WH-PAYER-MCD2:MCD2X']), 'member:MCD2X')
        self.assertIsNone(native_id([]))

    def test_declines_and_id_rewrite_in_submission_rows(self):
        from clinical_core.agere.onboarding.workspace import _answer_record
        r = _answer_record('Q', {'answer': {'patient_ids': ['AGP-1', 'AGP-2']}}, 'patient_set', {'AGP-1': 'epic:E:Z1'})
        self.assertEqual(r['patient_ids'], ['epic:E:Z1', 'AGP-2'])
        c = _answer_record('Q', {'answer': None, 'decline': {'kind': 'clarify', 'readings': ['a', 'b']}})
        self.assertEqual((c['status'], c['readings']), ('clarify', ['a', 'b']))
        u = _answer_record('Q', {'answer': None, 'decline': {'kind': 'unsupported', 'missing': ['x']}})
        self.assertEqual((u['status'], u['missing']), ('unsupported', ['x']))
