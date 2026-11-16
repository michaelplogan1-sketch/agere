"""Time-to-event operator: estimators against worked examples, censoring rules on fixtures, compiler routing."""
import json, math, sqlite3, tempfile, unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np

from clinical_core.agere.clinical import analysis_graph, local_compiler, semantic_layer, source_profile, survival
from test_aggregate_explain import AS_OF, build, pcp, visit

# Freireich et al. 6-MP leukaemia remission data (Gehan 1965); '+' = censored. Reference values: Kalbfleisch & Prentice,
# Collett "Modelling Survival Data", R survival::survfit(conf.type='log-log') and coxph(ties='efron').
SIX_MP = [(6, 1), (6, 1), (6, 1), (6, 0), (7, 1), (9, 0), (10, 1), (10, 0), (11, 0), (13, 1), (16, 1), (17, 0), (19, 0), (20, 0),
          (22, 1), (23, 1), (25, 0), (32, 0), (32, 0), (34, 0), (35, 0)]
PLACEBO = [1, 1, 2, 2, 3, 4, 4, 5, 5, 8, 8, 8, 8, 11, 11, 12, 12, 15, 17, 22, 23]


class Estimators(unittest.TestCase):
    def test_km_hand_example(self):
        # times 1, 2, 2+, 3+, 4: S = 4/5 at 1, 4/5 * 3/4 = 0.6 at 2, then 0 at 4 (one at risk, one event)
        km = survival.kaplan_meier([1, 2, 2, 3, 4], [1, 1, 0, 0, 1])
        by = {r['time']: r for r in km}
        self.assertAlmostEqual(by[1]['survival'], 0.8)
        self.assertAlmostEqual(by[2]['survival'], 0.6)
        self.assertEqual((by[2]['at_risk'], by[2]['events'], by[2]['censored']), (4, 1, 1))  # events precede censoring at ties
        self.assertAlmostEqual(by[2]['variance'], 0.36 * (1 / 20 + 1 / 12))  # Greenwood: S^2 * sum d/(n(n-d))
        self.assertEqual(by[4]['survival'], 0.0)
        self.assertAlmostEqual(survival.km_at(km, 3)[0], 0.6)
        self.assertEqual(survival.median_time(km), 4.0)

    def test_km_matches_published_6mp_curve(self):
        km = {r['time']: r for r in survival.kaplan_meier(*zip(*SIX_MP)) if r['events']}
        for t, s, se in [(6, 0.857, 0.076), (7, 0.807, 0.087), (10, 0.753, 0.096), (13, 0.690, 0.107),
                         (16, 0.627, 0.114), (22, 0.538, 0.128), (23, 0.448, 0.135)]:
            self.assertAlmostEqual(km[t]['survival'], s, places=3)
            self.assertAlmostEqual(math.sqrt(km[t]['variance']), se, places=3)
        self.assertAlmostEqual(km[6]['ci_low'], 0.620, places=3)   # log(-log) interval, R conf.type='log-log'
        self.assertAlmostEqual(km[6]['ci_high'], 0.952, places=3)
        self.assertAlmostEqual(km[23]['ci_low'], 0.188, places=3)
        self.assertAlmostEqual(km[23]['ci_high'], 0.680, places=3)

    def test_logrank_hand_example(self):
        # A: events at 1, 2; B: events at 3, 4. O_A = 2, E_A = 1/2 + 1/3, V = 1/4 + 2/9 -> chi2 = (7/6)^2 / (17/36)
        r = survival.logrank([1, 2, 3, 4], [1, 1, 1, 1], ['A', 'A', 'B', 'B'])
        self.assertAlmostEqual(r['statistic'], (7 / 6) ** 2 / (17 / 36), places=4)
        self.assertEqual(r['df'], 1)
        self.assertEqual(r['observed'], {'A': 2, 'B': 2})
        self.assertAlmostEqual(r['expected']['A'], 5 / 6, places=3)

    def test_logrank_and_cox_match_published_6mp_comparison(self):
        times = [t for t, _ in SIX_MP] + PLACEBO
        events = [e for _, e in SIX_MP] + [1] * len(PLACEBO)
        groups = ['6mp'] * len(SIX_MP) + ['placebo'] * len(PLACEBO)
        lr = survival.logrank(times, events, groups)
        self.assertAlmostEqual(lr['statistic'], 16.79, places=2)
        self.assertLess(lr['p_value'], 5e-5)
        fit = survival.cox(times, events, np.array([[g == 'placebo'] for g in groups], float), ['placebo'])
        c = fit['coefficients'][0]
        self.assertTrue(fit['converged'])
        self.assertAlmostEqual(c['coef'], 1.572, places=3)   # coxph(ties='efron'): 1.572, se 0.412
        self.assertAlmostEqual(c['se'], 0.412, places=3)
        self.assertAlmostEqual(c['hazard_ratio'], 4.817, places=2)

    def test_cox_separation_is_reported_not_estimated(self):
        # every event in the exposed group: the likelihood has no finite maximum
        fit = survival.cox([1, 2, 3, 4, 5, 6], [1, 1, 1, 0, 0, 0], np.array([[1], [1], [1], [0], [0], [0]], float), ['x'])
        self.assertFalse(fit['coefficients'][0]['estimable'])
        self.assertIsNone(fit['coefficients'][0]['hazard_ratio'])

    def test_planner_schema_payload_validates(self):
        from clinical_core.agere.clinical import graph_contracts
        from test_graph_contracts import catalog
        schema = graph_contracts.parameter_schema('survival', catalog())
        self.assertIn('survival', graph_contracts.TYPED)
        self.assertEqual(set(schema['required']), set(schema['properties']))
        # a schema-shaped payload carries every key, with empty values for the unused ones
        full = {'index': {'type': 'discharge', 'concept': '', 'date': '', 'window': {'start': '', 'end': ''}},
                'event': {'type': 'readmission', 'concept': '', 'op': '', 'value': 0}, 'horizon_days': 30, 'time_points': [7, 30],
                'group_by': 'sex', 'group_concept': '', 'cox': False, 'covariates': [], 'top': 10}
        p = survival.validate(full)
        self.assertEqual((p['index'], p['event'], p['time_points']), ({'type': 'discharge'}, {'type': 'readmission'}, [7, 30]))

    def test_validation(self):
        with self.assertRaises(ValueError):
            survival.validate({'index': {'type': 'date', 'date': '2028-01-01'}, 'event': {'type': 'readmission'}})
        with self.assertRaises(ValueError):
            survival.validate({'index': {'type': 'discharge'}, 'event': {'type': 'measurement', 'concept': 'egfr', 'op': 'lt'}})
        p = survival.validate({'index': {'type': 'discharge'}, 'event': {'type': 'readmission'}})
        self.assertEqual((p['horizon_days'], p['time_points']), (90, [7, 14, 30, 60, 90]))


