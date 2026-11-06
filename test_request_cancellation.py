"""An explicit interruption cannot silently turn into a new paid investigation."""
import os,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.gateway import ProviderUnavailable
from clinical_core.agere.clinical import proposers as P,backstop_agent as B

class Cancellation(unittest.TestCase):
    def test_cancelled_actor_saves_work_without_replaying_bottling_validation(self):
        from test_backstop_agent import EntryModelReuse
        from clinical_core.agere.clinical import harness_improvement as H
        native=[{'name':'query_sql','input':{'sql':'SELECT 3 AS n'},'result':{'rows':[[3]],'columns':['n']}}]
        with patch.object(H,'partial_prefix_evidence',return_value={'native':True}):
            result,_,propose=EntryModelReuse().entry_result(run_override={
                'answer':None,'error':'ProviderUnavailable: request_cancelled: stream aborted',
                'tool_calls':native,'estimated_cost_usd':None,'known_cost_usd':0.01})
        propose.assert_not_called()
        self.assertEqual(result['tool_calls'],native)
        self.assertEqual(result['known_cost_usd'],0.01)
        self.assertIsNone(result['estimated_cost_usd'])
        self.assertIsNone(result['recursive_improvement']['candidate'])

    def test_cascade_propagates_direct_and_wrapped_cancellation(self):
        for wrapped in(False,True):
            c=P.Cascade.__new__(P.Cascade);c.claude_down=None;c.claude_models=None;c.log=[]
            error=ProviderUnavailable('request_cancelled: source work retained',kind='cancelled')
            def interrupted(name,limit):
                if wrapped:
                    try:raise error
                    except ProviderUnavailable as exc:raise P.QuestionNotUnderstood('No final value')from exc
                raise error
            c.call_tier=interrupted
            with self.subTest(wrapped=wrapped),self.assertRaises(ProviderUnavailable) as raised:c.attempt('backstop_agent',None)
            self.assertIs(raised.exception,error)
    def test_backstop_returns_its_native_prefix_on_cancellation_instead_of_falling_through(self):
        run={'answer':None,'error':'ProviderUnavailable: request_cancelled: stream aborted',
             'new_paid_api_calls':1,'known_cost_usd':0.01,'estimated_cost_usd':None,
             'investigation':{'tool_calls':[{'name':'query_sql','result':{'rows':[[3]],'columns':['n']}}]}}
        c=P.Cascade.__new__(P.Cascade);c.stack=SimpleNamespace(allow_live=True);c.question='Inspect received source assertions.';c.population=None;c._ledger=SimpleNamespace();c.spend=lambda *a:None
        with patch.dict(os.environ,{'AGERE_BACKSTOP':'on'}),patch.object(P,'claude_available',return_value=True),patch.object(B,'answer',return_value=run) as call:
            with self.assertRaises(ProviderUnavailable) as raised:c.tier_backstop_agent()
        self.assertEqual(call.call_count,1);self.assertEqual(raised.exception.kind,'cancelled')
        self.assertIs(raised.exception.partial_result,run)

if __name__=='__main__':unittest.main()
