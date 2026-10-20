"""Full lesson lifecycle against authored rows; no protected evaluation or provider."""
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

import test_shared_planner as fixtures
from test_general_analysis import Q, spec
from clinical_core.agere.clinical import learning, procedural_memory as M, shared_planner as S
from clinical_core.agere.deliverables import answer_context as A


class ProceduralMemory(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)
        self.stack.allow_live = True
        self.stack.receipts = self.path
        self.model = Mock(side_effect=lambda *a, **k: self.response(*a, **k))
        self.model.identity = 'scripted-development-interpreter'
        self.model_patch = patch('clinical_core.agere.clinical.nl2sql.HostedModel', return_value=self.model)
        self.ledger_patch = patch('clinical_core.agere.gateway.default_ledger_path', return_value=self.path/'ledger.sqlite3')
        self.model_patch.start(); self.ledger_patch.start()
        self.addCleanup(self.model_patch.stop); self.addCleanup(self.ledger_patch.stop)
        result = S.compose(self.stack, Q)
        self.context = A.capture(self.path, Q, result)['id']

    def response(self, system, user, **kwargs):
        data = json.loads(user)
        return (json.dumps({'analysis': spec()}) if 'question' in data else
                json.dumps({'equivalent': True, 'issues': []})), {'api_usd': .01, 'api_cost_known': True}

    def proposal(self, **kwargs):
        return M.propose(self.path, self.context, kwargs.pop('instruction', 'Preserve every event-count threshold and its original time window.'),
                         probes=[{'query': 'SELECT COUNT(*) AS n FROM patient', 'returns': 'count', 'expected': {'value': 5}}],
                         required_fields={'utilization_event': ['patient_id', 'kind', 'event_date']}, **kwargs)

    def test_candidate_is_not_reused_until_grounded_then_actual_planner_records_use(self):
        lesson = self.proposal()
        self.assertEqual(M.select(self.path, Q), [])
        M.ground(self.path, lesson)
        self.assertEqual([r['id'] for r in M.select(self.path, Q)], [lesson])
        result = S.compose(self.stack, Q)
        used = result['planner_receipt']['interpretation']['procedural_memory']
        self.assertEqual(used['lesson_ids'], [lesson])
        self.assertEqual(result['answer']['count'], 1)
        self.assertEqual(M.summary(self.path)['outcomes'], {'observed_pass': 1})
        data = json.loads(self.model.call_args_list[-2].args[1])
        self.assertIn('Untrusted suggestions', data['procedural_lessons']['authority'])
        self.assertEqual(data['procedural_lessons']['items'][0]['id'], lesson)
        replay = S.compose(self.stack, Q)
        self.assertEqual(replay['new_paid_api_calls'], 0)
        self.assertNotEqual(replay['planner_receipt']['interpretation']['procedural_memory']['use_id'], used['use_id'])
        self.assertNotIn('learned_analysis_shape', result)

    def test_failed_use_quarantines_revision_and_invalidates_derived_context(self):
        lesson = self.proposal(); M.ground(self.path, lesson)
        result = S.compose(self.stack, Q)
        key = A.capture(self.path, Q, result)['id']
        self.assertFalse(A.load(self.path, key)['invalidated'])
        use = result['planner_receipt']['interpretation']['procedural_memory']['use_id']
        M.outcome(self.path, use, 'failed', detail='Independent development check found a dropped constraint')
        self.assertEqual(M.select(self.path, Q), [])
        self.assertTrue(A.load(self.path, key)['invalidated'])
        with self.assertRaisesRegex(ValueError, 'new candidate'): M.ground(self.path, lesson)
        with self.assertRaisesRegex(ValueError, 'invalidated'): A.load(self.path, key, current=True)

    def test_correction_and_revision_preserve_original_trajectory_and_event_history(self):
        lesson = self.proposal(); M.ground(self.path, lesson)
        result = S.compose(self.stack, Q)
        learning.record_correction(self.path, Q, 'Check the time-window boundary against source rows')
        self.assertEqual(M.records(self.path)[0]['status'], 'quarantined')
        revised = self.proposal(instruction='Check inclusive calendar-year boundaries using original event dates.', supersedes=lesson)
        M.ground(self.path, revised)
        records = {r['id']: r for r in M.records(self.path)}
        self.assertEqual(records[lesson]['status'], 'superseded')
        self.assertEqual(records[revised]['status'], 'active')
        self.assertTrue(any(e['kind'] == 'quarantined' for e in records[lesson]['events']))
        self.assertTrue(A.load(self.path, self.context))
        self.assertEqual(learning.summary(self.path)['procedural_memory']['revisions'], 2)

    def test_preconditions_reject_changed_source_family_and_failed_probe(self):
        lesson = self.proposal(); M.ground(self.path, lesson)
        self.assertEqual(M.select(self.path, 'Count all claims'), [])
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("UPDATE meta SET value='new-release' WHERE key='source_digest'")
        self.assertEqual(M.select(self.path, Q), [])
        with self.assertRaisesRegex(ValueError, 'Re-establish'): self.proposal(instruction='A new lesson')

    def test_sentence_final_numeric_literal_reuses_lesson_and_correction_suspends_it(self):
        original = Q.rstrip('?') + '.'
        changed = original.replace('2029', '2028')
        # A saved authored trajectory with a terminal literal; eligibility and
        # correction matching must agree before any hosted proposal is made.
        self.assertEqual(M._family(original), M._family(changed))
        self.assertNotEqual(M._family('Use diagnosis E11.9.'), M._family('Use diagnosis E11.8.'))
        result = S.compose(self.stack, original)
        self.context = A.capture(self.path, original, result)['id']
        lesson = self.proposal(); M.ground(self.path, lesson)
        self.assertEqual([r['id'] for r in M.select(self.path, changed)], [lesson])
        learning.record_correction(self.path, changed, 'Check the requested service year')
        self.assertEqual(M.select(self.path, original), [])
        self.assertEqual(M.records(self.path)[0]['status'], 'quarantined')

    def test_mutating_or_false_source_probes_cannot_activate(self):
        with self.assertRaisesRegex(ValueError, 'received warehouse evidence'):
            M.propose(self.path, self.context, 'A tautology is not grounding',
                      probes=[{'query': 'SELECT 1', 'returns': 'count', 'expected': {'value': 1}}])
        with self.assertRaises(ValueError):
            M.propose(self.path, self.context, 'Bad probe', probes=[{'query': 'DELETE FROM patient', 'returns': 'count', 'expected': {'value': 0}}])
        lesson = M.propose(self.path, self.context, 'Unverified count',
                           probes=[{'query': 'SELECT COUNT(*) AS n FROM patient', 'returns': 'count', 'expected': {'value': 99}}])
        with self.assertRaisesRegex(ValueError, 'no longer matches'): M.ground(self.path, lesson)
        self.assertEqual(M.records(self.path)[0]['status'], 'candidate')

    def test_provider_failure_is_unverified_not_a_negative_lesson_reward(self):
        lesson = self.proposal(); M.ground(self.path, lesson)
        self.model.side_effect = RuntimeError('provider unavailable')
        with self.assertRaises(RuntimeError): S.compose(self.stack, Q)
        self.assertEqual(M.summary(self.path)['outcomes'], {'unverified': 1})
        self.assertEqual(M.records(self.path)[0]['status'], 'active')

    def test_action_boundary_reprobes_preconditions_and_rejects_tampered_trajectory(self):
        lesson = self.proposal(); M.ground(self.path, lesson)
        result = S.compose(self.stack, Q)
        key = A.capture(self.path, Q, result)['id']
        with patch.object(M, '_probe', side_effect=ValueError('Source precondition failed')):
            with self.assertRaisesRegex(ValueError, 'invalidated'): A.load(self.path, key, current=True)
        self.assertEqual(M.records(self.path)[0]['status'], 'quarantined')
        target = A.root(self.path)/self.context/'answer.json'
        data = json.loads(target.read_text()); data['answer']['count'] = 99
        target.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'intact saved'): self.proposal(instruction='From a tampered trace')

    def test_lessons_are_bound_to_exact_input_population_including_empty_scope(self):
        result = S.compose(self.stack, Q, population=['B', 'A'])
        self.context = A.capture(self.path, Q, result)['id']
        lesson = self.proposal(); M.ground(self.path, lesson)
        self.assertEqual([r['id'] for r in M.select(self.path, Q, population={'A', 'B'})], [lesson])
        for scope in (None, [], ['A'], ['B'], ['A', 'OUTSIDE']):
            with self.subTest(scope=scope):
                self.assertEqual(M.select(self.path, Q, population=scope), [])
                other = S.compose(self.stack, Q, population=scope)
                self.assertNotIn('procedural_memory', other['planner_receipt']['interpretation'])
        same = S.compose(self.stack, Q, population=['A', 'B'])
        self.assertEqual(same['planner_receipt']['interpretation']['procedural_memory']['lesson_ids'], [lesson])
        key = A.capture(self.path, Q, same)['id']
        self.assertFalse(A.load(self.path, key, current=True)['invalidated'])
        empty = S.compose(self.stack, Q, population=[])
        self.context = A.capture(self.path, Q, empty)['id']
        empty_lesson = self.proposal(); M.ground(self.path, empty_lesson)
        self.assertEqual([r['id'] for r in M.select(self.path, Q, population=[])], [empty_lesson])
        self.assertEqual(M.select(self.path, Q, population=None), [])

    def test_subset_grounding_probe_can_use_bound_population_and_revision_cannot_broaden(self):
        result = S.compose(self.stack, Q, population=['A'])
        self.context = A.capture(self.path, Q, result)['id']
        lesson = M.propose(self.path, self.context, 'Retain the saved population before counting events.',
                           probes=[{'query': 'SELECT COUNT(*) AS n FROM patient WHERE patient_id IN (SELECT patient_id FROM population)',
                                    'returns': 'count', 'expected': {'value': 1}}])
        M.ground(self.path, lesson)
        self.assertEqual(M.select(self.path, Q, population=['A'], probe=True)[0]['id'], lesson)
        self.context = A.capture(self.path, Q, S.compose(self.stack, Q))['id']
        with self.assertRaisesRegex(ValueError, 'same question family and population'):
            self.proposal(supersedes=lesson)


if __name__ == '__main__': unittest.main()
