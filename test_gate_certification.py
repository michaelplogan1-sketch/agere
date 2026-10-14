"""Gate certification and the meaning of confidence 'verified'.

gate_policy: an answer is 'verified' only when every gate on its path has a Learn-then-Test certificate bound to the
current scorer/code and passed; 'low' when a check on the path failed; 'unverified' otherwise. tools/certify_gates.py:
calibration only from non-blind saved data (heldout* splits are blind-test only), deterministic 70/30 split, refutation
by held-back data, and an explicit 'labelled cases needed' when a gate cannot be certified. Fixtures only; no model calls."""
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
from clinical_core.agere.clinical import calibrated_thresholds as CT, gate_policy as GP, metric_router as MR  # noqa: E402
import certify_gates as CG  # noqa: E402

ANSWER = {'status': 'answered', 'answer_type': 'count', 'count': 7, 'missing': [], 'grounding': [], 'unknown_count': 0}


def certified(name, bind=None, score=1.0, n=200):
    return CT.fit(name, [score] * n, [False] * n, GP.GATES[name][1], {'source': 'fixture'}, grid=[score],
                  bind=GP.identity(name) if bind is None else bind)


class Thresholds:
    """Context manager: a thresholds file holding the given records."""
    def __init__(self, records):
        self.records = records

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        target = Path(self.tmp.name) / 't.json'
        CT.write(self.records, target)
        self.env = mock.patch.dict(os.environ, {'AGERE_THRESHOLDS_FILE': str(target)})
        self.env.start()
        return target

    def __exit__(self, *exc):
        self.env.stop(); self.tmp.cleanup()


class GatePaths(unittest.TestCase):
    def test_paths_per_route(self):
        names = lambda r: [g['gate'] for g in GP.path_gates(r)]
        self.assertEqual(names({'route': 'local_compiler'}), ['local_compiler.operation', 'local_compiler.answer'])
        self.assertEqual(names({'route': 'metric_router'}), ['metric_router.answer'])
        self.assertEqual(names({'route': 'semantic_cache', 'semantic_cache': {'similarity': 0.9}}),
                         ['semantic_cache.similarity', 'learned_reuse.answer'])
        op = {'route': 'operator_router', 'local_compiler_declined': {'operator_router': {
            'chosen_by': 'learned_router', 'learned_router': {'used': True, 'probability': 0.97}}}}
        self.assertEqual(names(op), ['learned_router', 'operator_router.choice', 'operator_router.answer'])
        hosted = {'route': 'hosted_verified_sql', 'acceptance': {'status': 'verified'},
                  'hosted_sql': {'agreement': 'decider_A', 'decider': {'choice': 'A', 'probability': 0.91}}}
        self.assertEqual(names(hosted), ['nl2sql.decider', 'freeform_sql.verified'])
        cached = {'route': 'exact_question_cache', 'prior_planning_receipt': {'route': 'local_compiler'}}
        self.assertEqual(names(cached), ['local_compiler.operation', 'local_compiler.answer'])
        self.assertIsNone(GP.path_gates({'route': 'something_new'}))

    def test_every_path_gate_is_registered(self):
        for r in ({'route': x} for x in ('local_compiler', 'local_model_rewrite_compiled', 'learned_verified_sql', 'semantic_cache',
                                         'bottled_sql', 'metric_router', 'operator_router', 'hosted_verified_sql',
                                         'compact_retrieval_typed_router', 'frontier_escalation')):
            for g in GP.path_gates(r): self.assertIn(g['gate'], GP.GATES)


