"""Generated governed metrics (generated_metrics.py): count / share / by-column metrics derived from a semantic layer's own structure,
offered to System One beside the authored ones. Synthetic fixture semantic DB; System One is a fake (system_one.decide is patched), no
network, no model."""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('AGERE_METRIC_ENCODER', 'off')
os.environ['AGERE_LOCAL_TYPED'] = 'off'
os.environ['AGERE_LOCAL_NORMALIZER'] = 'off'

from clinical_core.agere.clinical import generated_metrics as G, gate_policy, metric_router as MR, metrics, s1_governed as S1G

AS_OF = '2029-08-15'
N = 200


def fixture(root, digest='dig-1', freq7=20):
    """A small semantic layer: payer_claim (entity key, two clocks, a coded column, words, amounts), visit, a table with a sibling label
    column, and tables the generator must skip (no date, too few rows, the record table)."""
    root = Path(root) / 'stack'
    root.mkdir(exist_ok=True)
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.executescript('''CREATE TABLE meta(key TEXT, value TEXT);
        CREATE TABLE record(record_id TEXT, stream TEXT, payload TEXT);
        CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, claim_received_date TEXT, first_available_date TEXT,
            claim_frequency_code TEXT, final_status TEXT, place_of_service TEXT, paid_cents INTEGER, diagnosis_codes TEXT, counts_in_metrics INTEGER);
        CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, event_date TEXT, type_concept TEXT);
        CREATE TABLE lab_result(result_id TEXT, patient_id TEXT, event_date TEXT, status_code TEXT, status_description TEXT);
        CREATE TABLE clinician(npi TEXT, specialty TEXT);
        CREATE TABLE tiny(tiny_id TEXT, event_date TEXT, kind TEXT);''')
    db.executemany('INSERT INTO meta VALUES (?,?)', [('version', 'semantic-layer/test'), ('source_digest', digest), ('as_of', AS_OF)])
    base = date(2028, 1, 1)
    for i in range(N):
        freq = '7' if i < freq7 else '8' if i < freq7 + 10 else '1'
        status = ('paid', 'denied', 'voided')[i % 3]
        service = base + timedelta(days=i)
        received = service + timedelta(days=40)  # most received in 2028, the last ones in 2029
        db.execute('INSERT INTO payer_claim VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (f'C{i}', f'P{i % 80}', service.isoformat(), received.isoformat(), service.isoformat(), freq, status, ('11', '22')[i % 2],
                    1000 + i * 7, f'I{i % 97},J{i % 89}', 1 if i % 5 else 0))
        db.execute('INSERT INTO visit VALUES (?,?,?,?)', (f'V{i}', f'P{i % 80}', (base + timedelta(days=i * 2)).isoformat(), ('primary_care_visit', 'emergency_visit')[i % 4 == 0]))
        db.execute('INSERT INTO lab_result VALUES (?,?,?,?,?)', (f'L{i}', f'P{i % 80}', (base + timedelta(days=i)).isoformat(),
                                                                ('F', 'C')[i % 2], ('Final result', 'Corrected result')[i % 2]))
        db.execute('INSERT INTO clinician VALUES (?,?)', (f'N{i}', ('cardiology', 'family medicine')[i % 2]))
        db.execute('INSERT INTO record VALUES (?,?,?)', (f'R{i}', 'claims', '{}'))
    for i in range(5): db.execute('INSERT INTO tiny VALUES (?,?,?)', (f'T{i}', '2028-02-02', ('a', 'b')[i % 2]))
    db.commit()
    db.close()
    return root


def expected(root, where, year=2028, date_col='service_from', table='payer_claim'):
    db = sqlite3.connect(Path(root) / 'semantic.sqlite3')
    try:
        return db.execute(f"SELECT count(*) FROM {table} WHERE {where} AND substr({date_col},1,10) BETWEEN '{year}-01-01' AND '{year}-12-31'").fetchone()[0]
    finally:
        db.close()


