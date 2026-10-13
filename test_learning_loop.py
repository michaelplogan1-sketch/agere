"""Learning loop: clustering, regression cases + replay against a fake service, proposal format, dataset promotion
(benchmarks excluded), retrain decision, router recovery from receipts, read-only store and the service summary."""
import json, sqlite3, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

from tools.learning_loop import loop
from bench import regressions

BENCH_Q = 'How many claims were denied and then paid in 2028?'


def make_store(stack, outcomes, verified=(), corrections=()):
    stack.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(stack / 'learning.sqlite3')
    db.executescript("""CREATE TABLE verified(question TEXT, plan_kind TEXT, plan TEXT, signature TEXT, route TEXT, models TEXT,
                        agreement TEXT, created REAL, context TEXT, PRIMARY KEY(question, plan));
                        CREATE TABLE outcomes(question TEXT, route TEXT, status TEXT, reason TEXT, paid_calls INTEGER, usd REAL, created REAL);
                        CREATE TABLE corrections(question TEXT, note TEXT, expected TEXT, created REAL);""")
    db.executemany('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)', outcomes)
    db.executemany('INSERT INTO verified VALUES (?,?,?,?,?,?,?,?,?)', verified)
    db.executemany('INSERT INTO corrections VALUES (?,?,?,?)', corrections)
    db.commit(); db.close()
    (stack / 'run.json').write_text(json.dumps({'snapshot': {'as_of': '2029-08-15'}}))


def sql_pair(q, sql, t=1.0, agreement='primary=second'):
    return (q, 'sql', json.dumps({'query': sql, 'returns': 'count'}), '[]', 'hosted_verified_sql', '["haiku","sonnet"]', agreement, t, 'ctx')


def bench_root(tmp):
    root = Path(tmp) / 'repo'
    (root / 'bench/questions').mkdir(parents=True)
    (root / 'bench/acceptance').mkdir(parents=True)
    (root / 'bench/questions/heldout.json').write_text(json.dumps([{'id': 'H01', 'question': BENCH_Q}]))
    (root / 'bench/acceptance/survival.json').write_text(json.dumps([{'question': 'Time to readmission curve for HF', 'expect_operation': 'survival'}]))
    return root


class Clustering(unittest.TestCase):
    def test_reason_families(self):
        self.assertEqual(loop.reason_family('words not interpreted locally: collection, lab'), ('uninterpreted_words', ['collection', 'lab']))
        self.assertEqual(loop.reason_family('HTTP 400: Your credit balance is too low')[0], 'infrastructure')
        self.assertEqual(loop.reason_family('"or" joins terms that were not combined as either/or')[0], 'or_semantics')
        self.assertEqual(loop.reason_family("unrecognised lab label ['it']"), ('unrecognised_lab_label', ['it']))

    def test_similar_questions_cluster_and_infrastructure_is_one_bucket(self):
        o = []
        for i, q in enumerate(['How many lab results came in without a collection date?', 'Lab rows missing a collection date by source',
                               'Count lab results with no collection date in 2028']):
            o.append((q, 'hosted_verified_sql', 'escalated', 'words not interpreted locally: collection', 2, 0.01, i))
        o.append(('Readmissions within 30 days after discharge', 'operator_router', 'escalated', 'words not interpreted locally: after, discharge', 1, 0.001, 9))
        o += [(f'unrelated question {i}', 'declined', 'declined', 'HTTP 400: Your credit balance is too low', 0, 0, 10 + i) for i in range(3)]
        o.append(('How many diabetics?', 'local_compiler', 'answered', '', 0, 0, 20))  # not a gap
        store = {'outcomes': [dict(zip(('question', 'route', 'status', 'reason', 'paid_calls', 'usd', 'created'), r)) for r in o],
                 'verified': [], 'corrections': []}
        groups = loop.gap_questions(store)
        self.assertNotIn(loop.norm('How many diabetics?'), groups)
        clusters = loop.cluster(groups)
        lab = [c for c in clusters if 'collection' in c['label']]
        self.assertEqual(len(lab), 1); self.assertEqual(lab[0]['distinct_questions'], 3)
        self.assertEqual(lab[0]['caught_by'], {'hosted_verified_sql': 3}); self.assertAlmostEqual(lab[0]['usd'], 0.03)
        infra = [c for c in clusters if c['family'] == 'infrastructure']
        self.assertEqual(len(infra), 1); self.assertFalse(infra[0]['actionable'])
        self.assertTrue(clusters[0]['actionable'])  # actionable clusters sort first


