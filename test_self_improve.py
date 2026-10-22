"""Self-improving loop and the hard training types: exact step combination, hard-family datasets, outcome-labelled router
data from verified traffic and replays, the tier router (training + cascade reorder), and the one-job loop.
Fixture warehouse and fake encoders; the active-warehouse check runs only where the warehouse exists."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from tools.training import label as Lb, datasets as D, router_outcomes as RO, self_improve as SI, train_all as T, question_factory as F  # noqa: E402
from clinical_core.agere.clinical import learning_hooks as H, tier_router as TR  # noqa: E402
from clinical_core.agere.clinical.proposers import ORDER  # noqa: E402
from test_learning_hooks import cascade_result  # noqa: E402
from test_training_pipeline import fixture  # noqa: E402

_Bench = D.Bench


class Combine(unittest.TestCase):
    def test_exact_combinations(self):
        a = {'columns': ['payer_id', 'count'], 'rows': [{'payer_id': 'P1', 'count': 10}, {'payer_id': 'P2', 'count': 5}]}
        b = {'columns': ['payer_id', 'count'], 'rows': [{'payer_id': 'P1', 'count': 18}, {'payer_id': 'P3', 'count': 1}]}
        why = Lb.combine('delta_by_group', [a, b], {'by': 'payer'})
        self.assertEqual(why['rows'][0], {'payer_id': 'P1', 'before': 10, 'after': 18, 'change': 8})
        self.assertEqual(why['value'], {'total_change': 4, 'top_driver': 'P1', 'top_driver_share': 2.0})
        trend = Lb.combine('trend', [{'columns': ['month', 'count'], 'rows': [{'month': f'2029-{m:02d}', 'count': 10 + m} for m in range(1, 13)]}], {})
        self.assertEqual(trend['value']['direction'], 'up'); self.assertEqual(trend['value']['slope_per_period'], 1.0)
        self.assertEqual(Lb.combine('share', [{'value': 200}, {'value': 50}], {})['value'], {'share': 0.25})
        self.assertEqual(Lb.combine('count_and_share', [{'value': 200}, {'value': 50}], {})['value'], {'count': 50, 'share': 0.25, 'of': 200})
        self.assertEqual(Lb.combine('difference', [{'value': 100}, {'value': 110}], {})['value'], {'difference': 10, 'percent_change': 0.1})
        self.assertEqual(Lb.combine('ratio', [{'value': 9}, {'value': 3}], {})['value'], {'ratio': 3.0})
        with self.assertRaises(ValueError): Lb.combine('share', [{'rows': []}, {'value': 1}], {})

    def test_hard_families_carry_labels_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = F.Vocab(stack=fixture(Path(tmp) / 'stack'), out=Path(tmp) / 'out')
            for fam in F.HARD:
                item = next(F.generate(1, seed=2, vocab=v, families=[fam]))
                self.assertIn(item['request']['kind'], ('engine', 'steps', 'request'), fam)
                if fam in ('engine', 'definition', 'why'): self.assertTrue(item['router_label'])
            d = next(F.generate(1, seed=2, vocab=v, families=['definition']))
            self.assertTrue(d['definition_dependent'])


def labeled(kind, value, family, router_label=None, answer=None, tf=None):
    return {'schema': Lb.SCHEMA, 'status': 'ok', 'id': family + str(abs(hash(json.dumps(value, sort_keys=True))) % 10**8), 'family': family,
            'form': 'f', 'template_family': tf or f'{family}/f', 'canonical': f'{family} question?', 'request_source': kind,
            'request': {'kind': kind, 'value': value}, 'answer': answer or {'status': 'answered', 'value': 3}, 'answer_key': '3',
            'provenance': {'tables': [], 'columns': [], 'query': None}, 'paraphrases': [{'text': f'{family} q please', 'styles': ['filler']}],
            'chain': None, 'engines': [value['operation']] if kind == 'engine' else [], 'router_label': router_label}


class Datasets(unittest.TestCase):
    def test_router_override_shape_and_decompose(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / 'labels'; d.mkdir()
            rows = [labeled('engine', {'operation': 'default_definition', 'parameters': {}, 'cohort': None, 'returns': 'table'}, 'definition', 'default_definition'),
                    labeled('steps', {'steps': ['How many patients have diabetes?', 'x'], 'combine': 'share'}, 'multi_step'),
                    labeled('steps', {'steps': ['a', 'b'], 'combine': 'delta_by_group', 'by': 'payer'}, 'why', 'explain')]
            (d / 'labels-00000-0000000.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
            m = D.build(d, Path(tmp) / 'ds', catalog_tables={'patient': ['patient_id']}, bench=D.Bench([]), vbc=False)
            got = [json.loads(l) for s in ('train', 'valid', 'heldout') for l in (Path(tmp) / 'ds' / 'router' / f'{s}.jsonl').read_text().splitlines()]
            self.assertEqual({r['label'] for r in got}, {'default_definition', 'literal_lookup', 'explain'})
            self.assertEqual(sum(m['learners']['decompose']['rows'].values()), 4)  # two steps items x two wordings


class RouterOutcomes(unittest.TestCase):
    def test_traffic_replay_and_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {'AGERE_TRAINING_DIR': str(root / 'training')}):
                stack = SimpleNamespace(path=root / 'stack', allow_live=False); stack.path.mkdir()
                (stack.path / 'run.json').write_text(json.dumps({'source_digest': 'fixture', 'snapshot': {'as_of': '2030-05-27'}}))
                r = H.after_answer(stack, 'How many members are on file?', cascade_result('typed_decider'))
                self.assertTrue(r['router_outcome'])
                H.after_answer(stack, 'Corrected question here?', cascade_result('bottled_sql'))
                (H.training_dir() / 'corrections-x.jsonl').write_text(json.dumps({'question': 'Corrected question here?'}) + '\n')
                rows = RO.traffic_rows([H.training_dir()])
            self.assertEqual([(x['question'], x['label']) for x in rows], [('How many members are on file?', 'typed_decider')])
            self.assertEqual(rows[0]['tried'][0][0], 'local_normalizer')
            lab = {'answer': {'value': 880}}
            ok = {'answer': {'status': 'answered', 'count': 880}, 'planner_receipt': {'interpretation': {'proposer': {'name': 'local_normalizer'}}}}
            bad = {'answer': {'status': 'answered', 'count': 879}, 'planner_receipt': {'interpretation': {'proposer': {'name': 'local_free_sql'}}}}
            self.assertEqual(RO.outcome(lab, ok), ('local_normalizer', None))
            self.assertEqual(RO.outcome(lab, bad), (None, 'local_free_sql'))
            rep = root / 'replay-1.jsonl'
            rep.write_text(json.dumps({'question': 'Q2?', 'label': 'semantic_cache', 'wrong_tiers': [], 'group': 'care_gap/x', 'how': 'labeled_replay'}) + '\n'
                           + json.dumps({'question': 'Q3?', 'label': None, 'wrong_tiers': ['local_free_sql'], 'group': 'care_gap/y', 'how': 'labeled_replay'}) + '\n')
            with mock.patch.object(D, 'Bench', lambda questions=None: _Bench([])):
                m = RO.build([], [rep], root / 'ds')
            self.assertEqual(m['stats'], {'rows': 2, 'positives': 1, 'negatives_only': 1, 'benchmark_excluded': 0})

    def test_replay_runs_compose_and_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def compose(stack, body):
                calls.append(body['question'])
                return {'answer': {'status': 'answered', 'count': 5}, 'planner_receipt': {'interpretation': {
                    'proposer': {'name': 'semantic_cache'}, 'proposers': [{'proposer': 'paraphrase_reuse', 'outcome': 'no proposal'}]}}}
            row = labeled('request', {}, 'care_gap', answer={'value': 5})
            with mock.patch('clinical_core.agere.clinical.conversation.handle_compose', compose):
                path = RO.replay(object(), [row], tmp)
            got = [json.loads(l) for l in Path(path).read_text().splitlines()]
            self.assertEqual(calls, ['care_gap q please']); self.assertEqual(got[0]['label'], 'semantic_cache')


class FakeEncoder:
    identity = 'fake-enc'

    def encode(self, texts):
        out = []
        for t in texts:
            v = np.zeros(8); v[0 if 'notes' in t else 1 if 'how many' in t.lower() else 2] = 1.0; v[3 + len(t) % 5] = 0.1
            out.append(v)
        return np.array(out)


class TierRouter(unittest.TestCase):
    def test_train_promote_and_reorder(self):
        with tempfile.TemporaryDirectory() as tmp:
            ds = Path(tmp) / 'ds' / 'tier_router'; ds.mkdir(parents=True)
            def rows(n, off=0):
                out = []
                for i in range(n):
                    out.append({'question': f'What do the notes say about thing {i + off}?', 'label': 'evidence_retrieval', 'group': 'g'})
                    out.append({'question': f'How many patients have condition {i + off}?', 'label': 'paraphrase_reuse', 'group': 'g'})
                return out
            for split, n, off in (('train', 30, 0), ('valid', 8, 100), ('heldout', 20, 200)):
                (ds / f'{split}.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows(n, off)))
            with mock.patch.object(TR, 'model_path', lambda: None):
                rec = T.train_tier_router(Path(tmp) / 'ds', Path(tmp) / 'runs', FakeEncoder(), 'fake-enc', False)
            self.assertEqual(rec['decision'], 'promoted'); self.assertEqual(rec['candidate_heldout_accuracy'], 1.0)
            pointer = json.loads((Path(tmp) / 'runs' / 'promoted' / 'tier_router.json').read_text())
            model = TR.load(Path(pointer['model']))
            stack = SimpleNamespace(retriever=SimpleNamespace(encoder=FakeEncoder()))
            order, receipt = TR.reorder(stack, 'What do the notes say about statins?', ORDER, model=model)
            self.assertEqual(order[:2], ('exact_local_plan', 'evidence_retrieval'), receipt)
            self.assertEqual(sorted(order), sorted(ORDER)); self.assertTrue(receipt['applied'])
            other = SimpleNamespace(retriever=SimpleNamespace(encoder=SimpleNamespace(identity='different')))
            self.assertEqual(TR.reorder(other, 'q', ORDER, model=model)[0], ORDER)  # a different encoder: order unchanged


class Loop(unittest.TestCase):
    def test_one_job_harvests_builds_retrains_and_promotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'training'
            stackp = fixture(Path(tmp) / 'stack')
            with mock.patch.dict(os.environ, {'AGERE_TRAINING_DIR': str(root)}):
                hooks = Path(tmp) / 'live'; hooks.mkdir()
                st = SimpleNamespace(path=hooks, allow_live=False)
                (hooks / 'run.json').write_text(json.dumps({'source_digest': 'fixture', 'snapshot': {'as_of': '2030-05-27'}}))
                for i in range(3):
                    H.after_answer(st, f'How many members are on file number {i}?', cascade_result('typed_decider'))
            seen = {}

            def train(ds, out, stack, smoke, learners, allow_mlx, install, seed_stores):
                seen.update(ds=ds, learners=learners, allow_mlx=allow_mlx)
                return {'results': {'tier_router': {'decision': 'promoted'}, 'router': {'decision': 'not_promoted'}}}
            with mock.patch.object(D, 'Bench', lambda questions=None: _Bench([])):
                r1 = SI.run(root=root, stack=stackp, min_new=5, train=train, loop=False)
                self.assertEqual(r1['steps']['harvest']['verified_rows'], 3); self.assertEqual(r1['steps']['harvest']['router_outcomes'], 3)
                self.assertTrue(r1['steps']['due']['due']); self.assertEqual(r1['steps']['promoted'], ['tier_router'])
                self.assertTrue((Path(seen['ds']) / 'tier_router' / 'manifest.json').exists())
                self.assertFalse(seen['allow_mlx'])
                r2 = SI.run(root=root, stack=stackp, min_new=5, train=train, loop=False)  # nothing new since: not due
                self.assertFalse(r2['steps']['due']['due']); self.assertNotIn('retrain', r2['steps'])


class ActiveWarehouseHard(unittest.TestCase):
    """One item per hard family labeled exactly on the active warehouse (skipped where it is not present)."""

    def test_hard_families_label_exactly(self):
        try:
            stack = F.active_stack()
        except Exception:  # noqa: BLE001
            stack = None
        if not stack or not Path(stack).exists(): self.skipTest('no active warehouse')
        with tempfile.TemporaryDirectory() as tmp:
            v = F.Vocab(out=tmp); ctx = Lb.Context(stack)
            for fam in ('engine', 'notes', 'why', 'trend', 'multi_step'):
                item = next(F.generate(1, seed=11, vocab=v, families=[fam]))
                row = Lb.label_item(ctx, item)
                self.assertEqual(row['status'], 'ok', (fam, item['canonical'], row.get('reason')))
                self.assertTrue(row['verified_by'].startswith('exact'))


if __name__ == '__main__':
    unittest.main()
