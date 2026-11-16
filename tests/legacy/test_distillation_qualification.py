import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical import distillation_qualification as q


class DesignQualificationTests(unittest.TestCase):
    def rows(self,n):
        return [{'partition':part,'patient_family':str(i)} for part in ('train','calibration','validation') for i in range(n)]

    def test_small_fixed_sample_cannot_pass_even_with_perfect_predictions(self):
        r=q.assess(self.rows(23),[],q.student.POLICY)
        self.assertFalse(r['structural_minimums_met'])
        self.assertEqual({s['code'] for s in r['structural_failures']},{'insufficient_calibration_targets','insufficient_calibration_error_bound','insufficient_validation_error_bound'})
        self.assertFalse(r['fit_performed']);self.assertFalse(r['model_qualified'])

    def test_enough_rows_but_not_enough_families_is_still_unqualified(self):
        rows=self.rows(40)
        for r in rows:r['patient_family']='one'
        r=q.assess(rows,[],q.student.POLICY)
        self.assertFalse(r['structural_minimums_met'])
        self.assertTrue(all(s['code'].endswith('families') for s in r['structural_failures']))

    def test_minimums_met_does_not_claim_quality_or_invent_labels(self):
        r=q.assess(self.rows(40),[{'reason':'no_marked_target_not_a_retrieval_miss'}],q.student.POLICY)
        self.assertTrue(r['structural_minimums_met']);self.assertFalse(r['model_qualified']);self.assertFalse(r['automatic_acceptance'])
        self.assertEqual(r['unassessed_fields'],1)
        self.assertIn('Reviewed label quality',r['unmeasured'])

    def test_thirty_perfect_rows_still_cannot_meet_existing_error_bound(self):
        r=q.assess(self.rows(30),[],q.student.POLICY)
        self.assertEqual({s['minimum'] for s in r['structural_failures']},{35})
        self.assertEqual({s['code'] for s in r['structural_failures']},{'insufficient_calibration_error_bound','insufficient_validation_error_bound'})

    def test_stale_plan_fails_closed_and_missing_plan_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(q.inspect(tmp)['status'],'not_attached')
            (Path(tmp)/'source-distillation-link.json').write_text('{}')
            with patch.object(q.distillation,'connected',side_effect=ValueError('Changed source')):
                with self.assertRaisesRegex(ValueError,'Changed source'):q.inspect(tmp)


if __name__=='__main__':unittest.main()
