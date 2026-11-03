"""A complete answer cannot simultaneously declare unmet requirements."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,system_one_gate as G

class CompletenessContract(unittest.TestCase):
    def review(self,status,missing,limitations=()):
        call={'name':'query_sql','input':{'sql':'SELECT patient_id FROM patient'},'result':{
            'result_view':'agere_result_1','columns':['patient_id'],'rows':[{'patient_id':'A'}],
            'executed_query':'SELECT patient_id FROM patient','read_views':[]}}
        answer={'status':status,'answer_type':'table','headline':'Received patient membership',
            'table_from_tool':'agere_result_1','missing':list(missing),'limitations':list(limitations)}
        with patch.object(G,'ready',return_value=False)as ready:
            verdict=B.review_final(SimpleNamespace(), 'List received patients',answer,[call],
                SimpleNamespace(read={},document_reviews=[],executed_results={}),ledger=None,as_of='2030-05-27')
        return verdict,answer,ready.call_count

    def test_complete_label_with_declared_missing_requirement_is_repaired_before_paid_review(self):
        verdict,answer,gate_calls=self.review('answered',['The requested eligibility decision is not established.'])
        self.assertEqual(verdict['status'],'repair')
        self.assertIn('missing',verdict['feedback'])
        self.assertEqual(gate_calls,0)
        self.assertEqual(answer['missing'],['The requested eligibility decision is not established.'])

    def test_partial_answer_retains_genuine_unknown_without_forced_certainty(self):
        verdict,answer,gate_calls=self.review('partial',['No dated clinician assignment is available.'])
        self.assertNotEqual(verdict['status'],'repair')
        self.assertEqual(answer['missing'],['No dated clinician assignment is available.'])
        self.assertEqual(gate_calls,1)

    def test_method_notes_do_not_create_a_missing_requirement(self):
        note='Membership uses the declared warehouse population.'
        verdict,answer,gate_calls=self.review('answered',[],[note])
        self.assertNotEqual(verdict['status'],'repair')
        self.assertEqual(answer['limitations'],[note]);self.assertEqual(answer['missing'],[])
        self.assertEqual(gate_calls,1)

if __name__=='__main__':unittest.main()
