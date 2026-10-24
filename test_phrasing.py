"""phrasing.py: general rewording rules (tables) and the meaning guard. No stack, no model."""
import unittest

from clinical_core.agere.clinical import phrasing


def one(q):
    got = phrasing.variants(q)
    return got[0] if got else None


class Rules(unittest.TestCase):
    def test_count_leads(self):
        self.assertEqual(one('Number of ED visits in 2029?'), 'How many ED visits in 2029?')
        self.assertEqual(one('Count of pts with diabetes who didn\'t get an A1c in the last year.'),
                         "How many patients with diabetes who haven't had an A1c in the last year.")
        self.assertEqual(one('What number of Medicare members completed their AWV in 2029?'),
                         'How many Medicare members completed their annual wellness visit in 2029?')

    def test_share_leads(self):
        self.assertEqual(one('Percent of adult patients with diabetes whose latest A1c is over 9?'),
                         'What share of adult patients with diabetes whose latest A1c is over 9?')
        self.assertEqual(one('What proportion of patients with COPD had an ED visit in 2029?'),
                         'What share of patients with COPD had an ED visit in 2029?')

    def test_among_and_fronted_time(self):
        self.assertEqual(one('Among asthmatics, how many have had no inhaled steroid fill in the past year?'),
                         'How many asthma patients have no inhaled corticosteroid fill in the past year?')
        self.assertEqual(one('In 2029, how many Medicare patients had an annual wellness visit?'),
                         'How many Medicare patients had an annual wellness visit in 2029?')
        self.assertEqual(one('By line of business, how much did we pay on medical claims in 2029?'),
                         'How much did we pay on medical claims in 2029 by line of business?')

    def test_abbreviations_and_synonyms(self):
        self.assertEqual(one('How many pts on ACEi have HTN?'), 'How many patients on ACE inhibitor have hypertension?')
        self.assertEqual(one('How many scripts were never filled?'), 'How many prescriptions were never filled?')
        self.assertIn('PMPM', one('and per member per month?'))

    def test_negation_and_drug_forms(self):
        self.assertIn('are on an anticoagulant', one('What fraction of AFib patients take an anticoagulant?'))
        self.assertIn('are not on', one('Which diabetics with CKD are on neither an ACE inhibitor nor an ARB?'))
        self.assertIn('are on no GDMT', one('How many HF patients have zero GDMT?'))

    def test_age_and_range_forms(self):
        self.assertIn('65 and older', one('Last season, what share of patients aged 65+ got a flu shot?'))
        self.assertIn('3 or more ED visits', one('List patients with 3+ ED visits in the last year.'))
        self.assertIn('50 to 74', one('mammogram rate for women between 50 and 74 in the last two years'))
        self.assertIn('last two years', one('What share of women 50 to 74 had a mammogram in the last 2 years?'))

    def test_canonical_questions_are_left_alone(self):
        for q in ('How many patients have atrial fibrillation and heart failure?',
                  'Which diabetics with CKD are not on an ACE inhibitor or ARB?',
                  'What is the most recent eGFR on SYN-67436663-A0F?'):
            self.assertIsNone(one(q), q)


class MeaningGuard(unittest.TestCase):
    def test_numbers_ids_and_negation_survive(self):
        for q in ('Number of ED visits for SYN-04894A13-08C over the past 12 months?',
                  'Among our diabetics, how many have gone without an A1c in the past 12 months?',
                  'Count patients with 3+ ED visits and no PCP visit in 2029.',
                  'List the high-cost claimants in 2029, patients with at least $50K paid.'):
            got = one(q)
            if got:
                self.assertTrue(phrasing.safe(q, got), (q, got))

    def test_unsafe_rewrites_are_refused(self):
        self.assertFalse(phrasing.safe('How many have an A1c over 9?', 'How many have an A1c over 8?'))
        self.assertFalse(phrasing.safe('Patients without a statin fill', 'Patients with a statin fill'))
        self.assertFalse(phrasing.safe('Labs for SYN-67436663-A0F', 'Labs for SYN-67436663-A0E'))
        self.assertTrue(phrasing.safe('last 2 years', 'last two years'))
        self.assertTrue(phrasing.safe('$50K or more', '$50,000 or more'))
        self.assertTrue(phrasing.safe('missed 2 visits', 'no-showed 2 times'))

    def test_ids_are_never_touched(self):
        got = one('Latest LDL for P-HF11?')
        self.assertTrue(got is None or 'P-HF11' in got)

    def test_disclosure(self):
        r = {'question': 'x', 'answer': {'limitations': []}}
        phrasing.disclose(r, 'Number of X?', 'How many X?')
        self.assertEqual(r['question'], 'Number of X?')
        self.assertEqual(r['reworded_as'], 'How many X?')
        self.assertIn('Read as', r['answer']['limitations'][-1])


class AbsentDomains(unittest.TestCase):
    def test_revenue_and_mlr_wordings_are_not_available(self):
        import re
        from clinical_core.agere.clinical import data_domains
        pats = [p for label, p, _ in data_domains.DOMAINS if 'revenue' in label]
        self.assertTrue(pats)
        for q in ('How much revenue are we losing from uncaptured HCC codes in risk adjustment?',
                  'Estimate the risk-adjustment revenue left unrealized because of uncaptured HCC diagnoses.',
                  'What is the medical loss ratio for our Medicare Advantage plan?'):
            self.assertTrue(any(re.search(p, q.lower(), re.I) for p in pats), q)


if __name__ == '__main__':
    unittest.main()
