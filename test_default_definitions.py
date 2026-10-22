"""Named standard default definitions: AHRQ PQI, NYU/Billings ED avoidability, measure gaps, PDC >= 0.8."""
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import default_definitions as D

PLAN = {'coverage': 'supported_observation', 'unmet_requirements': []}


def warehouse():
    db = sqlite3.connect(':memory:')
    db.executescript('''
    CREATE TABLE patient(patient_id TEXT, birth_date TEXT, pcp_practice_id TEXT, payer_id TEXT);
    INSERT INTO patient VALUES('A','1950-01-01','ORG1','P1'),('B','1995-01-01','ORG1','P1'),('C','1960-01-01','ORG2','P2'),('D','1940-01-01','ORG2','P2');
    CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type_concept TEXT, event_date TEXT, end_date TEXT, principal_icd10 TEXT);
    INSERT INTO visit VALUES
      ('i1','A','inpatient_admission','2030-01-10','2030-01-14','I50.9'),   -- PQI 08 heart failure
      ('i2','B','inpatient_admission','2030-02-01','2030-02-03','J45.901'), -- asthma at 35: PQI 15, not PQI 05
      ('i3','C','inpatient_admission','2030-03-01','2030-03-05','K35.80'),  -- appendicitis: not ambulatory-care sensitive
      ('i4','D','inpatient_admission','2028-03-01','2028-03-05','I50.9'),   -- outside the period
      ('e1','A','emergency_visit','2030-04-01','2030-04-01','J06.9'),       -- URI: 1.0 avoidable
      ('e2','C','emergency_visit','2030-04-02','2030-04-02','I21.4'),       -- MI: 0 avoidable
      ('e3','D','emergency_visit','2030-04-03','2030-04-03','S52.501A'),    -- injury: special category
      ('e4','B','emergency_visit','2030-04-04','2030-04-04','R10.9');       -- abdominal pain: 0.5
    CREATE TABLE claim(claim_id TEXT, patient_id TEXT, service_date TEXT, allowed_cents INTEGER);
    INSERT INTO claim VALUES('c1','A','2030-01-11',1000000),('c2','B','2030-02-01',400000),('c3','C','2030-03-02',900000),
      ('c4','A','2030-04-01',50000),('c5','C','2030-04-02',300000),('c6','D','2030-04-03',80000),('c7','B','2030-04-04',60000);
    ''')
    return db


class Lookups(unittest.TestCase):
    def test_nyu_categories_and_pqi_families(self):
        self.assertEqual(D.ed_category('J06.9'), ('weights', (.70, .30, 0, 0)))
        self.assertEqual(D.ed_category('E11.65')[1], (.10, .40, .40, .10))  # longest prefix wins over E11
        self.assertEqual([D.ed_category(c)[0] for c in ('S72.001A', 'F10.20', 'F11.10', 'F32.9', 'Q99.9', None)],
                         ['injury', 'alcohol', 'substance', 'mental_health', 'unclassified', 'unclassified'])
        self.assertEqual(D.pqi_component('I50.9', 70)['id'], 'pqi_08')
        self.assertEqual(D.pqi_component('J45.901', 35)['id'], 'pqi_15')
        self.assertIsNone(D.pqi_component('K35.80', 50))
        self.assertIn('Agere approximations', D.PROVENANCE)
        self.assertEqual(D.detect('Where is the avoidable spend in our MSSP population?'), ['avoidable_spend'])


class Utilization(unittest.TestCase):
    def setUp(self):
        self.db = warehouse(); self.addCleanup(self.db.close)

    def test_pqi_admissions(self):
        r = D.pqi_admissions(self.db, '2029-06-01', '2030-05-27')
        self.assertEqual([(e['patient_id'], e['pqi']) for e in r['events']], [('A', 'pqi_08'), ('B', 'pqi_15')])
        self.assertEqual((r['patient_ids'], r['allowed_cents']), (['A', 'B'], 1400000))

    def test_ed_avoidability(self):
        r = D.ed_avoidability(self.db, '2029-06-01', '2030-05-27')
        self.assertEqual(r['visits'], 4)
        self.assertEqual(r['expected_avoidable_visits'], 1.5)  # URI 1.0 + abdominal pain 0.5; MI 0; injury excluded
        self.assertEqual(r['special_categories'], {'injury': 1})
        self.assertEqual(r['patient_ids'], ['A', 'B'])
        self.assertEqual(r['avoidable_allowed_cents'], 80000)  # 50000 x 1.0 + 60000 x 0.5

    def test_avoidable_spend_by_practice_names_its_definition(self):
        r = D.run(self.db, None, D.validate({'definition': 'avoidable_spend', 'group_by': 'practice'}), '2030-05-27')
        self.assertEqual(r['allowed_cents'], 1480000)
        self.assertEqual(r['rows'][0], {'group': 'ORG1', 'avoidable_allowed_cents': 1480000, 'pqi_admissions': 2,
                                        'pqi_allowed_cents': 1400000, 'ed_visits': 2, 'expected_avoidable_ed_visits': 1.5,
                                        'avoidable_ed_allowed_cents': 80000, 'patients': 2})
        a = D.answer(r, PLAN, 'Where is the avoidable spend in our MSSP population?')
        self.assertEqual((a['status'], a['patient_ids']), ('answered', ['A', 'B']))
        self.assertIn('AHRQ PQI', a['definitions'][0]['term'])
        self.assertIn('$14,800', a['value'])
        scoped = D.run(self.db, ['C', 'D'], D.validate({'definition': 'avoidable_spend'}), '2030-05-27')
        self.assertEqual((scoped['allowed_cents'], scoped['patient_ids']), (0, []))

    def test_governed_macros_execute(self):
        from clinical_core.agere.clinical.age import register
        register(self.db)
        catalog = SimpleNamespace(tables={'visit': ['principal_icd10']})
        pqi = self.db.execute(f"SELECT patient_id, pqi FROM {D.macro_sql('pqi_admission', catalog)} ORDER BY patient_id").fetchall()
        self.assertEqual(pqi, [('A', 'pqi_08'), ('B', 'pqi_15'), ('D', 'pqi_08')])  # macro rows carry no period; the step filters
        ed = dict(self.db.execute(f"SELECT encounter_id, avoidable_probability FROM {D.macro_sql('ed_avoidability', catalog)}"))
        self.assertEqual(ed, {'e1': 1.0, 'e2': 0, 'e3': 0, 'e4': 0.5})


