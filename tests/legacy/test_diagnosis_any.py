"""diagnosis_any: the governed diagnosis relation of the semantic layer under the ONE condition-evidence rule (decision context,
Mike 2026-10-23: EHR condition records incl. problem-list conditions and laboratory evidence for diabetes / CKD, plus counted payer-claim
diagnoses; encounter diagnoses are not evidence), its idempotent upgrade of an existing semantic.sqlite3, the read-only temp-view
fallback (also over an older table) and the planner-facing documentation. Synthetic fixture only; no model, network or stack database."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import nl2sql, semantic_layer as S, sql_query


def fixture(path, with_meta=True):
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, label TEXT, icd10 TEXT, event_date TEXT,
        recorded_date TEXT, is_diagnosis INTEGER, diagnosis_basis TEXT);
      CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, final_status TEXT, service_from TEXT, counts_in_metrics INTEGER);
      CREATE TABLE payer_claim_diagnosis(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, rank INTEGER, icd10 TEXT,
        icd10_received TEXT, counts_in_metrics INTEGER);
      CREATE TABLE encounter_diagnosis(encounter_id TEXT, patient_id TEXT, rank INTEGER, icd10 TEXT, type TEXT, record_id TEXT,
        source TEXT, event_date TEXT);
      CREATE TABLE problem_list_entry(patient_id TEXT, problem_key TEXT, icd10 TEXT, first_recorded TEXT, record_id TEXT);
      CREATE TABLE concept_patient(concept TEXT, kind TEXT, patient_id TEXT, records INTEGER, first_date TEXT, last_date TEXT);
    """)
    db.executemany('INSERT INTO condition VALUES (?,?,?,?,?,?,?,?,?,?)', [
        ('c1', 'p1', 'conditions', 'opioid_use_disorder', 'OUD', 'f11.20', '2029-02-01', None, 1, None),            # a diagnosis (lower case code)
        ('c2', 'p9', 'condition_assessments', None, 'screen', 'F11.10', '2029-02-02', None, 0, None),              # assessment topic: not a diagnosis
        ('c3', 'p8', 'conditions', 'diabetes', 'A1c high', 'E11.9', None, '2029-02-03', None, 'laboratory_evidence'),  # lab evidence: not a coded diagnosis
        ('c4', 'p7', 'conditions', None, 'problem', 'F11.20', '2029-02-04', None, None, 'ehr_problem_list'),       # EHR problem-list condition
        ('c5', 'p6', 'conditions', None, 'no code', None, '2029-02-05', None, 1, None)])
    db.executemany('INSERT INTO payer_claim VALUES (?,?,?,?,?)', [
        ('k1', 'p2', 'paid', '2029-03-01', 1), ('k2', 'p3', 'denied', '2029-03-01', 1), ('k3', 'p4', 'paid', '2029-03-01', 0)])
    db.executemany('INSERT INTO payer_claim_diagnosis VALUES (?,?,?,?,?,?,?)', [
        ('k1', 'p2', '2029-03-01T10:00', 1, 'F11.20', 'F1120', 1),   # paid, current
        ('k2', 'p3', '2029-03-01', 1, 'F11.20', 'F1120', 1),          # denied: not a paid claim
        ('k3', 'p4', '2029-03-01', 1, 'F11.20', 'F1120', 0)])         # superseded version: counts_in_metrics 0
    db.executemany('INSERT INTO encounter_diagnosis VALUES (?,?,?,?,?,?,?,?)', [
        ('e1', 'p5', 1, 'F11.20', 'principal', 'r1', 'ehr', '2029-04-01'),
        ('e2', 'p10', 1, 'F11.20', 'final', 'r2', 'hl7_adt', '2029-04-02'),
        ('k5', 'p11', 1, 'F11.20', 'principal', 'r3', 'payer_claim', '2029-04-03')])  # a payer ED claim's dx: read through the paid-claim rule
    db.executemany('INSERT INTO problem_list_entry VALUES (?,?,?,?,?)', [('p12', 'F11', 'F11.20', '2029-01-01', 'pl1'), ('p13', 'x', None, '2029-01-01', 'pl2')])
    db.executemany('INSERT INTO concept_patient VALUES (?,?,?,?,?,?)', [('opioid_use_disorder', 'condition', 'p1', 1, None, None)])
    db.commit(); db.close()


def f11(db):
    return {r[0] for r in db.execute("SELECT DISTINCT patient_id FROM diagnosis_any WHERE icd10 LIKE 'F11%'")}


