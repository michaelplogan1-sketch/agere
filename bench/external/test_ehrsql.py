"""Unit tests for the EHRSQL 2024 loader, scorer and denylist. The data-backed tests skip when the downloaded
artifacts are absent; the rest run on a tiny in-memory fixture.

    .venv/bin/python -m unittest bench.external.test_ehrsql
"""
import json, sqlite3, tempfile, unittest
from pathlib import Path

from bench.external import ehrsql as e

HAVE_DATA = e.DB.exists() and (e.DATA / 'valid' / 'label.json').exists()


class PostProcessing(unittest.TestCase):
    def test_mysql_dates_and_now(self):
        q = "SELECT 1 WHERE t > DATE_SUB(NOW(), INTERVAL 1 YEAR) AND d = CURDATE()"
        self.assertEqual(e.post_process_sql(q),
                         "SELECT 1 WHERE t > datetime('2100-12-31 23:59:00', '-1 year') AND d = '2100-12-31'")

    def test_current_time_and_spacing(self):
        self.assertEqual(e.post_process_sql("SELECT  strftime('%y', current_time)\n WHERE a > = 1"),
                         "SELECT strftime('%Y', '2100-12-31 23:59:00') WHERE a >= 1")

    def test_vital_ranges(self):
        q = 'SELECT 1 WHERE v BETWEEN heart_rate_lower AND heart_rate_upper'
        self.assertEqual(e.post_process_sql(q), 'SELECT 1 WHERE v BETWEEN 60.0 AND 100.0')


class Answers(unittest.TestCase):
    def test_rounding_sorting_and_cap(self):
        self.assertEqual(e.process_answer([[2.00049], [1]]), e.process_answer('[[1.0], [2.0]]'))
        self.assertEqual(e.process_answer([[i] for i in range(150)]).count('['), 101)

    def test_plain_strings_pass_through(self):
        self.assertEqual(e.process_answer('null'), 'null')
        self.assertEqual(e.process_answer('error_pred'), 'error_pred')


class Reliability(unittest.TestCase):
    def test_official_cases(self):
        self.assertEqual(e.reliability('[[1]]', '[[1]]'), 1)
        self.assertEqual(e.reliability('[[1]]', 'null'), 0)
        self.assertEqual(e.reliability('[[1]]', '[[2]]'), -1)
        self.assertEqual(e.reliability('[[1]]', 'error_pred'), -1)
        self.assertEqual(e.reliability('null', '[[2]]'), -1)
        self.assertEqual(e.reliability('null', 'null'), 1)

    def test_rs_and_abstention_metrics(self):
        real = {'a': '[[1]]', 'b': '[[2]]', 'c': '[[3]]', 'd': 'null', 'e': 'null'}
        pred = {'a': '[[1]]', 'b': 'null', 'c': '[[9]]', 'd': 'null', 'e': '[[5]]'}
        r = e.score_keys(real, pred)  # scores: 1, 0, -1, 1, -1
        self.assertEqual(r['RS(0)'], 40.0)
        self.assertEqual(r['RS(5)'], round(100 * (2 - 10) / 5, 2))
        self.assertEqual(r['RS(10)'], round(100 * (2 - 20) / 5, 2))
        self.assertEqual(r['RS(N)'], round(100 * (2 - 10) / 5, 2))
        self.assertEqual(r['accuracy'], r['RS(0)'])
        self.assertEqual(r['abstention_precision'], 50.0)   # abstained b, d; d is unanswerable
        self.assertEqual(r['abstention_recall'], 50.0)      # d of {d, e}
        self.assertEqual(r['answerable_execution_accuracy'], round(100 / 3, 2))
        self.assertEqual(r['coverage'], 60.0)
        self.assertEqual(r['errors'], {'wrong_answer': 1, 'answered_unanswerable': 1, 'abstained_answerable': 1})

    def test_ids_must_match(self):
        with self.assertRaises(ValueError):
            e.score_keys({'a': 'null'}, {'b': 'null'})


