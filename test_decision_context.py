"""The decision context (Mike, 2026-10-23): living patients, latest labs, one condition-evidence rule, one 12-month window, all-source events.
Definitions as data, enforcement in the SQL that runs, the generators that emit them natively, the mandatory answer check, the planners'
prompts. Synthetic fixtures only; no model, network or stack database."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import answer_checks as A
from clinical_core.agere.clinical import cohort_families as F
from clinical_core.agere.clinical import decision_context as DC
from clinical_core.agere.clinical import request_language as L
from clinical_core.agere.clinical import request_plan as R
from clinical_core.agere.clinical import semantic_layer as S
from clinical_core.agere.clinical import sql_query

AS_OF = '2030-05-27'


def build(path):
    """Patients A-F; D is deceased. Diabetes: A (EHR), B (claim only), C (lab evidence only), D (EHR, deceased), E (encounter dx only: not evidence)."""
    with sqlite3.connect(Path(path) / 'semantic.sqlite3') as db:
        db.executescript(f'''
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('version','semantic-layer/fixture'),('as_of','{AS_OF}');
        CREATE TABLE patient(patient_id TEXT, birth_date TEXT, sex_reported TEXT, coverage_type TEXT);
        INSERT INTO patient VALUES ('A','1950-01-01','F','medicare'),('B','1955-01-01','M','medicare'),('C','1990-01-01','F','commercial'),
          ('D','1940-01-01','F','medicare'),('E','1960-01-01','M','medicare'),('F','1985-01-01','F','commercial');
        CREATE TABLE death(patient_id TEXT PRIMARY KEY, death_date TEXT);
        INSERT INTO death VALUES ('D','2029-01-01');
        CREATE TABLE condition(record_id TEXT, patient_id TEXT, source_table TEXT, concept TEXT, icd10 TEXT, event_date TEXT,
          is_diagnosis INTEGER, diagnosis_basis TEXT);
        INSERT INTO condition VALUES ('c1','A','conditions','diabetes','E11.9','2028-01-01',1,NULL),
          ('c2','C','conditions','diabetes','E11.9','2029-01-01',NULL,'laboratory_evidence'),
          ('c3','D','conditions','diabetes','E11.9','2027-01-01',1,NULL),
          ('c4','F','conditions','hyperlipidemia','E78.00','2029-01-01',NULL,'laboratory_evidence'),
          ('c5','A','conditions','copd','J44.9','2028-01-01',1,NULL),('c6','D','conditions','copd','J44.9','2028-01-01',1,NULL);
        CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, final_status TEXT, service_from TEXT, counts_in_metrics INTEGER);
        INSERT INTO payer_claim VALUES ('k1','B','paid','2029-03-01',1),('k2','F','denied','2029-03-01',1);
        CREATE TABLE payer_claim_diagnosis(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, icd10 TEXT, counts_in_metrics INTEGER);
        INSERT INTO payer_claim_diagnosis VALUES ('k1','B','2029-03-01','E11.9',1),('k2','F','2029-03-01','E11.9',1);
        CREATE TABLE encounter_diagnosis(encounter_id TEXT, patient_id TEXT, rank INTEGER, icd10 TEXT, type TEXT, record_id TEXT, source TEXT, event_date TEXT);
        INSERT INTO encounter_diagnosis VALUES ('e1','E',1,'E11.9','principal','r1','ehr','2029-04-01');
        CREATE TABLE concept_patient(concept TEXT, kind TEXT, patient_id TEXT, records INTEGER, first_date TEXT, last_date TEXT);
        INSERT INTO concept_patient VALUES ('diabetes','condition','A',1,NULL,NULL);
        CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL, result_current INTEGER);
        INSERT INTO measurement VALUES ('m1','A','potassium','2028-01-01',6.1,1),('m2','A','potassium','2029-01-01',4.0,1),
          ('m3','B','potassium','2029-01-01',5.9,1),('m4','D','potassium','2028-06-01',6.5,1),('m5','C','potassium','2029-02-01',3.9,1);
        CREATE TABLE measurement_latest(patient_id TEXT, concept TEXT, event_date TEXT, value_canonical REAL, unit_canonical TEXT,
          same_day_values INTEGER, same_day_min REAL, record_id TEXT, value_censor TEXT);
        INSERT INTO measurement_latest VALUES ('A','potassium','2029-01-01',4.0,NULL,1,4.0,'m2',NULL),('B','potassium','2029-01-01',5.9,NULL,1,5.9,'m3',NULL),
          ('D','potassium','2028-06-01',6.5,NULL,1,6.5,'m4',NULL),('C','potassium','2029-02-01',3.9,NULL,1,3.9,'m5',NULL);
        CREATE TABLE utilization_event(patient_id TEXT, kind TEXT, event_date TEXT);
        INSERT INTO utilization_event VALUES ('A','inpatient','2029-05-01'),('B','ed_visit','2029-06-01'),('D','inpatient','2029-02-01'),('D','ed_visit','2029-03-01');
        CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT);
        INSERT INTO stay VALUES ('A','2029-05-01','2029-05-05','inpatient'),('D','2029-02-01','2029-02-04','inpatient');
        CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, event_date TEXT);
        INSERT INTO visit VALUES ('v1','A','office','2029-05-01');
        CREATE TABLE fill(patient_id TEXT, drug_class TEXT, fill_date TEXT, drug_name TEXT, rxnorm_ingredient TEXT, supply_end TEXT);
        INSERT INTO fill VALUES ('A','statin','2030-01-01','atorvastatin',NULL,'2030-03-01'),('B','statin','2028-01-01','atorvastatin',NULL,'2028-03-01'),
          ('A','lama','2030-02-01','tiotropium',NULL,'2030-04-01'),('D','laba','2030-02-01','salmeterol',NULL,'2030-04-01');
        ''')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        build(self.path)
        self.target = self.path / 'semantic.sqlite3'

    def run_sql(self, question, sql, returns='count'):
        with DC.scope(question, AS_OF):
            return sql_query.execute(self.path, {'query': sql, 'returns': returns}, target=self.target)


class Definitions(Fixture):
    def test_explicit_all_received_rows_preserve_patient_counts_within_events(self):
        q='Across all received appointment rows, show appointment counts and distinct patients.'
        self.assertFalse(DC.population(q)['living_only'])
        self.assertTrue(DC.population(q)['all_received_rows'])
        self.assertTrue(DC.population(q+' For living patients only.')['living_only'])
        sql='SELECT count(DISTINCT patient_id) FROM utilization_event'
        self.assertEqual(self.run_sql(q,sql)['value'],3)
        self.assertEqual(self.run_sql('How many patients had a utilization event?',sql)['value'],2)
        with DC.scope(q):
            verdict=DC.verify(q,{},sqls=['SELECT count(*) FROM living_patient'])
            self.assertFalse(verdict['ok'])
            self.assertIn('all-received-row',verdict['reasons'][0])
            good={'table_binding':{'query':'SELECT count(DISTINCT patient_id) FROM utilization_event'}}
            self.assertTrue(DC.verify(q,good,sqls=['SELECT count(*) FROM living_patient'])['ok'])
            bad={**good,'computed_prose':[{'sources':{'patients':{'input':{'sql':'SELECT count(*) FROM living_patient'}}}}]}
            self.assertFalse(DC.verify(q,bad)['ok'])

    def test_patients_are_living_unless_the_question_asks_for_the_deceased(self):
        self.assertTrue(DC.population('How many patients have diabetes?')['living_only'])
        self.assertTrue(DC.population('How many living patients have diabetes?')['living_only'])
        for q in ('How many patients with diabetes died in 2029?', 'How many deceased patients had COPD?', 'Mortality among patients with HF',
                  'How many patients with diabetes, including deceased?', 'How many patients ever, all patients ever enrolled?',
                  'How many deaths were there?', 'death rate in diabetes'):
            self.assertFalse(DC.population(q)['living_only'], q)
        # money and membership months are not a count of patients
        self.assertFalse(DC.population('What was the total paid per member per month in 2029?')['living_only'])
        self.assertTrue(DC.population('How many patients had a paid claim in 2029?')['living_only'])

    def test_labs_are_latest_unless_the_question_says_ever_or_any(self):
        self.assertEqual(DC.lab_mode('How many patients have a potassium above 5.5?'), 'latest')
        for q in ('How many patients ever had a potassium above 5.5?', 'potassium above 5.5 at any time', 'patients with a history of high potassium',
                  'What is the highest potassium?', 'lowest eGFR per patient', 'A1c over 9 at least once', 'trend in LDL'):
            self.assertEqual(DC.lab_mode(q), 'any', q)

    def test_one_twelve_month_window_and_calendar_years(self):
        w = DC.year_window('How many patients had no statin fill in the last year and an ED visit in 2029?', AS_OF)
        self.assertEqual([x['kind'] for x in w], ['rolling_12_months', 'calendar_year'])
        self.assertEqual((w[0]['start'], w[0]['end']), ('2029-05-27', AS_OF))
        self.assertEqual((w[1]['start'], w[1]['end']), ('2029-01-01', '2029-12-31'))
        self.assertEqual(DC.year_window('in the past 12 months', AS_OF)[0]['kind'], 'rolling_12_months')
        self.assertEqual(DC.add_months('2028-02-29', -12).isoformat(), '2027-03-01')  # SQLite's date(x, '-12 months')
        with sqlite3.connect(':memory:') as db:
            for day in ('2030-05-27', '2028-02-29', '2024-02-29', '2029-12-31'):
                self.assertEqual(DC.add_months(day, -12).isoformat(), db.execute("SELECT date(?, '-12 months')", (day,)).fetchone()[0])

    def test_the_prompt_block_tells_planners_every_definition(self):
        block = DC.prompt_block()
        for text in (DC.VERSION, 'LIVING patients', 'NOT IN (SELECT patient_id FROM death)', 'measurement_latest', 'counts_in_metrics = 1',
                     'diagnosis_any', 'HbA1c >= 6.5', 'eGFR < 60', 'utilization_event', "date(:as_of, '-12 months')", 'calendar year'):
            self.assertIn(text, block)
        self.assertNotIn('{', block)  # planners format their systems with str.format

    def test_the_planners_prompts_carry_the_block(self):
        from clinical_core.agere.clinical import nl2sql, semantic_analyst, shared_planner, operator_router, metric_router
        for text in (nl2sql.RULES, semantic_analyst.SEMANTIC_RULES, shared_planner.SYSTEM):
            self.assertIn(DC.VERSION, text)
        self.assertIn(DC.VERSION, operator_router.SYSTEM.format(as_of=AS_OF) + operator_router._decision_block())
        self.assertIn(DC.VERSION, metric_router.SYSTEM.format(as_of=AS_OF) + metric_router._decision_block())
        from clinical_core.agere.hybrid import live
        self.assertIn(DC.VERSION, live.system_blocks()[0]['text'])


class SqlEnforcement(Fixture):
    def test_a_patient_count_reads_living_patients_in_the_sql_that_runs(self):
        sql = "SELECT COUNT(DISTINCT patient_id) AS n FROM condition WHERE concept = 'copd'"
        self.assertEqual(self.run_sql('How many patients have COPD?', sql)['value'], 1)                    # A; D is deceased
        out = self.run_sql('How many patients have COPD?', sql)
        self.assertIn('NOT IN (SELECT patient_id FROM death)', out['decision_context']['executed_query'])
        self.assertEqual(out['query'], sql)                                                              # the planned text is what is shown
        self.assertEqual(self.run_sql('How many deceased patients had COPD?', sql)['value'], 2)          # the question asks: nothing rewritten
        self.assertEqual(self.run_sql('How many patients, living or deceased, have COPD?', sql)['value'], 2)

    def test_aliases_quotes_joins_and_literals(self):
        sql = ("SELECT COUNT(DISTINCT b.\"patient_id\") FROM \"patient\" AS b WHERE EXISTS (SELECT 1 FROM \"utilization_event\" r "
               "WHERE r.patient_id = b.patient_id AND r.kind = 'inpatient') AND 'FROM condition' <> ''")
        self.assertEqual(self.run_sql('How many patients had an inpatient stay?', sql)['value'], 1)
        sql = "SELECT COUNT(DISTINCT c.patient_id) FROM condition c JOIN fill f ON f.patient_id = c.patient_id WHERE f.drug_class = 'laba'"
        self.assertEqual(self.run_sql('How many patients with COPD fill a LABA?', sql)['value'], 0)      # only D, deceased
        self.assertEqual(self.run_sql('How many deceased patients with COPD fill a LABA?', sql)['value'], 1)

    def test_set_operations_and_exclusions_stay_living(self):
        sql = ("SELECT patient_id FROM patient EXCEPT SELECT patient_id FROM fill WHERE drug_class = 'statin'")
        self.assertEqual(self.run_sql('Which patients have no statin fill?', sql, 'patient_set')['patient_ids'], ['C', 'E', 'F'])

    def test_money_and_event_counts_and_death_queries_are_left_alone(self):
        sql = "SELECT COUNT(*) AS n FROM utilization_event WHERE kind = 'inpatient'"                        # events, not patients
        self.assertEqual(self.run_sql('How many inpatient events were there?', sql)['value'], 2)
        sql = "SELECT COUNT(DISTINCT p.patient_id) FROM patient p JOIN death d ON d.patient_id = p.patient_id"
        self.assertEqual(self.run_sql('How many patients?', sql)['value'], 1)                              # it reads death itself
        with sqlite3.connect(self.target) as db:
            self.assertEqual(DC.enforce_sql("SELECT COUNT(DISTINCT patient_id) FROM patient")[0], "SELECT COUNT(DISTINCT patient_id) FROM patient")  # no scope

    def test_latest_labs_by_default_any_when_asked(self):
        sql = "SELECT COUNT(DISTINCT patient_id) FROM measurement WHERE concept = 'potassium' AND value_canonical > 5.5"
        self.assertEqual(self.run_sql('How many patients have a potassium above 5.5?', sql)['value'], 1)          # B: A's 6.1 is superseded by 4.0, D died
        self.assertEqual(self.run_sql('How many patients ever had a potassium above 5.5?', sql)['value'], 2)      # A and B, any result, still living
        self.assertEqual(self.run_sql('How many patients, living or deceased, ever had a potassium above 5.5?', sql)['value'], 3)
        having = ("SELECT COUNT(*) FROM (SELECT patient_id FROM measurement WHERE concept = 'potassium' GROUP BY patient_id HAVING COUNT(*) >= 2)")
        self.assertEqual(self.run_sql('How many patients have two potassium results?', having)['value'], 1)       # per-patient result counts need every result

    def test_a_patient_query_it_cannot_restrict_is_recorded_as_a_problem(self):
        with DC.scope('How many patients have diabetes?', AS_OF) as state:
            sql, receipt = DC.enforce_sql("SELECT COUNT(DISTINCT patient_id) FROM json_each('[1]')")
            self.assertEqual(sql, "SELECT COUNT(DISTINCT patient_id) FROM json_each('[1]')")
            self.assertTrue(state.problems)

    def test_cohort_lab_atoms_follow_the_labs_rule(self):
        plan = {'nodes': [{'id': 'p', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': {'all': [
            {'kind': 'measurement', 'concept': 'ldl', 'op': 'gt', 'value': 190.0, 'aggregate': 'any', 'window': {'basis': 'all_observed'}},
            {'kind': 'concept', 'concept': 'diabetes', 'window': {'basis': 'all_observed'}}], 'any': [], 'exclude': []}}}]}
        with DC.scope('How many patients have an LDL over 190?', AS_OF):
            out, changes = DC.enforce_plan(plan)
        self.assertEqual(out['nodes'][0]['parameters']['specification']['all'][0]['aggregate'], 'latest')
        self.assertEqual(len(changes), 1)
        self.assertEqual(plan['nodes'][0]['parameters']['specification']['all'][0]['aggregate'], 'any')            # the input is not mutated
        with DC.scope('How many patients ever had an LDL over 190?', AS_OF):
            self.assertEqual(DC.enforce_plan(plan)[1], [])


class ConditionEvidence(Fixture):
    def test_diagnosis_any_is_the_compilers_evidence(self):
        out = S.upgrade_diagnosis_any(self.path)
        self.assertTrue(out['upgraded'])
        db = sqlite3.connect(self.target)
        got = {r[0] for r in db.execute("SELECT DISTINCT patient_id FROM diagnosis_any WHERE icd10 LIKE 'E11%'")}
        # A EHR, B counted claim, C laboratory evidence (diabetes), D EHR, F denied claim (counts); NOT E (encounter diagnosis only)
        self.assertEqual(got, {'A', 'B', 'C', 'D', 'F'})
        self.assertEqual({r[0] for r in db.execute("SELECT patient_id FROM diagnosis_any WHERE icd10 LIKE 'E78%'")}, set())  # lab evidence only counts for diabetes / CKD
        self.assertEqual({r[0] for r in db.execute('SELECT DISTINCT source FROM diagnosis_any')}, {'condition', 'payer_claim'})
        # ... and it equals the governed compiler subquery
        from clinical_core.agere.clinical import concepts
        tables = {t: [c[1] for c in db.execute(f'PRAGMA table_info({t})')] for t in ('condition', 'payer_claim_diagnosis')}
        compiler = {r[0] for r in db.execute('SELECT patient_id FROM ' + concepts.condition_evidence_sql(['diabetes'], tables))}
        self.assertEqual(compiler, got)
        db.close()

    def test_an_older_diagnosis_any_table_is_shadowed_at_the_current_definition(self):
        with sqlite3.connect(self.target) as db:
            db.execute('CREATE TABLE diagnosis_any(patient_id TEXT, icd10 TEXT, source TEXT, source_detail TEXT, event_date TEXT, record_ref TEXT)')
            db.execute("INSERT INTO diagnosis_any VALUES ('E','E11.9','encounter',NULL,NULL,NULL)")
            db.execute("INSERT INTO meta VALUES ('diagnosis_any','1')")
        db = sql_query.register(sqlite3.connect(f'file:{self.target}?mode=ro', uri=True))
        self.assertEqual({r[0] for r in db.execute("SELECT patient_id FROM diagnosis_any WHERE icd10 LIKE 'E11%'")}, {'A', 'B', 'C', 'D', 'F'})
        db.close()

    def test_concept_patient_condition_rows_are_the_same_evidence(self):
        db = sql_query.register(sqlite3.connect(f'file:{self.target}?mode=ro', uri=True))
        diabetes = {r[0] for r in db.execute("SELECT patient_id FROM concept_patient WHERE concept = 'diabetes' AND kind = 'condition'")}
        self.assertEqual(diabetes, {'A', 'B', 'C', 'D', 'F'})
        db.close()
        with sqlite3.connect(self.target) as db:
            self.assertGreater(S.build_concept_patient_conditions(db), 0)
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key = 'concept_patient'").fetchone()[0], str(S.CONCEPT_PATIENT_VERSION))
        db = sql_query.register(sqlite3.connect(f'file:{self.target}?mode=ro', uri=True))
        self.assertEqual({r[0] for r in db.execute("SELECT patient_id FROM concept_patient WHERE concept = 'diabetes'")}, {'A', 'B', 'C', 'D', 'F'})
        db.close()

    def test_the_cohort_engine_reads_the_same_evidence(self):
        with DC.scope('How many patients have diabetes?', AS_OF):
            spec = {'all': [{'kind': 'concept', 'concept': 'diabetes', 'window': {'basis': 'all_observed', 'start': '', 'end': ''}},
                            {'kind': 'concept', 'concept': 'copd', 'evidence': ['coded_diagnosis'], 'window': {'basis': 'all_observed', 'start': '', 'end': ''}}],
                    'any': [], 'exclude': []}
            self.assertEqual(sorted(DC.condition_membership(self.path, spec)), ['diabetes'])  # an atom that names its evidence keeps the engine's reading
            self.assertEqual(DC.condition_membership(self.path, spec)['diabetes'], {'A', 'B', 'C', 'D', 'F'})
        self.assertIsNone(DC.condition_membership(self.path, spec))                          # outside a question scope nothing changes


class Generators(Fixture):
    def setUp(self):
        super().setUp()
        self.catalog = R.Catalog.load(self.path)

    def compile(self, q):
        return R.compile_request(self.catalog, q, L.parse(self.catalog, q, bound=False))

    def count(self, q):
        c = self.compile(q)
        return sql_query.execute(self.path, {'query': c['query'], 'returns': c['returns']}, target=self.catalog.target)['value']

    def test_the_grammar_emits_the_living_filter(self):
        c = self.compile('How many patients have COPD?')
        self.assertIn('b.patient_id NOT IN (SELECT patient_id FROM death)', c['query'])
        self.assertIn('NOT IN (SELECT patient_id FROM death)', c['membership_query'])
        self.assertEqual(self.count('How many patients have COPD?'), 1)
        self.assertNotIn('FROM death', self.compile('How many patients, living or deceased, have COPD?')['query'])
        self.assertEqual(self.count('How many patients, living or deceased, have COPD?'), 2)

    def test_a_care_gap_is_not_filtered_twice(self):
        c = self.compile('How many patients with diabetes had no statin fill in the last year?')
        self.assertEqual(c['query'].count('death'), 1)  # the gap rule's own living predicate stands; the context adds nothing

    def test_a_lab_threshold_is_the_latest_result(self):
        q = 'How many patients have a potassium above 5.5?'
        self.assertIn('"measurement_latest"', self.compile(q)['query'])
        self.assertEqual(self.count(q), 1)
        ever = 'How many patients ever had a potassium above 5.5?'
        self.assertIn('"measurement" AS', self.compile(ever)['query'])
        self.assertEqual(self.count(ever), 2)

    def test_an_inpatient_stay_is_the_all_source_admission(self):
        self.assertEqual(self.count('How many patients with COPD had an inpatient stay in 2029?'), 1)
        self.assertIn('"stay"', self.compile('How many patients with COPD had an inpatient stay in 2029?')['query'])
        self.assertIn('"kind" = \'observation\'', self.compile('How many patients with COPD had an observation stay in 2029?')['query'])

    def test_what_share_of_patients_is_the_share_of_the_same_population(self):
        a = self.compile('What share of patients with diabetes had an inpatient stay in 2029?')
        b = self.compile('Of patients with diabetes, what share had an inpatient stay in 2029?')
        self.assertEqual(a['query'], b['query'])
        value = sql_query.execute(self.path, {'query': a['query'], 'returns': a['returns']}, target=self.catalog.target)['value']
        self.assertAlmostEqual(value, 25.0)  # 1 of the 4 living patients with diabetes evidence (A, B, C, F): A; D is deceased, E has an encounter dx only
        ids = sql_query.execute(self.path, {'query': a['membership_query'], 'returns': 'patient_set'}, target=self.catalog.target)['patient_ids']
        self.assertEqual(ids, ['A'])  # the numerator: the share's patients agree with its value


class Families(unittest.TestCase):
    def test_copd_without_any_inhaler_in_the_last_year(self):
        q = 'How many patients with COPD have no inhaled corticosteroid, LAMA or LABA fill in the last year?'
        parsed = F.parse(q, AS_OF)
        p = parsed['parameters']
        self.assertEqual([c['name'] for c in p['classes']], ['ics', 'lama', 'laba'])          # LAMA and LABA were dropped before: "no ICS" only
        self.assertEqual(p['mode'], 'all_missing')                                            # none of the three
        self.assertEqual(p['fill_months'], 12)                                                # the stated window, not a 120-day supply convention
        self.assertEqual(p['population']['condition_prefixes'], ['J43', 'J44'])               # the library's COPD
        self.assertTrue(p['population']['alive'])
        sql = F.render_therapy_class_gap(p, AS_OF)
        self.assertIn("fill_date > date('2030-05-27', '-12 months')", sql)
        self.assertNotIn('medication_list_entry', sql)
        self.assertIn('FROM diagnosis_any', sql)                                              # the one condition-evidence rule
        self.assertIn('SELECT patient_id FROM death', sql)
        self.assertIn("drug_class IN ('inhaled_corticosteroid')", sql)

    def test_running_it_counts_the_living_without_an_inhaler(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        build(tmp.name)
        parsed = F.parse('How many patients with COPD have no inhaled corticosteroid, LAMA or LABA fill in the last year?', AS_OF)
        sql = F.render_therapy_class_gap(parsed['parameters'], AS_OF, returns='patient_set')
        out = sql_query.execute(Path(tmp.name), {'query': sql, 'returns': 'patient_set'}, target=Path(tmp.name) / 'semantic.sqlite3')
        self.assertEqual(out['patient_ids'], [])  # A (COPD) fills a LAMA; D (COPD) is deceased


class LocalCompiler(unittest.TestCase):
    def test_a_lab_threshold_atom_is_the_latest_result_unless_asked_otherwise(self):
        from clinical_core.agere.clinical import local_compiler as lc
        from test_compiler_answer_shape import PROFILE
        aggregate = lambda q: [(a['concept'], a['aggregate']) for a in lc.compile(q, PROFILE, AS_OF)[0]['nodes'][0]['parameters']['specification']['all']]
        self.assertEqual(aggregate('How many patients have an LDL over 190?'), [('ldl', 'latest')])
        self.assertEqual(aggregate('How many patients ever had an LDL over 190?'), [('ldl', 'any')])

    def test_one_definition_of_a_year(self):
        from clinical_core.agere.clinical import default_definitions
        self.assertEqual(DC.rolling_year(AS_OF), ('2029-05-28', AS_OF))
        self.assertEqual(default_definitions.default_period(AS_OF), ('2029-05-28', AS_OF))
        self.assertEqual(DC.rolling_year('2028-02-29')[0], '2027-03-02')  # the day after date('2028-02-29', '-12 months') = 2027-03-01


class Receipts(Fixture):
    def test_the_receipt_names_the_definitions_and_what_was_enforced(self):
        with DC.scope('How many patients have COPD?', AS_OF) as state:
            sql_query.execute(self.path, {'query': "SELECT COUNT(DISTINCT patient_id) FROM condition WHERE concept = 'copd'", 'returns': 'count'},
                              target=self.target)
            r = DC.receipt(state=state)
        self.assertEqual(r['version'], DC.VERSION)
        self.assertTrue(r['population']['living_only'])
        self.assertEqual(r['lab_mode'], 'latest')
        self.assertEqual(r['rewrites'][0]['kind'], 'living_patients')
        self.assertEqual(r['problems'], [])

    def test_finalize_removes_a_deceased_patient_and_says_so(self):
        result = {'answer': {'status': 'answered', 'answer_type': 'patient_set', 'count': 3, 'headline': '3 patients match.',
                             'patient_ids': ['A', 'D', 'B'], 'unknown_patient_ids': [], 'limitations': []}}
        with DC.scope('Which patients have diabetes?', AS_OF) as state:
            out = DC.finalize(self.path, result, state)
        a = out['answer']
        self.assertEqual((a['patient_ids'], a['count']), (['A', 'B'], 2))
        self.assertTrue(a['headline'].startswith('2 '))
        self.assertTrue(any('deceased' in x for x in a['limitations']))
        self.assertEqual(a['decision_context']['version'], DC.VERSION)
        with DC.scope('Which patients with diabetes died?', AS_OF) as state:  # the question asks for the deceased: untouched
            kept = DC.finalize(self.path, {'answer': {**result['answer'], 'patient_ids': ['A', 'D', 'B'], 'count': 3, 'limitations': []}}, state)
        self.assertEqual(kept['answer']['patient_ids'], ['A', 'D', 'B'])


class MandatoryCheck(Fixture):
    ENV = {'AGERE_CATALOG_DENSE': 'off', 'AGERE_LOCAL_TYPED': 'off', 'AGERE_LOCAL_NORMALIZER': 'off'}

    def check(self, question, answer, sql='SELECT 1'):
        graph = {'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                 'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': 'patient_set'}}]}
        uncal = {'tier': 'local_compiler', 'status': 'uncalibrated', 'accept': True, 'certified': False, 'reason': 'x', 'statement': 'x'}
        unverified = {'level': 'unverified', 'reason': 'x', 'gates': []}
        env = patch.dict(os.environ, self.ENV); env.start(); self.addCleanup(env.stop)
        for target, value in (('clinical_core.agere.clinical.cascade.certify_route', uncal), ('clinical_core.agere.clinical.gate_policy.assess', unverified)):
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)
        stack = SimpleNamespace(path=str(self.path), allow_live=False)
        with DC.scope(question, AS_OF):
            return A.check(stack, question, graph, {'route': 'local_compiler'}, {'answer': answer, 'results': []}, profile={'streams': {}})

    def answer(self, ids):
        return {'status': 'answered', 'answer_type': 'patient_set', 'count': len(ids), 'patient_ids': ids, 'missing': []}

    def test_it_is_mandatory_and_runs(self):
        self.assertIn('decision_context', A.MANDATORY_CHECKS)
        v = self.check('Which patients have diabetes?', self.answer(['A', 'B']))
        self.assertIn('decision_context', v['checks_run'])
        self.assertEqual(v['decision_context']['ok'], True)
        self.assertEqual(v['result']['answer']['decision_context']['version'], DC.VERSION)

    def test_a_deceased_patient_declines_the_proposal_so_the_next_proposer_tries(self):
        v = self.check('Which patients have diabetes?', self.answer(['A', 'D']))
        self.assertFalse(v['accept'])
        self.assertTrue(any('deceased' in r for r in v['reasons']))
        self.assertTrue(self.check('Which patients with diabetes died?', self.answer(['A', 'D']))['accept'])  # asked for: never rewritten or declined

    def test_an_ehr_only_event_source_declines(self):
        sql = "SELECT DISTINCT v.patient_id FROM visit v WHERE v.type IN ('inpatient', 'Inpatient POD3') AND v.event_date BETWEEN '2029-01-01' AND '2029-12-31'"
        v = self.check('Which patients had an inpatient stay in 2029?', self.answer(['A']), sql)
        self.assertFalse(v['accept'])
        self.assertTrue(any('all-source events' in r for r in v['reasons']))
        ok = self.check('Which patients had an inpatient stay in 2029?', self.answer(['A']),
                        "SELECT DISTINCT patient_id FROM utilization_event WHERE kind = 'inpatient'")
        self.assertTrue(ok['accept'])


if __name__ == '__main__':
    unittest.main()


class SavedPlansFollowDefinitions(unittest.TestCase):
    """A saved plan replays only under the definitions it was planned with (2026-10-23: a pre-decision-context Haiku plan
    replayed ahead of the chart reader)."""
    def test_store_stamps_the_definitions_version(self):
        import json as _json, sqlite3 as _sqlite3, tempfile as _tempfile
        from pathlib import Path as _Path
        from clinical_core.agere.clinical import shared_planner as SP
        from clinical_core.agere.clinical.decision_context import VERSION
        with _tempfile.TemporaryDirectory() as d:
            db = _Path(d) / 'question-plans.sqlite3'
            SP.store_request(db, 'k', 's', 'How many patients?', {'kind': 'request', 'request': {'source': {}}},
                             {'execution_accepted': True, 'interpreter': 'compositional_grammar'})
            payload = _json.loads(_sqlite3.connect(db).execute('SELECT payload FROM shared_requests').fetchone()[0])
            self.assertEqual(payload['interpretation']['definitions_version'], VERSION)
