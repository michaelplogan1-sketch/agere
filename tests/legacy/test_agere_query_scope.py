import unittest
from clinical_core.agere.query_scope import scope_query, SCREENS

class QueryScopeTest(unittest.TestCase):
    def ask(self, question):
        return scope_query(question, [{'id': x, 'patients': 1} for x in SCREENS],
                           {k: 'Required' for k in ['creatinine_risk','snf_pcp_outcomes','causal_intervention','program_eligibility','network_leakage']})
    def test_named_screen(self):
        for name, _ in SCREENS.values():
            self.assertTrue(self.ask('Show '+name)['question_fully_answered'], name)
    def test_treatment_gap_is_not_low_ef_text(self):
        r=self.ask('Find HFrEF patients not on GDMT or max tolerated dose')
        self.assertEqual(r['status'],'partial_screening_results')
        self.assertIn('medication_appropriateness',r['missing_inputs'])
    def test_compound_request_preserves_unanswered_part(self):
        r=self.ask('Show financial assistance and explain why patients were readmitted')
        self.assertFalse(r['question_fully_answered']);self.assertIn('causal_intervention',r['missing_inputs'])
    def test_missing_is_not_zero(self):
        r=self.ask('Who has rising creatinine?')
        self.assertEqual(r['status'],'requires_explicit_operator_and_inputs');self.assertIn('creatinine_risk',r['missing_inputs'])
    def test_exclusion_not_silently_executed(self):
        self.assertFalse(self.ask('Find CKD patients without nephrology visits')['question_fully_answered'])
    def test_unrecognized_language_not_claimed_answered(self):
        self.assertFalse(self.ask('Patients with kidney disease under 40 at clinic A')['question_fully_answered'])
    def test_empty_and_oversized(self):
        for q in ['', 'a'*2001, None]:
            with self.assertRaises(ValueError):self.ask(q)
    def test_no_cards_still_named_screen(self):
        r=scope_query('show declining egfr',[],{})
        self.assertTrue(r['question_fully_answered']);self.assertEqual(r['cards'],[])

if __name__=='__main__':unittest.main()
