"""Authored calibration/label regressions; no saved question banks or inference."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import cascade as C
from tools import calibrate_cascades as K


class QualificationIntegrity(unittest.TestCase):
    def setUp(self):
        root = K.ROOT / 'artifacts' / 'qualification-authored-tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def groups(self, split, n):
        return [f'authored-independent-definition-{i}' for i in range(n * 10 + 100)
                if K.part(f'authored-independent-definition-{i}') == split][:n]

    def route(self, group, item, *, error=False, blind=False):
        return {'tier': 'cohort_family', 'group': group, 'item': item, 'score': 1.0,
                'error': error, 'source': 'authored-only', 'blind': blind}

    def fit(self, rows):
        route = self.path / 'routes.jsonl'
        route.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        with patch.object(K, 'ROUTE_SET', route), patch.object(K, 'THERAPY_LABELS', self.path / 'absent.jsonl'), \
                patch.object(K, 'therapy_rows', return_value=[]), patch.object(K, 'therapy_report', return_value=None), \
                patch.object(C, 'ROUTE_TIERS', ('cohort_family',)), patch.object(C, 'THERAPY_TIERS', ()), \
                contextlib.redirect_stdout(io.StringIO()):
            return K.fit_all()[0]

    def labels(self, rows):
        path = self.path / 'labels.jsonl'
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        return path

    def case(self, item, reference, prediction, *, probability=0.999):
        return {'case_id': item, 'tier': 3, 'decided_by': 'jev',
                'labels': {'claude': {'label': reference},
                           'jev': {'label': prediction, 'probability': probability}}}

    def test_wording_repetition_cannot_manufacture_independent_sample_size(self):
        group = self.groups('cal', 1)[0]
        rec = self.fit([self.route(group, f'wording-{i}') for i in range(45)])
        self.assertEqual(rec['n_calibration'], 1)
        self.assertFalse(rec['calibrated'])

    def test_distinct_group_positive_control_still_certifies(self):
        rows = [self.route(g, f'item-{i}') for i, g in enumerate(self.groups('cal', 45))]
        rec = self.fit(rows)
        self.assertTrue(rec['calibrated'])
        self.assertEqual(rec['n_accepted'], 45)
        self.assertLessEqual(rec['bound'], 0.05)

    def test_representative_does_not_depend_on_score_error_or_row_order(self):
        rows = [self.route('one-source-family', f'wording-{i}') for i in range(60)]
        selected = K.independent_rows(rows)[0]['item']
        changed = [{**r, 'score': i / 100, 'error': i % 2 == 0} for i, r in enumerate(rows)]
        self.assertEqual(K.independent_rows(list(reversed(changed)))[0]['item'], selected)
        self.assertEqual(K.independent_rows(rows * 3)[0]['item'], selected)

    def test_all_variant_errors_remain_visible_in_row_audit(self):
        group = self.groups('cal', 1)[0]
        rec = self.fit([self.route(group, f'wording-{i}', error=i < 3) for i in range(45)])
        self.assertEqual((rec['data']['raw_rows'], rec['data']['raw_errors']), (45, 3))
        self.assertIn('representative', rec['sampling'])

    def test_repeated_evaluation_group_cannot_inflate_refutation(self):
        cal = [self.route(g, f'cal-{i}') for i, g in enumerate(self.groups('cal', 45))]
        groups = self.groups('test', 45)
        test = [self.route(g, f'eval-{i}') for i, g in enumerate(groups[:-1])]
        test += [self.route(groups[-1], f'repeated-wrong-{i}', error=True) for i in range(100)]
        rec = self.fit(cal + test)
        self.assertTrue(rec['calibrated'])
        self.assertEqual((rec['heldout']['n'], rec['heldout']['errors_accepted']), (45, 1))

    def test_blind_member_keeps_entire_group_out_of_calibration(self):
        groups = self.groups('cal', 45)
        rows = [self.route(g, f'cal-{i}') for i, g in enumerate(groups)]
        rows.append(self.route(groups[0], 'blind-wording', blind=True))
        rec = self.fit(rows)
        self.assertEqual((rec['n_calibration'], rec['blind_test']['n']), (44, 1))
        self.assertFalse(rec['calibrated'])

    def test_conflicting_duplicate_observation_is_not_optimistically_selected(self):
        row = self.route('source-a', 'wording-a')
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            K.independent_rows([row, {**row, 'error': True}])

    def test_missing_group_cannot_claim_independent_evidence(self):
        with self.assertRaisesRegex(ValueError, 'independent source group'):
            K.independent_rows([{**self.route('source-a', 'a'), 'group': ''}])

    def test_unknown_reference_is_not_negative_supervision(self):
        path = self.labels([self.case('unresolved', 'unknown', 'no_barrier'),
                            self.case('explicit-negative', 'no_barrier', 'no_barrier'),
                            self.case('documented-access', 'cost_access', 'no_barrier')])
        rows = K.therapy_rows(path)
        self.assertEqual([r['item'] for r in rows], ['explicit-negative', 'documented-access'])
        self.assertEqual([r['error'] for r in rows], [False, True])

    def test_only_unresolved_references_cannot_create_zero_error_certificate(self):
        path = self.labels([self.case(f'unknown-{i}', 'unknown', 'no_barrier') for i in range(45)])
        rows = K.therapy_rows(path)
        rec = C.fit('tier3_jev', [r['score'] for r in rows], [r['error'] for r in rows], grid=[0.999])
        self.assertFalse(rec['calibrated'])
        self.assertEqual(rec['n_calibration'], 0)

    def test_student_unknown_remains_abstention_with_a_known_negative_reference(self):
        rows = K.therapy_rows(self.labels([self.case('declined', 'no_barrier', 'unknown')]))
        self.assertEqual(rows[0]['score'], -1)
        self.assertTrue(rows[0]['error'])
        self.assertEqual(C.evaluate([rows[0]['score']], [rows[0]['error']], 0.8)['n_accepted'], 0)

    def test_reference_exclusions_are_counted_separately(self):
        path = self.labels([self.case('missing', None, 'no_barrier'),
                            self.case('unknown', 'unknown', 'no_barrier')])
        audit = {}
        self.assertEqual(K.therapy_rows(path, audit=audit), [])
        self.assertEqual(audit, {'missing_reference': 1, 'unresolved_reference': 1})

    def test_report_does_not_dilute_error_rate_with_unresolved_references(self):
        path = self.labels([self.case('unresolved', 'unknown', 'no_barrier'),
                            self.case('wrong', 'cost_access', 'no_barrier')])
        with patch.object(K, 'THERAPY_LABELS', path):
            report = K.therapy_report([])
        stats = report['tiers']['tier3_jev']
        self.assertEqual((stats['decided'], stats['reference_compared'], stats['unresolved_reference']), (2, 1, 1))
        self.assertEqual(stats['error_vs_claude'], 1.0)

    def test_unresolved_fallback_is_not_reported_as_a_correct_answer(self):
        path = self.labels([self.case('unresolved', 'unknown', 'unknown')])
        with patch.object(K, 'THERAPY_LABELS', path):
            stats = K.therapy_report([])['tiers']['tier3_claude']
        self.assertEqual((stats['decided'], stats['abstained']), (0, 1))
        self.assertIsNone(stats['error_vs_claude'])

    def test_policy_change_invalidates_old_certificate_binding(self):
        root = self.path / 'code'
        here = root / 'clinical_core/agere/clinical'
        here.mkdir(parents=True)
        (root / 'tools').mkdir()
        for name in ('cohort_families.py', 'cascade.py', 'conformal.py'):
            (here / name).write_text('# authored fixture\n')
        tool = root / 'tools/calibrate_cascades.py'
        tool.write_text('# policy one\n')
        with patch.object(C, 'ROOT', root), patch.object(C, 'HERE', here), patch.dict(C._DIGESTS, {}, clear=True):
            before = C.code_digest('cohort_family')
            tool.write_text('# changed calibration policy, same route\n')
            after = C.code_digest('cohort_family')
        self.assertNotEqual(before, after)


if __name__ == '__main__':
    unittest.main()
