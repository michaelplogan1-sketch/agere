"""General analysis path fixtures; scripted model only, no provider, hidden references or generator truth."""
import json
import unittest
from unittest.mock import Mock, patch

import test_shared_planner as fixtures
from clinical_core.agere.clinical import general_analysis as G, request_plan as R, shared_planner as S

Q = 'How many patients with diabetes logged two or more ED visits in 2029?'  # wording the free parser does not read


def spec(year='2029'):
    return {'steps': [
        {'name': 'ed_counts', 'purpose': 'ED visits per patient in the year',
         'sql': f"SELECT patient_id, COUNT(*) AS n FROM utilization_event WHERE kind='ed_visit' "
                "AND event_date BETWEEN {{parameter:year}} || '-01-01' AND {{parameter:year}} || '-12-31' GROUP BY patient_id"},
        {'name': 'answer', 'purpose': 'Diabetic patients with at least two',
         'sql': 'SELECT COUNT(DISTINCT patient_id) AS patients FROM ed_counts WHERE n >= 2 '
                'AND patient_id IN {{condition:diabetes}}'}],
        'final': 'answer', 'returns': 'count', 'parameters': [{'name': 'year', 'value': int(year), 'quote': year}],
        'requirements': [{'quote': 'How many patients', 'applied_in': ['answer'], 'how': 'distinct count'},
                         {'quote': 'with diabetes', 'applied_in': ['answer'], 'how': 'governed condition macro'},
                         {'quote': 'logged two or more ED visits', 'applied_in': ['ed_counts', 'answer'], 'how': 'n >= 2'},
                         {'quote': f'in {year}', 'applied_in': ['ed_counts'], 'how': 'inclusive calendar year'}],
        'definitions': [{'term': 'diabetes', 'basis': '{{condition:diabetes}}'}], 'unresolved': []}


