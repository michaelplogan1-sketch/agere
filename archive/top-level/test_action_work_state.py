"""A current internal packet is not readiness to deliver care."""
import unittest
from unittest.mock import patch
import test_participation_evidence as fixture
from clinical_core.agere.clinical import action_work_state as a, care_capabilities as c, participation_work as p


class ActionState(unittest.TestCase):
    setUp = fixture.Participation.setUp
    put = fixture.Participation.put
    update = fixture.Participation.update
    save = fixture.Participation.save
    match = fixture.Participation.match
    context = fixture.Participation.context
    queue = fixture.Participation.queue
    event = fixture.Participation.event

    def refresh(self, task):
        return a.refresh(self.path, {'task_id': task['id'], 'expected_revision': task['revision'], 'actor': 'Temporary unit fixture'})

    def test_current_packet_and_unknown_clinical_requirements_remain_separate(self):
        task = self.queue(); self.event(task); report = a.inspect(self.path, task['id'])
        self.assertTrue(report['packet_matches_recorded_dependencies'])
        self.assertFalse(report['verified_benefits']); self.assertFalse(report['clinical_execution_authorized'])
        self.assertEqual(report['layers']['documentary_review']['state'], 'documentary_review_incomplete')
        self.assertTrue(report['layers']['documentary_review']['counts_by_route'])

    def test_stale_participation_can_refresh_same_task_then_prepare_new_packet(self):
        self.save(); task = self.event(self.queue())['task']; self.decl['reported_status'] = 'pending'; self.save()
        report = a.inspect(self.path, task['id']); self.assertFalse(report['packet_matches_recorded_dependencies'])
        self.assertTrue(report['local_matching_refresh_available']); self.assertEqual(report['layers']['participation']['state'], 'queue_refresh_required')
        result = self.refresh(task); self.assertEqual(result['task']['id'], task['id'])
        self.assertFalse(result['work_state']['packet_matches_recorded_dependencies'])
        self.event(result['task'], 'refreshed'); self.assertTrue(a.inspect(self.path, task['id'])['packet_matches_recorded_dependencies'])

    def test_changed_buyer_services_refresh_context_without_altering_origin_or_coverage(self):
        task = self.queue(); old = a.stored_plan(self.path, task['plan_id']); self.cap['name'] = 'Revised pharmacy service'; self.update()
        before = a.inspect(self.path, task['id']); self.assertFalse(before['layers']['source_and_service']['packet_current'])
        self.assertTrue(before['local_matching_refresh_available'])
        result = self.refresh(task); new = a.stored_plan(self.path, result['task']['plan_id'])
        self.assertEqual(new['origin'], old['origin']); self.assertEqual(new['coverage'], old['coverage'])
        self.assertEqual(result['task']['id'], task['id']); self.assertGreater(result['task']['revision'], task['revision'])

    def test_changed_coverage_cannot_implicitly_select_another_report(self):
        task = self.queue(); self.row['status'] = 'inactive_returned'; self.put(self.row)
        report = a.inspect(self.path, task['id']); self.assertFalse(report['local_matching_refresh_available'])
        self.assertEqual(report['layers']['coverage']['state'], 'source_changed')
        with self.assertRaises(c.work.WorkConflict): self.refresh(task)

    def test_unavailable_source_requires_new_explicit_evidence(self):
        task = self.queue()
        with patch.object(c.source_synthesis, 'inspect', side_effect=ValueError('Source interpretation is outdated')):
            report = a.inspect(self.path, task['id']); self.assertFalse(report['local_matching_refresh_available'])
            self.assertFalse(report['packet_matches_recorded_dependencies'])
            with self.assertRaises(ValueError): self.refresh(task)

    def test_service_no_longer_matches_does_not_redirect_task(self):
        task = self.queue(); self.cap['available'] = False; self.update()
        with self.assertRaisesRegex(c.work.WorkConflict, 'no longer'): self.refresh(task)
        self.assertEqual(a.care_readiness.task(self.path, task['id'])['plan_id'], task['plan_id'])

    def test_revision_race_and_cancelled_task_cannot_refresh(self):
        task = self.queue(); held = self.event(task, action='hold')['task']
        with self.assertRaises(c.work.WorkConflict): self.refresh(task)
        cancelled = self.event(held, rid='cancel', action='cancel')['task']
        report = a.inspect(self.path, task['id']); self.assertFalse(report['local_matching_refresh_available'])
        self.assertFalse(report['packet_matches_recorded_dependencies'])
        with self.assertRaises(c.work.WorkConflict): self.refresh(cancelled)

    def test_refresh_replay_does_not_create_or_duplicate_clinical_work(self):
        task = self.queue(); first = self.refresh(task); second = self.refresh(task)
        self.assertEqual(first['task'], second['task']); self.assertTrue(second['idempotent_replay'])
        self.assertEqual(second['paid_calls'], 0); self.assertEqual(second['external_actions_executed'], 0)
        self.assertNotIn('handoff', second['task'])


if __name__ == '__main__': unittest.main()
