"""Offline receipt tests. All data below is authored here; no question banks or providers."""
import copy
import unittest
from tools import diagnostic_cycle as D


def receipt():
    manifest = {k: 'a' * (40 if k == 'candidate_sha' else 64) for k in D.FROZEN}
    manifest.update(as_of='2030-05-27', cache_policy='disabled', run_id='synthetic-unit', transport='fake')
    arms = {a: {'status': 'completed', 'correct': True, 'answered': True, 'adjudicator': 'independent-review',
                'model': 'fake', 'transport': 'fake', 'output_sha256': 'b' * 64, 'seconds': 1, 'cost_usd': 0}
            for a in D.ARMS}
    return {'schema': D.VERSION, 'manifest': manifest, 'cases': [{'id': 'authored-1', 'origin': 'authored', 'bucket': 'notes',
            'reference_audit': {'state': 'verified', 'evidence_sufficient': True, 'reviewer': 'source-review',
                                'basis': 'Author-written exact source sentence'}, 'arms': arms}]}


class DiagnosticCycle(unittest.TestCase):
    def test_all_correct(self):
        r = D.report(receipt())
        self.assertEqual(r['class_counts'], {'all_correct': 1})
        self.assertEqual(r['arms']['full_stack']['overall_correct_fraction'], 1)

    def test_failure_attributions(self):
        for verdicts, expected in [((False, False, False), 'evidence_reading_or_reasoning'),
                                   ((True, False, False), 'retrieval_tools_or_planning'),
                                   ((True, True, False), 'cascade_or_acceptance'),
                                   ((False, True, True), 'non_monotonic_requires_repetition')]:
            with self.subTest(verdicts=verdicts):
                r = receipt()
                for a, v in zip(D.ARMS, verdicts): r['cases'][0]['arms'][a]['correct'] = v
                self.assertEqual(D.report(r)['case_classes'][0]['class'], expected)

    def test_outage_is_not_reasoning_failure_or_accuracy(self):
        r = receipt(); a = r['cases'][0]['arms']['investigator']; a.update(status='provider_error', correct=None, cost_usd=None)
        out = D.report(r)
        self.assertEqual(out['case_classes'][0]['class'], 'infrastructure_confounded')
        self.assertIsNone(out['arms']['investigator']['overall_correct_fraction'])
        self.assertIsNone(out['arms']['investigator']['cost_per_correct_usd'])

    def test_invalid_reference_excluded_from_accuracy(self):
        r = receipt(); r['cases'][0]['reference_audit'].update(state='invalid', issue='reference_wrong')
        out = D.report(r)
        self.assertEqual(out['verified_reference_cases'], 0)
        self.assertIsNone(out['arms']['full_stack']['overall_correct_fraction'])

    def test_warehouse_and_key_errors_are_distinct(self):
        for issue, label in [('warehouse_inconsistent', 'warehouse_inconsistent'), ('reference_wrong', 'reference_key_wrong')]:
            r = receipt(); r['cases'][0]['reference_audit'].update(state='invalid', issue=issue)
            self.assertEqual(D.report(r)['case_classes'][0]['class'], label)

    def test_unresolved_reference_does_not_become_a_stack_error(self):
        r = receipt(); r['cases'][0]['reference_audit']['state'] = 'unresolved'
        self.assertEqual(D.report(r)['case_classes'][0]['class'], 'reference_unresolved')

    def test_missing_cost_not_silently_zero(self):
        r = receipt(); r['cases'][0]['arms']['full_stack']['cost_usd'] = None
        out = D.report(r)['arms']['full_stack']
        self.assertEqual(out['missing_cost_rows'], 1); self.assertIsNone(out['cost_per_correct_usd'])

    def test_every_planned_arm_must_be_represented(self):
        r = receipt(); del r['cases'][0]['arms']['full_stack']
        with self.assertRaises(ValueError): D.report(r)

    def test_not_run_is_incomplete(self):
        r = receipt(); r['cases'][0]['arms']['full_stack'] = {'status': 'not_run', 'correct': None}
        out = D.report(r)
        self.assertEqual(out['case_classes'][0]['class'], 'incomplete_or_unadjudicated')
        self.assertIsNone(out['arms']['full_stack']['overall_correct_fraction'])

    def test_no_closed_evaluation_diagnosis(self):
        for origin in ('heldout', 'check', 'dev3_check', None):
            r = receipt(); r['cases'][0]['origin'] = origin
            with self.assertRaises(ValueError): D.report(r)

    def test_mixed_transport_rejected(self):
        r = receipt(); r['cases'][0]['arms']['full_stack']['transport'] = 'other'
        with self.assertRaises(ValueError): D.report(r)

    def test_unpinned_candidate_rejected(self):
        r = receipt(); r['manifest']['candidate_sha'] = 'HEAD'
        with self.assertRaises(ValueError): D.report(r)

    def test_nonfinite_or_negative_telemetry_rejected(self):
        for v in (float('nan'), float('inf'), -1, True):
            r = receipt(); r['cases'][0]['arms']['exact_evidence']['seconds'] = v
            with self.assertRaises(ValueError): D.report(r)

    def test_provider_error_cannot_carry_wrong_verdict(self):
        r = receipt(); r['cases'][0]['arms']['investigator']['status'] = 'provider_error'
        with self.assertRaises(ValueError): D.report(r)

    def test_source_review_must_attest_evidence_sufficiency(self):
        r = receipt(); r['cases'][0]['reference_audit']['evidence_sufficient'] = False
        with self.assertRaises(ValueError): D.report(r)

    def test_duplicate_cases_rejected(self):
        r = receipt(); r['cases'].append(copy.deepcopy(r['cases'][0]))
        with self.assertRaises(ValueError): D.report(r)

    def test_adjudicator_required_for_a_verdict(self):
        r = receipt(); del r['cases'][0]['arms']['investigator']['adjudicator']
        with self.assertRaises(ValueError): D.report(r)

    def test_bucket_stats_and_absence_not_precision(self):
        r = receipt(); r['cases'][0]['arms']['full_stack']['answered'] = False
        out = D.report(r)['buckets']['notes']['full_stack']
        self.assertEqual(out['correct'], 1)
        self.assertIsNone(out['precision_on_adjudicated_answers'])


if __name__ == '__main__': unittest.main()
