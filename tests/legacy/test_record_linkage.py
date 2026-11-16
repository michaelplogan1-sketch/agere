"""record_linkage: Fellegi-Sunter duplicate patient records as a governed engine on the question path."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import record_linkage as rl

PEOPLE = [
    ('P-1', 'Dorothy Evans', '1948-08-15', 'F', '745 Center St', '01059', '555-201-1111', 'PR-1', 'PAY-A'),
    ('P-2', 'Dottie Evans', '1948-08-15', 'F', '745 Center Street', '01059', '555-201-1111', 'PR-2', 'PAY-A'),  # duplicate of P-1
    ('P-3', 'Robert Stone', '1960-02-03', 'M', '12 Oak Ave', '02110', '555-300-2222', 'PR-1', 'PAY-B'),
    ('P-4', 'Bob Stone', '1960-03-02', 'M', '12 Oak Avenue', '02110', '555-300-2222', 'PR-1', 'PAY-B'),      # day/month swapped
    ('P-5', 'Maria Lopez', '1975-05-05', 'F', '9 Elm Rd', '30301', '555-400-3333', 'PR-2', 'PAY-A'),
    ('P-6', 'Maria Lopez', '1975-05-05', 'F', '400 Pine Dr', '94101', '555-999-0000', 'PR-2', 'PAY-A'),      # member id shared
    ('P-7', 'Ann Wu', '1990-01-01', 'F', '1 Main St', '10001', '555-111-1111', 'PR-3', 'PAY-B'),
    ('P-8', 'James Park', '1955-07-07', 'M', '2 Main St', '10001', '555-111-2222', 'PR-3', 'PAY-B'),
]
U = {'first': 0.01, 'last': 0.01, 'birth_date': 0.001, 'sex': 0.5, 'address': 0.001, 'postal': 0.01, 'phone': 0.001}


def fixture(root):
    root = Path(root)
    st = sqlite3.connect(root / 'stack.sqlite3')
    st.execute('CREATE TABLE patients(id TEXT, payload TEXT)')
    for pid, name, dob, sex, addr, zip_, phone, *_ in PEOPLE:
        st.execute('INSERT INTO patients VALUES (?,?)', (pid, json.dumps({'display_name': name, 'birth_date': dob, 'sex_reported': sex,
                                                                        'address_line': addr, 'postal_code': zip_, 'phone': phone})))
    st.commit(); st.close()
    se = sqlite3.connect(root / 'semantic.sqlite3')
    se.executescript("CREATE TABLE meta(key TEXT, value TEXT); INSERT INTO meta VALUES ('source_digest','d1');"
                     "CREATE TABLE patient(patient_id TEXT, pcp_practice_id TEXT, payer_id TEXT);"
                     "CREATE TABLE enrollment_span(patient_id TEXT, payer_id TEXT, member_id TEXT);")
    for pid, *_, practice, payer in PEOPLE:
        se.execute('INSERT INTO patient VALUES (?,?,?)', (pid, practice, payer))
    se.executemany('INSERT INTO enrollment_span VALUES (?,?,?)', [('P-5', 'PAY-A', 'M-77'), ('P-6', 'PAY-A', 'M-77'), ('P-1', 'PAY-A', 'M-1')])
    se.commit(); se.close()
    return root


class RecordLinkageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = fixture(self.tmp.name)
        self._u = rl.pl.DEFAULTS['u_override']
        rl.pl.DEFAULTS['u_override'] = U  # an 8-chart population cannot estimate u-probabilities

    def tearDown(self):
        rl.pl.DEFAULTS['u_override'] = self._u
        self.tmp.cleanup()

    def test_links_and_cache(self):
        ids = rl.enterprise_person_ids(self.path)
        self.assertEqual(ids['P-1'], ids['P-2'])
        self.assertEqual(ids['P-3'], ids['P-4'])
        self.assertEqual(ids['P-5'], 'AGP-P-5')
        self.assertNotIn('P-7', ids)
        self.assertEqual(rl.person_of(self.path, 'P-8'), 'P-8')
        cached = json.loads((self.path / rl.CACHE).read_text())
        self.assertEqual(cached['source_digest'], 'd1')
        _, links = rl.decide(cached, threshold=25)  # name + birth date alone (~17 bits) no longer link P-5/P-6 ...
        self.assertEqual({l['rule'] for l in links if l['a'] == 'P-5'}, {'member_id'})  # ... the shared member ID still does

    def test_threshold_is_the_lever(self):
        self.assertLess(len(rl.decide(rl.build(self.path), 25)[1]), len(rl.decide(rl.build(self.path), 15)[1]) + 1)
        no_member = {**rl.build(self.path), 'pairs': [{**p, 'shared_member_id': False} for p in rl.build(self.path)['pairs']]}
        self.assertNotIn('P-5', rl.decide(no_member, 25)[0])

    def test_execute_outputs_and_population(self):
        r = rl.execute(self.path, None, {'output': 'summary', 'group_by': 'practice'}, '2030-01-01')
        self.assertEqual(r['persons_with_duplicates'], 3)
        self.assertEqual(r['extra_charts'], 3)
        self.assertTrue(any(row['practice'] == 'PR-1' for row in r['rows']))
        pairs = rl.execute(self.path, None, {'output': 'pairs'}, '2030-01-01')
        self.assertIn('birth_date_agreement', pairs['rows'][0])
        scoped = rl.execute(self.path, ['P-2'], {}, '2030-01-01')
        self.assertEqual(scoped['patient_ids'], ['P-1', 'P-2'])
        a = rl.answer(r, {'coverage': 'supported_observation'}, 'How many patients have more than one chart?')
        self.assertEqual(a['count'], 3)
        self.assertIn('Fellegi-Sunter', a['method'])

    def test_local_question_routing(self):
        for q, out, by in [('Which patients look like duplicate records?', 'patients', 'none'),
                           ('How many patients have more than one chart?', 'summary', 'none'),
                           ('How many duplicate patient records are there by practice?', 'summary', 'practice')]:
            g = rl.local_question(q)
            self.assertEqual(g['nodes'][0]['operation'], 'record_linkage', q)
            self.assertEqual((g['nodes'][0]['parameters']['output'], g['nodes'][0]['parameters']['group_by']), (out, by), q)
        g = rl.local_question('Which patients have more than 2 charts?')
        self.assertEqual(g['nodes'][0]['parameters']['more_than'], 2)
        self.assertEqual(rl.execute(self.path, None, g['nodes'][0]['parameters'], '2030-01-01')['patient_ids'], [])
        for q in ['How many duplicate lab results were received in 2029?', 'How many HL7 messages were duplicated?',
                  'How many duplicate claims by month?']:
            self.assertIsNone(rl.local_question(q), q)

    def test_registered_on_planner_menu(self):
        from clinical_core.agere.clinical import analysis_graph, capability_registry, graph_planner, graph_contracts, economical_planner
        self.assertIn('record_linkage', analysis_graph.OPERATIONS)
        self.assertIn('record_linkage', capability_registry.PRODUCT)
        self.assertIn('record_linkage', graph_planner.CONTRACTS)
        self.assertIn('record_linkage', graph_contracts.TYPED)
        graph, recipe = economical_planner.local_plan('Which patients look like duplicate records?', '2030-05-27')
        self.assertEqual(recipe, 'exact_foundation_record_linkage')
        self.assertEqual(graph['nodes'][0]['parameters']['output'], 'patients')


if __name__ == '__main__':
    unittest.main()
