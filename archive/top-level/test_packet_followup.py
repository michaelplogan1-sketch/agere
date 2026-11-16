"""Follow-up source availability never becomes clinical resolution."""
import unittest
from unittest.mock import patch
import test_internal_execution as fixture
from clinical_core.agere.clinical import packet_followup as f, internal_execution as e
from clinical_core.contracts import digest


class Followup(unittest.TestCase):
    put = fixture.Execution.put
    update = fixture.Execution.update
    save = fixture.Execution.save
    match = fixture.Execution.match
    context = fixture.Execution.context
    queue = fixture.Execution.queue
    event = fixture.Execution.event
    credential = fixture.Execution.credential
    bearer = fixture.Execution.bearer
    run_packet = fixture.Execution.run_packet
    review_packet = fixture.Execution.review_packet

    def setUp(self):
        fixture.Execution.setUp(self)
        self.run = self.run_packet()['run']; self.review_packet(self.run)
        self.target = f.targets(self.path, self.task['id'])[0]['target']
        # Context construction has its own source/time/closure tests. Here use its
        # real preconditions, with a fixture output for the missing index tables.
        self.acquisition = {'context_id': 'c'*64, 'target': f.dispositions.resolve_target(self.path, self.target),
                            'record_scope': {'record_ids': ['coverage1']}, 'decision_cutoff': '2026-10-10'}
        self.prepare_patch = patch.object(f.dispositions, 'acquisition_context', return_value=self.acquisition)
        self.prepare_mock = self.prepare_patch.start(); self.addCleanup(self.prepare_patch.stop)
        self.inspect_patch = patch.object(f.record_context, 'inspect', return_value={'patient_id': 'p1'})
        self.inspect_patch.start(); self.addCleanup(self.inspect_patch.stop)
        self.req = {'run_id': self.run['id'], 'review_revision': 1, 'target': self.target, 'record_ids': ['coverage1'],
                    'note': 'Synthetic follow-up source only', 'request_id': 'followup-1'}

    def prepare(self, **changes): return f.prepare(self.path, {**self.req, **changes}, self.bearer(self.reviewer))

    def test_exact_review_target_owner_and_context_bound(self):
        out = self.prepare(); self.assertEqual(out['review_revision'], 1)
        self.assertEqual(out['principal']['id'], self.reviewer['credential']['id'])
        self.assertEqual(out['acquisition'], self.acquisition); self.assertFalse(out['requirement_resolved']); self.assertFalse(out['care_delivered'])
        self.prepare_mock.assert_called_once_with(self.path, {'target': self.target, 'record_ids': ['coverage1']})
        self.assertTrue(f.inspect(self.path, self.task['id'])['followups'][0]['current'])

    def test_idempotent_followup_and_request_collision(self):
        out = self.prepare(); again = self.prepare(); self.assertTrue(again['idempotent_replay']); self.assertEqual(out['id'], again['id'])
        self.assertEqual(self.prepare_mock.call_count, 1)
        with self.assertRaises(e.care.work.WorkConflict): self.prepare(note='Different content')

    def test_wrong_credential_cannot_prepare(self):
        for token in (None, self.bearer()):
            with self.assertRaises(PermissionError): f.prepare(self.path, self.req, token)
        self.assertEqual(self.prepare_mock.call_count, 0)

    def test_foreign_target_and_wrong_review_rejected(self):
        with self.assertRaises(ValueError): self.prepare(target={**self.target, 'plan_id': 'another-plan'})
        with self.assertRaises(e.care.work.WorkConflict): self.prepare(review_revision=2)
        self.assertEqual(self.prepare_mock.call_count, 0)

    def test_new_review_invalidates_old_followup_without_deletion(self):
        self.prepare(); self.review_packet(self.run, expected_revision=1, request_id='review-2')
        state = f.inspect(self.path, self.task['id']); self.assertFalse(state['followups'][0]['current'])
        self.assertEqual(state['current_review_revision'], 2)
        with self.assertRaises(e.care.work.WorkConflict): self.prepare(request_id='new-followup')

    def test_source_context_change_is_explicit_and_packet_not_closed(self):
        self.prepare()
        with patch.object(f.record_context, 'inspect', side_effect=ValueError('Source context changed')):
            item = f.inspect(self.path, self.task['id'])['followups'][0]
            self.assertFalse(item['current']); self.assertIn('Source context changed', item['stale_reasons'])
        self.assertEqual(e.inspect(self.path, self.task['id'])['runs'][0]['follow_up_state'], 'due')

    def test_changed_packet_blocks_followup(self):
        self.save()
        with self.assertRaises(e.care.work.WorkConflict): self.prepare()
        self.assertEqual(self.prepare_mock.call_count, 0)

    def test_failed_context_has_no_followup_receipt(self):
        self.prepare_mock.side_effect = ValueError('Wrong patient or future source')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(f.inspect(self.path, self.task['id'])['followups'], [])

    def test_source_change_during_prepare_cannot_commit_completion(self):
        original = e.binding
        calls = 0
        def changing(*args):
            nonlocal calls
            result = original(*args); calls += 1
            return (*result[:2], {**result[2], 'id': digest('changed')}) if calls > 1 else result
        with patch.object(e, 'binding', side_effect=changing):
            with self.assertRaises(e.care.work.WorkConflict): self.prepare()
        self.assertEqual(f.inspect(self.path, self.task['id'])['followups'], [])


if __name__ == '__main__': unittest.main()
