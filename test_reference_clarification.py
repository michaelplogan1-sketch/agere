"""Reference clarification never supplies a number or source acceptance."""
import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import backstop_agent as B, execution_state as E, system_one_gate as G
from clinical_core.agere.gateway import ProviderUnavailable


class ReferenceClarification(unittest.TestCase):
    def setUp(self):
        self.calls=[{'name':'query_sql','input':{'sql':'SELECT COUNT(*) AS n FROM invoice'},'result':{
            'native_sql_contract':B.NATIVE_SQL_CONTRACT,'result_view':'native_count',
            'executed_query':'SELECT COUNT(*) AS n FROM invoice','columns':['n'],'rows':[{'n':5}],
            'truncated':False}}]
        self.answer={'status':'partial','answer_type':'table','table_from_tool':'native_count',
            'headline':{'template':'{n} invoices were found. No rejection reason is established, and a missing receipt is not treated as one.',
                'bindings':{'n':{'result_view':'native_count','row':0,'column':'n'}}}}
        B.materialize_table(self.answer,self.calls)

    def request(self,answer=None):
        answer=answer if answer is not None else self.answer
        with self.assertRaises(B.ComputedProseError)as raised:B.bind_computed_prose(answer,self.calls)
        return E.reference_clarification_request(answer,raised.exception.answer_repair_targets,'Compare received invoices.')

    def judgment(self,request,phrase='rejection reason',role='reference'):
        out={}
        for key,ref in request['references'].items():
            out[key]={'role':role,'antecedent':phrase if role=='reference'else None,
                'article':'a'if role=='reference'else None,'reason':'The category is referenced; no cardinality is asserted.'}
        return out

    def test_only_the_reference_changes_all_bindings_and_native_values_survive(self):
        before=copy.deepcopy(self.answer);request=self.request()
        after,receipt=E.apply_reference_clarification(self.answer,request,self.judgment(request))
        self.assertEqual(self.answer,before)
        self.assertEqual(after['headline']['bindings'],before['headline']['bindings'])
        self.assertEqual(after['table'],before['table'])
        self.assertEqual(after['headline']['template'],before['headline']['template'].replace('as one.','as a rejection reason.'))
        self.assertFalse(receipt['quantity_binding_waived'])
        B.bind_computed_prose(after,self.calls)
        self.assertTrue(after['headline'].startswith('5 invoices'))
        self.assertEqual(after['computed_prose'][0]['sources']['n']['value'],5)

    def test_clinical_and_nonclinical_references_use_the_same_contract(self):
        for phrase,subject in [('non-use reason','missing fill'),('denial','missing claim'),
                               ('fault classification','missing sensor value')]:
            answer=copy.deepcopy(self.answer)
            answer['headline']['template']='No '+phrase+' is established, and a '+subject+' is not interpreted as one.'
            # Retain an actual scalar placeholder; the reference adds no value.
            answer['headline']['template']='{n} inputs. '+answer['headline']['template']
            request=self.request(answer)
            after,_=E.apply_reference_clarification(answer,request,self.judgment(request,phrase))
            B.bind_computed_prose(after,self.calls)
            self.assertIn('as a '+phrase+'.',after['headline'])

    def test_counts_doses_and_grouping_are_not_exempted(self):
        for text in ['one patient','one mg daily','one percent','one month','one invoice per account',
                     'Records are counted as one.','Records are merged as one.','one of the invoices',
                     'The rate was one.','There were one.']:
            answer={'table_binding':{'result_view':'native_count'},'headline':text}
            self.assertIsNone(self.request(answer),text)
        # Even the ambiguous syntax remains an error when its role is a quantity.
        for role in ['quantity','unknown']:
            request=self.request();after,receipt=E.apply_reference_clarification(self.answer,request,self.judgment(request,role=role))
            self.assertIsNone(after);self.assertEqual(receipt['edits'],[])
            with self.assertRaises(B.ComputedProseError):B.bind_computed_prose(self.answer,self.calls)

    def test_other_unbound_quantities_still_fail_after_a_valid_expansion(self):
        answer=copy.deepcopy(self.answer);answer['facts']={'rate':'The rate was 12 percent.'}
        request=self.request(answer);after,_=E.apply_reference_clarification(answer,request,self.judgment(request))
        with self.assertRaisesRegex(B.ComputedProseError,'12'):B.bind_computed_prose(after,self.calls)

    def test_model_sees_exact_occurrence_and_complete_text_without_offset_arithmetic(self):
        request=self.request();view=E.reference_clarification_model_view(request)
        for key,ref in request['references'].items():
            marked=view['references'][key]['marked_text']
            self.assertTrue(marked.endswith('as <REFERENCE_CANDIDATE>one</REFERENCE_CANDIDATE>.'))
            self.assertEqual(marked.replace('<REFERENCE_CANDIDATE>','').replace('</REFERENCE_CANDIDATE>',''),ref['text'])
            self.assertNotIn('literal_start',view['references'][key]);self.assertNotIn('literal_end',view['references'][key])
        key=next(iter(request['references']))
        request['references'][key]['literal_start']=0
        with self.assertRaises(ValueError):E.reference_clarification_model_view(request)

    def test_reference_must_be_complete_exact_and_before_the_literal(self):
        request=self.request();good=self.judgment(request);key=next(iter(good))
        for mutation in ['missing','extra','future','bool','partial_word','changed_draft','wrong_value','invented_text']:
            req=copy.deepcopy(request);judgments=copy.deepcopy(good);answer=copy.deepcopy(self.answer)
            if mutation=='missing':judgments={}
            elif mutation=='extra':judgments['extra']=copy.deepcopy(good[key])
            elif mutation=='future':judgments[key]['antecedent']='not in the original text'
            elif mutation=='bool':judgments[key]['antecedent']=True
            elif mutation=='partial_word':judgments[key]['antecedent']='ejection reason'
            elif mutation=='changed_draft':answer['status']='answered'
            elif mutation=='wrong_value':req['references'][key]['value_digest']='forged'
            else:judgments[key]['replacement']='invented reason'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                E.apply_reference_clarification(answer,req,judgments)

    def test_numeric_or_negated_antecedent_cannot_be_used_as_a_reference(self):
        for phrase in ['one invoice','12 invoices','no rejection','not taking','a pair','each event','{n} invoices']:
            answer=copy.deepcopy(self.answer)
            answer['headline']['template']=phrase+'. '+answer['headline']['template']
            # Locate using the actual compiler target, not an owner-authored receipt.
            request=self.request(answer)
            if request is None:continue  # Additional unbound numerals already prevent this path.
            with self.subTest(phrase=phrase),self.assertRaises(ValueError):
                E.apply_reference_clarification(answer,request,self.judgment(request,phrase))

    def test_compound_antecedent_cannot_drop_its_nonuse_prefix(self):
        answer=copy.deepcopy(self.answer)
        answer['headline']['template']=answer['headline']['template'].replace('rejection reason','non-use reason')
        request=self.request(answer)
        with self.assertRaises(ValueError):E.apply_reference_clarification(answer,request,self.judgment(request,'use reason'))
        expanded,_=E.apply_reference_clarification(answer,request,self.judgment(request,'non-use reason'))
        B.bind_computed_prose(expanded,self.calls)
        self.assertIn('not treated as a non-use reason.',expanded['headline'])

    def test_explicitly_disabled_clarification_keeps_the_compiler_guard(self):
        with patch.dict(os.environ,{'AGERE_REFERENCE_CLARIFICATION_CHECK':'off'}), \
             patch.object(B,'review_reference_clarification',side_effect=AssertionError('Unselected candidate')), \
             patch.object(G,'ready',return_value=False):
            out=B.review_final(SimpleNamespace(allow_live=True),'Compare invoices',copy.deepcopy(self.answer),self.calls,
                SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
        self.assertEqual(out['repair_kind'],'deterministic_compiler')

    def test_external_api_credentials_cannot_be_used_by_reference_review(self):
        request=self.request()
        with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription','ANTHROPIC_API_KEY':'fixture-external-key'}):
            with self.assertRaisesRegex(ValueError,'authorized Claude subscription'):
                B.review_reference_clarification(SimpleNamespace(),request,ledger=None)

    def test_full_meaning_review_still_runs_and_accounting_is_not_overwritten(self):
        def diagnose(stack,request,**kw):
            return {'judgments':self.judgment(request),'calls':1,'cost_usd':0.005,'cost_known':True,'model':'fixture'}
        answer=copy.deepcopy(self.answer)
        with patch.dict(os.environ,{'AGERE_REFERENCE_CLARIFICATION_CHECK':'on'}), \
             patch.object(B,'review_reference_clarification',side_effect=diagnose)as diagnosis, \
             patch.object(G,'ready',return_value=True), \
             patch.object(G,'check',side_effect=ValueError('Full meaning review reached'))as meaning:
            out=B.review_final(SimpleNamespace(allow_live=True),'Compare invoices',answer,self.calls,
                SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
        diagnosis.assert_called_once();meaning.assert_called_once()
        self.assertNotEqual(out['status'],'confirmed');self.assertIn('Full meaning review reached',out['reason'])
        self.assertEqual(out['calls'],1);self.assertEqual(out['cost_usd'],0.005)
        self.assertIn('as a rejection reason.',answer['headline'])

    def test_failed_diagnosis_keeps_compiler_error_and_actual_limit_propagates(self):
        for error in [ValueError('Invalid reference response'),ProviderUnavailable('Subscription limit',kind='rate_limit')]:
            with patch.dict(os.environ,{'AGERE_REFERENCE_CLARIFICATION_CHECK':'on'}), \
                 patch.object(B,'review_reference_clarification',side_effect=error),patch.object(G,'ready',return_value=False):
                if getattr(error,'kind',None)=='rate_limit':
                    with self.assertRaises(ProviderUnavailable):
                        B.review_final(SimpleNamespace(allow_live=True),'Compare invoices',copy.deepcopy(self.answer),self.calls,
                            SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
                else:
                    out=B.review_final(SimpleNamespace(allow_live=True),'Compare invoices',copy.deepcopy(self.answer),self.calls,
                        SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
                    self.assertEqual(out['repair_kind'],'deterministic_compiler')
                    self.assertIn('Invalid reference response',out['reference_clarification']['error'])


if __name__=='__main__':unittest.main()
