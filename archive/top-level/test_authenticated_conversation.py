"""Signed-in conversation lifecycle over compact authored sources; no model qualification."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from clinical_core.agere.hosted.secrets import SecretBox
from clinical_core.agere.hosted.server import make_server
from clinical_core.agere.onboarding import workspace_stack
from test_hosted_server import Client
from test_workspace_stack import native_workspace


class AuthenticatedConversation(unittest.TestCase):
    def test_clarification_scope_history_and_roles(self):
        from serve_hosted import configure_models
        from clinical_core.agere import subscription_transport
        from clinical_core.agere.clinical import request_program
        from clinical_core.agere.deliverables import workflow
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            root = Path(os.environ.get('AGERE_CONVERSATION_PROOF_ROOT') or tmp).resolve()
            root.mkdir(parents=True, exist_ok=True)
            os.environ.update(AGERE_LEDGER_PATH=str(root/'provider-budget.sqlite3'),
                AGERE_SYSTEM_ONE_DISTILL=str(root/'observations.sqlite3'),
                AGERE_SYSTEM_ONE_STUDENTS=str(root/'students'),
                AGERE_COMPONENT_RELEASE_ROOT=str(root/'components'),
                AGERE_CONTRACT_INPUTS=str(root/'contract-inputs'))
            os.environ['AGERE_TRAINING_DIR'] = str(root/'legacy-training-unused')
            configure_models()
            server = make_server('127.0.0.1', 0, root/'data', secret_key=SecretBox.generate_key(), cookie_secure=False)
            auth = server.ctx.auth
            password = 'authored fixture password only'
            for slug in ('alpha', 'beta'):
                org = auth.create_org(slug)
                user = auth.create_user(f'owner@{slug}.test', password)
                auth.add_member(org, user, 'owner')
                native_workspace(server.ctx.tenant_root(slug), 'demo', n=30 if slug == 'alpha' else 12)
            user = auth.create_user('viewer@alpha.test', password)
            auth.add_member('alpha', user, 'viewer')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_address[1]}'
            report = {'source': '30 independently authored fixture patients, 10 recorded with diabetes',
                      'clinical_qualification': False, 'status': 'started', 'steps': {}}

            def call(client, name, method, url, body=None, expected=200):
                code, _, out = client.call(method, url, body)
                report['steps'][name] = {'http': code, 'result': out}
                (root/'workflow-receipt.json').write_text(json.dumps(report, indent=2, default=str)+'\n')
                self.assertEqual(code, expected, out)
                return out

            try:
                clients = [Client(base) for _ in range(3)]
                for client, email in zip(clients, ('owner@alpha.test', 'owner@beta.test', 'viewer@alpha.test')):
                    self.assertEqual(client.login(email, password)[0], 200)
                alpha, beta, viewer = clients
                with patch.object(subscription_transport, 'respond', side_effect=AssertionError('Unexpected provider call')) as provider:
                    # Viewer analysis goes through the same stack without gaining
                    # work creation authority from an inferred request program.
                    with patch.object(workflow, 'fulfill', side_effect=AssertionError('Viewer cannot fulfill work')) as fulfill, \
                         patch.object(request_program, 'materialize', side_effect=AssertionError('Viewer cannot materialize work')) as materialize:
                        answer = call(viewer, 'native', 'POST', '/api/workspaces/demo/ask',
                            {'question': 'How many patients have diabetes?', 'conversation_id': None, 'interaction_mode': 'interactive'})
                        self.assertEqual(answer['answer'], {'count': 10})
                        self.assertTrue(answer['learning'])
                        self.assertIsNone(answer['learning']['corpus'])
                        self.assertFalse((root/'legacy-training-unused').exists())
                        self.assertTrue(answer['cost_receipt'])
                        self.assertFalse(answer.get('context_error'))
                        fulfill.assert_not_called(); materialize.assert_not_called()
                    cid = answer['conversation']['id']
                    grouped = call(viewer, 'followup', 'POST', '/api/workspaces/demo/ask',
                        {'question': 'Group them by practice', 'conversation_id': cid,
                         'answer_context_id': answer['action_context']['id'], 'answer_scope': 'auto', 'interaction_mode': 'interactive'})
                    self.assertEqual(sum(r['value'] for r in grouped['answer']['table']), 10)
                    self.assertEqual(grouped['conversation']['id'], cid)
                    self.assertTrue(grouped['conversation']['follow_up'])
                    child = call(alpha, 'child_evidence', 'GET', grouped['action_context']['url'])['saved_context']
                    parent = call(alpha, 'parent_evidence', 'GET', answer['action_context']['url'])['saved_context']
                    self.assertEqual(child['patient_ids'], parent['patient_ids'])
                    history = call(alpha, 'history', 'GET', f'/api/workspaces/demo/conversations/{cid}')
                    self.assertEqual(len(history['turns']), 2)
                    call(beta, 'other_tenant_history', 'GET', f'/api/workspaces/demo/conversations/{cid}', expected=404)
                    report_url = grouped['action_context']['url']
                    report_request = {'template': 'brief', 'request': 'Create an evidence brief of this saved answer.'}
                    call(viewer, 'viewer_report_denied', 'POST', report_url+'/report', report_request, expected=403)
                    call(beta, 'other_tenant_report_denied', 'POST', report_url+'/report', report_request, expected=404)
                    rendered = call(alpha, 'requested_report', 'POST', report_url+'/report', report_request)
                    self.assertEqual(rendered['status'], 'delivered')
                    self.assertEqual(rendered['new_paid_api_calls'], 0)
                    self.assertEqual(rendered['context_id'], grouped['action_context']['id'])
                    reopened = call(viewer, 'report_reopen', 'GET', report_url+'/deliverables/'+rendered['id'])
                    self.assertEqual(reopened['html'], rendered['html'])
                    self.assertIn('Evidence review brief', reopened['html'])
                    call(beta, 'other_tenant_report_read', 'GET', report_url+'/deliverables/'+rendered['id'], expected=404)
                    call(viewer, 'forged_authority', 'POST', '/api/workspaces/demo/ask',
                         {'question': 'How many patients?', 'allow_workflow_actions': True}, expected=400)
                    pending = call(alpha, 'clarify', 'POST', '/api/workspaces/demo/ask',
                        {'question': 'Show contract performance', 'conversation_id': None, 'interaction_mode': 'interactive'})
                    self.assertEqual(pending['status'], 'clarify')
                    c = pending['clarification']; conv = pending['conversation']['id']
                    self.assertEqual(c['status'], 'pending')
                    self.assertEqual(c['conversation_id'], conv)
                    resumed_body = {'conversation_id': conv, 'clarification_response': {'id': c['id'], 'option_id': 'different_inputs'}}
                    call(beta, 'other_tenant_resume', 'POST', '/api/workspaces/demo/ask', resumed_body, expected=400)
                    call(alpha, 'wrong_conversation_resume', 'POST', '/api/workspaces/demo/ask',
                        {**resumed_body, 'conversation_id': cid}, expected=400)
                    resumed = call(alpha, 'resume', 'POST', '/api/workspaces/demo/ask', resumed_body)
                    self.assertIn(resumed['status'], ('partial', 'unsupported'))
                    self.assertEqual(resumed['conversation']['id'], conv)
                    self.assertTrue(resumed['clarification_history'])
                    self.assertIsNone(resumed['answer'], 'Missing inputs are not a zero-patient finding')
                    self.assertFalse(resumed['action_context']['result_scope_available'])
                    self.assertEqual(resumed['learning']['status'], 'not_captured')
                    replay = call(alpha, 'idempotent_reply', 'POST', '/api/workspaces/demo/ask', resumed_body)
                    self.assertTrue(replay['clarification_replay'])
                    self.assertEqual(replay['cost_usd'], 0)
                    self.assertEqual(replay['new_paid_api_calls'], 0)
                    self.assertTrue(replay['original_cost_receipt'])
                    history = call(alpha, 'resumed_history', 'GET', f'/api/workspaces/demo/conversations/{conv}')
                    self.assertEqual(len(history['turns']), 2, 'A duplicate reply cannot create another analysis turn')
                    # Select the registered scenario explicitly. Missing fixture
                    # financial inputs remain partial; the native engine still runs.
                    selected = call(alpha, 'scenario_clarify', 'POST', '/api/workspaces/demo/ask',
                        {'question': 'Show contract performance', 'conversation_id': None, 'interaction_mode': 'interactive'})
                    choice = next(o for o in selected['clarification']['options'] if o['id'].startswith('demo-'))
                    computed = call(alpha, 'scenario_native_resume', 'POST', '/api/workspaces/demo/ask',
                        {'conversation_id': selected['conversation']['id'], 'clarification_response':
                         {'id': selected['clarification']['id'], 'option_id': choice['id']}})
                    self.assertIn(computed['status'], ('answered', 'partial', 'unsupported'))
                    self.assertIn('contract_performance', computed['operations'])
                    self.assertTrue(computed['clarification_history'])
                    from clinical_core.agere.clinical.stack_runtime import Stack
                    from clinical_core.agere.clinical import learning_hooks
                    from clinical_core.agere.onboarding.workspace import Workspace
                    with patch.object(Stack, 'compose', side_effect=RuntimeError('Authored interrupted analysis')) as analysis, \
                         patch.object(Workspace, '_ask_hybrid', side_effect=AssertionError('Must not replay')) as fallback, \
                         patch.object(learning_hooks, 'append', side_effect=AssertionError('No shared training corpus')) as corpus:
                        call(alpha, 'failed_analysis_preserved', 'POST', '/api/workspaces/demo/ask',
                            {'question': 'How many patients have diabetes?', 'conversation_id': None}, expected=400)
                        self.assertEqual(analysis.call_count, 1)
                        fallback.assert_not_called(); corpus.assert_not_called()
                    run_path = server.ctx.tenant_root('alpha')/'demo'/workspace_stack.DIRNAME/'run.json'
                    run = json.loads(run_path.read_text()); run['source_digest'] = 'authored-source-change'
                    run_path.write_text(json.dumps(run))
                    call(alpha, 'changed_source_followup', 'POST', '/api/workspaces/demo/ask',
                        {'question': 'Count them', 'conversation_id': cid,
                         'answer_context_id': answer['action_context']['id'], 'answer_scope': 'auto'}, expected=400)
                    call(alpha, 'changed_source_report', 'POST', report_url+'/report', report_request, expected=400)
                    self.assertEqual(provider.call_count, 0)
                    report.update(status='passed', provider_calls=provider.call_count)
            finally:
                (root/'workflow-receipt.json').write_text(json.dumps(report, indent=2, default=str)+'\n')
                server.shutdown(); server.server_close(); auth.close()
                with workspace_stack._GUARD:
                    for key in list(workspace_stack._STACKS):
                        if key[0].startswith(str(root)):
                            workspace_stack._STACKS.pop(key).close()


if __name__ == '__main__':
    unittest.main()
