"""Coverage fallback cannot become source support or invent unmet requests."""
import json,os,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json


class RequestCoverage(unittest.TestCase):
    def run_review(self,verdict,error=None):
        run={'answer':verdict,'model':'authored','new_paid_api_calls':1,'estimated_cost_usd':None}
        if error:run['error']=error
        with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription','ANTHROPIC_API_KEY':'subscription-transport'}),patch(
            'clinical_core.agere.clinical.legacy_proposers._send',return_value=lambda _:None),patch(
            'clinical_core.agere.clinical.semantic_analyst.tool_loop',return_value=run) as loop:
            result=B.frontier_coverage_review(SimpleNamespace(),'Show dated referrals and attendance.',
                'Complete request context',{'nodes':[{'id':'received'}]},
                {'table':[{'patient_id':'A','attendance':None}],'limitations':['Source attendance unavailable']},
                ledger=None,as_of='2030-05-27')
        return result,uncompact_json(json.loads(loop.call_args.args[0]))

    def test_full_coverage_retains_every_value_but_establishes_no_source_support(self):
        result,payload=self.run_review({'label':'answers_fully','reason':'Requested fields and scoped unknown are present.',
            'unaddressed_request_quotes':[]})
        self.assertEqual(result['status'],'reviewed')
        self.assertEqual(payload['answer']['table'],[{'patient_id':'A','attendance':None}])
        self.assertFalse(result['source_support_established']);self.assertFalse(result['clinical_qualification'])
        self.assertIsNone(result['cost_usd']);self.assertIsNone(result['calibrated_probability'])

    def test_missing_work_requires_an_exact_original_request_quote(self):
        for quote,expected in [('attendance','reviewed'),('unasked medication','unavailable')]:
            result,_=self.run_review({'label':'answers_partially','reason':'Requested work missing.',
                'unaddressed_request_quotes':[quote]})
            self.assertEqual(result['status'],expected)
        result,_=self.run_review({'label':'answers_fully','reason':'Conflicting verdict.',
            'unaddressed_request_quotes':['attendance']})
        self.assertEqual(result['status'],'unavailable')

    def test_actual_subscription_limit_propagates_without_source_dispatch(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        with self.assertRaises(ProviderUnavailable) as caught:
            self.run_review({},'Claude subscription limit reached: rate limit')
        self.assertEqual(caught.exception.kind,'rate_limit')


if __name__=='__main__':unittest.main()
