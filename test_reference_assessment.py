"""Evidence judgment contracts, exact source scope and changed-evidence behavior."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import reference_assessment as A


class AssessmentContract(unittest.TestCase):
    def setUp(self):
        self.w = {'findings': [{'id': 'a', 'citations': [{'id': '0'}]}, {'id': 'b', 'citations': [{'id': '0'}]}]}
        self.result = {'status': 'mixed', 'summary': 'Historical equipment support and travel limitations; current feasibility is unresolved.',
            'findings': [{'finding_id': 'a', 'relation': 'supporting', 'reason': 'Equipment reported.', 'citation_ids': ['0']},
                         {'finding_id': 'b', 'relation': 'contrary', 'reason': 'Travel barrier reported.', 'citation_ids': ['0']}],
            'missing': [{'question': 'Does this remain true?', 'decision_effect': 'Current information could change feasible delivery.'}]}

    def parse(self, result=None, **kw):
        return A.parse({'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(result or self.result)}], **kw}, self.w)

    def test_support_contrary_missing_are_distinct(self):
        self.assertEqual(self.parse(), self.result)
        self.assertFalse(A.LIMITS['clinical_applicability_established'])

    def test_no_omission_duplicate_or_invented_finding(self):
        for rows in [self.result['findings'][:1], [self.result['findings'][0]]*2,
                     [self.result['findings'][0], {**self.result['findings'][1], 'finding_id': 'invented'}]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError): self.parse({**self.result, 'findings': rows})

    def test_support_cannot_have_missing_or_foreign_citation(self):
        for citations in [[], ['1'], ['0', '0'], [0]]:
            bad = copy.deepcopy(self.result); bad['findings'][0]['citation_ids'] = citations
            with self.subTest(citations=citations), self.assertRaises(ValueError): self.parse(bad)

    def test_status_cannot_erase_contrary_evidence(self):
        with self.assertRaises(ValueError): self.parse({**self.result, 'status': 'supports_documentary_answer'})

    def test_insufficient_needs_a_discriminating_question(self):
        value = copy.deepcopy(self.result); value['status'] = 'insufficient'; value['missing'] = []
        for f in value['findings']: f['relation'] = 'context_only'
        with self.assertRaises(ValueError): self.parse(value)

    def test_truncated_output_rejected(self):
        with self.assertRaises(ValueError): self.parse(stop_reason='max_tokens')

    def test_no_silent_input_truncation(self):
        with self.assertRaises(ValueError): A.body({'findings': [{'text': 'x'*22000}]})

    def test_exact_source_patient_hash_and_quote_binding(self):
        citation = {'quote': 'Reported equipment', 'citation_verified': True,
                    'source': {'patient_id': 'p', 'record_id': 'r', 'record_hash': 'hash', 'source_table': 'notes'}}
        p = {'question': 'Original broad question', 'reference_question': 'Access?', 'preview': {
            'patient_id': 'p', 'available_by': '2029-01-01', 'selected_record_ids': ['r'], 'excluded_records': 20,
            'received_inventory': {'records': {'r': {'record_hash': 'hash'}}},
            'reference': {k: 'ref' for k in ('id', 'title', 'published_on', 'checked_on', 'paraphrase', 'evidence_limitations')}}}
        answer = {'coverage': {'unassessed_units': []}, 'unanswered': [], 'findings': [
            {'id': 'f', 'kind': 'documented', 'text': 'Reported equipment', 'citations': [citation]}]}
        self.assertEqual(A.wire(p, [answer])['findings'][0]['citations'][0]['quote'], citation['quote'])
        for field, value in [('patient_id', 'other'), ('record_id', 'unknown'), ('record_hash', 'changed')]:
            bad = copy.deepcopy(answer); bad['findings'][0]['citations'][0]['source'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): A.wire(p, [bad])


class InferenceAccounting(unittest.TestCase):
    def test_completed_source_reads_are_counted_after_bounded_fallback(self):
        """A successful response counts the two new upstream requests, once."""
        import sqlite3
        ledger = SimpleNamespace(db=sqlite3.connect(':memory:'))
        self.addCleanup(ledger.db.close)
        ledger.db.execute('CREATE TABLE calls(id TEXT,status TEXT,response TEXT)')
        request_key = digest({'contract':'context-student/1','provider':'claude','body':{}})
        for key in ['cached', request_key]:
            ledger.db.execute('INSERT INTO calls VALUES (?,?,?)', (key, 'validated', '{}'))
        preview = {k:{} for k in ['reference','reference_lifecycle','received_inventory','owner']}
        preview.update(patient_id='p',available_by='2029-01-01',task_digest='task',
                       selected_record_ids=['r'],excluded_records=0,request={'previous_assessment_id':None})
        prep = {k:k for k in ['source_plan_id','result_id','decision_id','task_id','request_id','question','reference_question']}
        prep.update(preview=preview,changes=[])
        plan = {'plan': {'groups': [{'context_id': 'context'}]}}
        answer = {'id': 'answer', 'new_paid_api_calls': 1, 'findings':[], 'coverage':{},
                  'usage': [{'request_hash': key} for key in ['cached', 'source', 'reconciler', 'source']]}
        snapshot={'id':'snapshot','source_units':1,'model_unresolved_units':0}
        result={'status':'insufficient','summary':'No useful evidence.','findings':[],
                'missing':[{'question':'Access?','decision_effect':'Changes feasibility.'}]}
        from clinical_core.agere.clinical import linked_claim_reconciliation as linked, linked_claim_integrity
        with patch.object(A, 'prepared', return_value=(prep, plan)), \
             patch.object(A.S, 'synthesize', side_effect=ValueError(A.SIZE_ERRORS[0])), \
             patch.object(A.S, 'saved_source_stage', return_value={'status':'completed_source_passes_only','answer':{'id':'stage'}}), \
             patch.object(linked_claim_integrity, 'install'), \
             patch.object(linked, 'synthesize', return_value=answer), \
             patch.object(A.A, 'attach', return_value={'attachment':{'id':'attachment'}}), \
             patch.object(A.S, 'inspect', return_value=answer), \
             patch.object(A.A, 'prepare', return_value={'snapshot':snapshot}), \
             patch.object(A, 'wire', return_value={'findings':[]}), \
             patch.object(A, 'body', return_value={}), \
             patch.object(A.case_synthesis, 'token_check', return_value={'input_tokens':0}), \
             patch.object(A, 'send', return_value=(result,{'cache_hit':True})), \
             patch.object(A.Q, 'save_followup', side_effect=lambda path,row:row):
            import tempfile
            with tempfile.TemporaryDirectory() as path:
                actual=A.assess(SimpleNamespace(path=path), {'preparation_id':'prep'}, ledger, allow_live=True)
        self.assertEqual(actual['new_paid_api_calls'], 2)
        self.assertFalse(actual['assessment']['clinical_applicability_established'])


class DiscoveryContract(unittest.TestCase):
    def setUp(self):
        self.records = [{'record_id': k, 'patient_id': 'p', 'source_table': 'notes', 'available_date': date}
                        for k, date in [('old', '2028-01-01'), ('new', '2028-02-01'), ('future', '2029-02-01'), ('unknown', None)]]
        self.inv = {'patient_id': 'p', 'records': {r['record_id']: {'record_hash': digest(r), 'source_table': 'notes'} for r in self.records}}
        self.row = {'id': 'decision', 'patient_id': 'p', 'question': 'Original population question', 'received_inventory': {'records': {}}, 'source_versions': {'records': {}}}
        self.gap = {'question': 'Can access be supported?', 'binding': 'access', 'parameters': {'available_by': '2029-01-01'}}
        self.ref = {'title': 'Registered reference'}
        self.retriever = SimpleNamespace(path='unused', search=lambda *args, **kw: {'evidence': [{'source': {'record_id': 'old'}}]})
        self.request = {'request_id': 'request', 'additional_record_ids': [], 'previous_assessment_id': None}
        mocks = [patch.object(A, 'origin', return_value=(self.row, {}, self.gap, self.ref, {}, {'id': 'task', 'owner': 'owner'})),
                 patch.object(A.Q, 'received_inventory', return_value=self.inv),
                 patch.object(A.C, 'received', return_value=(self.records, {'snapshot': {'as_of': '2029-03-01'}})),
                 patch.object(A.R, 'validate_sources'),
                 patch.object(A.Q, 'compare_received', return_value={'changes': [{'record_id': k, 'current': {'present': True}} for k in ['new', 'future', 'unknown']]}),
                 patch.object(A.RG, 'build', side_effect=lambda records, patient, cutoff, roots: {'record_ids': roots})]
        for m in mocks: m.start(); self.addCleanup(m.stop)

    def test_new_source_is_included_even_before_search_index_rebuild(self):
        p = A.preview(self.retriever, self.request)
        self.assertEqual(p['selected_record_ids'], ['new', 'old'])
        self.assertEqual({x['record_id'] for x in p['date_exclusions']}, {'future', 'unknown'})
        self.assertEqual(p['original_question'], self.row['question'])
        self.assertEqual(p['available_by'], '2029-01-01')
        self.assertFalse(p['clinical_applicability_established'])

    def test_cross_patient_expansion_rejected(self):
        with self.assertRaises(ValueError): A.preview(self.retriever, {**self.request, 'additional_record_ids': ['not_received']})

    def test_scope_identity_changes_when_source_changes(self):
        before = A.preview(self.retriever, self.request)
        self.records[1]['text'] = 'A newly corrected statement'
        self.inv['records']['new']['record_hash'] = digest(self.records[1])
        after = A.preview(self.retriever, self.request)
        self.assertNotEqual(before['id'], after['id'])

    def test_link_cannot_bypass_cutoff(self):
        with patch.object(A.RG, 'build', return_value={'record_ids': ['future']}), self.assertRaises(ValueError):
            A.preview(self.retriever, self.request)

    def test_previous_answer_must_have_same_request(self):
        with patch.object(A.Q, 'saved_followup', return_value={'schema': A.VERSION, 'request_id': 'other'}), self.assertRaises(ValueError):
            A.preview(self.retriever, {**self.request, 'previous_assessment_id': 'old_answer'})


if __name__ == '__main__': unittest.main()
