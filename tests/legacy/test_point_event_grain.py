"""A monthly attribute cannot establish membership at an explicit event date."""
import copy,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import source_scope as S,backstop_agent as B

QUESTION='For 2028 and2029, use eligibility at each discharge and keep all events.'
MONTH="SELECT COALESCE(m.payer_id,'unknown') AS payer_id FROM utilization_event u LEFT JOIN member_month m ON m.patient_id=u.patient_id AND m.month=substr(u.end_date,1,7)"
EXACT="SELECT COALESCE(e.payer_id,'unknown') AS payer_id FROM utilization_event u LEFT JOIN enrollment_span e ON e.patient_id=u.patient_id AND e.coverage_start<=u.end_date AND(e.coverage_end IS NULL OR e.coverage_end>=u.end_date)"


class PointEventGrain(unittest.TestCase):
    def test_observed_monthly_substitution_is_a_source_requirement_failure(self):
        self.assertIn('ANY day',S.point_event_grain_problem(QUESTION,MONTH))
        self.assertIn('at each discharge',S.problem(QUESTION,MONTH))

    def test_monthly_exposure_and_exact_interval_sources_still_work(self):
        self.assertIsNone(S.point_event_grain_problem('Compare monthly eligibility and member-month exposure.',MONTH))
        self.assertIsNone(S.point_event_grain_problem(QUESTION,EXACT))
        self.assertIsNone(S.point_event_grain_problem(QUESTION,'SELECT count(*) FROM member_month'))

    def test_cte_alias_and_period_function_do_not_hide_the_binding(self):
        sql="WITH coverage AS(SELECT patient_id,payer_id,month AS period FROM member_month)SELECT c.payer_id FROM utilization_event u JOIN coverage c ON c.patient_id=u.patient_id AND c.period=substr(u.end_date,1,7)"
        self.assertIsNotNone(S.point_event_grain_problem(QUESTION,sql))
        self.assertIsNotNone(S.point_event_grain_problem(QUESTION,MONTH.replace('substr(u.end_date,1,7)',"strftime('%Y-%m',u.end_date)")))

    def test_request_grammar_is_event_based_across_domains(self):
        for event in('discharge','admission','appointment','transaction','invoice','delivery'):
            for wording in('at each ','as of every ','at the time of each '):
                with self.subTest(event=event,wording=wording):self.assertIsNotNone(S.point_event_grain_problem('Use coverage '+wording+event,MONTH))
        for q in('Report membership at each clinic.','Compare yearly discharge counts by payer.'):
            self.assertIsNone(S.point_event_grain_problem(q,MONTH))
        self.assertIsNone(S.point_event_grain_problem('Report monthly payer exposure; record temperature at each visit.',MONTH))

    def test_correlated_exact_interval_can_prove_the_same_monthly_attribute(self):
        proven=MONTH+" JOIN enrollment_span e ON e.patient_id=u.patient_id AND e.coverage_start<=u.end_date AND(e.coverage_end IS NULL OR e.coverage_end>=u.end_date) AND m.payer_id=e.payer_id"
        self.assertIsNone(S.point_event_grain_problem(QUESTION,proven))
        for change in('different_patient','different_date','unrelated_attribute','nullable_left','or_branch'):
            sql=proven
            if change=='different_patient':sql=sql.replace('e.patient_id=u.patient_id','e.patient_id=m.payer_id')
            elif change=='different_date':sql=sql.replace('e.coverage_start<=u.end_date','e.coverage_start<=u.event_date')
            elif change=='unrelated_attribute':sql=sql.replace('m.payer_id=e.payer_id','m.product=e.product')
            elif change=='nullable_left':sql=sql.replace(' JOIN enrollment_span',' LEFT JOIN enrollment_span')
            else:sql=sql.replace('e.coverage_start<=u.end_date','(e.coverage_start<=u.end_date OR 1=1)')
            with self.subTest(change=change):self.assertIsNotNone(S.point_event_grain_problem(QUESTION,sql))

    def test_an_unused_span_query_or_text_literal_is_no_waiver(self):
        self.assertIsNotNone(S.point_event_grain_problem(QUESTION,'WITH unused AS(SELECT *FROM enrollment_span)'+MONTH))
        self.assertIsNone(S.point_event_grain_problem(QUESTION,"SELECT 'member_month.month=substr(u.end_date,1,7)'AS source_description"))

    def test_final_review_blocks_only_actual_dependencies_before_model_votes(self):
        def call(view,sql):return {'name':'query_sql','input':{'sql':sql},'result':{'result_view':view,'columns':['payer_id'],'rows':[['received']],'truncated':False,'executed_query':sql}}
        calls=[call('bad',MONTH),call('good',EXACT)]
        tools=SimpleNamespace(read={})
        for root in('bad','good'):
            answer={'status':'answered','answer_type':'table','headline':'Received assignments.','table_from_tool':root}
            with patch.object(B,'check_support_packets',return_value={'label':'supported','decided_by':'fixture','steps':[]}),patch('clinical_core.agere.clinical.system_one_gate.ready',return_value=False):
                review=B.review_final(SimpleNamespace(),QUESTION,answer,copy.deepcopy(calls),tools,ledger=None,as_of='2030-05-27')
            if root=='bad':
                self.assertEqual(review['status'],'repair');self.assertEqual(review['calls'],0)
                self.assertIn('month-truncated',review['feedback'])
            else:self.assertNotIn('month-truncated',review.get('feedback')or'')


if __name__=='__main__':unittest.main()
