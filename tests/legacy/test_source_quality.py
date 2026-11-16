"""Source-defect detectors (dq_detectors) and feed sequence gaps (feed_gaps): tiny fixtures, no model calls."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
from clinical_core.agere.clinical import dq_detectors as dq, feed_gaps as fg, foundation_engines, analysis_graph, graph_contracts, capability_registry, graph_planner
from clinical_core.agere.clinical.economical_planner import local_plan


def fixture(root):
    db = sqlite3.connect(Path(root)/'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE meta(key TEXT, value TEXT);
    INSERT INTO meta VALUES ('source_digest','abc'),('as_of','2030-05-27');
    CREATE TABLE patient(patient_id TEXT, birth_date TEXT, zip TEXT);
    CREATE TABLE organization(organization_id TEXT);
    CREATE TABLE claim(claim_id TEXT, patient_id TEXT, service_date TEXT, first_paid_date TEXT, principal_diagnosis TEXT,
                       allowed_cents INTEGER, service_organization_id TEXT);
    CREATE TABLE encounter(record_id TEXT, encounter_id TEXT, patient_id TEXT, type TEXT, event_date TEXT, end_date TEXT, organization_id TEXT);
    CREATE TABLE adt_visit(visit_number TEXT, patient_id TEXT, admit_date TEXT, discharge_date TEXT, discharge_before_admit INTEGER);
    CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT, organization_id TEXT, payload TEXT);
    ''')
    db.executemany('INSERT INTO patient VALUES (?,?,?)', [(f'P{i}', '1970-01-01', '12345' if i % 50 else '1234') for i in range(200)])
    db.execute("INSERT INTO organization VALUES ('ORG-1')")
    rows = []
    for i in range(300):
        rows.append((f'C{i}', f'P{i % 200}', '2029-03-01', '2029-03-20' if i % 100 else '2029-02-01', 'E11.65' if i % 30 else 'E1165',
                     -500 if i == 7 else 1000 + i, 'ORG-1' if i % 75 else 'ORG-9'))
    rows.append(('C5', 'P5', '2029-03-01', '2029-03-20', 'E11.65', 1000, 'ORG-1'))  # duplicate claim id
    db.executemany('INSERT INTO claim VALUES (?,?,?,?,?,?,?)', rows)
    enc = []
    for i in range(120):
        for v in range(2):  # two versions of each encounter
            enc.append((f'R{i}-{v}', f'E{i}', f'P{i}' if not (i == 3 and v == 1) else 'P199', 'office', '2029-01-05', '2029-01-05', 'ORG-1'))
    db.executemany('INSERT INTO encounter VALUES (?,?,?,?,?,?,?)', enc)
    db.executemany('INSERT INTO adt_visit VALUES (?,?,?,?,?)', [(f'V{i}', f'P{i}', '2029-05-10', '2029-05-08' if i < 2 else '2029-05-12', int(i < 2)) for i in range(30)])
    recs = []
    months = [f'2029-{m:02d}' for m in range(1, 13)] + [f'2030-{m:02d}' for m in range(1, 6)]
    n = 0
    for m in months:
        k = 0 if m == '2029-06' else 5 if m == '2029-09' else 40  # June missing, September partial
        for j in range(k):
            n += 1; recs.append((f'L{n}', 'P1', 'labs', m + '-10', None, m + '-12', 'ORG-1', '{}'))
        late = 30 if m == '2029-09' else 0  # September's records arrive later in October (late delivery recovers it)
        for j in range(late):
            n += 1; recs.append((f'L{n}', 'P1', 'labs', m + '-11', None, '2029-10-20', 'ORG-1', '{}'))
    db.executemany('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)', recs)
    db.commit(); db.close()


class Detectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); fixture(cls.tmp.name); cls.path = Path(cls.tmp.name)
        cls.report = dq.detect(cls.path, refresh=True)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def find(self, detector, table):
        return [f for f in self.report['findings'] if f['detector'] == detector and f['table'] == table]

    def test_each_strategy_finds_its_planted_defect(self):
        self.assertEqual(self.find('duplicate_key', 'claim')[0]['count'], 1)
        self.assertEqual(self.find('date_order', 'claim')[0]['count'], 3)       # first_paid_date before service_date
        self.assertEqual(self.find('pattern', 'claim')[0]['count'], 10)          # dotless ICD-10
        self.assertEqual(self.find('out_of_range', 'claim')[0]['count'], 1)      # negative allowed
        self.assertEqual(self.find('orphan_key', 'claim')[0]['count'], 4)        # ORG-9
        self.assertEqual(self.find('pattern', 'patient')[0]['count'], 4)         # 4-digit ZIP
        self.assertEqual(self.find('source_flag', 'adt_visit')[0]['count'], 2)
        self.assertEqual(self.find('date_order', 'adt_visit')[0]['count'], 2)
        fd = self.find('fd_violation', 'encounter')
        self.assertTrue(fd and fd[0]['columns'] == ['encounter_id', 'patient_id'] and fd[0]['count'] == 1)

    def test_cache_and_engine(self):
        self.assertTrue((self.path/dq.CACHE).exists())
        self.assertEqual(dq.detect(self.path)['findings'], self.report['findings'])
        r = dq.execute(self.path, None, {'tables': ['claim'], 'detectors': [], 'output': 'findings', 'min_count': 1}, '2030-05-27')
        a = dq.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(a['count'], 6); self.assertIn('source defect', a['headline'])
        with self.assertRaises(ValueError): dq.validate({'tables': ['nope']})

    def test_annotation_of_an_answer_that_reads_a_defective_table(self):
        graph = {'nodes': [{'operation': 'sql', 'parameters': {'query': 'SELECT count(*) FROM claim c JOIN patient p USING (patient_id)'}}]}
        msgs = dq.annotate(self.path, 'How many claims?', graph, {})
        self.assertTrue(any('Source defects in claim' in m for m in msgs))
        self.assertEqual(dq.annotate(self.path, 'q', {'nodes': [{'operation': 'sql', 'parameters': {'query': 'SELECT 1 FROM organization'}}]}, {}), [])


class Gaps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); fixture(cls.tmp.name); cls.path = Path(cls.tmp.name)
        cls.report = fg.detect(cls.path, refresh=True)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_missing_and_partial_months_with_recovery(self):
        by = {g['month']: g for g in self.report['gaps']}
        self.assertEqual(by['2029-06']['kind'], 'missing_month'); self.assertEqual(by['2029-06']['status'], 'unrecovered')
        self.assertEqual(by['2029-09']['kind'], 'partial_month'); self.assertEqual(by['2029-09']['status'], 'recovered_late_delivery')
        self.assertNotIn('2030-05', by)  # the snapshot month is still arriving

    def test_series_contract_needs_a_periodic_feed(self):
        self.assertEqual(fg.series_gaps({'2029-01': 50, '2029-02': 50}, {}, '2030-01'), (False, []))

    def test_engine_and_annotation(self):
        r = fg.execute(self.path, None, {'period': {'start': '2029-04-01', 'end': '2029-06-30'}}, '2030-05-27')
        self.assertEqual([g['month'] for g in r['gaps']], ['2029-06'])
        a = fg.answer(r, {}, 'q'); self.assertEqual(a['count'], 1); self.assertIn('not recovered', a['headline'])
        msgs = fg.annotate_tables(self.path, {'measurement'}, ["SELECT count(*) FROM measurement WHERE event_date LIKE '2029-06%'"])
        self.assertTrue(msgs and 'labs' in msgs[0])
        self.assertEqual(fg.annotate_tables(self.path, {'measurement'}, ["SELECT 1 WHERE d LIKE '2029-09%'"]), [])  # recovered

    def test_manifest_gaps_reuse_delivery_drop(self):
        with tempfile.TemporaryDirectory() as raw:
            files = [{'path': f'raw/labs/f{m}.csv', 'delivered': f'2029-{m:02d}-03'} for m in range(1, 13) if m != 7 for _ in range(6)]
            Path(raw, 'manifest.json').write_text(json.dumps({'files': files}))
            gaps = fg.manifest_gaps(raw, '2030-01')
            self.assertEqual([(g['stream'], g['month'], g['kind']) for g in gaps], [('labs', '2029-07', 'missing_file')])


class Routing(unittest.TestCase):
    def test_registered_on_the_planner_menu(self):
        for op in ('source_defects', 'feed_gaps'):
            self.assertIn(op, analysis_graph.OPERATIONS); self.assertIn(op, graph_contracts.TYPED)
            self.assertIn(op, capability_registry.PRODUCT); self.assertIn(op, graph_planner.CONTRACTS)

    def test_exact_local_questions(self):
        g, route = local_plan('What data quality problems are in our claims feed?', '2030-05-27')
        self.assertEqual(route, 'exact_foundation_source_defects'); self.assertIn('payer_claim', g['nodes'][0]['parameters']['tables'])
        g, route = local_plan('Are there encounters where discharge is before admission?', '2030-05-27')
        self.assertEqual(g['nodes'][0]['parameters']['detectors'], ['date_order', 'source_flag'])
        g, route = local_plan('Did any feed arrive incomplete last quarter?', '2030-05-27')
        self.assertEqual(route, 'exact_foundation_feed_gaps')
        self.assertEqual(g['nodes'][0]['parameters']['period'], {'start': '2030-01-01', 'end': '2030-03-31'})
        g, _ = local_plan('Which sources have missing months of data?', '2030-05-27')
        self.assertEqual(g['nodes'][0]['parameters']['output'], 'by_stream')

    def test_qualifiers_it_cannot_apply_are_not_claimed(self):
        self.assertIsNone(dq.local_question('What data quality problems are in claims for practice 4?'))
        self.assertIsNone(fg.local_question('Did the labs feed from Quest arrive incomplete?', '2030-05-27'))


if __name__ == '__main__':
    unittest.main()
