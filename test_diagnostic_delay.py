"""Diagnostic delay look-back: planted delayed diagnoses, undiagnosed review prompts, ED look-forward, routing (no stealing), wiring, real-v20 smoke."""
import json, os, re, shutil, sqlite3, subprocess, tempfile, unittest
from pathlib import Path

from clinical_core.agere.clinical import (analysis_graph, capability_registry, graph_contracts, graph_planner, local_compiler,
                                          diagnostic_delay as dd)
from clinical_core.agere.clinical.analysis_graph import build_answer, validate
from test_loop_closure import routing_profile, REAL_V20

ROOT = Path(__file__).resolve().parent
AS_OF = '2030-05-27'
DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, age_reported INTEGER, sex_reported TEXT, pcp_clinician_id TEXT, pcp_practice_id TEXT);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, value_canonical REAL, unit_canonical TEXT,
  abnormal_flag TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT);
CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, type_concept TEXT, status TEXT, event_date TEXT, organization_id TEXT,
  attending_clinician_id TEXT, principal_icd10 TEXT);
CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, label TEXT, icd10 TEXT, snomed TEXT, onset TEXT,
  event_date TEXT, recorded_date TEXT, available_date TEXT, organization_id TEXT);
CREATE TABLE encounter_diagnosis(encounter_id TEXT, patient_id TEXT, rank INTEGER, icd10 TEXT, type TEXT, record_id TEXT);
CREATE TABLE note_fact(patient_id TEXT, record_id TEXT, stream TEXT, fact_type TEXT, value, unit TEXT, negated INTEGER, temporality TEXT,
  event_date TEXT, evidence_text TEXT, qualifier TEXT);
CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT,
  organization_id TEXT, payload TEXT);
CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, organization_id TEXT, readmitted_30d INTEGER,
  days_to_readmission INTEGER, discharge_known INTEGER, los_days INTEGER);