class MeasureMembership(unittest.TestCase):
    rows = [{'patient_id': 'A', 'excluded': False, 'met': True, 'pdc': .91}, {'patient_id': 'B', 'excluded': False, 'met': False, 'pdc': .52},
            {'patient_id': 'C', 'excluded': True, 'met': False}, {'patient_id': 'D', 'excluded': False, 'met': False, 'pdc': .79}]

    def test_measure_gap_is_denominator_minus_numerator(self):
        with patch('clinical_core.agere.clinical.measures.evaluate_db', return_value={'patient_rows': self.rows, 'warnings': []}):
            r = D.run(None, None, D.validate({'definition': 'measure_gap', 'measure': 'bcs'}), '2030-05-27')
        self.assertEqual((r['denominator_patient_ids'], r['numerator_patient_ids'], r['gap_patient_ids']), (['A', 'B', 'D'], ['A'], ['B', 'D']))
        self.assertEqual((r['patient_ids'], r['rate']), (['B', 'D'], 0.3333))
        a = D.answer(r, PLAN, 'Which patients have open breast cancer screening gaps?')
        self.assertEqual((a['count'], a['membership_role'], a['numerator_patient_ids']), (2, 'gap', ['A']))
        with self.assertRaises(ValueError): D.validate({'definition': 'measure_gap'})

    def test_pdc_uses_the_library_measure_or_the_pqa_criterion(self):
        m, governed = D.pdc_measure(['statin'])
        self.assertEqual((m['id'], governed), ('pdc_statins', True))
        m, governed = D.pdc_measure(['sglt2_inhibitor'])
        crit = next(c for c in m['numerator']['criteria'] if c['type'] == 'pdc')
        self.assertEqual((governed, crit['classes'], crit['threshold'], crit['min_fills']), (False, ['sglt2_inhibitor'], 0.8, 2))
        with patch('clinical_core.agere.clinical.measures.evaluate_db', return_value={'patient_rows': self.rows, 'warnings': []}) as ev:
            r = D.run(None, ['A', 'B', 'C', 'D'], D.validate({'definition': 'pdc_adherence', 'drug_classes': ['sglt2_inhibitor'],
                                                              'select': 'numerator'}), '2030-05-27')
        self.assertEqual(ev.call_args.kwargs['keep'], {'A', 'B', 'C', 'D'})
        self.assertEqual((r['patient_ids'], r['gap_patient_ids']), (['A'], ['B', 'D']))

    def test_declined_measure_is_reported_not_zero(self):
        with patch('clinical_core.agere.clinical.measures.evaluate_db', return_value={'declined': True, 'missing_inputs': ['Missing input: x']}):
            r = D.run(None, None, D.validate({'definition': 'measure_gap', 'measure': 'bcs'}), '2030-05-27')
        a = D.answer(r, PLAN, 'q')
        self.assertEqual((a['status'], a['missing']), ('unsupported', ['Missing input: x']))


class GraphExecution(unittest.TestCase):
    """Both engines execute as registered graph operations through analysis_graph."""
    def test_engines_run_through_the_analysis_graph(self):
        import test_analysis_graph as graph_fixtures
        from clinical_core.agere.clinical import analysis_graph, semantic_layer
        fixture = graph_fixtures.GraphExecution(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        semantic_layer.build(fixture.root)
        with sqlite3.connect(fixture.root/'semantic.sqlite3') as db:
            cols = {r[1] for r in db.execute('PRAGMA table_info(visit)')}
            if 'principal_icd10' not in cols: db.execute('ALTER TABLE visit ADD COLUMN principal_icd10 TEXT')
            db.execute("INSERT INTO visit(encounter_id,patient_id,type_concept,event_date,principal_icd10) VALUES('e1','p1','emergency_visit','2026-05-01','J06.9')")
            db.execute("INSERT INTO medication(record_id,patient_id,evidence,drug_class,status,event_date) VALUES('m1','p2','listed','statin','active','2026-05-01')")
        def run(op, params):
            graph = {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                     'nodes': [{'id': 'base', 'operation': 'cohort', 'inputs': [], 'purpose': 'Everyone',
                                'parameters': {'specification': graph_fixtures.empty()}},
                               {'id': 'engine', 'operation': op, 'inputs': ['base'], 'parameters': params, 'purpose': op}]}
            return analysis_graph.execute(fixture.stack, graph, question='q')['answer']
        ed = run('default_definition', {'definition': 'ed_avoidability'})
        self.assertEqual((ed['status'], ed['patient_ids']), ('answered', ['p1']))
        self.assertIn('NYU/Billings', ed['method'])
        ms = run('medication_state', {'subjects': ['statin'], 'states': ['listed_not_filled']})
        self.assertEqual((ms['status'], ms['patient_ids']), ('answered', ['p2']))


if __name__ == '__main__':
    unittest.main()
