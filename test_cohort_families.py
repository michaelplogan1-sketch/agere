"""Cohort families (clinical_core/agere/clinical/cohort_families.py): deterministic population-health patient lists.

Small synthetic sqlite fixtures per family, parser generalization over phrasings, declines, the model-fill path (fake
transport), the economical-planner rung and the specialists-first claim in together.compose_together. No paid calls."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import cohort_families as cf, sql_query, together

AS_OF = '2030-05-27'

SCHEMA = '''
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, sex_reported TEXT, birth_date TEXT);
CREATE TABLE condition(record_id TEXT, patient_id TEXT, icd10 TEXT);
CREATE TABLE payer_claim(payer_claim_id TEXT, patient_id TEXT, type_of_bill TEXT, place_of_service TEXT, service_from TEXT,
  admission_date TEXT, discharge_date TEXT, discharge_status TEXT, principal_diagnosis TEXT, diagnosis_codes TEXT,
  counts_in_metrics INTEGER);
CREATE TABLE payer_claim_line(payer_claim_id TEXT, patient_id TEXT, service_date TEXT, cpt TEXT, revenue_code TEXT);
CREATE TABLE procedure_line(patient_id TEXT, service_date TEXT, cpt TEXT, counts_in_metrics INTEGER);
CREATE TABLE visit(patient_id TEXT, type TEXT, status TEXT, event_date TEXT);
CREATE TABLE encounter(patient_id TEXT, encounter_id TEXT, type TEXT, event_date TEXT);
CREATE TABLE encounter_diagnosis(encounter_id TEXT, patient_id TEXT, icd10 TEXT);
CREATE TABLE adt_visit(patient_id TEXT, patient_class TEXT, final_patient_class TEXT, admit_date TEXT, discharge_date TEXT,
  discharge_disposition TEXT);
CREATE TABLE measurement(record_id TEXT, patient_id TEXT, concept TEXT, value_canonical REAL, result_status TEXT,
  result_current INTEGER, event_date TEXT);
CREATE TABLE medication_list_entry(patient_id TEXT, name TEXT, list_status TEXT);
CREATE TABLE fill(patient_id TEXT, fill_date TEXT, days_supply INTEGER, supply_end TEXT, drug_name TEXT, rxnorm_ingredient TEXT);
CREATE TABLE enrollment_span(patient_id TEXT, product TEXT, coverage_start TEXT, coverage_end TEXT);
CREATE TABLE cancer_screening(patient_id TEXT, test TEXT, event_date TEXT);
CREATE TABLE death(patient_id TEXT, death_date TEXT, cclf8_death_date TEXT);
'''


def build(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    ins = lambda table, rows: db.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
    ins('meta', [('as_of', AS_OF)])
    ins('patient', [(p, s, b) for p, s, b in [
        ('H1', 'M', '1950-01-01'), ('H2', 'M', '1950-01-01'), ('H3', 'M', '1950-01-01'), ('H4', 'M', '1950-01-01'), ('H5', 'F', '1950-01-01'),
        ('D1', 'M', '1980-01-01'), ('D2', 'M', '1980-01-01'), ('D3', 'M', '1950-01-01'), ('D4', 'F', '1970-01-01'),
        ('B1', 'F', '1970-01-01'), ('B2', 'F', '1970-01-01'), ('B3', 'F', '1970-01-01'), ('B4', 'M', '1970-01-01'), ('B5', 'F', '1970-01-01')]])
    ins('condition', [('c1', 'H1', 'I50.9'), ('c3', 'H3', 'I50.22'), ('c5', 'H5', 'I50.1'),
                      ('c6', 'D1', 'E11.9'), ('c7', 'D2', 'E11.65'), ('c8', 'D3', 'E10.9'), ('c9', 'D4', 'E11.9'),
                      ('ca', 'A1', 'E11.9'), ('cb', 'A2', 'E11.9'), ('cc', 'A3', 'E11.9')])
    ins('payer_claim', [
        ('pc-h2', 'H2', '0131', '11', '2029-10-01', None, None, None, 'I10', 'I10,I50.22', 1),
        # LBP: ED claims
        ('ed-l1', 'L1', None, '23', '2029-12-01', None, None, None, 'M54.50', 'M54.50', 1),
        ('ed-l2', 'L2', None, '22', '2029-12-01', None, None, None, 'M545', 'M545', 1),
        ('ed-l3', 'L3', None, '23', '2029-12-01', None, None, None, 'M54.5', 'M54.5', 1),
        ('ip-l3', 'L3', '0111', '21', '2029-12-02', '2029-12-02', '2029-12-05', '03', 'M54.5', 'M54.5', 1),
        ('ed-l4', 'L4', None, '23', '2030-05-01', None, None, None, 'M54.5', 'M54.5', 1),
        # utilization
        ('u1a', 'U1', None, '23', '2029-07-01', None, None, None, 'R07.9', '', 1),
        ('u1b', 'U1', None, '23', '2029-09-01', None, None, None, 'R07.9', '', 1),
        ('u1c', 'U1', None, '23', '2030-01-01', None, None, None, 'R07.9', '', 1),
        ('u2a', 'U2', None, '23', '2029-07-01', None, None, None, 'R07.9', '', 1),
        ('u2b', 'U2', None, '23', '2029-07-01', None, None, None, 'R07.9', '', 1),
        ('u2c', 'U2', None, '23', '2029-08-01', None, None, None, 'R07.9', '', 1),
        ('u2d', 'U2', None, '23', '2028-08-01', None, None, None, 'R07.9', '', 1),
        # TCM discharges
        ('t1', 'T1', '0111', '21', '2030-02-25', '2030-02-25', '2030-03-01', '01', 'I21.4', '', 1),
        ('t3', 'T3', '0111', '21', '2030-02-25', '2030-02-25', '2030-03-01', '20', 'I21.4', '', 1),
        ('t4', 'T4', '0111', '21', '2030-05-15', '2030-05-15', '2030-05-20', '01', 'I21.4', '', 1)])
    ins('payer_claim_line', [('ed-l2', 'L2', '2029-12-01', None, '0450')])
    ins('procedure_line', [('L2', '2030-01-05', '97110', 1), ('T2', '2030-03-10', '99213', 1), ('Q2', '2030-02-10', '90834', 1),
                           ('B5', '2028-01-01', '77067', 1)])
    ins('visit', [('A3', 'office', 'completed', '2030-01-20'), ('A1', 'telephone', 'completed', '2030-01-20')])
    ins('encounter', [('U1', 'e1', 'emergency_registration', '2030-03-01'), ('U2', 'e2', 'emergency_registration', '2029-07-01')])
    ins('encounter_diagnosis', [('e1', 'U1', 'R07.9')])
    ins('adt_visit', [('T2', 'I', 'I', '2030-02-25', '2030-03-01', 'home')])
    ins('measurement', [('m1', 'A1', 'hba1c', 9.5, 'final', 1, '2030-01-10'),
                        ('m2', 'A2', 'hba1c', 9.5, 'final', 1, '2030-01-10'), ('m3', 'A2', 'hba1c', 8.0, 'final', None, '2030-02-01'),
                        ('m4', 'A3', 'hba1c', 10.0, 'final', 1, '2030-01-10'), ('m5', 'A4', 'hba1c', 9.5, 'final', 1, '2030-01-10'),
                        ('m6', 'Q1', 'phq9', 12, 'final', 1, '2030-02-01'), ('m7', 'Q2', 'phq9', 15, 'final', 1, '2030-02-01'),
                        ('m8', 'Q3', 'phq9', 8, 'final', 1, '2030-02-01')])
    ins('medication_list_entry', [('H1', 'Carvedilol 12.5 MG', 'active'), ('H1', 'Sacubitril/Valsartan 49/51', 'active'),
                                  ('H1', 'Spironolactone 25 MG', 'active'), ('H1', 'Empagliflozin 10 MG', 'active'),
                                  ('H5', 'Lisinopril 10 MG', 'discontinued')])
    pdc_full = [('R2', f'2029-{m:02d}-01', 31, None, 'Atorvastatin 20 MG', 'atorvastatin') for m in range(1, 13)]
    ins('fill', [('H2', '2030-04-01', 90, '2030-06-29', 'Carvedilol 25 MG', 'carvedilol'),
                 ('H5', '2030-04-01', 90, '2030-06-29', 'Metoprolol Tartrate 25 MG', 'metoprolol'),
                 ('D2', '2029-12-01', 90, '2030-03-01', 'Atorvastatin 40 MG', 'atorvastatin'),
                 ('D4', '2029-09-01', 90, '2029-12-01', 'Rosuvastatin 10 MG', 'rosuvastatin'),
                 ('R1', '2029-01-01', 30, None, 'Simvastatin 20 MG', 'simvastatin'),
                 ('R1', '2029-06-01', 30, None, 'Simvastatin 20 MG', 'simvastatin'),
                 ('R3', '2029-03-01', 30, None, 'Pravastatin 20 MG', 'pravastatin'), *pdc_full])
    ins('enrollment_span', [('B1', 'commercial', '2020-01-01', None), ('B2', 'commercial', '2020-01-01', None),
                            ('B3', 'medigap', '2020-01-01', None), ('B4', 'commercial', '2020-01-01', None),
                            ('B5', 'medicare_advantage', '2025-01-01', '2031-12-31')])
    ins('cancer_screening', [('B2', 'bilateral_screening_mammography', '2029-06-01')])
    ins('death', [('H3', '2030-01-01', '2030-01-01')])
    db.commit()
    db.close()


GOLD_STYLE = {
    'hf': ('Which heart failure patients are not on all four GDMT pillars? Definition: Heart failure = any I50.* diagnosis on a '
           'condition record or payer claim; alive at as_of (no CCLF8 death date on or before as_of). Pillars: (1) evidence-based '
           'HF beta blocker (carvedilol, metoprolol succinate, bisoprolol); (2) ACE inhibitor, ARB or sacubitril/valsartan; '
           '(3) MRA (spironolactone, eplerenone); (4) SGLT2 inhibitor. On a pillar = active on the medication list, or a fill '
           'whose supply covers any of the 120 days before as_of. List patients missing one or more pillars.'),
    'lbp': ('Which patients went to the ED with low back pain, were discharged home, and had no physical therapy within 60 days? '
            'Definition: ED visit = payer claim with place of service 23, revenue code 045x or CPT 99281-99285, service date in '
            'the 12 months before as_of and at least 60 days before as_of, with M54.5* on the claim. Discharged home = no '
            'inpatient admission (TOB 011x/012x or ADT inpatient) starting that day or the next. PT = CPT 97161-97164, 97110, '
            '97112, 97140, 97530 on any claim line with service in the 60 days after the visit.'),
    'a1c': ('Which diabetic patients have an A1c above 9 with no visit after it? Definition: Diabetes = E10*/E11* on a condition '
            'record or payer claim. Latest A1c in the 12 months before as_of above 9.0%, resulted at least 30 days before as_of, '
            'and no visit (completed office or virtual encounter, or billed CPT 99202-99215) on days 1-30 after the result.'),
    'ed': ('Which patients had 4 or more ED visits in the last 12 months? Definition: ED visits = distinct dates with an ED payer '
           'claim (place of service 23, revenue code 045x or CPT 99281-99285) or an EHR emergency encounter, in the 12 months '
           'before as_of.'),
    'tcm': ('Which patients discharged from the hospital had no follow-up visit within 14 days? Definition: Inpatient discharges '
            '(TOB 011x/012x with discharge status 01 or 06, or ADT inpatient with disposition home or home health) 14 to 182 days '
            'before as_of, with no visit (completed office or virtual encounter, or billed CPT 99202-99215) on days 1-14 after discharge.'),
    'spd': ('Which diabetic patients aged 40 to 75 are not on a statin? Definition: Diabetes = E10*/E11* on a condition record or '
            'payer claim; age 40-75 at as_of; alive at as_of. Not on a statin = no active statin on the medication list and no '
            'statin fill covering any of the 120 days before as_of.'),
    'phq': ('Which patients with a PHQ-9 of 10 or more had no follow-up within 30 days? Definition: PHQ-9 total >= 10 dated 30 to '
            '365 days before as_of, with no visit (completed office or virtual encounter, or billed CPT 99202-99215) and no '
            'behavioral health claim (CPT 90791, 90792, 90832-90838, 90846, 90847, 90853) on days 1-30 after it.'),
    'bcs': ('Which women are overdue for breast cancer screening? Definition: Women aged 52-74 at as_of, alive, with an enrollment '
            'span covering as_of, and no mammogram (cancer screening record or CPT 77065-77067, G0202, G0204, G0206) in the 27 '
            'months before as_of.'),
    'pdc': ('Which patients have statin adherence below 80% last year? Definition: Patients with 2 or more statin fills in the '
            'calendar year before as_of. PDC = days covered from the first fill in that year through 12-31, divided by those days; '
            'overlaps are not shifted; supply from earlier fills counts for days it covers. List patients with PDC below 80%.'),
}
EXPECTED = {'hf': ['H2', 'H5'], 'lbp': ['L1'], 'a1c': ['A1'], 'ed': ['U1'], 'tcm': ['T1'], 'spd': ['D1', 'D4'], 'phq': ['Q1'],
            'bcs': ['B1', 'B5'], 'pdc': ['R1']}
FAMILY = {'hf': 'therapy_class_gap', 'lbp': 'event_followup_gap', 'a1c': 'event_followup_gap', 'ed': 'utilization_threshold',
          'tcm': 'event_followup_gap', 'spd': 'therapy_class_gap', 'phq': 'event_followup_gap', 'bcs': 'screening_overdue',
          'pdc': 'adherence_pdc'}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.stack_dir = Path(self.temp.name) / 'stack'
        self.stack_dir.mkdir()
        self.db = self.stack_dir / 'semantic.sqlite3'
        build(self.db)
        (self.stack_dir / 'run.json').write_text(json.dumps({'source_digest': 'cohort-family-test'}))

    def tearDown(self):
        self.temp.cleanup()

    def run_family(self, question):
        res = cf.plan(self.stack_dir, question, AS_OF)
        self.assertIsNotNone(res, question)
        node = res['graph']['nodes'][0]['parameters']
        out = sql_query.execute(None, node, target=self.db)
        ids = out['patient_ids'] if node['returns'] == 'patient_set' else sorted({r['patient_id'] for r in out.get('rows') or []}) \
            if node['returns'] == 'table' else out['value']
        return res, ids, out


class FamilyTests(Fixture):
    def test_each_gold_style_question_is_answered_by_its_family(self):
        for key, question in GOLD_STYLE.items():
            with self.subTest(key=key):
                res, ids, _ = self.run_family(question)
                self.assertEqual(res['spec']['family'], FAMILY[key])
                self.assertEqual(ids, EXPECTED[key])
                self.assertEqual(res['calls'], [])                       # the parser filled every slot: no model
                self.assertIn('-- DEFINITION: cohort family', res['graph']['nodes'][0]['parameters']['query'])

    def test_therapy_gap_reports_which_classes_are_missing(self):
        _, _, out = self.run_family(GOLD_STYLE['hf'])
        rows = {r['patient_id']: r['missing_classes'] for r in out['rows']}
        self.assertEqual(rows['H2'], 'ras, mra, sglt2')
        self.assertEqual(rows['H5'], 'hf_beta_blocker, ras, mra, sglt2')  # metoprolol tartrate is not an HF beta blocker

    def test_identical_sql_and_results_across_runs(self):
        for question in GOLD_STYLE.values():
            a, b = cf.plan(self.stack_dir, question, AS_OF), cf.plan(self.stack_dir, question, AS_OF)
            self.assertEqual(a['graph'], b['graph'])
            self.assertEqual(self.run_family(question)[1], self.run_family(question)[1])

    def test_condition_without_therapy_is_a_one_class_gap_with_an_age_band(self):
        res, ids, _ = self.run_family('Which diabetics between the ages of 40 and 75 have no active statin?')
        self.assertEqual(res['spec']['parameters']['population']['age'], [40, 75])
        self.assertEqual([c['name'] for c in res['spec']['parameters']['classes']], ['statin'])
        self.assertEqual(ids, ['D1', 'D4'])

    def test_count_shape(self):
        res, value, _ = self.run_family('How many patients had 4 or more emergency room visits in the last 12 months?')
        self.assertEqual(res['spec']['returns'], 'count')
        self.assertEqual(value, 1)


class ParserTests(Fixture):
    VARIANTS = {
        'lbp': 'Which patients went to the emergency room with lumbago, were discharged home, and had no physical therapy within 2 months?',
        'a1c': 'Which diabetics have a most recent HbA1c above nine percent and were not seen within thirty days?',
        'ed': 'Who had at least four ER visits over the past year?',
        'tcm': 'Which patients had a hospital discharge home or to home health 14 to 182 days before as_of with no office or virtual visit within two weeks?',
        'phq': 'List patients with a PHQ9 score >= 10 from 30 to 365 days before as_of without a visit or behavioral health follow-up within 30 days.',
        'bcs': 'Which female members aged 52-74 have not had a mammogram in the last 27 months?',
        'pdc': 'Which patients were non-adherent to statins (PDC under 80 percent) last year?',
        'hf': 'List heart failure patients who are missing at least one of the 4 GDMT pillars.',
    }

    def test_phrasings_generalize(self):
        for key, question in self.VARIANTS.items():
            with self.subTest(key=key):
                res, ids, _ = self.run_family(question)
                self.assertEqual(res['spec']['family'], FAMILY[key])
                self.assertEqual(ids, EXPECTED[key])

    def test_slots(self):
        p = cf.parse(self.VARIANTS['a1c'], AS_OF)['parameters']
        self.assertEqual(p['anchor'], {'kind': 'measurement', 'concept': 'hba1c', 'op': 'gt', 'value': 9, 'latest_only': True})
        self.assertEqual(p['followup']['days'], 30)
        p = cf.parse(self.VARIANTS['tcm'], AS_OF)['parameters']
        self.assertEqual(p['window'], {'lookback': {'days': 182}, 'lower_inclusive': True, 'min_days_before': 14})
        self.assertEqual(p['followup']['days'], 14)
        p = cf.parse(GOLD_STYLE['lbp'], AS_OF)['parameters']
        self.assertEqual(p['anchor']['sources'], ['claims'])
        self.assertEqual(p['anchor']['dx_prefixes'], ['M545'])
        self.assertEqual(p['exclude_admission_within_days'], 1)
        self.assertEqual(p['followup']['procedure_codes'], ['97161-97164', '97110', '97112', '97140', '97530'])
        p = cf.parse(GOLD_STYLE['bcs'], AS_OF)['parameters']
        self.assertEqual(p['tests'][0]['codes'], ['77065-77067', 'G0202', 'G0204', 'G0206'])
        self.assertEqual(p['tests'][0]['months'], 27)

    def test_declines_shapes_the_families_do_not_represent(self):
        for q in ['How adherent were patients to their ACE inhibitors in 2029?',
                  'What is the rate of ED visits by practice?',
                  'Which women are due for a mammogram for 2029? Definition: Women 52-74 on 2029-12-31 with no mammogram in 2028-2029.',
                  'How many patients were switched from an ACE inhibitor to an ARB?',
                  'Which Medicare heart failure patients are not on a statin?',
                  'How many ED visits were there last year?',
                  'What is the average A1c of diabetic patients?']:
            with self.subTest(q=q):
                self.assertIsNone(cf.parse(q, AS_OF))

    def test_validation_refuses_bad_parameters(self):
        spec = {'family': 'event_followup_gap', 'returns': 'patient_set', 'parameters': {
            'anchor': {'kind': 'ed_visit', 'dx_prefixes': ["M54'; DROP"]}, 'followup': {'kinds': ['visit'], 'days': 30}}}
        with self.assertRaises(ValueError):
            cf.render(spec, AS_OF)

    def test_population_word_never_reaches_the_query(self):
        # sql_query treats the bare word as a request for an upstream population input
        for question in GOLD_STYLE.values():
            q = cf.plan(self.stack_dir, question, AS_OF)['graph']['nodes'][0]['parameters']['query']
            self.assertNotRegex(q, r'\bpopulation\b')


class ModelFillTests(Fixture):
    def test_only_missing_slots_go_to_the_model_and_sql_is_still_rendered_by_code(self):
        question = 'Which gout patients are not on allopurinol or febuxostat?'
        parsed = cf.parse(question, AS_OF)
        self.assertEqual(parsed['family'], 'therapy_class_gap')
        self.assertIn('condition', parsed['missing'])
        self.assertIsNone(cf.plan(self.stack_dir, question, AS_OF))     # no model available: the rung declines
        bodies = []

        def fake_send(body, provider, ledger, out, parse=None, **kw):
            bodies.append(body)
            raw = {'content': [{'type': 'tool_use', 'name': 'fill_cohort_slots', 'input': {
                'condition_prefixes': ['M10'], 'drug_classes': [{'name': 'urate_lowering', 'terms': ['allopurinol', 'febuxostat']}]}}]}
            return parse(raw), {'cache_hit': False, 'cost_estimate': {'estimated_usd': 0.0004}}
        with patch('clinical_core.agere.clinical.context_student.send', fake_send):
            res = cf.plan(self.stack_dir, question, AS_OF, ledger=object(), receipts=self.stack_dir)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]['tools'][0]['name'], 'fill_cohort_slots')
        self.assertEqual(res['spec']['parameters']['population']['condition_prefixes'], ['M10'])
        self.assertEqual(res['receipt']['model_filled'], ['condition_prefixes', 'drug_classes'])
        self.assertEqual(len(res['calls']), 1)
        self.assertIn("LIKE 'M10%'", res['graph']['nodes'][0]['parameters']['query'])


def fake_planner_stack(path, allow_live=False):
    return SimpleNamespace(path=Path(path), allow_live=allow_live, receipts=Path(path) / 'provider-receipts',
                           retriever=SimpleNamespace(meta={'snapshot': {'as_of': AS_OF}, 'source_digest': 'cohort-family-test'},
                                                     encoder=None))


class PlannerRungTests(Fixture):
    def setUp(self):
        super().setUp()
        self.env = patch.dict(os.environ, {'AGERE_LOCAL_MODEL': 'off', 'AGERE_COHORT_FAMILIES': 'on'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def test_inline_definition_question_routes_to_the_family_rung(self):
        from clinical_core.agere.clinical.economical_planner import plan
        graph, receipt = plan(fake_planner_stack(self.stack_dir), GOLD_STYLE['tcm'], None, None)
        self.assertEqual(receipt['route'], 'cohort_family')
        self.assertEqual(receipt['cohort_family']['family'], 'event_followup_gap')
        self.assertEqual(receipt['new_paid_api_calls'], 0)
        self.assertEqual(graph['nodes'][0]['operation'], 'sql')
        self.assertIn('cohort_family', together.SPECIALIST_ROUTES)

    def test_bare_question_routes_after_the_governed_rungs(self):
        from clinical_core.agere.clinical.economical_planner import plan
        graph, receipt = plan(fake_planner_stack(self.stack_dir), ParserTests.VARIANTS['ed'], None, None)
        self.assertEqual(receipt['route'], 'cohort_family')
        self.assertEqual(receipt['cohort_family']['family'], 'utilization_threshold')

    def test_flag_off_keeps_the_ladder_unchanged(self):
        from clinical_core.agere.clinical.economical_planner import plan
        with patch.dict(os.environ, {'AGERE_COHORT_FAMILIES': 'off'}):
            with self.assertRaises(Exception):  # no other rung can answer in this fixture (hosted planning disabled)
                plan(fake_planner_stack(self.stack_dir), GOLD_STYLE['tcm'], None, None)


class FamilyStack:
    """A stack whose fast path is the real economical planner + a read-only execution of its SQL node."""
    def __init__(self, path):
        self.path = Path(path)
        self.receipts = self.path / 'provider-receipts'
        self.receipts.mkdir(exist_ok=True)
        self.ledger_path = self.path.parent / 'ledger.sqlite3'
        self.allow_live = True
        self.answer_lock = threading.RLock()
        self.retriever = SimpleNamespace(meta={'snapshot': {'as_of': AS_OF}, 'source_digest': 'cohort-family-test'}, encoder=None)
        self.composed = 0

    def _compose(self, question, *, planning='auto'):
        from clinical_core.agere.clinical.economical_planner import plan
        from clinical_core.agere.clinical.metric_router import finalize_answer
        self.composed += 1
        graph, receipt = plan(SimpleNamespace(**{**vars(self), 'allow_live': False}), question, None, None)
        node = graph['nodes'][0]['parameters']
        out = sql_query.execute(None, node, target=self.path / 'semantic.sqlite3')
        ids = out['patient_ids'] if node['returns'] == 'patient_set' else sorted(r['patient_id'] for r in out['rows'])
        answer = {'status': 'answered', 'answer_type': 'patient_set', 'patient_ids': ids, 'count': len(ids), 'value': None,
                  'table': out.get('rows'), 'missing': [], 'grounding': [], 'query': node['query']}
        answer = finalize_answer(answer, receipt)
        return {'answer': answer, 'graph': graph, 'planner_receipt': receipt, 'planning_economics': {'new_api_estimate_usd': 0},
                'new_paid_api_calls': 0, 'question': question, 'route': 'economical_composition_local_execution'}


class SpecialistsFirstTests(Fixture):
    def setUp(self):
        super().setUp()
        self.env = patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'fake-test-key', 'AGERE_TOGETHER': 'on', 'AGERE_LOCAL_MODEL': 'off',
                                           'AGERE_SPECIALISTS_FIRST': 'on', 'AGERE_DEFER_FAST': 'on', 'AGERE_HYBRID': 'off',
                                           'AGERE_COHORT_FAMILIES': 'on'})
        self.env.start()

    def tearDown(self):
        together.TRANSPORT = None
        self.env.stop()
        super().tearDown()

    def test_family_route_claims_a_defined_question_and_the_analysts_never_start(self):
        def no_analyst(*a, **k):
            raise AssertionError('the analysts must not run when the family claims the question')
        together.TRANSPORT = no_analyst
        stack = FamilyStack(self.stack_dir)
        with patch.object(together, '_run_analyst', no_analyst):
            result = together.compose_together(stack, GOLD_STYLE['phq'], answer_type='patient_set')
        self.assertEqual(result['together']['analyst']['state'], 'skipped_specialist')
        self.assertEqual(result['planner_receipt']['route'], 'cohort_family')
        self.assertEqual(result['answer']['patient_ids'], ['Q1'])
        self.assertEqual(result['answer']['computed_as']['cohort_family'], 'event_followup_gap')
        self.assertEqual(result['answer']['computed_as']['parameters']['followup']['days'], 30)
        # the verified cache now holds the family answer and reuses it for the same question
        with patch.object(together, '_run_analyst', no_analyst):
            again = together.compose_together(stack, GOLD_STYLE['phq'], answer_type='patient_set')
        self.assertEqual(again['planner_receipt']['route'], 'learned_verified_sql')
        self.assertEqual(again['planner_receipt']['cohort_family']['family'], 'event_followup_gap')
        self.assertEqual(again['answer']['patient_ids'], ['Q1'])
        self.assertEqual(again['together']['analyst']['state'], 'skipped_specialist')

    def test_metric_router_still_refuses_defined_questions_but_the_family_route_does_not(self):
        fast = {'planner_receipt': {'route': 'metric_router'}, 'answer': {'status': 'answered', 'answer_type': 'patient_set',
                                                                          'patient_ids': ['Q1'], 'count': 1}}
        self.assertFalse(together._specialist_answer(fast, 'patient_set', GOLD_STYLE['phq']))
        fast['planner_receipt']['route'] = 'cohort_family'
        self.assertTrue(together._specialist_answer(fast, 'patient_set', GOLD_STYLE['phq']))


if __name__ == '__main__':
    unittest.main()
