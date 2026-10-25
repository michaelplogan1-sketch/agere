"""Patient membership survives arbitrary atomic answer-column names; no provider calls."""
import copy
import unittest
from clinical_core.agere.clinical import atomic_questions as aq


def report(keys=('patient_id',), labels=('unknown',), aggregation='latest', returns='count'):
    return {'parameters': {'aggregation': aggregation, 'select_labels': ['yes'],
            'sub_questions': [{'key': k, 'subject': '', 'atom': 'Synthetic assertion',
                               'options': {'yes': 'Yes', 'no': 'No', 'unknown': 'Unknown'}} for k in keys],
            'returns': returns, 'unit': 'patient', 'sources': ['passages']},
            'evaluated_patient_ids': ['P-1'], 'population_patients': 1, 'capped': False, 'cap': 10,
            'structured_states': {}, 'system_one': {'decisions': len(keys), 'new_paid_api_calls': 0,
                'estimated_usd': 0, 'decided_by': {}, 'errors': 0},
            'cells': [{'patient_id': 'P-1', 'key': k, 'label': label, 'date': '2030-01-01',
                       'document': None, 'probabilities': {label: .93}, 'sources': []}
                      for k, label in zip(keys, labels)]}


class IdentityColumns(unittest.TestCase):
    def test_reserved_unknown_keeps_identity_and_uncertainty(self):
        r = report(); before = copy.deepcopy(r)
        a = aq.answer(r, {'coverage': 'supported_observation'}, 'Synthetic dev reproduction')
        self.assertEqual(a['table'], [{'patient_id': 'P-1', 'answer': 'unknown', 'answer_p': .93}])
        self.assertEqual(a['unresolved_patient_ids'], ['P-1'])
        self.assertEqual(a['count_range'], [0, 1])
        self.assertEqual(a['status'], 'partial')
        self.assertEqual(r, before)

    def test_reserved_yes_no_and_verdict(self):
        for label in ('yes', 'no'):
            for returns in ('count', 'value', 'patient_set', 'verdict', 'table'):
                with self.subTest(label=label, returns=returns):
                    a = aq.answer(report(labels=(label,), returns=returns), {'coverage': 'supported_observation'}, 'Assertion?')
                    self.assertEqual(a['table'][0]['patient_id'], 'P-1')
                    self.assertEqual(a['status'], 'answered')
                    if returns == 'verdict': self.assertTrue(a['value'].startswith(label.capitalize()))
                    else: self.assertEqual(a['patient_ids'], ['P-1'] if label == 'yes' else [])

    def test_derived_column_names_do_not_overwrite_labels_or_confidence(self):
        for keys in (('x', 'x_p', 'patient_id', 'answer'), ('x_p', 'x', 'answer', 'patient_id')):
            r = report(keys, ('yes', 'no', 'unknown', 'yes'))
            rows, _ = aq.aggregate(r); columns = aq.question_columns(r['parameters'])
            self.assertEqual(rows[0]['patient_id'], 'P-1')
            for cell in r['cells']:
                k = columns[cell['key']]
                self.assertEqual(rows[0][k], cell['label'])
                self.assertEqual(rows[0][k + '_p'], .93)

    def test_conflict_and_trajectory_keep_patient_and_derived_columns(self):
        for aggregation, suffix in (('conflict', '_labels'), ('trajectory', '_trajectory')):
            r = report(('patient_id', 'answer' + suffix), ('yes', 'no'), aggregation)
            r['cells'].append({**r['cells'][0], 'label': 'no', 'date': '2030-02-01'})
            rows, chosen = aq.aggregate(r); columns = aq.question_columns(r['parameters'])
            self.assertEqual(rows[0]['patient_id'], 'P-1')
            self.assertIsInstance(rows[0][columns['patient_id'] + suffix], list)
            self.assertEqual(rows[0][columns['answer' + suffix]], 'no')
            if aggregation == 'conflict': self.assertEqual(chosen, ['P-1'])

    def test_multiple_patients_keep_selected_and_unresolved_membership(self):
        r = report(labels=('yes',))
        r['evaluated_patient_ids'] += ['P-2', 'P-3']
        r['population_patients'] = 3
        r['cells'] += [{**r['cells'][0], 'patient_id': pid, 'label': label}
                       for pid, label in [('P-2', 'unknown'), ('P-3', 'no')]]
        a = aq.answer(r, {'coverage': 'supported_observation'}, 'Synthetic cohort')
        self.assertEqual(a['patient_ids'], ['P-1'])
        self.assertEqual(a['unresolved_patient_ids'], ['P-2'])
        self.assertEqual(a['count_range'], [1, 2])
        self.assertEqual([row['patient_id'] for row in a['table']], ['P-1', 'P-2', 'P-3'])

    def test_ordinary_columns_keep_existing_shape(self):
        r = report(('assertion',), ('yes',))
        a = aq.answer(r, {'coverage': 'supported_observation'}, 'Assertion?')
        self.assertNotIn('question_columns', a)
        self.assertEqual(a['table'], [{'patient_id': 'P-1', 'assertion': 'yes', 'assertion_p': .93}])
