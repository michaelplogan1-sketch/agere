"""Canonical model (canonical/1) support: version resolution, coverage gate, docs generator, frontier_canonical bench mode.

Synthetic fixtures only, except the env-gated raw reconciliation (AGERE_CANONICAL_RAW_DB=<raw records sqlite>)."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.canonical import CONFIG, coverage, docs_gen, versions
from clinical_core.agere.canonical.versions import Version, settle, settle_fills

REL = 'rel-1'


def make_raw(path, records):
    """records: (id, stream, patient, arrival, supersedes, payload dict) -> a raw warehouse DB like the bench DB."""
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE releases (id TEXT PRIMARY KEY, manifest TEXT);
        CREATE TABLE records (release TEXT,id TEXT,patient TEXT,stream TEXT,event TEXT,arrival TEXT,
          identity TEXT,supersedes TEXT,hash TEXT,payload TEXT,PRIMARY KEY(release,id));
        CREATE INDEX corrections ON records(release,supersedes);
        CREATE INDEX ix_records_stream ON records(stream, release, identity, arrival);''')
    db.execute('INSERT INTO releases VALUES (?, ?)', (REL, json.dumps({'as_of': '2030-01-31'})))
    for rid, stream, patient, arrival, sup, payload in records:
        payload = {**payload, 'record_id': rid, 'supersedes_record_id': sup}
        db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (REL, rid, patient, stream, payload.get('event_date', arrival), arrival, 'matched', sup, '', json.dumps(payload)))
    db.commit()
    db.close()


def pmc(cid, status, seq=1, freq='1', paid=0.0, service='2029-03-01'):
    return {'payer_claim_id': cid, 'claim_status': status, 'adjudication_sequence': seq, 'claim_frequency_code': freq,
            'paid_amount': paid, 'service_from': service}


RAW_RECORDS = [
    # simple paid claim
    ('m1', 'payer_medical_claims', 'P1', '2029-03-05', None, pmc('C1', 'paid', paid=100.0)),
    # denied then paid
    ('m2', 'payer_medical_claims', 'P1', '2029-03-05', None, pmc('C2', 'denied')),
    ('m3', 'payer_medical_claims', 'P1', '2029-04-05', 'm2', pmc('C2', 'paid', seq=2, paid=50.0)),
    # paid, voided (freq 8) and replaced (freq 7, new claim id)
    ('m4', 'payer_medical_claims', 'P2', '2029-03-05', None, pmc('C3', 'paid', paid=70.0)),
    ('m5', 'payer_medical_claims', 'P2', '2029-05-01', 'm4', pmc('C3', 'voided', seq=2, freq='8')),
    ('m6', 'payer_medical_claims', 'P2', '2029-05-02', 'm4', pmc('C3-R', 'paid', freq='7', paid=80.0)),
    # paid then reversed
    ('m7', 'payer_medical_claims', 'P2', '2029-03-05', None, pmc('C4', 'paid', paid=40.0)),
    ('m8', 'payer_medical_claims', 'P2', '2029-06-01', 'm7', pmc('C4', 'reversed', seq=2, paid=-40.0)),
    # paid then recouped
    ('m9', 'payer_medical_claims', 'P3', '2029-03-05', None, pmc('C5', 'paid', paid=30.0)),
    ('m10', 'payer_medical_claims', 'P3', '2029-07-01', 'm9', pmc('C5', 'recouped', seq=2, paid=-30.0)),
    # 2028 service date (outside the 2029 reconciliation)
    ('m11', 'payer_medical_claims', 'P3', '2028-03-05', None, pmc('C6', 'paid', paid=999.0, service='2028-02-01')),
    # pharmacy: paid, rejected, paid->reversed
    ('r1', 'payer_pharmacy_claims', 'P1', '2029-03-02', None, {'rx_claim_id': 'X1', 'claim_status': 'paid', 'plan_paid': 12.5}),
    ('r2', 'payer_pharmacy_claims', 'P1', '2029-03-02', None, {'rx_claim_id': 'X2', 'claim_status': 'rejected', 'plan_paid': 0.0, 'reject_code': '79'}),
    ('r3', 'payer_pharmacy_claims', 'P2', '2029-03-02', None, {'rx_claim_id': 'X3', 'claim_status': 'paid', 'plan_paid': 20.0}),
    ('r4', 'payer_pharmacy_claims', 'P2', '2029-03-09', 'r3', {'rx_claim_id': 'X3', 'claim_status': 'reversed', 'plan_paid': 0.0}),
    # pharmacy transactions: paid then reversal (pointer original_transaction_record_id)
    ('t1', 'pharmacy_claim_transactions', 'P1', '2029-03-02', None, {'claim_id': 'F1', 'transaction_status': 'paid', 'payer_paid_delta_cents': 1250}),
    ('t2', 'pharmacy_claim_transactions', 'P1', '2029-03-04', None, {'claim_id': 'F1', 'transaction_status': 'reversed', 'payer_paid_delta_cents': -1250, 'original_transaction_record_id': 't1'}),
    # dispenses: prepared->dispensed (picked up), prepared->returned_to_stock, prepared only
    ('d1', 'pharmacy_dispenses', 'P1', '2029-03-01', None, {'fill_id': 'F1', 'status': 'prepared'}),
    ('d2', 'pharmacy_dispenses', 'P1', '2029-03-02', None, {'fill_id': 'F1', 'status': 'dispensed'}),
    ('d3', 'pharmacy_dispenses', 'P2', '2029-03-01', None, {'fill_id': 'F2', 'status': 'prepared'}),
    ('d4', 'pharmacy_dispenses', 'P2', '2029-03-12', None, {'fill_id': 'F2', 'status': 'returned_to_stock'}),
    ('d5', 'pharmacy_dispenses', 'P3', '2029-03-01', None, {'fill_id': 'F3', 'status': 'prepared'}),
    # provider claim: corrected submission replaces the original
    ('s1', 'medical_claim_submissions', 'P1', '2029-03-01', None, {'claim_id': 'PC1', 'submission_kind': 'original', 'billed_cents': 10000}),
    ('s2', 'medical_claim_submissions', 'P1', '2029-03-20', None, {'claim_id': 'PC1', 'submission_kind': 'corrected', 'billed_cents': 9000, 'original_submission_record_id': 's1'}),
    # lab corrected through replaces_result_record_id; adjudication replaced through claim_corrections
    ('l1', 'labs', 'P1', '2029-03-01', None, {'name': 'eGFR', 'value': 50}),
    ('l2', 'labs', 'P1', '2029-04-01', None, {'name': 'eGFR', 'value': 55, 'replaces_result_record_id': 'l1'}),
    ('a1', 'medical_claims', 'P1', '2029-03-01', None, {'status': 'paid', 'payer_paid_cents': 100}),
    ('a2', 'medical_claims', 'P1', '2029-04-01', None, {'status': 'paid', 'payer_paid_cents': 120}),
    ('cc1', 'claim_corrections', 'P1', '2029-04-02', None, {'original_adjudication_record_id': 'a1', 'replacement_adjudication_record_id': 'a2'}),
]


class SettleRules(unittest.TestCase):
    def by_key(self, settled):
        return {s.key: s for s in settled}

    def test_version_chain_and_denied_then_paid(self):
        s = self.by_key(settle([Version('a', key='C', status='denied', sequence=1),
                                Version('b', ('a',), key='C', status='paid', sequence=2, amount=50.0)]))['C']
        self.assertEqual((s.final_record_id, s.final_status, s.net_amount, s.denied_then_paid, s.status_path),
                         ('b', 'paid', 50.0, 1, 'denied>paid'))

    def test_void_freq8_and_replacement_freq7(self):
        s = self.by_key(settle([Version('a', key='C', status='paid', amount=70.0),
                                Version('v', ('a',), key='C', status='voided', sequence=2, frequency='8'),
                                Version('r', ('a',), key='C-R', status='paid', frequency='7', amount=80.0)]))
        self.assertEqual((s['C'].final_status, s['C'].net_amount, s['C'].rule), ('voided', 0.0, 'freq8_void'))
        self.assertEqual((s['C-R'].final_status, s['C-R'].net_amount, s['C-R'].rule), ('paid', 80.0, 'freq7_replacement_claim'))

    def test_replacement_without_void_marks_original_replaced(self):
        s = self.by_key(settle([Version('a', key='C', status='paid', amount=70.0),
                                Version('r', ('a',), key='C-R', status='paid', frequency='7', amount=80.0)]))
        self.assertEqual((s['C'].final_status, s['C'].replaced_by, s['C'].net_amount, s['C'].final_record_id),
                         ('replaced', 'C-R', 0.0, None))

    def test_reversed_and_recouped_net(self):
        s = self.by_key(settle([Version('a', key='R', status='paid', amount=40.0),
                                Version('b', ('a',), key='R', status='reversed', sequence=2, amount=-40.0),
                                Version('c', key='Q', status='paid', amount=30.0),
                                Version('d', ('c',), key='Q', status='recouped', sequence=2, amount=-12.0),
                                Version('e', key='S', status='recouped', sequence=2, amount=-5.0),
                                Version('f', key='T', status='paid', amount=10.0),
                                Version('g', ('f',), key='T', status='reversed', sequence=2, amount=-10.0),
                                Version('h', ('g',), key='T', status='paid', sequence=3, amount=10.0)]))
        self.assertEqual(s['R'].net_amount, 0.0)
        self.assertEqual(s['Q'].net_amount, 18.0)           # paid 30 less 12 recovered
        self.assertEqual(s['S'].net_amount, -5.0)           # recovery of a payment outside the window
        self.assertEqual((s['T'].final_status, s['T'].net_amount), ('paid', 10.0))

    def test_rejected_pharmacy_claim_is_zero(self):
        s = settle([Version('a', key='X', status='rejected', amount=0.0)])[0]
        self.assertEqual((s.final_status, s.net_amount), ('rejected', 0.0))

    def test_multiple_current_takes_latest(self):
        s = settle([Version('a', key='X', status='paid', arrival='2029-01-01', amount=1.0),
                    Version('b', key='X', status='paid', arrival='2029-01-05', amount=2.0)])[0]
        self.assertEqual((s.final_record_id, s.rule, s.net_amount), ('b', 'multiple_current_latest', 2.0))

    def test_transaction_deltas_sum(self):
        s = settle([Version('t1', key='F', status='paid', amount=1250),
                    Version('t2', ('t1',), key='F', status='reversed', amount=-1250)], net='sum')[0]
        self.assertEqual((s.final_status, s.net_amount), ('reversed', 0))

    def test_returned_to_stock_and_prepared_are_not_picked_up(self):
        s = {x.key: x for x in settle_fills([
            Version('1', key='F1', status='prepared'), Version('2', key='F1', status='dispensed'),
            Version('3', key='F2', status='prepared'), Version('4', key='F2', status='returned_to_stock'),
            Version('5', key='F3', status='prepared'), Version('6', key=None, status='dispensed')])}
        self.assertEqual((s['F1'].picked_up, s['F1'].final_record_id), (1, '2'))
        self.assertEqual((s['F2'].picked_up, s['F2'].final_status), (0, 'returned_to_stock'))
        self.assertEqual((s['F3'].picked_up, s['F3'].final_status), (0, 'prepared'))
        self.assertEqual(s['6'].picked_up, 1)

    def test_current_ids(self):
        vs = [Version('a'), Version('b', ('a',)), Version('c', ('b',)), Version('d', ('zz',))]
        self.assertEqual(versions.current_ids(vs), {'c', 'd'})


class BuildOnSyntheticRaw(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.raw = Path(cls.tmp.name) / 'raw.sqlite3'
        make_raw(cls.raw, RAW_RECORDS)
        cls.out = Path(cls.tmp.name) / 'v.sqlite3'
        cls.receipt = versions.build(cls.raw, cls.out)
        cls.db = sqlite3.connect(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def final(self, stream, key):
        return self.db.execute('SELECT final_status, net_amount, picked_up, replaced_by FROM entity_version '
                               'WHERE stream = ? AND entity_key = ?', (stream, key)).fetchone()

    def test_reconciliation_matches(self):
        checks = {c['check']: c for c in self.receipt['reconciliation']}
        self.assertTrue(self.receipt['reconciled'], [c for c in checks.values() if not c.get('match', True)])
        # 2029 final paid: C1 100 + C2 50 + C3-R 80 (C3 voided, C4 reversed, C5 recouped, C6 is 2028)
        self.assertEqual(checks['payer_medical_paid_2029_final_paid_status']['canonical'], 230.0)
        self.assertEqual(checks['payer_pharmacy_claims_distinct_claims']['raw'], 3)
        self.assertEqual(checks['pharmacy_dispenses_picked_up_fills']['canonical'], 1)

    def test_entity_rows(self):
        self.assertEqual(self.final('payer_medical_claims', 'C3'), ('voided', 0.0, None, None))
        self.assertEqual(self.final('payer_medical_claims', 'C5'), ('recouped', 0.0, None, None))
        self.assertEqual(self.final('payer_pharmacy_claims', 'X2')[:2], ('rejected', 0.0))
        self.assertEqual(self.final('pharmacy_claim_transactions', 'F1')[:2], ('reversed', 0))
        self.assertEqual(self.final('medical_claim_submissions', 'PC1')[:2], ('corrected', 9000))
        self.assertEqual(self.final('pharmacy_dispenses', 'F2')[2], 0)

    def test_record_level_pointers(self):
        rows = set(self.db.execute('SELECT record_id, stream, superseded_by, rule FROM superseded_record'))
        self.assertIn(('l1', 'labs', 'l2', 'replaces_result_record_id'), rows)
        self.assertIn(('a1', 'medical_claims', 'a2', 'claim_corrections.replacement_adjudication_record_id'), rows)
        self.assertIn(('s1', 'medical_claim_submissions', 's2', 'original_submission_record_id'), rows)
        self.assertIn(('m2', 'payer_medical_claims', 'm3', 'supersedes_record_id'), rows)

    def test_as_of_ignores_later_versions(self):
        out = Path(self.tmp.name) / 'asof.sqlite3'
        receipt = versions.build(self.raw, out, as_of='2029-04-30')
        db = sqlite3.connect(out)
        try:
            row = db.execute("SELECT final_status, net_amount FROM entity_version WHERE entity_key = 'C3'").fetchone()
            self.assertEqual(row, ('paid', 70.0))     # the void and replacement arrive in May
            self.assertTrue(receipt['reconciled'])
        finally:
            db.close()


class CoverageGate(unittest.TestCase):
    MAP = {'tables': {'medical_claim': {}, 'lab_result': {}},
           'streams': {'labs': {'tables': ['lab_result']}, 'payer_medical_claims': {'tables': ['medical_claim']},
                       'billing_authorizations': {'unused': 'workflow state, not a claim'}}}

    def test_passes_when_every_stream_is_accounted_for(self):
        report = coverage.require(['labs', 'payer_medical_claims', 'billing_authorizations'], self.MAP)
        self.assertEqual((report['mapped'], report['unused']), (2, ['billing_authorizations']))

    def test_fails_on_unmapped_stream(self):
        with self.assertRaises(coverage.CoverageError) as ctx:
            coverage.require(['labs', 'brand_new_feed'], self.MAP)
        self.assertIn('brand_new_feed', str(ctx.exception))

    def test_fails_on_unknown_table_or_reasonless_unused(self):
        bad = {**self.MAP, 'streams': {'labs': {'tables': ['lab_results_typo']}, 'x': {'unused': ' '}}}
        report = coverage.check(['labs', 'x'], bad)
        self.assertFalse(report['ok'])
        self.assertEqual((report['unknown_tables'], report['unused_without_reason']), ({'labs': ['lab_results_typo']}, ['x']))

    def test_shipped_stream_map_is_complete_and_consistent(self):
        sm = coverage.load_stream_map()
        self.assertEqual(len(sm['streams']), 237)
        report = coverage.check(list(sm['streams']), sm)
        self.assertTrue(report['ok'], report)
        for name in ('payer_medical_claims', 'payer_pharmacy_claims', 'pharmacy_dispenses', 'enrollment_spans', 'labs'):
            self.assertIn('tables', sm['streams'][name])


class DocsGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        raw = Path(self.tmp.name) / 'raw.sqlite3'
        make_raw(raw, RAW_RECORDS)
        self.out = Path(self.tmp.name) / 'v.sqlite3'
        versions.build(raw, self.out)

    def tearDown(self):
        self.tmp.cleanup()

    def examples(self, items):
        path = Path(self.tmp.name) / 'ex.json'
        path.write_text(json.dumps({'examples': items}))
        return path

    def test_docs_have_tables_rules_value_sets_and_only_verified_examples(self):
        ex = self.examples([
            {'id': 'ok', 'source': 'canonical-reconciliation/x', 'question': 'paid 2029?', 'expected': 230.0,
             'sql': "SELECT ROUND(SUM(net_amount),2) AS value FROM entity_version WHERE stream='payer_medical_claims' "
                    "AND final_status='paid' AND substr(service_date,1,4)='2029'"},
            {'id': 'wrong', 'source': 'canonical-reconciliation/y', 'question': 'q', 'expected': 1,
             'sql': 'SELECT COUNT(*) AS n FROM entity_version'}])
        db, columns, meta = docs_gen.open_database(self.out)
        try:
            text, ids, rejected = docs_gen.build_docs(columns, lambda s: [dict(r) for r in db.execute(s)], meta,
                                                      examples_path=ex)
        finally:
            db.close()
        self.assertEqual(ids, frozenset({'ok'}))
        self.assertEqual([r['id'] for r in rejected], ['wrong'])
        for needle in ('### entity_version (', '- final_status: settled final status', 'freq7_replacement',
                       'mammography', '77067', 'NUBC', 'pharmacy_dispenses', 'Q: paid 2029?'):
            self.assertIn(needle, text)
        self.assertNotIn('Q: q\n', text)

    def test_heldout_or_capability_examples_are_refused(self):
        for source in ('heldout10a/H01', 'bench/capability/C3'):
            with self.assertRaises(ValueError):
                docs_gen.load_examples(self.examples([{'id': 'x', 'source': source, 'question': 'q', 'sql': 'SELECT 1', 'expected': 1}]))

    def test_shipped_examples_are_clean(self):
        self.assertTrue(docs_gen.load_examples())

    def test_dbt_manifest_docs(self):
        manifest = Path(self.tmp.name) / 'manifest.json'
        manifest.write_text(json.dumps({'nodes': {'model.tuva.medical_claim': {
            'resource_type': 'model', 'name': 'medical_claim', 'alias': 'medical_claim', 'description': 'Claims.',
            'columns': {'paid_amount': {'description': 'Paid.'}}}}}))
        self.assertEqual(docs_gen.dbt_docs(manifest)['medical_claim']['columns']['paid_amount'], 'Paid.')


class FrontierCanonical(unittest.TestCase):
    def test_tool_loop_over_canonical_db(self):
        from bench.adapters import frontier_canonical
        from bench.common import CanonicalWarehouse
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        raw = Path(tmp.name) / 'raw.sqlite3'
        make_raw(raw, RAW_RECORDS)
        out = Path(tmp.name) / 'v.sqlite3'
        versions.build(raw, out)
        wh = CanonicalWarehouse(out)
        self.addCleanup(wh.close)
        calls = []

        def transport(url, body, headers):
            calls.append(body)
            self.assertNotIn('michael', json.dumps(headers).lower())
            if len(calls) == 1:
                self.assertIn('Canonical model documentation', body['system'][1]['text'])
                return {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'query_sql', 'input': {
                    'sql': "SELECT COUNT(*) AS n FROM entity_version WHERE stream='pharmacy_dispenses' AND picked_up=1"}}],
                        'usage': {'input_tokens': 10, 'output_tokens': 5}}
            result = json.loads(body['messages'][-1]['content'][0]['content'])
            answer = {'status': 'answered', 'answer_type': 'count', 'count': result['rows'][0]['n']}
            return {'content': [{'type': 'text', 'text': json.dumps(answer)}], 'usage': {'input_tokens': 10, 'output_tokens': 5},
                    'stop_reason': 'end_turn'}

        result = frontier_canonical({'id': 'q1', 'question': 'How many fills were picked up?', 'as_of': '2030-01-31'},
                                    wh, key='test', transport=transport)
        self.assertIsNone(result['error'])
        self.assertEqual(result['answer']['count'], 1)
        self.assertEqual(result['tool_call_count'], 1)

    def test_canonical_warehouse_is_read_only(self):
        from bench.common import CanonicalWarehouse
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'c.sqlite3'
        sqlite3.connect(path).executescript('CREATE TABLE t(x); INSERT INTO t VALUES (1);').close()
        wh = CanonicalWarehouse(path)
        self.addCleanup(wh.close)
        self.assertEqual(wh.query('SELECT x FROM t')['rows'], [{'x': 1}])
        self.assertIsNone(wh.as_of)
        for sql in ('DELETE FROM t', "ATTACH 'x.db' AS y", 'PRAGMA writable_schema=1'):
            with self.assertRaises((sqlite3.Error, ValueError)):
                wh.query(sql)


@unittest.skipUnless(os.environ.get('AGERE_CANONICAL_RAW_DB'), 'set AGERE_CANONICAL_RAW_DB to the raw v28-sample DB')
class RawReconciliation(unittest.TestCase):
    def test_full_build_reconciles(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = versions.build(os.environ['AGERE_CANONICAL_RAW_DB'], Path(tmp) / 'v.sqlite3')
        checks = {c['check']: c for c in receipt['reconciliation']}
        self.assertTrue(receipt['reconciled'], [c for c in checks.values() if not c.get('match', True)])
        self.assertEqual(checks['payer_medical_paid_2029_final_paid_status']['raw'], 13192436.59)


if __name__ == '__main__':
    unittest.main()