class FakeS1:
    def __init__(self, script, usd=0.0002):
        self.script, self.usd, self.calls = script, usd, []

    def decide(self, question, options, evidence, *, as_of, order=None, threshold=0.8, task_key=None, ledger=None, allow_live=True):
        label, p = self.script(task_key, question, options)
        self.calls.append({'task': task_key, 'options': list(options), 'label': label})
        rest = {k: (1 - p) / max(1, len(options) - 1) for k in options if k != label}
        step = {'provider': 'jev', 'status': 'answered', 'label': label, 'probability': p, 'cost_usd': self.usd, 'cache_hit': False}
        return {'label': label, 'probabilities': {label: p, **rest}, 'calibrated_probabilities': {label: p, **rest}, 'provider': 'jev',
                'cost_usd': self.usd, 'cache_hit': False, 'decided_by': 'jev' if p >= threshold else None, 'steps': [step], 'task_key': task_key}


def choose(fragment, slots=None, p=0.9):
    slots = slots or {}

    def script(task, prompt, options):
        if task == S1G.TASKS['metric']:
            return next((k for k in options if fragment in k), S1G.NONE_OF), p
        name = prompt.split('which value of "', 1)[1].split('"', 1)[0] if 'which value of "' in prompt else 'period'
        return slots.get(name, S1G.NOT_STATED if S1G.NOT_STATED in options else 'all_time'), p
    return script


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = fixture(self.tmp.name)
        G._MEMO.clear()
        patcher = mock.patch.dict(os.environ, {'AGERE_GENERATED_METRICS_DIR': ''})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.report = G.build(self.root)
        self.index = G.load(self.root)

    def metric(self, table, column, value, kind):
        return self.index.metric(G.metric_id(table, column, value, kind))


class Generation(Base):
    def test_categorical_columns_are_found_by_structure_not_by_name(self):
        by_table = {g['table']: {c['column'] for c in g['columns']} for g in self.index.groups}
        self.assertEqual(set(by_table), {'payer_claim', 'visit', 'lab_result'})  # no date / too few rows / the record table are skipped
        self.assertEqual(by_table['payer_claim'], {'claim_frequency_code', 'final_status', 'place_of_service', 'counts_in_metrics'})
        for excluded in ('payer_claim_id', 'patient_id', 'service_from', 'paid_cents', 'diagnosis_codes'):  # key, dates, amounts, free codes
            self.assertNotIn(excluded, by_table['payer_claim'])

    def test_entity_key_and_the_period_clock_are_measured(self):
        claim = next(g for g in self.index.groups if g['table'] == 'payer_claim')
        self.assertEqual(claim['key'], 'payer_claim_id')
        self.assertEqual(claim['date'], 'service_from')  # service before administrative dates
        self.assertEqual([a['column'] for a in claim['alt_dates']], ['claim_received_date'])
        self.assertEqual(claim['alt_dates'][0]['name'], 'received')

    def test_count_share_and_by_metrics_exist_for_every_value(self):
        for kind in ('count', 'share'):
            for v in ('1', '7', '8'):
                self.assertIn(G.metric_id('payer_claim', 'claim_frequency_code', v, kind), self.index.specs)
        self.assertIn(G.metric_id('payer_claim', 'claim_frequency_code', None, 'by'), self.index.specs)
        self.assertEqual(self.report['by_kind']['by'], sum(len(g['columns']) for g in self.index.groups))
        self.assertEqual(self.report['rejected'], 0)

    def test_every_metric_is_a_governed_definition_marked_generated(self):
        count = self.metric('payer_claim', 'claim_frequency_code', '7', 'count')
        metrics.validate_metric(count)  # the existing governed format
        self.assertEqual((count['origin'], count['certification'], count['status']), ('generated', 'generated_unreviewed', 'internal'))
        text = f"{count['name']} {count['summary']}"
        for needed in ('payer claim', 'claim frequency code', '7', 'payer_claim.service_from'):
            self.assertIn(needed, text)
        self.assertEqual(count['grain']['key'], 'payer_claim_id')
        self.assertEqual(count['generated_from']['value'], '7')
        share = self.metric('payer_claim', 'claim_frequency_code', '7', 'share')
        self.assertEqual((share['output']['returns'], share['unit']), ('value', 'percent'))
        self.assertIn('denominator all payer claim rows', share['summary'])
        by = self.metric('payer_claim', 'claim_frequency_code', None, 'by')
        self.assertEqual(by['output']['returns'], 'table')

    def test_values_are_named_in_words_from_the_received_vocabulary_and_sibling_labels(self):
        text = (self.metric('payer_claim', 'claim_frequency_code', '7', 'count')['name'])
        self.assertIn('replacement of prior claim (claim frequency code 7)', text)  # config/agere/canonical/value_sets.json
        lab = self.metric('lab_result', 'status_code', 'C', 'count')['name']
        self.assertIn('Corrected result (status code C)', lab)  # a label column of the same table, checked to be a function
        self.assertIn('voided', self.metric('payer_claim', 'final_status', 'voided', 'count')['name'])  # a self-describing word needs no label

    def test_flags_read_as_the_flag_itself(self):
        yes = self.metric('payer_claim', 'counts_in_metrics', 1, 'count')
        no = self.metric('payer_claim', 'counts_in_metrics', 0, 'count')
        self.assertEqual(yes['name'], 'Payer claim: counts in metrics')
        self.assertEqual(no['name'], 'Payer claim: not counts in metrics')

    def test_every_generated_metric_compiles_through_the_router_and_explains_on_the_schema(self):
        db = sqlite3.connect(self.root / 'semantic.sqlite3')
        try:
            for mid in self.index.specs:
                m = self.index.metric(mid)
                slots = MR.validate_slots(m, {'period': {'preset': 'year', 'year': 2028}})
                node = MR.compile_metric(mid, m, slots, AS_OF)
                db.execute('EXPLAIN ' + node['parameters']['query']).fetchall()
        finally:
            db.close()


