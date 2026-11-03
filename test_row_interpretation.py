"""Row interpretations retain native evidence and their own review dependencies."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, evidence_projection as E, execution_state as X
from clinical_core.agere.clinical import system_one_gate as G
from clinical_core.contracts import digest
from clinical_core.agere.deliverables.receipts import uncompact_json


class RowInterpretation(unittest.TestCase):
    def setUp(self):
        self.calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id, events FROM scoped_events'},
            'result':{'result_view':'agere_result_1','columns':['patient_id','events'],
                      'rows':[['A',2],['B',None]],'truncated':False}},
            {'name':'query_sql','input':{'sql':'SELECT patient_id, observation, date FROM received_observations'},
             'result':{'result_view':'agere_result_2','columns':['patient_id','observation','date'],
                       'rows':[['A','A dated request was received.','2029-05-01'],['B','The received observation is unresolved.',None]]}}]

    def answer(self):
        out={'status':'partial','answer_type':'table','headline':'Review the received observations.',
             'table_from_tool':'agere_result_1','missing':['Current responsibility is unestablished.'],
             'row_prose':{'keys':['patient_id'],'rows':[]}}
        for i,pid in enumerate(['A','B']):
            out['row_prose']['rows'].append({'key':{'patient_id':pid},
                'basis':[{'result_view':'agere_result_1','row':i},{'result_view':'agere_result_2','row':i}],
                'fields':{'explanation':{'template':'{observation} Received events: {events}. Source date: {date}.',
                    'bindings':{'observation':{'result_view':'agere_result_2','row':i,'column':'observation'},
                                'date':{'result_view':'agere_result_2','row':i,'column':'date'},
                                'events':{'result_view':'agere_result_1','row':i,'column':'events'}}},
                          'proposed_review_steps':'Confirm current responsibility before acting.'}})
        B.materialize_table(out,self.calls)
        return out

    def compiled(self):
        out=self.answer();B.bind_computed_prose(out,self.calls);return out

    def packets(self,answer=None,**kwargs):
        return B.support_packets('Explain each selected entity and its next review.',answer or self.compiled(),self.calls,[],
                                 '2030-05-27',**kwargs)

    def test_exact_native_values_complete_order_and_unknowns_survive(self):
        answer=self.answer();answer['row_prose']['rows'].reverse();before=copy.deepcopy(self.calls)
        B.bind_computed_prose(answer,self.calls)
        self.assertEqual([r['patient_id'] for r in answer['table']],['A','B'])
        self.assertEqual([r['events'] for r in answer['table']],[2,None])
        self.assertIn('Received events: 2',answer['table'][0]['explanation'])
        self.assertIn('Received events: unknown. Source date: unknown',answer['table'][1]['explanation'])
        self.assertEqual(self.calls,before);self.assertEqual(answer['status'],'partial')
        self.assertFalse(answer['table_binding']['copied_from_executed_result'])
        self.assertEqual(answer['table_binding']['native_columns'],['patient_id','events'])
        self.assertEqual(len(answer['computed_prose']),4)
        self.assertTrue(all(p['authority']=='model_proposed_interpretation_requires_source_review' for p in answer['computed_prose']))

    def test_plain_prose_and_native_metadata_are_recompiled_idempotently(self):
        answer=self.compiled();expected=copy.deepcopy(answer)
        B.bind_computed_prose(answer,self.calls);self.assertEqual(answer,expected)
        answer['table'][0]['events']=999
        answer['computed_prose'][0]['sources']['events']['value']=999
        answer['computed_prose'][0]['basis'][0]['source_row_digest']='forged'
        self.calls[0]['result']['rows'][0][1]=3
        B.bind_computed_prose(answer,self.calls)
        self.assertEqual(answer['table'][0]['events'],3)
        self.assertIn('events: 3',answer['table'][0]['explanation'])
        self.assertEqual(answer['computed_prose'][0]['sources']['events']['value'],3)
        self.assertNotEqual(answer['computed_prose'][0]['basis'][0]['source_row_digest'],'forged')

    def test_missing_extra_duplicate_and_wrong_type_entities_fail_atomically(self):
        for mutation in ['missing','extra','duplicate','wrong_type','duplicate_base']:
            answer=self.answer();calls=copy.deepcopy(self.calls)
            if mutation=='missing':answer['row_prose']['rows'].pop()
            if mutation=='extra':answer['row_prose']['rows'][1]['key']['patient_id']='C'
            if mutation=='duplicate':answer['row_prose']['rows'][1]=copy.deepcopy(answer['row_prose']['rows'][0])
            if mutation=='wrong_type':answer['row_prose']['rows'][0]['key']['patient_id']=1
            if mutation=='duplicate_base':calls[0]['result']['rows'][1][0]='A'
            before=copy.deepcopy(answer)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):B.bind_computed_prose(answer,calls)
            self.assertEqual(answer,before)

    def test_native_columns_cannot_be_replaced_and_text_fields_are_complete(self):
        for field,value in [('events','A replacement'),('patient_id','C'),('new',{'unsupported':'shape'})]:
            answer=self.answer();answer['row_prose']['rows'][0]['fields'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):B.bind_computed_prose(answer,self.calls)
        answer=self.answer();answer['row_prose']['rows'][0]['fields'].pop('proposed_review_steps')
        with self.assertRaisesRegex(ValueError,'same fields'):B.bind_computed_prose(answer,self.calls)

    def test_basis_must_be_real_complete_same_entity_and_nonduplicated(self):
        for change in [{'row':True},{'row':1},{'row':9},{'result_view':'invented'}]:
            answer=self.answer();answer['row_prose']['rows'][0]['basis'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):B.bind_computed_prose(answer,self.calls)
        answer=self.answer();answer['row_prose']['rows'][0]['basis']*=2
        with self.assertRaisesRegex(ValueError,'duplicate addresses'):B.bind_computed_prose(answer,self.calls)
        calls=copy.deepcopy(self.calls);calls[1]['result']['truncated']=True
        with self.assertRaisesRegex(ValueError,'Complete result'):B.bind_computed_prose(self.answer(),calls)
        answer=self.answer();answer['row_prose']['rows'][0]['basis']=[]
        with self.assertRaises(ValueError):B.bind_computed_prose(answer,self.calls)

    def test_scalar_cannot_borrow_another_entity_or_an_undeclared_basis(self):
        answer=self.answer();answer['row_prose']['rows'][0]['fields']['explanation']['bindings']['events']['row']=1
        with self.assertRaisesRegex(ValueError,'declared evidence bases'):B.bind_computed_prose(answer,self.calls)
        answer=self.answer();answer['row_prose']['rows'][0]['basis'].pop()
        with self.assertRaisesRegex(ValueError,'declared evidence bases'):B.bind_computed_prose(answer,self.calls)

    def test_unbound_quantities_are_rejected_before_paid_review(self):
        answer=self.answer();answer['row_prose']['rows'][0]['fields']['explanation']='There were 99 events.'
        with patch.object(G,'ready',side_effect=AssertionError('No provider check for a native binding error')):
            review=B.review_final(SimpleNamespace(),'Explain each entity',answer,self.calls,SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
        self.assertEqual(review['status'],'repair');self.assertEqual(review['calls'],0)
        self.assertIn('99',review['feedback'])

    def test_all_row_field_errors_are_reported_together_without_partial_commit(self):
        answer=self.answer()
        answer['row_prose']['rows'][0]['fields']['explanation']='There are two observations.'
        answer['row_prose']['rows'][0]['fields']['proposed_review_steps']='Review three issues.'
        answer['row_prose']['rows'][1]['fields']['explanation']='There are four observations.'
        before=copy.deepcopy(answer);native=copy.deepcopy(self.calls)
        with self.assertRaises(B.ComputedProseError) as caught:
            B.bind_computed_prose(answer,self.calls)
        error=caught.exception
        for field in ['table.0.explanation','table.0.proposed_review_steps','table.1.explanation']:
            self.assertIn(field,str(error))
        self.assertEqual(len(error.progress_failures),3)
        self.assertEqual(answer,before);self.assertEqual(self.calls,native)
        for field in ['explanation','proposed_review_steps']:
            answer['row_prose']['rows'][0]['fields'][field]='Confirm the received observation.'
        with self.assertRaises(B.ComputedProseError) as remaining:
            B.bind_computed_prose(answer,self.calls)
        self.assertEqual(len(remaining.exception.progress_failures),1)
        answer['row_prose']['rows'][1]['fields']['explanation']='The received observation is unresolved.'
        B.bind_computed_prose(answer,self.calls)
        self.assertEqual(len(answer['table']),2)
        self.assertEqual(answer['table'][0]['events'],2)
        self.assertIsNone(answer['table'][1]['events'])

    def test_wrong_entity_scalar_and_later_field_error_are_both_reported(self):
        answer=self.answer();before=copy.deepcopy(answer)
        answer['row_prose']['rows'][0]['fields']['explanation']['bindings']['events']['row']=1
        answer['row_prose']['rows'][1]['fields']['proposed_review_steps']='Review 99 events.'
        mutated=copy.deepcopy(answer)
        with self.assertRaises(B.ComputedProseError) as caught:
            B.bind_computed_prose(answer,self.calls)
        self.assertIn('declared evidence bases at table.0.explanation',str(caught.exception))
        self.assertIn('table.1.proposed_review_steps',str(caught.exception))
        self.assertEqual(len(caught.exception.progress_failures),2)
        self.assertEqual(answer,mutated)

    def test_row_quantities_cannot_borrow_a_global_quote_exception(self):
        answer=self.answer();answer['row_prose']['rows'][0]['fields']['explanation']="R1: 'Received 99 events.'"
        with self.assertRaises(ValueError):B.bind_computed_prose(answer,self.calls,verified_quotes=[{'record_id':'R1','quote':'Received 99 events.'}])

    def test_proofs_and_basis_are_localized_and_new_text_never_gets_native_fidelity(self):
        packets=self.packets();decoded={p['id']:uncompact_json(json.loads(p['text'])) for p in packets}
        self.assertNotIn('row_prose',decoded['summary']['answer'])
        self.assertFalse(decoded['summary']['computed_claims'])
        for pid in ['A','B']:
            local=decoded['patient_'+pid]
            self.assertEqual(len(local['computed_claims']),2)
            self.assertTrue(all(p['entity_key']=={'patient_id':pid} for p in local['computed_claims']))
            self.assertTrue(any(row.get('observation') for row in local['evidence']))
        for packet in packets:
            self.assertNotIn('native_numeric_fidelity',packet);self.assertNotIn('native_profile_fidelity',packet)
        edited=self.compiled();edited['row_prose']['rows'][0]['fields']['proposed_review_steps']='Clarify the dated request before acting.'
        B.bind_computed_prose(edited,self.calls)
        changed={p['id']:p for p in self.packets(edited)}
        original={p['id']:p for p in packets}
        self.assertEqual(changed['patient_B']['text'],original['patient_B']['text'])
        self.assertNotEqual(changed['patient_A']['text'],original['patient_A']['text'])

    def test_basis_only_documentary_claim_requires_its_complete_source(self):
        text='A dated source observation.'
        self.calls[1]['result']['columns'].append('finding')
        self.calls[1]['result']['rows'][0].append({'assertion':{'binding_version':'documentary-relation/1',
            'source':{'patient_id':'A','record_id':'R-A','event_date':'2029-05-01','document_text_digest':digest(text)}}})
        self.calls[1]['result']['rows'][1].append(None)
        packet=next(p for p in self.packets() if p['id']=='patient_A')
        self.assertFalse(packet['source_context_complete'])
        doc={'patient_id':'A','record_id':'R-A','date':'2029-05-01','text':text}
        packet=next(p for p in self.packets(source_documents={'R-A':doc}) if p['id']=='patient_A')
        self.assertTrue(packet['source_context_complete'])
        bad={**doc,'date':'2029-05-02'}
        packet=next(p for p in self.packets(source_documents={'R-A':bad}) if p['id']=='patient_A')
        self.assertFalse(packet['source_context_complete'])

    def test_coverage_and_editable_draft_preserve_actual_explanations(self):
        answer=self.compiled();coverage=E.coverage_answer(answer)
        self.assertNotIn('row_prose',coverage);self.assertFalse(coverage['computed_prose'])
        for actual,projected in zip(answer['table'],coverage['table']):
            self.assertEqual(actual['explanation'],projected['explanation'])
        self.assertFalse(any(edge.get('field')=='explanation' for edge in coverage['coverage_contract']['dependency_edges']))
        draft=X.draft_state(answer)['last_draft']
        self.assertEqual(draft['row_prose'],answer['row_prose']);self.assertNotIn('table',draft)

    def test_unfamiliar_nonpatient_composite_grain_uses_the_same_compiler(self):
        calls=[{'name':'query_sql','input':{'sql':'SELECT contract, period, balance FROM balances'},
                'result':{'result_view':'agere_result_1','columns':['contract','period','balance'],'rows':[
                    {'contract':'K-A','period':'2029','balance':120.5},
                    {'contract':'K-A','period':'2030','balance':None}]}}]
        answer={'table_from_tool':'agere_result_1','row_prose':{'keys':['contract','period'],'rows':[
            {'key':{'contract':'K-A','period':period},'basis':[{'result_view':'agere_result_1','row':i}],
             'fields':{'explanation':{'template':'Received balance: ${balance}; this is not realized savings.',
                 'bindings':{'balance':{'result_view':'agere_result_1','row':i,'column':'balance'}}}}}
            for i,period in enumerate(['2029','2030'])]}}
        B.materialize_table(answer,calls);B.bind_computed_prose(answer,calls)
        self.assertIn('$120.5',answer['table'][0]['explanation']);self.assertIn('$unknown',answer['table'][1]['explanation'])
        packets=B.support_packets('Explain each contract period.',answer,calls,[],'2030-05-27')
        self.assertEqual(len(packets),1)
        self.assertEqual(len(uncompact_json(json.loads(packets[0]['text']))['computed_claims']),2)

    def test_empty_native_population_stays_empty(self):
        calls=copy.deepcopy(self.calls);calls[0]['result']['rows']=[]
        answer={'table_from_tool':'agere_result_1','row_prose':{'keys':['patient_id'],'rows':[]}}
        B.materialize_table(answer,calls);B.bind_computed_prose(answer,calls)
        self.assertEqual(answer['table'],[])


if __name__=='__main__':unittest.main()