def egfr(pid, day, value):
    return (pid, 'labs', {'test': 'eGFR', 'value': value, 'unit': 'mL/min/1.73m2', 'event_date': day})


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patients = {f'p{i}': {'age_reported': 70 if i % 2 else 40, 'sex_reported': 'F' if i < 4 else 'M'} for i in range(8)}
        recs = [pcp(f'p{i}', 'drA', 'PR2' if i % 2 else 'PR1') for i in range(8)]
        recs += [(f'p{i}', 'coverage', {'coverage_type': 'medicare' if i % 2 else 'commercial', 'event_date': '2026-01-01'}) for i in range(8)]
        # stays: p0 discharged 01-05, readmitted 01-20 (day 15); the readmission stay (discharged 01-23) is followed by nothing.
        recs += [visit('p0', 'inpatient', '2028-01-01', 'H1', '2028-01-05'), visit('p0', 'inpatient', '2028-01-20', 'H1', '2028-01-23')]
        # p1 discharged 03-02, clinic follow-up on 03-09 (day 7) and an ED visit on 04-01 (day 30)
        recs += [visit('p1', 'observation', '2028-03-01', 'H2', '2028-03-02'), visit('p1', 'Office Visit', '2028-03-09', 'PR2'),
                 visit('p1', 'emergency', '2028-04-01')]
        # p2: stay with no received discharge time (excluded, never 0 days); p3: discharged 4 days before the snapshot
        recs += [visit('p2', 'inpatient', '2028-05-01'), visit('p3', 'inpatient', '2029-08-08', 'H1', '2029-08-11')]
        # CKD cohort entry and eGFR: p4 crosses below 30 on day 100; p5 never crosses (censored at last result, day 200);
        # p6 is already below 30 at entry (excluded); p7 has no result after entry (excluded).
        for pid in ('p4', 'p5', 'p6', 'p7'):
            recs.append((pid, 'conditions', {'description': 'Chronic kidney disease stage 3', 'event_date': '2028-01-01'}))
        recs += [egfr('p4', '2028-01-01', 40), egfr('p4', '2028-04-10', 28), egfr('p5', '2028-01-01', 45), egfr('p5', '2028-07-19', 41),
                 egfr('p6', '2027-12-01', 25), egfr('p6', '2028-03-01', 24), egfr('p7', '2027-06-01', 50)]
        recs += [('p1', 'adt', {'event': 'emergency_registration', 'event_date': '2028-04-01'})]
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)
        cls.ids = sorted(patients)

    def run_(self, **p):
        return survival.execute(self.path, self.ids, p)

    def test_readmission_index_stays_and_censoring(self):
        r = self.run_(index={'type': 'discharge'}, event={'type': 'readmission'})
        self.assertEqual(r['excluded'], {'stay_without_received_discharge': 1})
        self.assertEqual(r['units'], 4)  # p0 x2, p1, p3 (p2 has no discharge)
        self.assertEqual(r['events'], 1)
        row30 = next(x for x in r['table'] if x['day'] == 30)
        self.assertAlmostEqual(row30['cumulative_incidence'], 1 / 3, places=4)  # p3 censored at day 4: 1 event among 3 at risk at day 15
        self.assertEqual(r['groups'][0]['censoring'], {'horizon': 2, 'snapshot': 1})  # p3 censored 4 days in

    def test_followup_after_discharge(self):
        r = self.run_(index={'type': 'discharge'}, event={'type': 'followup'}, group_by='practice')
        groups = {g['group']: g for g in r['groups']}
        self.assertEqual(groups['PR2']['events'], 1)
        self.assertEqual(groups['PR2']['median_days'], 7.0)
        self.assertEqual(groups['PR1']['censoring'].get('readmitted_before_follow_up'), 1)  # p0's first stay
        self.assertIsNotNone(r['logrank'])

    def test_ed_after_fixed_date(self):
        r = self.run_(index={'type': 'date', 'date': '2028-03-02'}, event={'type': 'encounter', 'concept': 'emergency_visit'})
        self.assertEqual((r['units'], r['events']), (8, 1))
        self.assertIn(30, [x['day'] for x in r['table']])

    def test_lab_threshold_from_cohort_entry(self):
        r = self.run_(index={'type': 'condition_first_record', 'concept': 'chronic_kidney_disease'},
                      event={'type': 'measurement', 'concept': 'egfr', 'op': 'lt', 'value': 30})
        self.assertEqual(r['excluded'], {'no_index_condition_record': 4, 'already_past_threshold_at_time_zero': 1, 'no_result_after_time_zero': 1})
        self.assertEqual((r['units'], r['events']), (2, 1))
        self.assertEqual(r['groups'][0]['censoring'], {'last_result': 1})
        self.assertEqual(r['groups'][0]['median_days'], 100.0)
        self.assertIn('censored at their last result', r['method'])

    def test_answer_table_headline_and_method(self):
        params = {'index': {'type': 'discharge'}, 'event': {'type': 'readmission'}, 'group_by': 'coverage_type'}
        plan = {'interpretation': 'readmission curve by payer', 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}}, 'purpose': 'all'},
            {'id': 'survival', 'operation': 'survival', 'inputs': ['population'], 'parameters': params, 'purpose': 'curve'}]}
        plan = analysis_graph.validate(plan, AS_OF)
        report = survival.execute(self.path, self.ids, plan['nodes'][-1]['parameters'])
        answer = analysis_graph.build_answer(plan, [{'node': plan['nodes'][0], 'result': {}}, {'node': plan['nodes'][1], 'result': report}], {}, 'q')
        self.assertEqual((answer['status'], answer['answer_type']), ('answered', 'table'))
        self.assertEqual(set(answer['table'][0]), {'group', 'day', 'at_risk', 'events_by_day', 'survival', 'cumulative_incidence',
                                                                 'ci95_low', 'ci95_high', 'small_group', 'support'})
        self.assertIn('Greenwood', answer['method']); self.assertIn('Censoring', answer['method'])
        self.assertTrue(answer['table'][0]['support']['supported'])
        text = analysis_graph.headline(answer)
        self.assertTrue(text.startswith('Kaplan-Meier cumulative incidence of readmission by day 30 after discharge'))
        self.assertIn('Log-rank', text)
        json.dumps(answer)

    def test_cox_in_operator(self):
        r = self.run_(index={'type': 'date', 'date': '2028-01-01'}, event={'type': 'encounter', 'concept': 'emergency_visit'}, cox=True)
        self.assertIn('Fewer than 5 events', r['notes'][0])