class Compile(Base):
    def run_sql(self, node):
        db = sqlite3.connect(self.root / 'semantic.sqlite3')
        try:
            return db.execute(node['parameters']['query']).fetchall()
        finally:
            db.close()

    def compiled(self, table, column, value, kind, question='', **slots):
        m = self.metric(table, column, value, kind)
        slots = MR.validate_slots(m, {'period': {'preset': 'year', 'year': 2028}, **slots})
        return m, MR.compile_metric(m['id'], m, slots, AS_OF, question)

    def test_count_is_right(self):
        _, node = self.compiled('payer_claim', 'claim_frequency_code', '7', 'count')
        self.assertEqual(self.run_sql(node), [(expected(self.root, "claim_frequency_code = '7'"),)])
        self.assertEqual(self.run_sql(node)[0][0], 20)

    def test_share_is_right(self):
        _, node = self.compiled('payer_claim', 'final_status', 'denied', 'share')
        in_year = expected(self.root, '1=1')
        denied = expected(self.root, "final_status = 'denied'")
        self.assertEqual(self.run_sql(node), [(round(100.0 * denied / in_year, 2),)])

    def test_by_is_right(self):
        _, node = self.compiled('payer_claim', 'claim_frequency_code', None, 'by')
        got = dict(self.run_sql(node))
        self.assertEqual(got, {'1': expected(self.root, "claim_frequency_code = '1'"), '7': 20, '8': 10})

    def test_the_period_applies_to_the_clock_the_question_names(self):
        m, plain = self.compiled('payer_claim', 'claim_frequency_code', '7', 'count', 'How many replacement claims were in 2028?')
        m, received = self.compiled('payer_claim', 'claim_frequency_code', '7', 'count', 'How many replacement claims were received in 2028?')
        self.assertIn('service_from', plain['parameters']['query'])
        self.assertIn('claim_received_date', received['parameters']['query'])
        self.assertEqual(received['variant'], 'received')
        self.assertEqual(self.run_sql(received), [(expected(self.root, "claim_frequency_code = '7'", date_col='claim_received_date'),)])

    def test_an_all_time_period_covers_every_date(self):
        m = self.metric('payer_claim', 'claim_frequency_code', '7', 'count')
        node = MR.compile_metric(m['id'], m, MR.validate_slots(m, {'period': {'preset': 'all_time'}}), AS_OF)
        self.assertEqual(self.run_sql(node), [(20,)])

    def test_a_value_with_a_quote_or_brace_is_a_literal_not_sql(self):
        self.assertEqual(G.literal("it's"), "'it''s'")
        self.assertEqual(G.literal('a{b}'), "'a{{b}}'")
        self.assertEqual(G.literal(3), '3')


