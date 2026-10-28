"""Native scalar values, rather than model transcription, populate table prose."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,system_one_gate as G


class ComputedProse(unittest.TestCase):
    def setUp(self):
        self.calls=[{'name':'query_sql','input':{'sql':'SELECT count(*) AS distinct_patients, sum(visits) AS visits FROM scoped_clinic'},
                     'result':{'result_view':'agere_result_0','columns':['distinct_patients','visits','paid_dollars','pct','missing'],
                               'rows':[[12,17,20.5,66.7,None]],'table_rows':1,'truncated':False}}]
    def template(self):
        return {'template':'{people} patients had {visits} visits; ${paid} paid and {pct}% observed. Unavailable: {missing}.',
                'bindings':{k:{'result_view':'agere_result_0','row':0,'column':col} for k,col in
                            [('people','distinct_patients'),('visits','visits'),('paid','paid_dollars'),('pct','pct'),('missing','missing')]}}
    def answer(self):return {'table_binding':{'result_view':'table'},'headline':self.template(),'facts':{'observation':'Current use remains uncertain.'}}
    def test_values_are_inserted_from_executed_cells_with_units_and_null_preserved(self):
        a=self.answer();B.bind_computed_prose(a,self.calls)
        self.assertEqual(a['headline'],'12 patients had 17 visits; $20.5 paid and 66.7% observed. Unavailable: unknown.')
        self.assertEqual(a['computed_prose'][0]['sources']['visits']['value'],17)
        self.assertEqual(a['computed_prose'][0]['sources']['visits']['source_row']['distinct_patients'],12)
    def test_wrong_transcribed_value_is_blocked_before_any_model_call(self):
        a={'status':'answered','answer_type':'table','table_from_tool':'agere_result_0','headline':'12 patients had 13 visits.'}
        with patch.object(G,'check',side_effect=AssertionError('Do not purchase a model check for unbound quantities')):
            review=B.review_final(SimpleNamespace(),'Show clinic visits',a,self.calls,SimpleNamespace(read={}),ledger=None,as_of='2030-01-01')
        self.assertEqual(review['status'],'repair');self.assertEqual(review['calls'],0)
        self.assertIn('template with bindings',review['feedback'])
    def test_metadata_is_recomputed_and_cannot_override_native_values(self):
        a=self.answer();B.bind_computed_prose(a,self.calls)
        a['computed_prose'][0]['sources']['visits']['value']=13
        B.bind_computed_prose(a,self.calls)
        self.assertIn('17 visits',a['headline']);self.assertEqual(a['computed_prose'][0]['sources']['visits']['value'],17)
        a['headline']='12 patients had 13 visits.'
        with self.assertRaisesRegex(ValueError,'needs a template'):B.bind_computed_prose(a,self.calls)
    def test_bad_addresses_truncated_results_and_format_expressions_fail_closed(self):
        for change in [{'row':True},{'row':9},{'column':'invented'},{'result_view':'fabricated'}]:
            a=self.answer();a['headline']['bindings']['visits'].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):B.bind_computed_prose(a,self.calls)
        calls=copy.deepcopy(self.calls);calls[0]['result']['truncated']=True
        with self.assertRaisesRegex(ValueError,'incomplete'):B.bind_computed_prose(self.answer(),calls)
        for expression in ['{visits.__class__}','{visits[0]}','{visits:.0f}','{visits!r}']:
            a=self.answer();a['headline']['template']=expression
            with self.subTest(expression=expression),self.assertRaises(ValueError):B.bind_computed_prose(a,self.calls)
    def test_hidden_literal_quantities_ranges_and_words_cannot_evade_binding(self):
        for text in ['13 visits','thirteen visits','-3 dollars','1-3 visits','$1,000 paid','at most 4 patients']:
            a=self.answer();a['headline']['template']=text+'; {visits}'
            a['headline']['bindings']={'visits':{'result_view':'agere_result_0','row':0,'column':'visits'}}
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,'Numeric prose'):B.bind_computed_prose(a,self.calls)
    def test_dates_ids_and_nonquantitative_clinical_prose_are_retained(self):
        a={'table_binding':{'result_view':'native'},'headline':'SYN-1234 stopped therapy on 2030-01-01. Current tolerance is unknown.',
           'facts':{'note':'A source observation; no clinical eligibility established.'}}
        B.bind_computed_prose(a,self.calls);self.assertIn('2030-01-01',a['headline'])
    def test_template_failure_does_not_partially_rewrite_the_answer(self):
        a=self.answer();a['facts']['bad']='13 visits';original=copy.deepcopy(a)
        with self.assertRaises(ValueError):B.bind_computed_prose(a,self.calls)
        self.assertEqual(a,original)


if __name__=='__main__':unittest.main()
