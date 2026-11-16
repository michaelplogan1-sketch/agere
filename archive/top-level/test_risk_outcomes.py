"""Risk-standardized outcomes: O/E recovery in simulation (case mix vs true unit effects), fixture readmissions / ED revisits / LOS,
mortality refusal, routing (profile keeps fair comparison, survival keeps time-to-event), wiring, real-v20 smoke."""
import json, math, shutil, sqlite3, tempfile, unittest
from pathlib import Path

import numpy as np
from scipy.stats import poisson

from clinical_core.agere.clinical import (analysis_graph, capability_registry, graph_contracts, graph_planner, local_compiler,
                                          risk_outcomes as ro)
from clinical_core.agere.clinical.analysis_graph import build_answer, validate
from test_loop_closure import routing_profile, REAL_V20

ROOT = Path(__file__).resolve().parent
AS_OF = '2030-05-27'


def simulate(seed=11, n_per=1500):
    """8 hospitals; H0 truly worse (+0.7 log-odds), H7 truly better (-0.7); H3 has much sicker patients but no unit effect."""
    rng = np.random.default_rng(seed)
    units, rows, y = [], [], []
    for h in range(8):
        for _ in range(n_per):
            band = rng.choice(['18-44', '45-64', '65-74', '75+'], p=[.2, .3, .3, .2])
            sick = rng.random() < (0.7 if h == 3 else 0.25)
            conds = {'heart_failure'} if sick else set()
            prior = int(rng.random() < (0.5 if h == 3 else 0.15))
            eta = -2.4 + {'18-44': -0.3, '45-64': 0.0, '65-74': 0.2, '75+': 0.5}[band] + 1.1 * sick + 0.6 * prior + {0: 0.7, 7: -0.7}.get(h, 0.0)
            units.append(f'H{h}'); y.append(float(rng.random() < 1 / (1 + math.exp(-eta))))
            rows.append({'age_band': band, 'sex': 'F' if rng.random() < .5 else 'M', 'conditions': conds, 'prior': prior, 'observation': False})
    return units, rows, y


