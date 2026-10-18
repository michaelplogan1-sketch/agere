"""Full answer pipeline on customer workspaces (onboarding/workspace_stack.py): stack_for, the pipeline folder, clean
engine declines, Workspace.ask's pipeline choice and its hybrid fallback. Tiny synthetic native workspace, no paid calls."""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.onboarding import fixtures, workspace_stack
from clinical_core.agere.onboarding.workspace import Workspace

AS_OF = '2030-05-27'


def _patient(i):
    return {'patient_id': f'AGP-{i:012x}', 'birth_date': f'19{50 + i % 40:02d}-03-0{1 + i % 8}', 'sex_reported': 'F' if i % 2 else 'M',
            'display_name': f'Synthetic {i}', 'state': 'AR', 'postal_code': '72601', 'race': 'White', 'ethnicity': None,
            'preferred_language': 'English', 'identity_status': 'matched', 'synthetic': True}


def _record(pid, i, stream, day, **fields):
    return {'patient_id': pid, 'source_table': stream, 'source_system': 'epic:EPIC-AR', 'organization_id': f'WHN-PRACTICE-AR-{i % 3}',
            'event_date': day, 'event_time': None, 'event_date_precision': 'day', 'recorded_date': day, 'available_date': day,
            'encounter_id': None, 'supersedes_record_id': None, 'record_id': f'AGR-{stream}-{pid}-{day}',
            'synthetic': True, 'identity_status': 'matched', **fields}


def native_workspace(root, slug='tiny-native', n=30):
    """A ready native workspace: stack/{stack.sqlite3, run.json}, a product semantic layer built from it, analyst docs."""
    from clinical_core.agere.clinical import semantic_layer
    d = Path(root) / slug
    (d / 'stack').mkdir(parents=True)
    (d / 'analyst_docs').mkdir()
    (d / 'analyst_docs' / 'catalog_index.md').write_text('# tiny native workspace\n')
    db = sqlite3.connect(d / 'stack' / 'stack.sqlite3')
    db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY, payload TEXT);'
                     'CREATE TABLE records(id TEXT PRIMARY KEY, patient TEXT, stream TEXT, payload TEXT);'
                     'CREATE INDEX records_patient ON records(patient); CREATE INDEX records_stream ON records(stream);')
    h = hashlib.sha256()
    for i in range(n):
        p = _patient(i)
        db.execute('INSERT INTO patients VALUES (?, ?)', (p['patient_id'], json.dumps(p)))
        recs = [_record(p['patient_id'], i, 'encounters', f'2029-0{1 + i % 9}-1{i % 9}', type='office', status='completed',
                        place_of_service='11', specialty='Family Medicine', diagnoses=[])]
        if i % 3 == 0:
            recs.append(_record(p['patient_id'], i, 'conditions', '2028-02-02', description='Type 2 diabetes mellitus',
                                icd10='E11.9', icd10_code_system='ICD-10-CM', clinical_status='active', problem_type='problem_list'))
            recs.append(_record(p['patient_id'], i, 'labs', '2029-06-01', name='HEMOGLOBIN A1C', test='HEMOGLOBIN A1C',
                                loinc='4548-4', value=7 + i % 4, unit='%', result_status='final'))
        for r in recs:
            h.update(json.dumps(r, sort_keys=True).encode())
            db.execute('INSERT INTO records VALUES (?, ?, ?, ?)', (r['record_id'], r['patient_id'], r['source_table'], json.dumps(r)))
    db.commit()
    db.close()
    run = {'schema': 'agere-native-stack/1', 'synthetic': True, 'source_digest': h.hexdigest(), 'patients': n,
           'snapshot': {'release': 'native:test', 'as_of': AS_OF, 'source_dataset': {'adapter': 'test'}}}
    (d / 'stack' / 'run.json').write_text(json.dumps(run))
    semantic_layer.build(d / 'stack')
    import shutil
    shutil.copyfile(d / 'stack' / 'semantic.sqlite3', d / 'semantic.sqlite3')
    s = sqlite3.connect(d / 'semantic.sqlite3')
    s.executemany('INSERT INTO meta VALUES (?, ?)', [('workspace', slug)])
    s.commit()
    s.close()
    state = {'schema': 'agere-workspace/1', 'slug': slug, 'name': slug, 'source': 'native:///nonexistent/raw', 'as_of': AS_OF,
             'raw': 'all', 'status': 'ready', 'steps': {}, 'decisions': {}, 'created_at': '2030-05-27T00:00:00Z', 'error': None}
    (d / 'workspace.json').write_text(json.dumps(state))
    return Workspace(slug, root)