class Ranking(Base):
    def test_the_question_must_name_a_value_or_the_column(self):
        self.assertEqual(self.index.select('How many patients do we have?'), [])
        self.assertEqual(self.index.select('What is the average number of units?'), [])

    def test_the_named_value_ranks_first_and_the_kind_follows_the_wording(self):
        self.assertEqual(self.index.select('How many replacement claims were submitted in 2028?')[0],
                         'gen.payer_claim.claim_frequency_code.7.count')
        self.assertEqual(self.index.select('What percent of claims were voided in 2028?')[0], 'gen.payer_claim.final_status.voided.share')
        self.assertEqual(self.index.select('How many claims by final status in 2028?')[0], 'gen.payer_claim.final_status.by')

    def test_a_bare_number_counts_only_beside_the_column_words(self):
        self.assertNotIn('gen.payer_claim.claim_frequency_code.7.count', self.index.select('How many claims in the last 7 days?'))
        self.assertIn('gen.payer_claim.claim_frequency_code.7.count', self.index.select('How many claims have frequency code 7?'))

    def test_a_negated_flag_is_offered_only_for_a_negated_question(self):
        self.assertIn('gen.payer_claim.counts_in_metrics.1.count', self.index.select('How many claims count in metrics?'))
        self.assertNotIn('gen.payer_claim.counts_in_metrics.0.count', self.index.select('How many claims count in metrics?'))
        self.assertIn('gen.payer_claim.counts_in_metrics.0.count', self.index.select('How many claims do not count in metrics?'))

    def test_an_encoder_reorders_the_word_matches(self):
        class Encoder:  # a question and a percent definition point the same way, a count the other
            def encode(self, texts):
                return [[1.0, 0.0] if t.endswith('?') or 'Percent' in t else [0.0, 1.0] for t in texts]
        words_only = self.index.select('How many claims were voided in 2028?', 4)
        with_encoder = self.index.select('How many claims were voided in 2028?', 4, encoder=Encoder())
        self.assertEqual(words_only[0], 'gen.payer_claim.final_status.voided.count')
        self.assertEqual(with_encoder[0], 'gen.payer_claim.final_status.voided.share')


class Routing(Base):
    def route(self, question, script, **env):
        fake = FakeS1(script)
        stack = SimpleNamespace(path=str(self.root), retriever=SimpleNamespace(encoder=None))
        with mock.patch('clinical_core.agere.clinical.system_one.decide', fake.decide), mock.patch.dict(os.environ, env):
            graph, info, rec = S1G.route_metric(stack, question, AS_OF, None)
        return graph, info, rec, fake

    def test_a_question_about_a_value_routes_to_the_generated_metric_and_compiles_to_the_right_sql(self):
        graph, info, rec, fake = self.route('How many replacement claims were received in 2028?',
                                            choose('payer_claim_claim_frequency_code_7_count', {'period': 'year', 'anchor': 'received'}))
        self.assertEqual(info['decision'], 'metric', info)
        self.assertEqual(info['metric'], 'gen.payer_claim.claim_frequency_code.7.count')
        self.assertEqual(info['resolution']['origin'], 'generated')
        self.assertEqual(info['resolution']['slots']['period'], {'preset': 'year', 'year': 2028})
        self.assertIn('claim_received_date', graph['nodes'][0]['parameters']['query'])
        db = sqlite3.connect(self.root / 'semantic.sqlite3')
        try:
            self.assertEqual(db.execute(graph['nodes'][0]['parameters']['query']).fetchone()[0],
                             expected(self.root, "claim_frequency_code = '7'", date_col='claim_received_date'))
        finally:
            db.close()
        self.assertIn('gen.payer_claim.claim_frequency_code.7.count', info['generated_offered'])
        self.assertLessEqual(len(fake.calls[0]['options']), 11)  # the option list stays small: <= 10 + none_of_these

    def test_the_exact_fit_gate_still_decides(self):
        graph, info, rec, fake = self.route('How many replacement claims did women have in 2028?',
                                            choose('payer_claim_claim_frequency_code_7_count', {'period': 'year'}))
        self.assertIsNone(graph)
        self.assertIn('exact-fit gate', ' '.join(d['reason'] for d in info.get('declined', [])))

    def test_number_accounting_still_decides(self):
        graph, info, rec, fake = self.route('How many replacement claims were there in 2028 or 2027?',
                                            choose('payer_claim_claim_frequency_code_7_count', {'period': 'year'}))
        self.assertIsNone(graph)

    def test_a_breakdown_question_routes_to_the_by_metric_and_a_scalar_does_not_answer_it(self):
        graph, info, rec, fake = self.route('How many payer claims by final status in 2028?', choose('payer_claim_final_status_by', {'period': 'year'}))
        self.assertEqual(info['decision'], 'metric', info)
        self.assertEqual(graph['nodes'][0]['parameters']['returns'], 'table')
        graph, info, rec, fake = self.route('How many payer claims by final status in 2028?', choose('payer_claim_final_status_voided_count', {'period': 'year'}))
        self.assertIsNone(graph)

    def test_a_question_that_names_no_generated_value_offers_none(self):
        graph, info, rec, fake = self.route('How many patients do we have?', choose('nothing'))
        self.assertNotIn('generated_offered', info)
        self.assertFalse([o for c in fake.calls for o in c['options'] if o.startswith('gen_')])

    def test_the_switch_removes_generated_metrics(self):
        graph, info, rec, fake = self.route('How many replacement claims were received in 2028?', choose('payer_claim_claim_frequency_code_7_count',
                                            {'period': 'year'}), AGERE_S1_GENERATED='off')
        self.assertIsNone(graph)
        self.assertFalse([o for c in fake.calls for o in c['options'] if o.startswith('gen_')])

    def test_a_missing_semantic_layer_leaves_the_authored_metrics_alone(self):
        fake = FakeS1(choose('ed_visits', {'period': 'year'}))
        stack = SimpleNamespace(path=str(Path(self.tmp.name) / 'nowhere'), retriever=SimpleNamespace(encoder=None))
        with mock.patch('clinical_core.agere.clinical.system_one.decide', fake.decide):
            graph, info, rec = S1G.route_metric(stack, 'How many ED visits were there in 2028?', AS_OF, None)
        self.assertEqual(info['metric'], 'ed_visits')


