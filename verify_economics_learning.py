"""Local HTTP integration check with an authored, empty warehouse.

The Stack constructor is replaced with a fixture adapter to avoid embeddings and
hosted inference. Real service routes, conversation failure capture, correction
storage, rule checks and lesson retrieval run unchanged. This is not a live
shared-planner/model acceptance test; the integration owner performs that check.
"""
import json
import re
import sqlite3
import subprocess
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from clinical_core.agere import service
from clinical_core.agere.clinical import economics_learning as E, learning, programs, vbc


def main():
    out = Path('artifacts/agere-economics-learning-v1'); out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='agere-economics-http-') as tmp:
        path = Path(tmp)
        (path/'run.json').write_text(json.dumps({'source_digest': 'authored-economics-fixture',
            'cache_key_digest': 'authored-economics-fixture', 'synthetic': True, 'snapshot': {'as_of': '2026-12-31'}}))
        with sqlite3.connect(path/'stack.sqlite3') as db:
            db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT);'
                             'CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,stream TEXT,payload TEXT);'
                             'CREATE TABLE result_cache(id TEXT PRIMARY KEY,payload TEXT);')
        def missing_input(question, **kwargs):
            # Authored missing-input scenario exercises the engine and automatic
            # conversation capture, without pretending to test language inference.
            return vbc.validate({'metric': 'shared_savings', 'terms': programs.contract_terms(
                'MSSP BASIC level E 2026', {'benchmark_pmpm': 100})})
        stack = SimpleNamespace(path=path, allow_live=False, shared_request_planning=True, compose=missing_input,
            retriever=SimpleNamespace(path=path, meta={'source_run': 'authored-economics-fixture'}))
        ready, servers, errors = threading.Event(), [], []
        def bind(address, handler):
            server = ThreadingHTTPServer(address, handler); servers.append(server); ready.set(); return server
        def run():
            try: service.serve(path, 0, stack=path)
            except BaseException as exc: errors.append(exc); ready.set()
        with patch('clinical_core.agere.clinical.stack_runtime.Stack', return_value=stack), patch.object(service, 'ThreadingHTTPServer', side_effect=bind):
            worker = threading.Thread(target=run, daemon=True); worker.start()
            if not ready.wait(30): raise AssertionError('Fixture service did not start')
            if errors: raise errors[0]
            base = 'http://127.0.0.1:' + str(servers[0].server_address[1])
            def call(route, body=None, raw=False):
                request = Request(base+route, data=None if body is None else json.dumps(body).encode(),
                                  headers={'Content-Type': 'application/json'})
                with urlopen(request, timeout=20) as r: return r.read().decode() if raw else json.load(r)
            try:
                question = 'What is our downside risk under MSSP ENHANCED 2026?'
                options = call('/api/stack/economics-learning?'+urlencode({'question': question}))['rule_options']
                rule = next(r for r in options if r['parameter'] == 'loss_rate_min')
                rule = {k: rule[k] for k in ('program', 'parameter', 'value', 'source_digest')}
                first = call('/api/stack/correct', {'question': question, 'note': 'Please recheck the loss floor.', 'rule': rule})
                assert first['rule_verification']['status'] == 'verified_rule'
                assert E.context(path, 'Explain the loss floor for MSSP ENHANCED 2026')[0]['value'] == .4
                assert not E.context(path, 'Explain the loss floor for MSSP ENHANCED 2025')
                wrong = call('/api/stack/correct', {'question': question, 'note': 'Incorrect claim', 'rule': {**rule, 'value': .01}})
                assert wrong['rule_verification']['status'] == 'rejected'
                assert not E.context(path, question)
                assert learning.correction_context(path, 'Explain the minimum loss sharing rate for MSSP ENHANCED 2026')
                call('/api/stack/correct', {'question': question, 'note': 'Rechecked the source value.', 'rule': rule})
                try: call('/api/stack/compose', {'question': 'Shared savings under MSSP BASIC level E 2026 with a $100 benchmark?'})
                except HTTPError as exc:
                    failure = json.load(exc); assert 'participant_revenue' in json.dumps(failure)
                else: raise AssertionError('Missing revenue must not settle')
                summary = call('/api/stack/learning')['healthcare_economics']
                assert summary['questions'] == 1 and summary['verified_rules'] == 1 and summary['inactive_rules'] == 1
                assert summary['gaps'] and not summary['model_weights_trained']
                page = call('/ask', raw=True)
                assert 'Cited program rule' in page and '/api/stack/economics-learning' in page
                scripts = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', page, re.S)
                for i, script in enumerate(scripts):
                    if not script.strip(): continue
                    target = out/f'ask-script-{i}.js'; target.write_text(script)
                    subprocess.run(['node', '--check', str(target)], check=True, capture_output=True)
                report = {'status': 'passed', 'http': ['rule_options', 'correction', 'forged_value_rejected',
                    'later_feedback_suspends_lesson', 'recheck_restores_lesson', 'automatic_failed_question_capture', 'learning_counts', 'ask_ui'],
                    'cross_year_reuse_blocked': True, 'page_scripts_syntax_checked': len(scripts),
                    'new_paid_api_calls': 0, 'api_spend_usd': 0,
                    'fixture_boundary': __doc__, 'learning': summary}
                (out/'http-verification.json').write_text(json.dumps(report, indent=2)+'\n')
                print(json.dumps({k:v for k,v in report.items() if k not in ('learning', 'fixture_boundary')}, indent=2))
            finally:
                servers[0].shutdown(); servers[0].server_close(); worker.join(timeout=5)


if __name__ == '__main__': main()
