"""Model-free checks for the takeover rung, the hosted-only paid transport and the hosted Ask endpoint."""
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.tiered import app, models, store, takeover


def _db(path):
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE meta(key TEXT, value TEXT); INSERT INTO meta VALUES('as_of','2030-05-27'),('version','t'),('source_digest','d');
        CREATE TABLE patient(patient_id TEXT, birth_date TEXT); INSERT INTO patient VALUES('P-1','1950-01-01'),('P-2','1960-01-01'),('P-3','1970-01-01');""")
    db.commit(); db.close()


class Takeover(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        _db(Path(self.dir.name) / 's.sqlite3')
        self.env = mock.patch.dict(os.environ, {'AGERE_TIERED_DB': str(Path(self.dir.name) / 's.sqlite3')})
        self.env.start()
        self.schema = mock.patch.object(store, 'TABLES', ('patient',)); self.schema.start()
        self.defs = mock.patch.object(models, 'definitions', lambda question=None: ''); self.defs.start()
        store._as_of.clear()

    def tearDown(self):
        self.defs.stop(); self.schema.stop(); self.env.stop(); self.dir.cleanup(); store._as_of.clear()

    def _run(self, replies):
        replies = iter(replies)
        step = {'model': 'm', 'tokens': 10, 'usd': 0.01, 'seconds': 0.1}
        with mock.patch.object(models, '_claude', lambda m, s, u: (next(replies), dict(step))):
            return takeover.answer('How many patients are there?')

    def test_query_then_answer(self):
        got = self._run(['{"sql": "SELECT COUNT(*) AS patients FROM patient"}', '{"answer": "There are 3 patients.", "table": 1}'])
        self.assertEqual(got['rows'], [[3]]); self.assertEqual(got['unsupported'], []); self.assertEqual(len(got['steps']), 2)

    def test_invented_number_gets_one_correction_then_is_named(self):
        got = self._run(['{"sql": "SELECT COUNT(*) AS patients FROM patient"}', '{"answer": "There are 7 patients."}', '{"answer": "There are 7 patients."}'])
        self.assertEqual(got['unsupported'], ['7'])

    def test_write_is_refused_and_turns_are_bounded(self):
        got = self._run(['{"sql": "DELETE FROM patient"}'] * takeover.MAX_STEPS)
        self.assertIsNone(got)
        self.assertEqual(sqlite3.connect(os.environ['AGERE_TIERED_DB']).execute('SELECT COUNT(*) FROM patient').fetchone()[0], 3)

    def test_junk_reply_does_not_end_the_run(self):
        got = self._run(['not json', '[1, 2]', '{"sql": "SELECT COUNT(*) AS patients FROM patient"}', '{"answer": "3 patients."}'])
        self.assertEqual(got['text'], '3 patients.')


class PaidTransport(unittest.TestCase):
    def test_refused_off_the_hosted_server(self):
        with mock.patch.dict(os.environ, {'AGERE_CLAUDE_TRANSPORT': 'api'}):
            os.environ.pop('AGERE_HOSTED', None)
            with self.assertRaises(RuntimeError):
                models._claude('claude-haiku-5-5', 'system', 'user')

    def test_hosted_call_reads_text_and_usage(self):
        usage = types.SimpleNamespace(input_tokens=100, output_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        message = types.SimpleNamespace(stop_reason='end_turn', usage=usage, content=[types.SimpleNamespace(type='thinking', thinking=''), types.SimpleNamespace(type='text', text='{"a": 1}')])
        sent = {}
        class Client:
            def __init__(self, **kw): self.messages = self
            def create(self, **kw): sent.update(kw); return message
        fake = types.SimpleNamespace(Anthropic=Client, RateLimitError=type('R', (Exception,), {}), APIStatusError=type('S', (Exception,), {}), APIConnectionError=type('C', (Exception,), {}))
        with mock.patch.dict(sys.modules, {'anthropic': fake}), mock.patch.dict(os.environ, {'AGERE_CLAUDE_TRANSPORT': 'api', 'AGERE_HOSTED': '1'}), mock.patch.object(models, '_client', None):
            text, step = models._claude('claude-haiku-5-5', 'system', 'user')
        self.assertEqual(text, '{"a": 1}'); self.assertEqual(step['tokens'], 120); self.assertAlmostEqual(step['usd'], (100 * 0.1 + 20 * 0.5) / 1e6)
        self.assertEqual(sent['model'], 'claude-haiku-5-5'); self.assertEqual(sent['messages'], [{'role': 'user', 'content': 'user'}])


class Parts(unittest.TestCase):
    def test_a_part_never_takes_over(self):
        outcome = {'status': 'unanswered', 'steps': [], 'escalations': [], 'seconds': 0.1, 'missing': 'x'}
        with mock.patch.dict(os.environ, {'AGERE_TIERED_CORE': 'on', 'AGERE_TIERED_TAKEOVER': 'on', 'AGERE_TIERED_AUTO_LEARN': 'off'}), \
                mock.patch.object(app.router, 'answer', lambda *a, **k: dict(outcome)), mock.patch.object(app.ledger_store, 'Ledger'), \
                mock.patch.object(app.bottle_store, 'Bottles'), mock.patch.object(takeover, 'answer') as called:
            self.assertIsNone(app._single(None, 'part question', takeover_ok=False))
            called.assert_not_called()


class HostedAsk(unittest.TestCase):
    def test_limits_and_shapes(self):
        from clinical_core.agere.hosted import ask
        ctx = mock.Mock(); ctx.auth.allow.return_value = True
        with mock.patch.object(ask, 'available', lambda: False):
            self.assertEqual(ask.answer(ctx, 'o', 1, {'question': 'How many?'})[0], 503)
        with mock.patch.object(ask, 'available', lambda: True):
            self.assertEqual(ask.answer(ctx, 'o', 1, {'question': ''})[0], 400)
            ctx.auth.allow.return_value = False
            self.assertEqual(ask.answer(ctx, 'o', 1, {'question': 'How many ED visits?'})[0], 429)
            ctx.auth.allow.return_value = True
            result = {'answer': {'headline': 'events: 5.', 'table': [{'events': 5}], 'facts': {}, 'limitations': [], 'verification': 'checked'},
                      'tiered_core': {'final_tier': 'recipes', 'seconds': 0.1}, 'planner_receipt': {'known_api_estimate_usd': 0.0}, 'new_paid_api_calls': 0}
            with mock.patch.object(app, 'try_answer', lambda s, q: result):
                code, out = ask.answer(ctx, 'o', 1, {'question': 'How many ED visits?'})
            self.assertEqual((code, out['answered'], out['rows'], out['tier']), (200, True, [[5]], 'recipes'))
            ctx.auth.record_usage.assert_called(); ctx.auth.audit.assert_called()
            with mock.patch.object(app, 'try_answer', lambda s, q: None):
                code, out = ask.answer(ctx, 'o', 1, {'question': 'Something unanswerable?', 'conversation_id': 'fresh'})
            self.assertEqual((code, out['answered']), (200, False))


GOT = {'text': '44 patients.', 'columns': ['n'], 'rows': [[44]], 'sql': 'SELECT 44 AS n', 'queries': ['SELECT 44 AS n'], 'documents': [], 'caveats': [],
       'unsupported': [], 'steps': [{'model': 'm', 'tokens': 1, 'usd': 0.01, 'seconds': 1.0, 'tier': 'takeover'}], 'usd': 0.01, 'tokens': 1, 'seconds': 1.0, 'model': 'm'}
ON = {'AGERE_TIERED_CORE': 'on', 'AGERE_TIERED_TAKEOVER': 'on', 'AGERE_TIERED_AUTO_LEARN': 'off'}


class TakeoverInContext(unittest.TestCase):
    def test_follow_up_no_cheap_method_could_read_goes_to_the_frontier_model_with_the_previous_question(self):
        from clinical_core.agere.tiered import followup
        seen = {}
        def fake(question, cohort=None, why=None, may_ask=False, **kw): seen.update(question=question, cohort=cohort, why=why); return dict(GOT)
        with mock.patch.dict(os.environ, ON), mock.patch.object(followup, 'complete_question', lambda *a, **k: (None, {'method': 'rewrite_not_confirmed', 'usd': 0.0})), \
                mock.patch.object(takeover, 'answer', fake), mock.patch.object(app.ledger_store, 'Ledger'), mock.patch.object(app.bottle_store, 'Bottles'), \
                mock.patch.object(app.store, 'portable', lambda sql, cohort=None: sql):
            result, standalone, note = app.try_in_conversation(None, 'which looks worse?', 'How many ED visits were there in 2029?', True)
        self.assertEqual(result['tiered_core']['final_tier'], 'takeover'); self.assertEqual(standalone, 'which looks worse?')
        self.assertIn('How many ED visits were there in 2029?', seen['why']); self.assertIsNone(seen['cohort'])
        self.assertEqual(result['answer']['verification'], 'unverified')

    def test_group_follow_up_that_is_not_confirmed_is_answered_inside_the_group(self):
        from clinical_core.agere.tiered import followup
        seen = {}
        def fake(question, cohort=None, why=None, may_ask=False, **kw): seen.update(cohort=cohort, why=why); return dict(GOT)
        with mock.patch.dict(os.environ, ON), mock.patch.object(followup, 'group_question', lambda *a, **k: (None, {'method': 'rewrite_not_confirmed', 'usd': 0.0})), \
                mock.patch.object(takeover, 'answer', fake), mock.patch.object(app.ledger_store, 'Ledger'), mock.patch.object(app.bottle_store, 'Bottles'), \
                mock.patch.object(app.store, 'portable', lambda sql, cohort=None: sql):
            result = app.try_in_cohort(None, 'how many of those have CKD?', {'patient_ids': ['P-2', 'P-1', 'P-1']}, 'ctx')
        self.assertEqual(seen['cohort'], ['P-1', 'P-2']); self.assertIn('2 selected patients', seen['why'])
        self.assertEqual(result['cohort_transform']['input_patient_ids'], ['P-1', 'P-2'])
        self.assertTrue(result['answer']['limitations'][0].startswith('Computed only over the 2 selected patients'))

    def test_takeover_off_leaves_both_to_the_caller(self):
        from clinical_core.agere.tiered import followup
        with mock.patch.dict(os.environ, {**ON, 'AGERE_TIERED_TAKEOVER': 'off'}), mock.patch.object(followup, 'complete_question', lambda *a, **k: (None, {'method': 'x', 'usd': 0.0})), \
                mock.patch.object(followup, 'group_question', lambda *a, **k: (None, {'method': 'x', 'usd': 0.0})), mock.patch.object(app.bottle_store, 'Bottles'):
            self.assertIsNone(app.try_in_conversation(None, 'and?', 'How many?', True))
            self.assertIsNone(app.try_in_cohort(None, 'those?', {'patient_ids': ['P-1']}, 'ctx'))


class HostedGroups(unittest.TestCase):
    def test_a_list_answer_becomes_the_group_for_those_patients(self):
        from clinical_core.agere.hosted import ask
        ctx = mock.Mock(); ctx.auth.allow.return_value = True
        base = {'tiered_core': {'final_tier': 'recipes', 'seconds': 0.1}, 'planner_receipt': {'known_api_estimate_usd': 0.0}, 'new_paid_api_calls': 0}
        listed = {**base, 'answer': {'headline': '2 rows.', 'table': [{'patient_id': 'P-2'}, {'patient_id': 'P-1'}], 'facts': {}, 'limitations': [], 'verification': 'checked'}}
        counted = {**base, 'answer': {'headline': 'n: 1.', 'table': [{'n': 1}], 'facts': {}, 'limitations': [], 'verification': 'checked'}}
        seen = {}
        def cohort(stack, question, parent, key, scope='result'): seen['ids'] = parent['patient_ids']; return counted
        with mock.patch.object(ask, 'available', lambda: True), mock.patch.object(app, 'try_answer', lambda s, q: listed), mock.patch.object(app, 'try_in_cohort', cohort), \
                mock.patch.object(app, 'try_in_conversation', lambda *a, **k: None):
            ask.answer(ctx, 'org-g', 7, {'question': 'List the patients with heart failure', 'conversation_id': 'g1'})
            code, out = ask.answer(ctx, 'org-g', 7, {'question': 'How many of those patients have CKD?', 'conversation_id': 'g1'})
            self.assertEqual((code, seen['ids'], out['rows']), (200, ['P-1', 'P-2'], [[1]]))
            seen.clear()
            ask.answer(ctx, 'org-other', 7, {'question': 'How many of those patients have CKD?', 'conversation_id': 'g1'})
            self.assertEqual(seen, {})  # another organization never sees the group


class DeepPathSwitch(unittest.TestCase):
    def test_policy_turns_the_unbounded_path_off_and_the_takeover_on(self):
        from clinical_core.agere import model_policy
        env = model_policy.application_runtime({})['environment']
        self.assertEqual((env['AGERE_DEEP_PATH'], env['AGERE_TIERED_TAKEOVER'], env['AGERE_TIERED_CORE']), ('off', 'on', 'on'))

    def test_the_question_entry_checks_the_switch_before_the_old_path(self):
        source = Path('clinical_core/agere/clinical/conversation.py').read_text()
        self.assertIn("os.environ.get('AGERE_DEEP_PATH', 'on') == 'off'", source)
        for old in ('result = stack.compose(question) if planning', "bound = stack.compose(effective_question, population=parent['patient_ids']", 'result = bound if bound else stack.compose(standalone)'):
            at = source.index(old)
            self.assertIn('_bounded_only(planning)', source[at - 200:at])  # each start of the old path is preceded by the switch


if __name__ == '__main__':
    unittest.main()
