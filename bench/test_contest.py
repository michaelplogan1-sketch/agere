"""Contest harness tests: fake transports and a fake scorer only (no paid calls, no real warehouses, no answer keys)."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from bench import contest, contest_report
from bench.contest import Budget

QUESTIONS = [
    {'id': 'CLN-01', 'domain': 'clinical_care', 'question': 'How many have diabetes?', 'answer_type': 'count',
     'definition': 'E10/E11 documented.', 'recoverability': 'recoverable'},
    {'id': 'CLN-02', 'domain': 'clinical_care', 'question': 'How many undiagnosed?', 'answer_type': 'count',
     'definition': 'Labs, no code.', 'recoverability': 'recoverable'},
    {'id': 'FIN-01', 'domain': 'finance', 'question': 'Total paid?', 'answer_type': 'value',
     'definition': 'Sum of paid.', 'recoverability': 'recoverable'},
    {'id': 'FIN-09', 'domain': 'finance', 'question': 'Next year cost?', 'answer_type': 'value',
     'definition': 'Not in data.', 'recoverability': 'unsupported'},
]


def final(answer, usage=(100, 10)):
    return {'content': [{'type': 'text', 'text': json.dumps(answer)}], 'stop_reason': 'end_turn',
            'usage': {'input_tokens': usage[0], 'output_tokens': usage[1]}}


ANSWER = {'status': 'answered', 'answer_type': 'count', 'count': 5, 'value': 5, 'patient_ids': None, 'table': None,
          'unknown_count': 0, 'missing': [], 'grounding': ['fake']}


class FakeWarehouse:
    def __init__(self):
        self.queries = []

    def query(self, sql, **kw):
        self.queries.append(sql)
        return {'columns': ['n'], 'rows': [{'n': 1}], 'truncated': False}

    def close(self):
        pass


class Transport:
    """LLM fake: optionally asks for SQL until tool_choice is 'none'; records every request body."""

    def __init__(self, sql_forever=False, sleep=0):
        self.bodies, self.sql_forever, self.sleep = [], sql_forever, sleep

    def __call__(self, url, body, headers=None, **kw):
        assert 'api.anthropic.com' in url
        assert set(headers) == {'x-api-key', 'anthropic-version'}  # never anything else (no user identity)
        self.bodies.append(body)
        if self.sleep:
            time.sleep(self.sleep)
        if self.sql_forever and body['tool_choice']['type'] != 'none':
            return {'content': [{'type': 'tool_use', 'id': f't{len(self.bodies)}', 'name': 'query_sql',
                                 'input': {'sql': 'SELECT 1 AS n'}}], 'usage': {'input_tokens': 10, 'output_tokens': 5}}
        return final(ANSWER)


def agere_transport(url, body):
    assert url.endswith('/api/stack/compose') and set(body) == {'question'}
    return {'answer': ANSWER, 'new_paid_api_calls': 0}


# correctness pattern per contestant: which question ids the fake scorer marks correct
PATTERN = {'agere': {'CLN-01', 'CLN-02', 'FIN-01', 'FIN-09'}, 'llm_raw': {'FIN-09'},
           'llm_tuva': {'CLN-01', 'FIN-09'}, 'llm_tuva_frontier': {'CLN-01', 'CLN-02', 'FIN-09'}}


def fake_scorer(args, answers_path, per_question):
    contestant = Path(answers_path).parents[2].name
    rows = [json.loads(l) for l in Path(answers_path).read_text().splitlines()]
    per = [{'id': r['id'], 'correct': r['id'] in PATTERN[contestant], 'answered': r['status'] == 'answered',
            'within_10pct': False} for r in rows]
    out = {'questions': len(rows), 'all': {'n': len(rows)}}
    if per_question:
        out['per_question'] = per
    return 0, json.dumps(out), ''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.eco = root / 'eco'
        rel = self.eco / 'releases/rel-test'
        (rel / 'questions').mkdir(parents=True)
        (self.eco / 'ecosystem').mkdir()
        for m in contest.SCORER_MODULES:
            (self.eco / m).write_text(f'# fake {m}\n')
        (rel / 'manifest.json').write_text(json.dumps({'as_of': '2030-05-27'}))
        self.qfile = rel / 'questions/questions.jsonl'
        self.write_questions(QUESTIONS)
        self.files = {}
        for name in ('raw.sqlite3', 'tuva.duckdb', 'manifest.json'):
            self.files[name] = root / name
            self.files[name].write_bytes(name.encode() * 10)
        self.round = root / 'round'

    def tearDown(self):
        self.tmp.cleanup()

    def write_questions(self, qs):
        self.qfile.write_text(''.join(json.dumps(q) + '\n' for q in qs))

    def args(self, *contestants, extra=()):
        argv = ['--round', str(self.round), '--contestant', *contestants, '--release', 'rel-test',
                '--ecosystem', str(self.eco), '--sample-release', '', '--repeat', '2',
                '--raw-db', str(self.files['raw.sqlite3']), '--tuva-db', str(self.files['tuva.duckdb']),
                '--tuva-raw-db', str(self.files['raw.sqlite3']), '--tuva-manifest', str(self.files['manifest.json']),
                '--jobs', '2', *extra]
        a = contest.resolve(contest.parser().parse_args(argv))
        a.sample_release = None
        return a

    def run_contest(self, *contestants, transport=None, extra=(), warehouses=None):
        warehouses = warehouses if warehouses is not None else []

        def factory(cfg, budget):
            w = FakeWarehouse()
            warehouses.append((cfg['contestant'], w))
            return w
        llm = transport or Transport()

        def route(url, body, headers=None, **kw):
            return agere_transport(url, body) if headers is None else llm(url, body, headers, **kw)
        with mock.patch('bench.tuva.tuva_context', return_value='FAKE DOCS'), \
                mock.patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'fake-test-key'}):
            return contest.run(self.args(*contestants, extra=extra), transport=route, scorer=fake_scorer,
                               warehouse_factory=factory, hash_cache={})


class BudgetTests(Base):
    def test_every_contestant_gets_the_same_budget_and_enforces_it(self):
        llm, warehouses = Transport(sql_forever=True), []
        self.run_contest('agere', 'llm_raw', 'llm_tuva', 'llm_tuva_frontier', transport=llm, warehouses=warehouses,
                         extra=('--max-sql-calls', '3', '--max-tokens', '2048', '--modes', 'defined', '--repeat', '1'))
        rows = [json.loads(l) for p in self.round.glob('*/defined/attempt-1/rows.jsonl') for l in p.read_text().splitlines()]
        self.assertEqual({r['contestant'] for r in rows}, set(contest.CONTESTANTS))
        budgets = {json.dumps(r['budget'], sort_keys=True) for r in rows}
        self.assertEqual(len(budgets), 1)
        self.assertEqual(json.loads(budgets.pop()), {'max_sql_calls': 3, 'sql_timeout_s': 180.0, 'wall_clock_s': 900.0,
                                                     'max_tokens': 2048})
        # The enforced cap: each LLM question executed exactly max_sql_calls queries, and every request used max_tokens.
        per_question = {}
        for name, w in warehouses:
            per_question.setdefault(name, []).append(len(w.queries))
        self.assertEqual(set(per_question['llm_raw']), {3})
        tuva_total = sum(len(w.queries) for n, w in warehouses if n == 'llm_tuva')
        self.assertEqual(tuva_total, 3 * len(QUESTIONS))
        self.assertEqual({b['max_tokens'] for b in llm.bodies}, {2048})
        for b in llm.bodies:
            text = json.dumps(b['system'])
            self.assertIn('at most 3 ', text)
        models = {r['contestant']: r['model'] for r in rows}
        self.assertEqual(models['llm_raw'], 'claude-haiku-5-5')
        self.assertEqual(models['llm_tuva'], 'claude-haiku-5-5')
        self.assertEqual(models['llm_tuva_frontier'], 'claude-opus-5-5')
        for r in rows:
            self.assertEqual(len(r['question_prompt_sha256']), 64)
            if r['contestant'] != 'agere':
                self.assertEqual(len(r['system_prompt_sha256']), 1)
                self.assertLessEqual(r['sql_calls'], 3)

    def test_budget_has_no_per_contestant_override(self):
        a = self.args('llm_raw', 'llm_tuva')
        b = Budget(a.max_sql_calls, a.sql_timeout, a.wall_clock, a.max_tokens)
        cfgs = [contest.contestant_config(c, a, b) for c in contest.CONTESTANTS]
        self.assertEqual(len({json.dumps(c['budget'], sort_keys=True) for c in cfgs}), 1)
        with self.assertRaises(ValueError):
            Budget(max_sql_calls=0)

    def test_wall_clock_limit_discards_late_answer(self):
        self.run_contest('llm_raw', transport=Transport(sleep=0.3),
                         extra=('--wall-clock', '0.2', '--modes', 'plain', '--repeat', '1', '--ids', 'CLN-01'))
        row = json.loads((self.round / 'llm_raw/plain/attempt-1/rows.jsonl').read_text())
        self.assertIsNone(row['answer'])
        self.assertEqual(row['contract']['status'], 'abstain')
        self.assertIn('wall_clock', row['error'])

    def test_agere_gets_wall_clock_as_http_timeout(self):
        a = self.args('agere', extra=('--wall-clock', '42'))
        cfg = contest.contestant_config('agere', a, Budget(wall_clock_s=42))
        c = contest.Contestant(cfg)
        with mock.patch('bench.adapters.post_json', return_value={'answer': ANSWER, 'new_paid_api_calls': 0}) as post:
            c.transport = contest.adapters.post_json
            with mock.patch('bench.contest.adapters.agere', wraps=contest.adapters.agere) as ag:
                c.answer({'id': 'x', 'question': 'q', 'as_of': '2030-05-27'})
        self.assertEqual(ag.call_args.kwargs['timeout'], 42)
        self.assertEqual(post.call_args.args[1], {'question': 'q'})


class StampTests(Base):
    def test_stamp_records_hashes_before_running(self):
        self.run_contest('llm_raw', extra=('--modes', 'plain', '--repeat', '1'))
        stamp = json.loads((self.round / 'llm_raw/plain/attempt-1/stamp.json').read_text())
        sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
        self.assertEqual(stamp['frozen']['questions_file']['sha256'], sha(self.qfile))
        for m in contest.SCORER_MODULES:
            self.assertEqual(stamp['frozen']['scorer'][m], sha(self.eco / m))
        self.assertEqual({w['path']: w['sha256'] for w in stamp['warehouses']},
                         {str(self.files['raw.sqlite3']): sha(self.files['raw.sqlite3'])})
        self.assertEqual(stamp['contestant_config_sha256'], contest.sha256_json(stamp['contestant_config']))
        self.assertEqual(stamp['contestant_config']['model'], 'claude-haiku-5-5')
        self.assertTrue((self.round / 'round.json').exists())

    def test_warehouse_manifest_is_hashed_instead_of_file(self):
        db = self.files['tuva.duckdb']
        man = db.with_name(db.name + '.manifest.json')
        man.write_text('{"tables": 3}')
        s = contest.warehouse_stamp(db)
        self.assertEqual((s['hashed'], s['sha256']), ('manifest', hashlib.sha256(man.read_bytes()).hexdigest()))

    def test_round_is_frozen_against_question_and_warehouse_changes(self):
        self.run_contest('llm_raw', extra=('--modes', 'plain', '--repeat', '1'))
        self.write_questions(QUESTIONS[:3])
        with self.assertRaises(SystemExit) as e:
            self.run_contest('llm_tuva', extra=('--modes', 'plain', '--repeat', '1'))
        self.assertIn('questions_file', str(e.exception))
        self.write_questions(QUESTIONS)
        self.files['raw.sqlite3'].write_bytes(b'changed')
        with self.assertRaises(SystemExit) as e:
            self.run_contest('llm_tuva', extra=('--modes', 'plain', '--repeat', '1'))
        self.assertIn('warehouse', str(e.exception))
        self.assertFalse((self.round / 'llm_tuva/plain').exists())

    def test_hash_cache_reuses_digest_for_unchanged_file(self):
        cache = {}
        first = contest.sha256_file(self.files['raw.sqlite3'], cache)
        with mock.patch('builtins.open', side_effect=AssertionError('rehashed')):
            self.assertEqual(contest.sha256_file(self.files['raw.sqlite3'], cache), first)


class VerificationTests(Base):
    def test_absent_field_is_tolerated_and_logged(self):
        with self.assertLogs('bench.contest', 'WARNING') as logs:
            self.run_contest('llm_raw', extra=('--modes', 'plain', '--repeat', '1'))
        self.assertIn('no verification field', logs.output[0])
        stamp = json.loads((self.round / 'llm_raw/plain/attempt-1/stamp.json').read_text())
        self.assertEqual(stamp['frozen']['verification']['status'], 'absent')

    def test_unverified_question_refused_before_any_call(self):
        self.write_questions([{**q, 'verification_status': 'verified' if i else 'pending'} for i, q in enumerate(QUESTIONS)])
        llm = Transport()
        with self.assertRaises(SystemExit) as e:
            self.run_contest('llm_raw', transport=llm)
        self.assertIn('CLN-01', str(e.exception))
        self.assertEqual(llm.bodies, [])

    def test_partially_flagged_set_refused(self):
        self.write_questions([{**q, 'verification_status': 'verified'} for q in QUESTIONS[:2]] + QUESTIONS[2:])
        with self.assertRaises(SystemExit):
            self.run_contest('llm_raw')

    def test_unverified_manifest_refused(self):
        (self.qfile.with_name('manifest.json')).write_text(json.dumps({'version': 'v3', 'verification_status': 'draft'}))
        with self.assertRaises(SystemExit) as e:
            self.run_contest('agere')
        self.assertIn('draft', str(e.exception))

    def test_release_manifest_section_and_verified_questions_pass(self):
        rm = self.qfile.parents[1] / 'manifest.json'
        rm.write_text(json.dumps({'as_of': '2030-05-27', 'question_set': {'verification': {'status': 'verified'}}}))
        self.write_questions([{**q, 'verified': True} for q in QUESTIONS])
        self.run_contest('llm_raw', extra=('--modes', 'plain', '--repeat', '1'))
        stamp = json.loads((self.round / 'llm_raw/plain/attempt-1/stamp.json').read_text())
        self.assertEqual(stamp['frozen']['verification']['status'], 'verified')


class RepeatTests(Base):
    def test_attempts_and_modes_are_saved_separately(self):
        self.run_contest('llm_tuva', extra=('--repeat', '3'))
        for mode in contest.MODES:
            for k in (1, 2, 3):
                d = self.round / f'llm_tuva/{mode}/attempt-{k}'
                rows = [json.loads(l) for l in (d / 'rows.jsonl').read_text().splitlines()]
                self.assertEqual({(r['mode'], r['attempt']) for r in rows}, {(mode, k)})
                per = json.loads((d / 'per_question.json').read_text())['per_question']
                self.assertEqual(set(per[0]), {'id', 'correct', 'answered'})  # near-miss detail is dropped
        plain = json.loads((self.round / 'llm_tuva/plain/attempt-1/rows.jsonl').read_text().splitlines()[0])
        defined = json.loads((self.round / 'llm_tuva/defined/attempt-1/rows.jsonl').read_text().splitlines()[0])
        self.assertNotEqual(plain['question_prompt_sha256'], defined['question_prompt_sha256'])

    def test_existing_attempts_are_skipped(self):
        self.run_contest('llm_raw', extra=('--modes', 'plain', '--repeat', '1'))
        llm = Transport()
        self.run_contest('llm_raw', transport=llm, extra=('--modes', 'plain', '--repeat', '1'))
        self.assertEqual(llm.bodies, [])


class MathTests(unittest.TestCase):
    def test_wilson_known_values(self):
        p, lo, hi = contest_report.wilson(8, 10)
        self.assertAlmostEqual(lo, 0.4901625, places=6)
        self.assertAlmostEqual(hi, 0.9433178, places=6)
        p, lo, hi = contest_report.wilson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.2775328, places=6)
        p, lo, hi = contest_report.wilson(50, 100)
        self.assertAlmostEqual(lo, 0.4038315, places=6)
        self.assertAlmostEqual(hi, 0.5961685, places=6)
        self.assertIsNone(contest_report.wilson(0, 0))

    def test_mcnemar_exact_known_values(self):
        self.assertAlmostEqual(contest_report.mcnemar_exact(1, 6), 0.125)
        self.assertAlmostEqual(contest_report.mcnemar_exact(6, 1), 0.125)
        self.assertAlmostEqual(contest_report.mcnemar_exact(0, 5), 0.0625)
        self.assertAlmostEqual(contest_report.mcnemar_exact(2, 10), 0.0385742, places=6)
        self.assertEqual(contest_report.mcnemar_exact(0, 0), 1.0)
        self.assertEqual(contest_report.mcnemar_exact(5, 5), 1.0)

    def test_quantiles(self):
        v = list(range(1, 11))
        self.assertEqual(contest_report.median(v), 5.5)
        self.assertEqual(contest_report.quantile(v, .9), 9)
        self.assertEqual(contest_report.quantile([3.0], .9), 3.0)


class ReportTests(Base):
    def test_paired_report_from_saved_runs(self):
        self.run_contest('agere', 'llm_raw', 'llm_tuva', 'llm_tuva_frontier', extra=('--repeat', '3'))
        report = contest_report.build(self.round)
        all_agere = report['results']['agere']['plain']['all']
        self.assertEqual((all_agere['questions'], all_agere['attempts'], all_agere['correct']), (4, 3, 12))
        raw = report['results']['llm_raw']['defined']['all']
        self.assertEqual(raw['correct'], 3)  # only the unsupported question, x3 attempts
        self.assertEqual(raw['majority_accuracy'][0], .25)
        fin = report['results']['llm_tuva']['plain']['domain:finance']
        self.assertEqual(fin['questions'], 2)
        mc = report['mcnemar']['plain']['llm_raw']
        self.assertEqual((mc['agere_only_correct'], mc['baseline_only_correct']), (3, 0))
        self.assertAlmostEqual(mc['p_value_exact'], 0.25)
        self.assertEqual(report['abstention']['llm_raw']['plain']['unsupported']['questions'], 1)
        # precision on answered: agere 100% -> correct per dollar computed (no paid spend); raw below floor
        self.assertEqual(report['economics']['agere']['plain']['correct_per_dollar'], 'no paid spend')
        self.assertEqual(report['economics']['llm_raw']['plain']['correct_per_dollar'], 'below floor')
        self.assertTrue(report['checks'][0]['ok'])
        md = contest_report.render(report)
        for s in ('# Contest report', '## Mode: plain', '## Mode: defined', 'McNemar exact', 'below floor',
                  'claude-haiku-5-5', 'claude-opus-5-5', 'Question-set verification: absent', '| finance |'):
            self.assertIn(s, md)
        js = json.loads(json.dumps(contest_report._jsonable(report)))
        self.assertIn('estimate', js['results']['agere']['plain']['all']['accuracy'])
        contest_report.main([str(self.round)])
        self.assertTrue((self.round / 'report.md').exists() and (self.round / 'report.json').exists())

    def test_correct_per_dollar_above_floor(self):
        rows = [{'estimated_cost_usd': .01, 'latency_s': 1.0}] * 4
        e = contest_report.economics(rows, (0.95, 0.9, 0.99))
        self.assertIsNone(e['correct_per_dollar'])  # caller fills with the pooled correct count
        self.assertAlmostEqual(e['cost_per_question_usd'], .01)
        e = contest_report.economics(rows + [{'estimated_cost_usd': None, 'latency_s': 2.0}], (0.95, 0.9, 0.99))
        self.assertFalse(e['cost_complete'])

    def test_report_cost_per_dollar_value(self):
        self.run_contest('llm_tuva_frontier', extra=('--modes', 'plain', '--repeat', '1'))
        # make this contestant precise: only answered-and-correct pairs
        per = self.round / 'llm_tuva_frontier/plain/attempt-1/per_question.json'
        data = json.loads(per.read_text())
        for x in data['per_question']:
            x['correct'] = x['answered'] = True
        per.write_text(json.dumps(data))
        report = contest_report.build(self.round)
        e = report['economics']['llm_tuva_frontier']['plain']
        rows = [json.loads(l) for l in (per.parent / 'rows.jsonl').read_text().splitlines()]
        self.assertAlmostEqual(e['correct_per_dollar'], 4 / sum(r['estimated_cost_usd'] for r in rows))


if __name__ == '__main__':
    unittest.main()