class Shortlist(unittest.TestCase):
    def library(self, generated):
        lib = MR.routable()
        extra = {}
        for i, name in enumerate(generated):
            extra[f'gen.t.c.{name}.count'] = {**lib['ed_visits'], 'id': f'gen.t.c.{name}.count', 'name': f'Generated {name}', 'origin': 'generated',
                                              'summary': f'generated {name}'}
        return {**lib, **extra}

    def test_authored_come_first_and_generated_interleave_up_to_the_cap(self):
        lib = self.library([f'g{i}' for i in range(8)])
        out = S1G.metric_shortlist('How many ED visits were there?', lib, 10)
        self.assertEqual(len(out), 10)
        self.assertEqual(out[0], 'ed_visits')  # authored first on ties
        generated = [m for m in out if m.startswith('gen.')]
        self.assertEqual(len(generated), 5)  # AGERE_S1_GENERATED_OPTIONS default
        self.assertEqual(out[1], generated[0])  # a1, g1, a2, g2, ...
        with mock.patch.dict(os.environ, {'AGERE_S1_GENERATED_OPTIONS': '2'}):
            self.assertEqual(len([m for m in S1G.metric_shortlist('How many ED visits were there?', lib, 10) if m.startswith('gen.')]), 2)

    def test_the_authored_shortlist_is_unchanged_without_generated_metrics(self):
        lib = MR.routable()
        self.assertEqual(S1G.metric_shortlist('How many ED visits were there?', lib, 10),
                         S1G.metric_shortlist('How many ED visits were there?', self.library([]), 10))


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = fixture(self.tmp.name)
        G._MEMO.clear()
        patcher = mock.patch.dict(os.environ, {'AGERE_GENERATED_METRICS_DIR': ''})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_built_once_and_reused_for_the_same_digest(self):
        first = G.build(self.root)
        self.assertTrue((self.root / G.FILE).exists())
        with mock.patch.object(G, 'discover', side_effect=AssertionError('rebuilt')):
            again = G.build(self.root)
        self.assertEqual(first['metrics'], again['metrics'])
        self.assertEqual(len(G.load(self.root)), first['metrics'])

    def test_a_changed_semantic_digest_rebuilds(self):
        old = G.build(self.root)
        index = G.load(self.root)
        self.assertEqual(index.digest, 'semantic-layer/test:dig-1')
        db = sqlite3.connect(self.root / 'semantic.sqlite3')
        db.execute("UPDATE meta SET value = 'dig-2' WHERE key = 'source_digest'")
        db.execute("INSERT INTO payer_claim SELECT 'X' || payer_claim_id, patient_id, service_from, claim_received_date, first_available_date, '9', final_status, "
                   "place_of_service, paid_cents, diagnosis_codes, counts_in_metrics FROM payer_claim WHERE claim_frequency_code = '7'")
        db.commit()
        db.close()
        fresh = G.load(self.root)
        self.assertEqual(fresh.digest, 'semantic-layer/test:dig-2')
        self.assertIn('gen.payer_claim.claim_frequency_code.9.count', fresh.specs)
        self.assertNotIn('gen.payer_claim.claim_frequency_code.9.count', index.specs)
        self.assertEqual(json.loads((self.root / G.FILE).read_text())['semantic_digest'], 'semantic-layer/test:dig-2')
        self.assertGreater(G.build(self.root)['values'], old['values'])

    def test_a_schema_without_meta_is_digested_by_its_structure(self):
        a = sqlite3.connect(':memory:')
        a.execute('CREATE TABLE t(x TEXT)')
        b = sqlite3.connect(':memory:')
        b.execute('CREATE TABLE t(x TEXT, y TEXT)')
        self.assertNotEqual(G.digest_of(a), G.digest_of(b))
        self.assertTrue(G.digest_of(a).startswith('schema:'))

    def test_the_cache_can_live_beside_a_read_only_stack(self):
        scratch = Path(self.tmp.name) / 'scratch'
        os.chmod(self.root, 0o555)
        self.addCleanup(os.chmod, self.root, 0o755)
        report = G.build(self.root, cache_dir=scratch)
        self.assertFalse((self.root / G.FILE).exists())
        self.assertTrue(Path(report['file']).exists())
        self.assertEqual(Path(report['file']).parent, scratch)
        self.assertEqual(len(G.load(self.root, cache_dir=scratch)), report['metrics'])
        with mock.patch.dict(os.environ, {'AGERE_GENERATED_METRICS_DIR': str(scratch)}):
            self.assertEqual(G.cache_file(self.root).parent, scratch)

    def test_an_unwritable_stack_still_serves_from_memory(self):
        os.chmod(self.root, 0o555)
        self.addCleanup(os.chmod, self.root, 0o755)
        if os.access(self.root, os.W_OK): self.skipTest('running as a user that can write anywhere')
        index = G.load(self.root)
        self.assertGreater(len(index), 0)


