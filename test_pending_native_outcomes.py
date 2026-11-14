"""Work judgment sees genuine execution blocks; native UI keeps unknowns unknown."""
import copy
import json
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import system_one_gate as gate,queueing


def capacity_call():
    report={'schema':'queueing/2','inference_status':'insufficient_data',
        'input_scope':{'window':{'start':'2029-01-01','end':'2029-12-31'},'population':'selected','patient_count':2},
        'answer':{'status':'insufficient_data','reason':'no service times observed and no mean_service_minutes provided',
            'source':'appointment','inputs':{'arrivals_used':26,'mean_service_minutes':None,'supplied_servers':None}},
        'warnings':['No staffing answer: no service times.']}
    rows=[{'arrivals_used':26,'required_servers':None,'capacity_gap_servers':None}]
    native={'table':copy.deepcopy(rows),'overall':{'queueing':copy.deepcopy(report['answer']),
        'input_scope':copy.deepcopy(report['input_scope']),'inference_status':'insufficient_data'},'warnings':report['warnings']}
    return {'name':'execute_analysis','input':{'operation':'queueing'},'result':{
        'status':'partial','parameters':{'source':'appointment'},'result_view':'agere_result_1','rows':rows,
        'engine_result':{'answer':native,'results':[{'node':{'operation':'queueing'},'result':report}]}}}


class NativeWorkContext(unittest.TestCase):
    def test_input_block_is_visible_but_cannot_accept_work_or_change_sources(self):
        call=capacity_call();before=copy.deepcopy(call)
        class Judge:
            def judge_fields(self,subject,questions,evidence,**kwargs):
                self.state=json.loads(evidence[0]['text'])
                return {'judgments':{'missing_0':{'label':'pending_work','probability':.99,'decided_by':'jev'}},'steps':[]}
        judge=Judge()
        answer={'headline':'Demand is observed; staffing is unknown.','missing':['Required servers']}
        with patch.dict('os.environ',{'AGERE_NATIVE_WORK_OUTCOMES':'on'}),patch.object(gate,'s1',return_value=judge):
            result=gate.check_pending_work('Estimate capacity.',answer,[call],as_of_date='2030-01-01')
        report=judge.state['executed_outcomes'][0]['native_outcomes'][0]
        self.assertEqual(report['outcome']['inputs']['arrivals_used'],26)
        self.assertIsNone(report['outcome']['inputs']['mean_service_minutes'])
        self.assertEqual(report['input_scope']['patient_count'],2)
        self.assertEqual(judge.state['answer_context']['headline'],answer['headline'])
        self.assertEqual(result['targets'][0]['text'],'Required servers')  # context is not a waiver
        self.assertEqual(call,before)

    def test_unqualified_candidate_is_off_by_default(self):
        class Judge:
            def judge_fields(self,subject,questions,evidence,**kwargs):
                self.state=json.loads(evidence[0]['text']);self.questions=questions;return {'judgments':{},'steps':[]}
        judge=Judge()
        with patch.dict('os.environ',{'AGERE_NATIVE_WORK_OUTCOMES':'off'}),patch.object(gate,'s1',return_value=judge):
            gate.check_pending_work('q',{'missing':['Unresolved input.']},[capacity_call()],as_of_date='2030-01-01')
        self.assertNotIn('executed_outcomes',judge.state)
        self.assertNotIn('Use executed_outcomes',judge.questions['missing_0']['instructions'])

    def test_uncalled_failed_and_other_operator_are_not_input_block_proofs(self):
        self.assertEqual(gate.pending_work_outcomes([{'name':'describe_analysis','input':{'operation':'queueing'},'result':{}}]),[])
        call=capacity_call();call['result']['error']='dispatch failed'
        self.assertEqual(gate.pending_work_outcomes([call]),[])
        call=capacity_call();call['result']['engine_result']['results'][0]['node']['operation']='different'
        self.assertEqual(gate.pending_work_outcomes([call])[0]['native_outcomes'],[])

    def test_registry_scope_and_every_dated_outcome_remain_distinct(self):
        catalog={'as_of':'2029-06-30','quoted_packages':[],'legacy_packages':[],
            'scope':'Installed registries only, not an external search.'}
        calls=[{'name':'describe_analysis','input':{'operation':'program_document'},'result':{'criteria_context':catalog}},capacity_call()]
        second=copy.deepcopy(calls[-1]);second['result']['engine_result']['results'][0]['result']['input_scope']['window']['end']='2029-06-30';calls.append(second)
        outcomes=gate.pending_work_outcomes(calls)
        self.assertEqual(outcomes[0]['installed_criteria_context'],catalog)
        self.assertNotEqual(outcomes[1]['result_digest'],outcomes[2]['result_digest'])
        self.assertEqual(len(outcomes),3)


class NativeCapacityHandoff(unittest.TestCase):
    def answer(self):
        return {'table':copy.deepcopy(capacity_call()['result']['rows']),
            'table_binding':{'result_view':'agere_result_1','copied_from_executed_result':True},
            'status':'partial','headline':'Retained model prose.','investigation_review':{'status':'uncertain'}}

    def test_exact_table_restores_unknown_capacity_and_preserves_review(self):
        answer=self.answer();before=copy.deepcopy(answer)
        self.assertTrue(queueing.attach_outcome(answer,[capacity_call()]))
        self.assertEqual(answer['overall']['queueing']['status'],'insufficient_data')
        self.assertIsNone(answer['table'][0]['required_servers'])
        for k in ('table','status','headline','investigation_review'):self.assertEqual(answer[k],before[k])
        self.assertFalse(answer['native_queueing_binding']['model_verification_changed'])

    def test_changed_or_interpreted_or_unbound_table_cannot_claim_native_outcome(self):
        for change in ('value','view','interpreted','unbound'):
            with self.subTest(change=change):
                answer=self.answer();answer['overall']={'queueing':{'status':'invented'}}
                if change=='value':answer['table'][0]['required_servers']=0
                elif change=='view':answer['table_binding']['result_view']='other'
                elif change=='interpreted':answer['table_binding']['interpreted_columns']=['required_servers']
                else:answer['table_binding']['copied_from_executed_result']=False
                self.assertFalse(queueing.attach_outcome(answer,[capacity_call()]))
                self.assertNotIn('queueing',answer['overall'])
                self.assertNotIn('native_queueing_binding',answer)

    def test_legacy_overall_types_remain_untouched_without_native_binding(self):
        for value in ('text',None,[]):
            answer={'overall':value}
            self.assertFalse(queueing.attach_outcome(answer,[]))
            self.assertEqual(answer,{'overall':value})


if __name__=='__main__':unittest.main()
