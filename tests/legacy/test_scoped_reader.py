import unittest
from clinical_core.agere.clinical.scoped_reader import scoped_view, noise_variants, consistency_issues


def x(text, target, concept='outside care'):
    a = text.index(target)
    return {'text': text, 'target_quote': target, 'target_start': a, 'target_end': a+len(target), 'concept': concept}


class ScopeTests(unittest.TestCase):
    def test_neighbor_negation_is_not_in_target_clause(self):
        row = x('No current medicines; outside records are incomplete.', 'outside records')
        view = scoped_view(row)
        self.assertNotIn('No current medicines', view['focus'])
        self.assertIn('are incomplete', view['focus'])
        self.assertIn('No current medicines', view['context'])

    def test_target_negation_survives(self):
        row = x('No other issues. No outside records were missing. Next visit planned.', 'outside records')
        self.assertIn('No ', scoped_view(row)['focus'])

    def test_clinical_abbreviation_and_decimal(self):
        row = x('Dr. Quinn reports creatinine 1.8 today. No rash.', 'creatinine', 'lab')
        self.assertIn('Dr. Quinn', scoped_view(row)['trace']['focus_text'])
        self.assertIn('1.8', scoped_view(row)['focus'])
        self.assertNotIn('No rash', scoped_view(row)['focus'])

    def test_drug_name_with_spaced_slash_is_not_split(self):
        row = x('Medication: sacubitril / valsartan listed; no rash.', 'sacubitril / valsartan', 'drug')
        t = scoped_view(row)['trace']
        self.assertLessEqual(t['focus_start'], row['target_start'])
        self.assertGreaterEqual(t['focus_end'], row['target_end'])

    def test_trailing_medication_qualifier_kept(self):
        row = x('Metoprolol listed; current use not verified. Outside records incomplete.', 'Metoprolol', 'medication')
        view = scoped_view(row)
        self.assertIn('current use not verified', view['focus'])
        self.assertNotIn('Outside records', view['focus'])

    def test_prior_section_retained_separately(self):
        row = x('Prior note (2024-01-01):\nHistory:\nCKD documented. No fever.', 'CKD', 'disease')
        v = scoped_view(row)
        self.assertIn('Prior note', v['section'])
        self.assertEqual(v['trace']['focus_text'], 'CKD documented')

    def test_noise_offsets_remain_source_bound(self):
        row = x('Outside records missing.', 'Outside records')
        for variant in noise_variants(row):
            self.assertEqual(variant['text'][variant['target_start']:variant['target_end']], row['target_quote'])
            self.assertEqual(scoped_view(variant)['focus'], scoped_view(row)['focus'])

    def test_metadata_contradiction_is_visible(self):
        axes = {a: {'label': v} for a, v in dict(assertion='affirmed', subject='patient', time='current', relation='document_metadata').items()}
        self.assertTrue(consistency_issues(axes))


if __name__ == '__main__':unittest.main()
