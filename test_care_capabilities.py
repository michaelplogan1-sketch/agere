"""Buyer service routing: missing coverage, policy bounds, stale evidence and work."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from clinical_core.agere.clinical import care_capabilities as c, program_work as w


class CareCapabilities(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        (self.path/'run.json').write_text(json.dumps({'synthetic': True, 'source_run': 'fixture', 'source_digest': 'fixture', 'snapshot': {'as_of': '2026-10-10'}}))
        self.answer = {'id': 'a'*64, 'patient_id': 'p1', 'question': 'What is documented?',
                       'findings': [{'id': 'f1', 'text': 'Medication list needs review.', 'citations': [{'source': {'record_id': 'r1'}, 'quote': 'Review needed'}]}]}
        p = patch.object(c.source_synthesis, 'inspect', side_effect=lambda path, key: copy.deepcopy(self.answer))
        p.start(); self.addCleanup(p.stop)
        self.cap = {'id': 'pharmacy', 'name': 'Virtual pharmacy', 'description': 'Synthetic service fixture',
                    'need_tags': ['medication_review'], 'modality': 'virtual', 'regions': ['IL'],
                    'payer_types': ['original_medicare', 'commercial', 'medicare_advantage'], 'plan_ids': ['plan1'],
                    'pathway_ids': ['access_ckm'], 'owner': {'kind': 'clinician', 'id': 'fixture-pharmd'},
                    'available': True, 'capacity_slots': 4, 'capacity_as_of': '2026-10-01', 'capacity_until': '2026-10-31',
                    'effective_start': '2026-01-01', 'effective_end': '2027-12-31',
                    'prerequisites': ['Authorized treatment protocol'], 'provenance': 'Synthetic test declaration'}
        self.save_request = {'buyer_id': 'buyer1', 'expected_revision': 0, 'actor': 'test operator', 'capabilities': [self.cap]}
        self.port = c.save_portfolio(self.path, self.save_request)
        self.req = {'buyer_id': 'buyer1', 'origin': {'kind': 'answer', 'id': 'a'*64, 'finding_ids': ['f1']},
                    'need_tags': ['medication_review'], 'rationale': 'Operator-selected review only',
                    'coverage': {'payer_type': 'original_medicare', 'plan_id': 'plan1', 'region': 'IL',
                                 'as_of': '2026-10-10', 'provenance': 'Synthetic coverage fixture', 'preferences': ''}}

    def prepare(self): return c.prepare(self.path, self.req)

    def queue(self, plan=None):
        plan = plan or self.prepare()
        return c.enqueue(self.path, {'plan_id': plan['id'], 'option_id': plan['options'][0]['id'], 'actor': 'test operator'})

    def update(self):
        self.save_request['expected_revision'] = c.portfolio(self.path, 'buyer1')['revision']
        return c.save_portfolio(self.path, self.save_request)

    def event(self, task, **overrides):
        req = {'task_id': task['id'], 'expected_revision': task['revision'], 'request_id': 'request-1',
               'action': 'prepare_handoff', 'actor': 'test operator', 'note': 'Prepare internal fixture packet'}
        req.update(overrides)
        return c.task_event(self.path, req)

    def test_buyer_history_and_optimistic_revision(self):
        self.cap['name'] = 'Updated service'; self.update()
        with self.assertRaises(w.WorkConflict): c.save_portfolio(self.path, self.save_request)
        db = c.db_open(self.path)
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM capability_portfolios').fetchone()[0], 2)
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM capability_portfolios')
        finally: db.close()

    def test_custom_service_needs_and_no_match_visible(self):
        self.cap['need_tags'] = ['custom_need']; self.cap['pathway_ids'] = []; self.update()
        p = self.prepare(); self.assertFalse(p['options']); self.assertEqual(p['unmatched_tags'], ['medication_review'])
        self.req['need_tags'] = ['custom_need']; p = self.prepare()
        self.assertEqual(p['options'][0]['routes'][0]['pathway_id'], None)
        self.assertFalse(p['options'][0]['clinical_execution_authorized'])

    def test_unknown_is_not_denial_or_coverage(self):
        self.req['coverage'].update(payer_type='unknown', plan_id='', region='')
        route = self.prepare()['options'][0]['routes'][0]
        self.assertEqual(route['status'], 'requires_verification')
        self.assertFalse(route['coverage_established']); self.assertGreater(len(route['requirements']), 6)

    def test_access_excludes_ma_and_commercial_not_generic_service(self):
        for payer in ['medicare_advantage', 'commercial']:
            self.req['coverage']['payer_type'] = payer
            self.assertEqual(self.prepare()['options'][0]['status'], 'blocked')
        self.cap['pathway_ids'].append('commercial_virtual'); self.update()
        p = self.prepare(); self.assertEqual(p['options'][0]['status'], 'requires_verification')
        self.assertEqual([r['status'] for r in p['options'][0]['routes']], ['blocked', 'requires_verification'])

    def test_dates_capacity_plan_and_region_are_independent(self):
        for key, value, reason in [('plan_id', 'other', 'Exact plan'), ('region', 'TX', 'service regions'), ('as_of', '2028-01-01', 'effective period')]:
            req = copy.deepcopy(self.req); req['coverage'][key] = value
            p = c.prepare(self.path, req); self.assertEqual(p['options'][0]['status'], 'blocked')
            self.assertTrue(any(reason in v for v in p['options'][0]['routes'][0]['blocked_reasons']))
        self.cap['capacity_slots'] = 0; self.update()
        self.assertEqual(self.prepare()['options'][0]['status'], 'blocked')
        self.req['coverage']['as_of'] = '2026-11-01'
        self.assertEqual(self.prepare()['options'][0]['status'], 'requires_verification')
        self.assertIn('Capacity observation does not cover decision date', self.prepare()['options'][0]['routes'][0]['requirements'])

    def test_future_access_track_and_policy_clock(self):
        self.cap['pathway_ids'] = ['access_hf']; self.update()
        self.assertIn('Pathway has not started on decision date', self.prepare()['options'][0]['routes'][0]['blocked_reasons'])
        self.req['coverage']['as_of'] = '2027-04-01'
        route = self.prepare()['options'][0]['routes'][0]
        self.assertEqual(route['status'], 'requires_verification')
        self.assertTrue(any('verify policy for decision date' in s for s in route['requirements']))

    def test_ma_core_benefits_are_not_excluded_like_access(self):
        self.cap['pathway_ids'] = ['pulmonary_rehab']; self.update()
        self.req['coverage']['payer_type'] = 'medicare_advantage'
        route = self.prepare()['options'][0]['routes'][0]
        self.assertEqual(route['status'], 'requires_verification')
        self.assertTrue(any('For Medicare Advantage' in s for s in route['requirements']))

    def test_bad_binding_and_forged_fields_rejected(self):
        self.req['origin']['finding_ids'] = ['not-in-answer']
        with self.assertRaises(ValueError): self.prepare()
        for field, value in [('available', 1), ('capacity_slots', True), ('pathway_ids', ['fake']), ('owner', {'kind': 'prescriber', 'id': 'x'})]:
            request = copy.deepcopy(self.save_request); request['expected_revision'] = 1; request['capabilities'][0][field] = value
            with self.assertRaises(ValueError): c.save_portfolio(self.path, request)
        self.req['coverage']['verified'] = True
        with self.assertRaises(ValueError): c.coverage(self.req['coverage'])

    def test_buyer_isolation_and_empty_portfolio(self):
        self.req['buyer_id'] = 'other-buyer'
        p = self.prepare(); self.assertFalse(p['options']); self.assertFalse(c.task_view(self.path, 'other-buyer')['tasks'])

    def test_internal_worker_exact_sources_no_contact_or_review_claim(self):
        p = self.prepare(); first = self.queue(p); again = self.queue(p)
        self.assertTrue(again['idempotent_replay']); self.assertEqual(first['task'], again['task'])
        out = self.event(first['task']); packet = out['task']['handoff']
        self.assertEqual(packet['origin']['findings'], self.answer['findings'])
        self.assertFalse(packet['human_review_completed']); self.assertFalse(packet['external_dispatch_performed'])
        self.assertEqual(packet['service_slots_reserved'], 0)
        self.assertTrue(self.event(first['task'])['idempotent_replay'])
        with self.assertRaises(w.WorkConflict): self.event(first['task'], note='Different retry')
        with self.assertRaises(w.WorkConflict): self.event(first['task'], request_id='new')

    def test_blocked_route_cannot_queue(self):
        self.req['coverage']['payer_type'] = 'medicare_advantage'
        with self.assertRaises(ValueError): self.queue()

    def test_source_changes_block_worker(self):
        p = self.prepare(); task = self.queue(p)['task']; self.answer['findings'][0]['text'] = 'Revised'
        with self.assertRaises(w.WorkConflict): c.inspect_plan(self.path, p['id'])
        with self.assertRaises(w.WorkConflict): self.event(task)
        self.assertFalse(c.task_view(self.path, 'buyer1')['tasks'][0]['dependencies_current'])

    def test_service_revision_refreshes_same_task_preserving_history(self):
        p = self.prepare(); task = self.queue(p)['task']; done = self.event(task)['task']
        self.cap['owner']['id'] = 'another-fixture-team'; self.update()
        with self.assertRaises(w.WorkConflict): self.event(done, request_id='stale')
        refreshed = self.queue()['task']
        self.assertEqual(refreshed['id'], task['id']); self.assertEqual(refreshed['revision'], 3)
        self.assertNotIn('handoff', refreshed); self.assertEqual(refreshed['owner']['id'], 'another-fixture-team')
        db = c.db_open(self.path)
        try: self.assertEqual(db.execute('SELECT count(*) FROM capability_events').fetchone()[0], 3)
        finally: db.close()

    def test_policy_and_plan_tampering_invalidate(self):
        p = self.prepare()
        with patch.object(c, 'catalog', return_value={**c.catalog(), 'checked_on': '2026-10-11'}):
            with self.assertRaises(w.WorkConflict): c.inspect_plan(self.path, p['id'])
        db = c.db_open(self.path)
        try:
            p['coverage']['payer_type'] = 'commercial'
            db.execute('UPDATE capability_plans SET payload=? WHERE id=?', (json.dumps(p), p['id'])); db.commit()
        finally: db.close()
        with self.assertRaisesRegex(w.WorkConflict, 'integrity'): c.inspect_plan(self.path, p['id'])

    def test_concurrent_queue_deduplicates(self):
        p = self.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _: self.queue(p), [1, 2]))
        self.assertEqual(sum(not r['idempotent_replay'] for r in results), 1)
        self.assertEqual(len(c.task_view(self.path, 'buyer1')['tasks']), 1)

    def test_cancelled_task_cannot_progress(self):
        t = self.queue()['task']; t = self.event(t, action='cancel')['task']
        with self.assertRaises(ValueError): self.event(t, request_id='later')


class AssessmentRouting(unittest.TestCase):
    def test_existing_program_assessment_origin_is_verified(self):
        import test_program_work as fixture
        f = fixture.ProgramWork(); f.setUp()
        try:
            db = w.database(f.root)
            try: aid = w.load(db, 'work_proposals', f.proposal['proposal_id'])['assessment_ids'][0]
            finally: db.close()
            ref = {'kind': 'assessment', 'id': aid, 'finding_ids': []}
            row = c.origin(f.root, ref); self.assertEqual(row['patient_id'], 'p00')
            f.mutate()
            with self.assertRaises(w.WorkConflict): c.origin(f.root, ref)
        finally: f.tearDown()


if __name__ == '__main__': unittest.main()
