import itertools,unittest
from clinical_core.agere.clinical import evidence_resolution as resolution,evidence_requirements as er
from test_native_requirements import report,use
from test_warehouse_interpretation import record,ASOF


class ConditionalSetsTests(unittest.TestCase):
    def test_all_requires_pair_for_true_and_each_single_false(self):
        out=resolution.conditional_sets({'all':['a','b']},{'a':None,'b':None})
        self.assertEqual({(r['contract_value'],tuple(r['binding_values'].items())) for r in out['cases']},
            {(True,(('a',True),('b',True))),(False,(('a',False),)),(False,(('b',False),))})

    def test_shared_bindings_and_negation_never_join_incompatible_values(self):
        expr={'all':['a',{'not':'a'}]}
        out=resolution.conditional_sets(expr,{'a':None})
        self.assertEqual([r['contract_value'] for r in out['cases']],[False,False])
        self.assertIsNone(out['current_value'])

    def test_witnesses_are_sufficient_and_minimal_against_exhaustive_partial_assignments(self):
        expressions=[{'any':[{'all':['a','b']},{'all':['a','c']}]},
                     {'all':[{'any':['a','b']},{'any':[{'not':'a'},'c']}]},
                     {'not':{'all':['a',{'not':{'any':['b','c']}}]}}]
        for expr in expressions:
            for initial in itertools.product((False,True,None),repeat=3):
                values=dict(zip('abc',initial));out=resolution.conditional_sets(expr,values)
                expected=set()
                unknown=[k for k,v in values.items() if v is None]
                for choices in itertools.product((False,True,None),repeat=len(unknown)):
                    assignment={k:v for k,v in zip(unknown,choices) if v is not None}
                    result=er.logic(expr,{**values,**assignment})
                    if result is None:continue
                    if any(er.logic(expr,{**values,**{k:v for k,v in assignment.items() if k!=drop}}) is result for drop in assignment):continue
                    expected.add((result,tuple(sorted(assignment.items()))))
                actual={(x['contract_value'],tuple(sorted(x['binding_values'].items()))) for x in out['cases']}
                self.assertEqual(actual,expected,(expr,values))

    def test_budget_never_returns_incomplete_sets_as_complete(self):
        out=resolution.conditional_sets({'all':['a','b']},{'a':None,'b':None},maximum_cases=1)
        self.assertFalse(out['complete']);self.assertEqual(out['cases'],[])
        self.assertEqual(out['status'],'expression_budget_exceeded')

    def test_known_outcome_has_empty_assignment_and_no_acquisition_needed(self):
        out=resolution.conditional_sets({'any':['a','b']},{'a':True,'b':None})
        self.assertEqual(out['status'],'already_resolved')
        self.assertEqual(out['cases'][0]['binding_values'],{})
        self.assertEqual(len(out['cases']),1)


class SourceRequestsTests(unittest.TestCase):
    def evaluate(self,records,contract):
        row=er.evaluate(None,er.definition(contract),native_report=report(records),patient_id='P-1',as_of=ASOF)
        row['id']='a'*64
        return resolution.project(row)

    def test_no_source_means_locate_account_not_missing_care(self):
        out=self.evaluate([],'native_medication_nonuse')
        self.assertEqual(out['requests'][0]['kind'],'locate_source_account')
        self.assertFalse(out['requests'][0]['request_sent'])
        self.assertFalse(out['requests'][0]['clinical_execution_authorized'])
        self.assertTrue(out['resolution']['complete'])

    def test_concrete_missing_reference_and_date_are_separate(self):
        row=record('outside','outside_report_status',referral_record_id='missing-ref',performed_service_documented=True,event_date=None)
        out=self.evaluate([row],'native_outside_service')
        kinds={r['kind'] for r in out['requests']}
        self.assertEqual(kinds,{'resolve_source_reference','verify_statement_date'})
        link=next(x for x in out['requests'] if x['kind']=='resolve_source_reference')
        self.assertEqual(link['detail']['dependency']['record_id'],'missing-ref')
        self.assertEqual(set(out['resolution']['cases'][0]['request_ids']),{r['id'] for r in out['requests']})

    def test_conflict_is_reconciled_not_majority_voted(self):
        out=self.evaluate([use('a'),use('b',reported_intent='using')],'native_medication_nonuse')
        self.assertEqual(out['requests'][0]['kind'],'reconcile_competing_accounts')
        self.assertEqual(len(out['requests'][0]['detail']['statement_ids']),2)

    def test_documented_source_does_not_generate_review_work(self):
        out=self.evaluate([use('a')],'native_medication_nonuse')
        self.assertEqual(out['requests'],[])
        self.assertEqual(out['resolution']['status'],'already_resolved')


if __name__=='__main__':unittest.main()
