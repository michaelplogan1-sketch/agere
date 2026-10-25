"""Compound single-patient note answers must execute the compiled Boolean tree."""
import copy
import unittest
from unittest.mock import patch
from spec_test_support import Warehouse, AS_OF
from clinical_core.agere.clinical import spec_exec as E, request_plan as R, spec_fill as F, decision_context as D, shared_planner as S


class NoteConditionContract(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse(); self.addCleanup(self.w.close)
        for name in ('note_frames.evaluate','chart_reader.answer'):
            self.addCleanup(R.register_executor,name,None)
        self.calls=[]
        self.statuses={'cough':'documented','fever':'denied'}
        def evaluate(stack,pred,ids,*,as_of,ledger,allow_live=True):
            self.calls.append((pred['concept']['term'],list(ids),allow_live))
            term=pred['concept']['term'];status=self.statuses[term]
            return {pid:{'status':status,'detail':{},'evidence':[{'record_id':'N-'+term,'date':'2029-09-01','note_type':'progress','quote':term+': '+status}]}for pid in ids}
        R.register_executor('note_frames.evaluate',evaluate)
        # A flat reader's ANY-positive answer is deliberately wrong for AND/NOT.
        R.register_executor('chart_reader.answer',lambda *a,**k: {'status':'answered','answer_type':'yes_no','value':True,'yes_no':True,'headline':'Yes'})

    def leaf(self,term):
        return {'op':'note','frame':'symptom','concept':{'term':term,'quote':term},'statuses':['documented'],'quote':'documented '+term}

    def contract(self,operator='and',*,pid='SYN-A',structured=None,detail=False):
        if operator=='not':
            question=f'Does {pid} not have documented fever?';where={'op':'not','quote':'not','arg':self.leaf('fever')}
        else:
            question=f'Does {pid} have documented cough {operator} documented fever?'
            where={'op':operator,'quote':operator,'args':[self.leaf('cough'),self.leaf('fever')]}
        if structured:
            question=question[:-1]+f' and sex {structured}?'
            where={'op':'and','quote':'and','args':[where,{'field':'patient.sex_reported','op':'=','value':structured,'quote':'sex '+structured}]}
        if detail:question+=' What was the cause?'
        spec={'source':{'table':'patient','quote':pid},'subject':{'kind':'patient','patient_id':pid,'quote':pid},
              'select':[{'kind':'note_answer','alias':'answer','quote':(['Does','What was the cause'] if detail else 'Does')}],'where':where,'group_by':[],'order_by':[],'limit':None,'unresolved':[]}
        return question,F.validate(self.w.catalog,question,spec)

    def answer(self,operator='and',**kw):
        question,contract=self.contract(operator,**kw);before=copy.deepcopy(contract)
        with D.scope(question,AS_OF):
            receipt={'route':'shared_planner','request_contract':contract,'interpretation':{'execution_accepted':True},'new_paid_api_calls':0,'known_api_estimate_usd':0,'api_cost_known':True}
            out=S.execute_contract(self.w.stack,question,S.graph_for(contract),receipt)['answer']
        self.assertEqual(contract,before)
        self.assertTrue(all(live is False for _,_,live in self.calls))
        self.assertTrue(all(ids==[contract['subject']['patient_id']]for _,ids,_ in self.calls))
        return out

    def test_and_is_not_any_positive(self):
        out=self.answer();self.assertIs(out['value'],False);self.assertEqual(out['status'],'answered')
        self.assertEqual({s['record_id']for s in out['sources']},{'N-cough','N-fever'})

    def test_or_short_circuits_the_already_decided_subject(self):
        out=self.answer('or');self.assertIs(out['value'],True)
        self.assertEqual([term for term,_,_ in self.calls],['cough'])

    def test_not_keeps_negation(self):
        self.assertIs(self.answer('not')['value'],True)
        self.statuses['fever']='documented';self.calls.clear()
        self.assertIs(self.answer('not')['value'],False)

    def test_unknown_stays_unknown_under_not_and_and(self):
        self.statuses['fever']='unknown'
        for op in ('not','and'):
            with self.subTest(op=op):
                self.calls.clear();out=self.answer(op);self.assertIsNone(out['value']);self.assertEqual(out['status'],'partial');self.assertEqual(out['unknown_count'],1)

    def test_structured_false_sibling_is_not_dropped_or_read_as_a_note(self):
        out=self.answer('or',structured='M');self.assertIs(out['value'],False);self.assertEqual(self.calls,[])

    def test_structured_true_sibling_keeps_the_note_combination(self):
        self.assertIs(self.answer('or',structured='F')['value'],True)

    def test_deceased_named_subject_keeps_its_own_evidence(self):
        self.assertIs(self.answer('or',pid='SYN-E')['value'],True)

    def test_open_detail_request_is_not_substituted_by_boolean_membership(self):
        with self.assertRaises(R.PlanError):self.answer('and',detail=True)

    def test_three_valued_truth_tables_are_preserved_end_to_end(self):
        table=[('documented','documented',True,True),('documented','denied',False,True),
               ('documented','unknown',None,True),('denied','documented',False,True),
               ('denied','denied',False,False),('denied','unknown',False,None),
               ('unknown','documented',None,True),('unknown','denied',False,None),('unknown','unknown',None,None)]
        for left,right,and_result,or_result in table:
            for operator,expected in [('and',and_result),('or',or_result)]:
                with self.subTest(left=left,right=right,operator=operator):
                    self.statuses.update(cough=left,fever=right);self.calls.clear()
                    self.assertIs(self.answer(operator)['value'],expected)

    def test_incomplete_membership_contract_is_not_reported_as_false(self):
        question,contract=self.contract('and');contract['membership_query']=None
        with D.scope(question,AS_OF),self.assertRaises(R.PlanError):
            E.note_answer(self.w.stack,contract,as_of=AS_OF,question=question)

    def test_compiled_boolean_does_not_require_the_unrelated_chart_renderer(self):
        original=R.executor
        with patch.object(R,'executor',side_effect=lambda name:None if name=='chart_reader.answer' else original(name)):
            self.assertIs(self.answer('and')['value'],False)
