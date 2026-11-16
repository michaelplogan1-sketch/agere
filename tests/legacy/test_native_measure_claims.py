"""Counts and amounts retain native domain, grain, population and unknowns."""
import copy,unittest
from clinical_core.agere.clinical import result_assembly as A,backstop_agent as B


class Measures(unittest.TestCase):
    def setUp(self):
        self.results={'population':{'columns':['patient_id'],'rows':[{'patient_id':'A'},{'patient_id':'B'}]},
            'events':{'rows':[{'patient_id':'A','record_id':'r1','domain':'inpatient','paid':10.25},
                {'patient_id':'A','record_id':'r2','domain':'outpatient','paid':5.1},
                {'patient_id':'B','record_id':'r3','domain':'inpatient','paid':20.5},
                {'patient_id':'C','record_id':'r4','domain':'inpatient','paid':30.0}]}}
        self.spec={'result_view':'events','operation':'count_rows','grain':['patient_id','record_id'],'unit':'hospital stays',
            'population':{'result_view':'population','on':{'patient_id':'patient_id'}},'domain':{'column':'domain','values':['inpatient']}}

    def test_domain_and_population_are_applied_in_code(self):
        proof=A.measure(self.results,self.spec)
        self.assertEqual(proof['value'],2);self.assertEqual(proof['rows_outside_population'],1)
        self.assertEqual(proof['rows_in_domain'],2);self.assertEqual(proof['contract'],self.spec)

    def test_empty_population_stays_empty(self):
        self.results['population']['rows']=[]
        self.assertEqual(A.measure(self.results,self.spec)['value'],0)

    def test_duplicate_grain_and_duplicate_denominators_cannot_be_counted(self):
        self.results['events']['rows'].append(self.results['events']['rows'][0])
        with self.assertRaisesRegex(ValueError,'grain'):A.measure(self.results,self.spec)
        self.results['events']['rows'].pop();self.results['population']['rows'].append({'patient_id':'A'})
        with self.assertRaisesRegex(ValueError,'unique'):A.measure(self.results,self.spec)

    def test_complete_cardinality_and_received_predicate_columns_are_required(self):
        self.results['events']['table_rows']=99
        with self.assertRaisesRegex(ValueError,'cardinality'):A.measure(self.results,self.spec)
        del self.results['events']['table_rows']
        for change in [{'domain':{'column':'invented','values':['inpatient']}},{'where':[{'column':'domain','op':'contains','value':'in'}]},
                       {'domain':{'column':'domain','values':[]}},{'population':{'result_view':'invented','on':{'patient_id':'patient_id'}}}]:
            with self.subTest(change=change),self.assertRaises(ValueError):A.measure(self.results,{**self.spec,**change})

    def test_distinct_people_are_not_event_counts_and_missing_ids_stay_unknown(self):
        spec={**self.spec,'operation':'count_distinct','distinct':['patient_id']}
        self.results['events']['rows'].append({'patient_id':'A','record_id':'r5','domain':'inpatient','paid':3})
        self.assertEqual(A.measure(self.results,spec)['value'],2)
        self.assertEqual(A.measure(self.results,self.spec)['value'],3)

    def test_financial_decimal_sum_and_nulls_are_explicit(self):
        spec={**self.spec,'operation':'sum','column':'paid','unit':'received paid dollars'}
        self.assertEqual(A.measure(self.results,spec)['value'],'30.75')
        self.results['events']['rows'][2]['paid']=None
        self.assertIsNone(A.measure(self.results,spec)['value'])
        proof=A.measure(self.results,{**spec,'null_policy':'exclude'})
        self.assertEqual(proof['value'],'10.25');self.assertEqual(proof['missing_value_rows'],1)

    def test_boolean_or_nonfinite_numeric_amounts_are_rejected(self):
        for value in [True,float('nan'),float('inf'),'20.5']:
            self.results['events']['rows'][2]['paid']=value
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):
                A.measure(self.results,{**self.spec,'operation':'sum','column':'paid'})

    def test_computed_prose_inserts_the_domain_computed_value_and_recompiles(self):
        calls=[{'name':'query_sql','result':{**v,'result_view':k}}for k,v in self.results.items()]
        answer={'table_binding':{'result_view':'events'},'headline':{'template':'{n} hospital stays.', 'bindings':{'n':{'measure':self.spec}}}}
        B.bind_computed_prose(answer,calls);self.assertEqual(answer['headline'],'2 hospital stays.')
        answer['computed_prose'][0]['sources']['n']['value']=999
        B.bind_computed_prose(answer,calls);self.assertEqual(answer['headline'],'2 hospital stays.')

    def test_a_scalar_for_all_domains_cannot_verify_as_a_domain_count(self):
        calls=[{'name':'query_sql','result':{**v,'result_view':k}}for k,v in self.results.items()]
        calls.append({'name':'query_sql','result':{'result_view':'totals','rows':[{'n':4}],'columns':['n']}})
        answer={'table_binding':{'result_view':'events'},'headline':{'template':'{n} hospital stays.',
            'bindings':{'n':{'result_view':'totals','row':0,'column':'n','measure':self.spec}}}}
        with self.assertRaisesRegex(ValueError,'bound 4, recomputed 2'):B.bind_computed_prose(answer,calls)

    def test_mixed_medication_total_requires_the_actual_domain(self):
        self.results['events']={'rows':[{'patient_id':'A','record_id':str(i),'domain':'therapy' if i<14 else 'other'}for i in range(89)]}
        spec={**self.spec,'domain':{'column':'domain','values':['therapy']},'unit':'documented therapy rows'}
        self.assertEqual(A.measure(self.results,spec)['value'],14)
        self.assertEqual(A.measure(self.results,{**spec,'domain':None})['value'],89)


if __name__=='__main__':unittest.main()