class Certification(Base):
    def receipt(self, origin):
        return {'route': 'metric_router', 'metric_router': {'resolution': {'metric': 'x', 'origin': origin} if origin else {'metric': 'x'}}}

    def test_a_generated_metric_answer_is_below_an_authored_one(self):
        self.assertEqual(gate_policy.path_gates(self.receipt(None))[0]['gate'], 'metric_router.answer')
        self.assertEqual(gate_policy.path_gates(self.receipt('generated'))[0]['gate'], 'metric_router.generated_answer')
        verdict = gate_policy.assess(self.receipt('generated'))
        self.assertNotEqual(verdict['level'], 'verified')

    def test_no_cascade_tier_certificate_covers_a_generated_metric(self):
        from clinical_core.agere.clinical import cascade
        self.assertEqual(cascade.route_tier(self.receipt(None)), 'metric_router')
        self.assertIsNone(cascade.route_tier(self.receipt('generated')))

    def test_the_answer_states_it_was_generated(self):
        graph, info = MR.route('How many replacement claims were in 2028?', AS_OF, None, None, ask=lambda q, a, tools=None, forced=None, feedback=None: (
            {'name': MR.tool_name('gen.payer_claim.claim_frequency_code.7.count'), 'input': {}} if forced is None else
            {'name': forced, 'input': {'period': {'preset': 'year', 'year': 2028}}}, {}),
            lib={'gen.payer_claim.claim_frequency_code.7.count': self.metric('payer_claim', 'claim_frequency_code', '7', 'count')})
        self.assertEqual(info['decision'], 'metric', info)
        answer = MR.finalize_answer({'answer_type': 'count', 'status': 'answered', 'count': 20},
                                    {'route': 'metric_router', 'metric_router': info})
        self.assertEqual(answer['computed_as']['origin'], 'generated')
        self.assertNotEqual(answer['confidence'], 'verified')
        self.assertIn('generated definition', answer['confidence_reason'])


if __name__ == '__main__':
    unittest.main()
