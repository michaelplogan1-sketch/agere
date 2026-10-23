"""Benchmark harness isolation: every request is an evaluation request, a refusal keeps its diagnostics and its cost, and a resume
refuses to mix answers from two code states."""
import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bench import adapters, manifest as M, truth_run

SECRET = 'sk-ant-api03-abcdefghijklmnop'


class Server:
    """A throwaway local HTTP server that answers POSTs with a canned (status, body) and remembers the request."""

    def __init__(self, status, body):
        seen = self.seen = []
        outer = (status, body)

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append({'headers': dict(self.headers.items()), 'body': json.loads(self.rfile.read(int(self.headers['Content-Length'])))})
                raw = json.dumps(outer[1]).encode()
                self.send_response(outer[0]); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

            def log_message(self, *a):
                pass
        self.httpd = HTTPServer(('127.0.0.1', 0), H)
        self.url = f'http://127.0.0.1:{self.httpd.server_port}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()


class PostJson(unittest.TestCase):
    def serve(self, status, body):
        s = Server(status, body)
        self.addCleanup(s.close)
        return s

    def test_agere_refusal_keeps_its_structured_body(self):
        s = self.serve(400, {'status': 'not_understood', 'error': f'no tier answered (token {SECRET})', 'api_key': SECRET,
                             'tiers_tried': [{'tier': 'local_compiler', 'outcome': 'rejected', 'detail': 'no such table', 'seconds': 0.2}],
                             'stage_diagnostics': {'failed_stage': 'execution'}, 'new_paid_api_calls': 2,
                             'cost_receipt': {'paid_calls': 2, 'usd': 0.31, 'ledger_consistent': True},
                             'unrelated_big_field': 'x' * 10000})
        with self.assertRaises(adapters.AgereHTTPError) as caught:
            adapters.post_json(s.url + '/api/stack/compose', {'question': 'q'})
        exc = caught.exception
        self.assertEqual(exc.status, 400)
        self.assertTrue(str(exc).startswith('HTTP 400 from 127.0.0.1'))
        self.assertEqual(exc.body['tiers_tried'][0]['tier'], 'local_compiler')
        self.assertEqual(exc.body['stage_diagnostics'], {'failed_stage': 'execution'})
        self.assertEqual(exc.body['cost_receipt']['usd'], 0.31)
        self.assertNotIn('unrelated_big_field', exc.body)
        self.assertNotIn(SECRET, json.dumps(exc.body) + str(exc))
        self.assertNotIn('api_key', exc.body)

    def test_string_error_is_no_longer_dropped(self):
        s = self.serve(502, {'error': 'No tier answered and Claude is unavailable'})
        with self.assertRaises(RuntimeError) as caught:
            adapters.post_json(s.url, {'question': 'q'})
        self.assertIn('No tier answered', str(caught.exception))

    def test_provider_error_format_is_unchanged(self):
        s = self.serve(429, {'error': {'type': 'rate_limit_error', 'message': 'slow down'}})
        with self.assertRaises(RuntimeError) as caught:
            adapters.post_json(s.url, {'question': 'q'})
        self.assertIn('rate_limit_error: slow down', str(caught.exception))

    def test_scrub_keeps_token_counts(self):
        out = adapters.scrub({'tokens': {'input_tokens': 5}, 'access_token': 'abc', 'note': f'Bearer {SECRET}'})
        self.assertEqual(out['tokens'], {'input_tokens': 5})
        self.assertNotIn('access_token', out)
        self.assertNotIn(SECRET, out['note'])


