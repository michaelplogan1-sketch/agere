"""Acceptance of a semantic-analyst proposal (analyst_acceptance, legacy_proposers.analyst_graph, sql_query definition views).

A correct analyst answer built on the definition views must pass the checks every proposal passes; a wrong one (missing threshold, window, rank
direction) must still fail; no other tier is read more leniently. Synthetic SQL only; no stack, no model, no key."""
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import acceptance_contract as AC
from clinical_core.agere.clinical import analyst_acceptance as AA
from clinical_core.agere.clinical import decision_context as DC
from clinical_core.agere.clinical import legacy_proposers as L
from clinical_core.agere.clinical import literal_check, proposers, sql_query

AS_OF = '2030-05-27'
Q_SHARE = 'What share of our adult diabetic patients have an A1c above 9 on their latest result?'
NUM = ("SELECT COUNT(DISTINCT ll.patient_id) FROM latest_lab ll JOIN condition_member cm ON cm.patient_id = ll.patient_id AND cm.concept = 'diabetes' "
       "JOIN living_patient lp ON lp.patient_id = ll.patient_id WHERE ll.concept = 'hba1c' AND ll.value > 9 AND lp.age >= 18")
DEN = ("SELECT COUNT(DISTINCT cm.patient_id) FROM condition_member cm JOIN living_patient lp ON lp.patient_id = cm.patient_id "
       "WHERE cm.concept = 'diabetes' AND lp.age >= 18")
RATIO = {'numerator': 10, 'denominator': 40, 'numerator_sql': NUM, 'denominator_sql': DEN, 'scale': 'percent'}


