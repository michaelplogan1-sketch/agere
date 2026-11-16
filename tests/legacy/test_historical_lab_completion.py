"""Historical testing is not current numeric state; mixed reads retain their own roles."""
import sqlite3
import unittest
from clinical_core.agere.clinical import decision_context as D


class LabReadRoles(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE patient(patient_id TEXT); CREATE TABLE death(patient_id TEXT);
        INSERT INTO patient VALUES ('A'),('B');
        CREATE TABLE measurement(record_id TEXT,patient_id TEXT,concept TEXT,event_date TEXT,value_canonical REAL);
        INSERT INTO measurement VALUES
        ('a-old-e','A','egfr','2029-05-01',70),('a-new-e','A','egfr','2030-05-01',72),
        ('a-old-u','A','uacr','2029-05-01',20),('a-new-u','A','uacr','2030-05-01',21),
        ('b-e','B','egfr','2029-05-01',65),('b-new-e','B','egfr','2030-05-01',66),
        ('a-old-h','A','hba1c','2029-05-01',11),('a-new-h','A','hba1c','2030-05-01',7),
        ('b-h','B','hba1c','2030-05-01',10);
        CREATE VIEW measurement_latest AS SELECT * FROM measurement m WHERE NOT EXISTS
        (SELECT 1 FROM measurement newer WHERE newer.patient_id=m.patient_id AND newer.concept=m.concept AND newer.event_date>m.event_date);
        ''')
        self.tables = {'patient','death','measurement','measurement_latest'}

    def run_query(self, sql, question='How many patients got both kidney tests in 2029?'):
        with D.scope(question,'2030-05-27') as state:
            rewritten, receipt = D.enforce_sql(sql,'value',self.tables)
            self.assertFalse(state.problems, state.problems)
            value = self.db.execute(rewritten).fetchone()[0]
        return value, receipt, rewritten

    def test_historical_completion_keeps_old_valid_tests_after_new_results_arrive(self):
        sql = "SELECT COUNT(DISTINCT p.patient_id) AS value FROM patient p WHERE EXISTS (SELECT 1 FROM measurement m WHERE m.patient_id=p.patient_id AND m.concept='egfr' AND m.value_canonical IS NOT NULL AND m.event_date BETWEEN '2029-01-01' AND '2029-12-31') AND EXISTS (SELECT 1 FROM measurement m WHERE m.patient_id=p.patient_id AND m.concept='uacr' AND m.value_canonical IS NOT NULL AND m.event_date BETWEEN '2029-01-01' AND '2029-12-31')"
        value, receipt, _ = self.run_query(sql)
        self.assertEqual(value, 1)
        self.assertFalse(receipt['latest_labs'])

    def test_latest_numeric_threshold_still_uses_latest_results(self):
        sql = "SELECT COUNT(DISTINCT patient_id) FROM measurement WHERE concept='hba1c' AND value_canonical>9"
        value, receipt, _ = self.run_query(sql,'How many patients have an A1c above 9?')
        self.assertEqual(value, 1); self.assertTrue(receipt['latest_labs'])
        self.assertEqual(self.run_query(sql,'How many patients ever had an A1c above 9?')[0],2)

    def test_same_alias_in_sibling_scopes_does_not_mix_presence_and_value(self):
        sql = "SELECT COUNT(DISTINCT p.patient_id) FROM patient p WHERE EXISTS(SELECT 1 FROM measurement m WHERE m.patient_id=p.patient_id AND m.concept='hba1c' AND m.value_canonical>9) AND EXISTS(SELECT 1 FROM measurement m WHERE m.patient_id=p.patient_id AND m.concept='egfr' AND m.value_canonical IS NOT NULL AND substr(m.event_date,1,4)='2029')"
        value, receipt, rewritten = self.run_query(sql,'How many patients have an A1c above 9 and received an eGFR in 2029?')
        self.assertEqual(value,1); self.assertTrue(receipt['latest_labs'])
        self.assertEqual(rewritten.count('record_id IN (SELECT record_id FROM measurement_latest)'),1)

    def test_derived_null_checks_are_presence_but_derived_thresholds_use_values(self):
        base = "WITH labs AS (SELECT patient_id,concept,value_canonical AS lab_value,event_date FROM measurement) SELECT COUNT(DISTINCT patient_id) FROM labs WHERE concept='hba1c' AND "
        self.assertFalse(self.run_query(base+'lab_value IS NOT NULL')[1]['latest_labs'])
        self.assertEqual(self.run_query(base+'lab_value>9')[0],1)
        self.assertTrue(self.run_query(base+'lab_value>9')[1]['latest_labs'])

    def test_unused_exists_projection_does_not_select_latest(self):
        sql = "SELECT COUNT(DISTINCT p.patient_id) FROM patient p WHERE EXISTS (SELECT m.value_canonical FROM measurement m WHERE m.patient_id=p.patient_id AND m.concept='uacr' AND substr(m.event_date,1,4)='2029')"
        value, receipt, _ = self.run_query(sql)
        self.assertEqual(value,1); self.assertFalse(receipt['latest_labs'])

    def test_quoted_alias_and_correlated_value_keep_their_binding(self):
        sql = 'SELECT COUNT(DISTINCT p.patient_id) FROM patient p JOIN measurement AS "lab" ON "lab".patient_id=p.patient_id WHERE "lab".concept=\'hba1c\' AND EXISTS(SELECT 1 WHERE "lab".value_canonical>9)'
        value, receipt, _ = self.run_query(sql,'How many patients have an A1c above 9?')
        self.assertEqual(value,1); self.assertTrue(receipt['latest_labs'])

    def test_count_nonnull_values_does_not_mean_numeric_state(self):
        sql = "SELECT p.patient_id,COUNT(m.value_canonical) FROM patient p JOIN measurement m ON m.patient_id=p.patient_id WHERE m.concept='egfr' GROUP BY p.patient_id"
        _, receipt, _ = self.run_query(sql)
        self.assertFalse(receipt['latest_labs'])

    def test_cte_aliases_stars_and_union_lineage_keep_value_roles(self):
        queries = [
            "WITH labs(pid,lab_result) AS (SELECT patient_id,value_canonical FROM measurement WHERE concept='hba1c') SELECT COUNT(DISTINCT pid) FROM labs WHERE lab_result>9",
            "WITH labs AS (SELECT * FROM measurement) SELECT COUNT(DISTINCT patient_id) FROM labs WHERE concept='hba1c' AND value_canonical>9",
            "WITH labs AS (SELECT patient_id,value_canonical AS result FROM measurement WHERE concept='hba1c' UNION ALL SELECT patient_id,value_canonical FROM measurement WHERE concept='hba1c') SELECT COUNT(DISTINCT patient_id) FROM labs WHERE result>9",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                value, receipt, _ = self.run_query(sql,'How many patients have an A1c above 9?')
                self.assertEqual(value,1); self.assertTrue(receipt['latest_labs'])

    def test_cte_shadow_name_does_not_turn_a_computed_value_into_a_lab_read(self):
        sql = "WITH measurement AS (SELECT patient_id,20 AS value_canonical FROM patient) SELECT COUNT(DISTINCT patient_id) FROM measurement WHERE value_canonical>9"
        value, receipt, _ = self.run_query(sql)
        self.assertEqual(value,2); self.assertFalse(receipt['latest_labs'])

    def test_validity_only_case_expressions_are_not_numeric_thresholds(self):
        sql = "SELECT COUNT(DISTINCT CASE WHEN m.value_canonical IS NOT NULL THEN m.patient_id END) AS value FROM measurement m WHERE m.concept='uacr' AND substr(m.event_date,1,4)='2029'"
        value, receipt, _ = self.run_query(sql)
        self.assertEqual(value,1); self.assertFalse(receipt['latest_labs'])

    def test_claim_ids_and_composite_identifiers_are_not_patient_counts(self):
        from clinical_core.agere.clinical.sql_read_roles import counts_distinct_patient_ids
        self.assertFalse(counts_distinct_patient_ids('SELECT COUNT(DISTINCT claim_id) FROM claim'))
        self.assertFalse(counts_distinct_patient_ids("SELECT COUNT(DISTINCT patient_id || event_date) FROM measurement"))
        self.assertTrue(counts_distinct_patient_ids('WITH p AS (SELECT patient_id AS person FROM patient) SELECT COUNT(DISTINCT person) FROM p'))

    def test_explicit_historical_window_in_derived_scope_keeps_its_own_reduction(self):
        for projection in ('*', 'patient_id,event_date,value_canonical'):
            sql = "WITH a AS (SELECT patient_id,event_date,value_canonical FROM measurement WHERE concept='hba1c' AND event_date BETWEEN '2029-01-01' AND '2029-12-31'), r AS (SELECT " + projection + ", ROW_NUMBER() OVER(PARTITION BY patient_id ORDER BY event_date DESC) AS rn FROM a) SELECT COUNT(DISTINCT patient_id) FROM r WHERE rn=1 AND value_canonical>9"
            value, receipt, _ = self.run_query(sql,'How many patients had a most recent 2029 A1c above 9?')
            self.assertEqual(value,1); self.assertFalse(receipt['latest_labs'])