class AgereAdapter(unittest.TestCase):
    def test_every_request_is_an_evaluation_request(self):
        s = Server(200, {'answer': {'status': 'answered', 'count': 3}, 'new_paid_api_calls': 0})
        self.addCleanup(s.close)
        adapters.agere({'question': 'how many?'}, s.url, frozen_learning='learning-2026-10-23a')
        sent = s.seen[0]
        self.assertEqual(sent['headers']['X-Agere-Evaluation'], '1')
        self.assertEqual(sent['headers']['X-Agere-Frozen-Learning'], 'learning-2026-10-23a')
        self.assertEqual(sent['body'], {'question': 'how many?'})

    def test_transport_without_headers_still_works(self):
        out = adapters.agere({'question': 'q'}, transport=lambda url, body: {'answer': {'status': 'answered'}, 'new_paid_api_calls': 0})
        self.assertEqual(out['estimated_cost_usd'], 0.0)

    def test_request_ledger_receipt_is_the_cost_authority(self):
        response = {'answer': {'status': 'answered'}, 'new_paid_api_calls': 1, 'planner_receipt': {'new_paid_api_calls': 1},
                    'cost_receipt': {'paid_calls': 3, 'usd': 0.04, 'ledger_consistent': True, 'usd_known': True}}
        out = adapters.agere({'question': 'q'}, transport=lambda *a: response)
        self.assertEqual((out['new_paid_api_calls'], out['estimated_cost_usd'], out['cost_scope']), (3, 0.04, 'request_ledger'))
        self.assertEqual(M.cost_class(out), 'paid')  # known, where planner-only accounting called it unknown

    def test_unknown_usd_with_paid_calls_stays_unknown(self):
        response = {'answer': {'status': 'answered'}, 'cost_receipt': {'paid_calls': 2, 'usd': None, 'ledger_consistent': True}}
        out = adapters.agere({'question': 'q'}, transport=lambda *a: response)
        self.assertIsNone(out['estimated_cost_usd'])
        self.assertEqual(M.cost_class(out), 'unknown')

    def test_success_diagnostics_hold_tiers_winner_and_cost(self):
        response = {'answer': {'status': 'answered'}, 'cost_receipt': {'paid_calls': 1, 'usd': 0.01, 'ledger_consistent': True},
                    'planner_receipt': {'route': 'cascade', 'interpretation': {
                        'interpreter': 'atomic_system_one', 'proposer': {'name': 'atomic_system_one'},
                        'proposers': [{'proposer': 'local_compiler', 'outcome': 'no proposal', 'seconds': 0.1},
                                      {'proposer': 'atomic_system_one', 'outcome': 'accepted', 'seconds': 3.0}]}},
                    'evaluation': {'frozen_learning': 's1', 'learning_writes': 'off'}, 'api_key': SECRET}
        d = adapters.agere({'question': 'q'}, transport=lambda *a: response)['diagnostics']
        self.assertEqual(d['winning_tier'], 'atomic_system_one')
        self.assertEqual([(t['tier'], t['verdict']) for t in d['tiers_tried']], [('local_compiler', 'no proposal'), ('atomic_system_one', 'accepted')])
        self.assertEqual(d['cost_receipt']['paid_calls'], 1)
        self.assertEqual(d['evaluation']['learning_writes'], 'off')
        self.assertNotIn(SECRET, json.dumps(d))


Q = {'id': 'Q1', 'domain': 'd', 'answer_type': 'count', 'question': 'How many?', 'definition': 'x'}
ARGS = SimpleNamespace(mode='plain', as_of='2030-05-27', system='agere', url='http://x', model='m', frozen_learning=None)


class TruthRunRows(unittest.TestCase):
    def test_refusal_row_keeps_diagnostics_and_a_known_cost(self):
        body = {'status': 'not_understood', 'tiers_tried': [{'tier': 'a', 'outcome': 'rejected'}],
                'cost_receipt': {'paid_calls': 2, 'usd': 0.2, 'ledger_consistent': True}}
        err = adapters.AgereHTTPError('HTTP 400 from h', 400, body)
        with mock.patch.object(truth_run, 'agere', side_effect=err):
            row = truth_run.run_one(Q, ARGS)
        self.assertIn('HTTP 400', row['error'])
        self.assertEqual(row['diagnostics']['tiers_tried'][0]['tier'], 'a')
        self.assertEqual(row['diagnostics']['http_status'], 400)
        self.assertEqual((row['new_paid_api_calls'], row['estimated_cost_usd']), (2, 0.2))
        self.assertEqual(M.cost_class(row), 'paid')
        json.dumps(row, default=str)

    def test_refusal_without_a_receipt_is_still_unknown(self):
        with mock.patch.object(truth_run, 'agere', side_effect=RuntimeError('HTTP 502 from h')):
            row = truth_run.run_one(Q, ARGS)
        self.assertEqual(M.cost_class(row), 'unknown')
        self.assertIsNone(row['diagnostics'])

    def test_success_row_carries_diagnostics(self):
        ok = {'answer': {'status': 'answered', 'count': 1}, 'response': {}, 'estimated_cost_usd': 0.0, 'new_paid_api_calls': 0,
              'diagnostics': {'winning_tier': 'bottled_sql'}}
        with mock.patch.object(truth_run, 'agere', return_value=ok):
            row = truth_run.run_one(Q, ARGS)
        self.assertEqual(row['diagnostics'], {'winning_tier': 'bottled_sql'})

    def test_agere_is_called_with_the_frozen_learning_id(self):
        args = SimpleNamespace(**{**vars(ARGS), 'frozen_learning': 'snap'})
        with mock.patch.object(truth_run, 'agere', return_value={'answer': None}) as call:
            truth_run.run_one(Q, args)
        self.assertEqual(call.call_args.kwargs['frozen_learning'], 'snap')


