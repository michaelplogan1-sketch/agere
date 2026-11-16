"""Learned router (local choose step) and meaning-level semantic cache: thresholds, fallbacks, slot checks, receipts."""
import json, os, sqlite3, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from clinical_core.agere.clinical import learned_router as LR, operator_router as router, semantic_cache as SC, learning
from clinical_core.agere.clinical import economical_planner as planner
from clinical_core.agere.clinical.analysis_graph import validate

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools' / 'learned_routing'))
AS_OF = '2029-08-15'
PLAN = {'query': 'SELECT 1', 'returns': 'count'}


class ConstantEncoder:
    """Every text maps to the same unit vector: similarity is always 1, so only the slot check decides."""
    identity = 'enc-test'
    def encode(self, texts, **_):
        return np.tile(np.eye(1, 8)[0], (len(texts), 1))


class DistinctEncoder:
    identity = 'enc-test'
    def encode(self, texts, **_):
        return np.eye(len(texts), max(8, len(texts)))


def fake_model(label, probability, threshold=0.9):
    """A model whose softmax puts `probability` on `label` for every question."""
    n, d = len(LR.LABELS), 8 + len(LR.cue_names())
    rest = (1 - probability) / (n - 1)
    logits = np.log(np.full(n, rest)); logits[LR.LABELS.index(label)] = np.log(probability)
    return {'version': LR.VERSION, 'labels': LR.LABELS, 'cues': LR.cue_names(), 'encoder_identity': 'enc-test',
            'temperature': 1.0, 'threshold': threshold, 'W': np.zeros((n, d)), 'b': logits}


class LearnedRouterThreshold(unittest.TestCase):
    def test_confident_prediction_is_used(self):
        label, info = LR.decide('who is sliding on eGFR', ConstantEncoder(), fake_model('trajectory', 0.99))
        self.assertEqual(label, 'trajectory'); self.assertTrue(info['used']); self.assertEqual(info['api_usd'], 0.0)

    def test_below_threshold_defers(self):
        label, info = LR.decide('q', ConstantEncoder(), fake_model('trajectory', 0.80))
        self.assertIsNone(label); self.assertFalse(info['used']); self.assertEqual(info['label'], 'trajectory')

    def test_not_answerable_always_goes_to_the_paid_router(self):
        label, info = LR.decide('q', ConstantEncoder(), fake_model('not_answerable', 0.999))
        self.assertIsNone(label); self.assertFalse(info['used'])

    def test_literal_lookup_may_shortcut(self):
        self.assertEqual(LR.decide('how many ED visits', ConstantEncoder(), fake_model('literal_lookup', 0.99))[0], 'literal_lookup')

    def test_encoder_mismatch_disables(self):
        other = SimpleNamespace(identity='other', encode=ConstantEncoder().encode)
        label, info = LR.decide('q', other, fake_model('trajectory', 0.99))
        self.assertIsNone(label); self.assertIn('Encoder', info['reason'])

    def test_switch_off_and_no_encoder(self):
        with mock.patch.dict(os.environ, {'AGERE_LEARNED_ROUTER': 'off'}):
            self.assertIsNone(LR.decide('q', ConstantEncoder(), fake_model('trajectory', 0.99))[0])
        self.assertIsNone(LR.decide('q', None)[0])

    def test_model_for_an_older_capability_list_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'm.json'
            path.write_text(json.dumps({'version': LR.VERSION, 'labels': LR.LABELS[:-3] + ['not_answerable'], 'cues': LR.cue_names()}))
            self.assertIsNone(LR.load(path))

    def test_shipped_model_matches_current_registry_and_target(self):
        model = LR.load()
        self.assertIsNotNone(model, 'retrain: tools/learned_routing/train.py --write')
        self.assertGreaterEqual(model['heldout']['accuracy_at_threshold'], 0.98)
        self.assertTrue(model['training']['benchmarks_excluded'])

    def test_threshold_selection_reaches_target_on_every_stricter_cut(self):
        from train import choose_threshold
        p = np.array([0.99] * 50 + [0.9] * 50 + [0.6] * 50)
        correct = np.array([True] * 50 + [True] * 49 + [False] + [False] * 25 + [True] * 25)
        t, coverage, accuracy = choose_threshold(p, correct, target=0.98, min_cases=30)
        self.assertEqual(t, 0.9); self.assertAlmostEqual(coverage, 100 / 150); self.assertGreaterEqual(accuracy, 0.98)
        t, coverage, _ = choose_threshold(np.array([0.9] * 40), np.array([False] * 40))
        self.assertGreater(t, 1); self.assertEqual(coverage, 0)

    def test_training_never_sees_acceptance_questions(self):
        from train import bench_sets, bench_filter
        from templates import generate
        acceptance, bench = bench_sets()
        generated = {r['question'].casefold().rstrip('?') for r in generate(per_template=8)}
        self.assertFalse(generated & {q.casefold().rstrip('?') for q in bench})
        near = bench_filter(bench)
        self.assertTrue(acceptance); self.assertTrue(all(near(c['question']) for c in acceptance))
        self.assertTrue(near(acceptance[0]['question'].lower() + ' please'))
        model = LR.load()
        if model: self.assertTrue(model['training']['benchmarks_excluded'])


