"""Local scoped credentials never confer clinical/external authority."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import unittest
from unittest.mock import patch
import test_participation_evidence as fixture
from clinical_core.agere.clinical import internal_execution as e, action_work_state


class Execution(unittest.TestCase):
    put = fixture.Participation.put
    update = fixture.Participation.update
    save = fixture.Participation.save
    match = fixture.Participation.match
    context = fixture.Participation.context
    queue = fixture.Participation.queue
    event = fixture.Participation.event

    def setUp(self):
        fixture.Participation.setUp(self)
        self.task = self.event(self.queue())['task']
        self.worker = self.credential(['packet_worker'])
        self.reviewer = self.credential(['packet_reviewer'])
        self.request = {'task_id': self.task['id'], 'expected_revision': self.task['revision'], 'request_id': 'run-1'}

    def credential(self, roles, **changes):
        return e.provision(self.path, {'principal_id': 'Synthetic test principal', 'identity_kind': 'synthetic_test',
            'buyer_id': 'buyer1', 'service_id': 'pharmacy', 'roles': roles,
            'expires_at': (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(), **changes})

    def bearer(self, credential=None): return 'Bearer '+(credential or self.worker)['token']
    def run_packet(self, **changes): return e.run(self.path, {**self.request, **changes}, self.bearer())
    def review_packet(self, run, **changes):
        return e.review(self.path, {'run_id': run['id'], 'expected_revision': 0, 'request_id': 'review-1',
            'disposition': 'needs_more_evidence', 'note': 'Synthetic check only, clinical review still needed',
            'follow_up_on': datetime.now(timezone.utc).date().isoformat(), **changes}, self.bearer(self.reviewer))

    def test_real_checks_acknowledged_with_exact_packet_and_limits(self):
        out = self.run_packet(); run = out['run']
        self.assertEqual(len(run['completion']['checks']), 4)
        self.assertTrue(all(c['packet_current'] for c in run['completion']['checks']))
        self.assertEqual(run['binding']['task_revision'], self.task['revision'])
        self.assertEqual(run['acknowledgement']['principal']['id'], self.worker['credential']['id'])
        self.assertFalse(run['clinical_execution_authorized']); self.assertFalse(run['completion']['clinical_requirements_resolved'])
        self.assertEqual(e.inspect(self.path, self.task['id'])['current_run_id'], run['id'])
        self.assertEqual(e.care_readiness.task(self.path, self.task['id']), self.task)

    def test_authentication_required_and_body_cannot_spoof_actor_or_adapter(self):
        for bearer in (None, '', 'Bearer bad', 'Bearer '+'x'*45):
            with self.assertRaises(PermissionError): e.run(self.path, self.request, bearer)
        for extra in ({'actor': 'clinician'}, {'adapter': 'send_referral'}):
            with self.assertRaises(ValueError): self.run_packet(**extra)

    def test_role_separation_and_scope(self):
        with self.assertRaises(PermissionError): e.run(self.path, self.request, self.bearer(self.reviewer))
        principal = self.worker['credential']
        for change in ({'buyer_id': 'other'}, {'capability_id': 'other'}):
            with self.assertRaises(PermissionError): e.authorize(principal, {**self.task, **change})
        with self.assertRaises(ValueError): self.credential(['packet_worker'], service_id='unknown')
        with self.assertRaises(ValueError): self.credential(['clinical_executor'])

    def test_revoked_and_expired_credentials_cannot_replay(self):
        self.run_packet(); e.revoke(self.path, self.worker['credential']['id'], 'Unit test revocation')
        with self.assertRaises(PermissionError): self.run_packet()
        with patch.object(e, 'now', return_value=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()):
            with self.assertRaises(PermissionError): e.run(self.path, self.request, self.bearer(self.reviewer))

    def test_no_secrets_in_receipts_or_storage(self):
        out = self.run_packet(); state = e.inspect(self.path, self.task['id'])
        self.assertNotIn(self.worker['token'], json.dumps([out, state]))
        self.assertNotIn(self.worker['token'].encode(), (self.path/'internal-execution.sqlite3').read_bytes())
        self.assertEqual((self.path/'internal-execution.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_duplicate_requests_and_new_ids_reuse_same_completion(self):
        first = self.run_packet(); self.assertTrue(self.run_packet()['idempotent_replay'])
        again = self.run_packet(request_id='run-2'); self.assertTrue(again['deduplicated'])
        self.assertEqual(first['run'], again['run'])
        with self.assertRaises(e.care.work.WorkConflict): self.run_packet(expected_revision=999)
        self.assertEqual(len(e.inspect(self.path, self.task['id'])['runs']), 1)

    def test_concurrent_dispatch_produces_one_completion(self):
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda n: self.run_packet(request_id='concurrent-'+str(n)), range(2)))
        self.assertEqual(results[0]['run'], results[1]['run'])
        self.assertCountEqual([r['deduplicated'] for r in results], [False, True])

    def test_review_records_actor_follow_up_without_resolving_requirements(self):
        run = self.run_packet()['run']; out = self.review_packet(run)
        self.assertEqual(out['review']['principal']['id'], self.reviewer['credential']['id'])
        self.assertTrue(self.review_packet(run)['idempotent_replay'])
        self.assertFalse(out['review']['clinical_requirements_resolved'])
        self.assertEqual(e.inspect(self.path, self.task['id'])['runs'][0]['follow_up_state'], 'due')
        self.assertEqual(action_work_state.inspect(self.path, self.task['id'])['layers']['documentary_review']['state'], 'documentary_review_incomplete')

    def test_review_revision_conflict_backdate_and_clinical_disposition_rejected(self):
        run = self.run_packet()['run']; self.review_packet(run)
        with self.assertRaises(e.care.work.WorkConflict): self.review_packet(run, request_id='review-2')
        for value in ({'follow_up_on': '2000-01-01'}, {'disposition': 'clinically_approved'}, {'follow_up_on': '2026-1-1'}):
            with self.assertRaises(ValueError): self.review_packet(run, request_id='invalid', expected_revision=1, **value)

    def test_changed_dependencies_stale_completion_and_block_review(self):
        run = self.run_packet()['run']; self.save()
        state = e.inspect(self.path, self.task['id']); self.assertIsNone(state['current_run_id'])
        self.assertEqual(state['runs'][0]['follow_up_state'], 'refresh_required')
        with self.assertRaises(e.care.work.WorkConflict): self.review_packet(run)
        with self.assertRaises(e.care.work.WorkConflict): self.run_packet(request_id='new')
        # Exact replay remains an immutable historical receipt, never a new current completion.
        self.assertTrue(self.run_packet()['idempotent_replay'])

    def test_new_packet_does_not_reuse_old_review(self):
        old = self.run_packet()['run']; self.review_packet(old)
        task = self.event(self.task, rid='new-packet')['task']
        new = self.run_packet(request_id='new-run', expected_revision=task['revision'])['run']
        self.assertNotEqual(new['id'], old['id']); rows = e.inspect(self.path, task['id'])['runs']
        self.assertEqual(rows[-1]['reviews'], []); self.assertFalse(rows[0]['packet_current'])

    def test_cancelled_and_stale_revision_block_new_dispatch(self):
        with self.assertRaises(e.care.work.WorkConflict): self.run_packet(expected_revision=999)
        self.event(self.task, rid='cancel', action='cancel')
        with self.assertRaises(e.care.work.WorkConflict): self.run_packet()

    def test_immutable_history(self):
        self.run_packet(); db = e.database(self.path)
        try:
            for table in ('credentials', 'runs', 'requests'):
                with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM '+table)
        finally: db.close()


if __name__ == '__main__': unittest.main()
