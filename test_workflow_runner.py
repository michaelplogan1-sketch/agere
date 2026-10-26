"""Offline preflight, source freeze, cache reuse and stop-on-limit receipts."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import run_automatic_workflow as R
from clinical_core.agere.gateway import BudgetLedger,ProviderUnavailable


class Runner(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        runtime=self.root/'runtime';runtime.mkdir();sqlite3.connect(runtime/'semantic.sqlite3').close()
        request=self.root/'request.json';request.write_text(json.dumps({'question':'Review received records and create a brief','scope':'warehouse'}))
        self.args=SimpleNamespace(runtime=runtime,request=request,ledger=self.root/'cache.sqlite3',output=self.root/'attempt',
                                  expect_source=None,execute=False,rsi=False)
        for target in ('socket.socket.connect','socket.create_connection','subprocess.Popen'):
            p=patch(target,side_effect=AssertionError('No provider or CLI send permitted'))
            p.start();self.addCleanup(p.stop)

    def test_preflight_loads_no_credentials_makes_no_live_stack_and_does_not_create_attempt(self):
        with patch('run_integrated_stack.keys',side_effect=AssertionError('No credentials in preflight')),patch(
                'clinical_core.agere.clinical.stack_runtime.Stack',side_effect=AssertionError('No live stack in preflight')):
            result=R.run(self.args)
        self.assertEqual(result['provider_calls'],0);self.assertEqual(result['status'],'offline_preflight')
        self.assertFalse(self.args.output.exists());self.assertFalse(self.args.ledger.exists())
        self.args.expect_source='changed'
        with self.assertRaisesRegex(ValueError,'Source changed'):R.run(self.args)

    def test_limit_preserves_attempt_and_retained_ledger_without_retry_or_external_fallback(self):
        ledger=BudgetLedger(self.args.ledger);ledger.close()
        self.args.execute=True;self.args.expect_source=R.source_fingerprint()
        stack=SimpleNamespace(path=self.args.runtime,close=Mock())
        limit=ProviderUnavailable('Claude subscription weekly limit',kind='rate_limit')
        with patch.dict(os.environ,{}),patch('run_integrated_stack.keys') as keys,patch(
                'clinical_core.agere.subscription_transport.enabled',return_value=True),patch(
                'clinical_core.agere.subscription_transport.install'),patch(
                'clinical_core.agere.clinical.stack_runtime.Stack',return_value=stack),patch(
                'clinical_core.agere.request_lineage.Execution'),patch(
                'clinical_core.agere.clinical.conversation.handle_compose',side_effect=limit) as compose:
            with self.assertRaises(ProviderUnavailable):R.run(self.args)
            self.assertEqual(os.environ['AGERE_CLAUDE_TRANSPORT'],'subscription')
        compose.assert_called_once();keys.assert_called_once();stack.close.assert_called_once()
        self.assertEqual(json.loads((self.args.output/'failure.json').read_text())['kind'],'rate_limit')
        self.assertTrue((self.args.output/'ledger-after.json').is_file())
        self.assertFalse((self.args.output/'result.json').exists())

    def test_validated_response_in_retained_ledger_reuses_without_credentials_or_transport(self):
        from clinical_core.agere.clinical.context_student import send
        from clinical_core.agere import model_policy
        from clinical_core.contracts import digest
        ledger=BudgetLedger(self.args.ledger);self.addCleanup(ledger.close)
        body={'model':model_policy.HAIKU,'max_tokens':10,'messages':[{'role':'user','content':'ordinary exact request'}]}
        with model_policy.question_path():body['model']=model_policy.claude_model(body['model'],track=True)
        key=digest({'contract':'context-student/1','provider':'claude','body':body})
        ledger.reserve(key,'claude',body['model']);ledger.finish(key,response={'model':body['model'],'content':[]})
        out=self.root/'receipts';out.mkdir();transport=Mock(side_effect=AssertionError('Validated cache must not send'))
        with model_policy.question_path():
            raw,receipt=send(body,'claude',ledger,out,parse=lambda r:r,transport=transport)
        self.assertTrue(receipt['cache_hit']);transport.assert_not_called()
        self.assertEqual(R.ledger_status(self.args.ledger)['calls'],{'validated':1})


if __name__=='__main__':unittest.main()