class RouterPreStep(unittest.TestCase):
    good = {'name': 'trajectory', 'input': {'parameters': {'concept': 'egfr', 'direction': 'worse'}}}
    bad = {'name': 'trajectory', 'input': {'parameters': {'concept': 'not_a_lab'}}}

    def test_learned_choice_skips_the_paid_choose_call(self):
        calls = []
        def fake(question, as_of, ledger, out, op=None, feedback=None, model=None):
            calls.append(op); self.assertIsNotNone(op, 'paid choose must not run')
            return self.good, {'api_usd': 0.002, 'step': 'fill'}
        with mock.patch.object(LR, 'decide', return_value=('trajectory', {'used': True, 'probability': 0.99})), \
                mock.patch.object(router, '_ask', side_effect=fake):
            graph, info = router.route('whose kidney function is sliding', AS_OF, None, None, validate, encoder=ConstantEncoder())
        self.assertIsNotNone(graph); self.assertEqual(calls, ['trajectory'])
        self.assertEqual(info['chosen_by'], 'learned_router'); self.assertTrue(info['learned_router']['used'])
        self.assertAlmostEqual(router.cost(info), 0.002)

    def test_learned_literal_lookup_makes_no_paid_call(self):
        with mock.patch.object(LR, 'decide', return_value=('literal_lookup', {'used': True})), \
                mock.patch.object(router, '_ask', side_effect=AssertionError('no paid call')):
            graph, info = router.route('How many ED visits in 2029?', AS_OF, None, None, validate, encoder=ConstantEncoder())
        self.assertIsNone(graph); self.assertEqual(info['decision'], 'literal_lookup'); self.assertEqual(info['calls'], [])

    def test_unconfident_falls_back_to_paid_choose(self):
        steps = []
        def fake(question, as_of, ledger, out, op=None, feedback=None, model=None):
            steps.append(op)
            return ({'name': 'trajectory', 'input': {}} if op is None else self.good), {'api_usd': 0.001}
        with mock.patch.object(LR, 'decide', return_value=(None, {'used': False, 'probability': 0.5})), \
                mock.patch.object(router, '_ask', side_effect=fake):
            graph, info = router.route('q', AS_OF, None, None, validate, encoder=ConstantEncoder())
        self.assertEqual(steps, [None, 'trajectory']); self.assertEqual(info['chosen_by'], 'paid_router')
        self.assertFalse(info['learned_router']['used'])

    def test_unfillable_learned_choice_is_rechecked_by_the_paid_router(self):
        steps = []
        def fake(question, as_of, ledger, out, op=None, feedback=None, model=None):
            steps.append(op)
            if op is None: return {'name': 'literal_lookup', 'input': {}}, {'api_usd': 0.001}
            return self.bad, {'api_usd': 0.002}
        with mock.patch.object(LR, 'decide', return_value=('trajectory', {'used': True})), mock.patch.object(router, '_ask', side_effect=fake):
            graph, info = router.route('q', AS_OF, None, None, validate, encoder=ConstantEncoder())
        self.assertIsNone(graph); self.assertEqual(steps, ['trajectory', 'trajectory', None])
        self.assertEqual(info['decision'], 'literal_lookup'); self.assertEqual(info['chosen_by'], 'paid_router_after_learned_fill_failed')

    def test_no_encoder_keeps_the_old_behaviour(self):
        with mock.patch.object(router, '_ask', return_value=({'name': 'literal_lookup', 'input': {}}, {'api_usd': 0.001})) as ask:
            router.route('How many ED visits in 2029?', AS_OF, None, None, validate)
        self.assertEqual(ask.call_count, 1)


