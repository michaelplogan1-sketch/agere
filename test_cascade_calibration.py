"""Certified cascades (clinical_core/agere/clinical/cascade.py): Learn-then-Test thresholds per free tier, deferral below
the threshold, certificates in receipts, and today's behaviour when no thresholds exist. No model calls."""
import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.clinical import cascade, therapy_barriers as TB, together
from test_together import FakeStack, answer


def _write(path, records):
    cascade.write(records, path)
    return path


def _route_record(tier, threshold, *, bind=None, calibrated=True):
    return {'tier': tier, 'alpha': 0.05, 'delta': 0.10, 'n_calibration': 400, 'threshold': threshold, 'calibrated': calibrated,
            'bound': 0.031, 'n_accepted': 300, 'errors_accepted': 4, 'bind': cascade.code_digest(tier) if bind is None else bind}


class ThresholdSelectionTests(unittest.TestCase):
    def test_selected_threshold_meets_the_bound_with_the_stated_confidence(self):
        """Synthetic scores whose error probability falls with the score: over many calibration draws, the true error
        among accepted cases at the certified threshold is <= alpha in at least 1 - delta of the draws."""
        alpha, delta = 0.10, 0.10
        err = lambda s: 0.6 * (1 - s) ** 2       # true conditional error rate
        grid = [i / 50 for i in range(51)]
        def true_risk(lam):                     # E[err | s >= lam] for s ~ U(0, 1), by fine quadrature
            xs = [lam + (1 - lam) * (i + 0.5) / 2000 for i in range(2000)]
            return sum(err(x) for x in xs) / len(xs)
        rng, ok, certified = random.Random(3), 0, 0
        draws = 200
        for _ in range(draws):
            scores = [rng.random() for _ in range(400)]
            errors = [rng.random() < err(s) for s in scores]
            rec = cascade.fit('cohort_family', scores, errors, alpha=alpha, delta=delta, grid=grid, bind='x')
            if rec['threshold'] is None:
                ok += 1  # abstaining is always safe
                continue
            certified += 1
            self.assertLessEqual(rec['bound'], alpha + 1e-9)  # the reported bound is what LTT certified
            ok += true_risk(rec['threshold']) <= alpha
        self.assertGreater(certified, draws * 0.8)
        self.assertGreaterEqual(ok / draws, 1 - delta)

    def test_too_little_data_is_uncalibrated_with_a_reason(self):
        rec = cascade.fit('metric_router', [1.0] * 10, [False] * 10, bind='x')
        self.assertFalse(rec['calibrated'])
        self.assertIsNone(rec['threshold'])
        self.assertIn('needs >= 45 accepted', rec['reason'])  # alpha 5%, delta 10% with zero errors
        self.assertEqual(cascade.min_accepted(0.05, 0.10), 45)

    def test_route_level_certificate_and_heldout_evaluation(self):
        rec = cascade.fit('local_compiler', [1.0] * 120, [False] * 119 + [True], grid=[1.0], bind='x')
        self.assertTrue(rec['calibrated'])
        self.assertEqual(rec['threshold'], 1.0)
        self.assertLessEqual(rec['bound'], 0.05)
        ev = cascade.evaluate([1.0] * 30, [False] * 30, rec['threshold'])
        self.assertEqual((ev['n_accepted'], ev['errors_accepted'], ev['coverage']), (30, 0, 1.0))

    def test_upper_bound_is_clopper_pearson(self):
        self.assertAlmostEqual(cascade.upper_bound(0, 45, 0.10), 1 - 0.10 ** (1 / 45), places=4)
        self.assertEqual(cascade.upper_bound(3, 3, 0.1), 1.0)


class RuntimeCertificateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'cascade_thresholds.json'
        self.env = patch.dict(os.environ, {'AGERE_CASCADE_THRESHOLDS': str(self.path), 'AGERE_CASCADE': 'on'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        cascade._CACHE.clear()
        self.temp.cleanup()

    def receipt(self, defaults=0, definition=True):
        return {'route': 'cohort_family', 'cohort_family': {'family': 'therapy_class_gap', 'defaults': ['d'] * defaults,
                                                            'model_filled': [], 'has_definition': definition}}

    def test_below_threshold_defers_and_above_is_certified(self):
        _write(self.path, [_route_record('cohort_family', 0.9)])
        low = cascade.certify_route(self.receipt(defaults=2, definition=False))     # score 0.79
        high = cascade.certify_route(self.receipt())                                # score 1.0
        self.assertEqual((low['status'], low['accept']), ('below_threshold', False))
        self.assertEqual((high['status'], high['accept']), ('certified', True))
        block = cascade.receipt_block(high)
        self.assertEqual({k: block[k] for k in ('tier', 'alpha', 'delta', 'bound', 'n_calibration')},
                         {'tier': 'cohort_family', 'alpha': 0.05, 'delta': 0.10, 'bound': 0.031, 'n_calibration': 400})
        self.assertIn('calibrated error bound at confidence 90% is 3.1% <= alpha 5%', block['statement'])
        self.assertIsNone(cascade.receipt_block(low))

    def test_missing_stale_disabled_or_uncertified_records_accept_as_today(self):
        cases = []
        cases.append(cascade.certify('cohort_family', 0.1))                         # no file
        _write(self.path, [_route_record('cohort_family', 0.9, bind='another-code-digest')])
        cases.append(cascade.certify('cohort_family', 0.1))                         # stale binding
        _write(self.path, [{**_route_record('cohort_family', None, calibrated=False), 'reason': 'too few'}])
        cases.append(cascade.certify('cohort_family', 0.1))                         # not certifiable
        _write(self.path, [_route_record('cohort_family', 0.9)])
        with patch.dict(os.environ, {'AGERE_CASCADE': 'off'}):
            cases.append(cascade.certify('cohort_family', 0.1))                     # switched off
        for c in cases:
            self.assertEqual((c['status'], c['accept'], c['certified']), ('uncalibrated', True, False))
        self.assertIn('stale', cases[1]['reason'])
        self.assertIsNone(cascade.certify_route({'route': 'hosted_verified_sql'}))  # not a free specialist tier

    def test_measured_but_uncertifiable_tier_defers(self):
        rec = cascade.fit('metric_router', [1.0] * 100, [i % 3 == 0 for i in range(100)], grid=[1.0])  # 34% wrong
        self.assertEqual((rec['calibrated'], rec['decision']), (False, 'defer'))
        _write(self.path, [rec])
        cert = cascade.certify_route({'route': 'metric_router'})
        self.assertEqual((cert['status'], cert['accept']), ('not_certified', False))
        self.assertIn('cannot be certified at alpha 5%', cert['statement'])
        with patch.dict(os.environ, {'AGERE_CASCADE_DEFER_UNCERTIFIED': 'off'}):
            self.assertEqual(cascade.certify_route({'route': 'metric_router'})['status'], 'uncalibrated')

    def test_route_scores(self):
        self.assertEqual(cascade.route_score('semantic_cache', {'semantic_cache': {'similarity': 0.91}}), 0.91)
        typed = {'steps': [{'stage': 'typed_decision_router', 'decision': {'choice': 'renal', 'probabilities': {'renal': 0.97}}}]}
        self.assertEqual(cascade.route_score('compact_retrieval_typed_router', typed), 0.97)
        self.assertEqual(cascade.route_score('metric_router', {}), 1.0)
        self.assertEqual(cascade.route_tier({'route': 'exact_question_cache', 'original_route': 'metric_router'}), 'metric_router')


class TogetherCascadeTests(unittest.TestCase):
    """compose_together with a cohort-family specialist answer: certified -> ships with the certificate; below its
    threshold -> the analysts answer; no thresholds -> exactly today's specialists-first behaviour."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'cascade_thresholds.json'
        self.env = patch.dict(os.environ, {'AGERE_CASCADE_THRESHOLDS': str(self.path), 'AGERE_CASCADE': 'on', 'AGERE_TOGETHER': 'on',
                                           'AGERE_SPECIALISTS_FIRST': 'on', 'AGERE_DEFER_FAST': 'on', 'AGERE_TOGETHER_BUDGET_S': '20',
                                           'AGERE_DISAGREE_POLICY': 'analyst'})
        self.env.start()
        self.analyst_calls = []

    def tearDown(self):
        self.env.stop()
        cascade._CACHE.clear()
        with together._WAREHOUSE_LOCK:
            for wh in together._WAREHOUSES.values():
                wh.close()
            together._WAREHOUSES.clear()
        self.temp.cleanup()

    def fast(self, defaults, definition):
        ans = {**answer('patient_set', patient_ids=['P-1', 'P-2'], count=2), 'confidence': 'unverified',
               'query': 'SELECT patient_id FROM patient'}
        receipt = {'route': 'cohort_family', 'new_paid_api_calls': 0, 'steps': [],
                   'cohort_family': {'family': 'therapy_class_gap', 'defaults': ['d'] * defaults, 'model_filled': [],
                                     'has_definition': definition, 'returns': 'patient_set', 'version': 'x', 'parameters': {},
                                     'statement': 's'}}
        return {'answer': ans, 'graph': {'nodes': [{'id': 'answer', 'operation': 'sql', 'parameters': {}}]}, 'planner_receipt': receipt,
                'planning_economics': {'new_api_estimate_usd': 0.0}, 'new_paid_api_calls': 0, 'route': 'cohort_family'}

    def run_with(self, fast):
        root = tempfile.mkdtemp(dir=self.temp.name)
        stack = FakeStack(root, fast=fast)

        def analyst(stack, question, as_of, semantic_path, model, answer_type=None):
            self.analyst_calls.append(question)
            return {'answer': answer('patient_set', patient_ids=['P-1'], count=1), 'model': model, 'estimated_cost_usd': 0.01,
                    'new_paid_api_calls': 1, 'tool_calls': [], 'receipts': [], 'seconds': 0.1}
        with patch.object(together, '_run_analyst', analyst), patch.object(together, '_compiled_engine', lambda *a: []), \
                patch.object(together, '_snapshot_as_of', lambda s: '2030-05-27'):
            return together.compose_together(stack, 'Which diabetic patients are not on a statin?', answer_type='patient_set')

    def test_certified_specialist_answers_with_the_certificate_in_its_receipts(self):
        _write(self.path, [_route_record('cohort_family', 0.9)])
        out = self.run_with(self.fast(0, True))
        self.assertEqual(self.analyst_calls, [])
        self.assertEqual(out['answer']['patient_ids'], ['P-1', 'P-2'])
        for block in (out['certified'], out['answer']['certified'], out['planner_receipt']['certified'], out['together']['certified']):
            self.assertEqual({k: block[k] for k in ('tier', 'alpha', 'delta', 'bound', 'n_calibration')},
                             {'tier': 'cohort_family', 'alpha': 0.05, 'delta': 0.10, 'bound': 0.031, 'n_calibration': 400})
        self.assertEqual(out['together']['decision'], 'fast_unchanged')

    def test_specialist_below_its_certified_threshold_defers_to_the_analysts(self):
        _write(self.path, [_route_record('cohort_family', 0.9)])
        out = self.run_with(self.fast(2, False))  # score 0.79 < 0.9
        self.assertEqual(len(self.analyst_calls), 1)
        self.assertTrue(out['together']['deferred_by_certificate'])
        self.assertEqual(out['together']['certificate']['status'], 'below_threshold')
        self.assertNotIn('certified', out)
        self.assertEqual(out['answer']['patient_ids'], ['P-1'])  # the next tier's answer

    def test_missing_thresholds_leave_specialists_first_unchanged(self):
        out = self.run_with(self.fast(2, False))  # no thresholds file
        self.assertEqual(self.analyst_calls, [])
        self.assertEqual(out['answer']['patient_ids'], ['P-1', 'P-2'])
        self.assertNotIn('certified', out)
        self.assertEqual(out['together']['certificate']['status'], 'uncalibrated')
        self.assertEqual(out['together']['decision'], 'fast_unchanged')
        with patch.dict(os.environ, {'AGERE_CASCADE': 'off'}):
            _write(self.path, [_route_record('cohort_family', 0.99)])
            out = self.run_with(self.fast(2, False))
        self.assertEqual(self.analyst_calls, [])
        self.assertNotIn('certified', out)


class TherapyCascadeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'cascade_thresholds.json'
        self.env = patch.dict(os.environ, {'AGERE_CASCADE_THRESHOLDS': str(self.path), 'AGERE_CASCADE': 'on'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        cascade._CACHE.clear()
        self.temp.cleanup()

    def test_missing_thresholds_keep_the_hand_set_values(self):
        self.assertEqual(TB.tier_gate('tier2_classifier', None)[0], None)
        self.assertEqual(TB.tier_gate('tier3_jev', TB.TYPED_ACCEPT)[0], TB.TYPED_ACCEPT)
        certs = TB.certificates()
        self.assertEqual(certs['tier3_jev']['status'], 'uncalibrated')
        self.assertEqual(certs['tier3_jev']['threshold_in_force'], TB.TYPED_ACCEPT)

    def test_certified_typed_tier_uses_its_threshold_and_escalates_below_it(self):
        _write(self.path, [_route_record('tier3_jev', 0.97), _route_record('tier3_perplexity', 0.5)])
        self.assertEqual(TB.tier_gate('tier3_jev', TB.TYPED_ACCEPT)[0], 0.97)
        self.assertEqual(TB.certificates()['tier3_jev']['bound'], 0.031)
        cases = [{'case_id': 'c1', 'drug_surface': 'lisinopril', 'drug_class': 'ace_inhibitor', 'sentence': 'Cough on lisinopril.'}]
        sent = []

        class Factory:
            def __enter__(self_inner):
                def send(provider, body):
                    sent.append(provider)
                    if provider == 'jev':  # 0.9 clears the hand-set 0.80 but not the certified 0.97
                        return {'answers': {'c1': {'choice': 'intolerance_side_effect',
                                                   'probabilities': {'intolerance_side_effect': 0.9, 'other': 0.1}}}}, 0.0, False
                    return {'answers': {'c1': {'choice': 'intolerance_side_effect',
                                               'probabilities': {'intolerance_side_effect': 0.6, 'other': 0.4}}}}, 0.0, False
                return send

            def __exit__(self_inner, *exc):
                return False
        results, receipt = TB.typed_cascade(cases, lambda: Factory())
        self.assertEqual(sent, ['jev', 'perplexity'])
        self.assertEqual(results['c1'][1], 'perplexity')
        self.assertEqual(receipt['thresholds'], {'jev': 0.97, 'perplexity': 0.5})
        self.assertEqual(receipt['certified'], {'jev': True, 'perplexity': True})

    def test_measured_uncertifiable_typed_tier_is_skipped(self):
        jev = cascade.fit('tier3_jev', [0.9] * 60, [i % 2 == 0 for i in range(60)])  # half wrong: measured, not certifiable
        _write(self.path, [jev])
        self.assertEqual(TB.tier_gate('tier3_jev', TB.TYPED_ACCEPT)[1]['status'], 'not_certified')
        sent = []

        class Factory:
            def __enter__(self_inner):
                def send(provider, body):
                    sent.append(provider)
                    if provider == 'claude':
                        return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps({'labels': {'c1': 'no_barrier'}})}]}, 0.0, False
                    return {'answers': {'c1': {'choice': 'no_barrier', 'probabilities': {'no_barrier': 0.6, 'other': 0.4}}}}, 0.0, False
                return send

            def __exit__(self_inner, *exc):
                return False
        cases = [{'case_id': 'c1', 'drug_surface': 'lisinopril', 'drug_class': 'ace_inhibitor', 'sentence': 'Denies cough on lisinopril.'}]
        results, receipt = TB.typed_cascade(cases, lambda: Factory())
        self.assertEqual(sent, ['perplexity', 'claude'])
        self.assertEqual(receipt['skipped'], ['jev'])
        self.assertEqual(results['c1'][:2], ('no_barrier', 'claude'))


if __name__ == '__main__':
    unittest.main()


class GatePolicyHonorsCertificates(unittest.TestCase):
    def test_certified_tier_is_verified_without_a_cross_check(self):
        from unittest.mock import patch
        from clinical_core.agere.clinical import gate_policy, cascade
        cert = {'tier': 'cohort_family', 'certified': True, 'accept': True, 'status': 'certified', 'score': 0.9,
                'threshold': 0.71, 'statement': 'certified'}
        with patch.object(cascade, 'certify_route', return_value=cert):
            out = gate_policy.assess({'route': 'cohort_family'})
        self.assertEqual(out['level'], 'verified')
        with patch.object(cascade, 'certify_route', return_value={**cert, 'certified': False}):
            self.assertNotEqual(gate_policy.assess({'route': 'cohort_family'})['level'], 'verified')
