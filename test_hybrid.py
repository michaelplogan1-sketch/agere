import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from clinical_core.agere.hybrid import contract as hc, execute as hx, vote as hv
from bench import hybrid_run

Q = {'id': 'X-1', 'answer_type': 'count', 'as_of': '2030-05-27'}


def contract(**kw):
    c = {'answer_type': 'count', 'grain': {'entity': 'person', 'key': 'person'}, 'population': 'all', 'numerator': 'stays',
         'denominator': None, 'window': {'start': '2028-01-01', 'end': '2028-12-31', 'anchor': 'stay.admit_date'},
         'as_of': '2030-05-27', 'perspective': 'claims', 'exclusions': [], 'group_by': [], 'unit': 'count',
         'metric_id': None, 'unresolved': []}
    c.update(kw)
    return c


class Db(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 's.sqlite3'
        con = sqlite3.connect(self.db)
        con.executescript("""CREATE TABLE patient(patient_id TEXT);
            INSERT INTO patient VALUES ('P1'),('P2'),('P3');
            CREATE TABLE stay(patient_id TEXT, type TEXT, admit_date TEXT);
            INSERT INTO stay VALUES ('P1','observation','2028-02-01'),('P2','observation','2028-03-01'),('P2','inpatient','2028-03-05');""")
        con.commit(); con.close()

    def tearDown(self):
        self.tmp.cleanup()


class ContractValidation(unittest.TestCase):
    def test_valid_contract(self):
        self.assertEqual(hc.validate(contract(), Q), [])

    def test_problems_are_named(self):
        bad = contract(answer_type='value', unit='percent', denominator=None, as_of='2030-01-01',
                       window={'start': '2029-01-01', 'end': '2028-01-01', 'anchor': None})
        issues = hc.validate(bad, Q)
        for s in ('question asks for count', 'needs a denominator', 'starts after it ends', 'as_of', 'no anchor'):
            self.assertTrue(any(s in i for i in issues), (s, issues))

    def test_table_needs_group_by(self):
        self.assertTrue(any('group_by' in i for i in hc.validate(contract(answer_type='table'), {**Q, 'answer_type': 'table'})))

    def test_unresolved_means_clarification(self):
        self.assertTrue(hc.needs_clarification(contract(unresolved=['which year'])))


class Execution(Db):
    def test_read_only_single_statement(self):
        for sql in ("DELETE FROM stay", "SELECT 1; DROP TABLE stay", "PRAGMA table_info(stay)", None):
            self.assertIsNotNone(hx.run_sql(self.db, sql)[2], sql)
        self.assertIsNone(hx.run_sql(self.db, "SELECT 'delete me' AS x")[2])
        self.assertIsNone(hx.run_sql(self.db, "SELECT replace('I48.0', '.', '') AS code")[2])
        self.assertIsNotNone(hx.run_sql(self.db, "WITH x AS (SELECT 1) DELETE FROM stay")[2])  # read-only connection

    def test_count_shape_and_checks(self):
        cols, rows, err = hx.run_sql(self.db, "SELECT count(DISTINCT patient_id) n FROM stay")
        ans, why = hx.to_answer(contract(), cols, rows)
        self.assertEqual(ans, {'count': 2})
        self.assertEqual(hx.checks(contract(), ans), [])
        cols, rows, _ = hx.run_sql(self.db, "SELECT patient_id, count(*) FROM stay GROUP BY 1")
        self.assertIsNone(hx.to_answer(contract(), cols, rows)[0])

    def test_patient_set_checks_grain_and_ids(self):
        c = contract(answer_type='patient_set')
        cols, rows, _ = hx.run_sql(self.db, "SELECT patient_id FROM stay")
        ans, _ = hx.to_answer(c, cols, rows)
        self.assertTrue(any('duplicate' in p for p in hx.checks(c, ans, self.db)))
        self.assertTrue(any('not patients' in p for p in hx.checks(c, {'patient_ids': ['ZZ']}, self.db)))
        self.assertEqual(hx.checks(c, {'patient_ids': []}, self.db), [])  # a correctly empty set passes

    def test_table_uses_contract_group_by(self):
        c = contract(answer_type='table', group_by=['type'])
        cols, rows, _ = hx.run_sql(self.db, "SELECT type, count(*) AS n FROM stay GROUP BY type ORDER BY type")
        ans, _ = hx.to_answer(c, cols, rows)
        self.assertEqual(ans['table'], [{'keys': ['inpatient'], 'value': 1}, {'keys': ['observation'], 'value': 2}])
        self.assertIsNone(hx.to_answer(contract(answer_type='table', group_by=['month']), cols, rows)[0])

    def test_top_n_row_limit(self):
        c = contract(answer_type='table', group_by=['type'], row_limit=1)
        ans = {'table': [{'keys': ['a'], 'value': 2}, {'keys': ['b'], 'value': 1}]}
        self.assertTrue(any('top-1' in p for p in hx.checks(c, ans)))
        self.assertTrue(any('row_limit' in i for i in hc.validate(contract(row_limit=0), Q)))

    def test_percent_range_check(self):
        c = contract(answer_type='value', unit='percent', denominator='all')
        self.assertTrue(hx.checks(c, {'value': 140.0}))

    def test_governed_metric_used_only_when_it_matches(self):
        c = contract(metric_id='observation_stays', grain={'entity': 'stay', 'key': 'patient+admit_date'})
        sql, why = hx.compile_metric(c, '2030-05-27')
        self.assertIsNone(why)
        self.assertIn("'2028-01-01' AND '2028-12-31'", sql)
        sql, why = hx.compile_metric(contract(metric_id='observation_stays'), '2030-05-27')  # person grain
        self.assertIsNone(sql)
        self.assertIn('counts stay', why)
        c = contract(metric_id='observation_stays', grain={'entity': 'stay', 'key': 'k'},
                     window={'start': '2028-01-01', 'end': '2028-12-31', 'anchor': 'claim_line.service_date'})
        self.assertIn('anchors on', hx.compile_metric(c, '2030-05-27')[1])


class Voting(unittest.TestCase):
    def runs(self, *vals, passed=True):
        return [{'answer': {'count': v}, 'passed': passed, 'clarify': False} for v in vals]

    def test_decisions(self):
        self.assertEqual(hv.decide('count', self.runs(5, 5, 5))[0], 'unanimous')
        self.assertEqual(hv.decide('count', self.runs(5, 5, 6))[0], 'majority')
        self.assertEqual(hv.decide('count', self.runs(5, 6, 7))[0], 'escalate')
        self.assertEqual(hv.decide('count', self.runs(5, 5, 5, passed=False))[0], 'abstain')

    def test_failed_runs_do_not_vote(self):
        runs = self.runs(5, 5) + [{'answer': None, 'passed': False, 'clarify': False}]
        self.assertEqual(hv.decide('count', runs)[:2], ('majority', {'count': 5}))

    def test_counts_compare_exactly(self):
        self.assertNotEqual(hv.canonical('count', {'count': 21256}), hv.canonical('count', {'count': 21259}))
        self.assertEqual(hv.canonical('value', {'value': 38.12001}), hv.canonical('value', {'value': 38.12}))

    def test_canonical_forms(self):
        self.assertEqual(hv.canonical('patient_set', {'patient_ids': ['b', 'a']}), hv.canonical('patient_set', {'patient_ids': ['a', 'b', 'a']}))
        t1 = {'table': [{'keys': ['x'], 'value': 1.00001}, {'keys': ['y'], 'value': 2}]}
        t2 = {'table': [{'keys': ['y'], 'value': 2.0}, {'keys': ['x'], 'value': 1}]}
        self.assertEqual(hv.canonical('table', t1), hv.canonical('table', t2))


class Runner(Db):
    def test_execute_end_to_end(self):
        run = Path(self.tmp.name) / 'H'
        (run / 'model').mkdir(parents=True)
        qf = run / 'q.jsonl'
        qf.write_text(json.dumps({'id': 'X-1', 'answer_type': 'count', 'question': 'How many? Use data received as of 2030-05-27.'}) + '\n')
        line = lambda sql: json.dumps({'id': 'X-1', 'contract': contract(), 'sql': sql}) + '\n'
        (run / 'model/run1_b1.jsonl').write_text(line("SELECT count(DISTINCT patient_id) FROM stay"))
        (run / 'model/run2_b1.jsonl').write_text(line("SELECT count(DISTINCT patient_id) AS n FROM stay WHERE admit_date >= '2028-01-01'"))
        (run / 'model/run3_b1.jsonl').write_text(line("SELECT count(*) FROM stay"))
        hybrid_run.main(['execute', str(run), '--questions', str(qf), '--db', str(self.db)])
        a = json.loads((run / 'answers_hybrid.jsonl').read_text())
        self.assertEqual((a['status'], a['count']), ('answered', 2))
        self.assertIn('majority', a['interpretation'])


if __name__ == '__main__':
    unittest.main()