class ConfidenceRule(unittest.TestCase):
    LOCAL = {'route': 'local_compiler'}

    def test_verified_needs_every_gate_certified_and_passed(self):
        with Thresholds([certified('local_compiler.operation'), certified('local_compiler.answer')]):
            v = GP.assess(self.LOCAL)
            self.assertEqual(v['level'], 'verified')
            self.assertTrue(all(g['certified'] and g['passed'] for g in v['gates']))
            out = MR.finalize_answer(ANSWER, self.LOCAL)
            self.assertEqual(out['confidence'], 'verified')
            self.assertEqual(len(out['confidence_detail']['gates']), 2)

    def test_one_uncertified_gate_makes_it_unverified(self):
        with Thresholds([certified('local_compiler.operation')]):
            v = GP.assess(self.LOCAL)
            self.assertEqual(v['level'], 'unverified')
            self.assertIn('local_compiler.answer', v['reason'])
            out = MR.finalize_answer(ANSWER, self.LOCAL)
            self.assertEqual((out['confidence'], out['status'], out['count']), ('unverified', 'answered', 7))
            self.assertTrue(any(c['control'] == 'QC-CONFIDENCE' and c['severity'] == 'info' for c in out['quality_checks']))

    def test_certificate_for_other_code_is_void(self):
        with Thresholds([certified('local_compiler.operation'), certified('local_compiler.answer', bind='older-code')]):
            v = GP.assess(self.LOCAL)
        self.assertEqual(v['level'], 'unverified')
        self.assertIn('different scorer', [g for g in v['gates'] if g['gate'] == 'local_compiler.answer'][0]['why_uncertified'])

    def test_failed_check_is_low(self):
        enc = type('E', (), {'identity': 'enc-1'})()
        with Thresholds([certified('semantic_cache.similarity', bind='enc-1', score=0.9), certified('learned_reuse.answer')]):
            ok = GP.assess({'route': 'semantic_cache', 'semantic_cache': {'similarity': 0.95}}, encoder=enc)
            below = GP.assess({'route': 'semantic_cache', 'semantic_cache': {'similarity': 0.85}}, encoder=enc)
        self.assertEqual(ok['level'], 'verified')
        self.assertEqual(below['level'], 'low')
        out = MR.finalize_answer(ANSWER, {'route': 'hosted_verified_sql', 'acceptance': {'status': 'low_confidence', 'basis': 'judge'}})
        self.assertEqual(out['confidence'], 'low')

    def test_unknown_path_and_abstentions(self):
        self.assertEqual(GP.assess({'route': 'new_route'})['level'], 'unverified')
        unsupported = {**ANSWER, 'status': 'unsupported', 'answer_type': 'unsupported', 'count': None}
        self.assertNotIn('confidence', MR.finalize_answer(unsupported, self.LOCAL))

    def test_committed_certificates_leave_no_answer_path_verified_yet(self):
        # With the committed calibrated_thresholds.json: some gates are certified, but every answering route still has an
        # uncertified end-to-end gate, so nothing is labelled verified until the labelled data exist.
        with mock.patch.dict(os.environ, {'AGERE_THRESHOLDS_FILE': str(CT.FILE)}):
            for route in ('local_compiler', 'metric_router', 'operator_router', 'hosted_verified_sql'):
                receipt = {'route': route, 'acceptance': {'status': 'verified'}}
                self.assertNotEqual(GP.assess(receipt)['level'], 'verified', route)


