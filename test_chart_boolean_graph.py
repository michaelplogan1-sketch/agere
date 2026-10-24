"""A chart boolean must survive graph adaptation without becoming an empty result."""
import unittest
from clinical_core.agere.clinical import chart_reader_op as O,answer_checks as A,literal_check as L

class BooleanGraph(unittest.TestCase):
    def answer(self,value):
        out={'status':'answered','answer_type':'yes_no','value':value,'yes_no':value,
             'headline':('Yes' if value else 'No')+': recorded test in the window','table':[]}
        return O.answer({'answer_contract':out,'patient_ids':['SYN-12345678-ABC']},
                        {'coverage':'supported_observation','unmet_requirements':[]},'Has this patient had the test?')
    def test_no_remains_a_populated_boolean_result(self):
        answer=self.answer(False)
        self.assertEqual(answer['count'],0)
        self.assertEqual(answer['table'],[{'patient_id':'SYN-12345678-ABC','answer':False}])
        self.assertIsNone(A._zero_kind(answer))
        self.assertIsNone(L.shape_misfit('Has the patient had the test?',answer))
    def test_yes_and_no_are_distinct_and_have_the_same_graph_shape(self):
        yes,no=self.answer(True),self.answer(False)
        self.assertEqual((yes['answer_type'],no['answer_type']),('table','table'))
        self.assertEqual((yes['count'],no['count']),(1,0))
        self.assertEqual((yes['table'][0]['answer'],no['table'][0]['answer']),(True,False))
    def test_empty_non_boolean_tables_still_trigger_the_zero_result_check(self):
        answer=O.answer({'answer_contract':{'status':'answered','answer_type':'table','table':[]},'patient_ids':['SYN-12345678-ABC']},
                        {'coverage':'supported_observation','unmet_requirements':[]},'List tests')
        self.assertEqual(A._zero_kind(answer),'empty')
