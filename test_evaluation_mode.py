"""Evaluation mode (X-Agere-Evaluation): a measured question leaves every store and training file unchanged; the receipt says so;
cost is reported for every request; tools/eval_quarantine.py moves what earlier benchmark runs taught the stack, reversibly."""
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from clinical_core.agere.clinical import (answer_log, answer_stability, background_continuation, cascade_router, conversation,  # noqa: E402
                                          cost_telemetry, evaluation, learning, learning_hooks as H, procedural_memory,
                                          request_shapes, shared_planner, terminology, tier_timing, vcache)
from test_learning_hooks import cascade_result  # noqa: E402

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
import eval_quarantine as Q  # noqa: E402

QUESTIONS = ['SECRET-BENCH-QUESTION-ALPHA how many members?', 'SECRET-BENCH-QUESTION-BETA how many claims?']


def snapshot(root):
    """Every file under root: bytes, or for a sqlite store its rows table by table (a checkpoint does not count as a change)."""
    out = {}
    for p in sorted(Path(root).rglob('*')):
        if not p.is_file() or p.name.endswith(('-wal', '-shm', '-journal')):
            continue
        if p.suffix == '.sqlite3':
            db = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
            try:
                out[str(p)] = {t: db.execute(f'SELECT rowid,* FROM "{t}" ORDER BY rowid').fetchall()
                               for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
            finally:
                db.close()
        else:
            out[str(p)] = p.read_bytes()
    return out


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'stack'
        self.path.mkdir()
        (self.path / 'run.json').write_text(json.dumps({'source_digest': 'fixture', 'snapshot': {'as_of': '2030-05-27'}}))
        self.stack = SimpleNamespace(path=self.path, allow_live=False)
        self.training = self.root / 'training'
        self.env = mock.patch.dict(os.environ, {'AGERE_TRAINING_DIR': str(self.training), 'AGERE_EVALUATION': ''})
        self.env.start()
        # a stack that has already learned something: every store exists with at least one row
        learning.record_outcome(self.path, 'seed question', 'seed', 'answered', None, 0, 0.0)
        learning.record_verified(self.path, 'seed question', 'sql', {'query': 'SELECT 1', 'returns': 'count'}, {'count': 1}, 'seed', [], 'seed')
        answer_log.record(self.path, 'seed question', result=cascade_result(), seconds=0.1)
        answer_stability.record(self.path, 'seed question', 'typed_decider', True, {'k': 2, 'distinct': 1, 'pass_k': True, 'majority_share': 1.0})
        with sqlite3.connect(self.path / 'question-plans.sqlite3') as db:
            db.execute('CREATE TABLE shared_requests(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('INSERT INTO shared_requests VALUES (?,?)', ('seed', json.dumps({'question': 'seed question'})))
        (self.path / 'tier-timings.json').write_text('{"bottled_sql": [1.0]}')
        self.training.joinpath('traffic').mkdir(parents=True)
        self.training.joinpath('traffic', 'gaps-seed.jsonl').write_text('{"question":"seed"}\n')

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def every_writer(self):
        """Call every learning write path the compose route reaches."""
        q = QUESTIONS[0]
        H.after_answer(self.stack, q, cascade_result())
        H.after_answer(self.stack, QUESTIONS[1], cascade_result(status='partial'))
        H.after_failure(self.stack, q, ValueError('nobody could read it'))
        H.append('gaps', {'question': q})
        learning.record_verified(self.path, q, 'sql', {'query': 'SELECT 2', 'returns': 'count'}, {}, 'x', [], 'x')
        learning.record_outcome(self.path, q, 'route', 'answered', None, 1, 0.5)
        learning.record_routing(self.path, q, {'decision': 'operator', 'choice': {}, 'chosen_by': 'paid_router'}, True)
        learning.capture_verified(self.path, q, {'route': 'hosted_verified_sql', 'hosted_sql': {'capture': {'plan': {'query': 'SELECT 3'}}}}, {'status': 'answered'})
        learning.quarantine(self.path, q, {'quality_checks': [{'control': c, 'severity': 'warning'} for c in sorted(learning.QUARANTINE_CONTROLS)[:1]]})
        answer_log.record(self.path, q, result=cascade_result(), seconds=0.2)
        cost_telemetry.record(self.path, {'question_id': 'q1', 'question_sha': 'x', 'pricing_version': 'v', 'attempt_ids': [], 'api_calls': 0,
                                          'api_usd': 0.0, 'usd_known': True, 'stages': {}})
        answer_stability.record(self.path, q, 'typed_decider', True, {'k': 2, 'distinct': 1, 'pass_k': True, 'majority_share': 1.0})
        cascade_router.record_trace(q, cascade_result(), True)
        tier_timing.record(self.path, {'bottled_sql': [2.0]})
        vcache.log_reuse(self.path, q, 'entry', 0.9, {'query': 'SELECT 1'})
        vcache.observe(self.path, 'entry', 0.9, True, 'test', q)
        procedural_memory.begin_use(self.path, q, [])
        procedural_memory.outcome(self.path, 'no-such-use', 'failed')
        procedural_memory.correction(self.path, q, 'wrong')
        interp = {'execution_accepted': True, 'interpreter': 'typed_decider'}
        shared_planner.store_request(self.path / 'question-plans.sqlite3', 'k', 'sk', q, {'kind': 'request', 'request': {'source': 's'}}, interp)
        request_shapes.save(self.path, None, {}, {}, 'p')
        terminology.save(self.path, 'metformin', {'kind': 'drug', 'concept': 'c', 'method': 'm'})
        conversation.record(self.path, 'conv1', q, {'standalone': q, 'method': 'standalone', 'edits': [], 'frame': None})
        self.assertEqual(background_continuation.enqueue(SimpleNamespace(
            deferred=[{'tier': 'atomic_system_one'}], stack=self.stack, catalog=None, question=q, population=None, hosted_model=None,
            memory_identity=None, order_used=None, log=[], claude_down=False))['queued'], False)
        with self.assertRaises(PermissionError):
            learning.record_correction(self.path, q, 'a correction')


class IsolationTests(Fixture):
    def test_every_write_path_is_a_no_op_in_evaluation_mode(self):
        before = snapshot(self.root)
        with evaluation.scope('frozen-2026-10-23'):
            self.every_writer()
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(sorted(p.name for p in (self.training / 'traffic').iterdir()), ['gaps-seed.jsonl'])

    def test_the_same_calls_do_write_in_a_normal_run(self):
        before = snapshot(self.root)
        self.every_writer_normal()
        after = snapshot(self.root)
        self.assertNotEqual(after, before)
        changed = {Path(k).name for k in after if after[k] != before.get(k)} | {Path(k).name for k in after if k not in before}
        for expected in ('learning.sqlite3', 'answers.sqlite3', 'question-plans.sqlite3', 'answer-stability.sqlite3', 'tier-timings.json',
                         'vcache.sqlite3', 'terminology-map.sqlite3'):
            self.assertIn(expected, changed)
        self.assertTrue(any(p.name.startswith('labels-traffic') for p in (self.training / 'traffic').iterdir()))

    def every_writer_normal(self):
        q = QUESTIONS[0]
        H.after_answer(self.stack, q, cascade_result())
        H.after_failure(self.stack, q, ValueError('x'))
        answer_log.record(self.path, q, result=cascade_result(), seconds=0.2)
        tier_timing.record(self.path, {'bottled_sql': [2.0]})
        vcache.log_reuse(self.path, q, 'entry', 0.9, {'query': 'SELECT 1'})
        terminology.save(self.path, 'metformin', {'kind': 'drug', 'concept': 'c', 'method': 'm'})
        shared_planner.store_request(self.path / 'question-plans.sqlite3', 'k', 'sk', q, {'kind': 'request', 'request': {'source': 's'}},
                                     {'execution_accepted': True, 'interpreter': 'typed_decider'})

    def test_reads_of_certified_stores_stay_on(self):
        with evaluation.scope():
            hit = learning.lookup(self.path, 'seed question')
        self.assertEqual(hit['plan']['query'], 'SELECT 1')

    def test_after_answer_receipt_says_nothing_was_learned(self):
        with evaluation.scope():
            r = H.after_answer(self.stack, QUESTIONS[0], cascade_result())
        self.assertTrue(r['evaluation'])
        self.assertIsNone(r['saved_pair'])
        self.assertIsNone(r['corpus'])
        self.assertEqual(r['components'], {})

    def test_environment_switch_covers_the_whole_process(self):
        before = snapshot(self.root)
        with mock.patch.dict(os.environ, {'AGERE_EVALUATION': '1'}):
            self.assertTrue(evaluation.active())
            H.after_answer(self.stack, QUESTIONS[0], cascade_result())
            self.assertEqual(evaluation.receipt()['scope'], 'process')
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(evaluation.active())

    def test_header_turns_it_on_and_records_the_snapshot(self):
        h = Message()
        self.assertFalse(evaluation.requested(h))
        with evaluation.request_scope(h) as state:
            self.assertIsNone(state)
            self.assertFalse(evaluation.active())
        h[evaluation.HEADER] = '1'
        h[evaluation.FROZEN_HEADER] = 'learning-2026-10-23a'
        with evaluation.request_scope(h, self.path):
            self.assertTrue(evaluation.active())
            self.assertEqual(evaluation.receipt()['frozen_learning'], 'learning-2026-10-23a')
        self.assertFalse(evaluation.active())
        h.replace_header(evaluation.HEADER, '0')
        self.assertFalse(evaluation.requested(h))

    def test_background_continuation_is_never_started(self):
        with evaluation.scope():
            r = background_continuation.enqueue(SimpleNamespace(deferred=[{'tier': 'atomic_system_one'}], stack=self.stack, question='q'))
        self.assertFalse(r['queued'])
        self.assertIn('evaluation', r['reason'])
        self.assertEqual(background_continuation.depth(), 0)


class AuditTests(Fixture):
    def setUp(self):
        super().setUp()
        evaluation.install_audit()
        self.addCleanup(evaluation.uninstall_audit)

    def test_a_missed_write_path_is_counted_and_reported(self):
        with evaluation.scope('s', self.path):
            db = sqlite3.connect(self.path / 'learning.sqlite3')
            db.execute("INSERT INTO outcomes VALUES ('q','r','s','',0,0,0)")
            db.commit(); db.close()
            sqlite3.connect(self.path / 'model-cache.sqlite3').execute('CREATE TABLE IF NOT EXISTS c(x)').connection.close()
            cache = sqlite3.connect(self.path / 'model-cache.sqlite3')
            cache.execute('INSERT INTO c VALUES (1)'); cache.commit(); cache.close()
            r = evaluation.receipt()
        self.assertEqual(r['write_attempts_by_store'], {'learning.sqlite3': 1})
        self.assertEqual(r['allowed_writes_by_store'], {'model-cache.sqlite3': 1})

    def test_strict_mode_denies_the_write(self):
        with mock.patch.dict(os.environ, {'AGERE_EVALUATION_STRICT': '1'}), evaluation.scope('s', self.path):
            db = sqlite3.connect(self.path / 'learning.sqlite3')
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("INSERT INTO outcomes VALUES ('q','r','s','',0,0,0)")
            db.close()
        n = sqlite3.connect(self.path / 'learning.sqlite3').execute('SELECT count(*) FROM outcomes').fetchone()[0]
        self.assertEqual(n, 1)  # only the seed row

    def test_normal_requests_are_untouched(self):
        db = sqlite3.connect(self.path / 'learning.sqlite3')
        db.execute("INSERT INTO outcomes VALUES ('q','r','s','',0,0,0)")
        db.commit(); db.close()


class OperationalPreviewTests(Fixture):
    def test_private_work_copy_preserves_state_and_followups_without_live_writes(self):
        from clinical_core.agere.clinical import program_work_v1, program_work_current
        with program_work_v1.database(self.path) as db:
            db.execute("INSERT INTO work_proposals VALUES ('existing','{}')")
        before = snapshot(self.path)
        with mock.patch.dict(os.environ, {'AGERE_EVALUATION_SCRATCH': str(self.root / 'scratch')}):
            with evaluation.scope('run-a', self.path):
                with program_work_v1.database(self.path) as db:
                    self.assertEqual(db.execute('SELECT id FROM work_proposals').fetchall(), [('existing',)])
                    db.execute("INSERT INTO work_proposals VALUES ('draft','{}')")
            with evaluation.scope('run-a', self.path):
                with program_work_current.database(self.path) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM work_proposals').fetchone()[0], 2)
            with evaluation.scope('run-b', self.path):
                with program_work_v1.database(self.path) as db:
                    self.assertEqual(db.execute('SELECT id FROM work_proposals').fetchall(), [('existing',)])
        self.assertEqual(snapshot(self.path), before)

    def test_normal_work_path_and_missing_evaluation_store(self):
        self.assertEqual(evaluation.operational_path(self.path, 'program-work.sqlite3'), self.path / 'program-work.sqlite3')
        with mock.patch.dict(os.environ, {'AGERE_EVALUATION_SCRATCH': str(self.root / 'scratch')}):
            with evaluation.scope('new', self.path):
                from clinical_core.agere.clinical import program_work_v1
                with program_work_v1.database(self.path) as db:
                    db.execute("INSERT INTO work_proposals VALUES ('draft','{}')")
        self.assertFalse((self.path / 'program-work.sqlite3').exists())


class ComposeTests(Fixture):
    """handle_compose end to end with a fake stack: the response carries the receipts and an evaluation request writes nothing."""

    def compose(self, body=None, **kw):
        stack = SimpleNamespace(path=self.path, allow_live=False, shared_request_planning=False,
                                compose=lambda q, **k: cascade_result(**kw))
        return conversation.handle_compose(stack, body or {'question': QUESTIONS[0]})

    def test_evaluation_request_writes_nothing_and_says_so(self):
        before = snapshot(self.root)
        with evaluation.scope('snap-1'):
            out = self.compose()
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(out['evaluation']['frozen_learning'], 'snap-1')
        self.assertTrue(out['learning']['evaluation'])
        self.assertEqual(out['answer']['status'], 'answered')

    def test_a_refusal_in_evaluation_mode_writes_nothing_and_carries_receipts(self):
        stack = SimpleNamespace(path=self.path, allow_live=False, shared_request_planning=False,
                                compose=mock.Mock(side_effect=_refusal))
        before = snapshot(self.root)
        with evaluation.scope('snap-2'), self.assertRaises(ValueError) as caught:
            conversation.handle_compose(stack, {'question': QUESTIONS[1]})
        self.assertEqual(snapshot(self.root), before)
        exc = caught.exception
        self.assertEqual(exc.evaluation['frozen_learning'], 'snap-2')
        self.assertEqual(exc.cost_receipt['paid_calls'], 0)
        from clinical_core.agere.service import _failure_receipts
        self.assertEqual(set(_failure_receipts(exc)), {'cost_receipt', 'cost_telemetry', 'evaluation'})

    def test_normal_request_still_learns(self):
        self.compose()
        n = sqlite3.connect(self.path / 'learning.sqlite3').execute('SELECT count(*) FROM outcomes').fetchone()[0]
        self.assertEqual(n, 2)
        self.assertTrue(any(p.name.startswith('labels-traffic') for p in (self.training / 'traffic').iterdir()))

    def test_every_response_has_a_cost_receipt(self):
        out = self.compose(paid=0)
        self.assertEqual(out['cost_receipt']['paid_calls'], 0)
        self.assertEqual(out['cost_receipt']['usd'], None if not out['cost_receipt']['usd_known'] else 0.0)
        self.assertTrue(out['cost_receipt']['usd_known'])


def _refusal(question, **kw):
    exc = ValueError('not understood')
    exc.planner_receipt = {'new_paid_api_calls': 0, 'proposers': [{'proposer': 'local_compiler', 'outcome': 'rejected', 'detail': 'no such table'}]}
    raise exc


class CostReceiptTests(unittest.TestCase):
    def telemetry(self, **kw):
        return {'api_calls': 3, 'api_usd': 0.12, 'usd_known': True, 'claude_calls': 1, 'system_one_calls': 2,
                'calls_by_model': {'jev': 2, 'claude-sonnet': 1}, 'attempts': [{'attribution': 'tagged'}, {'attribution': 'window'}],
                'question_id': 'q-1', **kw}

    def test_ledger_covers_system_one_and_claude(self):
        r = cost_telemetry.receipt(self.telemetry(), {'new_paid_api_calls': 1})
        self.assertEqual((r['paid_calls'], r['usd'], r['usd_known'], r['system_one_calls'], r['claude_calls']), (3, 0.12, True, 2, 1))
        self.assertEqual(r['windowed_attempts'], 1)

    def test_planner_reporting_more_calls_than_the_ledger_makes_usd_unknown(self):
        r = cost_telemetry.receipt(self.telemetry(), {'new_paid_api_calls': 5})
        self.assertFalse(r['ledger_consistent'])
        self.assertIsNone(r['usd'])
        self.assertEqual(r['paid_calls'], 5)

    def test_unpriced_attempt_is_unknown_never_zero(self):
        r = cost_telemetry.receipt(self.telemetry(usd_known=False, api_usd=0.0), {})
        self.assertIsNone(r['usd'])
        self.assertFalse(r['usd_known'])

    def test_no_paid_call_is_a_known_zero(self):
        r = cost_telemetry.receipt({'api_calls': 0, 'api_usd': 0.0, 'usd_known': True, 'attempts': []}, {'new_paid_api_calls': 0})
        self.assertEqual((r['paid_calls'], r['usd_known']), (0, True))

    def test_background_work_started_is_reported(self):
        r = cost_telemetry.receipt(self.telemetry(), {'planner_receipt': {'answer_timing': {'background_continuation': {'queued': True, 'tiers': ['evidence_review']}}}})
        self.assertEqual(r['background']['queued'], True)

    def test_a_failure_with_only_a_planner_receipt(self):
        r = cost_telemetry.receipt({'api_calls': None}, {'new_paid_api_calls': 2}, failed=True)
        self.assertEqual(r['paid_calls'], 2)
        self.assertIsNone(r['usd'])


class QuarantineTests(Fixture):
    T0 = 1_800_000_000.0

    def setUp(self):
        super().setUp()
        db = sqlite3.connect(self.path / 'learning.sqlite3')
        db.execute('DELETE FROM outcomes')
        db.execute('DELETE FROM verified')
        for i, q in enumerate(QUESTIONS * 2):
            db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)', (q, 'r', 's', '', 0, 0.0, self.T0 + i * 10))   # in window (0..39)
        db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)', ('an ordinary question', 'r', 's', '', 0, 0.0, self.T0 + 9999))
        db.execute('INSERT INTO verified(question,plan_kind,plan,signature,route,models,agreement,created,context) VALUES (?,?,?,?,?,?,?,?,?)',
                   (QUESTIONS[0], 'sql', '{}', '{}', 'cascade:x', '[]', 'x', self.T0 + 5, 'c'))
        db.commit(); db.close()
        import hashlib
        a = sqlite3.connect(self.path / 'answers.sqlite3')
        a.execute('DELETE FROM answers')
        a.execute('INSERT INTO answers(at,question_sha,question,route) VALUES (?,?,?,?)',
                  (self.T0 + 3, hashlib.sha256(QUESTIONS[0].encode()).hexdigest(), 'redacted', 'r'))
        a.commit(); a.close()
        with sqlite3.connect(self.path / 'question-plans.sqlite3') as p:
            p.execute('INSERT INTO shared_requests VALUES (?,?)', ('bench', json.dumps({'question': QUESTIONS[0]})))
        t = self.training / 'traffic' / 'labels-traffic-x.jsonl'
        stamp = lambda s: time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(s))  # noqa: E731
        t.write_text(json.dumps({'question': QUESTIONS[0], 'at': stamp(self.T0 + 4)}) + '\n' +
                     json.dumps({'question': 'ordinary', 'at': stamp(self.T0 + 99999)}) + '\n')
        self.args = dict(stack=self.path, since=str(self.T0), until=str(self.T0 + 50), from_manifests=None, grace_seconds=0.0,
                         training_dir=self.training, include_logs=False, write=False, restore=None)

    def go(self, **over):
        lines = []
        rc = Q.run(SimpleNamespace(**{**self.args, **over}), out=lines.append)
        return rc, lines

    def test_dry_run_counts_by_table_and_never_prints_question_text(self):
        before = snapshot(self.root)
        rc, lines = self.go()
        text = '\n'.join(lines)
        self.assertEqual(rc, 0)
        self.assertIn('learning.sqlite3.outcomes: 4 of 5 rows in window', text)
        self.assertIn('learning.sqlite3.verified: 1 of 1 rows in window', text)
        self.assertIn('question-plans.sqlite3.shared_requests: 1 of 2 rows in window', text)
        self.assertIn('traffic/labels-traffic-x.jsonl: 1 of 2 lines in window', text)
        self.assertNotIn('SECRET-BENCH', text)
        self.assertEqual(snapshot(self.root), before)  # dry run touches nothing

    def test_write_moves_rows_with_backups_and_restore_puts_them_back(self):
        before = snapshot(self.root)
        rc, lines = self.go(write=True)
        text = '\n'.join(lines)
        self.assertNotIn('SECRET-BENCH', text)
        live = sqlite3.connect(self.path / 'learning.sqlite3')
        self.assertEqual(live.execute('SELECT count(*) FROM outcomes').fetchone()[0], 1)
        self.assertEqual(live.execute('SELECT count(*) FROM verified').fetchone()[0], 0)
        self.assertEqual(sqlite3.connect(self.path / 'question-plans.sqlite3').execute('SELECT count(*) FROM shared_requests').fetchone()[0], 1)
        self.assertEqual(len((self.training / 'traffic' / 'labels-traffic-x.jsonl').read_text().splitlines()), 1)
        qfile = next(self.path.glob('quarantine-*.sqlite3'))
        moved = sqlite3.connect(qfile)
        self.assertEqual(moved.execute('SELECT count(*) FROM "learning.sqlite3__outcomes"').fetchone()[0], 4)
        backups = next(self.path.glob('quarantine-*-backup'))
        self.assertTrue((backups / 'learning.sqlite3').is_file())
        self.assertTrue((backups / 'traffic' / 'labels-traffic-x.jsonl').is_file())
        # the backup is the original, byte for byte in content
        self.assertEqual(sqlite3.connect(backups / 'learning.sqlite3').execute('SELECT count(*) FROM outcomes').fetchone()[0], 5)
        # a second dry run finds nothing left to move
        _, again = self.go()
        self.assertIn('total rows/lines in window: 0', '\n'.join(again))
        # restore
        _, rlines = self.go(write=False, restore=qfile)
        self.assertIn('restore complete', '\n'.join(rlines))
        after = snapshot(self.root)
        mine = {k: v for k, v in after.items() if 'quarantine-' not in k}
        for k, v in before.items():
            if k.endswith('.jsonl'):
                self.assertEqual(sorted(mine[k].splitlines()), sorted(v.splitlines()))
            else:
                self.assertEqual(mine[k], v, k)
        # restoring twice does not duplicate
        _, again = self.go(write=False, restore=qfile)
        self.assertIn('nothing to restore', '\n'.join(again))

    def test_manifest_windows_read_only_timestamps(self):
        runs = self.root / 'truth'
        for name, created, finished in (('a', '2026-10-20T10:00:00+00:00', '2026-10-20T11:00:00+00:00'), ('b', '2026-10-20T10:30:00+00:00', None)):
            d = runs / name
            d.mkdir(parents=True)
            (d / 'manifest.json').write_text(json.dumps({'created_at': created, **({'finished_at': finished} if finished else {}),
                                                         'pinned': {'questions_file': 'SECRET-BENCH-NEVER-READ'}}))
        (runs / 'b' / 'rows.jsonl').write_text('{}\n')
        found, skipped = Q.manifest_windows(runs, grace=60)
        self.assertEqual((len(found), skipped), (2, 0))
        a = next(w for w in found if w[2] == 'a')
        self.assertEqual(a[1] - a[0], 3600 + 60)
        merged = Q.merge(found)
        self.assertEqual(len(merged), 1)

    def test_a_missing_window_is_refused(self):
        rc, lines = self.go(since=None, until=None)
        self.assertEqual(rc, 2)


if __name__ == '__main__':
    unittest.main()
