"""Study harness tests: no model calls, no real workspace, no answer key (a fake reference stands in).

    .venv/bin/python -m unittest bench.study.test_study_harness
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import unittest.mock

from bench.study import analyze, baseline, common, isolation, report, run as study_run, score

FAKE_REF = {'workspace': 'fake', 'questions': {'Q1': {'ids': ['epic:p1', 'epic:p2']}, 'Q2': {'count': 2}, 'Q3': {'ids': ['epic:p3']}}}
QUESTIONS = [{'set': 'locked', 'id': 'Q1', 'question': 'Which patients are flagged? Definition: flag = 1.', 'answer_type': 'patient_set'},
             {'set': 'locked', 'id': 'Q2', 'question': 'How many patients are flagged? Definition: flag = 1.', 'answer_type': 'count'},
             {'set': 'locked', 'id': 'Q3', 'question': 'Which patients have flag 2? Definition: flag = 2.', 'answer_type': 'patient_set'}]


def make_workspace(root):
    """A tiny workspace folder shaped like a native one, with answer caches in pipeline/ that a clone must not carry."""
    ws = Path(root) / 'fake-ws'
    (ws / 'stack').mkdir(parents=True)
    (ws / 'analyst_docs').mkdir()
    (ws / 'pipeline').mkdir()
    (ws / 'receipts').mkdir()
    (ws / 'workspace.json').write_text(json.dumps({'status': 'ready', 'as_of': '2030-05-27', 'source': 'native://x'}))
    db = sqlite3.connect(ws / 'semantic.sqlite3')
    db.executescript("CREATE TABLE patient(patient_id TEXT, flag INT); INSERT INTO patient VALUES ('p1',1),('p2',1),('p3',2),('p4',0);")
    db.commit()
    db.close()
    (ws / 'stack' / 'stack.sqlite3').write_bytes(b'')
    (ws / 'analyst_docs' / 'catalog_index.md').write_text('# Catalog\n\n## Tables: name(columns)\n- patient(patient_id, flag) [4 rows]\n\n## Raw\n')
    (ws / 'analyst_docs' / 'table_patient.md').write_text('patient: one row per patient; flag is 0, 1 or 2.')
    (ws / 'analyst_docs' / 'section_codes_and_values.md').write_text('flag: 0 | 1 | 2')
    for name in ('learning.sqlite3', 'question-plans.sqlite3', 'model-cache.sqlite3'):
        (ws / 'pipeline' / name).write_bytes(b'cache')
    (ws / 'receipts' / 'old.json').write_text('{}')
    (ws / 'questions.jsonl').write_text('{"old": true}\n')
    return ws


class FakeWorkspace:
    def __init__(self, d):
        self.dir = Path(d)
        self.semantic_path = self.dir / 'semantic.sqlite3'
        self.docs_dir = self.dir / 'analyst_docs'
        self.state = json.loads((self.dir / 'workspace.json').read_text())

    def customer_ids(self, ids):
        return {i: f'epic:{i}' for i in ids}

    def ask(self, question, answer_type=None, pipeline=None, send_factory=None):
        db = sqlite3.connect(self.semantic_path)
        flag = 2 if 'flag 2' in question else 1
        ids = [r[0] for r in db.execute('SELECT patient_id FROM patient WHERE flag = ?', (flag,))]
        db.close()
        answer = {'count': len(ids)} if answer_type == 'count' else {'patient_ids': ids}
        hybrid = pipeline == 'hybrid'
        return {'answer': answer, 'answer_type': answer_type, 'decision': 'unanimous' if hybrid else 'cohort_family',
                'pipeline': pipeline, 'interpretation': 'fake', 'cost_usd': 0.01 if hybrid else 0.0,
                'new_paid_api_calls': 3 if hybrid else 0, 'route': 'hybrid' if hybrid else 'cohort_family',
                'certified': None if hybrid else {'tier': 'cohort_family', 'bound': 0.0066, 'alpha': 0.05, 'delta': 0.1}}


def fake_sender(ledger_path, replies):
    """A ledgered-sender stand-in: writes one validated row per call into the arm's ledger, returns canned replies."""
    @contextmanager
    def factory():
        def send(body):
            text = replies.pop(0) if replies else '{"sql": "SELECT patient_id FROM patient WHERE flag = 1"}'
            usage = {'input_tokens': 1000, 'output_tokens': 100}
            raw = {'model': body['model'], 'content': [{'type': 'text', 'text': text}], 'usage': usage}
            db = sqlite3.connect(ledger_path)
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?)', (os.urandom(8).hex(), 'claude', body['model'], .5, 'validated',
                                                                    json.dumps(raw), None, 0.1))
            db.commit()
            db.close()
            return raw, 0.00015, False
        yield send
    return factory