class FakeService:
    def __init__(self, answers):
        answers_ = answers

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                d = answers_.get(body['question'], {'error': 'unknown question'})
                raw = json.dumps(d).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(raw)
        self.server = HTTPServer(('127.0.0.1', 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self): self.server.shutdown(); self.server.server_close()


class RegressionCases(unittest.TestCase):
    def test_cases_from_corrections_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack = Path(tmp) / 'v1/stack'
            make_store(stack, [('How many HF patients are on an ARB?', 'local_compiler', 'answered', '', 0, 0, 1.0),
                               ('What is the race breakdown?', 'hosted_verified_sql', 'escalated', 'x', 1, .01, 1.0),
                               ('Show me ED visits last month', 'local_compiler', 'answered', '', 0, 0, 1.0)],
                       corrections=[('How many HF patients are on an ARB?', 'Entresto is an ARNI, not an ARB', json.dumps({'count': 40}), 2.0),
                                    ('What is the race breakdown?', 'race is not in this warehouse; should decline', 'null', 2.0),
                                    ('Show me ED visits last month', 'wrong month', 'null', 2.0)])
            db = sqlite3.connect(stack / 'conversations.sqlite3')
            db.execute('CREATE TABLE turns(conversation_id TEXT, turn INTEGER, created REAL, question TEXT, resolved_question TEXT, method TEXT, '
                       'edits TEXT, frame TEXT, plan_summary TEXT, answer_summary TEXT, error TEXT)')
            db.execute('INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?)', ('c', 1, 1.0, 'Show me ED visits last month', 'Show me ED visits last month',
                                                                              'standalone', '[]', None, json.dumps({'route': 'local_compiler'}),
                                                                              json.dumps({'status': 'answered', 'headline': '120 ED visits in July'}), None))
            db.commit(); db.close()
            cases = loop.regression_cases(loop.read_store(stack), loop.observed_answers(stack))
            by_q = {c['question']: c for c in cases}
            self.assertEqual(by_q['How many HF patients are on an ARB?']['checks'], {'value': 40.0})
            self.assertEqual(by_q['What is the race breakdown?']['checks'], {'status': 'unsupported'})
            weak = by_q['Show me ED visits last month']
            self.assertTrue(weak['weak']); self.assertEqual(weak['observed']['headline'], '120 ED visits in July')
            out = Path(tmp) / 'cases'
            self.assertEqual(len(loop.write_cases(cases, out)), 3)
            self.assertEqual(loop.write_cases(cases, out), [])  # permanent: never rewritten
            for f in out.glob('*.json'):
                c = json.loads(f.read_text())
                self.assertEqual(set(c) >= {'question', 'expected', 'observed', 'checks', 'note'}, True)

            svc = FakeService({'How many HF patients are on an ARB?': {'answer': {'count': 40, 'status': 'answered'}, 'planner_receipt': {'route': 'local_compiler'}},
                               'What is the race breakdown?': {'answer': {'status': 'answered', 'headline': 'White 60%'}, 'planner_receipt': {'route': 'hosted_verified_sql'}},
                               'Show me ED visits last month': {'answer': {'status': 'answered', 'headline': '120 ED visits in July'}}})
            try:
                result = unittest.TestResult()
                regressions.suite(regressions.load_cases(out), lambda q: regressions.ask(svc.port, q, timeout=10)).run(result)
            finally:
                svc.close()
            self.assertEqual(result.testsRun, 3)
            failed = ' '.join(str(f[1]) for f in result.failures)
            self.assertEqual(len(result.failures), 2, failed)
            self.assertIn("status 'answered' != 'unsupported'", failed)
            self.assertIn('answer unchanged', failed)

    def test_check_value_tolerance_and_route(self):
        case = {'checks': {'value': 100.0, 'route_prefix': 'local'}}
        self.assertEqual(regressions.check(case, {'answer': {'value': 100.05}, 'planner_receipt': {'route': 'local_compiler'}}), [])
        self.assertTrue(regressions.check(case, {'answer': {'value': 101}, 'planner_receipt': {'route': 'hosted_verified_sql'}}))
        self.assertTrue(regressions.check(case, {'error': 'boom'}))


class FakeRouter:
    MODEL = 'haiku'
    SYSTEM = 'route {as_of}'

    @staticmethod
    def menu(): return [{'name': 'survival'}, {'name': 'literal_lookup'}]

    @staticmethod
    def tool(op): return {'name': op, 'schema': 'x'}


def fake_hash(body):
    return 'h' + loop.stable_id(json.dumps(body, sort_keys=True))


class ProposalsAndDatasets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); tmp = self.tmp.name
        self.root = bench_root(tmp)
        self.stack = Path(tmp) / 'agere-test/stack'
        qs = ['How many lab results came in without a collection date?', 'Lab rows missing a collection date, count them',
              'Count lab results with no collection date']
        sql = "SELECT count(*) FROM record WHERE stream = 'labs' AND event_date IS NULL"
        outcomes = [(q, 'hosted_verified_sql', 'escalated', 'words not interpreted locally: collection', 2, .02, i) for i, q in enumerate(qs)]
        outcomes += [(BENCH_Q, 'hosted_verified_sql', 'escalated', 'status cannot be both denied and paid on one record', 2, .02, 5),
                     ('How fast do HF patients come back after discharge?', 'operator_router', 'escalated', 'words not interpreted locally: back, after, discharge', 2, .001, 6),
                     ('How quickly are COPD patients readmitted after discharge?', 'operator_router', 'answered', 'words not interpreted locally: after, discharge', 2, .001, 7),
                     ('Flagged question', 'hosted_verified_sql', 'escalated', 'words not interpreted locally: flagged', 2, .02, 8)]
        verified = [sql_pair(q, sql, i) for i, q in enumerate(qs)]
        verified += [sql_pair(BENCH_Q, "SELECT count(*) FROM claim WHERE denied_then_paid = 1"),
                     sql_pair('Flagged question', 'SELECT 1 FROM claim'), sql_pair('Already bottled question?', 'SELECT 2 FROM claim')]
        make_store(self.stack, outcomes, verified, [('Flagged question', 'wrong', 'null', 9.0)])
        receipts = self.stack / 'provider-receipts'; receipts.mkdir()
        for q in ('How fast do HF patients come back after discharge?', 'How quickly are COPD patients readmitted after discharge?'):
            choose = {'model': 'haiku', 'max_tokens': 1200, 'system': 'route 2029-08-15', 'tools': FakeRouter.menu(),
                      'tool_choice': {'type': 'any', 'disable_parallel_tool_use': True}, 'messages': [{'role': 'user', 'content': q}]}
            fill = {'model': 'haiku', 'max_tokens': 1200, 'system': 'route 2029-08-15', 'tools': [FakeRouter.tool('survival')],
                    'tool_choice': {'type': 'tool', 'name': 'survival', 'disable_parallel_tool_use': True}, 'messages': [{'role': 'user', 'content': q}]}
            (receipts / (fake_hash(choose) + '.json')).write_text(json.dumps({'response': {'content': [{'type': 'tool_use', 'name': 'survival', 'input': {}}]}}))
            (receipts / (fake_hash(fill) + '.json')).write_text(json.dumps({'response': {'content': [
                {'type': 'tool_use', 'name': 'survival', 'input': {'parameters': {'event': 'readmission', 'horizon_days': 30}}}]},
                'receipt': {'model': 'haiku', 'cost_estimate': {'estimated_usd': 0.001}}}))
        self.receipts = receipts
        bottling = Path(tmp) / 'bottling'; bottling.mkdir()
        (bottling / 'answers-v1.jsonl').write_text(json.dumps({'question': 'Already bottled question?', 'route': 'hosted_verified_sql', 'query': 'SELECT 2'}) + '\n')
        (bottling / 'data').mkdir()
        (bottling / 'data/train.jsonl').write_text(json.dumps({'messages': [{'role': 'user', 'content': 'schema\n\nQ: Already bottled question?'}]}) + '\n')
        self.bottling = bottling

    def tearDown(self): self.tmp.cleanup()

    def run_loop(self, **kw):
        router, recovery = loop.recover_router_decisions(
            ['How fast do HF patients come back after discharge?', 'How quickly are COPD patients readmitted after discharge?'],
            '2029-08-15', self.receipts, router=FakeRouter, hasher=fake_hash)
        self.assertEqual(recovery['recovered'], 2)
        return loop.run(self.stack, Path(self.tmp.name) / 'out', root=self.root, router_override=router,
                        existing_bottling=[self.bottling], bottling_dir=Path(self.tmp.name) / 'new-bottling', **kw)

    def test_router_recovery_reads_decision_and_parameters(self):
        router, _ = loop.recover_router_decisions(['How fast do HF patients come back after discharge?'], '2029-08-15', self.receipts,
                                                  router=FakeRouter, hasher=fake_hash)
        rec = router[loop.norm('How fast do HF patients come back after discharge?')]
        self.assertEqual(rec['decision'], 'survival'); self.assertEqual(rec['parameters'], {'event': 'readmission', 'horizon_days': 30})

    def test_proposal_format(self):
        self.run_loop()
        props = json.loads((Path(self.tmp.name) / 'out/grammar-proposals.json').read_text())['proposals']
        sql = [p for p in props if p['target']['kind'] == 'verified_sql_template']
        self.assertEqual(len(sql), 1)
        p = sql[0]
        self.assertEqual(p['status'], 'proposed_for_review'); self.assertFalse(p['auto_applied'])
        self.assertEqual(p['target']['streams'], ['labs']); self.assertEqual(p['target']['tables'], ['record'])
        self.assertIn('collection', p['pattern']['decline_terms']); self.assertTrue(p['pattern']['regex_hint'])
        self.assertEqual(p['support']['questions'], 3); self.assertEqual(len(p['verified_outcomes']), 3)
        self.assertTrue(all('agreement' in v for v in p['verified_outcomes']))
        op = [p for p in props if p['target']['kind'] == 'operator']
        self.assertEqual(len(op), 1); self.assertEqual(op[0]['target']['operator'], 'survival')
        self.assertEqual(op[0]['target']['shared_parameters'], {'event': 'readmission', 'horizon_days': 30})
        for p in props: self.assertNotIn(BENCH_Q, p['examples'])

    def test_datasets_exclude_benchmarks_corrections_and_bottled(self):
        summary = self.run_loop(write_bottling=True)
        out = Path(self.tmp.name) / 'out'
        promoted = [json.loads(l) for l in (out / 'bottling-promoted.jsonl').read_text().splitlines()]
        qs = {p['question'] for p in promoted}
        self.assertEqual(len(qs), 3)
        self.assertNotIn(BENCH_Q, qs); self.assertNotIn('Flagged question', qs); self.assertNotIn('Already bottled question?', qs)
        self.assertTrue(all(p['route'] == 'hosted_verified_sql' and p['query'].startswith('SELECT') for p in promoted))
        self.assertTrue(list((Path(self.tmp.name) / 'new-bottling').glob('promoted-*.jsonl')))
        routing = [json.loads(l) for l in (out / 'routing-dataset.jsonl').read_text().splitlines()]
        labels = {r['question']: r['label'] for r in routing}
        self.assertEqual(labels['How fast do HF patients come back after discharge?'], 'survival')
        self.assertEqual(labels['Lab rows missing a collection date, count them'], 'literal_lookup')
        self.assertNotIn(BENCH_Q, labels); self.assertNotIn('Flagged question', labels)
        self.assertEqual(summary['routing_dataset']['skipped'].get('benchmark'), 1)
        self.assertEqual(summary['regressions']['cases'], 1)
        # second run: dataset stays deduplicated
        again = self.run_loop()
        self.assertEqual(again['routing_dataset']['new_rows'], 0)
        self.assertEqual(len((out / 'routing-dataset.jsonl').read_text().splitlines()), len(routing))

    def test_store_is_read_only_and_summary_served(self):
        before = (self.stack / 'learning.sqlite3').read_bytes()
        self.run_loop()
        self.assertEqual((self.stack / 'learning.sqlite3').read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.stack.iterdir()), ['learning.sqlite3', 'provider-receipts', 'run.json'])
        import os
        from clinical_core.agere.clinical import learning
        os.environ['AGERE_LEARNING_LOOP_DIR'] = str(Path(self.tmp.name) / 'out')
        try:
            s = learning.loop_summary(self.stack)
        finally:
            del os.environ['AGERE_LEARNING_LOOP_DIR']
        self.assertEqual(s['status'], 'ok'); self.assertIn('gaps', s); self.assertIn('retrain', s)
        os.environ['AGERE_LEARNING_LOOP_DIR'] = str(Path(self.tmp.name) / 'nowhere')
        try:
            self.assertEqual(learning.loop_summary(self.stack)['status'], 'not_run')
        finally:
            del os.environ['AGERE_LEARNING_LOOP_DIR']


