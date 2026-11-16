"""Documentary dependency integrity; temporary fixtures are not clinician reviews."""
import copy
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_care_capabilities as care_fixture
import test_record_evidence as record_fixture
from test_warehouse_interpretation import record, ASOF
from test_semantic_reader import LABELS
from clinical_core.contracts import digest
from clinical_core.agere.clinical import evidence_dispositions as d, care_readiness as r
from clinical_core.agere.clinical import care_capabilities as c, semantic_reader as sr
from clinical_core.agere.clinical import document_clause_bindings as clauses, program_documents as documents
from test_program_documents import package


class DocumentaryDecisions(unittest.TestCase):
    put = record_fixture.RecordContext.put
    chain = record_fixture.RecordContext.chain
    def setUp(self):
        record_fixture.fixture.Threads.setUp(self)
        self.chain()
        ev = sqlite3.connect(self.path/'events.sqlite')
        ev.execute('CREATE TABLE events(id TEXT,patient TEXT,payload TEXT)'); ev.close()
        self.receipt = {'id': 'n'*64, 'patient_id': 'P-1', 'snapshot': {'as_of': ASOF},
                        'needs': [{'id': 'need1', 'question': 'What does record a document?', 'selectors': [{'record_id': 'a', 'field': 'requested_information'}]}]}
        self.target = {'kind': 'evidence_need', 'receipt_id': self.receipt['id'], 'need_id': 'need1'}
        for p in [patch.object(d.evidence_needs, 'inspect', side_effect=lambda *_: copy.deepcopy(self.receipt)),
                  patch.object(d.context_builder, 'dependency_identity', return_value=('parent', {}, None, [])),
                  patch.object(d.record_context.longitudinal, 'connect', side_effect=lambda _: (sqlite3.connect(self.path/'events.sqlite'), {}))]:
            p.start(); self.addCleanup(p.stop)

    def field(self, rid='a', field='requested_information'):
        s = d.source_preview(self.path, {'target': self.target, 'record_id': rid})
        f = next(f for f in s['fields'] if f['field'] == field)
        return {'kind': 'field', 'record_id': rid, 'record_hash': s['record_hash'], 'field': field, 'value_hash': f['value_hash']}

    def req(self, **changes):
        row = {'target': self.target, 'expected_revision': 0, 'request_id': 'test-1', 'actor': 'temporary test operator',
               'actor_kind': 'operator', 'disposition': 'addressed', 'rationale': 'Fixture-only documentary decision', 'bindings': [self.field()]}
        row.update(changes); return row

    def test_exact_fields_preserve_false_null_and_exclude_hidden_truth(self):
        row = record('a', 'financial_screens', difficulty_paying_medical_bills=False, missing_value=None,
                     truth={'secret': 'DO NOT TRANSPORT'})
        self.put(row)
        fields = d.field_values(row)
        self.assertIs(fields['difficulty_paying_medical_bills'], False); self.assertIsNone(fields['missing_value'])
        self.assertNotIn('DO NOT TRANSPORT', json.dumps(fields))
        for field in ['difficulty_paying_medical_bills', 'missing_value']:
            b = d.binding(self.path, d.resolve_target(self.path, self.target), self.field(field=field))
            self.assertEqual(b['reference']['value'], row[field]); self.assertFalse(b['clinical_truth_established'])
        b = self.field(field='missing_value'); b['field'] = 'not_received'
        with self.assertRaises(ValueError): d.binding(self.path, d.resolve_target(self.path, self.target), b)

    def test_document_original_text_is_not_received_ocr(self):
        row = record('doc', 'documents', original_text='Hidden generator original', extracted_text='Received extraction')
        self.assertNotIn('original_text', d.field_values(row))
        row.pop('_warehouse_source'); row.pop('_warehouse_fields'); row['text'] = 'Unmapped legacy text'
        self.assertNotIn('text', d.field_values(row)); self.assertEqual(d.field_values(row)['extracted_text'], 'Received extraction')

    def test_nested_received_text_has_exact_path(self):
        row = record('a', 'imported_documents', history=[{'text': 'Exact nested statement', 'truth': 'Hidden'}])
        self.put(row)
        self.assertEqual(d.field_values(row)['history[0].text'], 'Exact nested statement')
        self.assertNotIn('Hidden', json.dumps(d.field_values(row)))
        self.assertEqual(self.field(field='history[0].text')['value_hash'], digest('Exact nested statement'))

    def test_span_exact_offsets_hash_and_patient_boundaries(self):
        b = self.field(); b.pop('value_hash'); b.update(kind='span', start=0, end=6, quote='Review')
        t = d.resolve_target(self.path, self.target)
        self.assertEqual(d.binding(self.path, t, b)['reference']['quote'], 'Review')
        for changes in [{'quote': 'review'}, {'start': True}, {'end': 500}, {'record_hash': 'wrong'}]:
            with self.assertRaises(ValueError): d.binding(self.path, t, {**b, **changes})
        self.put(record('private', 'notes', patient_id='P-2', text='Not this patient'))
        with self.assertRaises(ValueError): d.source_preview(self.path, {'target': self.target, 'record_id': 'private'})

    def test_stable_units_reject_pass_local_aliases(self):
        ctx = d.record_context.create(self.path, 'P-1', 'What is documented?', ['a'])
        unit = ctx['model_context']['evidence'][0]
        b = {'kind': 'source_unit', 'context_id': ctx['id'], 'unit_id': unit['id']}
        t = d.resolve_target(self.path, self.target)
        self.assertEqual(d.binding(self.path, t, b)['reference']['source_unit'], unit)
        with self.assertRaises(ValueError): d.binding(self.path, t, {**b, 'unit_id': 'u1'})

    def test_machine_test_empty_and_future_cannot_address(self):
        for kind in ['machine', 'synthetic_test']:
            with self.assertRaises(ValueError): d.save(self.path, self.req(actor_kind=kind))
        with self.assertRaises(ValueError): d.save(self.path, self.req(bindings=[]))
        self.receipt['snapshot']['as_of'] = '2028-08-31'
        with self.assertRaises(ValueError): d.save(self.path, self.req())
        d.save(self.path, self.req(actor_kind='synthetic_test', disposition='unresolved'))
        out = d.inspect(self.path, self.target)
        self.assertEqual(out['state'], 'machine_observation'); self.assertFalse(out['documentary_question_addressed'])

    def test_append_replay_correction_and_stale_revision(self):
        req = self.req(); d.save(self.path, req)
        self.assertTrue(d.save(self.path, req)['idempotent_replay'])
        with self.assertRaises(c.work.WorkConflict): d.save(self.path, {**req, 'rationale': 'changed'})
        with self.assertRaises(c.work.WorkConflict): d.save(self.path, {**req, 'request_id': 'new'})
        d.save(self.path, {**req, 'request_id': 'correction', 'expected_revision': 1, 'disposition': 'unresolved'})
        out = d.inspect(self.path, self.target)
        self.assertEqual(out['state'], 'unresolved'); self.assertEqual(len(out['history']), 2)
        self.assertFalse(out['clinical_execution_authorized']); self.assertFalse(out['reviewer_identity_verified'])
        db = d.database(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM decisions')
        finally: db.close()

    def test_source_and_parent_changes_make_disposition_stale(self):
        d.save(self.path, self.req()); self.assertEqual(d.inspect(self.path, self.target)['state'], 'addressed')
        self.receipt['needs'][0]['question'] = 'Changed original question'
        self.assertEqual(d.inspect(self.path, self.target)['state'], 'stale')
        self.receipt['needs'][0]['question'] = 'What does record a document?'
        self.put(record('a', 'medication_information_requests', requested_information='Revised source'))
        self.assertEqual(d.inspect(self.path, self.target)['state'], 'stale')

    def test_unavailable_parent_keeps_history_but_cannot_record_new_decision(self):
        d.save(self.path, self.req())
        with patch.object(d.evidence_needs, 'inspect', side_effect=ValueError('Source dependencies changed')):
            out = d.inspect(self.path, self.target)
            self.assertEqual(out['state'], 'stale'); self.assertEqual(len(out['history']), 1)
            self.assertFalse(out['documentary_question_addressed'])
            with self.assertRaises(ValueError): d.save(self.path, self.req())

    def test_concurrent_revision_has_exactly_one_winner(self):
        req = self.req()
        def submit(i):
            try: d.save(self.path, {**req, 'request_id': f'concurrent-{i}'}); return True
            except c.work.WorkConflict: return False
        with ThreadPoolExecutor(max_workers=2) as pool: self.assertEqual(sum(pool.map(submit, range(2))), 1)
        self.assertEqual(d.inspect(self.path, self.target)['revision'], 1)

    def reading(self, key='reading', status='accepted', **changes):
        row, _ = d.source_record(self.path, 'P-1', 'a')
        reading = {'id': key, 'target': {'patient_id': 'P-1', 'record_id': 'a', 'field': 'requested_information',
                   'start': 0, 'end': 6, 'concept': 'review'}, 'source_hash': digest(row),
                   'context': {'context_complete': True}, 'reading': {'labels': LABELS}, 'review_revision': 1,
                   'reviews': [{'status': status, 'revision': 1, 'labels': LABELS}] if status else []}
        reading.update(changes); return reading

    def test_only_consistent_complete_semantic_reviews_bind(self):
        t = d.resolve_target(self.path, self.target)
        b = {'kind': 'semantic_review', 'reading_id': 'reading', 'review_revision': 1}
        for status in [None, 'rejected', 'unresolved']:
            with patch.object(sr, 'inspect', return_value=self.reading(status=status)):
                with self.assertRaises(ValueError): d.binding(self.path, t, b)
        with patch.object(sr, 'inspect', return_value=self.reading(context={'context_complete': False})):
            with self.assertRaises(ValueError): d.binding(self.path, t, b)
        readings = {k: self.reading(k) for k in ['reading', 'second']}
        db = sr.db(self.path)
        with db: db.executemany('INSERT INTO readings VALUES (?,?,?)', [(k, 'P-1', json.dumps(v)) for k, v in readings.items()])
        db.close()
        with patch.object(sr, 'inspect', side_effect=lambda path, key: copy.deepcopy(readings[key])):
            self.assertTrue(d.binding(self.path, t, b)['semantic_review_attached'])
            d.save(self.path, self.req(bindings=[b]))
            readings['second']['reviews'][0]['labels'] = {**LABELS, 'statement': 'patient_denied'}
            with self.assertRaises(ValueError): d.binding(self.path, t, b)
            self.assertEqual(d.inspect(self.path, self.target)['state'], 'stale')

    def test_acquisition_is_patient_and_decision_bounded(self):
        out = d.acquisition_context(self.path, {'target': self.target, 'record_ids': ['a']})
        self.assertEqual(set(out['record_scope']['record_ids']), {'a', 'b', 'c', 'd'})
        self.assertFalse(out['meaning_resolved']); self.assertEqual(out['requests_sent'], 0)
        self.put(record('private', 'notes', patient_id='P-2', text='Private'))
        for ids in [['private'], ['unknown'], ['a', 'a']]:
            with self.assertRaises(ValueError): d.acquisition_context(self.path, {'target': self.target, 'record_ids': ids})
        self.receipt['snapshot']['as_of'] = '2028-08-31'
        with self.assertRaises(ValueError): d.acquisition_context(self.path, {'target': self.target, 'record_ids': ['a']})

    def clause(self, pkg=None):
        key = documents.install(self.path, pkg or package())['id']; p = clauses.preview(self.path, key)
        return {'kind': 'program_clause', 'package_id': key, 'input_key': 'report',
                'quote_hash': p['clauses'][0]['quote_hash'], 'review_revision': p['review']['revision']}

    def test_exact_clause_keeps_publisher_clocks_and_unreviewed_state(self):
        b = self.clause(); t = d.resolve_target(self.path, self.target); dep = d.binding(self.path, t, b)
        self.assertEqual(dep['reference']['clauses'][0]['span']['quote'], 'reports of difficulty paying medical bills')
        self.assertEqual(dep['reference']['provenance']['publisher'], 'Agere synthetic engineering example')
        self.assertIsNone(dep['source_patient_id']); self.assertFalse(dep['patient_applicability_established'])
        self.assertFalse(dep['documentary_mapping_usable'])
        self.assertEqual(len(clauses.catalog(self.path)['packages']), 1)
        with self.assertRaises(ValueError): d.save(self.path, self.req(bindings=[b]))
        d.save(self.path, self.req(bindings=[b], disposition='unresolved', actor_kind='synthetic_test'))
        for override in [{'quote_hash': 'forged'}, {'input_key': 'invented'}, {'review_revision': True}]:
            with self.assertRaises(ValueError): d.binding(self.path, t, {**b, **override})

    def test_clause_review_correction_invalidates_decision(self):
        b = self.clause()
        documents.review(self.path, {'package': b['package_id'], 'expected_revision': 0, 'actor': 'TEMPORARY UNIT FIXTURE',
                                    'status': 'mapping_confirmed', 'note': 'Unit test only; not a real review'})
        b['review_revision'] = 1
        d.save(self.path, self.req(bindings=[b]))
        self.assertEqual(d.inspect(self.path, self.target)['state'], 'addressed')
        documents.review(self.path, {'package': b['package_id'], 'expected_revision': 1, 'actor': 'TEMPORARY UNIT FIXTURE',
                                    'status': 'changes_requested', 'note': 'Test correction only'})
        self.assertEqual(d.inspect(self.path, self.target)['state'], 'stale')
        b['review_revision'] = 2
        with self.assertRaises(ValueError): d.save(self.path, self.req(bindings=[b], expected_revision=1, request_id='revised'))

    def test_confirmed_future_or_expired_clause_cannot_address(self):
        for version, part, field, value in [('future', 'provenance', 'received_on', '2030-01-01'),
                                          ('expired', 'spec', 'effective_end', '2028-01-02')]:
            p = package(version); p[part][field] = value; b = self.clause(p)
            documents.review(self.path, {'package': b['package_id'], 'expected_revision': 0, 'actor': 'TEMPORARY UNIT FIXTURE',
                                        'status': 'mapping_confirmed', 'note': 'Unit test only'})
            b['review_revision'] = 1
            with self.assertRaises(ValueError): d.save(self.path, self.req(bindings=[b]))


class ServiceReadiness(unittest.TestCase):
    setUp = care_fixture.CareCapabilities.setUp
    prepare = care_fixture.CareCapabilities.prepare
    queue = care_fixture.CareCapabilities.queue
    update = care_fixture.CareCapabilities.update

    def decision(self, target, revision=0, disposition='unresolved', bindings=None):
        return d.save(self.path, {'target': target, 'expected_revision': revision, 'request_id': f'{digest(target)}-{revision}',
                   'actor': 'temporary fixture operator', 'actor_kind': 'operator', 'disposition': disposition,
                   'rationale': 'Temporary unit fixture only', 'bindings': bindings or []})

    def event(self, task, rid):
        return r.task_event(self.path, {'task_id': task['id'], 'expected_revision': task['revision'], 'request_id': rid,
                     'action': 'prepare_handoff', 'actor': 'fixture', 'note': 'Internal fixture packet'})

    def test_exact_requirement_no_forged_target(self):
        task = self.queue()['task']; snap = r.snapshot(self.path, task['id'])
        target = snap['routes'][0]['reviews'][0]['target']['target']
        self.assertEqual(d.resolve_target(self.path, target)['target'], target)
        with self.assertRaises(ValueError): d.resolve_target(self.path, {**target, 'requirement_id': 'invented'})
        with self.assertRaises(ValueError): d.resolve_target(self.path, {**target, 'verified': True})
        self.assertFalse(snap['verified_benefits']); self.assertFalse(snap['clinical_execution_authorized'])

    def test_handoff_attachment_stales_and_preserves_revisions(self):
        task = self.queue()['task']; snap = r.snapshot(self.path, task['id'])
        target = snap['routes'][0]['reviews'][0]['target']['target']
        first = self.event(task, 'prepare-1')
        self.assertTrue(r.snapshot(self.path, task['id'])['handoff_readiness_current'])
        self.assertTrue(self.event(task, 'prepare-1')['idempotent_replay'])
        self.decision(target)
        self.assertFalse(r.snapshot(self.path, task['id'])['handoff_readiness_current'])
        with self.assertRaises(c.work.WorkConflict): self.event(task, 'prepare-1')
        latest = self.event(first['task'], 'prepare-2')
        self.assertNotEqual(first['readiness_attachment']['readiness_id'], latest['readiness_attachment']['readiness_id'])
        self.assertTrue(r.snapshot(self.path, task['id'])['handoff_readiness_current'])
        db = r.storage(self.path)
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM readiness_handoffs').fetchone()[0], 2)
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM readiness_handoffs')
        finally: db.close()

    def test_all_documentary_dispositions_do_not_authorize_blocked_route(self):
        self.req['coverage']['payer_type'] = 'commercial'
        self.cap['pathway_ids'].append('commercial_virtual'); self.update()
        task = self.queue()['task']; snap = r.snapshot(self.path, task['id'])
        fake = {'selector': {'kind': 'field'}, 'clocks': {'availability': 'available_by_decision'}}
        with patch.object(d, 'binding', return_value=fake):
            for route in snap['routes']:
                for review in route['reviews']:
                    self.decision(review['target']['target'], disposition='addressed', bindings=[{'kind': 'field'}])
            out = r.snapshot(self.path, task['id'])
        self.assertEqual([v['status'] for v in out['routes']], ['route_blocked', 'documentary_dispositions_recorded'])
        self.assertFalse(out['clinical_execution_authorized']); self.assertFalse(out['verified_benefits'])


if __name__ == '__main__': unittest.main()