class Certification(unittest.TestCase):
    def test_deterministic_split_and_bounds(self):
        self.assertEqual(CG.part('how many patients have diabetes'), CG.part('how many patients have diabetes'))
        share = sum(CG.part(f'q{i}') == 'cal' for i in range(2000)) / 2000
        self.assertAlmostEqual(share, CG.CAL_SHARE, delta=0.04)
        self.assertEqual(CG.accepted_needed(0.0, 0.02, 0.1), 114)
        self.assertIsNone(CG.accepted_needed(0.03, 0.02, 0.1))
        self.assertGreater(CG.accepted_needed(0.01, 0.02, 0.1), 114)
        self.assertAlmostEqual(CG.upper_bound(0, 114, 0.1), 0.02, delta=0.001)

    def test_certify_route_gate_and_refutation(self):
        cases = [{'key': f'q{i}', 'score': 1.0, 'error': False} for i in range(400)]
        rec = CG.certify('local_compiler.answer', cases, bind='b', fallback=1.0, data={'source': 'fixture'})
        self.assertTrue(rec['calibrated']); self.assertEqual(rec['threshold'], 1.0)
        self.assertEqual(rec['data']['calibration'] + rec['data']['test'], 400)
        blind = [{'key': f'b{i}', 'score': 1.0, 'error': i < 10} for i in range(40)]  # 25% wrong on a blind set
        refuted = CG.certify('local_compiler.answer', cases, blind, bind='b', fallback=1.0, data={'source': 'fixture'})
        self.assertFalse(refuted['calibrated']); self.assertIsNone(refuted['threshold'])
        self.assertIn('blind test refutes', refuted['reason'])

    def test_uncertifiable_gate_states_what_it_needs(self):
        few = [{'key': f'q{i}', 'score': 1.0, 'error': False} for i in range(50)]
        rec = CG.certify('metric_router.answer', few, bind='b', fallback=1.0, data={'source': 'fixture'})
        self.assertFalse(rec['calibrated'])
        self.assertEqual(rec['accepted_needed'], 114); self.assertGreater(rec['labelled_needed'], 50)
        noisy = [{'key': f'q{i}', 'score': 1.0, 'error': i % 10 == 0} for i in range(300)]  # 10% wrong
        rec = CG.certify('metric_router.answer', noisy, bind='b', fallback=1.0, data={'source': 'fixture'})
        self.assertIsNone(rec['labelled_needed']); self.assertIn('must fall below 2%', rec['needs'])

    def test_score_gate_threshold_search_ignores_floor_violations(self):
        cases = [{'key': f'q{i}', 'score': 0.95, 'error': False} for i in range(300)] + \
                [{'key': f'n{i}', 'score': 0.5, 'error': True} for i in range(300)]
        rec = CG.certify('semantic_cache.similarity', cases, bind='enc', fallback=0.85, floor=0.8, data={'source': 'fixture'})
        self.assertTrue(rec['calibrated']); self.assertEqual(rec['threshold'], 0.95)

    def test_blind_sets_never_calibrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = Path(tmp)
            (res / 'agere').mkdir(); (res / 'adjudications').mkdir()
            def row(i, split, q, correct, started):
                return {'id': ('D' if split == 'development' else 'H') + str(i), 'system': 'agere', 'question': q, 'split': split,
                        'started_at': started, 'score': {'correct': correct},
                        'answer': {'status': 'answered', 'answer_type': 'count', 'count': 1},
                        'expected': {'answer_type': 'count'}, 'response': {'planner_receipt': {'route': 'local_compiler'}}}
            dev = [row(i, 'development', f'dev question {i}', True, '2026-10-01T00:00:00') for i in range(5)]
            dev.append(row(9, 'development', 'shared question', True, '2026-10-01T00:00:00'))
            blind1 = [row(i, 'heldout9', f'blind question {i}', i > 2, '2026-10-02T00:00:00') for i in range(4)]
            blind1.append(row(9, 'heldout9', 'Shared question?', False, '2026-10-02T00:00:00'))
            blind2 = [row(i, 'heldout9', f'blind question {i}', True, '2026-10-03T00:00:00') for i in range(4)]  # a later rerun
            for name, rows in (('a.jsonl', dev), ('b.jsonl', blind1), ('c.jsonl', blind2)):
                (res / 'agere' / name).write_text('\n'.join(json.dumps(r) for r in rows))
            pool, blind, blind_q = CG.bench_rows(res)
        self.assertEqual(sorted(r['question'] for r in pool), [f'dev question {i}' for i in range(5)])  # shared text dropped
        self.assertEqual(len(blind), 5)  # first blind run only
        self.assertEqual(sum(r['wrong'] for r in blind), 4)
        cases = CG.route_cases(pool)
        self.assertEqual(len(cases['local_compiler.answer']), 5)


if __name__ == '__main__':
    unittest.main()
