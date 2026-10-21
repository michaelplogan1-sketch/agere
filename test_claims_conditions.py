"""Condition populations include counted payer-claim diagnoses (Mike, 2026-10-21)."""
import sqlite3
import unittest

from clinical_core.agere.clinical import concepts
from clinical_core.agere.clinical.clinical_definitions import PHENOTYPES


class ClaimsConditions(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.executescript('''
        CREATE TABLE condition(patient_id TEXT, concept TEXT, event_date TEXT, is_diagnosis INTEGER, diagnosis_basis TEXT);
        INSERT INTO condition VALUES ('ehr','diabetes','2029-01-01',1,'ehr_encounter'),
          ('problem','diabetes','2029-01-01',NULL,'ehr_problem_list'), ('rule_out','diabetes','2029-01-01',0,'ehr_encounter');
        CREATE TABLE payer_claim_diagnosis(patient_id TEXT, icd10 TEXT, service_from TEXT, counts_in_metrics INTEGER);
        INSERT INTO payer_claim_diagnosis VALUES ('claims_only','E11.9','2029-02-01',1), ('denied','E11.65','2029-02-01',0),
          ('other','I10','2029-02-01',1), ('hfref_claim','I50.22','2029-03-01',1);
        CREATE TABLE note_fact(patient_id TEXT, fact_type TEXT, value, event_date TEXT, negated INTEGER, experiencer TEXT);
        ''')
        self.tables = {t: [r[1] for r in self.db.execute(f'PRAGMA table_info({t})')]
                       for t in ('condition', 'payer_claim_diagnosis', 'note_fact')}

    def patients(self, sql):
        return sorted(r[0] for r in self.db.execute(f'SELECT DISTINCT patient_id FROM {sql}'))

    def test_claims_only_member_is_in_the_condition_population(self):
        sql = concepts.condition_evidence_sql(['diabetes'], self.tables)
        self.assertEqual(self.patients(sql), ['claims_only', 'ehr', 'problem'])
        sources = dict(self.db.execute(f'SELECT patient_id, source FROM {sql}'))
        self.assertEqual(sources['claims_only'], 'payer_claim_diagnosis')

    def test_without_claims_table_falls_back_to_ehr(self):
        sql = concepts.condition_evidence_sql(['diabetes'], {'condition': self.tables['condition']})
        self.assertEqual(self.patients(sql), ['ehr', 'problem'])

    def test_hfref_phenotype_counts_a_coded_claim(self):
        rows = self.db.execute(f"SELECT patient_id, hfref_basis FROM {PHENOTYPES['hfref']['build'](self.tables)}").fetchall()
        self.assertEqual(rows, [('hfref_claim', 'coded_hfref')])
        self.assertIn('counted payer claim', PHENOTYPES['hfref']['disclosure'])


if __name__ == '__main__':
    unittest.main()
