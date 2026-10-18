"""Native workspace additions: payer_claim first status from the adjudication history (native builds only),
delivery_file / delivery_gap (file-level delivery facts) and the source-to-model parity check. Synthetic fixtures only."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import adjudication_history
from clinical_core.agere.ingest import delivery
from clinical_core.agere.onboarding import parity

LANDING_DDL = '''CREATE TABLE ingest_files (relpath TEXT PRIMARY KEY, feed TEXT, parser TEXT, bytes INTEGER, mtime REAL,
    rows INTEGER, tables TEXT, error TEXT, seconds REAL, ingested_at TEXT);'''


def _claim(cid, status, seq, paid=0.0, available='2029-03-05'):
    return {'payer_claim_id': cid, 'claim_status': status, 'adjudication_sequence': seq, 'claim_frequency_code': '1',
            'paid_amount': paid, 'allowed_amount': paid, 'service_from': '2029-03-01', 'payer_id': 'WH-PAYER-1',
            'product': 'commercial', 'available_date': available}


def _warehouse(path, records):
    """records: (id, patient, stream, event, arrival, supersedes, payload) -> a received store like canonical._write_received."""
    w = sqlite3.connect(path)
    w.executescript('''CREATE TABLE records (release TEXT, id TEXT, patient TEXT, stream TEXT, event TEXT, arrival TEXT, identity TEXT,
                         supersedes TEXT, hash TEXT, payload TEXT, PRIMARY KEY(release,id));''')
    for rid, pid, stream, event, arrival, sup, payload in records:
        w.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',
                  ('r', rid, pid, stream, event, arrival, 'matched', sup, '', json.dumps(payload)))
    w.commit()
    w.close()


class PayerClaimHistoryTests(unittest.TestCase):
    """A denied-then-paid claim: the record store keeps only the paid version; the warehouse keeps both."""

    def _semantic(self, d):
        out = sqlite3.connect(d / 'semantic.sqlite3')
        out.executescript('''CREATE TABLE payer_claim(payer_claim_id TEXT PRIMARY KEY, patient_id TEXT, adjudications INTEGER,
                               first_status TEXT, final_status TEXT, first_available_date TEXT);
                             CREATE TABLE patient(patient_id TEXT);''')
        out.executemany('INSERT INTO payer_claim VALUES (?,?,?,?,?,?)', [
            ('C1', 'P1', 1, 'paid', 'paid', '2029-04-05'),     # first adjudication (denied) was superseded
            ('C2', 'P1', 1, 'paid', 'paid', '2029-03-05'),     # single paid adjudication
            ('C9', 'P2', 1, 'denied', 'denied', '2029-03-05')])  # not in the history: left as is
        _warehouse(d / 'warehouse.sqlite3', [
            ('m1', 'P1', 'payer_medical_claims', '2029-03-05', '2029-03-05', None, _claim('C1', 'denied', 1)),
            ('m2', 'P1', 'payer_medical_claims', '2029-04-05', '2029-04-05', 'm1', _claim('C1', 'paid', 2, 50.0, '2029-04-05')),
            ('m3', 'P1', 'payer_medical_claims', '2029-03-05', '2029-03-05', None, _claim('C2', 'paid', 1, 20.0))])
        adjudication_history.build(out, d / 'warehouse.sqlite3')
        return out

    def test_first_status_and_adjudications_come_from_history(self):
        with tempfile.TemporaryDirectory() as t:
            out = self._semantic(Path(t))
            summary = adjudication_history.reconcile_payer_claim(out)
            rows = {r[0]: r[1:] for r in out.execute(
                'SELECT payer_claim_id, first_status, adjudications, final_status, first_available_date FROM payer_claim')}
            self.assertEqual(rows['C1'], ('denied', 2, 'paid', '2029-03-05'))
            self.assertEqual(rows['C2'], ('paid', 1, 'paid', '2029-03-05'))
            self.assertEqual(rows['C9'], ('denied', 1, 'denied', '2029-03-05'))
            self.assertEqual(summary, {'claims_with_history': 2, 'first_status_changed': 1, 'adjudications_changed': 1})
            # Matches the event table it is derived from.
            first = dict(out.execute('SELECT payer_claim_id, claim_status FROM payer_adjudication WHERE is_first = 1'))
            self.assertEqual(first, {'C1': 'denied', 'C2': 'paid'})
            out.close()

    def test_missing_tables_skip(self):
        out = sqlite3.connect(':memory:')
        self.assertIn('skipped', adjudication_history.reconcile_payer_claim(out))

    def test_semantic_build_applies_only_to_native_stacks(self):
        """semantic_layer.build reconciles payer_claim for agere-native-stack/1 runs and leaves other builds unchanged."""
        from clinical_core.agere.clinical import semantic_layer
        results = {}
        for schema in ('agere-native-stack/1', 'warehouse-hybrid-retrieval/1'):
            with tempfile.TemporaryDirectory() as t:
                d = Path(t) / 'build'
                (d / 'stack').mkdir(parents=True)
                pid = 'AGP-000000000001'
                person = {'patient_id': pid, 'birth_date': '1960-01-01', 'sex_reported': 'F', 'synthetic': True}
                versions = [('m1', '2029-03-05', None, _claim('C1', 'denied', 1)),
                            ('m2', '2029-04-05', 'm1', _claim('C1', 'paid', 2, 50.0, '2029-04-05'))]
                recs = []
                for rid, day, sup, p in versions:
                    recs.append((rid, {**p, 'record_id': rid, 'patient_id': pid, 'source_table': 'payer_medical_claims',
                                       'event_date': day, 'supersedes_record_id': sup, 'synthetic': True}))
                st = sqlite3.connect(d / 'stack' / 'stack.sqlite3')
                st.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY, payload TEXT);'
                                 'CREATE TABLE records(id TEXT PRIMARY KEY, patient TEXT, stream TEXT, payload TEXT);')
                st.execute('INSERT INTO patients VALUES (?, ?)', (pid, json.dumps(person)))
                rid, rec = recs[-1]  # the record store keeps the current version only
                st.execute('INSERT INTO records VALUES (?,?,?,?)', (rid, pid, 'payer_medical_claims', json.dumps(rec)))
                st.commit()
                st.close()
                _warehouse(d / 'warehouse.sqlite3', [(rid, pid, 'payer_medical_claims', r['event_date'], r['event_date'],
                                                      r['supersedes_record_id'], r) for rid, r in recs])
                (d / 'stack' / 'run.json').write_text(json.dumps({'schema': schema, 'synthetic': True, 'source_digest': schema,
                                                                  'patients': 1, 'snapshot': {'release': 't', 'as_of': '2030-01-31'}}))
                semantic_layer.build(d / 'stack')
                db = sqlite3.connect(d / 'stack' / 'semantic.sqlite3')
                results[schema] = db.execute("SELECT first_status, adjudications, final_status FROM payer_claim "
                                             "WHERE payer_claim_id = 'C1'").fetchone()
                db.close()
        self.assertEqual(results['agere-native-stack/1'], ('denied', 2, 'paid'))
        self.assertEqual(results['warehouse-hybrid-retrieval/1'], ('paid', 1, 'paid'))


def _delivery_fixture(root, skip=()):
    """A landing DB + raw folder + manifest covering resend, v2, truncation, empty/header-only files and a series gap
    (skip: relpaths left out of the delivery)."""
    raw = root / 'raw'
    files = {  # relpath -> (content, parser, data table, parsed rows, claimed records, delivered)
        'payers/WH-PAYER-C1/2026-09/C1_MEDICAL_20261005.txt': ('H\n1\n2\n', 'flat', 'payers_medical', 2, 2, '2026-10-05'),
        'payers/WH-PAYER-C1/2026-09/C1_MEDICAL_20261005_RESEND.txt': ('H\n1\n2\n', 'flat', 'payers_medical', 2, 2, '2026-10-20'),
        'payers/WH-PAYER-C1/2026-10/C1_MEDICAL_20261105.txt': ('H\n1\n2\n3\n', 'flat', 'payers_medical', 3, 3, '2026-11-05'),
        'payers/WH-PAYER-C1/2026-10/C1_MEDICAL_20261105_v2.txt': ('H\n1\n2\n4\n', 'flat', 'payers_medical', 3, 3, '2026-11-12'),
        # 2026-11 medical missing while the sender delivered its other files that month (gap)
        'payers/WH-PAYER-C1/2026-11/C1_ELIGIBILITY_20261205.txt': ('H\nm1\n', 'flat', 'payers_medical', 1, 1, '2026-12-05'),
        'payers/WH-PAYER-C1/2026-12/C1_MEDICAL_20270105.txt': ('H\n1\n2', 'flat', 'payers_medical', 2, 5, '2027-01-25'),
        'payers/WH-PAYER-C1/2027-01/C1_MEDICAL_20270205.txt': ('H\n', 'flat', 'payers_medical', 0, 4, '2027-02-05'),
        'payers/WH-PAYER-C1/2027-02/C1_MEDICAL_20270305.txt': ('', 'flat', 'payers_medical', 0, 3, '2027-03-05'),
        'payers/WH-PAYER-C1/2027-03/C1_MEDICAL_20270405.txt': ('H\n1\n', 'flat', 'payers_medical', 1, 1, '2027-04-05'),
        'payers/WH-PAYER-C1/2027-04/C1_MEDICAL_20270505.txt': ('H\n5\n', 'flat', 'payers_medical', 1, 1, '2027-05-05'),
        'payers/WH-PAYER-C1/2027-05/C1_MEDICAL_20270605.txt': ('H\n6\n', 'flat', 'payers_medical', 1, 1, '2027-06-05'),
        'payers/WH-PAYER-C1/2027-06/C1_MEDICAL_20270705.txt': ('H\n7\n', 'flat', 'payers_medical', 1, 1, '2027-07-05'),
        # last month's bytes under this month's name, claiming more rows: a stale copy, not a truncation
        'payers/WH-PAYER-C1/2027-07/C1_MEDICAL_20270805.txt': ('H\n7\n', 'flat', 'payers_medical', 1, 3, '2027-08-05'),
        # stops inside its header line (CRLF file ending in a lone CR): truncated, not header-only
        'payers/WH-PAYER-C1/2027-02/C1_PHARMACY_20270305.txt': ('H1,H2\r', 'flat', 'payers_medical', 0, 2, '2027-03-05'),
        # CRLF file cut after the CR of its last record; the parser still counts 2 rows
        'payers/WH-PAYER-C1/2027-03/C1_PHARMACY_20270405.txt': ('H\r\n1\r\n2\r', 'flat', 'payers_medical', 2, 2, '2027-04-05'),
        'x12/835/WHN-PRACTICE-AK-0/2026-10/C1_835_202610.x12': ('ISA*...', 'x12', 'x12_835_claim', 1, 2, '2026-11-02'),
    }
    manifest = []
    land = sqlite3.connect(root / 'landing.sqlite')
    land.executescript(LANDING_DDL + '''
        CREATE TABLE x12_parse_issue(_feed, _instance, _drop, _file, _row, severity, segment_index, reason);
        CREATE TABLE payers_medical(_feed, _instance, _drop, _file, _row, claim_id, flat_row_issue);''')
    for rel, (content, parser, table, n, claimed, delivered) in files.items():
        if rel in skip:
            continue
        p = raw / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode())
        data = content.encode()
        import hashlib
        manifest.append({'path': 'raw/' + rel, 'sender': 'WH-PAYER-C1', 'format': 'x', 'records': claimed,
                         'delivered': delivered, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        tables = {table: n} if n else {}
        land.execute('INSERT INTO ingest_files VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (rel, rel.split('/')[0], parser, len(data), 1792110000.0, n, json.dumps(tables), None, 0.0, '2026-10-17'))
    land.execute("INSERT INTO x12_parse_issue VALUES ('x12','835','2026-10','x12/835/WHN-PRACTICE-AK-0/2026-10/C1_835_202610.x12',1,"
                 "'error',9,'missing_iea_at_eof')")
    land.execute("INSERT INTO x12_parse_issue VALUES ('x12','835','2026-10','x12/835/WHN-PRACTICE-AK-0/2026-10/C1_835_202610.x12',2,"
                 "'warning',9,'se_segment_count_mismatch:declared=9:actual=10')")
    land.commit()
    land.close()
    (raw / 'manifest.json').write_text(json.dumps({'files': manifest, 'summary': {}}))
    return raw


class DeliveryFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        raw = _delivery_fixture(root)
        cls.db = sqlite3.connect(root / 'semantic.sqlite3')
        cls.summary = delivery.build(cls.db, root / 'landing.sqlite', raw)
        cls.rows = {r['relpath'].rsplit('/', 1)[-1]: r for r in (dict(zip([c[0] for c in cur.description], row)) for cur in
                    [cls.db.execute('SELECT * FROM delivery_file')] for row in cur.fetchall())}

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def test_every_file_with_manifest_facts(self):
        self.assertEqual(self.summary['files'], 16)
        r = self.rows['C1_MEDICAL_20261005.txt']
        self.assertEqual((r['delivered_date'], r['claimed_records'], r['parsed_records'], r['in_manifest']), ('2026-10-05', 2, 2, 1))
        self.assertEqual((r['feed'], r['instance'], r['kind'], r['period'], r['cadence']),
                         ('payers', 'WH-PAYER-C1', 'C1_MEDICAL_#', '2026-09', 'month'))
        self.assertTrue(r['local_copy_time'].startswith('2026-'))

    def test_resend_and_v2(self):
        r = self.rows['C1_MEDICAL_20261005_RESEND.txt']
        self.assertEqual((r['is_resend'], r['resend_by_name'], r['is_original']), (1, 1, 0))
        self.assertTrue(r['content_identical_to'].endswith('C1_MEDICAL_20261005.txt'))
        v2 = self.rows['C1_MEDICAL_20261105_v2.txt']
        self.assertEqual((v2['is_v2_replacement'], v2['is_resend']), (1, 0))  # corrected copy, different bytes
        self.assertIsNone(v2['content_identical_to'])
        self.assertEqual(self.rows['C1_MEDICAL_20261005.txt']['is_resend'], 0)

    def test_truncated_empty_and_header_only(self):
        cut = self.rows['C1_MEDICAL_20270105.txt']
        self.assertEqual(cut['truncated'], 1)
        self.assertIn('flat_fewer_rows_than_claimed', cut['truncation_evidence'])
        self.assertIn('flat_no_final_newline', cut['truncation_evidence'])
        self.assertEqual(cut['claimed_vs_parsed_rows'], 3)
        hdr = self.rows['C1_MEDICAL_20270205.txt']
        self.assertEqual((hdr['header_only'], hdr['empty_file'], hdr['empty_or_header_only'], hdr['empty_where_expected']), (1, 0, 1, 1))
        empty = self.rows['C1_MEDICAL_20270305.txt']
        self.assertEqual((empty['empty_file'], empty['empty_where_expected'], empty['truncated']), (1, 1, 0))
        x12 = self.rows['C1_835_202610.x12']
        self.assertEqual((x12['truncated'], x12['truncation_evidence'], x12['parse_issue_count']), (1, 'x12_missing_iea_at_eof', 1))
        stale = self.rows['C1_MEDICAL_20270805.txt']
        self.assertEqual((stale['truncated'], stale['byte_records'], stale['claimed_records']), (0, 1, 3))
        self.assertTrue(stale['content_identical_to'].endswith('C1_MEDICAL_20270705.txt'))
        in_header = self.rows['C1_PHARMACY_20270305.txt']
        self.assertEqual((in_header['truncated'], in_header['truncation_evidence'], in_header['header_only'],
                          in_header['empty_where_expected']), (1, 'flat_cut_in_header', 0, 0))
        lone_cr = self.rows['C1_PHARMACY_20270405.txt']
        self.assertEqual((lone_cr['truncated'], lone_cr['byte_records']), (1, 1))
        self.assertIn('flat_no_final_newline', lone_cr['truncation_evidence'])
        self.assertIn('flat_fewer_rows_than_claimed', lone_cr['truncation_evidence'])  # counted from the bytes, not the parser

    def test_scan_lines(self):
        s = delivery.scan_lines
        self.assertEqual(s(b'a|b\r\n', 'flat'), {'complete': 1, 'records': 0, 'cut': False, 'header_only': True, 'cut_in_header': False})
        self.assertTrue(s(b'a|b', 'flat')['cut_in_header'])
        self.assertTrue(s(b'a|b\r', 'flat')['cut_in_header'])
        self.assertFalse(s(b'a|b\r', 'flat')['header_only'])
        self.assertEqual(s(b'h\r\n"x\ny"\r\n2\r\n', 'flat')['records'], 2)  # a bare LF inside a CRLF file is embedded
        self.assertTrue(s(b'h\r\n1\r\n2\r', 'flat')['cut'])
        self.assertEqual(s(b'{"a":1}\n{"a":2}\n{"a"', 'fhir'), {'complete': 2, 'records': 2, 'cut': True, 'header_only': False,
                                                              'cut_in_header': False})
        self.assertEqual(s(b'00\x1cT\nG1\x1cA\nG1\x1cB\n99\x1c2\n', 'ncpdp')['records'], 2)
        self.assertEqual(s(b'REC1      \nREC2      \nRE', 'fixed_width')['records'], 2)

    def test_lateness(self):
        r = self.rows['C1_MEDICAL_20270105.txt']
        self.assertEqual((r['name_date'], r['days_after_name_date'], r['late_vs_name_date']), ('2027-01-05', 20, 1))
        self.assertEqual(self.rows['C1_MEDICAL_20261005.txt']['late_vs_name_date'], 0)

    def test_series_gap(self):
        gaps = self.db.execute('SELECT series, missing_period, position, previous_period, next_period FROM delivery_gap').fetchall()
        self.assertEqual(gaps, [('payers|WH-PAYER-C1|C1_MEDICAL_#', '2026-11', 'inside', '2026-10', '2026-12')])
        self.assertEqual(self.rows['C1_MEDICAL_20270105.txt']['series_gap_before'], 1)

    def test_sender_skipping_a_whole_period_is_not_a_gap(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            raw = _delivery_fixture(root, skip={'payers/WH-PAYER-C1/2026-11/C1_ELIGIBILITY_20261205.txt'})
            out = sqlite3.connect(':memory:')
            delivery.build(out, root / 'landing.sqlite', raw)
            self.assertEqual(out.execute('SELECT count(*) FROM delivery_gap').fetchone()[0], 0)

    def test_without_manifest(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _delivery_fixture(root)
            out = sqlite3.connect(':memory:')
            s = delivery.build(out, root / 'landing.sqlite', None)
            self.assertFalse(s['manifest'])
            r = out.execute("SELECT delivered_date, claimed_records, in_manifest, truncated FROM delivery_file "
                            "WHERE relpath LIKE '%C1_835_202610.x12'").fetchone()
            self.assertEqual(r, (None, None, 0, 1))

    def test_path_kinds(self):
        d = delivery.describe_path('cms_cclf/2029-03/P.A9999.ACO.ZC1Y29.D290412_RESEND.T1596654')
        self.assertEqual((d['kind'], d['period']), ('P.A#.ACO.ZC1Y#.D#.T#', '2029-03'))
        d = delivery.describe_path('ehr/athena/2186/2026-06/CHART.csv')
        self.assertEqual((d['instance'], d['kind'], d['period']), ('athena/2186', 'CHART', '2026-06'))
        d = delivery.describe_path('x12_payer/WH-PAYER-0/2026-W36/0_837I_2026-W36_resend.x12')
        self.assertEqual((d['kind'], d['cadence']), ('0_837I_#', 'week'))


class ParityTests(unittest.TestCase):
    def test_flags_gap_with_source_named_and_skips_unmodeled_kinds(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            land = sqlite3.connect(root / 'landing.sqlite')
            land.executescript('''CREATE TABLE fhir_observation(_feed, _instance, _drop, _file, _row, patient_id, category_code);
                                  CREATE TABLE ehr_epic_order_results(_feed, _instance, _drop, _file, _row, pat_id, component_id);''')
            fa, fb = 'ehr/fhir/PRAC-A/2028-03/Observation.ndjson', 'ehr/fhir/PRAC-B/2028-03/Observation.ndjson'
            obs = [('ehr', 'fhir/PRAC-A', '2028-03', fa, i, f'pa{i % 10}', 'laboratory') for i in range(40)]
            obs += [('ehr', 'fhir/PRAC-A', '2028-03', fa, 100 + i, f'pa{i}', 'social-history') for i in range(5)]
            obs += [('ehr', 'fhir/PRAC-B', '2028-03', fb, i, f'pb{i % 10}', 'laboratory') for i in range(40)]
            land.executemany('INSERT INTO fhir_observation VALUES (?,?,?,?,?,?,?)', obs)
            fe = 'ehr/epic/EPIC-AR/2029-01/order_results.txt'
            land.executemany('INSERT INTO ehr_epic_order_results VALUES (?,?,?,?,?,?,?)',
                             [('ehr', 'epic/EPIC-AR', '2029-01', fe, i, f'e{i % 5}', 'c1') for i in range(30)])
            land.commit()
            land.close()
            recs = []
            for i in range(40):  # PRAC-A fully modeled
                recs.append((f'a{i}', f'P{i % 10}', 'labs', '2028-03-01', '2028-03-01', None,
                             {'_lineage': [{'table': 'fhir_observation', 'file': fa, 'row': i}]}))
            for i in range(20):  # PRAC-B half modeled
                recs.append((f'b{i}', f'Q{i % 10}', 'labs', '2028-03-01', '2028-03-01', None,
                             {'_lineage': [{'table': 'fhir_observation', 'file': fb, 'row': i}]}))
            for i in range(30):  # Epic: claims-style lineage keys (_file/_row)
                recs.append((f'e{i}', f'E{i % 5}', 'labs', '2029-01-01', '2029-01-01', None,
                             {'_lineage': [{'table': 'ehr_epic_order_results', '_file': fe, '_row': i}]}))
            _warehouse(root / 'warehouse.sqlite3', recs)
            report = parity.check(root / 'landing.sqlite', root / 'missing.sqlite3', root / 'warehouse.sqlite3', families=['labs'])
            groups = {(g['table'], g['instance']): g for g in report['groups']}
            a = groups[('fhir_observation', 'fhir/PRAC-A')]
            self.assertEqual((a['landing_rows'], a['covered_rows'], a['rows_of_unmodeled_kinds'], a['flagged']), (40, 40, 5, False))
            b = groups[('fhir_observation', 'fhir/PRAC-B')]
            self.assertEqual((b['landing_rows'], b['covered_rows'], b['row_gap'], b['flagged'], b['year']), (40, 20, 0.5, True, '2028'))
            self.assertEqual((b['landing_persons'], b['covered_persons'], b['modeled_persons']), (10, 10, 10))
            e = groups[('ehr_epic_order_results', 'epic/EPIC-AR')]
            self.assertEqual((e['covered_rows'], e['flagged']), (30, False))
            self.assertEqual(report['flagged_count'], 1)
            self.assertIn('ehr/fhir/PRAC-B/2028 fhir_observation [labs] rows 20/40', report['flagged'][0])
            self.assertEqual(report['unmodeled_kinds'][0]['kinds'], ['social-history'])
            self.assertEqual(report['lineage_source'], 'received warehouse')


class NativeWorkspaceWiringTests(unittest.TestCase):
    """delivery_file lands in the native build step and source_parity in verify, annotating without blocking."""

    def test_build_and_verify(self):
        from test_workspace_stack import native_workspace
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / 'workspaces'
            ws = native_workspace(root)
            raw = _delivery_fixture(ws.dir)
            state = ws.state
            state['source'] = 'native://' + str(raw)
            ws._save(state)
            (ws.dir / 'stage.json').write_text(json.dumps({'people': 30, 'records': 50, 'issues': 0, 'identity': {}, 'streams': {}}))
            ws._build_native()
            db = sqlite3.connect(ws.semantic_path)
            n = db.execute('SELECT count(*) FROM delivery_file').fetchone()[0]
            meta = dict(db.execute('SELECT key, value FROM meta'))
            db.close()
            self.assertEqual(n, 16)
            self.assertIn('delivery_file', meta)
            self.assertEqual(ws.state['steps']['build']['delivery']['files'], 16)
            ws._do_verify(None)
            v = json.loads((ws.dir / 'verification.json').read_text())
            par = next(c for c in v['checks'] if c['name'] == 'source_parity')
            self.assertTrue(par['annotation'])
            self.assertTrue(v['passed'])  # the annotation never fails verification
            self.assertTrue((ws.dir / 'parity.json').is_file())

    def test_delivery_failure_does_not_break_build(self):
        from test_workspace_stack import native_workspace
        with tempfile.TemporaryDirectory() as t:
            ws = native_workspace(Path(t) / 'workspaces', slug='no-landing')
            ws._build_native()  # no landing.sqlite: the delivery tables are skipped and the error recorded
            self.assertIn('error', ws.state['steps']['build']['delivery'])
            self.assertTrue(ws.semantic_path.is_file())


if __name__ == '__main__':
    unittest.main()
