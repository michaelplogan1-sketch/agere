"""Native scalar values, rather than model transcription, populate table prose."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,system_one_gate as G


class ComputedProse(unittest.TestCase):
    def test_indefinite_personal_pronoun_keeps_source_assertion_without_scalar_repair(self):
        for text in ('No one has checked whether the listed owner is responsible.',
                     'No-one was assigned to review the account.'):
            answer={'table_binding':{'result_view':'table'},'headline':text}
            B.bind_computed_prose(answer,[])
            self.assertEqual(answer['headline'],text)
            self.assertEqual(answer['computed_prose'][0]['parameters'][0]['kind'],
                             'indefinite_personal_pronoun')
            self.assertNotIn('verification',answer)
        for text in ('No one patient has a review.', 'No one hundred records were reviewed.',
                     'No one has reviewed two records.', 'One has checked the records.'):
            with self.subTest(text=text),self.assertRaises(B.ComputedProseError):
                B.bind_computed_prose({'table_binding':{'result_view':'table'},'headline':text},[])

    def test_initial_row_prose_example_compiles_and_cannot_borrow_another_entitys_scalar(self):
        import json
        rules=B.RULES.format(tool_quota='No fixed quota.')
        spec,_=json.JSONDecoder().raw_decode(rules[rules.index('{"keys":["patient_id"]'):])
        calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id,recorded_events FROM observations'},
                'result':{'result_view':'agere_result_9','columns':['patient_id','recorded_events'],
                          'rows':[['the exact ID',2]],'truncated':False}},
               {'name':'query_sql','input':{'sql':'SELECT patient_id,recorded_events FROM other_observations'},
                'result':{'result_view':'agere_result_10','columns':['patient_id','recorded_events'],
                          'rows':[['another entity',2]],'truncated':False}}]
        def candidate():return {'status':'partial','table_from_tool':'agere_result_9','row_prose':copy.deepcopy(spec)}
        a=candidate();B.materialize_table(a,calls);B.bind_computed_prose(a,calls)
        self.assertEqual(a['table'][0]['recorded_events'],2)
        self.assertEqual(a['table'][0]['patient_explanation'],'Recorded events: 2. Current responsibility remains unknown.')
        for change in ('foreign_binding','foreign_basis','unbound_text'):
            a=candidate();row=a['row_prose']['rows'][0]
            if change=='foreign_binding':row['fields']['patient_explanation']['bindings']['events']['result_view']='agere_result_10'
            elif change=='foreign_basis':row['basis'].append({'result_view':'agere_result_10','row':0})
            else:row['fields']['patient_explanation']='Recorded events: two. Current responsibility remains unknown.'
            with self.subTest(change=change),self.assertRaises(B.ComputedProseError):
                B.materialize_table(a,calls);B.bind_computed_prose(a,calls)

    def test_short_date_pairs_are_temporal_anchors_but_invalid_dates_and_ratios_still_bind(self):
        for text in ('Observations on 2026-09-03/17.','Observations on 2024-02-01/29.'):
            a={'table_binding':{'result_view':'table'},'headline':text}
            B.bind_computed_prose(a,[])
            self.assertEqual(a['headline'],text)
        for text in ('Observations on 2026-02-01/29.','Dose 10/20 mg.','There are 2026 patients.'):
            with self.subTest(text=text),self.assertRaises(B.ComputedProseError):
                B.bind_computed_prose({'table_binding':{'result_view':'table'},'headline':text},[])

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

    def test_requested_year_anchors_compile_without_becoming_computed_quantities(self):
        question='Compare calendar years 2028 and 2029.'
        for text in ['In 2029, {n} patients had visits.',
                     '{n} patients have amounts in 2029 than in 2028.',
                     '{n} patients have an unknown 2028 or 2029 total (missing source).',
                     '{n} patients meet the stated 2029 thresholds for claims.',
                     '{n} patients have an empty 2028 total that cannot serve as a denominator.',
                     '{n} patients in calendar years 2028 and 2029.']:
            a={'table_binding':{'result_view':'table'},'headline':{'template':text,'bindings':{'n':{'result_view':'agere_result_0','row':0,'column':'distinct_patients'}}}}
            B.bind_computed_prose(a,self.calls,question=question)
            self.assertEqual(a['headline'],text.replace('{n}','12'))
            with self.assertRaises(ValueError):B.bind_computed_prose(a,self.calls,question='Compare calendar year 2030.')

    def test_year_sized_counts_amounts_and_unrequested_dates_still_need_bindings(self):
        for text in ['2029 patients','in 2029 patients','for 2029 days','2029 total patients',
                     '2029 total claims','2028 or 2029 total records','$2029','2029 mg',
                     'received 2029 dollars','in 2030','2029']:
            a={'table_binding':{'result_view':'table'},'headline':text}
            with self.subTest(text=text),self.assertRaises(ValueError):
                B.bind_computed_prose(a,self.calls,question='Compare calendar years 2028 and 2029.')

    def test_literal_year_intervals_cannot_launder_a_previous_request_scope(self):
        for text in ('Across 2028-2029.', 'Between 2028 and 2029.', 'For calendar years 2028 and 2029.'):
            answer={'table_binding':{'result_view':'table'},'headline':text}
            B.bind_computed_prose(answer,self.calls,question='Compare calendar years 2028 and 2029.')
            original=copy.deepcopy(answer)
            with self.subTest(text=text),self.assertRaises(ValueError):
                B.bind_computed_prose(answer,self.calls,question='Compare calendar year 2030.')
            self.assertEqual(answer,original)

    def test_source_dates_and_bound_historical_intervals_keep_their_temporal_role(self):
        calls=self.calls+[{'name':'query_sql','result':{'result_view':'source_years','columns':['first_year','last_year'],
            'rows':[['2028','2029']],'truncated':False}}]
        answer={'table_binding':{'result_view':'table'},'headline':{
            'template':'Historical source range from {start}-{end}; recorded on 2028-01-01.',
            'bindings':{'start':{'result_view':'source_years','row':0,'column':'first_year'},
                        'end':{'result_view':'source_years','row':0,'column':'last_year'}}}}
        B.bind_computed_prose(answer,calls,question='Compare calendar year 2030.')
        self.assertEqual(answer['headline'],'Historical source range from 2028-2029; recorded on 2028-01-01.')
    def test_template_failure_does_not_partially_rewrite_the_answer(self):
        a=self.answer();a['facts']['bad']='13 visits';original=copy.deepcopy(a)
        with self.assertRaises(ValueError):B.bind_computed_prose(a,self.calls)
        self.assertEqual(a,original)

    def test_feedback_lists_all_unbound_fields_and_literals_in_one_pass(self):
        a=self.answer();a['headline']['template']='Last 12 months: {visits} visits and four measures.'
        a['headline']['bindings']={'visits':{'result_view':'agere_result_0','row':0,'column':'visits'}}
        a['facts']['patients']='13 patients'
        with self.assertRaises(ValueError) as error:B.bind_computed_prose(a,self.calls)
        for part in ['headline','12','four','facts.patients','13','explicitly named definition constant']:
            self.assertIn(part,str(error.exception))

    def test_provenance_checked_attributed_measurements_are_not_population_arithmetic(self):
        a={'table_binding':{'result_view':'native'},'headline':"N1: 'Blood pressure was 96 today.'",
           'facts':{'code':'Received diagnosis I11.0, patient SYN-1234 on 2030-01-01.'}}
        quote={'record_id':'N1','quote':'Blood pressure was 96 today.'}
        B.bind_computed_prose(a,self.calls,verified_quotes=[quote])
        with self.assertRaises(ValueError):B.bind_computed_prose(a,self.calls)
        a['headline']+=" Also 96 patients."
        with self.assertRaises(ValueError):B.bind_computed_prose(a,self.calls,verified_quotes=[quote])

    def test_review_reports_workflow_and_numeric_repairs_together_without_provider_use(self):
        a={'status':'answered','answer_type':'table','table_from_tool':'agere_result_0','headline':'13 visits',
           'workflow':[{'kind':'deliverable','title':'Clinic proposal','template':'brief','purpose':'proposal',
                        'quote':'create a clinic service proposal','unresolved':['Eligibility unknown']} ]}
        with patch.object(G,'ready',side_effect=AssertionError('Static errors must come first')):
            result=B.review_final(SimpleNamespace(),'Show visits and create a clinic service proposal',a,self.calls,SimpleNamespace(read={}),ledger=None,as_of='2030-01-01')
        self.assertEqual(result['status'],'repair');self.assertEqual(result['calls'],0)
        self.assertIn('13',result['feedback']);self.assertIn('unresolved=[]',result['feedback'])

    def test_uncompiled_final_draft_has_a_readable_partial_fallback_and_keeps_native_rows(self):
        a={'status':'partial','headline':self.template(),'facts':{'bad':'13 visits'},'table':[{'visits':17}]}
        original=copy.deepcopy(a);out=B.shaped(copy.deepcopy(a))
        self.assertIsInstance(out['headline'],str);self.assertIn('needs repair',out['headline'])
        self.assertEqual(out['table'],original['table']);self.assertEqual(out['facts'],{})
        self.assertTrue(out['uncompiled_prose_fingerprint']);self.assertEqual(a,original)

    def test_numeric_repair_also_locates_unfinished_interpretation_before_regeneration(self):
        calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id,review FROM observations'},
                'result':{'result_view':'agere_result_0','columns':['patient_id','review'],
                          'rows':[['A','Inspect the available findings and write the explanation.']],
                          'table_rows':1,'truncated':False}}]
        a={'status':'partial','answer_type':'table','table_from_tool':'agere_result_0','headline':'13 patients'}
        def judge(question,questions,state,**kwargs):
            self.assertIn('original_question',state[0]['text'])
            self.assertEqual(set(questions),{'row_0'})
            return {'judgments':{'row_0':{'label':'unfinished','probability':.98,'decided_by':'jev'}},
                    'steps':[{'provider':'jev','cost_usd':.002,'status':'answered','attempt_id':'fixture'}]}
        with patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=SimpleNamespace(judge_fields=judge)),\
             patch.object(G,'check',side_effect=AssertionError('No coverage/source review before compiler repair')):
            result=B.review_final(SimpleNamespace(),'Explain the findings for each patient.',a,calls,SimpleNamespace(read={}),ledger=None,as_of='2030-01-01')
        self.assertEqual(result['repair_kind'],'incomplete_composition')
        self.assertIn('13',result['feedback']);self.assertIn('explicitly defers',result['feedback'])
        self.assertEqual(result['calls'],1);self.assertEqual(result['cost_usd'],.002)
        self.assertEqual(result['progress_state']['unfinished_interpretation'][0]['patient_id'],'A')
        self.assertTrue(result['progress_state']['prose']);self.assertEqual(result['support']['label'],'not_run')
        self.assertEqual(a['table'],[{'patient_id':'A','review':calls[0]['result']['rows'][0][1]}])

    def test_missing_row_prose_uncertain_and_data_only_judgments_do_not_invent_an_omission(self):
        for label,provider,probability in [('no_observed_omission','jev',.99),('not_requested','jev',.99),
                                          ('unfinished',None,.99),('unfinished','jev',.1),('unknown',None,.5)]:
            a={'status':'partial','answer_type':'table','table_from_tool':'agere_result_0','headline':'13 visits'}
            judge=lambda *a,**kw:{'judgments':{'row_0':{'label':label,'probability':probability,'decided_by':provider}}}
            with self.subTest(label=label,provider=provider,probability=probability),\
                 patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=SimpleNamespace(judge_fields=judge)),\
                 patch.object(G,'check',side_effect=AssertionError('Quantity repair remains necessary')):
                result=B.review_final(SimpleNamespace(),'Show visits',a,self.calls,SimpleNamespace(read={}),ledger=None,as_of='2030-01-01')
            self.assertEqual(result['repair_kind'],'deterministic_compiler')
            self.assertNotIn('unfinished_interpretation',result['progress_state'])
            self.assertIn('13',result['feedback']);self.assertNotEqual(result['status'],'confirmed')

    def test_uncompiled_row_prose_does_not_review_stale_native_placeholders(self):
        from clinical_core.agere.clinical import execution_state as E
        calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id,events,review FROM observations'},
                'result':{'result_view':'agere_result_0','columns':['patient_id','events','review'],
                          'rows':[['A',2,'Inspect findings and write the explanation.'],
                                  ['B',3,'Inspect findings and write the explanation.']],
                          'table_rows':2,'truncated':False}}]
        draft={'status':'partial','answer_type':'table','table_from_tool':'agere_result_0',
               'headline':'Source findings retained.',
               'row_prose':{'keys':['patient_id'],'rows':[
                   {'key':{'patient_id':'A'},'basis':[{'result_view':'agere_result_0','row':0}],
                    'fields':{'explanation':'Two observed events; current responsibility is unknown.'}},
                   {'key':{'patient_id':'B'},'basis':[{'result_view':'agere_result_0','row':1}],
                    'fields':{'explanation':'Historical findings are retained; current responsibility is unknown.'}}]}}
        original=copy.deepcopy(draft)
        with patch.object(G,'ready',side_effect=AssertionError('Repair compiler before judging stale native text')):
            review=B.review_final(SimpleNamespace(),'Explain each patient.',draft,calls,SimpleNamespace(read={}),ledger=None,as_of='2030-01-01')
        self.assertEqual(review['repair_kind'],'deterministic_compiler')
        self.assertNotIn('unfinished_interpretation',review['progress_state'])
        targets=E.prose_repair_targets(draft,review)
        self.assertEqual([(t['key'],t['field'])for t in targets],[({'patient_id':'A'},'explanation')])
        self.assertEqual(draft['row_prose'],original['row_prose'])
        self.assertEqual(review['calls'],0);self.assertNotEqual(review['status'],'confirmed')
        # Repairing syntax must still reach the existing coverage/source path.
        draft['row_prose']['rows'][0]['fields']['explanation']={
            'template':'{n} observed events; current responsibility is unknown.',
            'bindings':{'n':{'result_view':'agere_result_0','row':0,'column':'events'}}}
        with patch.object(G,'ready',return_value=True),patch.object(G,'check',side_effect=ValueError('full review required')) as coverage:
            checked=B.review_final(SimpleNamespace(),'Explain each patient.',draft,calls,SimpleNamespace(read={}),ledger=None,as_of='2030-01-01')
        coverage.assert_called_once();self.assertIn('full review required',checked['reason'])
        self.assertEqual(draft['table'][0]['explanation'],'2 observed events; current responsibility is unknown.')

    def test_invalid_row_prose_is_still_a_compiler_failure_not_an_accepted_omission(self):
        for value in ({}, {'keys':[],'rows':[]}):
            draft={'status':'partial','answer_type':'table','table_from_tool':'agere_result_0',
                   'headline':'Source findings retained.','row_prose':value}
            with self.subTest(value=value),patch.object(G,'ready',side_effect=AssertionError('No model review of malformed prose')):
                review=B.review_final(SimpleNamespace(),'Explain findings.',draft,self.calls,SimpleNamespace(read={}),ledger=None,as_of='2030-01-01')
            self.assertEqual(review['repair_kind'],'deterministic_compiler');self.assertEqual(review['status'],'repair')
            self.assertFalse(review.get('prose_repair'));self.assertIn('row_prose',review['feedback'])

    def test_early_judge_unavailable_preserves_compiler_feedback_and_unknown_cost(self):
        a={'status':'partial','answer_type':'table','table_from_tool':'agere_result_0','headline':'13 visits'}
        with patch.object(G,'ready',return_value=True),patch.object(G,'check_composition',side_effect=OSError('fixture outage')):
            result=B.review_final(SimpleNamespace(),'Explain visits',a,self.calls,SimpleNamespace(read={}),ledger=None,as_of='2030-01-01')
        self.assertEqual(result['repair_kind'],'deterministic_compiler');self.assertIn('13',result['feedback'])
        self.assertEqual(result['composition']['status'],'unavailable')
        self.assertIsNone(result['cost_usd']);self.assertFalse(result['cost_known'])
        limited=RuntimeError('fixture subscription limit');limited.kind='rate_limit'
        with patch.object(G,'ready',return_value=True),patch.object(G,'check_composition',side_effect=limited),self.assertRaises(RuntimeError):
            B.review_final(SimpleNamespace(),'Explain visits',copy.deepcopy(a),self.calls,SimpleNamespace(read={}),ledger=None,as_of='2030-01-01')

    def test_valid_native_answer_keeps_existing_review_order(self):
        a={'status':'partial','answer_type':'table','table_from_tool':'agere_result_0','headline':self.template()}
        with patch.object(G,'ready',return_value=True),patch.object(G,'check',side_effect=ValueError('coverage reached')) as coverage,\
             patch.object(G,'check_composition',side_effect=AssertionError('No early check without numeric failure')) as composition:
            result=B.review_final(SimpleNamespace(),'Show visits',a,self.calls,SimpleNamespace(read={}),ledger=None,as_of='2030-01-01')
        coverage.assert_called_once();composition.assert_not_called()
        self.assertIn('coverage reached',result['reason'])


if __name__=='__main__':unittest.main()