def _fingerprints(d):
    return {str(p.relative_to(d)): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(Path(d).rglob('*')) if p.is_file() and not {'pipeline', 'receipts'} & set(p.relative_to(d).parts) and p.name != 'questions.jsonl'}


def count_reply(as_of, sql='SELECT count(DISTINCT patient_id) FROM patient'):
    text = json.dumps({'contract': {'answer_type': 'count', 'grain': {'entity': 'person', 'key': 'patient_id'},
                                    'population': 'all patients', 'numerator': 'patients', 'denominator': None,
                                    'window': {'start': None, 'end': None, 'anchor': None}, 'as_of': as_of,
                                    'perspective': 'all', 'exclusions': [], 'group_by': [], 'row_limit': None, 'unit': 'count',
                                    'metric_id': None, 'unresolved': []}, 'sql': sql})
    return {'content': [{'type': 'text', 'text': text}], 'usage': {'input_tokens': 1, 'output_tokens': 1}}, 0.0, False


class WorkspaceStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = patch.dict(os.environ, {'AGERE_LOCAL_MODEL': 'off', 'AGERE_BOTTLED': 'off', 'AGERE_SEMANTIC_CACHE': 'off'})
        cls.env.start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / 'workspaces'
        cls.ws = native_workspace(cls.root)
        cls.before = _fingerprints(cls.ws.dir)
        d = Path(cls.tmp.name)
        fixtures.clarity_like(d / 'clarity.sqlite', n_patients=12)
        cls.warehouse = Workspace.create('Acme Warehouse', f"sqlite:///{d / 'clarity.sqlite'}", root=cls.root)
        cls.warehouse.run()

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def test_support_names_the_missing_input(self):
        self.assertEqual(workspace_stack.support(self.ws), (True, None))
        ok, reason = workspace_stack.support(self.warehouse)
        self.assertFalse(ok)
        self.assertIn('stack/stack.sqlite3', reason)
        with self.assertRaises(ValueError) as cm:
            workspace_stack.stack_for(self.warehouse)
        self.assertIn('record store', str(cm.exception))
        self.assertEqual(workspace_stack.prepare(self.warehouse)['supported'], False)

    def test_stack_for_builds_the_pipeline_folder_without_touching_the_workspace(self):
        stack = workspace_stack.stack_for(self.ws, encoder=False)
        out = self.ws.dir / 'pipeline'
        for name in ('run.json', 'stack.sqlite3', 'semantic.sqlite3', 'source-profile.json', 'annotation-link.json',
                     'evidence/evidence.sqlite3', 'operators.json', 'pipeline.json'):
            self.assertTrue((out / name).exists(), name)
        self.assertEqual(stack.path, out.resolve())
        self.assertEqual(stack.retriever.meta['snapshot']['as_of'], AS_OF)
        self.assertFalse(stack.allow_live)
        db = sqlite3.connect(out / 'stack.sqlite3')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'patients', 'records', 'result_cache', 'analyses', 'proposals'} <= tables)
        ev = sqlite3.connect(out / 'evidence' / 'evidence.sqlite3')
        self.assertEqual(ev.execute('SELECT count(*) FROM patients').fetchone()[0], 30)
        self.assertEqual(_fingerprints(self.ws.dir), self.before)  # the workspace's own files are unchanged
        # idempotent: an unchanged workspace reuses the folder and the cached stack
        built = json.loads((out / 'pipeline.json').read_text())['built_at']
        self.assertEqual(workspace_stack.prepare(self.ws)['built_at'], built)
        self.assertIs(workspace_stack.stack_for(self.ws, encoder=False), stack)
        with self.assertRaises(ValueError):
            stack.retriever.search('anything')

    def test_engine_without_its_input_declines_as_unsupported(self):
        stack = workspace_stack.stack_for(self.ws, encoder=False)
        graph = {'interpretation': 'forecast', 'coverage': 'supported_observation', 'unmet_requirements': [],
                 'nodes': [{'id': 'population', 'operation': 'cohort', 'inputs': [],
                            'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}}, 'purpose': 'all'},
                           {'id': 'f', 'operation': 'forecast', 'inputs': ['population'], 'parameters': {'model': 'logistic'},
                            'purpose': 'forecast'}]}
        result = stack.run_graph(graph, question='Forecast next year')
        self.assertEqual(result['graph']['coverage'], 'unsupported')
        self.assertEqual(result['workspace_decline']['operation'], 'forecast')
        self.assertIn('Missing input for forecast', result['workspace_decline']['missing'])
        self.assertEqual(result['answer']['status'], 'unsupported')

    def test_offline_full_pipeline_answers_with_an_engine_and_no_hosted_call(self):
        from clinical_core.agere.clinical import together
        stack = workspace_stack.stack_for(self.ws, encoder=False)
        sent = []
        real = together._run_analyst

        def watch(*a, **k):
            out = real(*a, **k)
            sent.append(out)
            return out
        with patch.object(together, '_run_analyst', watch):
            out = together.compose_together(stack, 'How many patients have diabetes?')
        self.assertEqual(out['new_paid_api_calls'], 0)
        self.assertIn(out['planner_receipt']['route'], ('local_compiler', 'exact_question_cache'))
        self.assertEqual(out['answer']['count'], 10)
        self.assertTrue(all(s['new_paid_api_calls'] == 0 for s in sent))  # the offline sender refused; nothing was sent

    def test_ask_full_pipeline_reconciles_engine_path_and_hybrid_core(self):
        calls = []

        def fake_send(body):
            calls.append(body['model'])
            return count_reply(AS_OF, "SELECT count(DISTINCT patient_id) FROM condition WHERE concept = 'diabetes'")

        @contextmanager
        def factory():
            yield fake_send
        with patch.dict(os.environ, {'AGERE_HOSTED_SQL': 'off', 'AGERE_ROUTER': 'off', 'AGERE_METRIC_ROUTER': 'off'}):
            out = self.ws.ask('How many patients have diabetes?', answer_type='count', send_factory=factory, live=False)
        self.assertEqual(out['pipeline'], 'full')
        self.assertEqual(out['answer'], {'count': 10})
        self.assertIn('local_compiler', out['interpretation'])
        self.assertTrue(calls)  # the hybrid core ran inside together with the workspace's sender
        self.assertEqual(out['decision'], 'agree')  # two independent paths (engine graph, hybrid core) give 10
        last = json.loads((self.ws.dir / 'questions.jsonl').read_text().splitlines()[-1])
        self.assertEqual(last['pipeline'], 'full')

    def test_ask_falls_back_to_the_hybrid_core(self):
        @contextmanager
        def factory():
            yield lambda body: count_reply(self.ws.state['as_of'])
        from clinical_core.agere.clinical import together
        with patch.object(together, 'compose_together', side_effect=RuntimeError('engine exploded')):
            out = self.ws.ask('How many patients do we have?', answer_type='count', send_factory=factory)
            with self.assertRaises(RuntimeError):
                self.ws.ask('How many patients do we have?', answer_type='count', send_factory=factory, pipeline='full')
        self.assertEqual(out['pipeline'], 'hybrid')
        self.assertIn('engine exploded', out['pipeline_fallback']['reason'])
        self.assertEqual(out['answer']['count'], 30)
        # a warehouse workspace answers through the hybrid core; asking it for 'full' names why it cannot
        wh = self.warehouse.ask('How many patients do we have?', answer_type='count', send_factory=factory)
        self.assertEqual(wh['pipeline'], 'hybrid')
        self.assertIn('record store', wh['pipeline_fallback']['reason'])
        with self.assertRaises(ValueError):
            self.warehouse.ask('How many patients do we have?', pipeline='full', send_factory=factory)

    def test_stale_source_rebuilds_the_folder(self):
        workspace_stack.prepare(self.ws)
        out = self.ws.dir / 'pipeline'
        built = json.loads((out / 'pipeline.json').read_text())['built_at']
        p = self.ws.dir / 'stack' / 'run.json'
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        try:
            self.assertTrue(workspace_stack._stale(out, workspace_stack.sources(self.ws)))
            with patch.object(workspace_stack, '_now', return_value='rebuilt'):
                self.assertEqual(workspace_stack.prepare(self.ws)['built_at'], 'rebuilt')
        finally:
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
            workspace_stack.prepare(self.ws)


if __name__ == '__main__':
    unittest.main()