class DiagnosisAny(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'semantic.sqlite3'
        fixture(self.path)

    def test_the_relation_is_the_compilers_condition_evidence(self):
        out = S.upgrade_diagnosis_any(self.path)
        self.assertEqual(out, {'upgraded': True, 'rows': 5})
        db = sqlite3.connect(self.path)
        self.assertEqual(f11(db), {'p1', 'p7', 'p2', 'p3'})   # EHR diagnosis, problem-list condition, counted claims (a denied claim counts)
        by_source = dict(db.execute('SELECT source, count(DISTINCT patient_id) FROM diagnosis_any GROUP BY 1'))
        self.assertEqual(by_source, {'condition': 3, 'payer_claim': 2})
        # not evidence: assessment topic p9, superseded claim p4 (counts_in_metrics 0), uncoded rows p6, encounter diagnoses p5 / p10 / p11,
        # problem_list_entry p12 / p13; p8 is laboratory evidence of DIABETES (E11.9), which counts for diabetes and CKD only
        self.assertFalse({'p9', 'p4', 'p6', 'p5', 'p10', 'p11', 'p12', 'p13'} & {r[0] for r in db.execute('SELECT patient_id FROM diagnosis_any')})
        self.assertIn('p8', {r[0] for r in db.execute("SELECT patient_id FROM diagnosis_any WHERE icd10 LIKE 'E11%'")})
        row = db.execute("SELECT icd10, source, source_detail, event_date, record_ref FROM diagnosis_any WHERE patient_id = 'p2'").fetchone()
        self.assertEqual(row, ('F11.20', 'payer_claim', 'paid', '2029-03-01', 'k1'))
        self.assertEqual(db.execute("SELECT icd10 FROM diagnosis_any WHERE patient_id = 'p1'").fetchone()[0], 'F11.20')  # upper case
        self.assertEqual(db.execute("SELECT count(*) FROM diagnosis_any WHERE icd10 = 'f11.20'").fetchone()[0], 4)     # code compare ignores case
        self.assertEqual(db.execute("SELECT value FROM meta WHERE key = 'diagnosis_any'").fetchone()[0], str(S.DIAGNOSIS_ANY_VERSION))
        self.assertTrue(any('diagnosis_any_code' in r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")))
        db.close()

    def test_condition_alone_undercounts(self):
        S.upgrade_diagnosis_any(self.path)
        db = sqlite3.connect(self.path)
        alone = db.execute("SELECT count(DISTINCT patient_id) FROM condition WHERE icd10 LIKE 'F11%' AND is_diagnosis = 1").fetchone()[0]
        self.assertEqual((alone, len(f11(db))), (1, 4))
        db.close()

    def test_the_upgrade_is_idempotent(self):
        first = S.upgrade_diagnosis_any(self.path)
        second = S.upgrade_diagnosis_any(self.path)
        self.assertTrue(first['upgraded']); self.assertFalse(second['upgraded']); self.assertEqual(first['rows'], second['rows'])
        db = sqlite3.connect(self.path)
        db.execute("INSERT INTO condition VALUES ('c6', 'p20', 'conditions', NULL, 'x', 'F11.21', '2029-05-01', NULL, 1, NULL)"); db.commit(); db.close()
        self.assertFalse(S.upgrade_diagnosis_any(self.path)['upgraded'])            # already at this version: left alone
        self.assertEqual(S.upgrade_diagnosis_any(self.path, force=True), {'upgraded': True, 'rows': 6})  # force rebuilds (one table, no duplicates)
        db = sqlite3.connect(self.path)
        self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name = 'diagnosis_any'").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE type = 'index' AND name LIKE 'diagnosis_any%'").fetchone()[0], 2)
        db.close()

    def test_a_stack_directory_is_accepted(self):
        self.assertTrue(S.upgrade_diagnosis_any(Path(self.tmp.name))['upgraded'])

    def test_a_database_without_the_table_reads_it_as_a_temp_view_without_a_write(self):
        before = self.path.read_bytes()
        db = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
        self.assertEqual(S.ensure_diagnosis_view(db), 'view')
        self.assertEqual(f11(db), {'p1', 'p7', 'p2', 'p3'})
        self.assertEqual(S.ensure_diagnosis_view(db), 'view')                        # repeated: no error
        db.close()
        self.assertEqual(self.path.read_bytes(), before)
        S.upgrade_diagnosis_any(self.path)
        db = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
        self.assertEqual(S.ensure_diagnosis_view(db), 'table')
        db.close()

    def test_sql_query_registration_provides_it(self):
        db = sql_query.register(sqlite3.connect(f'file:{self.path}?mode=ro', uri=True))
        self.assertEqual(db.execute("SELECT count(DISTINCT patient_id) FROM diagnosis_any WHERE icd10 LIKE 'F11%'").fetchone()[0], 4)
        db.close()

    def test_a_database_with_no_diagnosis_source_gets_nothing(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE patient(patient_id TEXT)')
        self.assertIsNone(S.ensure_diagnosis_view(db))
        db.close()

    def test_planners_are_told_where_diagnoses_live(self):
        doc = S.SCHEMA_DOC
        self.assertIn('diagnosis_any', doc['condition']); self.assertIn('concept_patient', doc['condition'])
        self.assertIn('EHR condition records ONLY', doc['condition'])
        self.assertIn('counts_in_metrics', doc['diagnosis_any']); self.assertIn('DISTINCT patient_id', doc['diagnosis_any'])
        self.assertIn('diagnosis_any', doc['concept_patient'])
        self.assertEqual(S.SOURCE_COVERAGE['diagnoses']['default_relation'], 'diagnosis_any')
        self.assertIn('diagnosis_any(', nl2sql.TABLES)
        self.assertIn('diagnosis_any', nl2sql.RULES)
        self.assertTrue(any('diagnosis_any' in sql for _, sql in nl2sql.EXAMPLES))


if __name__ == '__main__':
    unittest.main()
