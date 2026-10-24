"""The materialized relations concept_patient (condition rows) and diagnosis_any hold exactly the governed condition evidence
(concepts.condition_evidence_sql + decision_context lab rules), per concept, on a fixture warehouse; a database stamped with an older
version is shadowed by the current definition and rebuilt by the upgrade. Synthetic fixture only; no stack database."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import concepts, semantic_layer as S, sql_query

CONCEPTS = ['diabetes', 'type_2_diabetes', 'chronic_kidney_disease', 'heart_failure']


def fixture(path):
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, label TEXT, icd10 TEXT, event_date TEXT,
        recorded_date TEXT, is_diagnosis INTEGER, diagnosis_basis TEXT);
      CREATE TABLE payer_claim_diagnosis(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, icd10 TEXT, counts_in_metrics INTEGER);
      CREATE TABLE measurement(patient_id TEXT, event_date TEXT, concept TEXT, value_canonical REAL, result_current INTEGER,
        excluded_reason TEXT, source_table TEXT);
      CREATE TABLE concept_patient(concept TEXT, kind TEXT, patient_id TEXT, records INTEGER, first_date TEXT, last_date TEXT,
        PRIMARY KEY(concept, kind, patient_id));
    """)
    C = lambda *a: db.execute('INSERT INTO condition VALUES (?,?,?,?,?,?,?,?,?,?)', a)
    C('1', 'dx1', 'conditions', 'diabetes', 'DM', 'E11.9', '2029-01-01', None, 1, None)
    C('2', 'dx2', 'conditions', 'type_2_diabetes', 'T2', 'E11.9', '2029-01-01', None, 1, None)
    C('3', 'dx3', 'conditions', 'heart_failure', 'HF', 'E11.9', '2029-01-01', None, 1, None)          # label differs, code is diabetes
    C('4', 'lab1', 'conditions', 'diabetes', 'A1c', 'E11.9', '2029-01-01', None, None, 'laboratory_evidence')  # generic lab row counts
    C('5', 'lab2', 'conditions', 'type_2_diabetes', 'A1c', 'E11.9', '2029-01-01', None, None, 'laboratory_evidence')  # typed: does not
    C('6', 'pl1', 'conditions', 'heart_failure', 'HF', 'I50.9', '2029-01-01', None, None, 'ehr_problem_list')
    C('7', 'as1', 'condition_assessments', 'heart_failure', 'topic', 'I50.9', '2029-01-01', None, 0, None)  # assessment topic: no
    C('8', 'ck1', 'conditions', 'chronic_kidney_disease', 'CKD', 'N18.3', '2029-01-01', None, 1, None)
    C('9', 'ck2', 'conditions', 'type_2_diabetes', 'DKD', 'E11.22', '2029-01-01', None, 1, None)      # E11.2x is a CKD code too
    db.executemany('INSERT INTO payer_claim_diagnosis VALUES (?,?,?,?,?)', [
        ('k1', 'cl1', '2029-02-01', 'E11.9', 1), ('k2', 'cl2', '2029-02-01', 'E11.9', 0), ('k3', 'cl3', '2029-02-01', 'I50.22', 1),
        ('k4', 'cl4', '2029-02-01', 'N18.4', 1)])
    M = lambda *a: db.execute('INSERT INTO measurement VALUES (?,?,?,?,?,?,?)', a)
    M('hb1', '2029-03-01', 'hba1c', 6.8, 1, None, 'lab')       # diabetes by the lab rule
    M('hb2', '2029-03-01', 'hba1c', 6.4, 1, None, 'lab')
    M('hb3', '2029-03-01', 'hba1c', 9.0, 1, None, 'claim_line')  # not a lab source
    M('hb4', '2029-03-01', 'hba1c', 9.0, 0, None, 'lab')         # superseded
    M('eg1', '2029-01-01', 'egfr', 50, 1, None, 'lab'); M('eg1', '2029-06-01', 'egfr', 45, 1, None, 'lab')  # CKD: >= 90 days apart
    M('eg2', '2029-01-01', 'egfr', 50, 1, None, 'lab'); M('eg2', '2029-02-01', 'egfr', 45, 1, None, 'lab')  # too close
    db.commit(); db.close()


def tables_of(db):
    return {t: [c[1] for c in db.execute(f'PRAGMA table_info({t})')] for t in ('condition', 'payer_claim_diagnosis', 'measurement')}


class MaterializedDefinitions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'semantic.sqlite3'
        fixture(self.path)

    def governed(self, db, name):
        return {r[0] for r in db.execute('SELECT patient_id FROM ' + concepts.condition_evidence_sql([name], tables_of(db)))}

    def stored(self, db, name):
        return {r[0] for r in db.execute("SELECT patient_id FROM concept_patient WHERE kind = 'condition' AND concept = ?", (name,))}

    def test_concept_patient_is_the_governed_evidence_per_concept(self):
        db = sqlite3.connect(self.path)
        S.build_concept_patient_conditions(db)
        for name in CONCEPTS + [c for c in concepts.CONDITIONS if c not in CONCEPTS]:
            self.assertEqual(self.stored(db, name), self.governed(db, name), name)
        self.assertEqual(self.governed(db, 'diabetes'), {'dx1', 'dx2', 'dx3', 'lab1', 'ck2', 'cl1', 'hb1'})
        self.assertNotIn('lab2', self.stored(db, 'type_2_diabetes'))                   # stored lab rows: generic diabetes / CKD only
        self.assertIn('dx3', self.stored(db, 'type_2_diabetes'))                       # a condition is its code set
        self.assertIn('eg1', self.stored(db, 'chronic_kidney_disease'))
        self.assertNotIn('eg2', self.stored(db, 'chronic_kidney_disease'))
        db.close()

    def test_diagnosis_any_holds_the_lab_rule_members(self):
        S.upgrade_diagnosis_any(self.path)
        db = sqlite3.connect(self.path)
        self.assertEqual({r[0] for r in db.execute("SELECT patient_id FROM diagnosis_any WHERE source = 'lab_rule'")}, {'hb1', 'eg1'})
        codes = {r[0] for r in db.execute("SELECT patient_id FROM diagnosis_any WHERE icd10 LIKE 'E1%' OR icd10 = 'LAB_RULE_DIABETES'")}
        self.assertIn('hb1', codes); self.assertIn('lab1', codes)
        db.close()

    def test_an_older_stamp_is_shadowed_then_rebuilt(self):
        with sqlite3.connect(self.path) as db:
            S.build_concept_patient_conditions(db)
            db.execute("DELETE FROM concept_patient WHERE concept = 'diabetes'")           # a stale table from before the lab rule
            db.execute("UPDATE meta SET value = '2' WHERE key = 'concept_patient'")
        db = sql_query.register(sqlite3.connect(f'file:{self.path}?mode=ro', uri=True))
        self.assertIn('hb1', self.stored(db, 'diabetes'))                               # the current definition shadows the stale table
        db.close()
        self.assertTrue(S.upgrade_concept_patient(self.path)['upgraded'])
        self.assertFalse(S.upgrade_concept_patient(self.path)['upgraded'])             # at the version: left alone
        with sqlite3.connect(self.path) as db:
            self.assertIn('hb1', self.stored(db, 'diabetes'))
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key = 'concept_patient'").fetchone()[0], str(S.CONCEPT_PATIENT_VERSION))


if __name__ == '__main__':
    unittest.main()
