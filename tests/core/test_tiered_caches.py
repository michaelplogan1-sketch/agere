"""What is cached about a warehouse follows the warehouse in use: two warehouses in one process, a per-request override, and a
warehouse replaced in place each get their own answers. No model is called."""
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from clinical_core.agere.tiered import recipes, store


def warehouse(path, years):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE visit(patient_id TEXT, event_date TEXT)')
    db.executemany('INSERT INTO visit VALUES (?, ?)', [(f'P{i}', f'{y}-03-01') for y in years for i in range(50)])
    db.execute('CREATE TABLE meta(key TEXT, value TEXT)')
    db.execute("INSERT INTO meta VALUES ('as_of', ?)", (f'{max(years)}-12-31',))
    db.commit(); db.close()


class Scoped(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a, self.b = Path(self.tmp.name) / 'a.sqlite3', Path(self.tmp.name) / 'b.sqlite3'
        warehouse(self.a, (2027, 2028)); warehouse(self.b, (2029, 2030))
        self.old = os.environ.get('AGERE_TIERED_DB')
        store.clear_caches()

    def tearDown(self):
        if self.old is None: os.environ.pop('AGERE_TIERED_DB', None)
        else: os.environ['AGERE_TIERED_DB'] = self.old
        store.clear_caches(); self.tmp.cleanup()

    def years(self):
        return recipes.years_without_data('How many visits in 2028?', sqlite3.connect(store.path()))

    def test_each_warehouse_has_its_own_years(self):
        os.environ['AGERE_TIERED_DB'] = str(self.a)
        self.assertEqual(self.years(), ([], ['2027', '2028']))
        token = store.DB_OVERRIDE.set(str(self.b))   # a live one-patient layer, for this request only
        try:
            self.assertEqual(self.years(), (['2028'], ['2029', '2030']))
        finally:
            store.DB_OVERRIDE.reset(token)
        self.assertEqual(self.years(), ([], ['2027', '2028']))   # the population warehouse is unchanged

    def test_a_warehouse_replaced_in_place_is_read_again(self):
        os.environ['AGERE_TIERED_DB'] = str(self.a)
        self.assertEqual(self.years()[1], ['2027', '2028'])
        time.sleep(0.01); self.a.unlink(); warehouse(self.a, (2029, 2030, 2031))
        self.assertEqual(self.years()[1], ['2029', '2030', '2031'])

    def test_the_cache_is_bounded(self):
        for i in range(store.MAX_WAREHOUSES + 5):
            p = Path(self.tmp.name) / f'w{i}.sqlite3'; warehouse(p, (2028,))
            token = store.DB_OVERRIDE.set(str(p))
            try:
                store.scoped('probe')['x'] = i
            finally:
                store.DB_OVERRIDE.reset(token)
        self.assertLessEqual(len(store._SCOPED), store.MAX_WAREHOUSES)


if __name__ == '__main__':
    unittest.main()
