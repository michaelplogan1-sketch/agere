"""Source expansion is additive and cannot clear prior uncertainty or change scope."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import source_navigation as N, backstop_agent as B


def record(key, stream, **fields):
    return {'record_id': key, 'patient_id': 'p1', 'source_table': stream,
            'event_date': '2030-01-01', 'available_date': '2030-01-02',
            '_warehouse_fields': list(fields), **fields}


class Navigation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.records = [record('root', 'diagnostic_access_contacts', order_record_id='order'),
                        record('order', 'diagnostic_orders', supporting_record_ids=['review']),
                        record('review', 'diagnostic_review_interviews'),
                        record('other', 'diagnostic_review_interviews')]
        self.docs = [('root', '', '', '', '2030-01-01', 'Original question context.', ''),
                     ('order', '', '', '', '2030-01-02', 'New linked source.', ''),
                     ('review', '', '', '', '', 'Undated linked account.', ''),
                     ('other', '', '', '', '2030-01-02', 'Unexamined unrelated source.', '')]
        self.questions = {'basis': {'question': 'What source basis is documented?',
                                   'entity': {'kind': 'concept', 'id': 'basis', 'aliases': ['basis']},
                                   'relation': 'documented_basis', 'subject': 'patient'}}
        self.old = {'patient_id': 'p1', 'review_status': 'needs_review',
                    'documents_reviewed': 1, 'review_limitations': '["uncertain meaning"]',
                    'review_json': json.dumps({'basis': [{'record_id': 'root', 'quote': 'Original question context.'}]}),
                    'review_unresolved_json': '[{"field":"basis","record_id":"root"}]'}
        self.call = {'name': 'review_documents',
                     'input': {'patient_ids': ['p1'], 'questions': self.questions,
                               'source_scope': 'focused', 'date_from': '2030-01-01'},
                     'result': {'result_view': 'agere_result_1', 'source_scope': 'focused',
                                'rows': [self.old], 'coverage': [{'patient_id': 'p1', 'source_selection': {
                                    'complete_chart_review': False, 'sources': [
                                        {'record_id': 'root', 'date': '2030-01-01',
                                         'document_text_digest': digest(self.docs[0][5]), 'selected_fields': ['basis']}]}}]}}
        self.tools = SimpleNamespace(question='Investigate the recorded source basis.', as_of='2030-05-27',
                                     native_calls=[self.call], document_reviews=[], stack=SimpleNamespace(path=Path(self.tmp.name)),
                                     in_scope=lambda p: self.assertEqual(p, 'p1'),
                                     _texts=lambda p: self.docs, continuation_identity=lambda: {'source': 1})
        self.source = patch.object(N.record_context, 'received', side_effect=lambda *a: (
            self.records, {'snapshot': {'as_of': self.tools.as_of}}))
        self.source.start(); self.addCleanup(self.source.stop)

    def plan(self):
        return B.Tools._call(self.tools, {'name': 'plan_evidence_expansion',
                                         'input': {'review_result_view': 'agere_result_1'}})

    def test_real_tool_dispatch_expands_exact_links_and_keeps_unexamined_unknown(self):
        before = copy.deepcopy(self.call)
        plan = self.plan()
        self.assertEqual(plan['additional_documents'], [
            {'patient_id': 'p1', 'record_id': 'order'}, {'patient_id': 'p1', 'record_id': 'review'}])
        units = {u['record_id']: u for u in plan['inventory'][0]['fields']['basis']['units']}
        self.assertEqual(units['review']['stratum'], 'unknown_date')
        self.assertEqual(units['other']['interpretation'], 'unexamined')
        self.assertFalse(plan['absence_established'])
        self.assertEqual(self.call, before)
        self.assertEqual(N.load(self.tools, plan['id'])[0]['id'], plan['id'])
        self.assertEqual(self.plan()['id'], plan['id'])

    def test_source_text_and_link_changes_reject_reopen(self):
        for kind in ('text', 'link'):
            with self.subTest(kind=kind):
                plan = self.plan(); old_docs, old_records = copy.deepcopy(self.docs), copy.deepcopy(self.records)
                if kind == 'text': self.docs[1] = (*self.docs[1][:5], 'Changed source.', '')
                else: self.records[0]['order_record_id'] = 'other'
                with self.assertRaisesRegex(ValueError, 'changed'): N.load(self.tools, plan['id'])
                self.docs, self.records = old_docs, old_records

    def test_changed_question_or_field_contract_cannot_reuse_plan(self):
        plan = self.plan(); self.tools.question = 'A different question'
        with self.assertRaisesRegex(ValueError, 'scope'): N.load(self.tools, plan['id'])
        self.tools.question = 'Investigate the recorded source basis.'
        self.questions['basis']['subject'] = 'family_member'
        with self.assertRaisesRegex(ValueError, 'changed'): N.load(self.tools, plan['id'])

    def test_outside_window_and_future_reference_are_not_selected(self):
        self.docs[1] = (*self.docs[1][:4], '2029-01-01', *self.docs[1][5:])
        self.records[2]['available_date'] = '2031-01-01'
        plan = self.plan()
        self.assertEqual(plan['additional_documents'], [])
        field = plan['inventory'][0]['fields']['basis']
        self.assertIn('not_received_by_cutoff', {r['reason'] for r in field['reference_issues']})
        self.assertFalse(field['absence_established'])

    def test_bad_reference_does_not_imply_source_absence(self):
        self.records[0]['order_record_id'] = 'missing'
        plan = self.plan()
        self.assertIsNone(plan['review_next'])
        self.assertEqual(plan['inventory'][0]['fields']['basis']['reference_issues'][0]['reason'],
                         'not_received_for_patient')
        self.assertFalse(plan['absence_established'])

    def test_reader_receives_only_new_sources_and_conserves_original_values(self):
        plan = self.plan(); before = copy.deepcopy(self.call); seen = []
        self.call['input']['field_patient_queries'] = {'basis': "SELECT patient_id FROM patient WHERE patient_id='p1'"}
        plan = self.plan()
        def reader(use):
            seen.append(use)
            return {'rows': [{'patient_id': 'p1', 'review_status': 'reviewed', 'documents_reviewed': 2,
                              'review_limitations': '[]', 'review_json': json.dumps({'basis': [
                                  {'record_id': 'review', 'quote': 'Undated linked account.'}]})}],
                    'coverage': [{'patient_id': 'p1', 'unique_whole_documents': 2}],
                    'accounting': {'new_paid_api_calls': 0}}
        self.tools._call = reader
        result = B.Tools._call(self.tools, {'name': 'review_documents', 'input': {'navigation_id': plan['id']}})
        self.assertEqual(seen[0]['input']['record_ids'], ['order', 'review'])
        self.assertEqual(seen[0]['input']['questions'], self.questions)
        self.assertEqual(seen[0]['input']['field_patient_queries'], self.call['input']['field_patient_queries'])
        self.assertNotIn('date_from', seen[0]['input'])  # undated source stays eligible, explicit IDs bind scope
        row = result['rows'][0]
        self.assertEqual(row['review_status'], 'needs_review')
        self.assertEqual(row['review_limitations'], self.old['review_limitations'])
        self.assertEqual(json.loads(row['review_unresolved_json']), json.loads(self.old['review_unresolved_json']))
        self.assertEqual(json.loads(row['review_json'])['basis'][0], json.loads(self.old['review_json'])['basis'][0])
        self.assertEqual(len(json.loads(row['review_json'])['basis']), 2)
        self.assertEqual(len(result['coverage']), 2)
        self.assertEqual(before['result'], self.call['result'])

    def test_new_failure_reaches_standard_status_and_evidence_views(self):
        self.old.update(review_status='reviewed', review_limitations='[]', review_unresolved_json='[]')
        plan = self.plan()
        self.tools._call = lambda use: {'rows': [{
            'patient_id': 'p1', 'review_status': 'needs_review', 'documents_reviewed': 2,
            'review_limitations': '["new source undecided"]',
            'review_unresolved_json': '[{"field":"basis","record_id":"review"}]',
            'review_json': '{"basis":[]}'}], 'coverage': []}
        out = N.review(self.tools, plan['id']); row = out['rows'][0]
        self.assertEqual(row['review_status'], 'needs_review')
        self.assertIn('new source undecided', json.loads(row['review_limitations']))
        self.assertEqual(json.loads(row['review_unresolved_json'])[0]['record_id'], 'review')
        self.assertEqual(json.loads(row['source_findings_json'])[0]['record_id'], 'root')
        self.assertEqual(self.tools.document_reviews[-1]['rows'], out['rows'])
        self.assertEqual(self.tools.document_reviews[-1]['provider_receipts'], [])

    def test_scope_override_and_partial_review_rejected_before_reader(self):
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, 'only navigation_id'):
            B.Tools._call(self.tools, {'name': 'review_documents',
                                     'input': {'navigation_id': plan['id'], 'patient_ids': ['foreign']}})
        self.call['result']['preview_only'] = True
        with self.assertRaisesRegex(ValueError, 'Complete native'): self.plan()

    def test_cross_patient_graph_rejected_before_new_reading(self):
        self.records[1]['patient_id'] = 'foreign'
        with self.assertRaisesRegex(ValueError, 'same-patient'): self.plan()

    def test_hierarchical_workflow_reads_only_new_branches_and_continues_native_result(self):
        import sqlite3
        from clinical_core.agere.clinical.semantic_analyst import actor_result
        self.docs[3] = ('other', 'hie_document', 'hie_documents', 'letter', '2030-02-02',
                        'An outside source gives another dated account.', '')
        self.docs.append(('later', 'hie_document', 'hie_documents', 'letter', '2030-03-02',
                          'A separate later source remains unexamined.', ''))
        self.records.append(record('later', 'hie_documents'))
        original = copy.deepcopy(self.call)
        plan = self.plan()
        branch = next(b for b in plan['source_branches'] if b['month'] == '2030-02')
        selected = B.Tools._call(self.tools, {'name': 'plan_evidence_expansion', 'input': {
            'review_result_view': 'agere_result_1', 'basis_navigation_id': plan['id'],
            'branch_ids': [branch['id']]}})
        self.assertEqual({d['record_id'] for d in selected['additional_documents']}, {'order','review','other'})
        shown = actor_result(selected)
        self.assertNotIn('inventory', shown)
        self.assertEqual(shown['source_branches'], selected['source_branches'])
        self.assertEqual(shown['native_inventory']['documents'], 5)
        self.assertFalse(shown['absence_established'])
        self.assertEqual(json.loads(Path(self.tmp.name, 'source-navigations', selected['id']+'.json').read_text())['inventory'], selected['inventory'])
        reads=[]
        def reader(use):
            ids=use['input']['record_ids'];reads.append(list(ids))
            return {'source_scope': 'focused', 'rows': [{
                'patient_id': 'p1', 'review_status': 'reviewed', 'documents_reviewed': len(ids),
                'review_limitations': '[]', 'review_unresolved_json': '[]',
                'review_json': json.dumps({'basis': [{'record_id': rid, 'quote': next(d[5] for d in self.docs if d[0]==rid)} for rid in ids]})}],
                'coverage': [{'patient_id': 'p1', 'unique_whole_documents': len(ids)}],
                'accounting': {'new_paid_api_calls': 0}}
        self.tools._call=reader
        combined = B.Tools._call(self.tools, selected['review_next'])
        combined['result_view']='agere_result_2'
        self.tools.native_calls.append({'name': 'review_documents', 'input': {'navigation_id': selected['id']}, 'result': combined})
        # The next expansion starts from the combined native result. Earlier
        # evidence is not re-read or discarded, even when it is unresolved.
        next_plan = N.prepare(self.tools, 'agere_result_2')
        self.assertEqual(next_plan['additional_documents'], [])
        self.assertEqual({b['month'] for b in next_plan['source_branches'] if b['level']=='month'}, {'2030-03'})
        later = next(b for b in next_plan['source_branches'] if b['level']=='month')
        final_plan = N.prepare(self.tools, 'agere_result_2', [later['id']], next_plan['id'])
        final = B.Tools._call(self.tools, final_plan['review_next'])
        self.assertEqual(reads, [['order','other','review'], ['later']])
        self.assertEqual(final['rows'][0]['documents_reviewed'],5)
        self.assertEqual(final['rows'][0]['document_reading_attempts'],5)
        self.assertEqual(final['rows'][0]['source_records_processed'],5)
        self.assertEqual(self.call, original)
        self.assertEqual(final['rows'][0]['review_status'], 'needs_review')
        self.assertEqual(json.loads(final['rows'][0]['review_unresolved_json']), json.loads(self.old['review_unresolved_json']))
        # Use the same native materialization used by the investigator, then
        # reopen its source-bound findings. This is an authored reader adapter,
        # not a claim that a model interpreted these sources correctly.
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        registrations=[]
        semantic=SimpleNamespace(db=db, _authorize=lambda *a: sqlite3.SQLITE_OK,
            register_native_result=lambda name,receipt: registrations.append(name))
        columns, rows=B.output_table(final)
        B.define_result(semantic, 'agere_result_3', columns, rows, receipt=final)
        findings=db.execute("SELECT json_extract(j.value,'$.record_id') FROM agere_result_3 r,json_each(r.review_json,'$.basis') j").fetchall()
        self.assertEqual({r[0] for r in findings}, {'root','order','review','other','later'})
        self.assertEqual(registrations,['agere_result_3'])
        self.assertFalse(final['navigation']['absence_established'])
        import os
        target=os.environ.get('AGERE_NAVIGATION_WORKFLOW_RECEIPT')
        if target:
            Path(target).write_text(json.dumps({'status':'passed','kind':'authored_connected_tool_workflow',
                'model_transport':'prepared_reader_not_live_or_clinically_qualified',
                'original_documents':1,'all_final_documents':len(findings),'incremental_read_batches':reads,
                'original_findings_and_uncertainty_preserved':True,'native_sql_materialization':registrations,
                'native_plan_bytes':len(json.dumps(selected).encode()),'actor_plan_bytes':len(json.dumps(shown).encode()),
                'source_branches_complete':True,'provider_calls':0,'source_navigation_id':selected['id']},indent=2)+'\n')

    def test_branch_selection_cannot_bypass_source_clock_scope_or_inventory_version(self):
        self.records[3]['available_date']='2031-01-01'
        plan=self.plan()
        self.assertEqual(plan['source_branches'], [])
        self.records[3]['available_date']='2030-01-02'
        plan=self.plan();branch=plan['source_branches'][0]['id']
        for ids,basis in [([branch],None),(['foreign'],plan['id']),('not-a-list',plan['id'])]:
            with self.subTest(ids=ids,basis=basis),self.assertRaises(ValueError):
                N.prepare(self.tools,'agere_result_1',ids,basis)
        self.docs[3]=(*self.docs[3][:5],'Changed unlinked source.', '')
        with self.assertRaisesRegex(ValueError,'changed'):
            N.prepare(self.tools,'agere_result_1',[branch],plan['id'])


if __name__ == '__main__': unittest.main()
