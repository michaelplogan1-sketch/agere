"""'Medicare' includes dual-eligible and Medigap members by default; 'traditional Medicare' narrows (Mike 2026-10-23)."""
import unittest

from clinical_core.agere.clinical import decision_context as D


class Medicare(unittest.TestCase):
    def test_default_includes_dual_and_medigap(self):
        self.assertEqual(D.coverage_values('medicare', 'How many patients on Medicare have heart failure?'), ('medicare', 'dual', 'medigap'))

    def test_traditional_narrows(self):
        for q in ('How many traditional Medicare patients have HF?', 'Patients in fee-for-service Medicare', 'Medicare only members'):
            self.assertEqual(D.coverage_values('medicare', q), ('medicare',), q)

    def test_other_coverage_unchanged(self):
        self.assertEqual(D.coverage_values('medicaid', 'on Medicaid'), ('medicaid',))

    def test_prompt_states_it(self):
        self.assertIn("'Medicare' means coverage_type IN ('medicare', 'dual', 'medigap')", D.prompt_block())


if __name__ == '__main__':
    unittest.main()
