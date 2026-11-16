"""Source suspension reaches saved answers and work without rewriting evidence."""
import copy
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import shared_planner as S
from clinical_core.agere.clinical import typed_document_review as T
from clinical_core.agere.clinical import span_repair as SP
from clinical_core.agere.deliverables import answer_context as A
from clinical_core.agere.deliverables import patient_evidence as P


class SourceDisposition(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)
        from clinical_core.agere.gateway import BudgetLedger
        forbidden = patch.object(BudgetLedger, 'reserve', side_effect=AssertionError('No provider dispatch in source-state regressions'))
        forbidden.start()
        self.addCleanup(forbidden.stop)
        self.result = S.compose(self.stack, 'List patients with diabetes')
        self.question = 'List patients with diabetes'
        self.calls = []

        def judge(task, named, evidence, **kwargs):
            self.calls.append(task)
            return {'judgments': {key: {'label': 'finding', 'provider': 'fixture',
                    'decided_by': 'fixture', 'probabilities': {'finding': .95, 'unknown': .05}}
                    for key in named}, 'steps': [{'request_hash': 'authored-request'}], 'cost_usd': 0}

        tools = SimpleNamespace(stack=SimpleNamespace(path=self.path))
        reading = T.cached_source_judge(tools, judge, patient_id='A')(
            'Authored source reading', {'field': {'instructions': {
                'source_context_id': 'note', 'question': 'What was documented?', 'target_block': 'block'}}},
            [{'id': 'note', 'date': '2030-01-01', 'text': 'A documented synthetic observation.'}],
            as_of='2030-05-27')
        self.observation = reading['judgments']['field']['source_observation_id']
        self.assertEqual(T.bind_assertion_observations(self.path, 'assertion-active', [self.observation]), 1)
        self.key = self.capture('assertion-active')
        self.unrelated = self.capture('assertion-unrelated')

    def capture(self, assertion_id, *, corrected=False):
        result = copy.deepcopy(self.result)
        finding = {'value': 'Authored observation', 'assertion': {'id': assertion_id}}
        review = {'questions': {'field': 'What was documented?'}, 'rows': [
            {'patient_id': 'A', 'review_json': json.dumps({'field': [finding]})}]}
        result['investigation'] = {'document_reviews': [review], 'tool_calls': []}
        if corrected:
            result['investigation']['document_reviews'].append({
                'candidate_observation_review': {'checked_assertions': [
                    {'patient_id': 'A', 'field': 'field', 'assertion_id': assertion_id}]},
                'questions': {'field': 'What was documented?'}, 'rows': [
                    {'patient_id': 'A', 'review_json': json.dumps({'field': []})}]})
        return A.capture(self.path, self.question, result)['id']

    def suspend(self):
        return T.quarantine_assertions(self.path, ['assertion-active'], 'Authored unadjudicated objection')

    def request(self, key=None, program='custom'):
        return {'context_id': key or self.key, 'program': program, 'patient_ids': ['A'],
                'owner': 'Fixture review team', 'note': 'Synthetic test only'}

    def test_suspension_blocks_program_work_but_not_unrelated_answers(self):
        target = A.root(self.path) / self.key / 'answer.json'
        original = target.read_bytes()
        before = A.source_identity(self.path)
        self.assertFalse(A.load(self.path, self.key, current=True)['stale'])
        self.assertEqual(self.suspend(), 1)
        self.assertEqual(before, A.source_identity(self.path))
        with self.assertRaisesRegex(ValueError, 'suspended source reading'):
            A.create_work(self.path, self.request())
        self.assertEqual(A.inspect(self.path, self.key)['items'], [])
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(A.load(self.path, self.unrelated, current=True)['stale'])

    def test_source_review_stays_open_and_prior_reading_is_audit_only(self):
        corrected = self.capture('assertion-active', corrected=True)
        original = (A.root(self.path) / self.key / 'answer.json').read_bytes()
        self.assertEqual(self.suspend(), 1)
        context = A.load(self.path, self.key)
        self.assertFalse(context['stale'])
        self.assertFalse(context['invalidated'])
        self.assertTrue(context['source_review_required'])
        check = context['dependency_validation'][0]
        self.assertTrue(check['derivation_current'])
        self.assertTrue(check['observation_current'])
        self.assertFalse(check['source_readings_current'])
        self.assertEqual(P.active_assertion_ids(context), ['assertion-active'])
        projected = P.projection(context)['notes']['A'][0]
        self.assertEqual(projected['findings']['field'], [])
        self.assertEqual(projected['prior_findings']['field'][0]['assertion']['id'], 'assertion-active')
        self.assertTrue(projected['source_reading_suspended'])
        self.assertFalse(A.load(self.path, corrected, current=True)['source_review_required'])
        created = A.create_work(self.path, self.request(program='evidence_review'))
        item = created['items'][0]
        self.assertFalse(item['evidence_review']['clinical_execution_authorized'])
        self.assertEqual(item['evidence_review']['suspended_source_readings'][0]['assertion_id'], 'assertion-active')
        updated = A.update_work(self.path, {'context_id': self.key, 'item_id': item['id'],
            'revision': item['revision'], 'state': 'in_review', 'owner': item['owner'], 'note': 'Review source'})
        self.assertEqual(updated['items'][0]['state'], 'in_review')
        self.assertEqual((A.root(self.path) / self.key / 'answer.json').read_bytes(), original)

    def counts(self):
        db = A._db(self.path, A.load(self.path, self.key))
        try:
            return {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                    for table in ('work_items', 'work_events', 'work_assessments', 'question_decisions')}
        finally:
            db.close()

    def test_suspension_during_transaction_rolls_back_all_rows(self):
        before = self.counts()
        now = A.now
        def during_write():
            self.suspend()
            return now()
        with patch.object(A, 'now', side_effect=during_write):
            with self.assertRaisesRegex(ValueError, 'suspended source reading'):
                A.create_work(self.path, self.request())
        self.assertEqual(before, self.counts())

    def test_source_review_transaction_does_not_commit_an_outdated_disposition(self):
        before = self.counts()
        now = A.now
        def during_write():
            self.suspend()
            return now()
        with patch.object(A, 'now', side_effect=during_write):
            with self.assertRaisesRegex(ValueError, 'Source-reading status changed'):
                A.create_work(self.path, self.request(program='evidence_review'))
        self.assertEqual(before, self.counts())

    def test_store_failure_is_unavailable_and_inspection_survives(self):
        before = self.counts()
        with sqlite3.connect(T.observation_path(self.path)) as db:
            db.execute('DELETE FROM observations WHERE id=?', (self.observation,))
        context = A.load(self.path, self.key)
        self.assertEqual(context['source_disposition']['status'], 'unavailable')
        self.assertFalse(context['source_review_required'])
        for program in ('custom', 'evidence_review'):
            with self.assertRaisesRegex(ValueError, 'Source-reading status is unavailable'):
                A.create_work(self.path, self.request(program=program))
        self.assertEqual(before, self.counts())
        self.assertFalse(A.load(self.path, self.unrelated, current=True)['stale'])

    def test_span_and_typed_suspensions_share_the_existing_read_only_guard(self):
        self.assertTrue(SP.remember_observation(self.path, 'span-fixture', {'label': 'finding'},
                                              ['authored-span-request'], assertion_id='assertion-span'))
        key = self.capture('assertion-span')
        self.assertFalse(A.load(self.path, key, current=True)['source_review_required'])
        self.assertEqual(SP.quarantine_assertions(self.path, ['assertion-span'], 'Authored objection'), 1)
        with self.assertRaisesRegex(ValueError, 'suspended source reading'):
            A.load(self.path, key, current=True)
        self.assertFalse(A.load(self.path, self.key, current=True)['source_review_required'])

    def test_prior_only_and_direct_answer_assertions_are_distinguished(self):
        context = {'answer': {'table': [{'patient_id': 'A',
            'current': {'assertion': {'id': 'current'}},
            'prior_observations': [{'assertion': {'id': 'prior'}}]}]}}
        self.assertEqual(P.active_assertion_ids(context), ['current'])

    def test_missing_store_blocks_assertion_answers_but_pure_sql_needs_no_store(self):
        T.observation_path(self.path).unlink()
        self.assertEqual(A.load(self.path, self.key)['source_disposition']['status'], 'unavailable')
        sql_key = A.capture(self.path, self.question, self.result)['id']
        self.assertTrue(A.load(self.path, sql_key, current=True)['source_disposition']['source_readings_current'])

    def test_lazy_span_only_store_and_corrupt_span_receipts(self):
        T.observation_path(self.path).unlink()
        SP.remember_observation(self.path, 'only-span', {'label': 'finding'},
                                ['authored-span-request'], assertion_id='span-only')
        status = T.assertion_disposition(self.path, ['span-only', 'not-bound'])
        self.assertEqual(status['status'], 'current')
        self.assertEqual(status['checked_assertion_ids'], ['span-only'])
        self.assertEqual(status['unchecked_assertion_ids'], ['not-bound'])
        with sqlite3.connect(T.observation_path(self.path)) as db:
            db.execute('DROP TRIGGER span_observations_immutable')
            db.execute("UPDATE span_observations SET payload=json_set(payload,'$.answer.label','irrelevant')")
        self.assertEqual(T.assertion_disposition(self.path, ['span-only'])['status'], 'unavailable')

    def test_derived_saved_context_keeps_parent_source_dependency(self):
        child = copy.deepcopy(self.result)
        child['cohort_transform'] = {'parent_context_id': self.key}
        key = A.capture(self.path, 'Show these saved patients', child)['id']
        A.load(self.path, key, current=True)
        self.assertEqual(self.suspend(), 1)
        with self.assertRaisesRegex(ValueError, 'suspended source reading'):
            A.load(self.path, key, current=True)

    def test_review_retirement_cannot_discharge_an_active_answer_claim(self):
        result = copy.deepcopy(self.result)
        result['answer'].setdefault('table', [{'patient_id': 'A'}])[0]['documentary_claim'] = {'assertion': {'id': 'assertion-active'}}
        result['investigation'] = {'document_reviews': [{
            'rows': [{'patient_id': 'A', 'review_json': json.dumps({'field': [
                {'assertion': {'id': 'assertion-active'}}]})}]}, {
            'candidate_observation_review': {'checked_assertions': [
                {'patient_id': 'A', 'field': 'field', 'assertion_id': 'assertion-active'}]}, 'rows': []}]}
        key = A.capture(self.path, self.question, result)['id']
        self.assertEqual(P.active_assertion_ids(A.load(self.path, key)), ['assertion-active'])
        self.assertEqual(self.suspend(), 1)
        with self.assertRaisesRegex(ValueError, 'suspended source reading'):
            A.load(self.path, key, current=True)

    def test_child_lessons_check_ancestor_sources_before_grounding_and_reuse(self):
        from clinical_core.agere.clinical import procedural_memory as M
        result = copy.deepcopy(self.result)
        result['cohort_transform'] = {'parent_context_id': self.key}
        child = A.capture(self.path, self.question, result)['id']
        def propose(instruction):
            return M.propose(self.path, child, instruction,
                probes=[{'query': 'SELECT COUNT(*) AS n FROM patient', 'returns': 'count', 'expected': {'value': 5}}],
                required_fields={'patient': ['patient_id']})
        active = propose('Preserve the exact source trajectory.')
        M.ground(self.path, active)
        pending = propose('Retain parent source uncertainty.')
        self.assertEqual([row['id'] for row in M.select(self.path, self.question)], [active])
        self.assertEqual(self.suspend(), 1)
        self.assertEqual(M.select(self.path, self.question), [])
        with self.assertRaisesRegex(ValueError, 'source readings'):
            M.ground(self.path, pending)

    def test_work_fingerprints_stay_durable_while_inspection_flags_change(self):
        from clinical_core.agere.deliverables.receipts import fingerprint
        A.create_work(self.path, self.request())
        before = fingerprint(A.work(self.path, A.load(self.path, self.key)))
        self.assertEqual(self.suspend(), 1)
        self.assertEqual(before, fingerprint(A.work(self.path, A.load(self.path, self.key))))
        self.assertTrue(A.inspect(self.path, self.key)['items'][0]['source_review_required'])

    def test_retained_render_rechecks_live_projection_without_rewriting_archive_authority(self):
        context, bundle = A.evidence_bundle(self.path, self.key, retained_inspection=True)
        validate = A.bundle_validator(self.path, bundle)
        validate()
        self.assertEqual(self.suspend(), 1)
        with self.assertRaisesRegex(ValueError, 'Source-reading status changed during rendering'):
            validate()
        A.bundle_validator(self.path, bundle, source_projection=False)()

    def test_new_unquarantined_cache_coverage_does_not_abort_work(self):
        now = A.now
        def during_write():
            T.bind_assertion_observations(self.path, 'assertion-unrelated', [self.observation])
            return now()
        with patch.object(A, 'now', side_effect=during_write):
            created = A.create_work(self.path, self.request(key=self.unrelated))
        self.assertEqual(len(created['items']), 1)
        self.assertFalse(created['context']['source_review_required'])

    def test_update_race_rolls_back_revision_state_and_event(self):
        item = A.create_work(self.path, self.request())['items'][0]
        before = self.counts()
        now = A.now
        def during_write():
            self.suspend()
            return now()
        with patch.object(A, 'now', side_effect=during_write):
            with self.assertRaisesRegex(ValueError, 'suspended source reading'):
                A.update_work(self.path, {'context_id': self.key, 'item_id': item['id'],
                    'revision': item['revision'], 'state': 'in_review', 'owner': item['owner'], 'note': 'Must roll back'})
        stored = A.work(self.path, A.load(self.path, self.key))[0]
        self.assertEqual((stored['revision'], stored['state'], stored['note']),
                         (item['revision'], item['state'], item['note']))
        self.assertEqual(before, self.counts())

    def test_shared_source_observation_suspends_each_dependent_assertion(self):
        T.bind_assertion_observations(self.path, 'assertion-unrelated', [self.observation])
        self.assertEqual(self.suspend(), 1)
        with self.assertRaisesRegex(ValueError, 'suspended source reading'):
            A.load(self.path, self.unrelated, current=True)

    def test_batched_lookup_preserves_checked_and_unchecked_coverage(self):
        ids = ['assertion-active', *['unchecked-' + str(index) for index in range(1001)]]
        status = T.assertion_disposition(self.path, ids)
        self.assertEqual(status['status'], 'current')
        self.assertEqual(status['checked_assertion_ids'], ['assertion-active'])
        self.assertEqual(len(status['unchecked_assertion_ids']), 1001)

    def test_answer_using_a_suspended_lesson_is_dependent_not_an_unrelated_control(self):
        from clinical_core.agere.clinical import procedural_memory as M
        lesson = M.propose(self.path, self.key, 'Preserve the exact source trajectory.',
            probes=[{'query': 'SELECT COUNT(*) AS n FROM patient', 'returns': 'count', 'expected': {'value': 5}}],
            required_fields={'patient': ['patient_id']})
        M.ground(self.path, lesson)
        result = copy.deepcopy(self.result)
        result['planner_receipt'].setdefault('interpretation', {})['procedural_memory'] = {
            'identity': M.identity(self.path, self.question, population=None), 'lesson_ids': [lesson]}
        consumer = A.capture(self.path, self.question, result)['id']
        A.load(self.path, consumer, current=True)
        self.assertEqual(self.suspend(), 1)
        inspected = A.load(self.path, consumer)
        self.assertTrue(inspected['invalidated'])
        self.assertTrue(any('procedural lesson' in issue for issue in inspected['dependency_validation'][0]['issues']))
        self.assertFalse(A.load(self.path, self.unrelated, current=True)['invalidated'])

    def test_existing_program_work_is_marked_and_cannot_be_updated_after_suspension(self):
        item = A.create_work(self.path, self.request())['items'][0]
        before = self.counts()
        self.assertEqual(self.suspend(), 1)
        self.assertTrue(A.inspect(self.path, self.key)['items'][0]['source_review_required'])
        with self.assertRaisesRegex(ValueError, 'suspended source reading'):
            A.update_work(self.path, {'context_id': self.key, 'item_id': item['id'],
                'revision': item['revision'], 'state': 'in_review', 'owner': item['owner'], 'note': ''})
        self.assertEqual(before, self.counts())

    def test_suspended_sources_cannot_propose_ground_or_select_reusable_lessons(self):
        from clinical_core.agere.clinical import procedural_memory as M
        def propose():
            return M.propose(self.path, self.key, 'Retain the exact documented question and source.',
                probes=[{'query': 'SELECT COUNT(*) AS n FROM patient', 'returns': 'count', 'expected': {'value': 5}}],
                required_fields={'patient': ['patient_id']})
        active = propose()
        M.ground(self.path, active)
        self.assertEqual([r['id'] for r in M.select(self.path, self.question)], [active])
        pending = M.propose(self.path, self.key, 'Preserve source uncertainty without inventing a reason.',
            probes=[{'query': 'SELECT COUNT(*) AS n FROM patient', 'returns': 'count', 'expected': {'value': 5}}],
            required_fields={'patient': ['patient_id']})
        self.assertEqual(self.suspend(), 1)
        with self.assertRaisesRegex(ValueError, 'source readings'):
            propose()
        with self.assertRaisesRegex(ValueError, 'source readings'):
            M.ground(self.path, pending)
        self.assertEqual(M.select(self.path, self.question), [])


if __name__ == '__main__':
    unittest.main()
