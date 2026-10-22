"""Six Sigma production defect tracking + public scorecard (quality_scorecard.py, /api/scorecard) and the sealed truth release
gate (tools/quality/sealed_gate.py, run by warehouse_activation.quality_gates). The generator scorer is faked: this test
never runs the sealed bank, never opens private/ and never sees a truth row.

    .venv/bin/python -m unittest test_quality_gates
"""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import quality_scorecard as Q

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('agere_sealed_gate_test', ROOT / 'tools/quality/sealed_gate.py')
G = importlib.util.module_from_spec(spec); spec.loader.exec_module(G)


def answer_log(path, rows, corrections=()):
    db = sqlite3.connect(Path(path) / 'answers.sqlite3')
    db.execute("""CREATE TABLE answers(at REAL, question_sha TEXT, question TEXT, route TEXT, status TEXT, answer_type TEXT,
                  api_calls INTEGER, api_usd REAL, seconds REAL, error TEXT)""")
    db.executemany('INSERT INTO answers VALUES (?,?,?,?,?,?,?,?,?,?)', rows); db.commit(); db.close()
    db = sqlite3.connect(Path(path) / 'learning.sqlite3')
    db.execute('CREATE TABLE corrections(question TEXT, note TEXT, expected TEXT, created REAL)')
    db.executemany('INSERT INTO corrections VALUES (?,?,?,?)', corrections); db.commit(); db.close()


class ProductionTests(unittest.TestCase):
    def test_classes_dpmo_sigma_routes_and_p_chart(self):  # SIX-SIGMA-P (FM-35)
        day = 86400; t0 = 1_800_000_000
        rows = []
        for d in range(5):
            for i in range(20):
                status = ('internal_error' if i == 0 else 'declined' if i == 1 and d >= 3 else 'unsupported' if i == 2 else
                          'partial' if i == 3 else 'answered')
                rows.append((t0 + d * day + i, 'x', f'q{d}-{i}', 'shared_planner' if i % 2 else 'exact', status, 'count', 0, 0.0, 1.0, ''))
        with tempfile.TemporaryDirectory() as tmp:
            answer_log(tmp, rows, corrections=[('q4-5', 'wrong', '3', t0 + 10 * day)])
            p = Q.production(tmp)
        o = p['overall']
        self.assertEqual(o['opportunities'], 100)
        self.assertEqual(o['classes'], {'hard_failure': 5, 'not_answered': 2, 'reported_wrong': 1, 'partial': 5, 'data_absent': 5, 'ok': 82})
        self.assertEqual(o['defects'], 8)
        self.assertAlmostEqual(o['dpmo'], 80000.0)
        self.assertAlmostEqual(o['sigma'], 2.905, places=2)
        self.assertEqual(len(p['p_chart']['points']), 5)
        self.assertEqual({r['route'] for r in p['routes']}, {'shared_planner', 'exact'})
        self.assertEqual(p['pareto'][0], {'class': 'hard_failure', 'count': 5})
        self.assertEqual(p['windows']['last_7_days']['opportunities'], 100)

    def test_correction_before_the_answer_does_not_mark_it_wrong(self):
        self.assertEqual(Q.classify('answered', corrected=True), 'reported_wrong')
        self.assertEqual(Q.classify('error'), 'hard_failure')
        self.assertEqual(Q.classify(None), 'hard_failure')
        self.assertEqual(Q.classify('not_understood'), 'not_answered')

    def test_scorecard_payload_sections_and_no_rows_from_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            answer_log(tmp, [(time.time(), 'x', 'q', 'exact', 'answered', 'count', 0, 0.0, 1.0, '')])
            (tmp / 'gate').mkdir()
            (tmp / 'gate' / '2026__rel.json').write_text(json.dumps({'status': 'passed', 'aggregates': {'f1': 0.9}, 'rows': [{'patient_id': 'P-1'}]}))
            (tmp / 'tasks.json').write_text(json.dumps({'scores': {'CT-01': {'a': {'f1': 1.0}}}}))
            s = Q.scorecard(tmp, results=tmp / 'none', tasks=tmp / 'tasks.json', gate=tmp / 'gate')
        self.assertEqual(s['production']['overall']['defects'], 0)
        self.assertNotIn('rows', s['sealed_truth_gate'])
        self.assertEqual(s['clinical_tasks']['scores']['CT-01']['a']['f1'], 1.0)
        self.assertIn('empty', s['blind_tests'])
        json.dumps(s, default=str)

    def test_endpoint_is_served(self):
        src = (ROOT / 'clinical_core/agere/service.py').read_text()
        self.assertIn("parsed.path in ('/api/scorecard','/api/stack/quality')", src)


