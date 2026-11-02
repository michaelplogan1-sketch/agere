"""Request ownership at real worker boundaries; fixtures make no provider calls."""
import contextvars
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import cost_telemetry as T
from clinical_core.agere.gateway import BudgetLedger, PersistentGateway
from clinical_core.providers import EvidenceTask
from test_cost_telemetry import paid


class QuestionOwnership(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'ledger.sqlite3'
        self.ledger = BudgetLedger(self.path)
        self.addCleanup(self.ledger.close)

    def owned(self, qid):
        probe = T.start(qid, question_id=qid, ledger_path=self.path)
        self.addCleanup(lambda: probe.finish() if probe._token is not None else None)
        return probe

    def test_two_concurrent_questions_through_real_patient_workers(self):
        from clinical_core.agere.clinical import document_review as D, typed_document_review as R
        from clinical_core.agere.clinical import harness_improvement as H
        started = threading.Barrier(2)
        dispatched = threading.Barrier(2)
        written = threading.Barrier(2)
        seen = []

        def read(tools, pid, docs, questions, ledger, **kwargs):
            # These are D.review's actual patient threads, without copied ContextVars.
            self.assertIsNone(T.CURRENT.get())
            seen.append((pid, ledger.path.question_id))
            dispatched.wait(timeout=5)
            paid(ledger, pid, 'jev-1.13.0', '{}', provider='jev')
            written.wait(timeout=5)
            return {'findings': {'field': []}, 'checks': [], 'unresolved': [],
                    'version': R.VERSION, 'coverage': [], 'ranking': {}, 'all_blocks_processed': True}

        def question(qid):
            probe = T.start(qid, question_id=qid, ledger_path=self.path)
            db = sqlite3.connect(':memory:')
            db.executescript('create table patient(patient_id)')
            db.execute('insert into patient values(?)', (qid,))
            stack_path = self.root / qid
            stack_path.mkdir()
            tools = SimpleNamespace(db=db, as_of='2030-05-27',
                stack=SimpleNamespace(path=stack_path, ledger_path=self.path, allow_live=False),
                in_scope=lambda pid: None, continuation_identity=lambda: {'source': 'fixture'},
                _texts=lambda pid: [('note-'+pid, 'clinical_note', 'notes', 'office',
                                    '2030-01-01', 'Authored source for '+pid, '')],
                document_reviews=[], subtool_accounting={'new_paid_api_calls': 0, 'estimated_cost_usd': 0})
            try:
                started.wait(timeout=5)
                result = D.review(tools, [qid], {'field': 'What is documented?'})
                self.assertEqual(result['rows'][0]['patient_id'], qid)
                return probe.finish()
            finally:
                db.close()
                if probe._token is not None: probe.finish()

        with patch.dict(os.environ, {'AGERE_DOCUMENT_REVIEW': 'typed_spans'}), \
             patch.object(R, 'read', side_effect=read), \
             patch.object(H, 'recover_document_observations', return_value=None), \
             patch.object(H, 'recover_document_fields', return_value=None):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(question, ['question-A', 'question-B']))
        self.assertEqual(set(seen), {('question-A', 'question-A'), ('question-B', 'question-B')})
        for qid, tel in zip(['question-A', 'question-B'], results):
            self.assertEqual(tel['attempt_ids'], [qid])
            self.assertEqual(T.receipt(tel)['paid_calls'], 1)
            self.assertTrue(T.receipt(tel)['usd_known'])
            self.assertEqual(T.receipt(tel)['windowed_attempts'], 0)
        ledger_ids = {row[0] for row in self.ledger.db.execute('select id from calls')}
        self.assertEqual({i for r in results for i in r['attempt_ids']}, ledger_ids)
        self.assertEqual(sum(r['api_calls'] for r in results), len(ledger_ids))

    def test_source_verification_workers_open_separate_owned_ledgers(self):
        from clinical_core.agere.clinical import backstop_agent as B, system_one as S
        probe = self.owned('source-question')
        barrier = threading.Barrier(2)
        def decide(question, options, evidence, **kwargs):
            ident = evidence[0]['id']
            ledger, owned = S._open_ledger(kwargs['ledger'])
            self.assertTrue(owned)
            self.assertEqual(ledger.path.question_id, 'source-question')
            try:
                if ident != 'summary': barrier.wait(timeout=5)
                paid(ledger, ident, 'jev-1.13.0', '{}', provider='jev')
            finally: ledger.close()
            return {'label': 'supported', 'decided_by': 'authored_fixture', 'steps': []}
        packets = [{'id': i, 'date': '2030-05-27', 'text': i, 'bytes': len(i)}
                   for i in ['summary', 'patient-A', 'patient-B']]
        with patch.dict(os.environ, {'AGERE_SYSTEM_ONE_WORKERS': '2'}):
            out = B.check_support_packets(packets, decide, as_of='2030-05-27', ledger=self.ledger)
        tel = probe.finish()
        self.assertEqual(out['checked_source_packets'], 3)
        self.assertEqual(set(tel['attempt_ids']), {'summary', 'patient-A', 'patient-B'})
        self.assertEqual(T.receipt(tel)['paid_calls'], 3)
        self.assertTrue(T.receipt(tel)['usd_known'])

    def test_missing_tags_keep_all_attempts_but_neither_question_claims_spend(self):
        probes = [self.owned('A'), self.owned('B')]
        def unowned(key):
            ledger = BudgetLedger(self.path)
            try: paid(ledger, key, 'jev-1.13.0', '{}', provider='jev')
            finally: ledger.close()
        with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(unowned, ['one', 'two']))
        results = [p.finish() for p in reversed(probes)]
        for tel in results:
            r = T.receipt(tel, {'new_paid_api_calls': 1})
            self.assertEqual(set(tel['attempt_ids']), {'one', 'two'})
            self.assertEqual(r['windowed_attempts'], 2)
            self.assertIsNone(r['paid_calls']); self.assertIsNone(r['usd'])
            self.assertFalse(r['usd_known']); self.assertFalse(r['ownership_complete'])
        self.assertEqual(self.ledger.db.execute('select count(*) from calls').fetchone()[0], 2)

    def test_cache_use_is_free_for_second_question_and_does_not_steal_first_owner(self):
        first = self.owned('first')
        second = self.owned('second')
        # First dispatch returns in a thread without context, while second is active.
        path = T.QuestionLedgerPath(self.path, 'first', first.owner)
        def write():
            ledger = BudgetLedger(path)
            try: paid(ledger, 'shared-request', 'jev-1.13.0', '{}', provider='jev')
            finally: ledger.close()
        with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(write).result()
        self.ledger.reserve('shared-request', 'jev', 'jev-1.13.0')
        free = second.finish(); charged = first.finish()
        self.assertEqual(free['api_calls'], 0); self.assertEqual(free['api_usd'], 0)
        self.assertTrue(free['attempts'][0]['cache_hit'])
        self.assertEqual(charged['api_calls'], 1)
        self.assertGreater(charged['api_usd'], 0)
        self.assertEqual(self.ledger.db.execute('select count(*) from calls').fetchone()[0], 1)

    def test_failed_pending_and_explicit_retry_remain_separate_owned_attempts(self):
        probe = self.owned('retry')
        self.ledger.reserve('failed', 'jev', 'jev-1.13.0')
        self.ledger.finish('failed', error='fixture transport failure')
        key = self.ledger.authorize_retry('failed', 'authored fixture explicit retry')['request_hash']
        paid(self.ledger, key, 'jev-1.13.0', '{}', provider='jev')
        self.ledger.reserve('pending', 'jev', 'jev-1.13.0')
        with self.assertRaises(ValueError): self.ledger.reserve('pending', 'jev', 'jev-1.13.0')
        tel = probe.finish()
        self.assertEqual(tel['api_calls'], 3)
        self.assertEqual({a['status'] for a in tel['attempts']}, {'validated', 'failed', 'pending'})
        self.assertFalse(tel['usd_known']); self.assertTrue(tel['ownership_complete'])
        self.assertIsNone(T.receipt(tel)['usd'])

    def test_tag_write_failure_does_not_abort_answer_or_report_known_cost(self):
        probe = self.owned('failure')
        self.ledger.db.execute(T.TAG_DDL)
        self.ledger.db.execute("create trigger reject_tag before insert on call_questions begin select raise(abort,'fixture'); end")
        self.ledger.db.commit()
        paid(self.ledger, 'untagged', 'jev-1.13.0', '{}', provider='jev')
        tel = probe.finish()
        self.assertEqual(tel['attempt_ids'], ['untagged'])
        self.assertEqual(tel['attribution_issues'][0]['reason'], 'tag_write_failed:IntegrityError')
        self.assertIsNone(T.receipt(tel)['usd'])
        self.assertFalse(tel['accounting_complete'])

    def test_conflicting_explicit_and_context_owners_remain_unknown(self):
        first = self.owned('first')
        path = T.bind_ledger_path(self.path)
        second = self.owned('second')
        self.assertIs(T.bind_ledger_path(path), path)
        ledger = BudgetLedger(path)
        try: paid(ledger, 'conflict', 'jev-1.13.0', '{}', provider='jev')
        finally: ledger.close()
        for probe in [second, first]:
            tel = probe.finish()
            self.assertEqual(tel['attribution_issues'][0]['reason'], 'ledger_owner_context_conflict')
            self.assertFalse(T.receipt(tel)['usd_known'])

    def test_two_charge_owners_are_ambiguous_without_changing_ledger(self):
        first = self.owned('first')
        second = self.owned('second')
        paid(self.ledger, 'shared', 'jev-1.13.0', '{}', provider='jev')
        self.ledger.db.execute('insert into call_questions values(?,?,?,?)', ('shared', 'first', 0, 0))
        self.ledger.db.commit()
        for probe in [second, first]:
            tel = probe.finish()
            self.assertEqual(tel['attempts'][0]['attribution'], 'shared_unresolved')
            self.assertIsNone(T.receipt(tel)['usd'])

    def test_context_copy_restores_worker_scope_after_nested_work(self):
        probe = self.owned('parent')
        sentinel = contextvars.ContextVar('ownership-test', default=None)
        token = sentinel.set('source-context')
        def job(i):
            self.assertIsNone(sentinel.get())  # bookkeeping must not copy unrelated policy/state
            self.assertEqual(T.CURRENT.get(), 'parent')
            ledger = BudgetLedger(self.path)
            try: paid(ledger, str(i), 'jev-1.13.0', '{}', provider='jev')
            finally: ledger.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(T.inherit_context(job), range(4)))
            self.assertIsNone(pool.submit(T.CURRENT.get).result())
        sentinel.reset(token)
        self.assertEqual(probe.finish()['api_calls'], 4)

    def test_reuse_tag_cannot_hide_a_new_charge_with_no_owner(self):
        first = self.owned('first')
        second = self.owned('second')
        def unowned():
            ledger = BudgetLedger(self.path)
            try: paid(ledger, 'unowned-charge', 'jev-1.13.0', '{}', provider='jev')
            finally: ledger.close()
        with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(unowned).result()
        self.ledger.reserve('unowned-charge', 'jev', 'jev-1.13.0')
        for probe in [second, first]:
            tel = probe.finish()
            self.assertEqual(tel['attempt_ids'], ['unowned-charge'])
            self.assertEqual(tel['attempts'][0]['attribution'], 'shared_unresolved')
            self.assertFalse(T.receipt(tel)['usd_known'])
            self.assertIsNone(T.receipt(tel)['paid_calls'])

    def test_dispatch_ownership_preserves_model_eval_and_request_identity(self):
        from clinical_core.agere import model_policy as M
        from clinical_core.agere.clinical import evaluation as E, system_one as S
        from clinical_core.providers import JevProvider
        from clinical_core.contracts import digest
        def request():
            model = M.claude_model(M.OPUS)
            task = EvidenceTask('Authored question', '2030-05-27', {'yes':'yes','unknown':'unknown'}, [])
            body = JevProvider(None).request(task)
            return {'claude_model': model, 'body': body, 'key': digest(body), 'evaluating': E.active()}
        with ThreadPoolExecutor(max_workers=2) as pool:
            baseline = pool.submit(request).result()
            probe = self.owned('policy')
            with E.scope():
                wrapped = pool.submit(T.inherit_context(request)).result()
                batch = S._batch([request, request], 2)
            self.assertEqual(wrapped, baseline)
            self.assertEqual(batch, [baseline, baseline])
            self.assertEqual(pool.submit(T.CURRENT.get).result(), None)
        self.assertEqual(probe.finish()['api_calls'], 0)

    def test_path_and_string_inputs_through_attribute_and_factory(self):
        from clinical_core.agere.clinical import together,system_one as S,therapy_barriers as B
        from clinical_core.agere.clinical import context_student as C
        from clinical_core.contracts import digest
        bodies=[]
        def choice(question,options,evidence,**kwargs):
            handle,owned=S._open_ledger(kwargs['ledger'])
            try:
                self.assertEqual(handle.path.question_id, 'paths')
                bodies.append((question,options,evidence,kwargs['as_of']))
            finally:
                if owned:handle.close()
            return {'label':'yes','probabilities':{'yes':.9},'status':'answered'}
        probe=self.owned('paths')
        for path in [self.path,str(self.path)]:
            bound=together._ledger_path(SimpleNamespace(ledger_path=path))
            self.assertEqual(os.fspath(bound),str(self.path))
            self.assertEqual(bound.question_id,'paths')
            with patch.object(S,'choice',side_effect=choice):
                S.attribute('Documented?',{'yes':'yes','unknown':'unknown'},
                    [{'id':'n','text':'Authored source.','date':'2030-01-01'}],'yes',as_of='2030-05-27',ledger=path,workers=2)
            factory=B.ledgered_typed_send_factory(self.root/'receipts',ledger_path=path)
            body={'model':'jev-1.13.0','fixture':str(path)}
            key=digest({'contract':'context-student/1','provider':'jev','body':body})
            paid(self.ledger,key,'jev-1.13.0','{}',provider='jev')
            with factory() as send:
                raw,cost,reused=send('jev',body)
                self.assertTrue(reused);self.assertEqual(cost,0)
        self.assertCountEqual([json.dumps(v,sort_keys=True)for v in bodies[:4]],
                              [json.dumps(v,sort_keys=True)for v in bodies[4:]])
        self.assertTrue(probe.finish()['usd_known'])

    def test_unknown_cost_roundtrips_without_becoming_free_or_teaching_a_cost_gain(self):
        from clinical_core.agere.clinical import answer_log as A
        probe=self.owned('unknown-summary')
        def unowned():
            ledger=BudgetLedger(self.path)
            try:paid(ledger,'lost','jev-1.13.0','{}',provider='jev')
            finally:ledger.close()
        with ThreadPoolExecutor(max_workers=1)as pool:pool.submit(unowned).result()
        tel=probe.finish()
        A.record(self.root,'Authored question',{'answer':{'status':'answered'},'new_paid_api_calls':0,
            'planner_receipt':{'route':'fixture'},'planning_economics':{'new_api_estimate_usd':0}},question_id=probe.question_id)
        self.assertTrue(T.record(self.root,tel))
        self.assertIsNone(T.rows(self.root)[0]['api_calls'])
        summary=A.summary(self.root)
        self.assertEqual(summary['unknown_usage_questions'],1)
        self.assertEqual(summary['answered_without_api_calls'],0)
        self.assertIsNone(summary['usd_per_question'])

    def test_early_gateway_and_sender_cache_hits_retain_question_lineage(self):
        from clinical_core.agere.clinical import context_student as C
        from clinical_core.contracts import digest
        body = {'model': 'jev-1.13.0', 'question': 'authored fixture'}
        task = EvidenceTask('authored fixture', '2030-05-27', {'yes': 'yes', 'unknown': 'unknown'}, [])
        gateway_key = digest({'provider': 'jev', 'body': body, 'contract': 2})
        sender_key = digest({'contract': 'context-student/1', 'provider': 'jev', 'body': body})
        for key in [gateway_key, sender_key]: paid(self.ledger, key, 'jev-1.13.0', '{}', provider='jev')
        before = list(self.ledger.db.execute('select * from calls order by id'))
        probe = self.owned('cached')
        with patch('clinical_core.providers.JevProvider.request', return_value=body), \
             patch.object(PersistentGateway, '_parse', return_value={'label': 'yes'}):
            cached = PersistentGateway(self.ledger).classify('jev', task)
        self.assertTrue(cached['cache_hit'])
        C.send(body, 'jev', self.ledger, self.root / 'receipts', parse=lambda r: r)
        tel = probe.finish()
        self.assertEqual(set(tel['attempt_ids']), {gateway_key, sender_key})
        self.assertTrue(all(a['cache_hit'] for a in tel['attempts']))
        self.assertEqual(tel['api_calls'], 0); self.assertTrue(tel['usd_known'])
        self.assertEqual(before, list(self.ledger.db.execute('select * from calls order by id')))

    def test_answer_guard_dispatches_keep_owner_without_warehouse_fixture(self):
        import time
        from clinical_core.agere.clinical import answer_guard as G
        probe = self.owned('guard')
        def run(*args):
            self.assertEqual(T.CURRENT.get(), 'guard')
            ledger = G._own_ledger(self.ledger)
            try: paid(ledger, 'check', 'jev-1.13.0', '{}', provider='jev')
            finally: ledger.close()
        with patch.object(G.PendingCheck, '_run', side_effect=run):
            pending = G.PendingCheck(SimpleNamespace(allow_live=False), 'fixture', {},
                ledger=self.ledger, profile=None, hosted=None, deadline=time.perf_counter()+5)
            pending.future.result(timeout=5)
        done, value = G._bounded(lambda: T.CURRENT.get(), time.perf_counter()+5)
        self.assertTrue(done); self.assertEqual(value, 'guard')
        self.assertEqual(probe.finish()['attempt_ids'], ['check'])


if __name__ == '__main__': unittest.main()
