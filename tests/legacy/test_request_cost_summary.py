"""Summary must prefer full request usage and preserve unknown accounting."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from clinical_core.agere.clinical import answer_log as A,cost_telemetry as T


class Summary(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)

    def record(self,qid,calls,usd):
        A.record(self.path,'Authored question',{'answer':{'status':'answered'},'new_paid_api_calls':calls,
                 'planning_economics':{'new_api_estimate_usd':usd},'planner_receipt':{'route':'fixture'}},question_id=qid)

    def telemetry(self,qid,calls,usd,known):
        return {'question_id':qid,'question_sha':'authored','pricing_version':'fixture','attempt_ids':[],
                'api_calls':calls,'api_usd':usd,'usd_known':known,'status':'answered','stages':{}}

    def test_full_request_replaces_planner_only_totals_without_double_count(self):
        self.record('q-own',2,.2);T.record(self.path,self.telemetry('q-own',7,.7,True))
        s=A.summary(self.path);self.assertEqual(s['questions'],1);self.assertEqual(s['api_calls'],7);self.assertEqual(s['api_usd'],.7)
        self.assertEqual(s['routes'][0]['api_calls'],7)

    def test_unknown_full_request_does_not_fall_back_to_cheaper_planner_total(self):
        self.record('q-own',0,0);T.record(self.path,self.telemetry('q-own',3,.1,False))
        s=A.summary(self.path);self.assertEqual(s['unknown_usage_questions'],1);self.assertIsNone(s['usd_per_question'])
        self.assertEqual(s['answered_without_api_calls'],0)

    def test_old_rows_remain_visible_and_repeated_question_ids_are_not_a_text_join(self):
        self.record('q-old',1,.1);self.record('q-new',2,.2)
        T.record(self.path,self.telemetry('q-new',5,.5,True))
        s=A.summary(self.path);self.assertEqual(s['api_calls'],6);self.assertAlmostEqual(s['api_usd'],.6)


if __name__=='__main__':unittest.main()