class SealedGateTests(unittest.TestCase):
    def test_aggregates_drop_rows_ids_and_long_text(self):  # SEALED-AGG (FM-34)
        scored = {'scope': 'sample', 'conditions': {'f1': 0.81, 'precision': 0.9, 'patients': ['P-1', 'P-2'],
                                                    'errors': [{'patient_id': 'P-1', 'got': 'x'}]},
                  'hcc': {'recall': 0.7, 'note': 'WHP-ABC123 was wrong'}, 'patient_ids': ['P-1'], 'n': 412}
        agg = G.aggregates(scored)
        self.assertEqual(agg, {'scope': 'sample', 'conditions': {'f1': 0.81, 'precision': 0.9}, 'hcc': {'recall': 0.7}, 'n': 412})

    def test_regressions_only_on_quality_metrics_beyond_tolerance(self):
        prev = {'a': {'f1': 0.80, 'n': 100}, 'b': {'recall': 0.5}}
        cur = {'a': {'f1': 0.75, 'n': 10}, 'b': {'recall': 0.49}}
        self.assertEqual(G.regressions(cur, prev), [{'metric': 'a.f1', 'previous': 0.8, 'current': 0.75}])

    def fake_runner(self, out):
        calls = []
        def run(cmd, cwd=None, capture_output=True, text=True):
            calls.append(cmd)
            if 'eval_predictions.py' in ' '.join(map(str, cmd)): return SimpleNamespace(returncode=0, stdout='', stderr='')
            return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr='')
        return run, calls

    def test_run_scores_saves_aggregates_and_compares(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            eco = tmp / 'eco'; (eco / 'ecosystem').mkdir(parents=True); (eco / 'ecosystem' / 'eval_scorer.py').write_text('')
            (eco / 'releases' / 'rel-v1').mkdir(parents=True)
            run, calls = self.fake_runner({'conditions': {'f1': 0.9, 'patients': ['P-1']}})
            first = G.run(tmp, release='rel-v1', sample_release=tmp, gate_dir=tmp / 'g', ecosystem=eco, runner=run)
            self.assertEqual((first['status'], first['aggregates']), ('passed', {'conditions': {'f1': 0.9}}))
            self.assertIn('--predictions', calls[1]); self.assertEqual(calls[1][:3], ['python3', '-m', 'ecosystem.eval_scorer'])
            run, _ = self.fake_runner({'conditions': {'f1': 0.8}})
            second = G.run(tmp, release='rel-v1', sample_release=tmp, gate_dir=tmp / 'g', ecosystem=eco, runner=run)
            self.assertEqual(second['status'], 'regressed')
            with self.assertRaises(ValueError):
                G.run(tmp, release='rel-v1', sample_release=tmp, gate_dir=tmp / 'g', ecosystem=eco, runner=run, enforce=True)
            saved = [json.loads(f.read_text()) for f in (tmp / 'g').glob('*.json')]
            self.assertTrue(all('P-1' not in json.dumps(s) for s in saved))
            missing = G.run(tmp, release='nope', sample_release=tmp, gate_dir=tmp / 'g', ecosystem=tmp / 'absent', runner=run)
            self.assertEqual(missing['status'], 'unavailable')

    def test_release_name_from_sample_directory(self):
        self.assertEqual(G.release_of('/x/artifacts/healthcare-warehouse-v2.8-sample-0.30')[0], 'healthcare-warehouse-v2.8')

    def test_activation_runs_the_gates_and_heals(self):
        from clinical_core.agere import warehouse_activation as W
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'connection.json').write_text(json.dumps({'snapshot': {'as_of': '2030-05-27'}}))
            fake_gate = SimpleNamespace(run=lambda build, enforce=False: {'status': 'passed', 'aggregates': {'f1': 1.0}})
            fake_ref = SimpleNamespace(score=lambda build, index, as_of: {'CT-01': {'a': {'f1': 1.0}}})
            with patch('importlib.util.spec_from_file_location') as spec_from, patch('importlib.util.module_from_spec') as from_spec:
                mods = iter([fake_gate, fake_ref])
                from_spec.side_effect = lambda s: next(mods)
                spec_from.return_value = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda m: None))
                with patch.object(Path, 'write_text') as write, patch.object(Path, 'mkdir'):
                    out = W.quality_gates(tmp)
        self.assertEqual(out['sealed_truth'], {'status': 'passed'})  # aggregates stay in bench/results, not on the receipt
        self.assertEqual(out['clinical_tasks'], {'status': 'saved', 'min_f1': 1.0})
        self.assertTrue(write.called)


if __name__ == '__main__':
    unittest.main()
