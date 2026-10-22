"""Clinical workflow tasks CT-01..CT-10 (clinical_tasks.py): recognizer, engine on a tiny fixture, System One review (mocked),
graph wiring, and the regression on the active sample warehouse against independent references
(bench/clinical_task_reference.py, written from the task definitions; never the generator's private truth).

    AGERE_LOCAL_TYPED=off AGERE_LOCAL_NORMALIZER=off .venv/bin/python -m unittest test_clinical_tasks
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import clinical_tasks as C

ROOT = Path(__file__).resolve().parent
QUESTIONS = {  # realistic analyst wording -> task
    'CT-01': 'Which HFrEF patients are missing a GDMT pillar?',
    'CT-02': 'What are the documented reasons our heart failure patients are not on all four GDMT pillars?',
    'CT-03': 'Which HFrEF patients are on a GDMT drug below the target dose?',
    'CT-04': 'Which type 2 diabetes patients with ASCVD or CKD are not on an SGLT2 inhibitor or GLP-1?',
    'CT-05': 'Which AFib patients with a CHA2DS2-VASc of 2 or more are not on an anticoagulant?',
    'CT-06': 'Which CKD stage 3 or higher patients with albuminuria are not on an ACE inhibitor or ARB?',
    'CT-07': 'Which statin-indicated patients are not on a statin?',
    'CT-08': 'Which patients who had an MI in the last 12 months are not on a beta blocker or a high-intensity statin?',
    'CT-09': 'Which patients on an MRA have a potassium of 5.5 or higher?',
    'CT-10': 'Which patients with a documented drug allergy or intolerance are still getting that drug filled after it was documented?',
}


class RecognizerTests(unittest.TestCase):
    def test_every_task_is_recognized_and_keeps_its_literals(self):
        from clinical_core.agere.clinical.literal_check import executable_text, missing
        for task, q in QUESTIONS.items():
            g = C.local_question(q)
            self.assertIsNotNone(g, q)
            node = g['nodes'][0]
            self.assertEqual((node['operation'], node['parameters']['task']), ('clinical_task', task))
            self.assertEqual(missing(q, executable_text([node['parameters']])), [], q)
            C.validate(node['parameters'])

    def test_different_population_or_threshold_declines(self):  # CT-FIT (FM-26)
        for q in ('Which AFib patients are not on an anticoagulant?',                          # no CHA2DS2-VASc
                  'Which patients with diabetes and kidney disease lack an SGLT2 or GLP-1?',   # not T2D + ASCVD/CKD
                  'Which CKD patients with a UACR of 300 or more are not on an ACE inhibitor?',  # other threshold
                  'How many patients are on spironolactone with a potassium above 5.5?',       # strict >, one drug
                  'Which post-MI patients are not on a beta blocker?',                         # no 12-month window
                  'Which of our heart failure with reduced ejection fraction patients are not on an SGLT2 inhibitor or a beta blocker?'):
            self.assertIsNone(C.local_question(q), q)

    def test_steps_follow_the_wording(self):
        self.assertEqual(C.recognize('Which CKD stage 3 or higher patients with a UACR of 30 or more are not on an SGLT2 inhibitor?'), ('CT-06', 'c'))
        self.assertEqual(C.recognize('Which statin-indicated patients with a documented statin intolerance are not on a statin?'), ('CT-07', 'c'))
        self.assertEqual(C.recognize('Run CT-9'), ('CT-09', None))

    def test_definition_parameter_is_the_governed_one(self):
        with self.assertRaises(ValueError): C.validate({'task': 'CT-01', 'definition': 'anything goes'})
        with self.assertRaises(ValueError): C.validate({'task': 'CT-11'})
        self.assertEqual(C.validate({'task': 'ct-05'})['step'], 'b')


def fixture(path):
    """A tiny semantic layer: 1 HFrEF patient missing MRA+SGLT2i (one documented hyperkalemia), 1 AFib woman 76 untreated,
    1 MRA patient with K 5.8, 1 penicillin allergy still filled, 1 patient who died before the index date."""
    db = sqlite3.connect(path)
    db.executescript('''
    CREATE TABLE patient(patient_id, birth_date, sex_reported);
    CREATE TABLE death(patient_id, death_date);
    CREATE TABLE condition(patient_id, icd10, event_date, recorded_date, is_diagnosis, diagnosis_basis, concept);
    CREATE TABLE payer_claim_diagnosis(patient_id, icd10, service_from, counts_in_metrics);
    CREATE TABLE encounter_diagnosis(patient_id, icd10, event_date);
    CREATE TABLE problem_list_entry(patient_id, icd10, first_recorded);
    CREATE TABLE medication(record_id, patient_id, evidence, drug_name, drug_class, status, event_date, fill_reversed);
    CREATE TABLE record(record_id, payload);
    CREATE TABLE note_fact(patient_id, fact_type, value, negated, experiencer, event_date);
    CREATE TABLE measurement(patient_id, concept, value_canonical, result_current, excluded_reason, event_date);
    CREATE TABLE therapy_barrier(patient_id, drug_class, gdmt_pillar, drug_key, barrier_type, detail, evidence_text, event_date,
                                 last_date, source_type, record_id, negated);
    CREATE TABLE note_therapy_change(patient_id, drug_class, action, reason, evidence_text, event_date, record_id, drug_key);
    CREATE TABLE allergy_entry(patient_id, substance, substance_received, reaction, first_recorded, last_confirmed, record_id,
                               no_known_allergies, status);
    ''')
    db.executemany('INSERT INTO patient VALUES (?,?,?)', [('HF1', '1950-01-01', 'M'), ('AF1', '1950-06-01', 'F'), ('K1', '1960-01-01', 'M'),
                                                          ('AL1', '1970-01-01', 'F'), ('DEAD', '1940-01-01', 'M')])
    db.execute("INSERT INTO death VALUES ('DEAD', '2028-01-01')")
    db.executemany('INSERT INTO payer_claim_diagnosis VALUES (?,?,?,1)', [('HF1', 'I50.22', '2028-03-01'), ('AF1', 'I48.91', '2028-02-01'),
                                                                          ('AF1', 'I10', '2028-02-01'), ('DEAD', 'I50.22', '2027-01-01')])
    fills = [('f1', 'HF1', 'payer_claim', 'carvedilol 12.5 mg tablet', 'hf_evidence_based_beta_blocker', 'paid', '2028-12-01', 30, 60),
             ('f2', 'HF1', 'payer_claim', 'lisinopril 20 mg tablet', 'ace_inhibitor', 'paid', '2028-12-01', 30, 30),
             ('f3', 'K1', 'payer_claim', 'spironolactone 25 mg tablet', 'mra', 'paid', '2028-12-10', 30, 30),
             ('f4', 'AL1', 'payer_claim', 'amoxicillin 500 mg capsule', 'penicillin', 'paid', '2028-06-01', 10, 30)]
    for rid, pid, ev, name, cls, st, day, supply, qty in fills:
        db.execute('INSERT INTO medication VALUES (?,?,?,?,?,?,?,0)', (rid, pid, ev, name, cls, st, day))
        db.execute('INSERT INTO record VALUES (?,?)', (rid, json.dumps({'days_supply': supply, 'quantity_dispensed': qty, 'drug_name': name})))
    db.execute("INSERT INTO therapy_barrier VALUES ('HF1','mra','mra','spironolactone','contraindication_lab','K 5.9 hyperkalemia',NULL,"
               "'2028-05-01',NULL,'clinical_note','n1',0)")
    db.execute("INSERT INTO measurement VALUES ('K1','potassium',5.8,1,NULL,'2028-12-20')")
    db.execute("INSERT INTO measurement VALUES ('K1','potassium',4.0,1,NULL,'2028-11-01')")
    db.execute("INSERT INTO allergy_entry VALUES ('AL1','penicillin','penicillin (rash)','rash','2028-01-15',NULL,'a1',0,'active')")
    db.commit(); db.close()


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp.name) / 'semantic.sqlite3'
        fixture(cls.db_path)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def run_task(self, task, step='all'):
        from clinical_core.agere.clinical.sql_query import register
        db = register(sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True))
        try: return C.run(db, {'task': task, 'step': step, 'index_date': '2028-12-31'}, '2030-05-27')
        finally: db.close()

    def test_ct01_missing_pillars_and_death_exclusion(self):
        r = self.run_task('CT-01')
        self.assertEqual([x['patient_id'] for x in r['steps']['a']], ['HF1'])  # DEAD excluded
        self.assertEqual(r['steps']['b'][0]['missing_pillars'], ['MRA', 'SGLT2i'])

    def test_ct02_reason_category_and_location(self):
        rows = {x['pillar']: x for x in self.run_task('CT-02', 'a')['rows']}
        self.assertEqual(rows['MRA']['reason_category'], 'hyperkalemia')
        self.assertEqual(rows['MRA']['reason_location'], 'note_text_only')
        self.assertEqual(rows['SGLT2i']['reason_category'], 'none_documented')

    def test_ct03_daily_dose_below_target(self):
        rows = self.run_task('CT-03', 'a')['rows']
        self.assertEqual([(r['drug'], r['daily_dose_mg'], r['target_daily_dose_mg']) for r in rows], [('carvedilol', 25.0, 50.0)])

    def test_ct05_cha2ds2_vasc(self):
        r = self.run_task('CT-05')
        self.assertEqual(r['steps']['a'], [{'patient_id': 'AF1', 'cha2ds2_vasc': 4}])  # HTN 1 + age 76 2 + female 1
        self.assertEqual([x['patient_id'] for x in r['steps']['b']], ['AF1'])

    def test_ct09_and_ct10(self):
        self.assertEqual([(x['patient_id'], x['latest_potassium_mmol_l']) for x in self.run_task('CT-09', 'a')['rows']], [('K1', 5.8)])
        rows = self.run_task('CT-10', 'a')['rows']
        self.assertEqual([(x['patient_id'], x['drug_class'], x['documentation_date'], x['latest_fill_or_order_date']) for x in rows],
                         [('AL1', 'penicillin', '2028-01-15', '2028-06-01')])

    def test_empty_answer_keeps_requested_shape(self):  # CT-SHAPE (FM-27)
        rep = C.report({'a': []}, C.validate({'task': 'CT-09'}), '2028-12-31', 3)
        a = C.answer(rep, {'coverage': 'supported_observation'}, QUESTIONS['CT-09'])
        self.assertEqual((a['answer_type'], a['count'], a['patient_ids']), ('patient_set', 0, []))
        from clinical_core.agere.clinical.literal_check import shape_misfit
        self.assertIsNone(shape_misfit(QUESTIONS['CT-09'], a))

    def test_several_rows_per_patient_collapse_for_which_patients(self):
        rows = [{'patient_id': 'P1', 'drug': 'a'}, {'patient_id': 'P1', 'drug': 'b'}, {'patient_id': 'P2', 'drug': 'c'}]
        self.assertEqual(C.per_patient(rows), [{'patient_id': 'P1', 'drug': ['a', 'b']}, {'patient_id': 'P2', 'drug': 'c'}])


class ReviewTests(unittest.TestCase):
    def report(self):
        rows = [{'patient_id': 'P1', 'pillar': 'SGLT2i', 'reason_category': 'none_documented'},
                {'patient_id': 'P2', 'pillar': 'SGLT2i', 'reason_category': 'none_documented'},
                {'patient_id': 'P3', 'pillar': 'MRA', 'reason_category': 'hyperkalemia'}]
        return {'task': 'CT-02', 'rows': rows, 'index_date': '2030-05-27', 'new_paid_api_calls': 0}

    def detail(self):
        cell = lambda pid, label: {'patient_id': pid, 'key': 'sglt2i', 'label': label, 'decided_by': 'jev',
                                   'sources': [{'text': 'declined SGLT2i due to cost', 'record_id': 'n9', 'date': '2029-01-01'}]}
        return {'cells': [cell('P1', 'cost_access'), cell('P2', 'unknown')], 'evaluated_patient_ids': ['P1', 'P2'], 'capped': False,
                'new_paid_api_calls': 2, 'system_one': {'estimated_usd': 0.0004, 'decided_by': {'jev': 2}}}

    def test_offline_review_is_recorded_not_guessed(self):
        r = C.review(SimpleNamespace(allow_live=False), self.report())
        self.assertEqual(r['system_one_review']['open_gaps'], 2)
        self.assertTrue(all(x['reason_category'] in ('none_documented', 'hyperkalemia') for x in r['rows']))

    def test_unknown_never_fills_a_reason_and_calls_are_charged(self):  # CT-S1-REVIEW, CT-S1-COST (FM-28, FM-30)
        with patch('clinical_core.agere.clinical.atomic_questions.run', return_value=self.detail()) as run:
            r = C.review(SimpleNamespace(allow_live=True), self.report())
        params = run.call_args[0][2]
        self.assertEqual([s['subject'] for s in params['sub_questions']], ['sglt2i'])
        self.assertEqual(run.call_args[0][1], ['P1', 'P2'])  # only unexplained gaps are read
        rows = {x['patient_id']: x for x in r['rows']}
        self.assertEqual((rows['P1']['reason_category'], rows['P1']['reason_record_id']), ('cost_access', 'n9'))
        self.assertEqual(rows['P2']['reason_category'], 'none_documented')
        self.assertEqual(r['new_paid_api_calls'], 2)
        self.assertEqual(r['system_one_review']['reasons_found'], 1)

    def test_review_charges_calls_through_the_graph_hook(self):
        import inspect
        from clinical_core.agere.clinical import analysis_graph
        src = inspect.getsource(analysis_graph.execute)
        self.assertIn("engine.review(stack, detail)", src)
        self.assertIn("paid_calls += detail.get('new_paid_api_calls', 0)", src)


class WiringTests(unittest.TestCase):
    def test_registered_on_every_planner_surface(self):
        from clinical_core.agere.clinical import analysis_graph, capability_registry, graph_contracts, graph_planner
        self.assertEqual(analysis_graph.CLINICAL_LOGIC['clinical_task'], 'clinical_tasks')
        self.assertIn('clinical_task', capability_registry.PRODUCT)
        self.assertIn('clinical_task', graph_contracts.TYPED)
        self.assertIn('clinical_task', graph_planner.CONTRACTS)

    def test_exact_local_tier_returns_a_valid_graph(self):
        from clinical_core.agere.clinical.economical_planner import local_plan
        for task, q in QUESTIONS.items():
            graph, route = local_plan(q, '2030-05-27')
            self.assertEqual((route, graph['nodes'][0]['parameters']['task']), ('exact_clinical_task', task))


def active_build():
    try: return Path(json.loads((ROOT/'config/agere/active_warehouse.json').read_text())['build'])
    except (OSError, ValueError, KeyError): return None


@unittest.skipUnless(active_build() and (active_build()/'stack/semantic.sqlite3').exists(), 'active sample warehouse not present')
class SampleWarehouseRegression(unittest.TestCase):
    """Engine (the question path's operation) vs independent references on the active sample, index 2028-12-31."""
    def test_engine_matches_independent_references(self):
        from bench.clinical_task_reference import score
        as_of = json.loads((ROOT/'config/agere/active_warehouse.json').read_text())['snapshot']['as_of']
        scores = score(active_build(), '2028-12-31', as_of)
        self.assertEqual(set(scores), {'CT-01', 'CT-03', 'CT-04', 'CT-05', 'CT-06', 'CT-07', 'CT-08', 'CT-09', 'CT-10'})
        for task, steps in scores.items():
            for step, s in steps.items():
                floor = 0.9 if step == 'a_structured' else 0.95
                self.assertGreaterEqual(s.get('f1', s.get('recall')), floor, f'{task}.{step}: {s}')
        self.assertGreater(scores['CT-01']['b']['reference'], 0)
        self.assertGreater(scores['CT-07']['b']['reference'], 0)

    def test_ct02_gaps_are_the_ct01_gaps_with_a_reason_column(self):
        r1 = C.execute(active_build()/'stack', None, {'task': 'CT-01', 'step': 'b', 'index_date': '2028-12-31'}, '2030-05-27')
        r2 = C.execute(active_build()/'stack', None, {'task': 'CT-02', 'index_date': '2028-12-31'}, '2030-05-27')
        self.assertEqual({(x['patient_id'], p) for x in r1['rows'] for p in x['missing_pillars']},
                         {(x['patient_id'], x['pillar']) for x in r2['rows']})
        self.assertTrue(all(x['reason_category'] in C.REASONS for x in r2['rows']))
        self.assertTrue(any(x['reason_category'] != 'none_documented' for x in r2['rows']))


if __name__ == '__main__':
    unittest.main()
