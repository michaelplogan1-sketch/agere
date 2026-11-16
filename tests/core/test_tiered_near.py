"""Model-free checks for the near-match rung: a saved recipe answers a rewording of its question."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.tiered import methods, models, near
from tests.core.needs_data import needs_warehouse

STEP = {'model': 'm', 'tokens': 10, 'usd': 0.002, 'seconds': 0.1}


class Store:
    def __init__(self, saved):
        self.path, self.saved = Path('/tmp/near-test.sqlite3'), saved

    def full(self):
        return self.saved


def shape(i, template, params, status='promoted'):
    return {'id': i, 'template': template, 'status': status, 'sql': 'SELECT 1', 'params': params, 'grain': [], 'read': None}


SAVED = [shape(1, 'Number of {drug_class} fills in {year} and how many patients had them', {'drug_class': {'type': 'text'}, 'year': {'type': 'year'}}),
         shape(2, 'Top {n} conditions ranked by number of patients', {'n': {'type': 'int'}}),
         shape(3, 'Break down the appointments scheduled in {year} by their final status', {'year': {'type': 'year'}}),
         shape(4, 'How many deaths were recorded in {year}', {'year': {'type': 'year'}}, status='provisional')]


class Near(unittest.TestCase):
    def setUp(self):
        near._INDEX.clear()
        self.store = Store(SAVED)

    def test_shortlist_is_promoted_shapes_ranked_by_distinctive_words(self):
        got = near.shortlist('In 2029, how many statin fills were there and for how many patients?', self.store)
        self.assertEqual(got[0]['id'], 1)
        self.assertNotIn(4, [b['id'] for b in near.shortlist('How many deaths were there in 2029?', self.store)])  # a provisional shape is never offered
        self.assertEqual(near.shortlist('zzz qqq', self.store), [])

    def test_slot_values_are_type_checked_and_text_must_come_from_the_question(self):
        q = 'In 2029, how many statin fills were there and for how many patients?'
        self.assertEqual(near.typed(SAVED[0], {'drug_class': 'statin', 'year': '2029'}, q), {'drug_class': 'statin', 'year': '2029'})
        self.assertIsNone(near.typed(SAVED[0], {'drug_class': 'beta blocker', 'year': '2029'}, q))   # not in the question
        self.assertIsNone(near.typed(SAVED[0], {'drug_class': 'statin', 'year': 'last year'}, q))    # not a year
        self.assertIsNone(near.typed(SAVED[0], {'drug_class': 'statin'}, q))                         # a slot left empty
        self.assertEqual(near.typed(SAVED[1], {'n': '5'}, 'What are the five... top 5 conditions?'), {'n': 5})

    @needs_warehouse

    def test_a_table_or_column_the_question_names_must_be_read_by_the_saved_query(self):
        saved = {'sql': "SELECT COUNT(DISTINCT c.patient_id) FROM concept_patient c WHERE c.concept = :concept AND EXISTS (SELECT 1 FROM utilization_event u WHERE u.patient_id = c.patient_id)"}
        q = 'How many distinct patients with a utilization_event dated in 2027 also have a heart_failure condition in the condition table?'
        self.assertEqual(near.named_but_unused(q, saved, {'concept': 'heart_failure', 'year': '2027'}), ['condition'])
        by_kind = {'sql': "SELECT COUNT(DISTINCT patient_id) FROM concept_patient WHERE kind = 'condition' AND concept = :concept"}
        self.assertEqual(near.named_but_unused('Count unique patients with a heart_failure condition in the condition table.', by_kind, {'concept': 'heart_failure'}), ['condition'])
        self.assertEqual(near.named_but_unused('Count patients in the condition table', {'sql': 'SELECT COUNT(*) FROM condition c'}, {}), [])
        self.assertEqual(near.named_but_unused('How many patients with a heart_failure condition had a utilization_event in 2027?', saved, {'concept': 'heart_failure'}), [])
        self.assertEqual(near.named_but_unused('How many fills are flagged new_user_180?', {'sql': 'SELECT COUNT(*) FROM fill WHERE new_user_180 = 1'}, {}), [])
        with mock.patch.object(near, '_columns', lambda: ['source_field', 'source_stream']):
            grouped = {'sql': 'SELECT source_field, COUNT(DISTINCT patient_id) FROM social_need GROUP BY 1'}
            self.assertEqual(near.named_but_unused('How many patients with social need records came from each source stream in 2029?', grouped, {}), ['source_stream'])
            self.assertEqual(near.named_but_unused('How many patients by source field in 2029?', grouped, {}), [])

    def _propose(self, picks, reply='{"drug_class": "statin", "year": "2029"}'):
        picks = iter(picks)
        with mock.patch.object(methods, '_system_one', lambda prompt, options, ledger: next(picks)), \
                mock.patch.object(models, '_claude', lambda m, s, u: (reply, dict(STEP))):
            return near.propose('In 2029, how many statin fills were there and for how many patients?', self.store)

    @needs_warehouse

    def test_a_rewording_is_answered_only_when_pick_values_and_fit_all_hold(self):
        bottle, params, steps = self._propose([('1', 0.95, 0.0001), ('exact', 0.93, 0.0001)])
        self.assertEqual((bottle['id'], params), (1, {'drug_class': 'statin', 'year': '2029'}))
        self.assertEqual(steps[-1]['read_as'], 'Number of statin fills in 2029 and how many patients had them')
        self.assertEqual(self._propose([('none', 0.9, 0.0)])[2][-1]['outcome'], 'no_saved_shape_asks_the_same')
        self.assertEqual(self._propose([(None, 0.4, 0.0)])[2][-1]['outcome'], 'no_saved_shape_asks_the_same')
        # With the fallback switched on (off by default), the most similar saved shape still has to pass the small model and the exact-fit check.
        with mock.patch.object(near, 'FALLBACK', True):
            self.assertEqual(self._propose([('none', 0.9, 0.0), ('not_exact', 0.9, 0.0)])[2][-1]['outcome'], 'saved_shape_is_not_an_exact_fit')
            self.assertEqual(self._propose([('none', 0.9, 0.0)], reply='{"fits": false}')[2][-1]['outcome'], 'small_model_says_it_does_not_fit')
            self.assertIsNotNone(self._propose([('none', 0.9, 0.0), ('exact', 0.93, 0.0)])[0])
        self.assertEqual(self._propose([('1', 0.95, 0.0), ('not_exact', 0.9, 0.0)])[2][-1]['outcome'], 'saved_shape_is_not_an_exact_fit')
        self.assertEqual(self._propose([('1', 0.95, 0.0), (None, 0.5, 0.0)])[2][-1]['outcome'], 'saved_shape_is_not_an_exact_fit')
        self.assertEqual(self._propose([('1', 0.95, 0.0)], reply='{"fits": false}')[2][-1]['outcome'], 'small_model_says_it_does_not_fit')
        self.assertEqual(self._propose([('1', 0.95, 0.0)], reply='{"drug_class": "insulin", "year": "2029"}')[2][-1]['outcome'], 'slot_values_failed_type_checks')
        self.assertEqual(self._propose([('1', 0.95, 0.0)], reply='not json')[2][-1]['outcome'], 'small_model_unusable')

    def test_the_rung_is_off_unless_switched_on(self):
        with mock.patch.dict(os.environ, {'AGERE_TIERED_NEAR': 'off'}): self.assertFalse(near.enabled())
        with mock.patch.dict(os.environ, {'AGERE_TIERED_NEAR': 'on'}): self.assertTrue(near.enabled())


if __name__ == '__main__':
    unittest.main()