class Compiler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        recs = [visit('p0', 'emergency', '2028-01-01'), ('p0', 'adt', {'event': 'emergency_registration', 'event_date': '2028-01-01'}),
                ('p0', 'coverage', {'coverage_type': 'medicare', 'event_date': '2026-01-01'})]
        cls.profile = source_profile.load(build(recs, {'p0': {'age_reported': 70, 'sex_reported': 'F'}}))

    def compile(self, q):
        return local_compiler.compile(q, self.profile, AS_OF)

    def last(self, q):
        g, info = self.compile(q)
        self.assertEqual(info['route'], 'local_compiler', info.get('reason'))
        self.assertEqual(g['nodes'][-1]['operation'], 'survival')
        return g['nodes'][0]['parameters']['specification'], g['nodes'][-1]['parameters']

    def test_readmission_curve_by_payer(self):
        spec, p = self.last('Readmission curve for heart failure discharges by payer')
        self.assertEqual([a['concept'] for a in spec['all']], ['heart_failure'])
        self.assertEqual(p, {'index': {'type': 'discharge'}, 'event': {'type': 'readmission'}, 'group_by': 'coverage_type'})

    def test_ed_after_diagnosis(self):
        spec, p = self.last('Time to first ED visit after diagnosis for diabetics')
        self.assertEqual(p['index'], {'type': 'condition_first_record', 'concept': 'diabetes'})
        self.assertEqual(p['event'], {'type': 'encounter', 'concept': 'emergency_visit'})

    def test_followup_by_practice(self):
        spec, p = self.last('How long until discharged patients get a follow-up visit, by practice?')
        self.assertEqual(spec, {'all': [], 'any': [], 'exclude': []})
        self.assertEqual((p['event'], p['group_by']), ({'type': 'followup'}, 'practice'))

    def test_lab_threshold(self):
        spec, p = self.last('Kaplan-Meier for eGFR dropping below 30 among CKD patients')
        self.assertEqual(p['index'], {'type': 'condition_first_record', 'concept': 'chronic_kidney_disease'})
        self.assertEqual(p['event'], {'type': 'measurement', 'concept': 'egfr', 'op': 'lt', 'value': 30.0})

    def test_day_marks_period_condition_groups_and_cox(self):
        _, p = self.last('30/60/90-day readmission curve for discharges in 2028 by age band')
        self.assertEqual((p['time_points'], p['horizon_days'], p['group_by']), ([30, 60, 90], 90, 'age_band'))
        self.assertEqual(p['index']['window'], {'start': '2028-01-01', 'end': '2028-12-31'})
        _, p = self.last('Kaplan-Meier readmission curve for heart failure patients with vs without diabetes')
        self.assertEqual((p['group_by'], p['group_concept']), ('condition', 'diabetes'))
        _, p = self.last('Cox model for time to first ED visit among diabetics adjusted for age, sex and payer')
        self.assertEqual(p['covariates'], ['age_band', 'sex', 'coverage_type'])
        spec, p = self.last('Time to first ED visit after discharge for medicare patients by sex')
        # "Medicare" = medicare, dual or Medigap coverage (decision context, Mike 2026-10-23): one OR group
        self.assertEqual(sorted(a['conditions'][0]['value'] for a in spec['any']), ['dual', 'medicare', 'medigap'])

    def test_declines_rather_than_guesses(self):
        for q, route in [('Time to readmission or ED visit after discharge', 'fallback'),          # two events
                         ('Time to first ED visit for diabetics with hypertension', 'fallback'),    # no single cohort entry
                         ('Kaplan-Meier curve for heart failure patients by month', 'fallback'),   # no event named
                         ('Survival curve for heart failure patients', 'local_unsupported'),       # mortality is not received
                         ('How long until patients die after a heart failure diagnosis?', 'local_unsupported')]:
            g, info = self.compile(q)
            self.assertEqual(info['route'], route, q)
            self.assertTrue(g is None or not g['nodes'], q)

    def test_non_survival_questions_unchanged(self):
        g, info = self.compile('How many patients had an ED visit in 2028?')
        self.assertNotIn('survival', [n['operation'] for n in (g or {}).get('nodes', [])])
        g, info = self.compile('What is the 30-day readmission rate by payer?')
        self.assertEqual(g['nodes'][-1]['operation'], 'aggregate')


