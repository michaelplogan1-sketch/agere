"""Raw value checks must respect lexical scope and computed output domains."""
import sqlite3, tempfile, unittest
from pathlib import Path
from clinical_core.agere.clinical import answer_checks as A


class ScopedValues(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.target=Path(temp.name)/'semantic.sqlite3'
        with sqlite3.connect(self.target)as db:
            db.executescript("""CREATE TABLE appointment(organization_id TEXT,scheduled_date TEXT);
                INSERT INTO appointment VALUES ('known','2029-01-01'),(NULL,'2029-02-01');
                CREATE TABLE other(organization_id TEXT);
                INSERT INTO other VALUES ('different');""")

    def check(self,sql):
        return A.value_existence(None,{'nodes':[{'operation':'sql','parameters':{'query':sql}}]}, {},target=self.target)

    def test_cte_generated_missing_label_is_not_raw_column_domain(self):
        sql="""WITH marked AS (SELECT COALESCE(organization_id,'missing') AS organization_id,scheduled_date FROM appointment)
            SELECT count(*) FROM marked WHERE organization_id='missing' AND scheduled_date>='2029-01-01'"""
        self.assertEqual(self.check(sql),[])

    def test_unrelated_physical_table_does_not_resolve_scalar_result_column(self):
        sql="""WITH marked AS (SELECT COALESCE(organization_id,'missing') AS organization_id FROM appointment)
            SELECT (SELECT count(*) FROM marked WHERE organization_id='missing'),count(*)
            FROM appointment WHERE scheduled_date>='2029-01-01'"""
        self.assertEqual(self.check(sql),[])

    def test_shadowed_alias_uses_its_own_scope(self):
        sql="""SELECT (SELECT count(*) FROM (SELECT 'missing' AS organization_id) AS x
            WHERE x.organization_id='missing'),count(*) FROM appointment AS x WHERE x.scheduled_date>='2029-01-01'"""
        self.assertEqual(self.check(sql),[])

    def test_real_raw_label_error_still_fails_beside_unrelated_cte(self):
        sql="""WITH marked AS (SELECT COALESCE(organization_id,'missing') AS organization_id FROM appointment)
            SELECT count(*) FROM appointment AS A WHERE a.organization_id='missing' AND a.scheduled_date>='2029-01-01'"""
        self.assertTrue(self.check(sql))

    def test_conditional_missingness_count_is_not_a_row_filter(self):
        sql="SELECT SUM(CASE WHEN organization_id='missing' THEN 1 ELSE 0 END) FROM appointment WHERE scheduled_date>='2029-01-01'"
        self.assertEqual(self.check(sql),[])

    def test_direct_label_with_another_real_filter_remains_checked(self):
        self.assertTrue(self.check("SELECT count(*) FROM appointment WHERE organization_id IN ('invented') AND scheduled_date>='2029-01-01'"))


if __name__=='__main__':unittest.main()
