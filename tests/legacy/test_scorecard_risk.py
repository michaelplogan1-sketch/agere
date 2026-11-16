"""Scorecard risk engine: fixed bins, WoE/IV, monotone points, PDO / integer scaling, out-of-time validation, patient lines, route."""
import json
import math
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import scorecard_risk as S, engine_suite, decision_context as D, analysis_tools

AS_OF = '2030-05-27'


def build(path, n=400, seed=3):
    rng = random.Random(seed)
    db = sqlite3.connect(Path(path) / 'semantic.sqlite3')
    db.executescript('''
    CREATE TABLE patient(patient_id TEXT, birth_date TEXT, sex_reported TEXT, line_of_business TEXT);
    CREATE TABLE enrollment_span(patient_id TEXT, dual_status TEXT);
    CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, readmitted_30d INTEGER);
    CREATE TABLE encounter(patient_id TEXT, event_date TEXT, type TEXT);
    CREATE TABLE adt_visit(patient_id TEXT, admit_date TEXT, patient_class TEXT);
    CREATE TABLE condition(patient_id TEXT, icd10 TEXT, icd10_category TEXT, chronic INTEGER, available_date TEXT, recorded_date TEXT, event_date TEXT);
    CREATE TABLE fill(patient_id TEXT, fill_date TEXT, drug_class TEXT, drug_name TEXT);
    CREATE TABLE social_need(patient_id TEXT, event_date TEXT, positive INTEGER);
    CREATE TABLE death(patient_id TEXT, death_date TEXT);
    ''')
    for i in range(n):
        pid = f'P-{1000 + i}'
        hf = i % 4 == 0
        old = i % 3 == 0
        db.execute('INSERT INTO patient VALUES(?,?,?,?)', (pid, '1945-03-01' if old else '1985-03-01', 'F' if i % 2 else 'M', 'medicare' if old else 'commercial'))
        for y in (2027, 2028, 2029, 2030):
            db.execute('INSERT INTO encounter VALUES(?,?,?)', (pid, f'{y}-01-10', 'primary_care'))
        if hf: db.execute("INSERT INTO condition VALUES(?,?,?,?,?,?,?)", (pid, 'I50.9', 'I50', 1, '2026-06-01', '2026-06-01', '2026-06-01'))
        # admissions: heart failure drives risk in each follow-up year
        for y in (2028, 2029):
            if rng.random() < (0.45 if hf else 0.05) + (0.1 if old else 0):
                db.execute("INSERT INTO stay VALUES(?,?,?,?,?)", (pid, f'{y}-09-01', f'{y}-09-04', 'inpatient', int(rng.random() < 0.3)))
        if i % 10 == 0: db.execute("INSERT INTO stay VALUES(?,?,?,?,?)", (pid, '2030-03-01', '2030-03-03', 'observation', 0))
    db.execute("INSERT INTO condition VALUES('P-1001','I50.1','I50',1,'2031-01-01','2031-01-01','2026-01-01')")  # known after the as-of date: excluded
    db.execute("INSERT INTO death VALUES('P-1003','2027-01-01')")  # died before every index: never sampled
    db.commit(); db.close()


