"""Executed labels and calendar descriptors cannot become observed quantities."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B, system_one_gate as G


class SymbolicProseRoles(unittest.TestCase):
    def setUp(self):
        self.question='Compare the calendar 2028 and 2029 service cohorts, with missing or zero baselines explicit.'
        sql=("SELECT substr(service_date,1,4) AS cohort_year, CASE WHEN baseline = 0 THEN 'baseline zero: pct undefined' "
             "ELSE 'observed' END AS pct_change FROM received_cohorts")
        self.calls=[{'name':'query_sql','input':{'sql':sql},'result':{
            'result_view':'result','native_sql_contract':B.NATIVE_SQL_CONTRACT,
            'executed_query':sql,'columns':['cohort_year','pct_change'],
            'rows':[['2028','observed'],['2029','observed']],'truncated':False}}]

    def compile(self,text,calls=None,question=None):
        a={'table_from_tool':'result','headline':text}
        B.materialize_table(a,self.calls if calls is None else calls)
        B.bind_computed_prose(a,self.calls if calls is None else calls,
                              question=self.question if question is None else question)
        return a

    def test_executed_conditional_label_is_a_definition_even_if_branch_not_observed(self):
        text="The query would label such a row 'baseline zero: pct undefined'."
        a=self.compile(text)
        self.assertEqual(a['headline'],text)
        proof=a['computed_prose'][0]['parameters'][0]
        self.assertEqual(proof['kind'],'executed_output_label')
        self.assertEqual(proof['column'],'pct_change')
        self.assertEqual(proof['result_view'],'result')
        self.assertIn('query_sha256',proof)
        self.assertFalse(any(row['pct_change']=='baseline zero: pct undefined'
                             for row in a['table']))

    def test_unbound_observations_and_changed_quoted_labels_still_fail(self):
        for text in ["There were 'zero' patients.",
                     "The patient reported 'baseline zero: pct undefined'.",
                     "The query would label such a row 'baseline one: pct undefined'.",
                     "The query would label such a row 'baseline zero: pct undefined'; zero accounts qualified.",
                     "The query returns a label, and there were 'zero' patients."]:
            with self.subTest(text=text),self.assertRaises(B.ComputedProseError):self.compile(text)

    def test_label_requires_complete_current_conditional_output_in_final_dependencies(self):
        text="The query would label such a row 'baseline zero: pct undefined'."
        for change in ('contract','truncated','error','unexecuted','not_conditional','not_output','unrelated','duplicate'):
            calls=copy.deepcopy(self.calls);r=calls[0]['result']
            if change=='contract':r.pop('native_sql_contract')
            elif change=='truncated':r['truncated']=True
            elif change=='error':r['error']='not executed'
            elif change=='unexecuted':r.pop('executed_query')
            elif change=='not_conditional':r['executed_query']="SELECT 'baseline zero: pct undefined' AS pct_change"
            elif change=='not_output':r['columns']=['cohort_year','other']
            elif change=='duplicate':calls.append(copy.deepcopy(calls[0]))
            else:
                calls[0]['result']['result_view']='unrelated'
                calls.append({'name':'query_sql','input':{'sql':'SELECT * FROM other'},'result':{
                    'result_view':'result','columns':['cohort_year'],'rows':[['2028']]}})
            with self.subTest(change=change),self.assertRaises(ValueError):self.compile(text,calls)

    def test_calendar_cohort_descriptors_and_antecedents_preserve_text(self):
        text=('The 2028 service cohort and the 2029 cohort are shown. '
              'The 2029 observed total remains incomplete.')
        a=self.compile(text)
        self.assertEqual(a['headline'],text)
        proofs=a['computed_prose'][0]['parameters']
        self.assertEqual({p['year']for p in proofs},{'2028','2029'})
        self.assertTrue(all(p['column']=='cohort_year'for p in proofs))

    def test_year_sized_counts_foreign_periods_and_unanchored_references_still_bind(self):
        for text in ('There were 2029 patients.','A 2029 patient cohort was observed.',
                     'A 2029 USD cohort was observed.', 'A 2029 invented cohort was observed.',
                     'There were 2029 cohort members.','The 2027 service cohort is shown.',
                     'The 2029 observed total is shown.'):
            with self.subTest(text=text),self.assertRaises(B.ComputedProseError):self.compile(text)
        for calls in ([],[{**self.calls[0],'result':{**self.calls[0]['result'],
                                                   'columns':['count','pct_change']}}]):
            with self.subTest(calls=calls),self.assertRaises(B.ComputedProseError):
                # No materialized table can waive a year-sized measurement.
                a={'table_binding':{'result_view':'result'},'headline':'The 2029 cohort is shown.'}
                B.bind_computed_prose(a,calls,question=self.question)

    def test_entity_prose_cannot_borrow_global_label_or_calendar_roles(self):
        calls=copy.deepcopy(self.calls);calls[0]['result'].update(
            columns=['patient_id','cohort_year','pct_change'],rows=[['A','2029','observed']])
        for text in ('The 2029 cohort is shown.',"The query returns 'baseline zero: pct undefined'."):
            a={'table_from_tool':'result','row_prose':{'keys':['patient_id'],'rows':[{
                'key':{'patient_id':'A'},'basis':[{'result_view':'result','row':0}],
                'fields':{'explanation':text}}]}}
            with self.subTest(text=text),self.assertRaises(B.ComputedProseError):
                B.materialize_table(a,calls);B.bind_computed_prose(a,calls,question=self.question)

    def test_label_compilation_still_reaches_independent_meaning_review(self):
        a={'status':'partial','answer_type':'table','table_from_tool':'result',
           'headline':"The query would label such a row 'baseline zero: pct undefined'."}
        with patch.object(G,'ready',return_value=True),patch.object(G,'check',side_effect=ValueError('meaning review reached'))as meaning:
            review=B.review_final(SimpleNamespace(),self.question,a,self.calls,SimpleNamespace(read={}),
                                  ledger=None,as_of='2030-05-27')
        meaning.assert_called_once()
        self.assertIn('meaning review reached',review['reason'])
        self.assertNotEqual(review['status'],'confirmed')


if __name__=='__main__':unittest.main()