def graph_of(query, returns='value'):
    return {'interpretation': 'x', 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': query, 'returns': returns}, 'purpose': 'p'}]}


def receipt_for(*queries):
    return {'route': 'semantic_analyst', 'analyst_evidence': {'queries': list(queries), 'composed': True, 'views': []}}


def plan_gaps(question, query, receipt=None, returns='value'):
    graph = graph_of(query, returns)
    return AC.evaluate(question, graph=graph, as_of=AS_OF, phase='plan', extra_text=AC.analyst_text(receipt, graph))['unmet']


class Evidence(unittest.TestCase):
    def test_composed_share_carries_numerator_and_denominator(self):
        both = L.ratio_sql(RATIO)
        self.assertIn(NUM, both)
        self.assertIn(DEN, both)
        self.assertRegex(both, r'NULLIF\(\(')
        self.assertIsNone(L.ratio_sql({**RATIO, 'denominator_sql': None}))
        self.assertIsNone(L.ratio_sql({**RATIO, 'scale': 'bogus'}))

    def test_composed_share_computes_the_stated_value(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE t(x)')
        db.executemany('INSERT INTO t VALUES (?)', [(i,) for i in range(40)])
        ratio = {'numerator_sql': 'SELECT COUNT(*) FROM t WHERE x < 10', 'denominator_sql': 'SELECT COUNT(*) FROM t;', 'scale': 'percent'}
        self.assertEqual(db.execute(L.ratio_sql(ratio)).fetchone()[0], 25.0)
        ratio['scale'] = 'fraction'
        self.assertEqual(db.execute(L.ratio_sql(ratio)).fetchone()[0], 0.25)

    def test_the_graph_is_built_from_all_the_sql_not_the_last_audit_query(self):
        calls = [{'sql': NUM, 'result': {'rows': 1}}, {'sql': NUM, 'result': {'value': 10}, 'audit': 'numerator'}, {'sql': DEN, 'result': {'value': 40}, 'audit': 'denominator'}]
        found = L.analyst_queries({'ratio': RATIO, 'sql': NUM}, calls)
        self.assertEqual(found['queries'], [NUM, DEN])
        self.assertEqual(found['composed'], L.ratio_sql(RATIO))
        # without a ratio the answer is read from its own query: an audit-rewritten one first, else the last successful one
        found = L.analyst_queries({'sql': 'SELECT 1'}, [{'sql': 'SELECT 2'}, {'sql': 'SELECT 3', 'result': {'error': 'x'}}])
        self.assertEqual(found['composed'], 'SELECT 2')
        found = L.analyst_queries({'sql': 'SELECT 1'}, [{'sql': 'SELECT 2'}, {'sql': 'SELECT 4', 'result': {'value': 1}, 'audit': 'answer'}])
        self.assertEqual(found['composed'], 'SELECT 4')

    def test_analyst_graph_binds_the_audit_to_the_graph(self):
        columns = {'patient': ['patient_id', 'birth_date'], 'death': ['patient_id']}
        receipt = {'route': 'semantic_analyst', 'definitions_audit': {'action': 'ok', 'rewrites': [], 'arithmetic': None}}
        graph, out = L.analyst_graph(Q_SHARE, {'ratio': RATIO, 'sql': NUM}, [], {'answer_type': 'value'}, AS_OF, columns, receipt)
        self.assertIsNotNone(graph, out)
        self.assertEqual(out['definitions_audit']['final_sql_digest'], AC.sql_digest(graph))
        self.assertTrue(AC.audit_bound(out, graph))
        self.assertEqual(out['analyst_evidence']['views'], ['condition_member', 'latest_lab', 'living_patient'])
        self.assertEqual(graph['nodes'][0]['parameters']['returns'], 'value')

    def test_a_query_that_departs_from_the_definitions_makes_no_proposal(self):
        columns = {'patient': ['patient_id', 'birth_date', 'age_reported'], 'death': ['patient_id']}
        receipt = {'route': 'semantic_analyst', 'definitions_audit': {'action': 'ok', 'rewrites': [], 'arithmetic': None}}
        bad = 'SELECT COUNT(*) FROM living_patient WHERE age_reported > 65'
        graph, why = L.analyst_graph('How many patients are over 65?', {'sql': bad}, [], {'answer_type': 'count'}, AS_OF, columns, receipt)
        self.assertIsNone(graph)
        self.assertIn('age', why)


class Audit(unittest.TestCase):
    COLUMNS = {'patient': ['patient_id', 'birth_date'], 'death': ['patient_id'], 'measurement': ['patient_id', 'concept', 'value_canonical'],
               'measurement_latest': ['patient_id', 'concept', 'value_canonical']}

    def findings(self, question, sql):
        from clinical_core.agere.clinical import analyst_definitions
        return [f['rule'] for f in analyst_definitions.audit_sql(question, AS_OF, sql, self.COLUMNS)]

    def test_a_missing_lab_question_reads_every_result_without_a_threshold(self):
        sql = ("SELECT COUNT(DISTINCT c.patient_id) FROM condition_member c JOIN living_patient p ON p.patient_id = c.patient_id WHERE NOT EXISTS "
               "(SELECT 1 FROM measurement m WHERE m.patient_id = c.patient_id AND m.concept = 'uacr' AND m.value_canonical IS NOT NULL)")
        self.assertEqual(self.findings('How many CKD patients are missing a urine albumin-creatinine ratio in the last year?', sql), [])

    def test_a_lab_threshold_on_every_result_is_still_flagged(self):
        sql = ("SELECT COUNT(DISTINCT m.patient_id) FROM measurement m JOIN living_patient p ON p.patient_id = m.patient_id "
               "WHERE m.concept = 'hba1c' AND m.value_canonical > 9")
        self.assertIn('latest_lab', self.findings('How many patients have an A1c above 9?', sql))


class PlanTimeChecks(unittest.TestCase):
    def test_a_correct_share_on_the_definition_views_passes(self):
        both = L.ratio_sql(RATIO)
        self.assertEqual(plan_gaps(Q_SHARE, both, receipt_for(NUM, DEN)), [])

    def test_a_share_missing_its_threshold_still_fails(self):
        wrong_num = NUM.replace(' AND ll.value > 9', '')
        both = L.ratio_sql({**RATIO, 'numerator_sql': wrong_num})
        gaps = plan_gaps(Q_SHARE, both, receipt_for(wrong_num, DEN))
        self.assertTrue(any('9' in g for g in gaps), gaps)

    def test_only_a_direct_scalar_negation_establishes_the_complement(self):
        asc = "SELECT COUNT(*) FROM latest_lab WHERE concept = 'hba1c' AND value < 9"
        self.assertTrue(plan_gaps('How many have an A1c above 9?', asc, receipt_for(asc), 'count'))
        neg = "SELECT COUNT(*) FROM living_patient WHERE patient_id NOT IN (SELECT patient_id FROM latest_lab WHERE concept = 'hba1c' AND value <= 9)"
        self.assertTrue(plan_gaps('How many have an A1c above 9?', neg, receipt_for(neg), 'count'))
        direct = "SELECT COUNT(*) FROM latest_lab WHERE concept = 'hba1c' AND NOT (value <= 9)"
        self.assertEqual(plan_gaps('How many have an A1c above 9?', direct, receipt_for(direct), 'count'), [])
        plain = "SELECT COUNT(*) FROM latest_lab WHERE concept = 'hba1c' AND value <= 9"
        self.assertTrue(plan_gaps('How many have an A1c above 9?', plain, receipt_for(plain), 'count'))

    def test_missing_measurement_is_not_above_threshold(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript("CREATE TABLE living_patient(patient_id TEXT); CREATE TABLE latest_lab(patient_id TEXT,concept TEXT,value REAL); INSERT INTO living_patient VALUES ('high'),('low'),('missing'); INSERT INTO latest_lab VALUES ('high','hba1c',10),('low','hba1c',8);")
        neg = "SELECT COUNT(*) FROM living_patient WHERE patient_id NOT IN (SELECT patient_id FROM latest_lab WHERE concept='hba1c' AND value<=9)"
        direct = "SELECT COUNT(*) FROM latest_lab WHERE concept='hba1c' AND NOT (value<=9)"
        self.assertEqual(db.execute(neg).fetchone()[0], 2)
        self.assertEqual(db.execute(direct).fetchone()[0], 1)
        self.assertTrue(plan_gaps('How many have an A1c above 9?', neg, receipt_for(neg), 'count'))
        self.assertEqual(plan_gaps('How many have an A1c above 9?', direct, receipt_for(direct), 'count'), [])

    def test_compound_negation_and_sql_literals_are_not_scalar_proof(self):
        for sql in ("NOT (value<=9 AND sex='F')", "NOT (value<=9 OR sex='F')",
                    "NOT EXISTS (SELECT 1 WHERE value<=9)", "SELECT 'NOT(value<=9)'",
                    "SELECT 1 /* NOT(value<=9) */", "NOT(value!=9)", "NOT(value<>9)"):
            with self.subTest(sql=sql): self.assertEqual(AA._negated_complements(sql), [])
        self.assertEqual(AA._negated_complements('NOT (ll.value <= 9)'), ['> 9'])

    def test_the_view_applies_the_window_only_when_it_is_read(self):
        q = 'How many patients filled an opioid prescription in the past year?'
        windowed = "SELECT COUNT(DISTINCT f.patient_id) FROM fill f CROSS JOIN last_12_months w WHERE f.drug_class = 'opioid' AND f.fill_date > w.start_exclusive"
        self.assertEqual(plan_gaps(q, windowed, receipt_for(windowed), 'count'), [])
        unwindowed = "SELECT COUNT(DISTINCT f.patient_id) FROM fill f WHERE f.drug_class = 'opioid'"
        self.assertTrue(plan_gaps(q, unwindowed, receipt_for(unwindowed), 'count'))

    def test_a_calendar_year_is_not_satisfied_by_a_view(self):
        q = 'How many diabetic patients had an A1c above 9 in 2029?'
        sql = "SELECT COUNT(*) FROM latest_lab WHERE concept = 'hba1c' AND value > 9"
        self.assertTrue(any('2029' in g for g in plan_gaps(q, sql, receipt_for(sql), 'count')))

    def test_a_living_view_never_answers_a_question_about_the_deceased(self):
        q = 'How many diabetic patients died in 2029?'
        sql = "SELECT COUNT(*) FROM condition_member WHERE concept = 'diabetes' AND first_date < '2029-12-31'"
        self.assertTrue(plan_gaps(q, sql, receipt_for(sql), 'count'))
        self.assertNotRegex(AA.text(receipt_for(sql)), r'death|deceased|died')

    def test_concept_values_meet_the_question_words(self):
        q = 'How many patients have a mammography screening in the last 24 months?'
        sql = ("SELECT COUNT(DISTINCT patient_id) FROM cancer_screening WHERE test = 'bilateral_screening_mammography' "
               "AND event_date > date('2030-05-27', '-24 months')")
        self.assertEqual(plan_gaps(q, sql, receipt_for(sql), 'count'), [])
        self.assertIn('hemoglobin a1c', AA.text(receipt_for("SELECT 1 WHERE concept = 'hba1c'")).lower())
        self.assertNotRegex(AA.text(receipt_for("SELECT 1 WHERE concept = 'diabetes' AND c = 'hba1c'")), r'(?<![a-z])\d')  # no code or number rides in with a concept

    def test_numbers_inside_column_names_count(self):
        q = 'What is the 30-day readmission rate for 2029 admissions?'
        sql = "SELECT ROUND(100.0 * SUM(readmitted_30d = 1) / COUNT(*), 2) FROM stay WHERE admit_date BETWEEN '2029-01-01' AND '2029-12-31'"
        self.assertEqual(plan_gaps(q, sql, receipt_for(sql)), [])

    def test_other_tiers_are_read_on_their_own_sql_only(self):
        both = L.ratio_sql(RATIO)
        self.assertEqual(AA.text({'route': 'hosted_verified_sql'}, graph_of(both)), '')
        self.assertTrue(AC.evaluate(Q_SHARE, graph=graph_of(DEN), as_of=AS_OF, phase='plan')['unmet'])  # the denominator alone is not the share


class Shapes(unittest.TestCase):
    ANSWER = {'status': 'answered', 'answer_type': 'count', 'count': 64}

    def test_how_many_with_a_list_word_is_a_count_for_an_analyst_only(self):
        q = 'How many patients 65 or older have ten or more active medications on their list?'
        self.assertIsNotNone(literal_check.shape_misfit(q, self.ANSWER))
        self.assertIsNone(literal_check.shape_misfit(q, self.ANSWER, analyst=True))

    def test_a_patient_list_asked_for_is_still_not_a_count(self):
        q = 'Which patients have an A1c above 9?'
        self.assertIsNotNone(literal_check.shape_misfit(q, self.ANSWER, analyst=True))

    def test_which_entity_has_the_most_is_a_table(self):
        from clinical_core.agere.clinical import answer_shape
        q = 'Which practice has the most diabetic patients with an A1c above 9?'
        answer = {'status': 'answered', 'answer_type': 'table', 'table': [{'practice': 'P2', 'patients': 18}, {'practice': 'P6', 'patients': 15}]}
        self.assertEqual(answer_shape.conform(q, answer)[1]['status'], 'misfit')
        self.assertEqual(answer_shape.conform(q, answer, analyst=True)[1]['status'], 'fits')
        self.assertEqual(answer_shape.conform('Which patients have an A1c above 9?', answer, analyst=True)[1]['status'], 'misfit')


class Execution(unittest.TestCase):
    def test_definition_views_exist_only_for_a_query_that_reads_them(self):
        self.assertTrue(sql_query.uses_definition_views('SELECT COUNT(*) FROM living_patient'))
        self.assertTrue(sql_query.uses_definition_views("SELECT 1 FROM x CROSS JOIN last_12_months w"))
        self.assertFalse(sql_query.uses_definition_views("SELECT 'living_patient' AS name FROM patient"))
        self.assertFalse(sql_query.uses_definition_views('SELECT 1 -- latest_lab\nFROM patient'))

    def test_the_views_are_created_on_the_connection_and_never_written_to_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'semantic.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript("""CREATE TABLE meta(key, value); INSERT INTO meta VALUES ('as_of', '2030-05-27');
                    CREATE TABLE patient(patient_id, birth_date, coverage_type); INSERT INTO patient VALUES ('a', '1960-01-01', 'medicare'), ('b', '1990-01-01', 'commercial'), ('c', '1950-01-01', 'medicare');
                    CREATE TABLE death(patient_id); INSERT INTO death VALUES ('c');""")
            db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
            sql_query.define_definition_views(db)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM living_patient').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM medicare_member').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT end_inclusive FROM last_12_months').fetchone()[0], '2030-05-27')
            db.close()
            with sqlite3.connect(path) as fresh:
                self.assertEqual(fresh.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'living_patient'").fetchone()[0], 0)


class DecisionContext(unittest.TestCase):
    def test_a_query_on_a_living_view_is_not_an_unrestrictable_patient_grain_query(self):
        with DC.scope('How many patients have a PHQ-9 of 10 or more?', AS_OF) as state:
            DC.enforce_sql("SELECT COUNT(DISTINCT patient_id) FROM latest_lab WHERE concept = 'phq9' AND value >= 10", 'count', {'death', 'measurement_latest'})
            self.assertEqual(state.problems, [])
        with DC.scope('How many patients are there?', AS_OF) as state:  # a query reading nothing keyed to patients is still a problem
            DC.enforce_sql('SELECT COUNT(*) FROM organization', 'count', {'death'})


class CascadeWiring(unittest.TestCase):
    def cascade(self, question):
        return proposers.Cascade(type('S', (), {'path': '.', 'allow_live': False})(), SimpleNamespace(identity={'as_of': AS_OF}), question, ledger=SimpleNamespace(close=lambda: None))

    def test_the_cascade_passes_the_analyst_evidence_to_the_plan_check(self):
        both = L.ratio_sql(RATIO)
        graph = graph_of(both)
        self.assertEqual(self.cascade(Q_SHARE).contract_gaps(graph=graph, receipt=receipt_for(NUM, DEN)), [])
        # the same graph from another tier (no analyst evidence) is read on its SQL alone
        other = self.cascade(Q_SHARE).contract_gaps(graph=graph_of(DEN), receipt={'route': 'hosted_verified_sql'})
        self.assertTrue(other)


if __name__ == '__main__':
    unittest.main()