class Retrain(unittest.TestCase):
    def test_threshold_and_ratio(self):
        self.assertFalse(loop.retrain_decision(10, 234, 800)['recommend_retrain'])
        self.assertTrue(loop.retrain_decision(120, 1000, 900)['recommend_retrain'])
        r = loop.retrain_decision(60, 200, 300)
        self.assertTrue(r['recommend_retrain']); self.assertIn('30%', r['reason']); self.assertTrue(r['next_steps'])
        self.assertFalse(loop.retrain_decision(0, 0, 0)['recommend_retrain'])


class BenchFilterTest(unittest.TestCase):
    def test_exact_and_near_duplicates(self):
        f = loop.BenchFilter([BENCH_Q])
        self.assertTrue(f('how many claims were denied and then paid in 2028'))
        self.assertTrue(f('How many claims were denied then paid in 2028?'))
        self.assertFalse(f('Average days to first payer cash'))

    def test_repo_benchmarks_loaded(self):
        qs = loop.benchmark_questions()
        self.assertGreater(len(qs), 100)
        self.assertIn('How many claims were denied and then paid?', qs)  # smoke probe


if __name__ == '__main__':
    unittest.main()


class RoutingLog(unittest.TestCase):
    def test_router_decisions_are_logged(self):
        import tempfile, sqlite3
        from clinical_core.agere.clinical import learning
        with tempfile.TemporaryDirectory() as d:
            learning.record_routing(d, 'whose kidney function is sliding', {'decision': 'trajectory', 'chosen_by': 'learned_router',
                                    'choice': {'name': 'trajectory', 'input': {'parameters': {'concept': 'egfr'}}}}, executed=True)
            learning.record_routing(d, 'q', {}, executed=False)  # no decision: nothing logged
            rows = sqlite3.connect(f'{d}/learning.sqlite3').execute('SELECT decision, operator, chooser, executed FROM routing').fetchall()
        self.assertEqual(rows, [('trajectory', 'trajectory', 'learned_router', 1)])
