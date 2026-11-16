"""A warehouse laid out differently: mapped onto the semantic layer, or shown as it is (foreign)."""
import os
import sqlite3
import unittest
from pathlib import Path

from clinical_core.agere.tiered import portability, recipes, store

LAYOUT = portability.LAYOUT_B
MAPPING = portability.REVIEWED


class Env:
    def __init__(self, **values): self.values, self.old = values, {}
    def __enter__(self):
        for k, v in self.values.items():
            self.old[k] = os.environ.get(k)
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        recipes._DATA_YEARS.clear(); store._vocab.clear(); store._as_of.clear()
    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        recipes._DATA_YEARS.clear(); store._vocab.clear(); store._as_of.clear()


@unittest.skipUnless(LAYOUT.exists(), 'layout B not built (python -m clinical_core.agere.tiered.portability build)')
class Mapped(unittest.TestCase):
    def env(self): return Env(AGERE_TIERED_DB=str(LAYOUT), AGERE_TIERED_MAPPING=str(MAPPING), AGERE_TIERED_FOREIGN=None)

    def test_semantic_tables_answer_as_on_the_original(self):
        orig = sqlite3.connect(Path(store.DEFAULT_DB).resolve().as_uri() + '?mode=ro', uri=True)
        sql = "SELECT COUNT(*), COUNT(DISTINCT patient_id) FROM fill WHERE drug_class = 'statin' AND substr(fill_date, 1, 4) = '2029'"
        with self.env():
            self.assertEqual(store.run(sql)[1], [list(orig.execute(sql).fetchone())])

    def test_saved_recipes_see_the_semantic_layer_version_the_mapping_presents(self):
        orig = sqlite3.connect(Path(store.DEFAULT_DB).resolve().as_uri() + '?mode=ro', uri=True)
        meta = dict(orig.execute("SELECT key, value FROM meta"))
        with self.env():
            self.assertEqual(store.schema_version(), f"{meta['version']}:{meta['source_digest'][:16]}")
            self.assertEqual(store.as_of(store.connect()), meta['as_of'][:10])

    def test_a_cohort_restricts_mapped_tables(self):
        with self.env():
            some = [r[0] for r in store.run('SELECT patient_id FROM patient ORDER BY patient_id LIMIT 3')[1]]
            db = store.connect(some)
            self.assertEqual(store.run('SELECT COUNT(DISTINCT patient_id) FROM member_month', db=db)[1][0][0] <= 3, True)

    def test_portable_sql_runs_on_the_customer_warehouse_itself(self):
        with self.env():
            sql = "SELECT COUNT(*) FROM stay WHERE type = 'inpatient'"
            want = store.run(sql)[1]
            text = store.portable(sql)
        raw = sqlite3.connect(LAYOUT.resolve().as_uri() + '?mode=ro', uri=True)
        self.assertEqual([list(r) for r in raw.execute(text).fetchall()], want)


@unittest.skipUnless(LAYOUT.exists(), 'layout B not built')
class Foreign(unittest.TestCase):
    def env(self): return Env(AGERE_TIERED_DB=str(LAYOUT), AGERE_TIERED_MAPPING=None, AGERE_TIERED_FOREIGN='on')

    def test_its_own_tables_and_vocabulary_are_shown(self):
        with self.env():
            self.assertIn('visit_occurrence', store.tables())
            self.assertNotIn('stay', store.tables())
            self.assertIn('visit_occurrence.visit_type: ER, IP, OBS', store.vocabulary_text())

    def test_years_held_are_read_from_its_own_date_columns(self):
        with self.env():
            missing, held = recipes.years_without_data('How many ER visits in 2029 and 2035?')
            self.assertEqual(missing, ['2035'])
            self.assertIn('2029', held)

    def test_a_partial_copy_of_the_layer_is_not_foreign_unless_declared(self):
        with Env(AGERE_TIERED_FOREIGN=None, AGERE_TIERED_MAPPING=None):
            self.assertFalse(store.foreign())


if __name__ == '__main__':
    unittest.main()