STACK = Path(__file__).resolve().parent / 'artifacts' / 'agere-healthcare-v123' / 'stack'


@unittest.skipUnless((STACK / 'semantic.sqlite3').exists(), 'real synthetic release not present')
class RealRelease(unittest.TestCase):
    def test_follow_up_curve_runs(self):
        import sqlite3
        original = semantic_layer.ensure
        semantic_layer.ensure = lambda path: STACK / 'semantic.sqlite3'  # read the release as built; never rebuild it from a test
        try:
            r = survival.execute(STACK, None, {'index': {'type': 'discharge'}, 'event': {'type': 'followup'}})
        finally:
            semantic_layer.ensure = original
        self.assertGreater(r['units'], 0)
        self.assertTrue(all(0 <= row['cumulative_incidence'] <= 1 for row in r['table']))


class ReceivedFollowup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name); self.target = self.path/'semantic.sqlite3'
        (self.path/'run.json').write_text(json.dumps({'snapshot': {'as_of': '2028-01-31'}}))
        db = sqlite3.connect(self.target)
        db.executescript('CREATE TABLE patient(patient_id TEXT); CREATE TABLE death(patient_id TEXT,death_date TEXT,sources_disagree INTEGER);'
                         'CREATE TABLE utilization_event(patient_id TEXT,kind TEXT,event_date TEXT,end_date TEXT,discharge_known INTEGER,record_id TEXT,sources TEXT);'
                         'CREATE TABLE stay(patient_id TEXT,admit_date TEXT,discharge_date TEXT,discharge_known INTEGER);'
                         'CREATE TABLE enrollment_span(patient_id TEXT,coverage_start TEXT,coverage_end TEXT,open_ended INTEGER);')
        pids = ['normal', 'prior', 'death', 'gap', 'adjacent', 'uncovered', 'tie', 'same_tie', 'missing', 'death_tie', 'end_tie', 'undated']
        for pid in pids:
            db.execute('INSERT INTO patient VALUES(?)', (pid,))
            end = None if pid == 'missing' else '2028-01-03'
            db.execute('INSERT INTO utilization_event VALUES(?,?,?,?,?,NULL,?)', (pid, 'inpatient', '2028-01-01', end, int(end is not None), 'claims,adt'))
            db.execute('INSERT INTO utilization_event VALUES(?,?,?,?,1,NULL,?)', (pid, 'inpatient', '2028-01-08', '2028-01-09', 'claims'))
            if pid != 'uncovered': db.execute('INSERT INTO enrollment_span VALUES(?,?,NULL,1)', (pid, '2027-01-01'))
        db.execute("INSERT INTO utilization_event VALUES('prior','inpatient','2027-12-01','2027-12-03',1,NULL,'claims')")
        db.execute("INSERT INTO utilization_event VALUES('tie','inpatient','2028-01-01','2028-01-04',1,NULL,'adt')")
        db.execute("INSERT INTO utilization_event VALUES('same_tie','inpatient','2028-01-01','2028-01-03',1,NULL,'adt')")
        db.execute("INSERT INTO utilization_event VALUES('undated','inpatient',NULL,'2027-12-03',1,NULL,'claims')")
        db.execute("INSERT INTO death VALUES('death','2028-01-05',0),('death_tie','2028-01-08',1)")
        db.execute("DELETE FROM enrollment_span WHERE patient_id IN ('gap','adjacent','end_tie')")
        db.execute("INSERT INTO enrollment_span VALUES('gap','2028-01-01','2028-01-06',0),('gap','2028-01-10',NULL,1),"
                   "('adjacent','2028-01-01','2028-01-05',0),('adjacent','2028-01-06','2028-01-20',0),"
                   "('uncovered','2028-01-10',NULL,1),('end_tie','2028-01-01','2028-01-08',0)")
        db.commit(); db.close()

    def params(self, **changes):
        p = {'index': {'type': 'discharge', 'selection': 'first_recorded', 'window_basis': 'admission', 'stay_kind': 'inpatient',
                       'window': {'start': '2028-01-01', 'end': '2028-12-31'}},
             'event': {'type': 'readmission'}, 'output': 'observed_units', 'coverage': 'contiguous_enrollment'}
        p.update(changes); return p

    def report(self, **changes):
        with patch.object(survival.semantic_layer, 'ensure', return_value=self.target):
            return survival.execute(self.path, None, self.params(**changes))

    def test_first_across_received_history_and_first_in_window_are_distinct(self):
        report = self.report(); by = {r['patient_id']: r for r in report['table']}
        self.assertNotIn('prior', by)
        ix = self.params()['index']; ix['selection'] = 'first_in_window'
        alternate = self.report(index=ix)
        self.assertIn('prior', {r['patient_id'] for r in alternate['table']})
        self.assertEqual(len(alternate['table']), len(report['table'])+1)

    def test_null_ids_do_not_remove_next_admission_and_risk_time_stops_at_event(self):
        row = next(r for r in self.report()['table'] if r['patient_id'] == 'normal')
        self.assertEqual(row['next_recorded_admission_date'], '2028-01-08')
        self.assertEqual((row['event_observed'], row['time_at_risk_days']), (1, 5))
        self.assertEqual(row['followup_boundary_days'], 28)

    def test_received_death_stops_risk_but_keeps_the_later_source_event(self):
        r = self.report(); row = next(r for r in r['table'] if r['patient_id'] == 'death')
        self.assertEqual((row['event_observed'], row['time_at_risk_days'], row['censor_reason']), (0, 2, 'death'))
        self.assertEqual(row['next_recorded_admission_date'], '2028-01-08')
        self.assertEqual(r['death_source']['received_rows'], 2)
        self.assertNotIn('No death records', r['method'])

    def test_later_open_span_does_not_bridge_an_enrollment_gap(self):
        row = next(r for r in self.report()['table'] if r['patient_id'] == 'gap')
        self.assertEqual((row['event_observed'], row['time_at_risk_days'], row['censor_reason']), (0, 3, 'enrollment_boundary'))
        self.assertEqual(row['contiguous_coverage_end'], '2028-01-06')

    def test_adjacent_spans_merge_and_inclusive_enrollment_end_keeps_that_day_event(self):
        by = {r['patient_id']: r for r in self.report()['table']}
        self.assertEqual(by['adjacent']['contiguous_coverage_end'], '2028-01-20')
        self.assertEqual(by['adjacent']['event_observed'], 1)
        self.assertEqual(by['end_tie']['event_observed'], 1)

    def test_missing_index_coverage_is_unknown_and_lifetime_open_does_not_establish_it(self):
        row = next(r for r in self.report()['table'] if r['patient_id'] == 'uncovered')
        self.assertEqual(row['status'], 'coverage_at_index_not_established')
        self.assertIsNone(row['time_at_risk_days']); self.assertIsNone(row['event_observed'])
        self.assertEqual(row['next_recorded_admission_date'], '2028-01-08')

    def test_origin_ties_missing_dates_and_death_day_order_remain_unresolved(self):
        by = {r['patient_id']: r for r in self.report()['table']}
        self.assertEqual(by['tie']['index_candidates'], 2)
        self.assertEqual(by['tie']['status'], 'index_discharge_unknown_or_ambiguous')
        self.assertEqual(by['same_tie']['index_candidates'], 2)
        self.assertEqual(by['same_tie']['time_at_risk_days'], 5)
        self.assertEqual(by['missing']['status'], 'index_discharge_unknown_or_ambiguous')
        self.assertEqual(by['undated']['status'], 'undated_admission_prevents_first_recorded_order')
        self.assertEqual(by['death_tie']['status'], 'event_censor_same_day_order_unknown')
        self.assertIsNone(by['death_tie']['event_observed'])

    def test_observed_output_keeps_all_accounts_without_models_or_an_unasked_horizon(self):
        r = self.report()
        self.assertIsNone(r['parameters']['horizon_days'])
        self.assertEqual(r['selected_index_accounts'], 11)
        self.assertEqual(len(r['table']), 11)
        self.assertEqual(r['curves'], {}); self.assertIsNone(r['cox']); self.assertIsNone(r['conformal_lower_bounds'])
        self.assertEqual(r['status'], 'partial')
        self.assertTrue(all('parameters_json' in row for row in r['table']))
        self.assertIn('no modeled risk', survival.summary(r))
        for changes in ({'cox': True}, {'lower_bound': True}, {'coverage': 'invented'}):
            with self.assertRaises(ValueError): survival.validate(self.params(**changes))

    def test_unknown_only_followup_remains_a_table_and_retains_its_patient_scope(self):
        with patch.object(survival.semantic_layer, 'ensure', return_value=self.target):
            report = survival.execute(self.path, ['tie', 'uncovered'], self.params())
        self.assertEqual(report['units'], 0)
        self.assertEqual(len(report['table']), 2)
        self.assertTrue(all(r['event_observed'] is None for r in report['table']))
        node = {'operation': 'survival'}
        plan = {'nodes': [node], 'coverage': 'supported_observation', 'unmet_requirements': []}
        answer = analysis_graph.build_answer(plan, [{'node': node, 'result': report}], {}, 'observed followup')
        self.assertEqual((answer['status'], answer['answer_type'], answer['count']), ('partial', 'table', 2))
        self.assertEqual(answer['patient_ids'], ['tie', 'uncovered'])

    def test_null_end_without_asserted_open_flag_does_not_invent_coverage(self):
        with sqlite3.connect(self.target) as db:
            db.execute("UPDATE enrollment_span SET coverage_end=NULL,open_ended=0 WHERE patient_id='normal'")
        row = next(r for r in self.report()['table'] if r['patient_id'] == 'normal')
        self.assertEqual(row['status'], 'coverage_at_index_not_established')
        self.assertIsNone(row['event_observed'])

    def test_unknown_origin_still_exposes_received_death_without_timing_it(self):
        with sqlite3.connect(self.target) as db:
            db.execute("INSERT INTO death VALUES('tie','2028-01-20',0)")
        row = next(r for r in self.report()['table'] if r['patient_id'] == 'tie')
        self.assertEqual(row['death_date'], '2028-01-20')
        self.assertEqual(row['status'], 'index_discharge_unknown_or_ambiguous')
        self.assertIsNone(row['time_at_risk_days'])

    def test_death_censored_KM_is_not_labeled_absolute_competing_risk_incidence(self):
        params = self.params(output='estimates', horizon_days=30, time_points=[3, 5], lower_bound=True)
        ids = ['normal', 'death', 'gap']
        with patch.object(survival.semantic_layer, 'ensure', return_value=self.target), \
                patch.object(survival, 'attributes', return_value={pid: {} for pid in ids}):
            report = survival.execute(self.path, ids, params)
        self.assertEqual(report['estimand'], 'net_failure_under_censoring')
        self.assertEqual(report['conformal_lower_bounds']['status'], 'insufficient')
        self.assertTrue(all(r['cumulative_incidence'] is None and 'net_failure_probability' in r for r in report['table']))
        self.assertIn('net failure probability under censoring', survival.summary(report))
        self.assertIn('not an absolute competing-risk', report['method'])


if __name__ == '__main__':
    unittest.main()
