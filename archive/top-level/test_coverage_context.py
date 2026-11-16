"""Literal enrollment/eligibility evidence does not verify current benefits."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
import test_evidence_threads as records_fixture
import test_care_capabilities as care_fixture
from test_warehouse_interpretation import record, ASOF
from clinical_core.agere.clinical import coverage_sources as s, coverage_work as w, care_capabilities as c


class CoverageSources(unittest.TestCase):
    setUp = records_fixture.Threads.setUp
    put = records_fixture.Threads.put

    def prepare(self, date=ASOF): return s.prepare(self.path, {'patient_id': 'P-1', 'as_of': date})

    def verification(self, rid='v', **fields):
        return record(rid, 'coverage_verifications', **{'plan_id': 'plan1', 'coverage_type_reported': 'medicare',
                      'status': 'active_returned', 'service_reference_date': '2028-09-01',
                      'payer_organization_id': 'payer', **fields})

    def test_ambiguous_medicare_and_historical_response_remain_unknown(self):
        self.put(self.verification()); out = self.prepare(); chosen = s.select(out, 'v')
        self.assertEqual(chosen['routing_fields']['payer_type'], 'unknown')
        self.assertEqual(chosen['routing_fields']['plan_id'], 'plan1')
        self.assertEqual(chosen['report']['temporal_scope'], 'historical_service_date')
        self.assertFalse(chosen['report']['verified_current_membership']); self.assertFalse(out['verified_benefits'])
        self.assertEqual(s.inspect(self.path, out['id']), out)

    def test_service_date_match_still_is_only_a_report(self):
        self.put(self.verification(service_reference_date=ASOF, coverage_type_reported='commercial'))
        out = self.prepare(); chosen = s.select(out, 'v')
        self.assertEqual(chosen['report']['temporal_scope'], 'reported_for_decision_date')
        self.assertEqual(chosen['routing_fields']['payer_type'], 'commercial')
        self.assertFalse(chosen['report']['verified_benefits'])

    def test_arrival_cutoff_and_patient_are_not_inferred(self):
        self.put(self.verification('private', patient_id='P-2'), self.verification('later', available_date='2028-09-20'))
        out = self.prepare('2028-09-10'); self.assertFalse(out['reports'])
        for rid in ['private', 'later', 'unknown']:
            with self.assertRaises(ValueError): s.select(out, rid)
        self.assertFalse(out['verified_benefits'])

    def test_end_unknown_and_uninsured_do_not_imply_current_membership_or_self_pay(self):
        self.put(record('r', 'coverage', coverage_type='uninsured', status='no_active_plan_reported', effective_start='2028-01-01'))
        chosen = s.select(self.prepare(), 'r')
        self.assertEqual(chosen['report']['temporal_scope'], 'enrollment_end_unknown')
        self.assertEqual(chosen['routing_fields']['payer_type'], 'unknown'); self.assertEqual(chosen['routing_fields']['plan_id'], '')
        self.assertTrue(any('self-pay' in v for v in chosen['report']['caveats']))

    def test_supersession_and_future_service_reports_cannot_supply_routing(self):
        self.put(self.verification('old'), self.verification('new', supersedes_record_id='old'),
                 self.verification('future', service_reference_date='2028-10-01'),
                 record('expired', 'coverage', coverage_type='commercial', effective_start='2028-01-01', effective_end='2028-06-30'))
        out = self.prepare()
        for rid in ['old', 'future', 'expired']:
            with self.assertRaises(ValueError): s.select(out, rid)
        self.assertEqual(s.select(out, 'new')['record_id'], 'new')

    def test_conflicting_same_plan_date_reports_are_retained(self):
        self.put(self.verification('active'), self.verification('inactive', status='inactive_returned'))
        out = self.prepare(); self.assertEqual(len(out['conflicts']), 1)
        self.assertEqual(len(s.select(out, 'active')['conflicts']), 1)
        self.assertEqual(len(out['reports']), 2)

    def test_change_invalidates_coverage_but_unrelated_source_does_not(self):
        self.put(self.verification()); out = self.prepare()
        self.put(record('note', 'notes', text='Unrelated clinical note'))
        self.assertEqual(s.inspect(self.path, out['id']), out)
        self.put(self.verification('second', status='inactive_returned'))
        with self.assertRaises(c.work.WorkConflict): s.inspect(self.path, out['id'])

    def test_projection_excludes_hidden_truth_and_unnecessary_member_identifiers(self):
        self.put(self.verification(truth={'label': 'SECRET'}, member_identifier='MEMBER_SECRET', arbitrary_field='UNMAPPED'))
        out = self.prepare(); rendered = json.dumps(out)
        for secret in ['SECRET', 'MEMBER_SECRET', 'UNMAPPED']: self.assertNotIn(secret, rendered)
        self.assertIn('record_hash', rendered)

    def test_source_receipts_immutable_and_dates_canonical(self):
        self.put(self.verification()); self.prepare()
        db = s.database(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM contexts')
        finally: db.close()
        for date in ['2028-9-1', 'not-a-date', None, True]:
            with self.assertRaises((ValueError, TypeError)): self.prepare(date)


class CoverageWork(unittest.TestCase):
    prepare_base = care_fixture.CareCapabilities.prepare
    update = care_fixture.CareCapabilities.update
    def setUp(self):
        care_fixture.CareCapabilities.setUp(self)
        self.cap['pathway_ids'] = []; self.update()
        self.db = sqlite3.connect(self.path/'stack.sqlite3'); self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY);CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT);')
        self.db.executemany('INSERT INTO patients VALUES (?)', [('p1',), ('p2',)]); self.db.commit()
        self.row = record('coverage1', 'coverage_verifications', patient_id='p1', event_date='2026-10-01', available_date='2026-10-02',
                          coverage_type_reported='medicare', plan_id='plan1', service_reference_date='2026-10-01', status='active_returned')
        self.put(self.row)
        ctx = s.prepare(self.path, {'patient_id': 'p1', 'as_of': '2026-10-10'})
        self.binding = {'context_id': ctx['id'], 'record_id': 'coverage1'}
        self.req['coverage'].update(s.select(ctx, 'coverage1')['routing_fields'])
        self.req['coverage_binding'] = self.binding

    def put(self, row):
        self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)', (row['record_id'], row['patient_id'], row['source_table'], json.dumps(row))); self.db.commit()

    def match(self): return w.prepare(self.path, self.req)
    def queue(self, plan): return w.enqueue(self.path, {'plan_id': plan['id'], 'option_id': plan['options'][0]['id'], 'actor': 'fixture'})['task']
    def event(self, task): return w.task_event(self.path, {'task_id': task['id'], 'expected_revision': task['revision'], 'request_id': 'fixture-handoff', 'action': 'prepare_handoff', 'actor': 'fixture', 'note': 'Fixture only'})

    def test_source_bound_work_preserves_ambiguity_and_attaches_exact_report(self):
        plan = self.match(); task = self.queue(plan); out = self.event(task)
        self.assertEqual(plan['coverage']['payer_type'], 'unknown')
        self.assertEqual(out['coverage_attachment']['coverage_selection']['record_id'], 'coverage1')
        self.assertFalse(out['coverage_attachment']['verified_benefits'])
        self.assertTrue(w.task_view(self.path, task['id'])['handoff_coverage_current'])
        self.assertTrue(self.event(task)['idempotent_replay'])
        self.assertEqual(c.inspect_plan(self.path, plan['id'])['id'], plan['id'])

    def test_forged_payer_plan_or_provenance_rejected(self):
        for key, value in [('payer_type', 'original_medicare'), ('plan_id', 'other'), ('provenance', 'manually overwritten'), ('as_of', '2026-10-11')]:
            req = copy.deepcopy(self.req); req['coverage'][key] = value
            with self.assertRaises(ValueError): w.prepare(self.path, req)

    def test_cross_patient_binding_rejected(self):
        self.answer['patient_id'] = 'p2'
        with self.assertRaises(ValueError): self.match()

    def test_changed_source_blocks_queue_handoff_and_marks_attachment_stale(self):
        plan = self.match(); task = self.queue(plan); self.event(task)
        self.put({**self.row, 'status': 'inactive_returned'})
        with self.assertRaises(c.work.WorkConflict): self.queue(plan)
        with self.assertRaises(c.work.WorkConflict): self.event(task)
        view = w.task_view(self.path, task['id']); self.assertFalse(view['coverage_dependencies_current']); self.assertFalse(view['handoff_coverage_current'])

    def test_matching_adapter_revision_can_prepare_new_immutable_match(self):
        first = self.match()
        with patch.object(w, 'identity', return_value='new-adapter'):
            second = self.match(); self.assertNotEqual(first['id'], second['id'])
            self.assertIsNotNone(w.association(self.path, c.inspect_plan(self.path, second['id'])))
            with self.assertRaises(c.work.WorkConflict): w.association(self.path, c.inspect_plan(self.path, first['id']))

    def test_manual_legacy_context_still_works_but_cannot_impersonate_bound_evidence(self):
        req = copy.deepcopy(self.req); req.pop('coverage_binding'); req['coverage']['provenance'] = 'Unverified manual context'
        plan = w.prepare(self.path, req); task = self.queue(plan); self.assertNotIn('coverage_attachment', self.event(task))
        self.assertIsNone(w.task_view(self.path, task['id'])['coverage_receipt'])
        req['coverage']['provenance'] = 'received-coverage:forged'
        plan = w.prepare(self.path, req)
        with self.assertRaises(c.work.WorkConflict): self.queue(plan)


if __name__ == '__main__': unittest.main()
