"""Actual planner/reviewer boundaries retain governed definitions and time anchors.
Authored callbacks test request construction, not model or clinical accuracy.
"""
import copy
import json
import unittest
from unittest.mock import Mock

import test_shared_planner as fixtures
from test_general_analysis import Q, spec
from clinical_core.agere.clinical import decision_context as D, general_analysis as G, request_language as L, shared_planner as S


class ReviewDecisionContext(unittest.TestCase):
    setUp = fixtures.SharedPlanner.setUp

    def checker(self, proposal, question, *, program=False, top_level_clock=True):
        calls=[]
        def model(system,user,**kwargs):
            calls.append((system,json.loads(user)))
            if len(calls)==1:return json.dumps(proposal),{}
            self.assertIn(D.prompt_block(),system)
            data=calls[-1][1]
            self.assertEqual(data['original_question'],question)
            if top_level_clock:self.assertEqual(data['as_of'],self.catalog.identity['as_of'])
            answer={'equivalent':True,'issues':[]}
            if program:answer['nodes']=[{'id':n['id'],'equivalent':True,'issues':[]}for n in proposal['program']['nodes']]
            return json.dumps(answer),{}
        return model,calls

    def test_analysis_review_receives_planner_definitions_and_exact_snapshot(self):
        model,calls=self.checker({'analysis':spec()},Q)
        contract,interpretation=S.interpret(self.catalog,Q,model=model,force_model=True)
        self.assertEqual(len(calls),2)
        self.assertEqual(G.preview(self.catalog,contract)[0]['value'],1)
        self.assertFalse(interpretation['execution_accepted']) # input completeness cannot promote a model verdict
        self.assertEqual(interpretation['semantic_review'],{'equivalent':True,'issues':[]})

    def test_typed_review_receives_the_same_governed_decision_block(self):
        question='Count patients with diabetes'
        proposal=L.parse(self.catalog,question)
        model,calls=self.checker(proposal,question,top_level_clock=False)
        contract,interpretation=S.interpret(self.catalog,question,model=model,force_model=True)
        self.assertEqual(len(calls),2)
        self.assertIn('diabetes',contract['query'])
        self.assertFalse(interpretation['execution_accepted'])

    def test_program_review_receives_snapshot_and_definitions_for_all_nodes(self):
        question=Q+'; count claims'
        second={'steps':[{'name':'counted','purpose':'Count received claims','sql':'SELECT COUNT(*) AS n FROM claim'}],
                'returns':'count','requirements':[{'quote':'count claims','applied_in':['counted'],'how':'Count received claims'}], 'unresolved':[]}
        proposal={'program':{'nodes':[{'id':'patients','quote':Q,'inputs':[],'analysis':spec()},
                                    {'id':'claims','quote':'count claims','inputs':[],'analysis':second}], 'unresolved':[]}}
        model,calls=self.checker(proposal,question,program=True)
        contract,interpretation=S.interpret(self.catalog,question,model=model,force_model=True)
        self.assertEqual(len(calls),2)
        self.assertEqual(len(contract['nodes']),2)
        self.assertFalse(interpretation['execution_accepted'])

    def test_rescue_analysis_review_keeps_the_clock_and_governed_context(self):
        # Compilation errors enter the existing final-analysis path; its review
        # must not lose the context that the ordinary analysis reviewer receives.
        calls=[]
        def model(system,user,**kwargs):
            data=json.loads(user);calls.append((system,data))
            if len(calls)<=3:return json.dumps({'source':{'table':'absent','quote':'patients'}}),{}
            if 'Review a staged warehouse analysis' in system:
                self.assertIn(D.prompt_block(),system)
                self.assertEqual(data['as_of'],self.catalog.identity['as_of'])
                return json.dumps({'equivalent':True,'issues':[]}),{}
            return json.dumps({'analysis':spec()}),{}
        contract,interpretation=S.interpret(self.catalog,Q,model=model,force_model=True)
        self.assertTrue(interpretation['final_llm_analysis'])
        self.assertEqual(G.preview(self.catalog,contract)[0]['value'],1)
        self.assertFalse(interpretation['execution_accepted'])


if __name__=='__main__':unittest.main()