"""


def fixture():
    root = Path(tempfile.mkdtemp())
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.executescript(DDL)
    db.execute("INSERT INTO meta VALUES ('version','semantic-layer/13'), ('as_of', ?)", (AS_OF,))
    for i in range(1, 9):
        db.execute('INSERT INTO patient VALUES (?,?,?,?,?)', (f'd{i}', 62, 'F', 'drA' if i <= 4 else 'drB', 'PR1' if i <= 4 else 'PR2'))
    k = [0]

    def lab(pid, concept, v, d):
        k[0] += 1; db.execute('INSERT INTO measurement VALUES (?,?,?,?,?,?,?,?,?,?)', (f'm{k[0]}', pid, 'labs', concept, v, None, None, d, d, d))

    def visit(pid, d, tc='primary_care_visit', icd=None, typ='office'):
        k[0] += 1; db.execute('INSERT INTO visit VALUES (?,?,?,?,?,?,?,?,?)', (f'e{k[0]}', pid, typ, tc, 'completed', d, 'PR1', 'drA', icd))
        return f'e{k[0]}'

    def cond(pid, concept, icd, onset, ev=None):
        k[0] += 1; db.execute('INSERT INTO condition VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (f'c{k[0]}', pid, 'conditions', concept, concept, icd, None, onset, ev or onset, ev or onset, ev or onset, 'PR1'))

    for i in range(1, 9): visit(f'd{i}', '2026-01-01')                         # observation starts 2026 for everyone
    # d1: incident CKD; eGFR < 60 twice >= 90 days apart (criterion 2027-05-01), two visits before the 2028-01-15 diagnosis
    lab('d1', 'egfr', 55, '2027-01-10'); lab('d1', 'egfr', 52, '2027-05-01'); visit('d1', '2027-06-01'); visit('d1', '2027-09-01')
    cond('d1', 'chronic_kidney_disease', 'N18.3', '2028-01-15')
    cond('d2', 'chronic_kidney_disease', 'N18.2', '2010-05-01'); lab('d2', 'egfr', 50, '2027-01-01'); lab('d2', 'egfr', 49, '2027-06-01')  # prevalent
    lab('d3', 'egfr', 50, '2028-01-01'); lab('d3', 'egfr', 48, '2028-06-01'); visit('d3', '2029-01-01')        # criterion met, never diagnosed
    lab('d4', 'egfr', 55, '2030-01-01')                                                                           # one value, never repeated
    lab('d5', 'egfr', 58, '2029-01-01'); lab('d5', 'egfr', 57, '2029-02-01')                                     # repeated within 90 days: no prompt
    # d6: incident diabetes from an encounter diagnosis; A1c 7.0 then glucose 250 (criterion 2028-09-01) -> delay 181 days
    lab('d6', 'hba1c', 7.0, '2028-06-01'); lab('d6', 'glucose', 250, '2028-09-01')
    e = visit('d6', '2029-03-01')
    db.execute('INSERT INTO encounter_diagnosis VALUES (?,?,?,?,?,?)', (e, 'd6', 1, 'E11.9', 'principal', 'ed1'))
    # d7: three elevated BP days within a year, no hypertension diagnosis
    for d in ('2029-01-05', '2029-03-05', '2029-06-05'): lab('d7', 'systolic_bp', 152, d)
    # d8: EF 35% in a note, heart failure recorded 90 days later
    db.execute("INSERT INTO note_fact VALUES ('d8','nf8','notes','ejection_fraction',35,'%',0,'current','2029-01-01','EF 35%',NULL)")
    cond('d8', 'heart_failure', 'I50.22', '2029-04-01')
    # colorectal red flag: d6 iron-deficiency anemia, never scoped; d7 IDA then colonoscopy (evaluated)
    lab('d6', 'hemoglobin', 10.5, '2029-01-01'); lab('d6', 'ferritin', 12, '2029-01-20')
    lab('d7', 'hemoglobin', 10.0, '2029-02-01'); lab('d7', 'ferritin', 10, '2029-02-10')
    db.execute('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)', ('en7', 'd7', 'endoscopy_reports', '2029-04-01', '2029-04-01', '2029-04-01', 'GI',
                                                              json.dumps({'performed_procedures': ['colonoscopy']})))
    # look-forward: d1 ED dizziness -> stroke admission 9 days later; d2 chest pain no return; d3 dizziness no return
    visit('d1', '2029-05-01', 'emergency_visit', 'R42', 'emergency'); visit('d2', '2029-06-01', 'emergency_visit', 'R07.89', 'emergency')
    visit('d3', '2029-07-01', 'emergency_visit', 'R42', 'emergency')
    db.execute("INSERT INTO stay VALUES ('d1','2029-05-10','2029-05-14','inpatient','H1',0,NULL,1,4)")
    cond('d1', 'stroke', 'I63.9', '2029-05-10')
    db.commit(); db.close()
    return root


class Rules(unittest.TestCase):
    def test_criteria(self):
        self.assertEqual(dd.criterion_date('ckd', {'egfr_low': [('2027-01-10', 55), ('2027-05-01', 52)]})[0], '2027-05-01')
        self.assertIsNone(dd.criterion_date('ckd', {'egfr_low': [('2027-01-10', 55), ('2027-02-01', 52)]}))
        self.assertEqual(dd.criterion_date('diabetes', {'a1c_high': [('2028-06-01', 7.0)], 'glucose_high': [('2028-09-01', 250)]})[0], '2028-09-01')
        self.assertIsNone(dd.criterion_date('diabetes', {'a1c_high': [('2028-06-01', 7.0)]}))
        self.assertEqual(dd.criterion_date('hypertension', {'bp_high': [('2029-01-05', 150), ('2029-03-05', 150), ('2029-06-05', 150)]})[0], '2029-06-05')
        self.assertIsNone(dd.criterion_date('hypertension', {'bp_high': [('2027-01-05', 150), ('2028-03-05', 150), ('2029-06-05', 150)]}))
        self.assertEqual(dd.criterion_date('heart_failure', {'bnp_high': [('2029-01-01', 900)], 'hf_symptom': [('2029-02-15', 1)]})[0], '2029-02-15')
        self.assertEqual(dd.criterion_date('colorectal_cancer', {'hgb_low': [('2029-01-01', 10.5)], 'ferritin_low': [('2029-01-20', 12)]})[0], '2029-01-20')

    def test_delay_summary(self):
        s = dd.delay_summary([10, 100, 200, 400])
        self.assertEqual((s['n'], s['median_days'], s['share_over_90'], s['share_over_365']), (4, 150.0, 0.75, 0.25))
        self.assertEqual([b['patients'] for b in s['histogram']], [1, 0, 1, 1, 1, 0])

    def test_parameters(self):
        self.assertEqual(dd.validate({})['mode'], 'all')
        self.assertEqual(dd.validate({'target': 'stroke'})['mode'], 'look_forward')
        for bad in ({'target': 'gout'}, {'mode': 'x'}, {'target': 'stroke', 'mode': 'undiagnosed'}, {'lookback_days': 10}, {'extra': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dd.validate(bad)


class Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()
        cls.db = sqlite3.connect(cls.path / 'semantic.sqlite3')

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        shutil.rmtree(cls.path, ignore_errors=True)

    def run_(self, **p):
        return dd.run(self.db, None, p, AS_OF)

    def test_planted_ckd_delay(self):
        r = self.run_(target='ckd')
        lb, ud = r['lookback'], r['undiagnosed']
        self.assertEqual((lb['incident'], lb['prevalent_excluded'], lb['with_prior_criterion']), (1, 1, 1))
        c = lb['cases'][0]
        self.assertEqual((c['patient_id'], c['criterion_date'], c['delay_days'], c['missed_opportunity_visits']), ('d1', '2027-05-01', 259, 2))
        self.assertEqual(c['first_signal_date'], '2027-01-10')
        self.assertEqual(lb['delay']['median_days'], 259.0)
        prompts = {p['patient_id']: p for p in ud['prompts']}
        self.assertEqual(set(prompts), {'d3', 'd4'})                    # d5 repeated inside 90 days: no prompt; d1/d2 diagnosed
        self.assertEqual((prompts['d3']['prompt'], prompts['d3']['visits_since']), ('criterion_met', 2))  # office + ED visit
        self.assertEqual(prompts['d3']['risk_adjustment_suspect'], 'ckd_egfr')
        self.assertEqual(prompts['d4']['prompt'], 'unconfirmed_signal')
        self.assertIn('not a diagnosis', prompts['d3']['review'])
        v, table = dd.summarize(r)
        self.assertIn('median delay 259 days', v)
        self.assertEqual(table[0]['patient_id'], 'd3')

    def test_other_targets(self):
        r = self.run_(target='diabetes', mode='lookback')
        c = r['lookback']['cases'][0]
        self.assertEqual((c['patient_id'], c['diagnosis_source'], c['delay_days']), ('d6', 'encounter_diagnosis', 181))
        r = self.run_(target='heart_failure', mode='lookback')
        self.assertEqual(r['lookback']['cases'][0]['delay_days'], 90)
        r = self.run_(target='hypertension', mode='undiagnosed')
        self.assertEqual([p['patient_id'] for p in r['undiagnosed']['prompts'] if p['prompt'] == 'criterion_met'], ['d7'])
        r = self.run_(target='colorectal_cancer', mode='undiagnosed')
        self.assertEqual([p['patient_id'] for p in r['undiagnosed']['prompts']], ['d6'])   # d7 had a colonoscopy after the red flag
        self.assertIn('red flag was evaluated', r['undiagnosed']['prompts'][0]['review'])

    def test_look_forward(self):
        r = self.run_(target='stroke', period={'start': '2029-01-01', 'end': '2029-12-31'})
        f = r['look_forward']
        self.assertEqual((f['index_visits'], f['symptom_index_visits'], f['returns_with_target'], f['symptom_returns']), (3, 2, 1, 1))
        self.assertEqual(f['symptom_rate'], 0.5)
        self.assertEqual(f['cases'][0]['days_to_admission'], 9)
        v, _ = dd.summarize(r)
        self.assertIn('review prompts', v)

    def test_population_and_answer(self):
        r = dd.run(self.db, ['d3'], {'target': 'ckd'}, AS_OF)
        self.assertEqual([p['patient_id'] for p in r['undiagnosed']['prompts']], ['d3'])
        full = self.run_(target='ckd')
        plan = validate({'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                         'nodes': [{'id': 'answer', 'operation': 'diagnostic_delay', 'inputs': [], 'parameters': {'target': 'ckd'}, 'purpose': 'x'}]}, AS_OF)
        a = build_answer(plan, [{'result': full}], {}, 'q')
        self.assertEqual((a['status'], a['answer_type'], a['count']), ('answered', 'table', 2))
        self.assertIn('diagnostic_delay', a['overall'])
        json.dumps(a)


PHRASINGS = {
    'How many people have diabetes that their lab results show but nobody has diagnosed?': ('diabetes', 'undiagnosed'),
    'How many patients have lab evidence of diabetes without a diabetes diagnosis?': ('diabetes', 'undiagnosed'),'How long were CKD diagnoses delayed after eGFR fell below 60?': ('ckd', 'lookback'),
             'Which patients have undiagnosed diabetes?': ('diabetes', 'undiagnosed'),
             'Missed opportunities to diagnose heart failure earlier': ('heart_failure', 'lookback'),
             'Look-back on new colon cancer diagnoses for earlier red flags': ('colorectal_cancer', 'lookback'),
             'Patients meeting hypertension criteria with no diagnosis': ('hypertension', 'undiagnosed'),
             'How many patients sent home from the ER with dizziness were admitted with a stroke within 30 days?': ('stroke', 'look_forward'),
             'Diagnostic delay for CKD by PCP, and who is still undiagnosed': ('ckd', 'all'),
             'Which abnormal findings were documented before patients were diagnosed with heart failure?': ('heart_failure', 'lookback'),
             'Were there missed diagnoses of sepsis after ED discharges? ED discharges admitted with sepsis within 7 days': ('sepsis', 'look_forward')}


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path, cls.profile = routing_profile()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.path, ignore_errors=True)

    def compile(self, q):
        return local_compiler.compile(q, self.profile, '2029-08-15')

    def test_phrasings(self):
        for q, (target, mode) in PHRASINGS.items():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertIsNotNone(g, info)
                node = g['nodes'][-1]
                self.assertEqual(node['operation'], 'diagnostic_delay', info)
                self.assertEqual((node['parameters']['target'], node['parameters']['mode']), (target, mode))
        g, _ = self.compile('How many patients sent home from the ER with dizziness were admitted with a stroke within 30 days?')
        self.assertEqual((g['nodes'][-1]['parameters']['horizon_days'], g['nodes'][-1]['parameters']['symptom_only']), (30, True))

    def test_acceptance_file(self):
        rows = json.loads((ROOT / 'bench' / 'acceptance' / 'diagnostic_delay.json').read_text())
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            with self.subTest(q=row['question']):
                g, info = self.compile(row['question'])
                self.assertIsNotNone(g, info)
                self.assertEqual(g['nodes'][-1]['operation'], 'diagnostic_delay', info)

    def test_does_not_steal(self):
        qs = [c['question'] for op in ('risk_adjustment', 'survival', 'profile', 'pathway', 'early_signal', 'trajectory')
              for c in json.loads((ROOT / 'bench' / 'acceptance' / f'{op}.json').read_text())]
        qs += ['How many patients have two eGFR values below 60 at least 90 days apart but no CKD diagnosis?',   # literal count
               'How many patients have an A1c of 6.5% or more on two occasions without a diabetes diagnosis?',
               'Who has a low eGFR (under 60) on their latest result but no CKD on their problem list?',
               'Show suspect conditions for heart failure without a diagnosis code', 'How long after a diabetes diagnosis until the first ED visit?']
        for q in qs:
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertNotEqual(g['nodes'][-1]['operation'] if g and g.get('nodes') else None, 'diagnostic_delay', info)

    def test_follow_ups_compile_back(self):
        for q in dd.follow_up():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertEqual(g['nodes'][-1]['operation'], 'diagnostic_delay', info)


class Integration(unittest.TestCase):
    def test_registered_everywhere(self):
        op = 'diagnostic_delay'
        self.assertIn(op, analysis_graph.OPERATIONS)
        self.assertIn(op, analysis_graph.NO_BENCHMARK_OPERATIONS)
        self.assertIn(op, graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract(op)['count'], [0, 1])
        self.assertIn(op, graph_planner.CONTRACTS)
        self.assertIn('risk_adjustment', capability_registry.PRODUCT[op]['use'])
        schema = graph_contracts.parameter_schema(op, {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']['target']['enum']), set(dd.TARGETS))
        self.assertTrue(set(schema['properties']) <= dd.FIELDS)
        dd.validate({k: v for k, v in graph_planner.CONTRACTS[op]['parameters'].items() if not isinstance(v, str) or '|' not in v})
        source = Path(analysis_graph.__file__).read_text()
        block = source[source.index(f"if last['operation'] == '{op}':"):]
        block = block[:block.index("\n    if last['operation']", 10)]
        self.assertTrue(block.rstrip().endswith('return answer'))
        self.assertIn('diagnostic_delay.py', source[source.index('def code_identity'):source.index('def code_identity') + 2500])

    def test_router_and_console(self):
        from clinical_core.agere.clinical import operator_router
        from clinical_core.agere import ask_view
        self.assertIn('review prompts', operator_router.tool('diagnostic_delay')['description'])
        html = ask_view.page()
        self.assertIn('if(o.diagnostic_delay)return vizDelay(a)', html)
        self.assertIn('function delayHist', html)


@unittest.skipUnless((REAL_V20 / 'semantic.sqlite3').exists(), 'warehouse v2.0 semantic layer not present')
class RealV20(unittest.TestCase):
    def test_smoke(self):
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)  # read-only; never rebuilt
        try:
            r = dd.run(db, None, {'target': 'ckd'}, AS_OF)
            f = dd.run(db, None, {'target': 'stroke'}, AS_OF)
        finally:
            db.close()
        self.assertEqual(r['status'], 'answered')
        self.assertGreater(r['lookback']['diagnosed_patients'], 50)
        self.assertGreater(r['undiagnosed']['criterion_met'], 0)
        self.assertTrue(all('not a diagnosis' in p['review'] for p in r['undiagnosed']['prompts']))
        self.assertGreater(f['look_forward']['index_visits'], 0)


if __name__ == '__main__':
    unittest.main()