def in_process_runner(spec, env):
    ws = FakeWorkspace(Path(spec['workspace_root']) / spec['workspace_slug'])
    arm = common.ARMS[spec['arm']]
    sf = fake_sender(spec['ledger_path'], []) if arm['kind'] == 'baseline' else None
    study_run.run_arm_pass({**spec, 'prepare': False}, ws=ws, send_factory=sf, keys=False)


def fake_scorer(run_dir, arm, n):
    return score.score_arm_pass(run_dir, arm, n, ref=FAKE_REF)


class HarnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws = make_workspace(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def run_study(self, arms, repeat=1):
        clone = lambda src, dst: isolation.clone_workspace(src, dst, allow_copy=True)  # noqa: E731
        return study_run.orchestrate(arms, 'T1', questions=QUESTIONS, repeat=repeat, runner=in_process_runner, scorer=fake_scorer,
                                     workspace_dir=self.ws, study_root=self.root / 'study', clone=clone, keep_workspaces=True)

    def test_arms_produce_expected_files(self):
        run_dir = self.run_study(['A', 'D', 'E', 'F5'], repeat=2)
        run = json.loads((run_dir / 'run.json').read_text())
        for key in ('git', 'pricing_sha256', 'thresholds_sha256', 'questions', 'arms', 'passes'):
            self.assertIn(key, run)
        self.assertEqual(run['questions']['fingerprint']['locked']['n'], 3)
        for arm in ('A', 'D', 'E', 'F5'):
            for n in (1, 2):
                d = run_dir / arm / f'p{n}'
                for f in ('answers.jsonl', 'timings.json', 'ledger.jsonl', 'score.json', 'env.json'):
                    self.assertTrue((d / f).is_file(), f'{arm} p{n} {f}')
                s = json.loads((d / 'score.json').read_text())
                self.assertEqual(s['summary']['n'], 3)
        flags = {(p['arm'], p['pass']): p['agere_flags'] for p in run['passes']}
        self.assertEqual(flags[('F5', 1)].get('AGERE_SPECIALISTS_FIRST'), 'off')
        self.assertNotIn('AGERE_SPECIALISTS_FIRST', flags[('A', 1)])
        self.assertTrue(all(p['status'] == 'done' for p in run['passes']), run['passes'])

        # A and the fake stack answer all three correctly at $0; D's fixed patient-list SQL gets Q1 right, cannot shape Q2 (count)
        # and lists the wrong patients for Q3
        a = json.loads((run_dir / 'A' / 'p1' / 'score.json').read_text())
        self.assertEqual(a['summary']['correct'], 3)
        self.assertEqual(a['summary']['free'], 3)
        d = json.loads((run_dir / 'D' / 'p1' / 'score.json').read_text())
        self.assertEqual([r['correct'] for r in d['questions']], [True, False, False])
        self.assertEqual(d['summary']['ledger_calls'], 3)
        self.assertAlmostEqual(d['summary']['cost_usd'], 3 * (1000 * 0.1 + 100 * 0.5) / 1e6, places=9)  # ledger window, pricing file
        self.assertTrue(all(r['cost_source'] == 'ledger' for r in d['questions']))
        analysis = json.loads((run_dir / 'analysis.json').read_text())
        self.assertIn('D', analysis['sets']['locked']['comparisons'])
        self.assertIn('F5', analysis['sets']['locked']['ablations'])
        self.assertEqual(analysis['certificates'][0]['tier'], 'cohort_family')
        self.assertTrue(any(r['arm'] == 'A' and r['warm_second_pass'] for r in analysis['repeatability']))

    def test_exploratory_arms_are_not_dropped_from_analysis(self):
        run_dir = self.run_study(['A2', 'A3'])
        result = json.loads((run_dir / 'analysis.json').read_text())
        self.assertEqual(set(result['sets']['locked']['arms']), {'A2', 'A3'})

    def test_report_renders_with_linked_numbers_and_no_external_calls(self):
        run_dir = self.run_study(['A', 'D'])
        page = (run_dir / 'report.html').read_text()
        md = (run_dir / 'report.md').read_text()
        self.assertIn('href="A/p1/score.json"', page)
        self.assertIn('prefers-color-scheme:dark', page)
        self.assertIn('@media print', page)
        self.assertNotIn('http://', page)
        self.assertNotIn('https://', page)
        self.assertNotIn('<script', page)
        self.assertIn('(A/p1/score.json)', md)
        # headline claims only where the paired interval excludes zero: D's cost is a constant $0.00015 per question above A's $0,
        # so the cost difference clears it; three questions cannot clear McNemar (p = 0.5), so no accuracy claim is made
        analysis = json.loads((run_dir / 'analysis.json').read_text())
        self.assertEqual({h['metric'] for h in analysis['headlines']}, {'cost_per_question', 'cost_per_correct'})

    def test_caches_isolated_between_arms(self):
        run_dir = self.run_study(['A', 'E'], repeat=2)
        envs = {(arm, n): json.loads((run_dir / arm / f'p{n}' / 'env.json').read_text()) for arm in ('A', 'E') for n in (1, 2)}
        ledgers = {k: v['ledger'] for k, v in envs.items()}
        roots = {k: v['workspace_root'] for k, v in envs.items()}
        self.assertEqual(len(set(ledgers.values())), 4)                  # a fresh ledger file per arm-pass
        self.assertEqual(roots[('A', 2)], roots[('A', 1)])               # arm A pass 2 is warm by design
        self.assertEqual(len({roots[('A', 1)], roots[('E', 1)], roots[('E', 2)]}), 3)
        self.assertTrue(envs[('A', 2)]['warm'] and not envs[('E', 2)]['warm'])
        clone = Path(roots[('E', 1)]) / 'fake-ws'
        self.assertFalse((clone / 'pipeline' / 'learning.sqlite3').exists())
        self.assertFalse((clone / 'pipeline' / 'question-plans.sqlite3').exists())
        self.assertTrue((clone / 'pipeline' / 'model-cache.sqlite3').exists())  # deterministic embedding memo only
        self.assertEqual(list((clone / 'receipts').iterdir()), [])
        self.assertFalse((clone / 'questions.jsonl').exists())
        # a write in one clone never reaches another clone or the source
        db = sqlite3.connect(clone / 'semantic.sqlite3')
        db.execute("INSERT INTO patient VALUES ('pX', 1)")
        db.commit()
        db.close()
        for other in (Path(roots[('E', 2)]) / 'fake-ws' / 'semantic.sqlite3', self.ws / 'semantic.sqlite3'):
            n = sqlite3.connect(other).execute("SELECT count(*) FROM patient WHERE patient_id = 'pX'").fetchone()[0]
            self.assertEqual(n, 0)

    def test_worker_env_strips_inherited_flags(self):
        env = common.worker_env('A', {'AGERE_CASCADE': 'off', 'AGERE_STREAMING': 'on', 'PATH': '/bin'})
        self.assertNotIn('AGERE_CASCADE', env)
        self.assertEqual(env['AGERE_STREAMING'], 'on')
        self.assertEqual(common.worker_env('F2', {})['AGERE_CASCADE'], 'off')
        f1 = common.worker_env('F1', {})
        self.assertEqual({f1[k] for k in ('AGERE_COHORT_FAMILIES', 'AGERE_DQ_FAMILIES', 'AGERE_VBC_FAMILIES')}, {'off'})

    def test_ledger_seed_and_product_path(self):
        from clinical_core.agere import gateway
        target = isolation.seed_ledger(self.root / 'arm-ledger.sqlite3')
        cap = sqlite3.connect(target).execute('SELECT cap FROM authorization').fetchone()[0]
        self.assertIsNone(cap)  # uncapped, like the product ledger
        with unittest.mock.patch.dict(os.environ, {'AGERE_LEDGER_PATH': str(target)}):
            self.assertEqual(Path(gateway.default_ledger_path()).resolve(), Path(target).resolve())


class BaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = make_workspace(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_docs_levels(self):
        _, idx = baseline.docs_bundle(self.ws / 'analyst_docs', 'index')
        _, full = baseline.docs_bundle(self.ws / 'analyst_docs', 'full')
        self.assertEqual(idx['files'], ['catalog_index.md'])
        self.assertEqual(full['files'], ['catalog_index.md', 'table_patient.md', 'section_codes_and_values.md'])

    def test_one_repair_on_execution_error(self):
        calls = []

        def send(body):
            calls.append(body)
            text = '{"sql": "SELECT nope FROM missing"}' if len(calls) == 1 else '{"sql": "SELECT patient_id FROM patient WHERE flag = 2"}'
            return {'content': [{'type': 'text', 'text': text}], 'model': body['model']}, 0.002, False
        q = {'id': 'Q3', 'question': 'flag 2', 'answer_type': 'patient_set'}
        row = baseline.answer(q, model='claude-haiku-5-5', docs_text='docs', as_of='2030-05-27', db_path=self.ws / 'semantic.sqlite3',
                              send=send, customer_ids=lambda ids: {i: 'epic:' + i for i in ids})
        self.assertEqual(len(calls), 2)
        self.assertTrue(row['repaired'])
        self.assertEqual(row['patient_ids'], ['epic:p3'])
        self.assertEqual(row['new_paid_api_calls'], 2)
        self.assertAlmostEqual(row['cost_usd'], 0.004)
        self.assertEqual(calls[0]['system'][1]['cache_control'], {'type': 'ephemeral'})

    def test_shapes(self):
        cid = lambda ids: {}  # noqa: E731
        self.assertEqual(baseline.to_answer('count', ['n'], [(3,)], cid)[0], {'count': 3})
        self.assertEqual(baseline.to_answer('value', ['v'], [(2.5,)], cid)[0], {'value': 2.5})
        t = baseline.to_answer('table', ['payer', 'n'], [('A', 2), ('B', 5)], cid)[0]
        self.assertEqual(t, {'table': [{'key': 'A', 'value': 2}, {'key': 'B', 'value': 5}]})
        self.assertIsNone(baseline.to_answer('count', ['n'], [], cid)[0])
        row = baseline.answer({'id': 'X', 'question': 'q', 'answer_type': 'count'}, model='m', docs_text='', as_of='', db_path='x',
                              send=lambda b: ({'content': [{'type': 'text', 'text': '{"sql": null, "unsupported": "no field"}'}]}, 0.0, True),
                              customer_ids=cid)
        self.assertEqual(row['status'], 'unsupported')
        self.assertEqual(row['new_paid_api_calls'], 0)


class StatisticsTest(unittest.TestCase):
    def test_mcnemar_exact(self):
        self.assertEqual(analyze.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(analyze.mcnemar_exact(5, 0), 0.0625)
        self.assertAlmostEqual(analyze.mcnemar_exact(10, 2), 2 * (1 + 12 + 66) / 4096)
        self.assertAlmostEqual(analyze.mcnemar_exact(2, 10), analyze.mcnemar_exact(10, 2))
        self.assertEqual(analyze.mcnemar_exact(3, 3), 1.0)

    def test_bootstrap_fixed_data(self):
        rows = [{'correct': i % 2 == 0, 'cost_usd': float(i), 'seconds': float(i), 'free': i < 3} for i in range(10)]
        ci1 = analyze.bootstrap(rows, analyze.METRICS['cost_per_question'])
        ci2 = analyze.bootstrap(rows, analyze.METRICS['cost_per_question'])
        self.assertEqual(ci1, ci2)                       # fixed seed: reproducible
        self.assertLess(ci1[0], 4.5)
        self.assertGreater(ci1[1], 4.5)                  # brackets the mean 4.5
        const = [{'correct': True, 'cost_usd': 1.0, 'seconds': 2.0, 'free': False}] * 8
        self.assertEqual(analyze.bootstrap(const, analyze.METRICS['cost_per_question']), [1.0, 1.0])
        self.assertEqual(analyze.paired_bootstrap(rows, rows, analyze.METRICS['latency_p90']), [0.0, 0.0])
        cheaper = [{**r, 'cost_usd': r['cost_usd'] + 10} for r in rows]
        ci = analyze.paired_bootstrap(rows, cheaper, analyze.METRICS['cost_per_question'])
        self.assertTrue(analyze.excludes_zero(ci))
        self.assertAlmostEqual(analyze.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertAlmostEqual(analyze.percentile(list(range(11)), 0.9), 9.0)

    def test_clopper_pearson(self):
        self.assertAlmostEqual(analyze.clopper_pearson_upper(0, 59, 0.95), 1 - 0.05 ** (1 / 59), places=6)
        self.assertEqual(analyze.clopper_pearson_upper(3, 3), 1.0)

    def test_locked_value_tolerance(self):
        ref = {'questions': {'V1': {'value': 10.0, 'tolerance': 0.05}, 'V2': {'count': 4}}}
        got = score.score_locked([{'id': 'V1', 'status': 'answered', 'value': 10.04},
                                  {'id': 'V2', 'status': 'answered', 'count': 5}], ref)
        self.assertTrue(got['V1']['correct'])
        self.assertFalse(got['V2']['correct'])


if __name__ == '__main__':
    unittest.main()