class ResumeSafety(unittest.TestCase):
    def manifest(self, head='aaa', diff=None, digest='d1', flags=None, mode='plain', jobs=3, system='agere'):
        return {'pinned': {'code': {'head': head, 'diff_sha256': diff}, 'system': system,
                           'model_settings': {'mode': mode, 'jobs': jobs, 'url': 'http://x', 'as_of': '2030'},
                           'extra': {'agere_flags': flags or {}, 'stack_digest': digest}, 'questions_file': {'sha256': 'q'},
                           'scorer': {'files': {}}, 'data': [], 'docs': [], 'cache_policy': 'off', 'pricing': None}}

    def test_same_run_may_resume_and_drive_settings_are_ignored(self):
        old, new = self.manifest(), self.manifest(jobs=8)
        new['pinned']['model_settings']['url'] = 'http://other:9999'
        self.assertEqual(M.resume_conflicts(old, new), [])

    def test_each_difference_is_named(self):
        old = self.manifest()
        self.assertIn('code commit changed', M.resume_conflicts(old, self.manifest(head='bbb'))[0])
        self.assertIn('uncommitted code', M.resume_conflicts(old, self.manifest(diff='x'))[0])
        self.assertIn('stack digest', M.resume_conflicts(old, self.manifest(digest='d2'))[0])
        self.assertIn('AGERE_BACKGROUND_CLAUDE', M.resume_conflicts(old, self.manifest(flags={'AGERE_BACKGROUND_CLAUDE': 'on'}))[0])
        self.assertIn('mode', M.resume_conflicts(old, self.manifest(mode='defined'))[0])

    def test_a_manifest_from_before_run_identity_is_refused(self):
        old = self.manifest()
        del old['pinned']['extra']
        self.assertTrue(any('predates' in p for p in M.resume_conflicts(old, self.manifest())))

    def test_check_resume_messages(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            self.assertIn('no manifest.json', truth_run.check_resume(out, self.manifest()))
            (out / 'manifest.json').write_text(json.dumps(self.manifest()))
            self.assertIsNone(truth_run.check_resume(out, self.manifest()))
            msg = truth_run.check_resume(out, self.manifest(head='bbb', digest='d2'))
            self.assertIn('Refusing to resume', msg)
            self.assertIn('code commit changed', msg)
            self.assertIn('stack digest changed', msg)

    def test_main_exits_before_asking_anything_when_the_manifest_differs(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'run'
            out.mkdir()
            (out / 'manifest.json').write_text(json.dumps(self.manifest(head='old')))
            current = self.manifest(head='new')
            with mock.patch.object(truth_run, 'manifest_for', return_value=current), \
                    mock.patch.object(truth_run, 'ECOSYSTEM', Path(d) / 'eco'), \
                    mock.patch.object(truth_run, 'run_one') as ask:
                (Path(d) / 'eco/releases/r/questions').mkdir(parents=True)
                (Path(d) / 'eco/releases/r/questions/questions.jsonl').write_text(json.dumps(Q) + '\n')
                with self.assertRaises(SystemExit) as stop:
                    truth_run.main(['--release', 'r', '--system', 'agere', '--resume', str(out)])
            self.assertEqual(stop.exception.code, 2)
            ask.assert_not_called()

    def test_stack_digest_is_about_content_not_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            stack = Path(d)
            (stack / 'run.json').write_text('{"a": 1}')
            db = sqlite3.connect(stack / 'learning.sqlite3')
            db.execute('CREATE TABLE verified(q TEXT)'); db.execute("INSERT INTO verified VALUES ('a')"); db.commit(); db.close()
            first = M.stack_digest(stack)
            sqlite3.connect(stack / 'learning.sqlite3').execute('VACUUM').connection.close()  # file bytes change, rows do not
            self.assertEqual(M.stack_digest(stack), first)
            db = sqlite3.connect(stack / 'learning.sqlite3')
            db.execute("INSERT INTO verified VALUES ('b')"); db.commit(); db.close()
            self.assertNotEqual(M.stack_digest(stack), first)
            self.assertIsNone(M.stack_digest(None))

    def test_flags_exclude_secrets_by_name(self):
        flags = M.agere_flags({'AGERE_LEARNED': 'on', 'AGERE_PERPLEXITY_API_KEY': 'x', 'HOME': '/h', 'AGERE_TOKEN': 't'})
        self.assertEqual(flags, {'AGERE_LEARNED': 'on'})

    def test_stamp_finished_adds_the_window_end(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'manifest.json').write_text(json.dumps({'created_at': '2026-10-23T00:00:00+00:00', 'pinned': {}}))
            M.stamp_finished(d)
            self.assertIn('finished_at', json.loads((Path(d) / 'manifest.json').read_text()))


if __name__ == '__main__':
    unittest.main()
