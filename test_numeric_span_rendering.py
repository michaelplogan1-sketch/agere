"""Source-bound display signs and temporal precision must not trigger rereading."""
import unittest
from clinical_core.agere.clinical import execution_state as E,backstop_agent as B

class NumericSpanRendering(unittest.TestCase):
    def setUp(self):
        self.native={'result':{'result_view':'result','columns':['increase_pct'],'rows':[[10.0]],'truncated':False}}
        self.address={'result_view':'result','row':0,'column':'increase_pct'}
    def bind(self,text,literal):
        return E.bind_prose_literals(text,{'bind_literals':[{'literal':literal,'binding':self.address}]},self.native)
    def test_signed_source_scalar_retains_explicit_plus_and_complete_sentence(self):
        original='The requested +10% demand scenario.'
        bound=self.bind(original,'+10');rendered=E.repair_rendered_value(bound,self.native)
        self.assertEqual(rendered,'The requested +10.0% demand scenario.')
        self.assertEqual(E.canonical_repair_prose(original),E.canonical_repair_prose(rendered))
        self.assertEqual(bound['bindings']['bound_literal_0'],self.address)
        answer={'headline':bound,'table_binding':{'result_view':'result'}}
        B.bind_computed_prose(answer,[{'name':'query_sql','result':self.native['result']}])
        self.assertEqual(answer['headline'],rendered)
    def test_positive_formatting_does_not_relax_value_or_negative_sign(self):
        for text,literal in [('Demand -10%.','-10'),('Demand +11%.','+11'),('Demand +1.0%.','+1.0')]:
            with self.subTest(text=text),self.assertRaises(ValueError):self.bind(text,literal)
        with self.assertRaises(ValueError):self.bind('Demand +10%.','10')
    def test_grouped_positive_numbers_preserve_sign_without_changing_source(self):
        self.native['result']['rows']=[[1000]]
        self.assertEqual(E.repair_rendered_value(self.bind('Difference +1,000 units.','+1,000'),self.native),'Difference +1000 units.')
    def test_valid_month_day_and_timestamp_addresses_preserve_exact_text(self):
        for text in ('From 2028-01 to 2029-12.', 'Signal 2028-09..2029-04.',
                     'Source date 2030-05-27.', 'As of 2030-05-27T10:25:30+00:00.',
                     'Observed at 2030-05-27 10:25Z.'):
            with self.subTest(text=text):
                a={'headline':text,'table_binding':{'result_view':'result'}}
                B.bind_computed_prose(a,[{'name':'query_sql','result':self.native['result']}])
                self.assertEqual(a['headline'],text)
    def test_invalid_clocks_and_other_observed_quantities_still_require_bindings(self):
        for text in ('From 2028-13.', 'On 2030-02-30.', 'At 2030-01-01T25:00Z.',
                     'Observed 10 patients.', 'Dose 10 mg.', 'Observed p = 0.05.', 'Difference -10%.'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                B.bind_computed_prose({'headline':text,'table_binding':{'result_view':'result'}},[{'name':'query_sql','result':self.native['result']}])
    def test_date_parts_cannot_be_rebound_as_quantities(self):
        for text,literal in [('Source month 2028-10.','10'),('Source day 2030-05-10.','10'),('Recorded 2030-01-01T10:00Z.','10')]:
            with self.subTest(text=text),self.assertRaises(ValueError):self.bind(text,literal)
    def test_count_adjacent_to_calendar_address_is_not_hidden(self):
        with self.assertRaises(ValueError):
            B.bind_computed_prose({'headline':'In 2028-03, 10 patients had events.','table_binding':{'result_view':'result'}},[{'name':'query_sql','result':self.native['result']}])
    def test_calendar_precision_can_cross_bound_year_placeholders(self):
        native={'result_view':'years','columns':['first_year','last_year'],'rows':[['2028','2029']],'truncated':False}
        for template in ('Months from {start}-01 to {end}-12.', 'Stable across {start}-{end}.',
                         'Changed from 2028 to {end}.'):
            with self.subTest(template=template):
                bindings={
                    'start':{'result_view':'years','row':0,'column':'first_year'},
                    'end':{'result_view':'years','row':0,'column':'last_year'}}
                a={'headline':{'template':template,'bindings':{k:v for k,v in bindings.items()if '{'+k+'}'in template}},'table_binding':{'result_view':'years'}}
                B.bind_computed_prose(a,[{'name':'query_sql','result':native}])
                self.assertEqual(a['headline'],template.format(start='2028',end='2029'))
    def test_year_sized_count_range_and_invalid_bound_clock_keep_numeric_guards(self):
        for text in ('Between 2028-2029 patients.', 'Across 2028-2029 mg.'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                B.bind_computed_prose({'headline':text,'table_binding':{'result_view':'result'}},[{'name':'query_sql','result':self.native['result']}])
        a={'headline':{'template':'From {year}-13.','bindings':{'year':self.address}},'table_binding':{'result_view':'result'}}
        with self.assertRaises(ValueError):B.bind_computed_prose(a,[{'name':'query_sql','result':self.native['result']}])

if __name__=='__main__':unittest.main()
