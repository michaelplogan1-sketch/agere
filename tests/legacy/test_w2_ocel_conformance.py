import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import guideline_conformance as G, ocel


def fixture(path):
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE concept_patient(concept, kind, patient_id, records, first_date, last_date);
    CREATE TABLE stay(patient_id, admit_date, discharge_date, type, organization_id, stay_source);
    CREATE TABLE visit(encounter_id, patient_id, event_date, setting);
    CREATE TABLE fill(fill_id, patient_id, fill_date, drug_class, supply_end);
    CREATE TABLE measurement(record_id, patient_id, event_date, concept, result_current);
    """)
    db.executemany('INSERT INTO concept_patient VALUES (?,?,?,?,?,?)', [
        ('heart_failure', 'condition', 'A', 1, '2028-01-01', '2030-01-01'), ('heart_failure', 'condition', 'B', 1, '2028-01-01', '2030-01-01'),
        ('diabetes', 'condition', 'D1', 1, '2027-01-01', '2030-01-01'), ('diabetes', 'condition', 'D2', 1, '2027-01-01', '2030-01-01'),
        ('chronic_kidney_disease', 'condition', 'K1', 1, '2027-01-01', '2030-01-01')])
    db.executemany('INSERT INTO stay VALUES (?,?,?,?,?,?)', [
        ('A', '2029-10-01', '2029-10-05', 'inpatient', 'H1', 'adt'), ('A', '2029-10-01', '2029-10-05', 'inpatient', 'H1', 'payer_claim'),
        ('B', '2029-11-01', '2029-11-03', 'inpatient', 'H1', 'encounters'),
        ('B', '2030-05-20', '2030-05-25', 'inpatient', 'H1', 'encounters')])  # window still open at the as-of date
    db.executemany('INSERT INTO visit VALUES (?,?,?,?)', [('v1', 'A', '2029-10-09', 'primary_care'), ('v2', 'B', '2029-11-20', 'specialty')])
    db.executemany('INSERT INTO fill VALUES (?,?,?,?,?)', [('f1', 'A', '2029-09-20', 'hf_evidence_based_beta_blocker', '2029-10-20'),
                                                          ('f2', 'B', '2029-11-10', 'sglt2_inhibitor', '2029-12-10')])
    db.executemany('INSERT INTO measurement VALUES (?,?,?,?,?)', [
        ('m1', 'D1', '2029-07-01', 'hba1c', 1), ('m2', 'D1', '2030-02-01', 'hba1c', 1), ('m3', 'D1', '2029-08-01', 'egfr', 1),
        ('m4', 'D1', '2029-08-01', 'uacr', 1), ('m5', 'D2', '2029-07-01', 'hba1c', 1), ('m6', 'K1', '2029-09-01', 'egfr', 1)])
    db.commit(); db.close()


class OcelConformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)
        fixture(self.path / 'semantic.sqlite3')

    def tearDown(self): self.tmp.cleanup()

    def test_rules_and_object_centric_metrics(self):
        r = G.execute(self.path, None, {'pathway': 'all', 'export': 'ocel'}, '2030-05-27')
        rules = {x['rule']: x for x in r['rules']}
        self.assertEqual((rules['HF-FU7']['eligible'], rules['HF-FU7']['conformant'], rules['HF-FU7']['not_yet_evaluable']), (2, 1, 1))
        self.assertEqual((rules['HF-GDMT30']['eligible'], rules['HF-GDMT30']['conformant']), (2, 2))  # A covered at discharge, B filled day 7
        self.assertEqual((rules['DM-A1C6']['eligible'], rules['DM-A1C6']['conformant']), (2, 1))
        self.assertEqual(rules['DM-KED']['conformant'], 1)
        self.assertEqual(rules['CKD-MON']['conformant'], 0)
        oc = r['object_centric']
        self.assertEqual(oc['discharge_reports'], 4)
        self.assertEqual(oc['stay_objects'], 3)  # A's ADT + claim reports are one stay object
        self.assertEqual(oc['flattened_discharge_overcount'], 1)
        self.assertEqual(oc['stays_reported_by_several_sources'], 1)
        doc = r['ocel']
        self.assertTrue({'objectTypes', 'eventTypes', 'objects', 'events'} <= set(doc))
        disch = [e for e in doc['events'] if e['type'] == 'discharge' and any(x['objectId'] == 'stay:A:2029-10-05' for x in e['relationships'])]
        self.assertEqual(len(disch), 2)
        dev = [d for d in r['deviations'] if d['rule'] == 'HF-FU7']
        self.assertEqual([d['patient_id'] for d in dev], ['B'])

    def test_population_and_answer(self):
        r = G.execute(self.path, ['A'], {'pathway': 'heart_failure'}, '2030-05-27')
        self.assertEqual({x['rule'] for x in r['rules']}, {'HF-FU7', 'HF-GDMT30'})
        a = G.answer(r, {'coverage': 'supported_observation', 'unmet_requirements': []}, 'q')
        self.assertEqual(a['answer_type'], 'table'); self.assertIn('HF-FU7 100%', a['value'])

    def test_shared_event_linked_to_two_stays(self):
        stays = ocel.merge_stays([('P', '2030-01-01', '2030-01-02', 'adt', None), ('P', '2030-01-05', '2030-01-06', 'adt', None)])
        log = ocel.build(stays, visits=[('v', 'P', '2030-01-08', 'primary_care')])
        ocel.link_followups(log, [s['id'] for s in stays], 'visit', 7)
        self.assertEqual(ocel.metrics(log)['events_shared_by_several_stays'], {'visit': 1})

    def test_validate_and_route(self):
        with self.assertRaises(ValueError): G.validate({'pathway': 'asthma'})
        with self.assertRaises(ValueError): G.validate({'follow_up_days': 0})
        g, info = G.route('guideline adherence for heart failure discharges within 14 days', None, '2030-05-27')
        self.assertEqual(g['nodes'][0]['parameters'], {'pathway': 'heart_failure', 'follow_up_days': 14})
        self.assertIsNone(G.route('How many patients have heart failure?', None, '2030-05-27'))
        for t in G.TEMPLATES: self.assertIsNotNone(G.route(t, None, '2030-05-27'), t)


if __name__ == '__main__':
    unittest.main()
