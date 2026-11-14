"""Operational reviewer state cannot become clinical evidence or certify itself."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import backstop_agent as B, execution_state as E
from clinical_core.agere.deliverables.receipts import uncompact_json


def call(view, rows, *, markers=None, sql='SELECT 3 AS value'):
    result={'result_view':view,'rows':rows,'columns':list(rows[0]) if rows else [],'truncated':False}
    if markers is not None:result['incomplete_dependencies']=markers
    return {'name':'query_sql','input':{'sql':sql},'result':result}


OP=[{'authority':'unverified_review_feedback','reason':'Unverified operational state'}]


class OperationalSupport(unittest.TestCase):
    def setUp(self):
        self.normal=call('agere_result_1',[{'patient_id':'P1','value':3,'quote':'Earlier held; later restarted.'}])
        self.feedback=call('agere_result_2',[{'feedback_json':'Unverified prior review, not patient evidence.'}],markers=OP)
        self.answer={'headline':'One received observation','table':[{'patient_id':'P1','value':3}],
                     'table_from_tool':'agere_result_1'}

    def packets(self,calls=None,answer=None,**kw):
        return B.support_packets('Review received observations',answer or self.answer,
            calls or [self.normal,self.feedback],[],'2030-05-27',**kw)

    def test_producer_marked_rows_are_absent_but_exact_native_digest_survives(self):
        packets=self.packets();state=uncompact_json(json.loads(packets[0]['text']))
        self.assertNotIn('Unverified prior review',json.dumps(state['shared_evidence']))
        retained=next(v for v in state['native_bindings'] if v['result_view']=='agere_result_2')
        self.assertEqual(retained['result_digest'],digest(self.feedback['result']))
        self.assertEqual(retained['operational_inputs']['rows'],1)
        self.assertTrue(retained['operational_inputs']['excluded_from_clinical_evidence'])
        self.assertNotIn('agere_result_2',[v['result_view'] for v in state['native_calculation_contracts']])
        self.assertTrue(all(p['dependency_complete'] for p in packets))

    def test_navigation_inventory_is_bound_bookkeeping_not_repeated_clinical_evidence(self):
        from clinical_core.agere.clinical.source_navigation import VERSION
        question='Review received observations'
        account={'units':[{'record_id':'doc1','selection':'unexamined','stratum':'unknown_date',
            'interpretation':'unexamined'}], 'reference_issues':[{'record_id':'missing','reason':'unresolved'}],
            'linked_no_document':[], 'selection_sufficiency':'unassessed','absence_established':False}
        inventory=[{'patient_id':pid,'fields':{'relationship':copy.deepcopy(account)},
            'field_applicability':{'relationship':True},'received_source_digest':'source-'+pid,
            'document_inventory_digest':'documents-'+pid}for pid in ('P1','P2')]
        nav={'schema':VERSION,'question':question,'as_of':'2030-05-27','patient_ids':['P1','P2'],
            'inventory':inventory,'questions':{'relationship':'What relationship is documented?'},
            'prior_result_view':'review1','prior_call_digest':'old-review','additional_documents':[],
            'selection_sufficiency':'unassessed','absence_established':False,'clinical_qualified':False}
        nav['id']=digest(nav);original=copy.deepcopy(nav)
        calls=[self.normal,{'name':'plan_evidence_expansion','input':{'review_result_view':'review1'},'result':nav}]
        packets=self.packets(calls);state=uncompact_json(json.loads(packets[1]['text']))
        self.assertEqual(state['answer_rows'],self.answer['table'])
        self.assertEqual(nav,original)
        self.assertFalse(state['shared_evidence'])
        navigation=[x for x in state['evidence']if 'source_navigation_scope'in x]
        self.assertEqual(len(navigation),1);self.assertEqual(navigation[0]['patient_id'],'P1')
        scope=navigation[0]['source_navigation_scope'];self.assertEqual(scope['complete_receipt_digest'],digest(nav))
        self.assertEqual(scope['fields']['relationship']['time_strata_counts'],{'unknown_date':1})
        self.assertEqual(scope['fields']['relationship']['reference_issues'],account['reference_issues'])
        self.assertFalse(scope['absence_established']);self.assertFalse(scope['clinical_qualified'])
        self.assertIsNone(B.navigation_support_rows({**nav,'question':'changed'},question,'2030-05-27'))
        self.assertIsNone(B.navigation_support_rows(nav,question,'2029-01-01'))
        enriched=question+'\nGoverned review definitions and request context'
        live=B.support_packets(enriched,self.answer,calls,[],'2030-05-27',original_question=question)
        live_state=uncompact_json(json.loads(live[1]['text']))
        self.assertEqual(live_state['question'],enriched)
        self.assertEqual(live_state['evidence'],state['evidence'])
        self.assertFalse(live_state['shared_evidence'])
        wrong=B.support_packets(enriched,self.answer,calls,[],'2030-05-27',original_question='Different request')
        self.assertTrue(uncompact_json(json.loads(wrong[1]['text']))['shared_evidence'])

    def test_unmarked_other_and_mixed_authorities_are_retained(self):
        for markers in [None,[],[{'authority':'missing_source'}],OP+[{'authority':'missing_source'}]]:
            with self.subTest(markers=markers):
                c=call('agere_result_2',[{'feedback_json':'Actual received data, regardless of column name'}],markers=markers)
                state=uncompact_json(json.loads(self.packets([self.normal,c])[0]['text']))
                self.assertIn('Actual received data',json.dumps(state['shared_evidence']))
                self.assertNotIn('operational_inputs',state['native_bindings'][-1])

    def test_same_row_from_unmarked_source_is_still_available(self):
        legitimate=call('agere_result_3',copy.deepcopy(self.feedback['result']['rows']))
        state=uncompact_json(json.loads(self.packets([self.normal,self.feedback,legitimate])[0]['text']))
        self.assertEqual(state['shared_evidence'],legitimate['result']['rows'])

    def test_unknown_lineage_retains_operational_rows(self):
        answer={**self.answer,'table_from_tool':'not_an_executed_view'}
        state=uncompact_json(json.loads(self.packets(answer=answer)[0]['text']))
        self.assertIn('Unverified prior review',json.dumps(state['shared_evidence']))
        self.assertNotIn('operational_inputs',state['native_bindings'][-1])

    def test_final_and_computed_dependencies_fail_before_any_provider(self):
        joined=call('agere_result_3',[{'patient_id':'P1','value':3}],sql='SELECT * FROM agere_result_1 JOIN agere_result_2')
        cases=[{**self.answer,'table_from_tool':'agere_result_2'},
               {**self.answer,'table_from_tool':'agere_result_3'},
               {**self.answer,'computed_prose':[{'field':'headline','template':'{x}','rendered':'3',
                    'bindings':{'x':{'result_view':'agere_result_2'}},
                    'sources':{'x':{'binding':{'result_view':'agere_result_2'},'value':3}}}]}]
        for answer in cases:
            with self.subTest(answer=answer):
                packets=self.packets([self.normal,self.feedback,joined],answer)
                self.assertTrue(all(p['dependency_complete'] is False for p in packets))
                def forbidden(*args,**kw):raise AssertionError('Operational state cannot trigger verification')
                verdict=B.check_support_packets(packets,forbidden,as_of='2030-05-27',ledger=None)
                self.assertEqual(verdict['label'],'unknown')
                self.assertEqual(verdict['steps'],[])

    def test_mixed_operational_final_dependency_also_cannot_self_certify(self):
        c=copy.deepcopy(self.feedback);c['result']['incomplete_dependencies']+= [{'authority':'missing_source'}]
        packets=self.packets([self.normal,c],{**self.answer,'table_from_tool':'agere_result_2'})
        self.assertFalse(packets[0]['dependency_complete'])

    def test_patient_packet_bytes_and_all_source_values_are_unchanged(self):
        before=self.packets([self.normal]);after=self.packets()
        self.assertEqual(before[1:],after[1:])
        self.assertEqual(uncompact_json(json.loads(after[1]['text']))['answer_rows'],self.answer['table'])

    def test_bound_review_projection_keeps_prose_and_unrelated_json(self):
        raw='Inspect prior machine feedback: '+json.dumps([{'source_id':'synthetic-'+str(i),'label':'unknown'} for i in range(80)])
        assessment={'feedback':raw,'support':{'packets':[{'id':'patient','label':'unknown'}]}}
        ref={'result_view':'agere_review_feedback_test','feedback_digest':digest(assessment),'json_column':'feedback_json'}
        text='Clinical qualifier {"received": true}. '+raw+' The source remains unresolved.'
        projected=E.review_limitation_view(text,assessment,ref)
        self.assertEqual(projected['original_text_digest'],digest(text))
        self.assertTrue(projected['prose'].startswith('Clinical qualifier {"received": true}. Inspect prior machine feedback: '))
        self.assertTrue(projected['prose'].endswith(' The source remains unresolved.'))
        self.assertEqual(projected['native_review_reference'],ref)
        self.assertEqual(E.review_limitation_view(text,assessment,{**ref,'feedback_digest':'wrong'}),text)
        answer={**self.answer,'limitations':[text],'investigation_review':assessment}
        before=copy.deepcopy(answer)
        packet=self.packets(answer=answer,prior_review_reference=ref)[0]
        state=uncompact_json(json.loads(packet['text']))
        self.assertEqual(state['answer']['limitations'][0],projected)
        self.assertEqual(answer,before)

    def test_declared_long_route_gets_exact_complete_text_and_existing_jev_mode(self):
        text='A complete source context. '*11000
        packet={'id':'patient_large','date':'2030-05-27','text':text,'bytes':len(text.encode()),
                'dependency_complete':True,'source_context_complete':True}
        received=[]
        def forbidden(*args,**kw):raise AssertionError('Small-context route must not receive this packet')
        def long_route(question,options,evidence,**kw):
            received.append((evidence,kw))
            return {'label':'supported','decided_by':'jev','probabilities':{'supported':.9},
                    'steps':[{'provider':'jev','status':'answered','request_hash':'recorded-call'}]}
        result=B.check_support_packets([packet],forbidden,as_of='2030-05-27',ledger=None,
            field_decider=forbidden,long_context_decider=long_route)
        self.assertEqual(received[0][0][0]['text'],text)
        self.assertEqual(received[0][1]['order'],('jev','perplexity'))
        self.assertTrue(received[0][1]['long_context'])
        self.assertEqual(result['label'],'supported')
        route=result['packets'][0]['context_route']
        self.assertEqual(route['source_packet_digest'],hashlib.sha256(text.encode()).hexdigest())
        self.assertFalse(route['independent_reading_qualification'])
        self.assertEqual(result['steps'][0]['context_route'],route)

    def test_large_dependency_uses_uncertain_result_fallback_without_changing_text(self):
        from clinical_core.agere.clinical import system_one as S
        text='Complete source, including negation and unresolved history. '*5000
        packet={'id':'large','date':'2030-05-27','text':text,'bytes':len(text.encode()),
                'dependency_complete':True,'source_context_complete':True}
        seen=[]
        def choice(question,options,evidence,**kw):
            seen.append((kw['provider'],evidence,kw))
            p=.9 if kw['provider']=='perplexity'else .4
            return {'label':'supported','probabilities':{'supported':p,'unknown':1-p},
                    'calibrated_probabilities':{'supported':p,'unknown':1-p},
                    'cost_usd':0.,'cache_hit':kw['provider']=='jev','request_hash':kw['provider']}
        def forbidden(*args,**kw):raise AssertionError('No small-context or field route')
        with patch.object(S,'choice',side_effect=choice),patch('clinical_core.agere.clinical.system_one_student.predict',return_value=None),patch('clinical_core.agere.clinical.system_one_student.record'):
            result=B.check_support_packets([packet],forbidden,as_of='2030-05-27',ledger=None,
                field_decider=forbidden,long_context_decider=S.decide)
        self.assertEqual([p for p,_,_ in seen],['jev','perplexity'])
        self.assertTrue(all(e[0]['text']==text and kw['long_context']for _,e,kw in seen))
        self.assertEqual(result['packets'][0]['decided_by'],'perplexity')
        self.assertEqual(result['label'],'supported')
        self.assertTrue(result['steps'][0]['cache_hit'])
        self.assertEqual(result['packets'][0]['context_route']['requested_providers'],['jev','perplexity'])

    def test_absent_route_or_real_transport_oversize_makes_no_provider_call(self):
        def forbidden(*args,**kw):raise AssertionError('No provider dispatch allowed')
        for size,declared in [(250000,None),(32*1024*1024+1,forbidden)]:
            with self.subTest(size=size):
                packet={'id':'large','date':'2030-05-27','text':'Complete source retained natively',
                        'bytes':size,'dependency_complete':True,'source_context_complete':True}
                result=B.check_support_packets([packet],forbidden,as_of='2030-05-27',ledger=None,
                    long_context_decider=declared)
                self.assertEqual(result['label'],'unknown')
                self.assertEqual(result['steps'],[])

    def test_unsettled_large_route_cannot_trigger_field_fanout_or_certify_answer(self):
        def forbidden(*args,**kw):raise AssertionError('No small-route/field fanout')
        packet={'id':'large','date':'2030-05-27','text':'Complete retained context',
                'bytes':250000,'dependency_complete':True,'source_context_complete':True,
                'claim_manifest':[{'id':'c','field':'reason','path':['answer','reason']}]}
        for label,decided in [('unknown',None),('supported',None),('contradicted','jev')]:
            with self.subTest(label=label,decided=decided):
                def long_route(*args,**kw):return {'label':label,'decided_by':decided,'steps':[]}
                result=B.check_support_packets([packet],forbidden,as_of='2030-05-27',ledger=None,
                    field_decider=forbidden,long_context_decider=long_route)
                self.assertEqual(result['label'],'unknown')
                self.assertIsNone(result['decided_by'])
                self.assertEqual(result['packets'][0]['routed_judgment']['label'],label)

    def test_declared_large_field_adapter_localizes_once_with_complete_context(self):
        from clinical_core.agere.deliverables.receipts import readable_json
        row={'patient_id':'A',**{'field_'+str(i):'A source-bound value'for i in range(30)}}
        claims=[{'id':'c'+str(i),'field':name,'path':['answer_rows',0,name]}
                for i,name in enumerate(row)if name!='patient_id']
        state={'patient_id':'A','answer_rows':[row],'claim_manifest':claims,
            'source_contexts':[{'patient_id':'A','record_id':'source','text':' '.join('source'+str(i)for i in range(30000))}]}
        text=json.dumps(readable_json(state),separators=(',',':'))
        packet={'id':'patient_A','date':'2030-05-27','text':text,'bytes':len(text.encode()),
            'dependency_complete':True,'source_context_complete':True,'claim_manifest':claims}
        self.assertGreater(packet['bytes'],240000)
        def forbidden(*a,**kw):raise AssertionError('Unqualified short-context adapter')
        def whole(*a,**kw):return {'label':'supported','decided_by':None,
            'probabilities':{'supported':.79,'unknown':.21},'steps':[]}
        for field_outcome in ['supported','unknown','wrong_role']:
            seen=[]
            def fields(task,named,evidence,**kw):
                seen.append((named,evidence,kw))
                return {'steps':[],'judgments':{k:{'label':field_outcome if k=='c30'else'supported',
                    'decided_by':None if k=='c30'and field_outcome=='unknown'else'fixture',
                    'threshold':.8,'probability':.9}for k in named}}
            result=B.check_support_packets([packet],forbidden,as_of=packet['date'],ledger=None,
                field_decider=forbidden,long_context_decider=whole,long_context_field_decider=fields)
            self.assertEqual(len(seen),1)
            self.assertEqual(list(seen[0][0]),[c['id']for c in claims])
            self.assertEqual(seen[0][1][0]['text'],text)
            self.assertTrue(seen[0][2]['long_context'])
            self.assertEqual(seen[0][2]['order'],('jev','perplexity'))
            self.assertEqual(result['label'],'contradicted'if field_outcome=='wrong_role'else'unknown')
            review=result['packets'][0]['field_review']
            self.assertEqual(review['expected_count'],30)
            self.assertFalse(review['complete_dependency_accepted'])
            self.assertEqual(review['supported_count'],30 if field_outcome=='supported' else 29)

    def test_large_field_adapter_does_not_retry_an_outage_or_missing_source(self):
        def forbidden(*a,**kw):raise AssertionError('No diagnostic for absent source/provider response')
        for source_complete in [True,False]:
            packet={'id':'patient_A','date':'2030-05-27','text':'Retained source','bytes':250000,
                'source_context_complete':source_complete,'dependency_complete':True,
                'claim_manifest':[{'id':'c','field':'reason','path':['answer','reason']}]}
            result=B.check_support_packets([packet],forbidden,as_of=packet['date'],ledger=None,
                field_decider=forbidden,long_context_field_decider=forbidden,
                long_context_decider=lambda *a,**kw:{'label':'unknown','decided_by':None,
                    'steps':[{'provider':'fixture','status':'error'}]})
            self.assertEqual(result['label'],'unknown')


if __name__=='__main__':unittest.main()
