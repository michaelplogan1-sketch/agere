import unittest
from unittest.mock import patch
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from clinical_core.agere.clinical.relation_learning import source_window, groups_for, distribution, feature
from clinical_core.agere.clinical.relation_learning import label_batch, fit


class RelationLearningTests(unittest.TestCase):
    def test_quote_mismatch_fails_closed(self):
        with self.assertRaises(ValueError):
            source_window('No metoprolol today.', {'start': 3, 'end': 13, 'quote': 'carvedilol'})

    def test_wider_window_keeps_negation_and_exact_offsets(self):
        text = 'Patient reports: NOT TAKING metoprolol. Old list still copied.'
        a = text.index('metoprolol'); b = a+len('metoprolol')
        w = source_window(text, {'start': a, 'end': b, 'quote': 'metoprolol',
            'context_start': a, 'context_end': b, 'context': 'metoprolol'})
        self.assertIn('NOT TAKING', w['text'])
        self.assertEqual(w['text'][w['target_start']:w['target_end']], 'metoprolol')

    def test_dates_doses_and_drug_variants_stay_together(self):
        rows = [{'central_text': t} for t in ['Medication order: metoprolol succinate 50 mg daily',
                  'Medication order: carvedilol 25 mg daily', 'Medication order: bisoprolol 5 mg daily']]
        self.assertEqual(len(set(groups_for(rows))), 1)

    def test_invalid_distribution_rejected(self):
        for probs in ({'a': .3, 'b': .3}, {'a': float('nan'), 'b': .5}, {'a': -.1, 'b': 1.1}):
            with self.assertRaises(ValueError):
                distribution({'choice': 'a', 'probabilities': probs}, {'a': '', 'b': ''})
        with self.assertRaises(ValueError):
            distribution({'choice': 'a', 'probabilities': {'a': .1, 'b': .9}}, {'a': '', 'b': ''})

    def test_features_exclude_labels_and_identity(self):
        row = {'input': {'concept': 'medication', 'target_quote': 'Drug', 'text': 'Drug prescribed.', 'target_start': 0, 'target_end': 4},
               'patient_id': 'SECRET_ID', 'split': 'test', 'label': 'prescription_order'}
        self.assertEqual(feature(row), 'medication [CONTEXT]  [TARGET] Drug [/TARGET]  prescribed.')
        self.assertNotIn('SECRET_ID', feature(row))

    def test_teachers_cannot_see_supervision_or_patient_id(self):
        row = {'example_id': 'example-id', 'synthetic': True,
               'patient_id': 'NEVER_SEND_ID', 'split': 'NEVER_SEND_SPLIT', 'rule_label': 'NEVER_SEND_RULE',
               'input': {'concept': 'medication', 'text': 'Drug prescribed.', 'target_quote': 'Drug',
                         'target_start': 0, 'target_end': 4}}
        with patch('clinical_core.agere.clinical.relation_learning.send', return_value=([], {})) as send:
            label_batch([row], 'perplexity', None, Path('.'))
        body = send.call_args.args[0]
        text = json.dumps(body)
        for secret in ('NEVER_SEND_ID', 'NEVER_SEND_SPLIT', 'NEVER_SEND_RULE'):
            self.assertNotIn(secret, text)
        self.assertEqual(set(body['questions']), {'case0_assertion', 'case0_relation'})

    def test_non_synthetic_labeling_fails_before_network(self):
        with patch('clinical_core.agere.clinical.relation_learning.send') as send:
            with self.assertRaises(ValueError):
                label_batch([{'synthetic': False}], 'jev', None, Path('.'))
            send.assert_not_called()

    def test_template_leakage_stops_training(self):
        rows = [{'example_id': str(i), 'split': s, 'template_group': 'same'}
                for i, s in enumerate(('train', 'test'))]
        with TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'Template leakage'):
                fit(rows, {'0': {}, '1': {}}, Path(d), Path(d), neural=False)


if __name__ == '__main__':
    unittest.main()