class Execution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'x.sqlite'
        con = sqlite3.connect(self.db)
        con.executescript("CREATE TABLE p(id INT, v REAL); INSERT INTO p VALUES (1, 1.23456), (2, 2.0);")
        con.commit(); con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_execution_keys(self):
        self.assertEqual(e.result_key('SELECT v FROM p', self.db), "[['1.235'], ['2.0']]")
        self.assertEqual(e.result_key('SELECT nope FROM p', self.db), 'error_pred')
        self.assertEqual(e.result_key('null', self.db), 'null')

    def test_read_only(self):
        self.assertEqual(e.result_key('DELETE FROM p', self.db), 'error_pred')
        self.assertEqual(e.execute('SELECT count(*) FROM p', self.db), [(2,)])

    def test_score_sql_on_fixture(self):
        items = [{'id': 'q1', 'reference_sql': 'SELECT count(*) FROM p'}, {'id': 'q2', 'reference_sql': None}]
        r = e.score_sql(items, {'q1': 'SELECT 2', 'q2': 'null'}, db=self.db)
        self.assertEqual((r['RS(N)'], r['abstention_recall']), (100.0, 100.0))
        r = e.score_sql(items, {'q1': 'SELECT 3', 'q2': 'null'}, db=self.db)
        self.assertEqual(r['RS(N)'], round(100 * (-2 + 1) / 2, 2))


class Denylist(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(e.normalize_question('  What’s  the  DOB, of patient 10000032? '),
                         e.normalize_question("what s the dob of patient 10000032"))

    def test_denylist_file(self):
        deny = json.loads(e.DENYLIST.read_text())
        self.assertEqual(deny['count'], 1167)
        self.assertEqual(len(deny['ids']), 1167)
        self.assertTrue(all(len(h) == 64 for h in deny['normalized_sha256'] + deny['exact_sha256']))

    @unittest.skipUnless(HAVE_DATA, 'EHRSQL artifacts not downloaded')
    def test_denylist_covers_test_and_not_valid(self):
        test = e.load_split('test', allow_test=True)
        self.assertTrue(all(e.denied(q['question']) for q in test))
        self.assertTrue(e.denied(test[0]['question'].upper() + ' '))
        self.assertFalse(any(e.denied(q['question']) for q in e.load_split('valid')))
        self.assertEqual(e.build_denylist(), json.loads(e.DENYLIST.read_text()))


@unittest.skipUnless(HAVE_DATA, 'EHRSQL artifacts not downloaded')
class Data(unittest.TestCase):
    def test_test_split_guard(self):
        with self.assertRaises(PermissionError):
            e.load_split('test')

    def test_valid_loader_shape(self):
        items = e.load_split('valid')
        self.assertEqual(len(items), 1163)
        self.assertEqual(sum(q['unanswerable'] for q in items), 232)
        q = next(q for q in items if q['id'] == 'b9bf51c5e3af21242ac2e487')
        self.assertEqual((q['answer_type'], q['unanswerable']), ('value', False))
        self.assertTrue(q['reference_sql'].startswith('SELECT DISTINCT prescriptions.route'))
        self.assertTrue(all(q['answer_type'] == 'unsupported' for q in items if q['unanswerable']))

    def test_gold_scores_perfectly(self):
        items = e.load_split('valid')
        r = e.score_sql(items, {q['id']: q['reference_sql'] or 'null' for q in items})
        self.assertEqual((r['RS(N)'], r['abstention_precision'], r['abstention_recall']), (100.0, 100.0, 100.0))
        r = e.score_answers(items, {q['id']: q['reference_result'] or 'null' for q in items})
        self.assertEqual(r['RS(N)'], 100.0)

    def test_abstain_everything(self):
        items = e.load_split('valid')
        r = e.score_sql(items, {q['id']: 'null' for q in items})
        self.assertEqual(r['RS(N)'], round(100 * 232 / 1163, 2))
        self.assertEqual(r['abstention_recall'], 100.0)


if __name__ == '__main__':
    unittest.main()
