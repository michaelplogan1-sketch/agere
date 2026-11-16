"""Independent fixtures for the Oct 19 review; no benchmark keys or model calls."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from clinical_core.agere.clinical import answer_log, bottled, bottled_cohort_hook, cohorts, cohort_families, conversation, grounding, therapy_review
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood
from clinical_core.agere.deliverables import answer_context as A, synthetic_actions as S


class Interpretation(unittest.TestCase):
    def test_derived_age_uses_real_dependencies_and_evaluates(self):
        profile = {'patients': {'fields': {'age_reported': {'populated': 1}}}, 'streams': {}}
        atom = {'kind': 'patient', 'field': 'age_latest', 'op': 'gt', 'value': 65}
        self.assertEqual(grounding.check_patient(atom, profile), [])
        value, _ = cohorts.evaluate_atom(atom, {'age_reported': 70}, [], '2030-05-27')
        self.assertTrue(value)
        profile['patients']['fields'] = {'birth_date': {'populated': 1}}
        self.assertEqual(grounding.check_patient(atom, profile), [])
        value, _ = cohorts.evaluate_atom(atom, {'birth_date': '1950-01-01'}, [], '2030-05-27')
        self.assertTrue(value)
        profile['patients']['fields'] = {}
        issue = grounding.check_patient(atom, profile)[0]
        self.assertEqual(len(issue['sources_checked']), 3)

    def test_mixed_polarity_cannot_become_all_negative_predicates(self):
        for q in ['List diabetics on a statin but not on an ACE inhibitor or ARB',
                  'Which patients taking metformin are not taking insulin?',
                  'List patients not on a statin but taking an ARB']:
            self.assertIsNone(cohort_families.parse(q, '2030-05-27'), q)
        self.assertIsNotNone(cohort_families.parse('Which diabetics are not on a statin?', '2030-05-27'))

    def test_learned_slots_cannot_bypass_the_shared_scope_guard(self):
        q = 'List diabetics on a statin but not on an ACE inhibitor or ARB'
        model = Mock(side_effect=AssertionError('Rejected scope must not reach a model'))
        got, receipt = bottled.cohort_slots(q, '2030-05-27', model=model)
        self.assertIsNone(got)
        self.assertIn('Positive medication', receipt['declined'])
        self.assertIsNone(bottled_cohort_hook.plan(None, q, '2030-05-27', model=model))
        model.assert_not_called()

    def test_positive_hf_request_is_not_claimed_and_incomplete_review_is_partial(self):
        for q in ['Flag HFrEF patients who ARE on full GDMT at target dose', 'Review HFrEF patients on GDMT']:
            self.assertFalse(therapy_review.accepts(q))
        with patch.object(therapy_review, 'assessment', return_value={
                'rows': [], 'lookback_days': 120, 'ef_unresolved_patient_ids': []}):
            r = therapy_review.answer(Path('.'), 'Flag HFrEF patients not on GDMT')
        self.assertEqual(r['answer']['status'], 'partial')
        self.assertTrue(r['answer']['missing'])
        self.assertFalse(r['answer']['actionable_cohort'])

    def test_error_cost_is_unknown_not_free(self):
        with tempfile.TemporaryDirectory() as p:
            answer_log.record(p, 'failed after dispatch', error='provider failure')
            with sqlite3.connect(Path(p) / 'answers.sqlite3') as db:
                self.assertEqual(db.execute('SELECT api_calls,api_usd FROM answers').fetchone(), (None, None))


class SavedCohort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        with sqlite3.connect(self.path / 'semantic.sqlite3') as db:
            db.executescript('''CREATE TABLE meta(key,value);
                INSERT INTO meta VALUES('as_of','2030-05-27'),('source_digest','independent'),('version','test');
                CREATE TABLE patient(patient_id,pcp_clinician_id,pcp_practice_id,payer_id);
                INSERT INTO patient VALUES('A','PCP-1','PRACTICE-1','PAYER-1'),('B','PCP-2','PRACTICE-1','PAYER-2'),('OUTSIDE','PCP-2','PRACTICE-2','PAYER-2');
                CREATE TABLE death(patient_id,death_date,record_ids);
                INSERT INTO death VALUES('B','2029-01-01','D-B');''')
        self.result = {'answer': {'status': 'answered', 'answer_type': 'table', 'missing': [],
                                 'headline': 'Two patients', 'patient_ids': ['A', 'B'],
                                 'table': [{'patient_id': 'A', 'source': 'R-A'}, {'patient_id': 'B', 'source': 'R-B'}]},
                       'planner_receipt': {'route': 'test'}, 'new_paid_api_calls': 0}
        self.stack = SimpleNamespace(path=self.path, allow_live=False, compose=Mock(return_value=self.result))
        self.first = conversation.handle_compose(self.stack, {'question': 'List patients with diabetes', 'conversation_id': 't'})
        self.c = self.first['action_context']

    def turn(self, q):
        return conversation.handle_compose(self.stack, {'question': q, 'conversation_id': 't'})

    def req(self, **kw):
        return {'context_id': self.c['id'], 'patient_ids': ['A'], 'program': 'rpm', 'owner': 'Team A', 'note': '', **kw}

    def test_followups_keep_membership_and_evidence_through_failed_turn(self):
        self.stack.compose.side_effect = AssertionError('No new population query is allowed')
        with self.assertRaises(QuestionNotUnderstood):
            self.turn('Predict their response and enroll them')
        r = self.turn('Group them by PCP')
        self.assertEqual(r['answer']['table'], [{'pcp': 'PCP-1', 'patients': 1}, {'pcp': 'PCP-2', 'patients': 1}])
        self.assertEqual(r['cohort_transform']['input_patient_ids'], ['A', 'B'])
        r = self.turn('List them')
        self.assertEqual(r['answer']['table'], self.result['answer']['table'])
        r = self.turn('Exclude patients who have died')
        self.assertEqual(r['answer']['patient_ids'], ['A'])
        r = self.turn('Count them')
        self.assertEqual(r['answer']['count'], 1)
        self.assertEqual(self.stack.compose.call_count, 1)
        self.assertEqual(r['new_paid_api_calls'], 0)

    def test_missing_context_never_defaults_to_all_patients(self):
        for body in [{'question': 'Group them by PCP'}, {'question': 'List them', 'conversation_id': 'new'}]:
            with self.assertRaises(QuestionNotUnderstood):
                conversation.handle_compose(self.stack, body)
        self.assertEqual(self.stack.compose.call_count, 1)

    def test_partial_parent_cannot_become_answered_after_followup(self):
        r = copy.deepcopy(self.result)
        r['answer'].update(status='partial', missing=['Treatment scope not implemented'])
        c = A.capture(self.path, 'Partial example', r)
        answer = A.followup(self.path, c['id'], 'Group them by PCP')['answer']
        self.assertEqual(answer['status'], 'partial')
        self.assertEqual(answer['missing'], r['answer']['missing'])

    def test_answer_identity_ignores_question_punctuation_and_execution_cost(self):
        r = copy.deepcopy(self.result)
        r['planning_economics'] = {'known_api_estimate_usd': 10}
        self.assertEqual(A.capture(self.path, 'List patients with diabetes.', r)['id'], self.c['id'])
        r['answer']['table'][0]['source'] = 'CORRECTED'
        self.assertNotEqual(A.capture(self.path, 'List patients with diabetes', r)['id'], self.c['id'])

    def test_old_incorrect_review_is_preserved_but_blocked(self):
        r = copy.deepcopy(self.result)
        r['planner_receipt']['version'] = 'patient-therapy-review/1'
        r['answer']['headline'] = 'Original therapy review'
        c = A.capture(self.path, 'Historical question', r)
        self.assertTrue(A.load(self.path, c['id'])['invalidated'])
        with self.assertRaisesRegex(ValueError, 'invalidated'):
            A.create_work(self.path, self.req(context_id=c['id']))

    def test_conflicting_owner_does_not_silently_win_or_drop_other_patients(self):
        A.create_work(self.path, self.req())
        with self.assertRaisesRegex(ValueError, 'different owner'):
            A.create_work(self.path, self.req(owner='Team B', patient_ids=['B', 'A']))
        self.assertEqual(len(A.inspect(self.path, self.c['id'])['items']), 1)

    def test_review_rules_missing_id_and_program_wording(self):
        item = A.create_work(self.path, self.req())['items'][0]
        edit = {'context_id': self.c['id'], 'item_id': item['id'], 'revision': 1,
                'state': 'review_complete', 'owner': 'Team A', 'note': ''}
        with self.assertRaisesRegex(ValueError, 'cannot move'):
            A.update_work(self.path, edit)
        request = {'context_id': self.c['id'], 'item_id': item['id'], 'revision': 1, 'scenario': 'happy_path'}
        with self.assertRaisesRegex(ValueError, 'absent'):
            S.advance(self.path, {**request, 'item_id': 'MISSING'})
        item = S.advance(self.path, request)['items'][0]
        self.assertIn('monitoring-team', item['simulation']['receipts'][0]['reason'])
        A.update_work(self.path, {**edit, 'revision': item['revision'], 'state': 'blocked'})
        with self.assertRaisesRegex(ValueError, 'active work'):
            S.advance(self.path, {**request, 'revision': item['revision'] + 1})


if __name__ == '__main__':
    unittest.main()