BASE = 'How many Medicare patients with diabetes had an A1c above 9 in 2028?'
MINIMAL = {
    'year': 'How many Medicare patients with diabetes had an A1c above 9 in 2029?',
    'threshold': 'How many Medicare patients with diabetes had an A1c above 8 in 2028?',
    'comparator': 'How many Medicare patients with diabetes had an A1c below 9 in 2028?',
    'inclusive': 'How many Medicare patients with diabetes had an A1c at least 9 in 2028?',
    'payer': 'How many Medicaid patients with diabetes had an A1c above 9 in 2028?',
    'unit': 'How many Medicare visits with diabetes had an A1c above 9 in 2028?',
    'negation': 'How many Medicare patients without diabetes had an A1c above 9 in 2028?',
    'condition': 'How many Medicare patients with hypertension had an A1c above 9 in 2028?',
    'subtype': 'How many Medicare patients with type 2 diabetes had an A1c above 9 in 2028?',
    'lab': 'How many Medicare patients with diabetes had an LDL above 9 in 2028?',
    'shape': 'Which Medicare patients with diabetes had an A1c above 9 in 2028?',
    'month': 'How many Medicare patients with diabetes had an A1c above 9 in March 2028?',
    'latest': 'How many Medicare patients with diabetes had their latest A1c above 9 in 2028?',
    'sex': 'How many female Medicare patients with diabetes had an A1c above 9 in 2028?',
}


class SlotCheck(unittest.TestCase):
    def test_each_minimal_difference_is_caught(self):
        for kind, variant in MINIMAL.items():
            with self.subTest(kind=kind):
                self.assertFalse(SC.same_slots(BASE, variant))

    def test_and_or_order_and_ids(self):
        self.assertFalse(SC.same_slots('Patients with heart failure and CKD', 'Patients with heart failure or CKD'))
        self.assertFalse(SC.same_slots('Patients with heart failure but not CKD', 'Patients with CKD but not heart failure'))
        self.assertFalse(SC.same_slots('Total paid for PCP-0012 in 2029', 'Total paid for PCP-0013 in 2029'))
        self.assertFalse(SC.same_slots('ED visits by practice', 'ED visits by PCP'))
        self.assertFalse(SC.same_slots('Readmissions within 30 days', 'Readmissions within 90 days'))
        self.assertFalse(SC.same_slots('patients aged 65 or older', 'patients aged 65 or younger'))

    def test_paraphrases_keep_the_same_slots(self):
        for variant in ['how many medicare pts with diabetes had A1c > 9 in 2028',
                        'Can you tell me how many Medicare diabetic patients had an A1c greater than 9 during 2028?']:
            self.assertTrue(SC.same_slots(BASE, variant), variant)
        self.assertTrue(SC.same_slots('Readmissions within 30 days for heart failure patients',
                                      'readmissions within thirty days for CHF patients'))


class SemanticCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        (self.path / 'run.json').write_text(json.dumps({'source_digest': 'release-a'}))
        learning.record_verified(self.path, BASE, 'sql', PLAN, {}, 'hosted_verified_sql', [], 'agree')
        SC._INDEX.clear()

    def tearDown(self): self.tmp.cleanup()

    def test_paraphrase_reuses_the_verified_plan(self):
        hit, info = SC.lookup(self.path, 'how many medicare pts with diabetes had A1c > 9 in 2028', ConstantEncoder())
        self.assertEqual(hit['plan'], PLAN); self.assertEqual(info['matched_question'], BASE)
        self.assertEqual(info['reason'], 'reused')

    def test_minimal_differences_never_reuse(self):
        for kind, variant in MINIMAL.items():
            with self.subTest(kind=kind):
                self.assertIsNone(SC.lookup(self.path, variant, ConstantEncoder())[0])

    def test_low_similarity_blocks_reuse(self):
        hit, info = SC.lookup(self.path, 'how many medicare pts with diabetes had A1c > 9 in 2028', DistinctEncoder())
        self.assertIsNone(hit); self.assertIn('similarity', info['reason'])

    def test_corrected_questions_are_never_reused(self):
        learning.record_correction(self.path, BASE, 'wrong denominator')
        SC._INDEX.clear()
        hit, info = SC.lookup(self.path, 'how many medicare pts with diabetes had A1c > 9 in 2028', ConstantEncoder())
        self.assertIsNone(hit); self.assertIn('corrected', info['reason'])

    def test_corrected_incoming_question_is_not_served(self):
        q = 'how many medicare pts with diabetes had A1c > 9 in 2028'
        learning.record_correction(self.path, q, 'user flagged')
        SC._INDEX.clear()
        self.assertIsNone(SC.lookup(self.path, q, ConstantEncoder())[0])

    def test_other_context_is_not_reused(self):
        (self.path / 'run.json').write_text(json.dumps({'source_digest': 'release-b'}))
        self.assertIsNone(SC.lookup(self.path, 'how many medicare pts with diabetes had A1c > 9 in 2028', ConstantEncoder())[0])

    def test_benchmark_questions_are_never_served_or_used(self):
        variant = 'how many medicare pts with diabetes had A1c > 9 in 2028'
        with mock.patch.object(SC, 'benchmark_questions', return_value=frozenset({SC._norm(variant)})):
            hit, info = SC.lookup(self.path, variant, ConstantEncoder())
        self.assertIsNone(hit); self.assertIn('benchmark', info['reason'])
        with mock.patch.object(SC, 'benchmark_questions', return_value=frozenset({SC._norm(BASE).rstrip('?.')})):
            self.assertIsNone(SC.lookup(self.path, variant, ConstantEncoder())[0])
        acceptance = json.loads((Path(__file__).resolve().parents[2] / 'bench' / 'acceptance' / 'survival.json').read_text())[0]['question']
        self.assertTrue(SC.is_benchmark(acceptance))

    def test_exact_question_is_left_to_the_learned_rung(self):
        hit, info = SC.lookup(self.path, BASE.lower(), ConstantEncoder())
        self.assertIsNone(hit); self.assertIn('exact', info['reason'])

    def test_switch_off(self):
        with mock.patch.dict(os.environ, {'AGERE_SEMANTIC_CACHE': 'off'}):
            self.assertIsNone(SC.lookup(self.path, 'how many medicare pts with diabetes had A1c > 9 in 2028', ConstantEncoder())[0])

    def test_adversarial_report_has_zero_false_reuses(self):
        report = Path(__file__).resolve().parents[2] / 'tools' / 'learned_routing' / 'semantic_cache_report.json'
        summary = json.loads(report.read_text())['summary']
        self.assertEqual(summary['false_reuses'], 0); self.assertGreater(summary['minimal_difference_pairs'], 500)