class GeneralAnalysis(unittest.TestCase):
    def setUp(self):
        fixtures.SharedPlanner.setUp(self)

    def test_prepare_expands_governed_definitions_and_records_exact_sources(self):
        c = G.prepare(self.catalog, Q, spec())
        self.assertIn("concept IN ('diabetes')", c['query'])
        self.assertIn('is_diagnosis = 1', c['query'])
        self.assertEqual(c['governed_macros'], ['condition:diabetes'])
        self.assertEqual(c['sources']['utilization_event'], ['event_date', 'kind', 'patient_id'])
        self.assertIn('condition', c['sources'])
        self.assertIs(G.sealed(c), c)
        tampered = {**c, 'query': c['query'].replace('>= 2', '>= 1')}
        with self.assertRaises(R.PlanError):
            G.sealed(tampered)

    def test_every_question_word_must_be_mapped_or_explicitly_unresolved(self):
        s = spec()
        s['requirements'] = [r for r in s['requirements'] if r['quote'] != 'with diabetes']
        with self.assertRaisesRegex(R.PlanError, 'diabetes'):
            G.prepare(self.catalog, Q, s)
        s['unresolved'] = [{'quote': 'with diabetes', 'reason': 'test'}]
        self.assertTrue(G.prepare(self.catalog, Q, s)['analysis']['unresolved'])
        s = spec()
        s['requirements'][1]['quote'] = 'diabetic'  # not literal question text
        with self.assertRaises(R.PlanError):
            G.prepare(self.catalog, Q, s)

    def test_sql_is_bounded_read_only_and_compiled_against_the_catalog(self):
        nested = spec()
        nested['steps'][0]['sql'] = ("WITH v AS (SELECT patient_id FROM utilization_event WHERE kind='ed_visit' AND event_date BETWEEN "
                                     "'2029-01-01' AND '2029-12-31') SELECT patient_id, COUNT(*) AS n FROM v GROUP BY patient_id")
        _, shown = G.preview(self.catalog, G.prepare(self.catalog, Q, nested))
        self.assertEqual(shown['value'], 1)  # a nested WITH inside a step is valid SQL and runs
        for bad in ["WITH RECURSIVE x(n) AS (SELECT 1) SELECT n FROM x", "SELECT 1; DELETE FROM patient",
                    "SELECT age_latest FROM patient", "SELECT * FROM missing_table",
                    "SELECT patient_id FROM patient WHERE patient_id IN {{condition:not_a_concept}}"]:
            s = spec()
            s['steps'][0]['sql'] = bad
            with self.assertRaises((R.PlanError, ValueError)):
                G.prepare(self.catalog, Q, s)

    def test_execution_checks_and_candidate_status_without_acceptance(self):
        model = Mock(side_effect=[(json.dumps({'analysis': spec()}), {}),
                                  (json.dumps({'equivalent': True, 'issues': []}), {})])
        a = S.compose(self.stack, Q, model=model)
        self.assertEqual(a['answer']['count'], 1)  # A only: two 2029 ED visits; B has one; D lacks a diagnosis
        self.assertEqual(a['answer']['status'], 'partial')  # provided interpreter is not an accepted hosted review
        self.assertFalse(a['answer']['actionable_cohort'])
        self.assertIn('utilization_event', a['answer']['sources'])
        self.assertEqual(a['answer']['analysis']['requirements'][2]['quote'], 'logged two or more ED visits')
        audit = json.loads(model.call_args_list[1].args[1])
        self.assertEqual(audit['result_preview']['value'], 1)
        self.assertNotIn('learned_analysis_shape', a)

    def test_review_mismatch_is_repaired_before_execution(self):
        wrong = spec()
        wrong['steps'][1]['sql'] = wrong['steps'][1]['sql'].replace('>= 2', '>= 1')
        model = Mock(side_effect=[(json.dumps({'analysis': wrong}), {}),
                                  (json.dumps({'equivalent': False, 'issues': ['uses one or more']}), {}),
                                  (json.dumps({'analysis': spec()}), {}),
                                  (json.dumps({'equivalent': True, 'issues': []}), {})])
        a = S.compose(self.stack, Q, model=model)
        self.assertEqual(a['answer']['count'], 1)
        feedback = json.loads(model.call_args_list[2].args[1])['validator_feedback']
        self.assertIn('semantic_review', feedback)

    def hosted(self, responses):
        model = Mock(side_effect=responses)
        model.identity = 'configured-frontier'
        self.stack.allow_live = True
        self.stack.receipts = self.path
        return model

    def test_historical_checked_analysis_cache_and_parameter_recompile_remain_compatible(self):
        model = self.hosted([(json.dumps({'analysis': spec()}), {'api_usd': .2, 'api_cost_known': True}),
                             (json.dumps({'equivalent': True, 'issues': []}), {'api_usd': .1, 'api_cost_known': True})])
        def historical_receipt(c,q,**kwargs):
            # Authored checked receipt for stored-artifact compatibility.
            # Production creates this receipt only after deterministic decline,
            # hosted enablement and a successful semantic review.
            contract, interpretation=fixtures.authored_review_fixture(c,q,model,population=kwargs.get('population'))
            interpretation['review']='checked_hosted_interpretation'
            return contract,interpretation
        with patch.object(S,'checked_interpret',side_effect=historical_receipt):
            first = S.compose(self.stack, Q)
            again = S.compose(self.stack, Q)
            other_year = S.compose(self.stack, Q.replace('2029', '2028'))
        self.assertEqual(first['answer']['status'], 'answered')
        self.assertEqual(first['answer']['missing'], [])
        self.assertEqual(first['new_paid_api_calls'], 2)
        self.assertTrue(first['learned_analysis_shape'])
        self.assertEqual(again['new_paid_api_calls'], 0)
        self.assertTrue(again['planner_receipt']['interpretation']['exact_contract_cache_hit'])
        self.assertEqual(other_year['new_paid_api_calls'], 0)
        self.assertEqual(other_year['planner_receipt']['interpretation']['interpreter'], 'learned_analysis_shape')
        self.assertIn("2028 || '-01-01'", other_year['request_contract']['query'])
        self.assertEqual(other_year['answer']['count'], 0)  # A's single 2028 visit
        self.assertEqual(model.call_count, 2)

    def test_verified_answer_and_shape_survive_a_code_release(self):
        model = self.hosted([(json.dumps({'analysis': spec()}), {'api_usd': .2, 'api_cost_known': True}),
                             (json.dumps({'equivalent': True, 'issues': []}), {'api_usd': .1, 'api_cost_known': True})])
        def historical_receipt(c,q,**kwargs):
            contract, interpretation=fixtures.authored_review_fixture(c,q,model,population=kwargs.get('population'))
            interpretation['review']='checked_hosted_interpretation'
            return contract,interpretation
        with patch.object(S,'checked_interpret',side_effect=historical_receipt):
            first = S.compose(self.stack, Q)
        with patch.object(S,'policy_identity',return_value='next-release'), \
             patch.object(S,'checked_interpret',side_effect=AssertionError('must replay, not reinterpret')):
            again = S.compose(self.stack, Q)
            other_year = S.compose(self.stack, Q.replace('2029', '2028'))
            replay = S.compose(self.stack, Q)
        self.assertEqual(again['answer']['count'], first['answer']['count'])
        self.assertEqual(again['new_paid_api_calls'], 0)
        self.assertIn('carry_forward', again['planner_receipt']['interpretation'])
        self.assertEqual(other_year['answer']['count'], 0)
        self.assertTrue(replay['planner_receipt']['interpretation']['exact_contract_cache_hit'])
        self.assertEqual(model.call_count, 2)

    def test_typed_failure_can_switch_to_analysis(self):
        model = Mock(side_effect=[(json.dumps({'source': {'table': 'nope', 'quote': 'patients'}}), {}),
                                  (json.dumps({'analysis': spec()}), {}),
                                  (json.dumps({'equivalent': True, 'issues': []}), {})])
        a = S.compose(self.stack, Q, model=model)
        self.assertEqual(a['answer']['count'], 1)
        self.assertIn('analysis', json.loads(model.call_args_list[1].args[1])['validator_feedback']['instruction'])

    def test_final_llm_analysis_answers_when_every_other_path_fails(self):
        bad_typed = (json.dumps({'source': {'table': 'nope', 'quote': 'patients'}}), {})
        loose = spec()
        loose['requirements'] = [r for r in loose['requirements'] if r['quote'] != 'logged two or more ED visits']
        model = Mock(side_effect=[bad_typed] * 3 + [(json.dumps({'analysis': loose}), {}),
                                                    (json.dumps({'equivalent': True, 'issues': []}), {})])
        a = S.compose(self.stack, Q, model=model)
        self.assertEqual(a['answer']['count'], 1)
        self.assertEqual(a['answer']['status'], 'partial')  # answered, with the unmapped words disclosed
        self.assertTrue(any('ed' in m.lower() and 'confirm' in m.lower() for m in a['answer']['missing']))
        self.assertTrue(a['planner_receipt']['interpretation']['final_llm_analysis'])
        self.assertIn('Do not return a typed request', json.loads(model.call_args_list[3].args[1])['validator_feedback']['instruction'])

    def test_governed_dose_relation_and_parsers(self):
        from clinical_core.agere.clinical import dose as D
        self.assertEqual((D.dose_mg('carvedilol 12.5 mg tablet BID'), D.per_day('carvedilol 12.5 mg tablet BID')), (12.5, 2.0))
        self.assertEqual(D.dose_mg('Sacubitril/valsartan 49/51 mg'), 49.0)
        self.assertEqual(D.target_mg('sacubitril/valsartan'), 194.0)
        self.assertIsNone(D.target_mg('metoprolol tartrate 25 mg'))  # not an evidence-based HF beta blocker
        self.assertEqual(D.target_mg('metoprolol succinate 50 mg ER'), 200.0)
        self.assertIsNone(D.dose_mg('insulin 10 units'))
        self.assertEqual(D.dose_mg('carvedilol 3.125 mg'), 3.125)  # never mistaken for 25 mg
        import sqlite3
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript("""CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, payload TEXT);
              INSERT INTO record VALUES ('M1','A','medication_lists','2029-03-01','{"name":"Carvedilol","dose":"12.5 mg","frequency":"BID","list_status":"active","event_date":"2029-03-01"}'),
                ('M2','A','medication_orders','2029-04-01','{"medication_name":"carvedilol 6.25 mg tablet","strength":6.25,"strength_unit":"mg/tablet","dose":1,"dose_unit":"tablet","times_per_day":2,"status":"active","event_date":"2029-04-01"}');""")
        catalog = R.Catalog.load(self.path)
        s = {'steps': [{'name': 'doses', 'purpose': 'dose evidence', 'sql':
                        "SELECT patient_id, record_id, daily_mg, target_daily_mg FROM {{medication_dose}} ORDER BY record_id"}],
             'returns': 'table', 'requirements': [{'quote': 'carvedilol dose', 'applied_in': ['doses'], 'how': 'governed dose relation'}],
             'unresolved': []}
        c = G.prepare(catalog, 'carvedilol dose', s)
        self.assertEqual(c['governed_macros'], ['medication_dose'])
        _, shown = G.preview(catalog, c)
        self.assertEqual([(r['record_id'], r['daily_mg'], r['target_daily_mg']) for r in shown['rows']],
                         [('M1', 25.0, 50.0), ('M2', 12.5, 50.0)])

    def test_current_dose_window_unknown_status_and_specialty_groups(self):
        import sqlite3
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript("""CREATE TABLE record(record_id TEXT, patient_id TEXT, stream TEXT, event_date TEXT, payload TEXT);
              INSERT INTO record VALUES
                ('OLD','A','medication_lists','2027-01-01','{"name":"Carvedilol","dose":"25 mg","frequency":"BID","event_date":"2027-01-01"}'),
                ('NEW','A','medication_lists','2030-01-01','{"name":"Carvedilol","dose":"6.25 mg","frequency":"BID","list_status":"active","event_date":"2030-01-01"}'),
                ('UNK','B','medication_lists','2030-02-01','{"name":"Spironolactone","frequency":"daily","list_status":"active","event_date":"2030-02-01"}'),
                ('STALE','C','medication_lists','2026-01-01','{"name":"Lisinopril","dose":"40 mg","frequency":"daily","event_date":"2026-01-01"}');
              CREATE TABLE clinician(clinician_id TEXT, specialty TEXT);
              INSERT INTO clinician VALUES ('C1','Cardiology'),('C2','Cardiothoracic Surgery'),('C3','Family Medicine');""")
        catalog = R.Catalog.load(self.path)
        s = {'steps': [{'name': 'cur', 'purpose': 'current doses', 'sql':
                        "SELECT patient_id, drug_key, record_id, daily_mg, dose_status FROM {{current_dose}} ORDER BY patient_id"},
                       {'name': 'cards', 'purpose': 'cardiologists', 'sql': 'SELECT clinician_id FROM {{specialty:cardiology}}'}],
             'final': 'cur', 'returns': 'table',
             'requirements': [{'quote': 'current doses', 'applied_in': ['cur'], 'how': 'governed window'},
                              {'quote': 'cardiologists', 'applied_in': ['cards'], 'how': 'governed specialty'}], 'unresolved': []}
        with self.assertRaisesRegex(R.PlanError, 'not used'):
            G.prepare(catalog, 'current doses and cardiologists', s)
        # These are independent outputs: a final dose table cannot also claim
        # it returned an unrelated, unused cardiologist step.
        s['requirements'] = s['requirements'][:1]
        c = G.prepare(catalog, 'current doses', s)
        _, shown = G.preview(catalog, c)
        self.assertEqual([(r['patient_id'], r['record_id'], r['daily_mg'], r['dose_status']) for r in shown['rows']],
                         [('A', 'NEW', 12.5, 'below_target'), ('B', 'UNK', None, 'unknown_dose')])  # C is outside the window
        self.assertIn("specialty IN ('Cardiology')", c['query'])
        self.assertEqual(G.data_currency(catalog, ['A', 'C'])['stale_patients'], 1)
        with self.assertRaises(R.PlanError):
            G.prepare(catalog, 'current doses and cardiologists', {**s, 'steps': [s['steps'][0], {**s['steps'][1],
                      'sql': 'SELECT clinician_id FROM {{specialty:cardiac_magic}}'}]})

    def test_patient_selection_accepts_singular_listing_and_rejects_counts(self):
        q = 'Find every patient with diabetes and their ED visits'
        s = {'steps': [{'name': 'rows', 'purpose': 'patients', 'sql':
                        'SELECT patient_id FROM patient WHERE patient_id IN {{condition:diabetes}}'}],
             'returns': 'table', 'requirements': [{'quote': 'Find every patient with diabetes', 'applied_in': ['rows'], 'how': 'x'},
                                                   {'quote': 'their ED visits', 'applied_in': ['rows'], 'how': 'x'}],
             'unresolved': [], 'patient_selection': {'column': 'patient_id', 'quote': 'Find every patient'}}
        self.assertEqual(G.prepare(self.catalog, q, s)['analysis']['patient_selection']['quote'], 'Find every patient')
        counted = {**s, 'patient_selection': {'column': 'patient_id', 'quote': 'How many patients'}}
        cq = 'How many patients have diabetes'
        counted['requirements'] = [{'quote': 'How many patients have diabetes', 'applied_in': ['rows'], 'how': 'x'}]
        with self.assertRaises(R.PlanError):
            G.prepare(self.catalog, cq, counted)
        self.assertIsNone(G.prepare(self.catalog, cq, counted, relaxed=True)['analysis']['patient_selection'])

    def test_clinical_owner_definitions_select_evidence_and_are_disclosed(self):
        import sqlite3
        from clinical_core.agere.clinical import clinical_definitions as CD
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript("""DROP TABLE condition;
              CREATE TABLE condition(patient_id, concept, is_diagnosis, diagnosis_basis);
              INSERT INTO condition VALUES ('A','heart_failure_reduced_ef',1,NULL),('B','heart_failure',1,NULL),('C','heart_failure',1,NULL),('D','diabetes',1,NULL);
              CREATE TABLE note_fact(patient_id, fact_type, value, negated, experiencer, event_date);
              INSERT INTO note_fact VALUES ('B','ejection_fraction',30,0,'patient','2029-01-01'),('C','ejection_fraction',30,1,'patient','2029-01-01'),
                ('D','ejection_fraction',25,0,'patient','2029-01-01'),('C','ejection_fraction','not_quantifiable',0,'patient','2029-01-01');
              CREATE TABLE therapy_barrier(patient_id, drug_key, drug_class, gdmt_pillar, barrier_type, assertion, negated, event_date, record_id);
              INSERT INTO therapy_barrier VALUES ('A','carvedilol','beta_blocker','beta_blocker','intolerance_side_effect','affirmed',0,'2029-02-01','TB1'),
                ('B','lisinopril','ace_inhibitor','rasi','other','affirmed',1,'2029-02-01','TB2');
              CREATE TABLE note_therapy_change(patient_id, drug_key, drug_class, action, event_date, record_id);
              INSERT INTO note_therapy_change VALUES ('B','spironolactone','mra','dose_decrease','2029-03-01','TC1'),('B','x','y','start','2029-03-01','TC2');""")
        catalog = R.Catalog.load(self.path)
        q = 'heart failure with reduced EF and tolerated dose'
        s = {'steps': [{'name': 'ph', 'purpose': 'phenotype', 'sql': 'SELECT patient_id, hfref_basis FROM {{phenotype:hfref}} ORDER BY patient_id'}],
             'returns': 'table', 'requirements': [{'quote': 'heart failure with reduced EF', 'applied_in': ['ph'], 'how': 'governed'},
                                                   {'quote': 'and tolerated dose', 'applied_in': ['ph'], 'how': 'x'}], 'unresolved': []}
        _, shown = G.preview(catalog, G.prepare(catalog, q, s))
        self.assertEqual([(r['patient_id'], r['hfref_basis']) for r in shown['rows']], [('A', 'coded_hfref'), ('B', 'note_ef_le_40')])
        s['steps'][0]['sql'] = 'SELECT patient_id, evidence_type, record_id FROM {{tolerance_evidence}} ORDER BY record_id'
        _, shown = G.preview(catalog, G.prepare(catalog, q, s))
        self.assertEqual([r['record_id'] for r in shown['rows']], ['TB1', 'TC1'])  # negated barrier and non-decrease changes excluded
        self.assertIn('40%', CD.PHENOTYPES['hfref']['disclosure'])
        self.assertIn('clinical_owner_definitions', G.governed_knowledge())


if __name__ == '__main__':
    unittest.main()
