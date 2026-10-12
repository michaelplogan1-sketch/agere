"""Declared contract scope remains documentary and changes expire internal work."""
import copy
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
import test_coverage_context as coverage_fixture
from test_program_documents import package
from clinical_core.agere.clinical import participation_evidence as p, participation_work as w
from clinical_core.agere.clinical import care_capabilities as c, coverage_work, coverage_sources
from clinical_core.agere.clinical import document_clause_bindings as clauses, program_documents as documents


class Participation(unittest.TestCase):
    put = coverage_fixture.CoverageWork.put
    update = coverage_fixture.CoverageWork.update
    def setUp(self):
        coverage_fixture.CoverageWork.setUp(self)
        self.row['payer_organization_id'] = 'payer1'; self.row['_warehouse_fields'].append('payer_organization_id'); self.put(self.row)
        ctx = coverage_sources.prepare(self.path, {'patient_id': 'p1', 'as_of': '2026-10-10'})
        self.req['coverage_binding'] = {'context_id': ctx['id'], 'record_id': 'coverage1'}
        self.req['coverage'].update(coverage_sources.select(ctx, 'coverage1')['routing_fields'])
        self.decl = {'id': 'network1', 'service_id': 'pharmacy', 'provider_organization_id': 'provider1',
                     'payer_organization_id': 'payer1', 'plan_id': 'plan1', 'region': 'IL', 'pathway_id': '',
                     'relationship': 'network_contract', 'reported_status': 'participating',
                     'effective_start': '2026-01-01', 'effective_end': '2026-12-31', 'received_on': '2026-10-01',
                     'provenance': 'Synthetic test declaration only', 'clauses': []}

    def save(self, declarations=None, revision=None):
        return p.save(self.path, {'buyer_id': 'buyer1', 'expected_revision': p.registry(self.path, 'buyer1')['revision'] if revision is None else revision,
                      'actor': 'Temporary unit fixture', 'actor_kind': 'synthetic_test', 'declarations': declarations if declarations is not None else [self.decl]})

    def match(self): return w.prepare(self.path, self.req)
    def context(self, plan=None):
        plan = plan or self.match(); return plan['participation_contexts'][plan['options'][0]['id']]

    def queue(self, plan=None):
        plan = plan or self.match()
        return w.enqueue(self.path, {'plan_id': plan['id'], 'option_id': plan['options'][0]['id'],
                         'actor': 'Temporary unit fixture', 'participation_context_id': self.context(plan)['id']})['task']

    def event(self, task, rid='prepare-1', action='prepare_handoff'):
        return w.task_event(self.path, {'task_id': task['id'], 'expected_revision': task['revision'], 'request_id': rid,
                           'action': action, 'actor': 'Temporary unit fixture', 'note': 'Unit fixture; internal review only'})

    def bind_clause(self, changes=None):
        pkg = package(); pkg['spec'].update(effective_start='2026-01-01', effective_end='2027-12-31')
        pkg['provenance']['received_on'] = '2026-01-01'
        for (part, key), value in (changes or {}).items(): pkg[part][key] = value
        key = documents.install(self.path, pkg)['id']; preview = clauses.preview(self.path, key)
        return {'kind': 'program_clause', 'package_id': key, 'input_key': 'report',
                'quote_hash': preview['clauses'][0]['quote_hash'], 'review_revision': 0}

    def test_no_declaration_is_unknown_not_denial_or_network_verification(self):
        report = self.context(); self.assertEqual(report['routes'][0]['status'], 'no_in_scope_declaration')
        self.assertFalse(report['verified_participation']); self.assertFalse(report['verified_benefits'])
        self.assertEqual(report['reported_payer_organization'], 'payer1')

    def test_exact_scope_matches_but_only_as_declaration(self):
        self.save(); report = self.context(); row = report['routes'][0]['declarations'][0]
        self.assertEqual(row['scope_status'], 'declared_scope_matches'); self.assertEqual(row['document_state'], 'no_imported_clause')
        self.assertFalse(row['verified_participation']); self.assertFalse(report['clinical_execution_authorized'])
        self.assertEqual(p.inspect(self.path, report['id']), report)

    def test_scope_mismatch_dimensions_are_preserved(self):
        values = [dict(self.decl, id=k, **{k: v}) for k, v in [('plan_id', 'other'), ('region', 'NY'),
                  ('payer_organization_id', 'other'), ('received_on', '2026-10-11'), ('effective_end', '2026-10-09'),
                  ('effective_start', '2026-10-11'), ('pathway_id', 'access_ckm')]]
        self.save(values); report = self.context()
        self.assertEqual(len(report['routes'][0]['declarations']), 7)
        self.assertTrue(all(r['scope_status'] == 'outside_declared_scope' for r in report['routes'][0]['declarations']))

    def test_unbound_patient_plan_or_payer_remains_unknown(self):
        self.save(); req = copy.deepcopy(self.req); req.pop('coverage_binding')
        req['coverage'].update(plan_id='', provenance='Unverified manual declaration')
        plan = w.prepare(self.path, req); row = self.context(plan)['routes'][0]['declarations'][0]
        self.assertEqual(row['scope_checks']['plan_id'], 'unknown'); self.assertEqual(row['scope_checks']['payer_organization'], 'unknown')
        self.assertEqual(row['scope_status'], 'scope_unresolved')

    def test_conflicts_only_for_same_provider_relationship_and_in_scope(self):
        self.save([self.decl, dict(self.decl, id='negative', reported_status='not_participating'),
                   dict(self.decl, id='other_provider', provider_organization_id='provider2', reported_status='not_participating'),
                   dict(self.decl, id='different_scope', plan_id='different', reported_status='not_participating')])
        conflicts = self.context()['routes'][0]['conflicts']; self.assertEqual(len(conflicts), 1)
        self.assertEqual(set(conflicts[0]['declaration_ids']), {'network1', 'negative'})

    def test_exact_unreviewed_clause_does_not_establish_participation(self):
        self.decl['clauses'] = [self.bind_clause()]; self.save(); row = self.context()['routes'][0]['declarations'][0]
        self.assertEqual(row['document_state'], 'mapping_or_period_unresolved')
        self.assertEqual(row['clauses'][0]['reference']['provenance']['publisher'], 'Agere synthetic engineering example')
        self.assertFalse(row['clauses'][0]['patient_applicability_established'])

    def test_mapping_review_change_expires_saved_queue_context(self):
        b = self.bind_clause(); self.decl['clauses'] = [b]; self.save(); task = self.queue(); self.event(task)
        documents.review(self.path, {'package': b['package_id'], 'expected_revision': 0, 'actor': 'TEMPORARY UNIT FIXTURE',
                                    'status': 'changes_requested', 'note': 'Unit test only'})
        with self.assertRaises(c.work.WorkConflict): self.event(task)
        view = w.task_view(self.path, task['id']); self.assertFalse(view['handoff_participation_current'])
        self.assertEqual(view['participation']['routes'][0]['declarations'][0]['document_state'], 'changed_or_unavailable')

    def test_confirmed_mapping_is_not_declaration_or_network_validation(self):
        b = self.bind_clause()
        documents.review(self.path, {'package': b['package_id'], 'expected_revision': 0, 'actor': 'TEMPORARY UNIT FIXTURE',
                                    'status': 'mapping_confirmed', 'note': 'Unit test only'})
        b['review_revision'] = 1; self.decl['clauses'] = [b]; self.save(); row = self.context()['routes'][0]['declarations'][0]
        self.assertEqual(row['document_state'], 'mapping_and_period_usable')
        self.assertEqual(row['declaration_to_clause_meaning'], 'not_independently_reviewed'); self.assertFalse(row['verified_participation'])

    def test_future_clause_keeps_document_receipt_time_unresolved(self):
        b = self.bind_clause({('provenance', 'received_on'): '2027-01-01'})
        self.decl['clauses'] = [b]; self.save(); row = self.context()['routes'][0]['declarations'][0]
        self.assertEqual(row['clauses'][0]['clocks']['availability'], 'future_or_unknown_arrival')
        self.assertEqual(row['document_state'], 'mapping_or_period_unresolved')

    def test_changed_received_coverage_blocks_participation_reuse(self):
        self.save(); plan = self.match(); task = self.queue(plan); self.event(task)
        self.row['payer_organization_id'] = 'changed'; self.put(self.row)
        with self.assertRaises(c.work.WorkConflict): self.queue(plan)
        with self.assertRaises(c.work.WorkConflict): self.event(task)

    def test_registry_optimistic_concurrency_and_append_only(self):
        self.save(); self.decl['reported_status'] = 'pending'
        with self.assertRaises(c.work.WorkConflict): self.save(revision=0)
        self.save(); db = p.database(self.path)
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM declarations').fetchone()[0], 2)
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM declarations')
        finally: db.close()

    def test_concurrent_saves_allow_one_revision(self):
        def attempt(status):
            try: return self.save([dict(self.decl, reported_status=status)], revision=0)['revision']
            except c.work.WorkConflict: return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool: out = list(pool.map(attempt, ['pending', 'participating']))
        self.assertCountEqual(out, [1, 'conflict'])

    def test_service_buyer_cross_binding_unknown_fields_and_bad_dates_rejected(self):
        for changes in [{'service_id': 'other'}, {'effective_end': '2025-01-01'}, {'relationship': 'verified'},
                        {'relationship': 'program_participation'}, {'received_on': '2026-1-1'}, {'verified_participation': True}]:
            with self.assertRaises(ValueError): self.save([dict(self.decl, **changes)])

    def test_handoff_contains_current_declarations_and_coverage(self):
        self.save(); plan = self.match(); task = self.queue(plan); result = self.event(task)
        self.assertEqual(result['participation_attachment']['participation_id'], self.context(plan)['id'])
        self.assertTrue(w.task_view(self.path, task['id'])['handoff_participation_current'])
        self.assertTrue(coverage_work.task_view(self.path, task['id'])['handoff_coverage_current'])
        self.assertTrue(self.event(task)['idempotent_replay'])
        self.assertEqual(self.queue(plan)['id'], task['id'])

    def test_declaration_change_requires_queue_refresh_then_new_handoff_request(self):
        self.save(); old = self.match(); task = self.queue(old); first = self.event(task)
        self.decl['reported_status'] = 'pending'; self.save()
        with self.assertRaises(c.work.WorkConflict): self.queue(old)
        with self.assertRaises(c.work.WorkConflict): self.event(task)
        self.assertFalse(w.task_view(self.path, task['id'])['handoff_participation_current'])
        task = self.queue(); self.assertEqual(task['id'], first['task']['id'])
        with self.assertRaises(c.work.WorkConflict): self.event(task)
        self.event(task, rid='refresh-2'); self.assertTrue(w.task_view(self.path, task['id'])['handoff_participation_current'])

    def test_wrong_option_context_rejected_before_enqueue(self):
        plan = self.match(); context = self.context(plan)
        with self.assertRaises(ValueError): w.enqueue(self.path, {'plan_id': plan['id'], 'option_id': 'wrong', 'actor': 'fixture', 'participation_context_id': context['id']})

    def test_legacy_task_gets_explicit_unverified_baseline_when_prepared(self):
        plan = coverage_work.prepare(self.path, self.req)
        task = coverage_work.enqueue(self.path, {'plan_id': plan['id'], 'option_id': plan['options'][0]['id'], 'actor': 'fixture'})['task']
        self.assertIsNone(w.queued(self.path, task['id'])); self.event(task)
        self.assertTrue(w.task_view(self.path, task['id'])['handoff_participation_current'])
        self.assertFalse(w.task_view(self.path, task['id'])['verified_participation'])

    def test_hold_and_cancel_never_claim_current_prepared_attachment(self):
        task = self.queue(); task = self.event(task)['task']; task = self.event(task, rid='hold', action='hold')['task']
        self.assertFalse(w.task_view(self.path, task['id'])['handoff_participation_current'])
        self.event(task, rid='cancel', action='cancel')
        with self.assertRaises(c.work.WorkConflict): self.queue()


if __name__ == '__main__': unittest.main()
