"""Recovery from real provider-failure shape without duplicate work or hidden costs."""
import unittest
import json
import importlib
from unittest.mock import Mock
import test_frontier_conversation_integration as F
from clinical_core.agere.deliverables import workflow as W, answer_context as A

class WorkflowRetry(unittest.TestCase):
    def setUp(self):
        self.f=F.FrontierConversationIntegration();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.prepare(brief='create an HTML brief')
        first=self.f.ask(Mock(side_effect=TimeoutError('authored unavailable-provider case')))
        self.path=self.f.path;self.key=first['action_context']['id'];self.first=first['workflow'][0]
        self.request={'context_id':self.key,'index':0,'attempt':2}

    def test_success_updates_workflow_retains_failure_and_unknown_total(self):
        def sender(body):
            if body['model']==importlib.import_module('clinical_core.agere.deliverables.compose').SMALL_MODEL:
                return {'content':[{'type':'text','text':json.dumps({'coverage':'complete','support':'supported','issues':[]})}]},{'cost_estimate':{'estimated_usd':.01}}
            return self.f.sender(body)
        r=W.retry_deliverable(self.path,self.request,allow_live=True,send=sender);s=r['steps'][0]
        self.assertEqual(s['status'],'ready');self.assertEqual(len(s['attempts']),2)
        self.assertEqual(s['attempts'][0]['deliverable_id'],self.first['deliverable_id'])
        self.assertNotEqual(s['deliverable_id'],self.first['deliverable_id'])
        self.assertFalse(s['costs']['api_cost_known']);self.assertIsNone(s['costs']['total_usd'])
        self.assertEqual(s['costs']['known_api_estimate_usd'],.26)
        self.assertEqual(r['new_paid_api_calls'],2);self.assertEqual(s['new_paid_api_calls'],3)
        self.assertEqual(W.materialize(self.path,self.key)['new_paid_api_calls'],0)
        with self.assertRaisesRegex(ValueError,'Only a failed'):
            W.retry_deliverable(self.path,self.request,allow_live=True,send=Mock(side_effect=AssertionError()))

    def test_stale_attempt_and_unavailable_live_authority_make_no_call(self):
        sender=Mock(side_effect=AssertionError())
        with self.assertRaisesRegex(ValueError,'attempt changed'):
            W.retry_deliverable(self.path,{**self.request,'attempt':3},allow_live=True,send=sender)
        with self.assertRaises(ValueError):
            W.retry_deliverable(self.path,self.request,allow_live=False,send=sender)
        sender.assert_not_called();self.assertEqual(W.read(self.path,self.key)['steps'][0]['status'],'blocked')

    def test_retry_rejects_changed_feedback_and_keeps_attempt_receipt(self):
        from clinical_core.agere.clinical.learning import record_correction
        def sender(body):
            record_correction(self.path,self.f.question,'Recheck the saved population')
            return self.f.sender(body)
        r=W.retry_deliverable(self.path,self.request,allow_live=True,send=sender)
        self.assertEqual(r['steps'][0]['status'],'blocked');self.assertEqual(len(r['steps'][0]['attempts']),2)
        self.assertEqual(r['retry_costs']['known_api_estimate_usd'],.25)
        self.assertTrue(A.load(self.path,self.key)['invalidated'])

if __name__=='__main__': unittest.main()
