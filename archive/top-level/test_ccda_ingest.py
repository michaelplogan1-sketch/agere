"""C-CDA R2.1 ingestion: landing tables, narrative into document_text, negation, hostile XML, delivery integration."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.ingest import ccda, landing
from clinical_core.agere.onboarding import ccda_fixture as F
from clinical_core.agere.onboarding import formats

PATIENT = {'id': 'P100', 'mrn': 'MRN-100', 'name': 'Ada Test', 'sex': 'female', 'birth_date': '1961-04-02', 'date': '2030-03-15',
           'problems': [{'label': 'Type 2 diabetes mellitus', 'icd10': 'E11.9', 'snomed': '44054006', 'onset': '2019-02-01'},
                        {'label': 'Essential hypertension', 'icd10': 'I10', 'onset': '2015-06-10', 'resolved': '2029-01-01'}],
           'medications': [{'name': 'metformin 1000 MG Oral Tablet', 'rxnorm': '861004', 'start': '2019-03-01', 'dose': 1000, 'dose_unit': 'mg', 'status': 'active'}],
           'allergies': [{'none': True}],
           'results': [{'name': 'Hemoglobin A1c', 'loinc': '4548-4', 'value': 7.4, 'unit': '%', 'date': '2030-02-10', 'range': '4.0-5.6 %', 'panel': 'Glycemic', 'panel_loinc': '55454-3'}],
           'vitals': [{'name': 'Systolic blood pressure', 'loinc': '8480-6', 'value': 138, 'unit': 'mm[Hg]', 'date': '2030-03-15'}]}


class CcdaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / 'raw'
        (self.raw / 'hie' / '2030-03').mkdir(parents=True)
        (self.raw / 'hie' / '2030-03' / 'ada.xml').write_text(F.render(PATIENT))
        self.land = Path(self.tmp.name) / 'landing.sqlite'

    def ingest(self):
        stats = landing.ingest(self.raw, self.land)
        return stats, sqlite3.connect(self.land)

    def test_parser_registered_and_detects(self):
        self.assertIn('ccda', [m.__name__.rsplit('.', 1)[-1] for m in landing.parsers()])
        self.assertTrue(ccda.detect(self.raw / 'hie' / '2030-03' / 'ada.xml'))
        other = Path(self.tmp.name) / 'x.xml'; other.write_text('<root/>')
        self.assertFalse(ccda.detect(other))

    def test_landing_tables(self):
        stats, db = self.ingest()
        self.assertEqual(stats['by_parser'], {'ccda': 1}); self.assertEqual(stats['errors'], 0)
        doc = db.execute('SELECT document_id, doc_type_code, patient_name, sex, birth_date, mrn, effective, sections FROM ccda_document').fetchone()
        self.assertEqual(doc[1:], ('34133-9', 'Ada Test', 'female', '1961-04-02', 'MRN-100', '2030-03-15', 5))
        probs = db.execute('SELECT display, icd10, snomed, onset, resolved, negated FROM ccda_problem ORDER BY onset').fetchall()
        self.assertEqual(probs[0][0], 'Essential hypertension'); self.assertEqual(probs[0][1], 'I10'); self.assertEqual(probs[0][4], '2029-01-01')
        self.assertEqual((probs[1][1], probs[1][2]), ('E11.9', '44054006'))
        med = db.execute('SELECT rxnorm, display, dose_value, dose_unit, start FROM ccda_medication').fetchone()
        self.assertEqual(med, ('861004', 'metformin 1000 MG Oral Tablet', 1000.0, 'mg', '2019-03-01'))
        res = db.execute('SELECT loinc, value, unit, effective, reference_range, panel FROM ccda_result').fetchone()
        self.assertEqual(res, ('4548-4', 7.4, '%', '2030-02-10', '4.0-5.6 %', 'Glycemic'))
        vit = db.execute('SELECT loinc, value, unit FROM ccda_vital').fetchone()
        self.assertEqual(vit, ('8480-6', 138.0, 'mm[Hg]'))

    def test_no_known_allergy_is_negated_not_an_allergy(self):
        _, db = self.ingest()
        alg = db.execute('SELECT allergen, negated FROM ccda_allergy').fetchall()
        self.assertEqual(alg, [(None, 1)])

    def test_entries_carry_fhir_shaped_resources(self):
        _, db = self.ingest()
        cond = json.loads(db.execute('SELECT resource FROM ccda_problem WHERE icd10=?', ('E11.9',)).fetchone()[0])
        self.assertEqual(cond['resourceType'], 'Condition'); self.assertEqual(cond['onsetDateTime'], '2019-02-01')
        obs = json.loads(db.execute('SELECT resource FROM ccda_result').fetchone()[0])
        self.assertEqual(obs['valueQuantity'], {'value': 7.4, 'unit': '%'}); self.assertEqual(obs['subject']['identifier']['value'], 'MRN-100')

    def test_narrative_lands_in_document_text_for_passage_indexes(self):
        _, db = self.ingest()
        status, engine, text, dos = db.execute('SELECT status, engine, text, date_of_service FROM document_text').fetchone()
        self.assertEqual((status, engine, dos), ('text_layer', 'ccda_xml', '2030-03-15'))
        self.assertIn('Type 2 diabetes mellitus', text); self.assertIn('metformin', text)

    def test_hostile_and_broken_documents_land_as_issues(self):
        bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><ClinicalDocument xmlns="urn:hl7-org:v3"><title>&a;</title></ClinicalDocument>')
        (self.raw / 'hie' / '2030-03' / 'bomb.xml').write_text(bomb)
        (self.raw / 'hie' / '2030-03' / 'cut.xml').write_text('<ClinicalDocument xmlns="urn:hl7-org:v3"><title>cut off')
        _, db = self.ingest()
        codes = dict(db.execute('SELECT _file, code FROM ccda_parse_issue').fetchall())
        self.assertEqual(codes['hie/2030-03/bomb.xml'], 'doctype_refused'); self.assertEqual(codes['hie/2030-03/cut.xml'], 'xml_parse_error')

    def test_onboarding_standard_label(self):
        label = formats.standard('ccda', self.raw / 'hie' / '2030-03' / 'ada.xml', 'hie')
        self.assertTrue(label.startswith('C-CDA R2.1')); self.assertIn('Continuity of Care', label)

    def test_delivery_manifest_counts_ccda(self):
        from clinical_core.agere.ingest import delivery
        self.assertIn('ccda_parse_issue', delivery.ISSUE_TABLES); self.assertIn('ccda', delivery.ACTIVITY_PARSERS)


if __name__ == '__main__':
    unittest.main()
