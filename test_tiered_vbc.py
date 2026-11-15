"""Model-free checks for the value-based-care recipes: wording matches, and the care-gap rules on a tiny warehouse."""
import sqlite3
import unittest
import unittest.mock

from clinical_core.agere.tiered import recipes


class Wording(unittest.TestCase):
    def test_each_question_reaches_its_recipe(self):
        for q, rid in (('What was the cost per member per month in 2029 by line of business?', 'pmpm_by_line_of_business'),
                       ('What is the PMPM for 2029', 'pmpm_by_line_of_business'),
                       ('What are the care gaps in 2029?', 'care_gaps_summary'),
                       ('Which practices have the most care gaps in 2029?', 'care_gaps_by_practice'),
                       ('List the top 10 highest-cost members in 2029', 'high_cost_members'),
                       ('How do the attributed practices compare in 2029 on cost and utilization?', 'attributed_panel_by_practice'),
                       ('Show open statin_cardiovascular gaps in 2029', 'care_gap_list')):
            self.assertEqual(recipes.match(q)[0], rid, q)



class UnknownValues(unittest.TestCase):
    def test_a_filter_on_a_value_the_column_never_holds_is_named(self):
        db = sqlite3.connect(':memory:')
        db.executescript("CREATE TABLE fill (patient_id, drug_class, fill_date); INSERT INTO fill VALUES ('A', 'glp1_agonist', '2029-01-02'), ('B', 'statin', '2029-02-02');")
        recipes._COLUMN_TABLES.clear()
        try:
            if True:
                msg = recipes.unknown_filter_values("SELECT COUNT(DISTINCT patient_id) FROM fill WHERE lower(drug_class) = lower(:c) AND substr(fill_date, 1, 4) = :y", {'c': 'GLP-1 agonist', 'y': '2029'}, db)
                self.assertIn("'GLP-1 agonist' does not occur in column drug_class", msg); self.assertIn('glp1_agonist', msg)
                db.executescript("CREATE TABLE claim (source); INSERT INTO claim VALUES ('ehr'); CREATE TEMP VIEW document AS SELECT 'scanned_document' AS source;")
                self.assertIsNone(recipes.unknown_filter_values("SELECT COUNT(*) FROM document d WHERE d.source = 'scanned_document'", {}, db))   # judged against the table read
                self.assertIsNone(recipes.unknown_filter_values("WITH x AS (SELECT 1 AS kind) SELECT * FROM x WHERE kind = 'abc'", {}, db))        # a CTE column is not checked
                self.assertIsNone(recipes.unknown_filter_values("SELECT 1 FROM fill WHERE drug_class = :c", {'c': 'GLP1_AGONIST'}, db))
                self.assertIsNone(recipes.unknown_filter_values("SELECT 1 FROM fill WHERE drug_class NOT IN ('insulin')", {}, db))
                self.assertIn('insulin', recipes.unknown_filter_values("SELECT 1 FROM fill WHERE drug_class IN ('statin', 'insulin')", {}, db))
        finally:
            recipes._COLUMN_TABLES.clear()


class CareGaps(unittest.TestCase):
    def test_the_four_rules_on_a_tiny_warehouse(self):
        db = sqlite3.connect(':memory:')
        db.executescript('''
            CREATE TABLE member_month (patient_id, month, line_of_business, attribution_practice_id);
            CREATE TABLE patient (patient_id, sex_reported, birth_date);
            CREATE TABLE condition (patient_id, concept, event_date, chronic);
            CREATE TABLE measurement (patient_id, concept, event_date, value_canonical);
            CREATE TABLE fill (patient_id, drug_class, fill_date);
            CREATE TABLE cancer_screening (patient_id, test, event_date);
            CREATE TABLE wellness_visit (patient_id, awv, event_date);
            INSERT INTO member_month VALUES ('A', '2029-03', 'medicare', 'P1'), ('B', '2029-05', 'commercial', 'P2'), ('C', '2029-01', 'commercial', 'P2'), ('D', '2028-12', 'medicare', 'P1');
            INSERT INTO patient VALUES ('A', 'F', '1960-01-01'), ('B', 'M', '1970-06-01'), ('C', 'F', '1990-01-01'), ('D', 'F', '1960-01-01');
            INSERT INTO condition VALUES ('A', 'type_2_diabetes', '2020-01-01', 1), ('B', 'diabetes', '2021-01-01', NULL), ('B', 'coronary_artery_disease', '2022-01-01', 1),
                                         ('D', 'diabetes', '2020-01-01', 1);
            INSERT INTO measurement VALUES ('A', 'hba1c', '2029-02-01', 10.2), ('A', 'hba1c', '2029-09-01', 7.1), ('B', 'hba1c', '2028-11-01', 6.0);
            INSERT INTO fill VALUES ('B', 'statin', '2028-12-30');
            INSERT INTO cancer_screening VALUES ('A', 'bilateral_screening_mammography', '2027-10-15');
            INSERT INTO wellness_visit VALUES ('A', 1, '2029-04-01');
        ''')
        rows = db.execute('WITH ' + recipes._CARE_GAPS + ' SELECT measure, patient_id, gap FROM g ORDER BY measure, patient_id', {'year': '2029'}).fetchall()
        self.assertEqual(rows, [('annual_wellness_visit', 'A', 0),            # Medicare, wellness visit in the year
                                ('breast_cancer_screening', 'A', 0),          # mammogram on 15 Oct 2027 counts (from 1 Oct two years earlier)
                                ('diabetes_a1c', 'A', 0),                     # the latest result in the year (7.1) decides, not the first
                                ('diabetes_a1c', 'B', 1),                     # no result in 2029; a 2028 result does not count
                                ('statin_cardiovascular', 'B', 1)])           # a December 2028 fill does not count; D not enrolled in 2029; C too young for screening


if __name__ == '__main__':
    unittest.main()
