import json
import sqlite3
import unittest

from clinical_core.agere.clinical import fill_events


def _db(rows):
    """rows: (record_id, patient_id, source_stream, event_date, drug_class, drug_name, days_supply)."""
    db = sqlite3.connect(':memory:')
    db.executescript("""
        CREATE TABLE medication(record_id TEXT, patient_id TEXT, source_stream TEXT, evidence TEXT, drug_name TEXT,
          drug_class TEXT, status TEXT, event_date TEXT, available_date TEXT, ingredients TEXT, therapeutic_category TEXT,
          rxnorm TEXT, rxnorm_source TEXT, rxnorm_ingredient TEXT, ndc TEXT, fill_reversed INTEGER, picked_up INTEGER);
        CREATE TABLE payer_pharmacy_claim(record_id TEXT, days_supply INTEGER);
        CREATE TABLE record(record_id TEXT, stream TEXT, payload TEXT);
        CREATE TABLE meta(key TEXT, value TEXT);""")
    for rid, pid, src, d, cls, name, ds in rows:
        db.execute('INSERT INTO medication(record_id, patient_id, source_stream, drug_name, drug_class, event_date, picked_up) '
                   'VALUES (?,?,?,?,?,?,1)', (rid, pid, src, name, cls, d))
        if src == 'payer_pharmacy_claims':
            db.execute('INSERT INTO payer_pharmacy_claim VALUES (?,?)', (rid, ds))
        else:
            db.execute('INSERT INTO record VALUES (?,?,?)', (rid, src, json.dumps({'days_supply_reported': ds})))
    return db


def _fills(db):
    return db.execute('SELECT patient_id, fill_date, drug_name, sources, record_ids, class_fill_seq FROM fill '
                      'ORDER BY fill_id').fetchall()


class SameIngredientMerge(unittest.TestCase):
    def test_different_ingredients_same_class_same_day_stay_two_fills(self):
        db = _db([('c1', 'P1', 'payer_pharmacy_claims', '2028-03-01', 'opioid', 'tramadol hydrochloride 50 MG Oral Tablet', 30),
                  ('d1', 'P1', 'pharmacy_dispenses', '2028-03-01', 'opioid',
                   'Abuse-Deterrent 12 HR Oxycodone Hydrochloride 10 MG Extended Release Oral Tablet [Oxycontin]', 30)])
        summary = fill_events.build(db)
        self.assertEqual(summary['fills'], 2)
        self.assertEqual(summary['merged'], 0)
        self.assertEqual({r[3] for r in _fills(db)}, {'payer', 'dispense'})

    def test_dispense_and_its_claim_still_merge_across_case_and_salt(self):
        db = _db([('c1', 'P1', 'payer_pharmacy_claims', '2028-03-01', 'opioid', 'oxycodone hydrochloride 5 MG Oral Tablet', 30),
                  ('d1', 'P1', 'pharmacy_dispenses', '2028-03-02', 'opioid', 'Oxycodone Hydrochloride 5 MG Oral Tablet', None)])
        summary = fill_events.build(db)
        self.assertEqual(summary['fills'], 1)
        (row,) = _fills(db)
        self.assertEqual(row[3], 'dispense,payer')
        self.assertEqual(db.execute('SELECT days_supply FROM fill').fetchone()[0], 30)

    def test_combination_products_merge_only_on_the_same_ingredient_set(self):
        db = _db([('c1', 'P1', 'payer_pharmacy_claims', '2028-03-01', 'opioid',
                   'Acetaminophen 300 MG / Hydrocodone Bitartrate 5 MG Oral Tablet', 5),
                  ('d1', 'P1', 'pharmacy_dispenses', '2028-03-01', 'opioid',
                   'Acetaminophen 325 MG / Oxycodone Hydrochloride 10 MG Oral Tablet [Percocet]', 5),
                  ('d2', 'P1', 'pharmacy_dispenses', '2028-03-01', 'opioid',
                   'acetaminophen 300 MG / hydrocodone bitartrate 5 MG Oral Tablet', 5)])
        summary = fill_events.build(db)
        self.assertEqual(summary['fills'], 2)
        merged = [r for r in _fills(db) if ',' in r[3]]
        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0][4].split(',')), {'c1', 'd2'})

    def test_class_sequence_counts_both_drugs(self):
        db = _db([('c1', 'P1', 'payer_pharmacy_claims', '2028-03-01', 'opioid', 'tramadol 50 MG Oral Tablet', 30),
                  ('c2', 'P1', 'payer_pharmacy_claims', '2028-03-01', 'opioid', 'oxycodone 5 MG Oral Tablet', 30),
                  ('c3', 'P1', 'payer_pharmacy_claims', '2028-04-01', 'opioid', 'tramadol 50 MG Oral Tablet', 30)])
        fill_events.build(db)
        self.assertEqual([r[5] for r in _fills(db)], [1, 2, 3])

    def test_ingredient_key_falls_back_to_delivered_columns_then_name(self):
        self.assertEqual(fill_events.ingredient_key('Unlisted Brand 10 MG', 'Zolmitriptan'), 'zolmitriptan')
        self.assertEqual(fill_events.ingredient_key('Unlisted Brand 10 MG', None, '12345'), 'rxnorm:12345')
        self.assertEqual(fill_events.ingredient_key('Unlisted Brand 10 MG'), 'name:unlisted')


if __name__ == '__main__':
    unittest.main()