class Scorecard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        build(self.tmp.name)

    def run_(self, **p):
        return S.execute(self.tmp.name, None, {'outcome': 'admission', **p}, AS_OF)

    def test_points_and_validation(self):
        r = self.run_()
        self.assertEqual(r['status'], 'answered')
        hf = {x['bin']: x for x in r['scorecard'] if x['feature'] == 'condition_heart_failure'}
        self.assertEqual(hf['no']['points'], 0)            # reference bin
        self.assertGreater(hf['yes']['points'], 20)        # strong risk factor
        self.assertGreater(hf['yes']['woe'], 0)
        self.assertGreater(r['information_value']['condition_heart_failure'], 0.3)
        self.assertGreater(r['validation']['validation_auroc'], 0.7)
        self.assertNotIn('P-1003', [x['patient_id'] for x in r['patients']])
        self.assertEqual(r['train_n'], 399)
        top = r['patients'][0]
        self.assertEqual(top['score'], r['base_points'] + sum(x['points'] for x in top['lines']))
        # PDO: score 100 at base odds 1:19, +20 points doubles the odds
        self.assertAlmostEqual(S.predicted(100, *S.points_for(0.0, {}, S.validate({}))[2:]), 0.05, places=6)
        self.assertAlmostEqual(S.predicted(120, *S.points_for(0.0, {}, S.validate({}))[2:]) / (1 - S.predicted(120, *S.points_for(0.0, {}, S.validate({}))[2:])),
                               2 / 19, places=6)

    def test_feature_timing(self):
        data = S.Data(sqlite3.connect(Path(self.tmp.name) / 'semantic.sqlite3'), None)
        from datetime import date
        f, _ = data.features('P-1001', date(2030, 5, 27))
        self.assertEqual(f['condition_heart_failure'], 'no')   # its I50 record became available only in 2031
        f, _ = data.features('P-1000', date(2030, 5, 27))
        self.assertEqual(f['condition_heart_failure'], 'yes')

    def test_integer_scale_and_patient(self):
        r = self.run_(scale='integer', patient='P-1000')
        self.assertEqual(r['base_points'], 0)
        e = r['explained'][0]
        ans = S.answer(r, {'coverage': 'supported_observation'}, 'q')
        self.assertEqual(ans['patient_ids'], ['P-1000'])
        self.assertEqual(ans['table'][-1]['points'], e['score'])
        self.assertIn('P-1000 scores', ans['value'])

    def test_pav(self):
        self.assertEqual(S.pav([0, 2, 1, 3], [1, 1, 1, 1]), [0, 1.5, 1.5, 3])
        self.assertEqual(S.pav([0, -2, -1], [1, 1, 1], increasing=False), [0, -1.5, -1.5])

    def test_too_few_events(self):
        r = S.execute(self.tmp.name, ['P-1001', 'P-1002'], {'outcome': 'death'}, AS_OF)
        self.assertEqual(r['status'], 'partial')
        self.assertIn('Too few training events', r['assumptions'][0])

    def test_validate(self):
        for bad in ({'outcome': 'x'}, {'pdo': 0}, {'features': ['bogus']}, {'bogus': 1}, {'horizon_months': 99}):
            with self.assertRaises(ValueError): S.validate(bad)

    def test_route(self):
        g, _ = engine_suite.compile_route('Build an auditable points-based risk model for 12-month hospital admission', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['operation'], 'scorecard_risk')
        self.assertEqual(g['nodes'][0]['parameters'], {'outcome': 'admission', 'horizon_months': 12})
        g, _ = engine_suite.compile_route('Which patients have the highest ED visit risk on the scorecard?', {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters'], {'outcome': 'ed_visit', 'view': 'patients'})
        g, _ = engine_suite.compile_route("Show the scorecard points behind WHP-20BC580B73BB's readmission risk", {}, AS_OF)
        self.assertEqual(g['nodes'][0]['parameters'], {'outcome': 'readmission', 'patient': 'WHP-20BC580B73BB'})
        for t in S.TEMPLATES:
            g, _ = engine_suite.compile_route(t, {}, AS_OF)
            self.assertEqual(g['nodes'][0]['operation'], 'scorecard_risk', t)
            S.validate(g['nodes'][0]['parameters'])
        self.assertIsNone(S.route('What is our Star rating scorecard?', {}, AS_OF))
        self.assertIsNone(S.route('Which patients are at highest risk of admission?', {}, AS_OF))
        self.assertIsNone(S.route('Show the HEDIS quality scorecard', {}, AS_OF))


class EvidenceOnly(unittest.TestCase):
    def test_registered_no_fit_semantics_satisfy_the_original_plan_gate(self):
        from clinical_core.agere.clinical import literal_check as L, requirement_ledger as R, acceptance_contract as A
        question = ('What existing evidence supports predicting an inpatient admission over the next 90 days '
                    'using only inputs available at 2029-01-01? Report positive-label support. '
                    'Do not fit a model on this request.')
        graph = {'nodes': [{'operation': S.OP, 'parameters': {
            'mode': 'evidence', 'outcome': 'admission', 'origin_date': '2029-01-01', 'horizon_days': 90}}]}
        plain = L.executable_text([node['parameters'] for node in graph['nodes']])
        self.assertEqual(len(R.gaps(question, plain)), 3)
        text = L.graph_evidence_text(graph)
        self.assertEqual(R.gaps(question, text), [])
        self.assertEqual(A.evidence(graph=graph), text)
        self.assertNotIn(question, text)

    def test_registered_semantics_do_not_cover_a_wrong_outcome_date_or_window(self):
        from clinical_core.agere.clinical import literal_check as L, requirement_ledger as R
        question = ('Show inpatient positive-label evidence over the next 90 days using inputs '
                    'available at 2029-01-01. Do not fit a model.')
        parameters = {'mode': 'evidence', 'outcome': 'admission', 'origin_date': '2029-01-01', 'horizon_days': 90}
        for changed in ({'outcome': 'ed_visit'}, {'origin_date': '2028-01-01'}, {'horizon_days': 30}):
            graph = {'nodes': [{'operation': S.OP, 'parameters': {**parameters, **changed}}]}
            with self.subTest(changed=changed):
                self.assertTrue(R.gaps(question, L.graph_evidence_text(graph)))

    def test_fit_invalid_and_unregistered_modes_cannot_claim_no_fit_semantics(self):
        from clinical_core.agere.clinical import literal_check as L
        for op, parameters in ((S.OP, {'outcome': 'admission'}),
                               (S.OP, {'mode': 'evidence', 'origin_date': 'invalid', 'horizon_days': 90}),
                               ('unregistered_engine', {'mode': 'evidence'})):
            with self.subTest(operation=op, parameters=parameters):
                text = L.graph_evidence_text({'nodes': [{'operation': op, 'parameters': parameters,
                                                       'purpose': 'no fit inpatient positive labels'}]})
                self.assertNotIn('operator_semantics', text)
                self.assertNotIn('no fit inpatient positive labels', text)

    def test_validated_evidence_plan_moves_before_heavy_fallback_without_bypassing_checks(self):
        from clinical_core.agere.clinical.proposers import Cascade
        question = ('Report inpatient positive-label support over the next 90 days using inputs '
                    'available at 2029-01-01. Do not fit a model.')
        graph = {'nodes': [{'operation': S.OP, 'parameters': {
            'mode': 'evidence', 'outcome': 'admission', 'origin_date': '2029-01-01', 'horizon_days': 90}}]}
        cascade = object.__new__(Cascade)
        cascade.question = question
        cascade._ladder = SimpleNamespace(compiled=lambda: (graph, {}))
        order = ('evidence_retrieval', 'backstop_agent', 'local_compiler')
        self.assertEqual(cascade.free_first(order), ('local_compiler', 'evidence_retrieval', 'backstop_agent'))
        graph['nodes'][0]['parameters']['outcome'] = 'ed_visit'
        self.assertEqual(cascade.free_first(order), order)

    def test_incomplete_high_scoring_route_cannot_hide_complete_evidence_candidate(self):
        from clinical_core.agere.clinical import route_arbiter as R
        question = ('Report inpatient positive-label support over the next 90 days using inputs '
                    'available at 2029-01-01. Do not fit a model.')
        graph = {'nodes': [{'operation': S.OP, 'parameters': {
            'mode': 'evidence', 'outcome': 'admission', 'origin_date': '2029-01-01', 'horizon_days': 90}}]}
        wrong = {'nodes': [{'operation': 'calibration', 'parameters': {'prediction': 'all'}}]}
        choices = [{'op': 'calibration', 'source': 'native', 'plan': (wrong, {})},
                   {'op': S.OP, 'source': 'engine:' + S.OP, 'plan': (graph, {})}]
        with patch.object(R, 'candidates', return_value=choices), patch.object(R, 'decide') as decide:
            selected, info = R.arbitrate(question, {}, AS_OF)
        self.assertEqual(selected, graph)
        self.assertEqual(info['arbitration']['complete_literal_candidates'], 1)
        self.assertTrue(info['arbitration']['candidate_literal_coverage'][0]['missing'])
        decide.assert_not_called()

    def test_no_complete_candidate_retains_downstream_rejection(self):
        from clinical_core.agere.clinical import route_arbiter as R, literal_check as L, requirement_ledger
        question = 'Report inpatient positive-label support at 2029-01-01. Do not fit a model.'
        wrong = {'nodes': [{'operation': 'calibration', 'parameters': {'prediction': 'all'}}]}
        choice = {'op': 'calibration', 'source': 'native', 'plan': (wrong, {})}
        with patch.object(R, 'candidates', return_value=[choice]), patch.object(R, 'decide', return_value=(choice, {})):
            selected, info = R.arbitrate(question, {}, AS_OF)
        self.assertEqual(info['arbitration']['complete_literal_candidates'], 0)
        self.assertTrue(requirement_ledger.gaps(question, L.graph_evidence_text(selected)))

    question = ('What existing evidence supports predicting inpatient admission over the next 90 days using only inputs available at 2029-01-01? '
                'Do not fit or claim a model on this request.')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.path = Path(self.tmp.name)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript("""CREATE TABLE patient(patient_id TEXT);
              INSERT INTO patient VALUES('A'),('B'),('C'),('D'),('E'),('F');
              CREATE TABLE record(patient_id TEXT,stream TEXT,event_date TEXT,available_date TEXT);
              INSERT INTO record VALUES('A','encounters','2028-10-01','2028-10-02'),
               ('A','labs','2029-01-01T14:00:00','2029-01-01T15:00:00'),
               ('B','encounters','2028-10-01','2030-01-01'),('C','notes',NULL,'2028-12-01'),
               ('D','encounters','2028-12-01','2028-12-02'),('E','encounters','2028-12-01','2028-12-02'),
               ('F','pharmacy','2028-12-01',NULL);
              CREATE TABLE utilization_event(patient_id TEXT,kind TEXT,event_date TEXT);
              INSERT INTO utilization_event VALUES('A','inpatient','2029-02-01'),('A','inpatient','2029-04-01'),
               ('A','inpatient','2029-04-02'),('B','inpatient','2029-02-01'),('D','inpatient','2029-02-02'),
               ('F','inpatient',NULL),('A','observation','2029-02-05'),('C','observation','2029-02-01');
              CREATE TABLE death(patient_id TEXT,death_date TEXT,first_reported_date TEXT);
              INSERT INTO death VALUES('D','2028-12-20','2028-12-30'),('E','2028-12-20','2029-02-01');""")

    def params(self, **changes):
        p = {'mode': 'evidence', 'outcome': 'admission', 'origin_date': '2029-01-01', 'horizon_days': 90}
        p.update(changes); return p

    def report(self, ids=None, as_of=AS_OF, **changes):
        with patch.object(S, 'fit', side_effect=AssertionError('evidence must not fit')), \
                patch.object(S, 'Data', side_effect=AssertionError('evidence must not construct a scoring feature frame')):
            return S.execute(self.path, ids, self.params(**changes), as_of)

    def test_inpatient_target_excludes_observation_and_respects_exact_day_endpoint(self):
        r = self.report(); s = r['summary']
        self.assertEqual((s['recorded_target_event_rows'], s['recorded_positive_subjects_all_received']), (4, 3))
        self.assertEqual((s['observation_event_rows_excluded_from_admission'], s['observation_only_subjects_excluded_from_admission']), (2, 1))
        self.assertEqual(s['unknown_target_dates_all_received'], 1)
        self.assertEqual(r['rows'][0]['requested_target_end'], '2029-04-01')

    def test_both_input_clocks_and_origin_known_death_are_distinct_from_retrospective_knowledge(self):
        r = self.report(); s = r['summary']; inv = {x['source']: x for x in r['rows'][1:]}
        self.assertEqual((s['subjects_with_both_clock_inputs'], s['known_dead_by_origin_excluded'], s['origin_input_candidates']), (3, 1, 2))
        self.assertEqual(s['retrospective_preorigin_deaths_not_known_by_origin'], 1)
        self.assertEqual(s['recorded_positive_origin_candidates'], 1)
        self.assertEqual(inv['encounters']['past_event_arrived_later'], 1)
        self.assertEqual(inv['notes']['source_event_time_unknown'], 1)
        self.assertEqual(inv['pharmacy']['availability_unknown'], 1)
        self.assertEqual(inv['labs']['both_clocks_by_origin'], 1)
        self.assertFalse(s['feature_frame_origin_state_qualified'])

    def test_absent_positives_do_not_become_negative_labels_or_zero_calibration_errors(self):
        s = self.report()['summary']
        self.assertEqual(s['no_recorded_positive_origin_candidates_capture_unknown'], 1)
        self.assertFalse(s['negative_labels_established']); self.assertFalse(s['source_capture_established'])
        self.assertIsNone(s['calibration_brier']); self.assertIsNone(s['calibration_ece']); self.assertIsNone(s['model_vs_simple_baseline'])
        self.assertFalse(s['qualified_predictor_available'])
        self.assertEqual((s['model_fits'], s['model_scores_produced'], s['private_partition_or_weight_reads']), (0, 0, 0))

    def test_attachment_presence_never_certifies_a_model_or_opens_private_partition_rows(self):
        (self.path/'prediction-qualification-link.json').write_text('{"reports":[{"path":"protected/private-targets.jsonl"}]}')
        with patch.object(Path, 'read_text', side_effect=AssertionError('no pointer traversal or private data reads')):
            r = self.report()
        self.assertIsNone(r['summary']['qualified_predictor_available'])
        self.assertEqual(r['summary']['qualification_status'], 'attached_contract_unverified')

    def test_subset_and_empty_scope_preserve_observed_support_without_fabricating_candidates(self):
        r = self.report(['B']); self.assertEqual(r['summary']['recorded_target_event_rows'], 1)
        self.assertEqual(r['summary']['origin_input_candidates'], 0)
        empty = self.report([]); self.assertEqual(empty['summary']['received_subjects'], 0)
        self.assertEqual(len(empty['rows']), 1)
        self.assertFalse(empty['summary']['negative_labels_established'])

    def test_unmatured_window_keeps_requested_endpoint_and_reports_only_received_time(self):
        r = self.report(as_of='2029-03-15')
        self.assertFalse(r['summary']['calendar_window_mature'])
        self.assertEqual(r['rows'][0]['requested_target_end'], '2029-04-01')
        self.assertEqual(r['rows'][0]['observed_target_through'], '2029-03-15')
        self.assertEqual(r['summary']['recorded_target_event_rows'], 3)

    def test_no_fit_and_wrong_origin_or_horizon_fail_before_source_open_or_dispatch(self):
        for params in ({'outcome': 'admission'}, self.params(origin_date='2028-01-01'), self.params(horizon_days=30)):
            with self.subTest(params=params), D.scope(self.question, AS_OF), patch.object(S.rc, 'connect') as connect:
                with self.assertRaises(ValueError): S.execute(self.path, None, params, AS_OF)
                connect.assert_not_called()
            tools = SimpleNamespace(question=self.question)
            with self.assertRaises(ValueError): analysis_tools.execute(tools, 'scorecard_risk', params)

    def test_observed_source_inventory_has_no_model_presentation_row_limit(self):
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executemany('INSERT INTO record VALUES(?,?,?,?)', [('A', f'source_{i}', '2028-12-01', '2028-12-02') for i in range(40)])
        r = self.report()
        self.assertEqual(len(r['rows']), 45)
        self.assertTrue(all('parameters_json' in row for row in r['rows']))
        answer = S.answer(r, {'coverage': 'supported_observation', 'unmet_requirements': []}, self.question)
        self.assertEqual(answer['table'], r['rows'])
        self.assertEqual(answer['status'], 'partial')
        self.assertFalse(answer['overall']['model_fitting_performed'])

    def test_fixed_origin_readmission_without_episode_index_is_unknown_not_zero(self):
        r = self.report(outcome='readmission')
        self.assertEqual(r['target_status'], 'episode_index_contract_required_not_a_fixed_population_origin')
        self.assertIsNone(r['summary']['recorded_target_event_rows'])
        self.assertNotIn('None recorded', S.headline(r))

    def test_unattributed_records_are_not_fabricated_patient_inputs_or_labels(self):
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.execute("INSERT INTO record VALUES('not_on_roster','unassigned','2028-12-01','2028-12-02')")
            db.execute("INSERT INTO utilization_event VALUES('not_on_roster','inpatient','2029-02-01')")
        r = self.report()
        self.assertEqual(r['summary']['received_subjects'], 6)
        self.assertEqual(r['summary']['origin_input_candidates'], 2)
        self.assertEqual(r['summary']['recorded_target_event_rows'], 4)
        self.assertNotIn('unassigned', {row['source'] for row in r['rows'][1:]})

    def test_existing_native_route_extracts_literal_evidence_contract_without_fitting(self):
        g, info = engine_suite.compile_route(self.question, {}, AS_OF)
        self.assertEqual(g['nodes'][-1]['operation'], 'scorecard_risk')
        self.assertEqual(g['nodes'][-1]['parameters'], self.params())
        for outcome, wording in [('ed_visit', 'an emergency visit'), ('death', 'death')]:
            q = self.question.replace('inpatient admission', wording)
            routed = S.route(q, {}, AS_OF)
            self.assertEqual(routed[0]['nodes'][0]['parameters'], self.params(outcome=outcome))

    def test_ambiguous_or_incomplete_no_fit_request_never_falls_through_to_fit(self):
        for q in (self.question.replace('next 90 days', 'next 90 days and next 30 days'),
                  self.question.replace('2029-01-01', 'an unspecified date'),
                  self.question.replace('inpatient admission', 'inpatient admission and death'),
                  'Review the points scorecard evidence without fitting. Do not fit a new scorecard.'):
            self.assertIsNone(S.route(q, {}, AS_OF), q)


if __name__ == '__main__':
    unittest.main()
