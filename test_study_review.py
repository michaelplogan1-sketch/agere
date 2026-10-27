"""Fresh native review detects changed totals, task alignment and source evidence."""
import json
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_adapter_learning import ScriptedBackend, base_fixture, task_manifest
from test_schema_prefetch import warehouse
from tools.training import adapters as D, recursive_adapters as R, study_review as S


class StudyReview(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name); warehouse(self.path)
        self.base = base_fixture(self.path / 'base')
        self.manifest, _ = task_manifest(self.path)
        for name in ('socket.socket.connect', 'socket.create_connection',
                     'urllib.request.urlopen', 'subprocess.Popen'):
            p = patch(name, side_effect=AssertionError('Review must stay offline'))
            p.start(); self.addCleanup(p.stop)
        result = D.run(self.manifest, self.path / 'registry', stack=self.path,
                       base=self.base, backend=ScriptedBackend(), train=True, install=False)
        self.receipt = next((self.path / 'registry/adapter-jobs').glob('*/receipt.json'))
        self.assertEqual(result['status'], 'not_selected')

    def review(self):
        return S.review_round(self.manifest, self.receipt, stack=self.path)

    def change(self, fn):
        record = json.loads(self.receipt.read_text()); fn(record)
        self.receipt.write_text(json.dumps(record))

    def test_actual_sql_reproduces_and_does_not_call_a_model_or_activate(self):
        with patch.object(D.Mlx, 'generator', side_effect=AssertionError('No inference in review')):
            result = self.review()
        self.assertEqual(result['status'], 'reproduced')
        self.assertEqual(result['comparisons']['candidate']['verification']['outcomes'],
                         {'acceptable': 1, 'accepted_wrong': 0, 'declined': 0})
        self.assertEqual(result['useful_improvements'], 0)
        self.assertFalse(result['selected_candidate']); self.assertFalse(result['install'])
        self.assertIsNone(result['cost_per_clinically_right_answer'])

    def test_forged_totals_or_verdict_cannot_claim_an_improvement(self):
        self.change(lambda r: r['qualification']['comparisons']['baseline']['verification'].update(acceptable=1))
        with self.assertRaisesRegex(ValueError, 'totals do not reproduce'):
            self.review()

    def test_missing_task_cannot_silently_shorten_a_comparison(self):
        self.change(lambda r: r['qualification']['comparisons']['candidate']['verification']['details'].clear())
        with self.assertRaisesRegex(ValueError, 'task identities'):
            self.review()

    def test_changed_native_source_invalidates_review(self):
        import sqlite3
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.execute('UPDATE appointment SET kept=0')
        with self.assertRaisesRegex(ValueError, 'source or variant changed'):
            self.review()

    def test_variant_error_names_the_failing_source_instead_of_the_primary_success_reason(self):
        import shutil, sqlite3
        from tools.training import adapter_tasks as T
        variant = self.path / 'variant'; variant.mkdir()
        shutil.copy(self.path / 'semantic.sqlite3', variant / 'semantic.sqlite3')
        with sqlite3.connect(variant / 'semantic.sqlite3') as db:
            db.execute("UPDATE appointment SET final_status='unknown'")
        spec = json.loads(self.manifest.read_text())
        spec['source_variants'] = [{'path': str(variant), 'source_identity': T.Context(variant).identity}]
        self.manifest.write_text(json.dumps(spec))
        class Backend(ScriptedBackend):
            @contextmanager
            def generator(self, learner, adapter, ctx, cfg):
                def answer(row):
                    if adapter is None: return 'UNSUPPORTED'
                    if row['split'] != 'verification': return json.dumps(row['target'])
                    return json.dumps({'analysis': {'steps': [{'name': 'answer_rows', 'purpose': row['question'],
                        'sql': "SELECT COUNT(*) AS count FROM appointment WHERE kept=1 AND final_status='completed'"}],
                        'final': 'answer_rows', 'returns': 'count', 'requirements': [
                            {'quote': row['question'], 'applied_in': ['answer_rows']}], 'unresolved': []}})
                yield answer
        D.run(self.manifest, self.path / 'variant-registry', stack=self.path,
              base=self.base, backend=Backend(), train=True, install=False)
        receipt = next((self.path / 'variant-registry/adapter-jobs').glob('*/receipt.json'))
        result = S.review_round(self.manifest, receipt, stack=self.path)
        metrics = result['comparisons']['candidate']['verification']
        self.assertEqual(metrics['source_results']['primary']['acceptable'], 1)
        self.assertEqual(metrics['source_results']['variant_1']['accepted_wrong'], 1)
        self.assertTrue(all(k.startswith('variant_1: ') for k in metrics['failure_reasons']))
        self.assertFalse(any('Within declared' in k for k in metrics['failure_reasons']))

    def test_cross_source_rejection_and_error_are_not_double_counted(self):
        details = [{'acceptable': False, 'guarded_wrong': True, 'guarded': False},
                   {'acceptable': True, 'guarded_wrong': False, 'guarded': True},
                   {'acceptable': False, 'guarded_wrong': False, 'guarded': False}]
        self.assertEqual(S.outcomes(details), {'acceptable': 1, 'accepted_wrong': 1, 'declined': 1})

    def test_dependencies_require_receipts_and_do_not_wait_on_a_reused_pid(self):
        paths = [self.path / 'typed-result.json', self.path / 'bottled-result.json']
        self.assertFalse(S.dependency_complete(paths, process_alive=True))
        with self.assertRaisesRegex(RuntimeError, 'without all required'):
            S.dependency_complete(paths, process_alive=False)
        for path in paths:
            path.write_text(json.dumps({'status': 'completed'}))
        self.assertTrue(S.dependency_complete(paths, process_alive=True))
        paths[1].write_text(json.dumps({'status': 'failed'}))
        with self.assertRaisesRegex(ValueError, 'did not complete'):
            S.dependency_complete(paths, process_alive=True)

    def test_whole_recursive_review_checks_native_results_and_fit_only_rehearsal(self):
        class Backend(ScriptedBackend):
            def train(self, data, output, cfg, resume=None):
                result = super().train(data, output, cfg, resume)
                (output / 'adapter_config.json').write_text(json.dumps({
                    'num_layers': cfg['num_layers'],
                    'lora_parameters': {k: cfg[k] for k in ('rank', 'scale', 'dropout', 'keys')}}))
                (output / 'adapters.safetensors').write_bytes(str(len(self.fits)).encode())
                return result
            @contextmanager
            def generator(self, learner, adapter, ctx, cfg):
                yield lambda row: ('UNSUPPORTED' if adapter is None or
                    (row['split'] == 'fit' and len(self.fits) == 1) else
                    json.dumps(row['target']))
        root = self.path / 'recursive-registry'
        result = R.run(self.manifest, root, stack=self.path, base=self.base,
                       rounds=2, train=True, backend=Backend())
        path = self.path / 'recursive-result.json'; path.write_text(json.dumps(result))
        reviewed = S.review_study(path, stack=self.path)
        self.assertEqual(len(reviewed['rounds']), 2)
        self.assertEqual(reviewed['status'], 'reproduced')
        # A fabricated correction must not pass merely because its split labels
        # still say fit. It must retain the exact approved source targets.
        correction = Path(result['study']) / 'correction-1/manifest.json'
        spec = json.loads(correction.read_text()); data = Path(spec['files'][0]['path'])
        rows = [json.loads(line) for line in data.read_text().splitlines()]
        rows[0]['question'] += ' Unreviewed extra clause.'
        data.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        from clinical_core.agere.clinical import adapter_learning as A
        spec['files'][0]['sha256'] = A.file_hash(data); correction.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError, 'correction/rehearsal allocation'):
            S.review_study(path, stack=self.path)


if __name__ == '__main__':
    unittest.main()