class Models(unittest.TestCase):
    def test_logistic_recovers_coefficients(self):
        rng = np.random.default_rng(3)
        X = np.column_stack([np.ones(30000), rng.random(30000) < .4, rng.normal(size=30000)])
        eta = -1.5 + 0.9 * X[:, 1] - 0.5 * X[:, 2]
        y = (rng.random(30000) < 1 / (1 + np.exp(-eta))).astype(float)
        beta, mu, ok = ro.fit_glm(X, y)
        self.assertTrue(ok)
        np.testing.assert_allclose(beta, [-1.5, 0.9, -0.5], atol=0.08)
        self.assertAlmostEqual(mu.sum(), y.sum(), places=4)          # unpenalised intercept: expected = observed network-wide

    def test_poisson_and_c_statistic(self):
        rng = np.random.default_rng(4)
        X = np.column_stack([np.ones(20000), rng.random(20000) < .5])
        y = rng.poisson(np.exp(1.0 + 0.4 * X[:, 1])).astype(float)
        beta, _, _ = ro.fit_glm(X, y, 'poisson')
        np.testing.assert_allclose(beta, [1.0, 0.4], atol=0.04)
        self.assertEqual(ro.c_statistic([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]), 1.0)
        self.assertEqual(ro.c_statistic([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]), 0.5)
        self.assertIsNone(ro.c_statistic([0, 0], [0.1, 0.2]))

    def test_oe_recovery_in_simulation(self):
        units, rows, y = simulate()
        X, names = ro.design(rows, 'age_sex_conditions')
        out, model = ro.standardise(units, y, X, names, draws=2000)
        by = {u['unit']: u for u in out}
        self.assertEqual(by['H0']['flag'], 'worse_than_network')
        self.assertEqual(by['H7']['flag'], 'better_than_network')
        self.assertEqual(by['H3']['flag'], 'no_different')           # sicker case mix, no unit effect: not penalised
        crude = {h: sum(yy for u, yy in zip(units, y) if u == h) / units.count(h) for h in by}
        self.assertGreater(crude['H3'], 1.4 * np.median(list(crude.values())))   # ... although its crude rate is far higher
        for h in ('H1', 'H2', 'H3', 'H4', 'H5', 'H6'):
            self.assertLess(abs(by[h]['srr'] - 1), 0.2, h)
        self.assertGreater(by['H0']['srr'], 1.4); self.assertLess(by['H7']['srr'], 0.7)
        self.assertEqual(out[0]['unit'], 'H0')
        lo, hi = by['H0']['rsr_ci95']
        self.assertTrue(lo <= by['H0']['rsr'] <= hi)
        self.assertAlmostEqual(by['H0']['rsr'], by['H0']['srr'] * model['network_rate'], places=4)
        self.assertGreater(model['c_statistic'], 0.6)
        self.assertAlmostEqual(model['calibration_in_the_large'], 1.0, places=3)
        X0, _ = ro.design(rows, 'none')                               # without case mix H3 looks worse
        crude_out, _ = ro.standardise(units, y, X0, ['intercept'], draws=2000)
        self.assertEqual({u['unit']: u['flag'] for u in crude_out}['H3'], 'worse_than_network')

    def test_too_few_events_are_not_estimated(self):
        out, model = ro.standardise(['A'] * 50 + ['B'] * 50, [1.0] * 3 + [0.0] * 97, np.ones((100, 1)), ['intercept'])
        self.assertFalse(model['unit_effects_estimable'])
        self.assertEqual({u['flag'] for u in out}, {'insufficient_events'})
        self.assertIsNone(out[0]['srr'])

    def test_funnel_limits(self):
        units = [{'unit': 'A', 'expected': 20.0, 'observed': 40.0, 'oe_ratio': 2.0}, {'unit': 'B', 'expected': 20.0, 'observed': 21.0, 'oe_ratio': 1.05}]
        f = ro.funnel(units)
        pts = {p['unit']: p for p in f['points']}
        self.assertTrue(pts['A']['outside_998']); self.assertFalse(pts['B']['outside_95'])
        row = min(f['limits'], key=lambda r: abs(r['expected'] - 20))
        e = row['expected']
        self.assertAlmostEqual(row['upper_0.95'], round(poisson.ppf(0.975, e) / e, 4))
        self.assertTrue(all(r['lower_0.998'] <= r['lower_0.95'] <= 1 <= r['upper_0.95'] <= r['upper_0.998'] for r in f['limits']))

    def test_parameters(self):
        p = ro.validate({})
        self.assertEqual((p['outcome'], p['unit'], p['window_days'], p['adjust']), ('readmission', 'hospital', 30, 'age_sex_conditions'))
        for bad in ({'outcome': 'falls'}, {'unit': 'payer'}, {'window_days': 0}, {'include_observation': 'yes'}, {'extra': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ro.validate(bad)


DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE patient(patient_id TEXT PRIMARY KEY, age_reported INTEGER, sex_reported TEXT, pcp_clinician_id TEXT, pcp_practice_id TEXT{death});
CREATE TABLE stay(patient_id TEXT, admit_date TEXT, discharge_date TEXT, type TEXT, organization_id TEXT, readmitted_30d INTEGER,
  days_to_readmission INTEGER, discharge_known INTEGER, los_days INTEGER);
CREATE TABLE visit(encounter_id TEXT, patient_id TEXT, type TEXT, type_concept TEXT, status TEXT, event_date TEXT, organization_id TEXT,
  attending_clinician_id TEXT, principal_icd10 TEXT);
CREATE TABLE concept_patient(concept TEXT, kind TEXT, patient_id TEXT, records INTEGER, first_date TEXT, last_date TEXT);
CREATE TABLE record(record_id TEXT PRIMARY KEY, patient_id TEXT, stream TEXT, event_date TEXT, recorded_date TEXT, available_date TEXT,
  organization_id TEXT, payload TEXT);
"""


def fixture(death=False):
    """HA: 40 index stays, 12 readmitted at day 10; HB: 40 index stays, 2 readmitted. Plus one stay too recent to follow up."""
    root = Path(tempfile.mkdtemp())
    db = sqlite3.connect(root / 'semantic.sqlite3')
    db.executescript(DDL.replace('{death}', ', death_date TEXT' if death else ''))
    db.execute("INSERT INTO meta VALUES ('version','semantic-layer/13'), ('as_of', ?)", (AS_OF,))
    k = 0
    for h, n_read in (('HA', 12), ('HB', 2)):
        for i in range(40):
            pid = f'{h}{i}'
            db.execute(f"INSERT INTO patient VALUES (?,?,?,?,?{',?' if death else ''})",
                       (pid, 70, 'F' if i % 2 else 'M', 'drA' if i % 2 else 'drB', 'PR1' if i < 20 else 'PR2', *(['2029-03-20'] if death and h == 'HA' and i < 5 else [None] if death else [])))
            db.execute("INSERT INTO stay VALUES (?, '2029-03-01', '2029-03-05', 'inpatient', ?, 0, NULL, 1, 4)", (pid, h))
            if i < n_read:
                db.execute("INSERT INTO stay VALUES (?, '2029-03-15', '2029-03-18', 'inpatient', ?, 0, NULL, 1, 3)", (pid, h))
            k += 1
            # ED treat-and-release index visit; HA patients 0-9 return within 30 days
            db.execute('INSERT INTO visit VALUES (?,?,?,?,?,?,?,?,?)', (f'v{k}', pid, 'emergency', 'emergency_visit', 'completed', '2029-06-01', h, None, None))
            if h == 'HA' and i < 10:
                db.execute('INSERT INTO visit VALUES (?,?,?,?,?,?,?,?,?)', (f'w{k}', pid, 'emergency', 'emergency_visit', 'completed', '2029-06-12', h, None, None))
            if i % 3 == 0: db.execute("INSERT INTO concept_patient VALUES ('heart_failure','condition',?,1,'2020-01-01','2029-01-01')", (pid,))
    db.execute("INSERT INTO patient (patient_id, age_reported, sex_reported, pcp_clinician_id, pcp_practice_id) VALUES ('late', 70, 'F', 'drA', 'PR1')")
    db.execute("INSERT INTO stay VALUES ('late', '2030-05-10', '2030-05-12', 'inpatient', 'HA', 0, NULL, 1, 2)")   # < 30 days of follow-up
    db.commit(); db.close()
    return root


class Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()
        cls.db = sqlite3.connect(cls.path / 'semantic.sqlite3')

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        shutil.rmtree(cls.path, ignore_errors=True)

    def test_readmission_by_hospital(self):
        r = ro.run(self.db, None, {}, AS_OF)
        self.assertEqual(r['status'], 'answered')
        by = {u['unit']: u for u in r['units']}
        # index stays = first stays (80) + the readmission stays themselves (14, also discharged with full follow-up); 'late' excluded
        self.assertEqual((by['HA']['index_cases'], by['HB']['index_cases']), (52, 42))
        self.assertEqual((by['HA']['observed'], by['HB']['observed']), (12, 2))
        self.assertAlmostEqual(by['HA']['expected'] + by['HB']['expected'], 14, places=2)
        self.assertEqual(r['model']['events'], 14)
        self.assertGreater(by['HA']['srr'], by['HB']['srr'])
        self.assertTrue(r['funnel']['points'])
        self.assertTrue(r['model']['unit_effects_estimable'])          # 14 events >= MIN_EVENTS
        v, table = ro.summarize(r)
        self.assertIn('Risk-standardized all-cause inpatient readmission (30-day) across 2 hospitals', v)
        self.assertEqual(table[0]['unit'], 'HA')

    def test_ed_revisit_los_and_units(self):
        r = ro.run(self.db, None, {'outcome': 'ed_revisit'}, AS_OF)
        by = {u['unit']: u for u in r['units']}
        self.assertEqual((by['HA']['observed'], by['HB']['observed'], by['HA']['index_cases']), (10, 0, 50))
        r = ro.run(self.db, None, {'outcome': 'length_of_stay'}, AS_OF)
        self.assertEqual(r['status'], 'answered')
        self.assertEqual(r['model']['family'], 'Poisson log-linear')
        r = ro.run(self.db, None, {'unit': 'practice'}, AS_OF)
        self.assertEqual({u['unit'] for u in r['units']}, {'PR1', 'PR2'})
        r = ro.run(self.db, [f'HA{i}' for i in range(40)] + [f'HB{i}' for i in range(40)], {'window_days': 7}, AS_OF)
        self.assertEqual(r['status'], 'empty')                       # day-10 readmissions fall outside a 7-day window
        self.assertIn('No all-cause inpatient readmission events', r['reason'])

    def test_mortality_refused_without_death_records(self):
        r = ro.run(self.db, None, {'outcome': 'mortality'}, AS_OF)
        self.assertEqual(r['status'], 'unsupported')
        self.assertIn('death records', r['missing'][0])
        v, table = ro.summarize(r)
        self.assertIn('Missing input: death records', v)
        plan = validate({'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                         'nodes': [{'id': 'answer', 'operation': 'risk_outcomes', 'inputs': [], 'parameters': {'outcome': 'mortality'}, 'purpose': 'x'}]}, AS_OF)
        a = build_answer(plan, [{'result': r}], {}, 'q')
        self.assertEqual(a['status'], 'unsupported')
        self.assertTrue(any('death records' in m for m in a['missing']))

    def test_mortality_with_death_records(self):
        path = fixture(death=True)
        try:
            db = sqlite3.connect(path / 'semantic.sqlite3')
            r = ro.run(db, None, {'outcome': 'mortality'}, AS_OF)
            db.close()
        finally:
            shutil.rmtree(path, ignore_errors=True)
        self.assertEqual(r['status'], 'answered')                     # death dates present: computed, not refused
        by = {u['unit']: u for u in r['units']}
        self.assertEqual((by['HA']['observed'], by['HB']['observed']), (5, 0))
        self.assertFalse(r['model']['unit_effects_estimable'])        # 5 deaths < MIN_EVENTS: O/E only

    def test_answer_contract(self):
        r = ro.run(self.db, None, {}, AS_OF)
        plan = validate({'interpretation': 'q', 'coverage': 'supported_observation', 'unmet_requirements': [],
                         'nodes': [{'id': 'answer', 'operation': 'risk_outcomes', 'inputs': [], 'parameters': {}, 'purpose': 'x'}]}, AS_OF)
        a = build_answer(plan, [{'result': r}], {}, 'q')
        self.assertEqual((a['status'], a['answer_type'], a['count']), ('answered', 'table', 2))
        self.assertIn('funnel', a['overall']['risk_outcomes'])
        json.dumps(a)


PHRASINGS = {'What is our risk-standardized readmission rate by hospital?': ('readmission', 'hospital'),
             'RSRR by practice': ('readmission', 'practice'),
             'Observed to expected readmissions for each PCP': ('readmission', 'pcp'),
             'Excess readmission ratio for COPD and heart failure patients under HRRP': ('readmission', 'hospital'),
             'Funnel plot of 30-day readmissions across hospitals': ('readmission', 'hospital'),
             'Risk-adjusted ED revisit rates by hospital': ('ed_revisit', 'hospital'),
             'Which hospitals have case-mix adjusted readmission rates worse than the network?': ('readmission', 'hospital'),
             'O/E length of stay by facility': ('length_of_stay', 'hospital'),
             'Standardized 72-hour ED returns by hospital': ('ed_revisit', 'hospital'),
             'Risk-standardized ED visits after discharge by hospital (EDAC)': ('post_discharge_ed', 'hospital')}


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
        for q, (outcome, unit) in PHRASINGS.items():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertIsNotNone(g, info)
                node = g['nodes'][-1]
                self.assertEqual(node['operation'], 'risk_outcomes', info)
                self.assertEqual((node['parameters']['outcome'], node['parameters']['unit']), (outcome, unit))
        g, _ = self.compile('Standardized 72-hour ED returns by hospital')
        self.assertEqual(g['nodes'][-1]['parameters']['window_days'], 3)
        g, _ = self.compile('Excess readmission ratio for COPD and heart failure patients under HRRP')
        self.assertEqual({a['concept'] for a in g['nodes'][0]['parameters']['specification']['any']}, {'copd', 'heart_failure'})  # HRRP cohorts: a union
        g, _ = self.compile('What is our observed-to-expected length of stay by DRG family?')
        self.assertEqual((g['coverage'], g['nodes'][-1]['operation']), ('partial', 'risk_outcomes'))
        bare = {**self.profile, 'streams': {k: v for k, v in self.profile['streams'].items() if k != 'medical_claims'}}
        g, info = local_compiler.compile('What is our observed-to-expected length of stay by DRG family?', bare, '2029-08-15')
        self.assertIsNone(g); self.assertIn('MS-DRG', info['reason'])    # no DRG anywhere in the release
        g, info = self.compile('How large a HRRP payment reduction should we expect next fiscal year?')
        self.assertEqual(g['coverage'], 'unsupported')
        g, info = self.compile('What is the risk-standardized mortality rate by hospital?')
        self.assertEqual(g['coverage'], 'unsupported')                       # no death records in this release
        self.assertIn('death', ' '.join(g['unmet_requirements']).lower())

    def test_acceptance_file(self):
        rows = json.loads((ROOT / 'bench' / 'acceptance' / 'risk_outcomes.json').read_text())
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            with self.subTest(q=row['question']):
                g, info = self.compile(row['question'])
                self.assertIsNotNone(g, info)
                self.assertEqual(g['nodes'][-1]['operation'], 'risk_outcomes', info)

    def test_does_not_steal(self):
        qs = [c['question'] for op in ('profile', 'survival', 'market', 'pathway', 'vbc', 'calibration')
              for c in json.loads((ROOT / 'bench' / 'acceptance' / f'{op}.json').read_text())]
        qs += ['Which attending physicians have high readmission rates after adjusting for case mix and volume?',   # profile
               'Which hospitals in our system moved the most this month on readmissions and ED returns?',          # market
               'How many readmissions in the HRRP cohorts were planned versus unplanned?',                         # literal
               'Kaplan-Meier for 30-day readmission by hospital', 'Why are readmissions high at hospital 2?']
        for q in qs:
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertNotEqual(g['nodes'][-1]['operation'] if g and g.get('nodes') else None, 'risk_outcomes', info)

    def test_follow_ups_compile_back(self):
        for q in ro.follow_up():
            with self.subTest(q=q):
                g, info = self.compile(q)
                self.assertEqual(g['nodes'][-1]['operation'], 'risk_outcomes', info)


class Integration(unittest.TestCase):
    def test_registered_everywhere(self):
        op = 'risk_outcomes'
        self.assertIn(op, analysis_graph.OPERATIONS)
        self.assertIn(op, analysis_graph.NO_BENCHMARK_OPERATIONS)
        self.assertIn(op, graph_contracts.TYPED)
        self.assertEqual(graph_contracts.input_contract(op)['count'], [0, 1])
        self.assertIn(op, graph_planner.CONTRACTS)
        self.assertIn('profile', capability_registry.PRODUCT[op]['use'])
        schema = graph_contracts.parameter_schema(op, {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}})
        self.assertEqual(set(schema['properties']['outcome']['enum']), set(ro.OUTCOMES))
        self.assertTrue(set(schema['properties']) <= ro.FIELDS)
        ro.validate({k: v for k, v in graph_planner.CONTRACTS[op]['parameters'].items() if not isinstance(v, str) or '|' not in v})
        source = Path(analysis_graph.__file__).read_text()
        block = source[source.index(f"if last['operation'] == '{op}':"):]
        block = block[:block.index("\n    if last['operation']", 10)]
        self.assertTrue(block.rstrip().endswith('return answer'))
        self.assertIn('risk_outcomes.py', source[source.index('def code_identity'):source.index('def code_identity') + 2500])
        self.assertIn('profile.analyse', Path(ro.__file__).read_text())        # reuses the hierarchical machinery

    def test_router_and_console(self):
        from clinical_core.agere.clinical import operator_router
        from clinical_core.agere import ask_view
        self.assertIn('funnel', operator_router.tool('risk_outcomes')['description'])
        html = ask_view.page()
        self.assertIn('if(o.risk_outcomes)return vizFunnel(a)', html)
        self.assertIn('function funnelChart', html)


@unittest.skipUnless((REAL_V20 / 'semantic.sqlite3').exists(), 'warehouse v2.0 semantic layer not present')
class RealV20(unittest.TestCase):
    def test_smoke(self):
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)  # read-only; never rebuilt
        try:
            r = ro.run(db, None, {}, AS_OF)
            m = ro.run(db, None, {'outcome': 'mortality'}, AS_OF)
            los = ro.run(db, None, {'outcome': 'length_of_stay'}, AS_OF)
        finally:
            db.close()
        self.assertEqual(r['status'], 'answered')
        self.assertGreaterEqual(r['units_total'], 2)
        self.assertEqual(m['status'], 'unsupported')
        self.assertIn('death records', m['missing'][0])
        self.assertEqual(los['status'], 'answered')
        self.assertTrue(los['model']['unit_effects_estimable'])


if __name__ == '__main__':
    unittest.main()
