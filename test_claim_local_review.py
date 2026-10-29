"""Local source checks retain dependencies and report actionable field faults."""
import copy,json,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from clinical_core.agere.clinical import backstop_agent as B,execution_state as E,system_one_gate as G
from clinical_core.agere.deliverables.receipts import uncompact_json


class LocalReview(unittest.TestCase):
    def fixture(self):
        rows=[{'patient_id':'A','rasi_reason':[{'record_id':'n1','date':'2030-01-01','quote':'MRA held for potassium.'}],
               'current_assignment':'Unknown from this evidence.'},
              {'patient_id':'B','rasi_reason':[],'current_assignment':'Unknown from this evidence.'}]
        calls=[{'name':'query_sql','input':{'sql':'SELECT * FROM received'},'result':{'result_view':'base','rows':copy.deepcopy(rows)}},
            {'name':'query_sql','input':{'sql':'SELECT 4 AS definition'},'result':{'result_view':'reference','rows':[{'definition':4}]}},
            {'name':'query_sql','input':{'sql':'SELECT base.* FROM base CROSS JOIN reference'},'result':{'result_view':'final','rows':copy.deepcopy(rows)}}]
        answer={'table_binding':{'result_view':'final'},'table':copy.deepcopy(rows),'headline':'Current responsibility remains unknown.','facts':{}}
        return answer,calls

    def packets(self,answer,calls,reviews=None):
        return {p['id']:p for p in B.support_packets('Review the exact recorded care relationships.',answer,calls,reviews or [],'2030-05-27')}

    def test_global_wording_and_unrelated_scalar_edits_preserve_patient_requests(self):
        answer,calls=self.fixture();before=self.packets(answer,calls)
        answer['headline']='Dated roles are recorded; current responsibility is unknown.'
        calls.append({'name':'query_sql','input':{'sql':'SELECT 999 AS unrelated'},'result':{'result_view':'unrelated','rows':[{'unrelated':999}]}})
        after=self.packets(answer,calls)
        self.assertEqual(before['patient_A']['text'],after['patient_A']['text']);self.assertEqual(before['patient_B']['text'],after['patient_B']['text'])
        self.assertNotEqual(before['summary']['text'],after['summary']['text'])
        local=uncompact_json(json.loads(after['patient_A']['text']))
        self.assertEqual(local['shared_evidence'],[{'definition':4}]);self.assertNotIn('native_bindings',local)

    def test_an_actual_shared_dependency_change_invalidates_local_check(self):
        answer,calls=self.fixture();before=self.packets(answer,calls)
        calls[1]['result']['rows'][0]['definition']=5
        self.assertNotEqual(before['patient_A']['text'],self.packets(answer,calls)['patient_A']['text'])

    def test_unresolved_lineage_keeps_all_shared_evidence(self):
        answer,calls=self.fixture();answer['table_binding']['result_view']='unavailable'
        calls.append({'name':'query_sql','input':{},'result':{'result_view':'extra','rows':[{'extra':9}]}})
        local=uncompact_json(json.loads(self.packets(answer,calls)['patient_A']['text']))
        self.assertIn({'extra':9},local['shared_evidence'])

    def test_one_patients_source_change_does_not_invalidate_another_review(self):
        answer,calls=self.fixture();review={'questions':{'rasi_reason':'What is stated?'},
            'manifest':[{'patient_id':p,'document_digest':p} for p in ('A','B')],
            'rows':[{'patient_id':p,'review_json':'{}'}for p in ('A','B')]}
        before=self.packets(answer,calls,[review]);review['rows'][0]['review_json']='{"rasi_reason":[]}'
        after=self.packets(answer,calls,[review]);self.assertNotEqual(before['patient_A']['text'],after['patient_A']['text'])
        self.assertEqual(before['patient_B']['text'],after['patient_B']['text'])

    def test_direct_document_result_binds_only_to_its_patient(self):
        answer,calls=self.fixture();calls.append({'name':'read_document','input':{'record_id':'b1'},
            'result':{'patient_id':'B','record_id':'b1','text':'B has a documented account.'}})
        packets=self.packets(answer,calls)
        self.assertNotIn('B has a documented account.',packets['patient_A']['text'])
        self.assertIn('B has a documented account.',packets['patient_B']['text'])

    def test_failed_packet_localizes_entity_error_and_preserves_supported_unknown(self):
        answer,calls=self.fixture();packet=self.packets(answer,calls)['patient_A'];seen=[]
        def decide(*args,**kw):return {'label':'contradicted','decided_by':'fixture','steps':[]}
        def fields(task,questions,evidence,**kw):
            seen.append(questions)
            return {'judgments':{key:{'label':'wrong_entity' if value['instructions']['claim']['field']=='rasi_reason' else 'supported','decided_by':'fixture'}
                for key,value in questions.items()},'steps':[]}
        out=B.check_support_packets([packet],decide,as_of='2030-05-27',ledger=None,field_decider=fields)
        self.assertEqual(len(out['claim_feedback']),1);failure=out['claim_feedback'][0]
        self.assertEqual(failure['field'],'rasi_reason');self.assertEqual(failure['source_record_ids'],['n1']);self.assertEqual(failure['result_view'],'final')
        self.assertEqual(failure['path'],['answer_rows',0,'rasi_reason']);self.assertTrue(seen)
        state=json.loads(E.messages('q',calls,feedback={'status':'repair','support':out})[0]['content'])
        self.assertEqual(state['current_review_feedback']['unresolved_dependencies'][0]['claim_feedback'],out['claim_feedback'])

    def test_clean_supported_unknown_does_not_trigger_extra_reading(self):
        answer,calls=self.fixture();judge=Mock(side_effect=AssertionError('No diagnostic calls for clean support'))
        out=B.check_support_packets(list(self.packets(answer,calls).values()),lambda *a,**kw:{'label':'supported','decided_by':'fixture','steps':[]},
            as_of='2030-05-27',ledger=None,field_decider=judge)
        self.assertEqual(out['label'],'supported');self.assertEqual(out['claim_feedback'],[]);judge.assert_not_called()

    def test_uncertain_packet_can_be_localized_without_lowering_thresholds(self):
        answer,calls=self.fixture();packet=self.packets(answer,calls)['patient_A']
        def fields(task,questions,evidence,**kw):
            return {'judgments':{key:{'label':'wrong_entity' if q['instructions']['claim']['field']=='rasi_reason' else 'supported','decided_by':'fixture'}for key,q in questions.items()},'steps':[]}
        out=B.check_support_packets([packet],lambda *a,**kw:{'label':'not_established','decided_by':None,'probabilities':{'not_established':.7},'steps':[]},
            as_of='2030-05-27',ledger=None,field_decider=fields)
        self.assertEqual(out['label'],'contradicted');self.assertEqual(out['claim_feedback'][0]['field'],'rasi_reason')

    def test_supported_local_fields_cannot_promote_an_unresolved_complete_check(self):
        answer,calls=self.fixture();packet=self.packets(answer,calls)['patient_A']
        def fields(task,questions,evidence,**kw):
            return {'judgments':{key:{'label':'supported','decided_by':'fixture'}for key in questions},'steps':[]}
        for broad in ({'label':'not_established','decided_by':None,'probabilities':{'not_established':.7},'steps':[]},
                      {'label':'contradicted','decided_by':'fixture','steps':[]}):
            out=B.check_support_packets([packet],lambda *a,**kw:broad,
                as_of='2030-05-27',ledger=None,field_decider=fields)
            self.assertEqual(out['label'],'unknown');self.assertIsNone(out['decided_by'])
            self.assertEqual(out['claim_feedback'],[])

    def test_verifier_outage_does_not_purchase_a_second_diagnostic(self):
        answer,calls=self.fixture();judge=Mock(side_effect=AssertionError('Outage is not a source fault'))
        out=B.check_support_packets([self.packets(answer,calls)['patient_A']],
            lambda *a,**kw:{'label':'unknown','decided_by':None,'steps':[{'provider':'fixture','status':'error'}]},
            as_of='2030-05-27',ledger=None,field_decider=judge)
        self.assertIsNone(out['decided_by']);judge.assert_not_called()

    def test_unresolved_provider_reading_reaches_the_actor_but_an_outage_does_not_request_retries(self):
        answer,calls=self.fixture()
        calls[-1]['result']['columns']=list(calls[-1]['result']['rows'][0])
        answer.update(status='answered',answer_type='table',table_from_tool='final')
        answer.pop('table_binding');answer.pop('table')
        tools=SimpleNamespace(read={},document_reviews=[],rows_by_record={})
        def judge(task,questions,evidence,**kw):
            return {'judgments':{key:{'label':'unknown','decided_by':None}for key in questions},'steps':[]}
        for responded in (True,False):
            model=SimpleNamespace(decide=lambda *a,**kw:{'label':'unknown','decided_by':None,
                'probabilities':{'unknown':.9}if responded else {},
                'steps':[{'provider':'fixture','status':'answered'if responded else 'error','error':None if responded else 'outage'}]},judge_fields=judge)
            with self.subTest(responded=responded),patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=model),patch.object(G,'check',return_value={'label':'answers_fully','calls':0,'cost_usd':0}):
                out=B.review_final(SimpleNamespace(),'Review the exact recorded care relationships.',copy.deepcopy(answer),calls,tools,ledger=None,as_of='2030-05-27')
                if responded:
                    self.assertEqual(out['status'],'repair');self.assertIn('Source support remains unresolved',out['feedback'])
                    self.assertIn('current_assignment',out['feedback']);self.assertIn('population/domain/grain/unit',out['feedback'])
                else:
                    self.assertEqual(out['status'],'unavailable');self.assertIsNone(out['feedback'])


if __name__=='__main__':unittest.main()
