"""Native-format detection, OMOP round trip and canonical (Tuva/OMOP) mapping in workspace onboarding, and the
onboarding_review engine that answers questions from those receipts (tiny fixtures; no model calls)."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.clinical import onboarding_review as orv
from clinical_core.agere.onboarding import canonical_map, formats, omop_fixture, round_trip
from clinical_core.agere.onboarding.workspace import Workspace

X12 = ('ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *300101*1200*^*00501*000000001*0*P*:~'
       'GS*HC*S*R*20300101*1200*1*X*005010X222A1~ST*837*0001*005010X222A1~BHT*0019*00*1*20300101*1200*CH~SE*3*0001~GE*1*1~IEA*1*000000001~')
HL7 = 'MSH|^~\\&|EHR|FAC|AGERE|AGERE|20300101120000||ADT^A01|MSG1|P|2.5\rPID|1||P-1^^^FAC^MR||DOE^JANE||19700101|F\r'
FHIR = '{"resourceType":"Patient","id":"p1","gender":"female","birthDate":"1970-01-01"}\n'


def native_dir(root):
    d = Path(root) / 'delivery'
    for rel, text in (('x12/claims_2030-01.x12', X12), ('hl7/adt_2030-01.hl7', HL7), ('fhir_bulk/Patient.ndjson', FHIR)):
        (d / rel).parent.mkdir(parents=True, exist_ok=True); (d / rel).write_text(text)
    (d / 'private').mkdir(); (d / 'private' / 'answers.json').write_text('{}')  # never read
    return d


def omop_db(path, n=6):
    db = sqlite3.connect(path)
    db.executescript(omop_fixture.DDL)
    for i in range(1, n + 1):
        db.execute('INSERT INTO person VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (i, f'P-{i:04d}', 'female' if i % 2 else 'male', 1950 + i, 1, f'{1950 + i}-01-0{i % 9 + 1}', 'white', 'not hispanic',
                    'english', '94110', 'CA', '06075'))
        db.execute('INSERT INTO visit_occurrence(visit_occurrence_id, person_id, visit_start_date, visit_end_date, visit_source_value, '
                   'visit_status_source_value, encounter_number, source_row_id, etl_received_date) VALUES (?,?,?,?,?,?,?,?,?)',
                   (i, i, '2029-03-01', '2029-03-01', 'office', 'completed', f'E-{i}', f'R-V{i}', '2029-03-02'))
        db.execute('INSERT INTO condition_occurrence(condition_occurrence_id, person_id, condition_start_date, condition_source_value, '
                   'condition_source_description, source_row_id, etl_received_date) VALUES (?,?,?,?,?,?,?)',
                   (i, i, '2029-03-01', 'E119', 'Type 2 diabetes', f'R-C{i}', '2029-03-02'))
    db.execute('INSERT INTO observation_period VALUES (1, 1, "2029-01-01", "2029-12-31", 1)')
    db.commit(); db.close()
    return path


class Detection(unittest.TestCase):
    def test_native_delivery_formats_and_standards(self):
        with tempfile.TemporaryDirectory() as t:
            d = native_dir(t)
            det = formats.detect_source(str(d))
            self.assertEqual(det['kind'], 'native_delivery')
            self.assertEqual(det['files'], 3)  # the private folder is never read
            self.assertTrue({'x12', 'hl7', 'fhir'} <= set(det['formats']), det['formats'])
            self.assertIn('X12 837 claim', det['standards'])
            self.assertIn('HL7 v2 ADT^A01', det['standards'])
            self.assertIn('FHIR Patient (bulk ndjson)', det['standards'])
            spec, _ = formats.normalize_source(str(d))
            self.assertTrue(spec.startswith('native://'))

    def test_databases_classified_by_tables(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(formats.detect_source(f"sqlite:///{omop_db(Path(t) / 'omop.sqlite')}")['kind'], 'omop')
            self.assertEqual(formats.classify_tables({'eligibility', 'medical_claim', 'pharmacy_claim'})[0], 'tuva_input_layer')
            self.assertEqual(formats.detect_source('/no/such/file.sqlite')['kind'], 'unknown')

    def test_stream_receipt_shares(self):
        rep = canonical_map.stream_receipt({'labs': 100, 'medical_claims': 300, 'not_a_stream': 100})
        self.assertEqual(rep['records_total'], 500)
        self.assertEqual(rep['records_mapped'], 400)
        self.assertEqual(rep['share_mapped'], 0.8)
        self.assertEqual([u['source'] for u in rep['unmapped']], ['not_a_stream'])


class OmopWorkspace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        src = omop_db(d / 'omop.sqlite')
        with mock.patch.object(round_trip, 'default_reference', return_value=None):  # never read the 8 GB active build
            cls.ws = Workspace.create('Omop Clinic', f'sqlite:///{src}', root=d / 'workspaces')
            cls.ws.run()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_detect_round_trip_canonical_steps_recorded(self):
        steps = self.ws.state['steps']
        self.assertEqual(steps['detect']['source_kind'], 'omop')
        self.assertIn('round_trip', steps)
        self.assertIn('canonical', steps)
        rt = json.loads((self.ws.dir / 'round_trip.json').read_text())
        person = next(c for c in rt['source_parity']['checks'] if c['omop_table'] == 'person')
        self.assertEqual(person['omop_rows'], 6)
        canon = json.loads((self.ws.dir / 'canonical.json').read_text())
        self.assertEqual(canon['source_kind'], 'omop')
        self.assertIn('observation_period', canon['unmapped_source_tables'])

    def test_questions_read_the_workspace_receipts(self):
        def ask(q):
            g = orv.local_question(q)
            self.assertIsNotNone(g, q)
            p = {**g['nodes'][0]['parameters'], 'workspace': self.ws.slug}
            with mock.patch.object(orv, '_workspaces_root', return_value=self.ws.dir.parent):
                return orv.answer(orv.execute(self.ws.dir, None, p, '2030-01-01'), g, q)
        a = ask('Did the OMOP round trip reproduce the counts?')
        self.assertIn('workspace omop-clinic', a['headline'])
        a = ask('Which source fields did not map?')
        self.assertTrue(any(r['source'] == 'observation_period' for r in a['table']), a['table'])
        a = ask('Which source feeds did we onboard and in what formats?')
        self.assertIn('omop', a['headline'])
        a = ask('How much of our data maps to the Tuva canonical model?')
        self.assertEqual(a['unit'], '% of received records')


class Routing(unittest.TestCase):
    def test_exact_routes_and_declines(self):
        topic = lambda q: (orv.local_question(q) or {'nodes': [{'parameters': {}}]})['nodes'][0]['parameters'].get('topic')
        self.assertEqual(topic('Which source feeds did we onboard and in what formats?'), 'feeds')
        self.assertEqual(topic('How much of our data maps to OMOP?'), 'canonical')
        self.assertEqual(topic('Which source fields did not map?'), 'unmapped')
        self.assertEqual(topic('Did the OMOP round trip reproduce the counts?'), 'round_trip')
        self.assertIsNone(topic('How many late files were received?'))  # data-quality family
        self.assertIsNone(topic('How many patients have diabetes?'))
        self.assertEqual(orv.local_question('How much of our data maps to OMOP?')['nodes'][0]['parameters']['model'], 'omop')

    def test_on_planner_menu_and_exact_tier(self):
        from clinical_core.agere.clinical import analysis_graph, capability_registry, economical_planner, graph_planner
        self.assertIn('onboarding_review', analysis_graph.OPERATIONS)
        self.assertIn('onboarding_review', capability_registry.PRODUCT)
        self.assertIn('onboarding_review', graph_planner.CONTRACTS)
        graph, recipe = economical_planner.local_plan('Which source fields did not map?', '2030-05-27')
        self.assertEqual(recipe, 'exact_foundation_onboarding_review')
        self.assertEqual(graph['nodes'][0]['operation'], 'onboarding_review')


if __name__ == '__main__':
    unittest.main()
