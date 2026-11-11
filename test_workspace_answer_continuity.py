"""Authenticated saved evidence, tenant isolation and honest usage on authored sources."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.hosted.auth import AuthStore
from clinical_core.agere.hosted.secrets import SecretBox
from clinical_core.agere.hosted.server import make_server
from clinical_core.agere.onboarding import workspace_stack
from test_hosted_server import Client
from test_workspace_stack import native_workspace


class WorkspaceAnswerContinuityTests(unittest.TestCase):
    def test_authenticated_answer_save_reopen_and_isolation(self):
        from serve_hosted import configure_models
        from clinical_core.agere import subscription_transport
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            root = Path(os.environ.get('AGERE_WORKSPACE_PROOF_ROOT') or tmp)
            root.mkdir(parents=True, exist_ok=True)
            os.environ.update(AGERE_CLAUDE_TRANSPORT='subscription',
                              AGERE_LEDGER_PATH=str(root / 'provider-budget.sqlite3'),
                              AGERE_SYSTEM_ONE_DISTILL=str(root / 'observations.sqlite3'),
                              AGERE_SYSTEM_ONE_STUDENTS=str(root / 'students'),
                              AGERE_COMPONENT_RELEASE_ROOT=str(root / 'components'))
            configure_models()
            server = make_server('127.0.0.1', 0, root / 'data', secret_key=SecretBox.generate_key(), cookie_secure=False)
            auth = server.ctx.auth
            password = 'authored fixture password only'
            for slug in ('alpha', 'beta'):
                org = auth.create_org(slug)
                user = auth.create_user(f'owner@{slug}.test', password)
                auth.add_member(org, user, 'owner')
            viewer = auth.create_user('viewer@alpha.test', password)
            auth.add_member('alpha', viewer, 'viewer')
            ws = native_workspace(server.ctx.tenant_root('alpha'), 'demo', n=30)
            native_workspace(server.ctx.tenant_root('beta'), 'demo', n=12)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_address[1]}'
            report = {'schema': 'agere-workspace-continuity/1', 'source': 'authored compact synthetic fixture',
                      'clinical_qualification': False, 'production_deployment': False}

            def save():
                (root / 'workflow-receipt.json').write_text(json.dumps(report, indent=2, default=str) + '\n')

            try:
                clients = {}
                for email in ('owner@alpha.test', 'owner@beta.test', 'viewer@alpha.test'):
                    c = Client(base)
                    self.assertEqual(c.login(email, password)[0], 200)
                    clients[email] = c
                alpha, beta, viewer = [clients[k] for k in clients]
                with patch.object(subscription_transport, 'respond', side_effect=AssertionError('Unexpected provider call')) as provider:
                    code, _, answer = alpha.call('POST', '/api/workspaces/demo/ask', {'question': 'How many patients have diabetes?'})
                    report.update(ask_http=code, answer=answer)
                    save()
                    self.assertEqual(code, 200, answer)
                    self.assertEqual(answer['answer'], {'count': 10})
                    self.assertEqual(answer['status'], 'answered')
                    self.assertFalse(answer.get('context_error'), answer)
                    ref = answer['action_context']
                    self.assertTrue(ref)
                    key, url = ref['id'], ref['url']
                    code, _, reopened = alpha.call('GET', url)
                    report.update(reopen_http=code, reopened=reopened)
                    save()
                    self.assertEqual(code, 200, reopened)
                    self.assertEqual(reopened['answer'], answer['answer'])
                    self.assertEqual(reopened['cost_usd'], answer['cost_usd'])
                    self.assertEqual(reopened['saved_context']['question'], answer['question'])
                    self.assertFalse(reopened['saved_context']['stale'])
                    self.assertEqual(reopened['reopen_paid_api_calls'], 0)
                    history = alpha.call('GET', '/api/workspaces/demo/questions')[2]['questions']
                    self.assertEqual(history[0]['action_context']['id'], key)
                    self.assertEqual(len(history), 1)
                    followup_url = url + '/followup'
                    code, _, grouped = viewer.call('POST', followup_url, {'question': 'Group them by practice'})
                    report['followup'] = {'http': code, 'result': grouped}
                    save()
                    self.assertEqual(code, 200, grouped)
                    self.assertEqual(grouped['parent_context_id'], key)
                    self.assertEqual(sum(r['value'] for r in grouped['answer']['table']), 10)
                    self.assertEqual(grouped['new_paid_api_calls'], 0)
                    self.assertFalse(grouped.get('context_error'))
                    child_url = grouped['action_context']['url']
                    child = alpha.call('GET', child_url)[2]['saved_context']
                    self.assertEqual(child['original_question'], answer['question'])
                    self.assertEqual(child['patient_ids'], reopened['saved_context']['patient_ids'])
                    self.assertEqual(child['cohort_transform']['parent_context_id'], key)
                    self.assertEqual(beta.call('POST', followup_url, {'question': 'Count them'})[0], 404)
                    self.assertEqual(alpha.call('POST', followup_url, {'question': 'Count them', 'patient_ids': ['invented']})[0], 400)
                    code, _, unsupported = viewer.call('POST', followup_url, {'question': 'What is total revenue for everyone?'})
                    self.assertEqual(code, 200)
                    self.assertEqual(unsupported['status'], 'clarify')
                    self.assertIsNone(unsupported['answer'])
                    report['other_tenant_http'] = beta.call('GET', url)[0]
                    report['signed_out_http'] = Client(base).call('GET', url)[0]
                    report['viewer_reopen_http'] = viewer.call('GET', url)[0]
                    report['viewer_plan_http'] = viewer.call('POST', '/api/workspaces/demo/plan', {'program_kind': 'pharmacy_gdmt'})[0]
                    self.assertEqual(report['other_tenant_http'], 404)
                    self.assertEqual(report['signed_out_http'], 401)
                    self.assertEqual(report['viewer_reopen_http'], 200)
                    self.assertEqual(report['viewer_plan_http'], 403)
                    # Change only this disposable fixture's source identity. Reopen
                    # must retain the old answer and flag it rather than recompute.
                    run_path = ws.dir / workspace_stack.DIRNAME / 'run.json'
                    run = json.loads(run_path.read_text())
                    run['source_digest'] = 'authored-source-version-2'
                    run_path.write_text(json.dumps(run))
                    code, _, stale = alpha.call('GET', url)
                    self.assertEqual(code, 200)
                    self.assertTrue(stale['saved_context']['stale'])
                    self.assertEqual(stale['answer'], answer['answer'])
                    stale_code, _, stale_error = viewer.call('POST', followup_url, {'question': 'Count them'})
                    self.assertEqual(stale_code, 400, stale_error)
                    report['stale_followup_http'] = stale_code
                    report['stale_reopen'] = {'http': code, 'stale': True, 'answer_unchanged': True}
                    report['usage'] = alpha.call('GET', '/api/admin/usage')[2]['usage']
                    self.assertEqual(report['usage']['records'], 3)
                    self.assertEqual(provider.call_count, 0)
                    report.update(provider_calls=provider.call_count, status='passed')
                    save()
            finally:
                save()
                server.shutdown()
                server.server_close()
                auth.close()
                with workspace_stack._GUARD:
                    for key in list(workspace_stack._STACKS):
                        if key[0].startswith(str(root)):
                            workspace_stack._STACKS.pop(key).close()

    def test_unknown_and_legacy_costs_are_not_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AuthStore(Path(tmp) / 'auth.sqlite3')
            try:
                org = store.create_org('usage')
                store.record_usage(org, 'known', 'native', 0, 1)
                store.record_usage(org, 'known', 'paid', 0.25, 2)
                store.record_usage(org, 'unknown', 'incomplete', None, 3)
                # Exact format of an old row, without cost_known.
                store._conn().execute('INSERT INTO usage(org_id,workspace,cost_usd,seconds,at) VALUES (?,?,?,?,?)',
                                      (org['id'], 'legacy', 0, 1, store._now()))
                out = store.usage(org)
                self.assertIsNone(out['cost_usd'])
                self.assertEqual(out['known_cost_usd'], 0.25)
                self.assertEqual(out['unknown_cost_records'], 2)
                self.assertEqual(out['by_workspace']['known']['cost_usd'], 0.25)
                self.assertIsNone(out['by_workspace']['legacy']['cost_usd'])
            finally:
                store.close()

    def test_failed_save_never_replays_analysis(self):
        from clinical_core.agere.onboarding.workspace import Workspace
        from clinical_core.agere.deliverables import answer_context
        with tempfile.TemporaryDirectory() as tmp:
            ws = native_workspace(tmp, n=3)
            out = {'answer': {'count': 3}, 'decision': 'native', 'cost_usd': 0, 'interpretation': 'native',
                   '_full_result': {'answer': {'status': 'answered', 'count': 3}}}
            with patch.object(Workspace, '_ask_full', return_value=out) as full, \
                 patch.object(Workspace, '_ask_hybrid', side_effect=AssertionError('Must not replay')) as fallback, \
                 patch.object(answer_context, 'capture', side_effect=ValueError('source changed')):
                result = ws.ask('How many patients?')
            self.assertEqual(full.call_count, 1)
            self.assertEqual(fallback.call_count, 0)
            self.assertEqual(result['answer'], {'count': 3})
            self.assertTrue(result['context_error'])
            self.assertEqual(len(list((ws.dir / 'receipts').glob('answer-save-failed-*.json.gz'))), 1)

    def test_subscription_startup_rejects_external_transport(self):
        from serve_hosted import configure_models
        with patch.dict(os.environ, {'AGERE_CLAUDE_TRANSPORT': 'api'}):
            with self.assertRaisesRegex(ValueError, 'subscription transport'):
                configure_models()


if __name__ == '__main__':
    unittest.main()
