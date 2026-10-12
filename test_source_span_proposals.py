import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import source_span_proposals as p
from test_question_learning import QuestionLearningTests


class SourceSpanTests(unittest.TestCase):
    setUp=QuestionLearningTests.setUp
    tearDown=QuestionLearningTests.tearDown
    make_candidate=QuestionLearningTests.make_candidate

    def request(self):return {'candidate_id':'a'*64,'passage_rank':1}

    def response(self, **changes):
        result={'question_relation':'potentially_relevant','scope_reason':'A reported visit may bear on the question.',
            'proposals':[{'quote':'visit happened','occurrence':1,'concept':'reported visit completion','reason':'Check the scope of the completion account.'}],
            'unresolved':['Timing relative to the question is unverified.']}
        result.update(changes)
        return {'model':p.gateway.PersistentGateway.MODELS['claude'],'stop_reason':'end_turn',
                'content':[{'type':'text','text':json.dumps(result)}]}

    def run_proposal(self, raw=None):
        def send(body,provider,ledger,out,parse):return parse(raw or self.response()),{'cache_hit':False,'request_hash':'fixture'}
        with Budget(self.fixture.root/'ledger.sqlite3') as ledger, patch.object(p.gateway,'send',side_effect=send),patch.object(p.gateway,'token_check',return_value={'provider_count_cached':False,'input_tokens':100}):
            return p.propose(self.stack,self.request(),ledger,allow_live=True)

    def test_preview_excludes_hidden_truth_and_keeps_full_field(self):
        prepared=p.prepare(self.stack,self.request());wire=prepared['wire']
        self.assertNotIn('HIDDEN CONTENT',json.dumps(prepared['body']))
        self.assertEqual(wire['complete_received_field'],self.fixture.sources['b']['text'])
        self.assertFalse(p.preview(self.stack,self.request())['saved'])
        self.assertEqual(p.learning.listing(self.stack)['total'],0)

    def test_machine_proposal_selects_exact_task_without_labels_and_replays(self):
        saved=self.run_proposal();self.assertFalse(saved['abstained'])
        self.assertEqual(p.learning.listing(self.stack)['total'],0)
        with Budget(self.fixture.root/'ledger.sqlite3') as ledger,patch.object(p.gateway,'send') as send:
            replay=p.propose(self.stack,self.request(),ledger)
            self.assertTrue(replay['cache_hit']);self.assertEqual(replay['new_paid_api_calls'],0);send.assert_not_called()
        request={'receipt_id':saved['id'],'proposal_id':saved['proposals'][0]['id']}
        selected=p.select(self.stack,request);self.assertEqual(selected,p.select(self.stack,request))
        self.assertEqual(selected['task']['target']['start'],self.fixture.sources['b']['text'].rindex('visit'))
        self.assertFalse(selected['task']['qualification']['human_reviewed'])
        self.assertEqual(p.origins(self.stack,selected['task_id'])[0]['status'],'current')
        self.assertEqual(p.learning.learning.audit(self.stack)['recorded_review_revisions'],0)

    def test_abstention_is_saved_without_task(self):
        saved=self.run_proposal(self.response(question_relation='not_relevant',proposals=[]))
        self.assertTrue(saved['abstained']);self.assertEqual(saved['proposals'],[])
        with self.assertRaises(ValueError):p.select(self.stack,{'receipt_id':saved['id'],'proposal_id':'a'*64})
        self.assertEqual(p.learning.listing(self.stack)['total'],0)

    def test_invalid_quotes_labels_occurrences_and_irrelevant_proposals_fail(self):
        prepared=p.prepare(self.stack,self.request())
        for changes in ({'quote':'fabricated'}, {'occurrence':True},{'occurrence':-1},{'concept':''},{'labels':{}}, {'reason':''}):
            r=json.loads(self.response()['content'][0]['text']);r['proposals'][0].update(changes)
            raw=self.response(proposals=r['proposals'])
            with self.assertRaises(ValueError):p.parse(self.stack,prepared,raw)
        with self.assertRaises(ValueError):p.parse(self.stack,prepared,self.response(question_relation='not_relevant'))
        self.assertEqual(p.learning.listing(self.stack)['total'],0)

    def test_protected_family_rejected_before_token_or_provider_call(self):
        for pid in ('c','d'):
            self.candidate=self.make_candidate(pid)
            with Budget(self.fixture.root/'ledger.sqlite3') as ledger,patch.object(p.gateway,'token_check') as token,patch.object(p.gateway,'send') as send:
                with self.assertRaisesRegex(ValueError,'excluded'):p.propose(self.stack,self.request(),ledger,allow_live=True)
                token.assert_not_called();send.assert_not_called()

    def test_failed_attempt_is_not_retried(self):
        with Budget(self.fixture.root/'ledger.sqlite3') as ledger,patch.object(p.gateway,'token_check',side_effect=RuntimeError('Unavailable')) as token:
            with self.assertRaises(RuntimeError):p.propose(self.stack,self.request(),ledger,allow_live=True)
            with self.assertRaisesRegex(ValueError,'no automatic'):p.propose(self.stack,self.request(),ledger,allow_live=True)
            self.assertEqual(token.call_count,1)

    def test_question_change_stales_receipt_and_prevents_selection(self):
        saved=self.run_proposal();self.candidate['original_question']='Different unresolved question'
        with self.assertRaisesRegex(ValueError,'changed'):p.inspect(self.stack,saved['id'])
        with self.assertRaises(ValueError):p.select(self.stack,{'receipt_id':saved['id'],'proposal_id':saved['proposals'][0]['id']})

    def test_source_change_during_provider_call_retains_failure_without_receipt(self):
        def send(body,provider,ledger,out,parse):
            result=parse(self.response());self.candidate['original_question']='Changed during inference'
            return result,{'cache_hit':False}
        with Budget(self.fixture.root/'ledger.sqlite3') as ledger,patch.object(p.gateway,'send',side_effect=send),patch.object(p.gateway,'token_check',return_value={'provider_count_cached':False}):
            with self.assertRaisesRegex(ValueError,'changed during'):p.propose(self.stack,self.request(),ledger,allow_live=True)
        self.assertEqual(list((self.stack/'source-span-proposals/receipts').glob('*.json')),[])

    def test_full_field_overflow_does_not_truncate(self):
        r=self.fixture.sources['b'];r['text']='visit '+'Long source text '*2500
        db=sqlite3.connect(self.stack/'stack.sqlite3')
        with db:db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),r['record_id']))
        db.close();self.candidate=self.make_candidate('b')
        with self.assertRaisesRegex(ValueError,'no truncation'):p.prepare(self.stack,self.request())

    def test_structured_generated_display_and_extra_request_fields_rejected(self):
        with self.assertRaises(ValueError):p.prepare(self.stack,{**self.request(),'labels':{}})
        self.candidate['candidates'][0]['passages'][0]['source']['field']='structured_fields'
        with self.assertRaisesRegex(ValueError,'received text'):p.prepare(self.stack,self.request())

    def test_receipt_tampering_rejected(self):
        r=self.run_proposal();file=p.location(self.stack,r['id']);r['question_relation']='not_relevant';file.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'integrity'):p.inspect(self.stack,r['id'])

    def test_structured_siblings_kept_separate_from_other_text_fields(self):
        r=self.fixture.sources['b'];r.update(summary='Another source field',service_performed_confirmed=False,
            _warehouse_fields=['text','summary','service_performed_confirmed'],_warehouse_source={'adapter':'original-cohort-warehouse-adapter/1'})
        db=sqlite3.connect(self.stack/'stack.sqlite3')
        with db:db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),r['record_id']))
        db.close();self.candidate=self.make_candidate('b')
        wire=p.prepare(self.stack,self.request())['wire']
        self.assertEqual(wire['reported_siblings'],{'service_performed_confirmed':False})
        self.assertEqual(wire['other_text_fields_not_submitted'],['summary'])


class Budget:
    def __init__(self,path):self.ledger=BudgetLedger(path)
    def __enter__(self):return self.ledger
    def __exit__(self,*args):self.ledger.close()


if __name__=='__main__':unittest.main()