class PlannerReceipts(unittest.TestCase):
    def stack(self, root, live=False):
        return SimpleNamespace(path=root, receipts=root, allow_live=live,
                               retriever=SimpleNamespace(encoder=ConstantEncoder(), meta={'source_digest': 'release-a', 'snapshot': {'as_of': AS_OF}}))

    def patches(self):
        return [mock.patch('clinical_core.agere.clinical.source_profile.load', return_value={'streams': {}}),
                mock.patch('clinical_core.agere.clinical.local_compiler.compile', return_value=(None, {'route': 'fallback', 'reason': 'test'})),
                mock.patch('clinical_core.agere.clinical.verify.lint', return_value=[]),
                mock.patch.dict(os.environ, {'AGERE_LOCAL_MODEL': 'off'})]

    def test_semantic_cache_route_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'run.json').write_text(json.dumps({'source_digest': 'release-a'}))
            learning.record_verified(root, BASE, 'sql', PLAN, {}, 'hosted_verified_sql', [], 'agree')
            SC._INDEX.clear()
            ps = self.patches()
            for p in ps: p.start()
            try:
                graph, receipt = planner.plan(self.stack(root), 'how many medicare pts with diabetes had A1c > 9 in 2028', None, None)
            finally:
                for p in ps: p.stop()
        self.assertEqual(receipt['route'], 'semantic_cache'); self.assertEqual(receipt['new_paid_api_calls'], 0)
        self.assertEqual(receipt['semantic_cache']['matched_question'], BASE)
        self.assertEqual(graph['nodes'][-1]['parameters'], PLAN)

    def test_learned_router_decision_is_in_the_operator_router_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'run.json').write_text(json.dumps({'source_digest': 'release-a'}))
            fill = ({'name': 'trajectory', 'input': {'parameters': {'concept': 'egfr', 'direction': 'worse'}}}, {'api_usd': 0.002, 'cache_hit': False})
            ps = self.patches() + [mock.patch.object(LR, 'decide', return_value=('trajectory', {'used': True, 'probability': 0.99})),
                                   mock.patch.object(router, '_ask', return_value=fill)]
            for p in ps: p.start()
            try:
                graph, receipt = planner.plan(self.stack(root, live=True), 'whose kidney function is sliding', None, None)
            finally:
                for p in ps: p.stop()
        self.assertEqual(receipt['route'], 'operator_router'); self.assertEqual(receipt['new_paid_api_calls'], 1)
        info = receipt['local_compiler_declined']['operator_router']
        self.assertEqual(info['chosen_by'], 'learned_router'); self.assertTrue(info['learned_router']['used'])
        self.assertEqual(receipt['local_compiler_declined']['semantic_cache']['reason'], 'no verified store')


if __name__ == '__main__':
    unittest.main()
