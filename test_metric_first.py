"""Metric-first hosted path (metric_router.py, metric_families.py, config/agere/metrics/families.json): rung ordering in the
economical planner, slot-schema derivation from metric definitions, slot validation, no_metric fall-through, two readings
on an ambiguous choice, low-confidence status for unverified free-form SQL, and governed metrics reproducing benchmark
reference answers on the v1.23 stack (read only). Synthetic data only; every model call is a fake; no network."""
import json, os, re, sqlite3, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('AGERE_METRIC_ENCODER', 'off')

from clinical_core.agere.clinical import age, metric_router as MR, metric_families as MF, metrics, semantic_layer, learning
from clinical_core.agere.clinical import economical_planner as planner, operator_router
from test_metric_layer import build_fixture

AS_OF = '2029-08-15'
ROOT = Path(__file__).resolve().parent
STACK = ROOT / 'artifacts/agere-healthcare-v123/stack'
REFERENCES = ROOT / 'tools/metric_first/reference_answers.json'
ORACLE = ROOT / 'tools/metric_first/oracle.json'


def fixture_stack(root):
    """A fixture semantic layer that semantic_layer.ensure accepts as current (no rebuild)."""
    db_path = root / 'semantic.sqlite3'
    build_fixture(db_path)
    db = sqlite3.connect(db_path)
    db.execute('CREATE TABLE meta(key TEXT, value TEXT)')
    db.executemany('INSERT INTO meta VALUES (?, ?)', [('version', semantic_layer.VERSION), ('source_digest', 'fixture'), ('as_of', AS_OF)])
    db.execute('ALTER TABLE claim ADD COLUMN benefit_year TEXT'); db.execute('ALTER TABLE claim ADD COLUMN denied_then_paid INTEGER')
    db.execute("UPDATE claim SET benefit_year = '2028', denied_then_paid = ever_denied")
    db.commit(); db.close()
    (root / 'run.json').write_text(json.dumps({'source_digest': 'fixture', 'as_of': AS_OF}))
    return root


class Script:
    """Fake model: a queue of tool choices; records every call (tools offered, forced tool, feedback)."""
    def __init__(self, *choices):
        self.choices, self.calls = list(choices), []

    def __call__(self, question, as_of, tools, forced=None, feedback=None):
        self.calls.append({'tools': [t['name'] for t in tools], 'forced': forced, 'feedback': feedback})
        name, args = self.choices.pop(0)
        return {'name': name, 'input': args}, {'api_usd': 0.001, 'cache_hit': False}


def choose(mid): return (MR.tool_name(mid), {})
def fill(mid, **slots): return (MR.tool_name(mid), slots)


DIABETES = [{'kind': 'condition', 'concepts': ['diabetes']}]


class SchemaTests(unittest.TestCase):
    def test_slot_schema_is_derived_from_the_metric_definition(self):
        lib = MR.routable()
        cohort = MR.input_schema(lib['patient_cohort'])
        self.assertEqual(cohort['required'], ['population'])
        self.assertEqual(cohort['properties']['population']['type'], 'array')
        atom = cohort['properties']['population']['items']['properties']
        self.assertIn('diabetes', atom['concepts']['items']['enum'])            # concept library
        self.assertIn('hba1c', atom['concept']['enum'])
        self.assertIn('statin', atom['groups']['items']['enum'])                # drug-group vocabulary
        ev = MR.input_schema(lib['encounter_volume'])['properties']
        self.assertEqual(set(ev['visit_classes']['items']['enum']), set(MF.vocabulary()['visit_classes']))
        self.assertIn('per_1000_roster', ev['measure']['enum'])
        # a templated metric anchored on a period gets a period slot; a trailing-window metric gets none
        self.assertEqual(set(MR.slots_of(lib['ed_visits'])), {'period'})
        self.assertEqual(MR.slots_of(lib['repeat_ed_users_12m']), {})
        # a measure-backed metric gets a period and the measure engine's group-by
        self.assertEqual(set(MR.slots_of(lib['measure.cbp'])), {'period', 'group_by'})

    def test_menu_is_compact_and_names_are_valid(self):
        lib = MR.routable()
        menu = MR.menu(lib)
        names = [t['name'] for t in menu]
        self.assertIn(MR.NO_METRIC, names); self.assertIn(MR.AMBIGUOUS, names)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', n) for n in names))
        self.assertGreaterEqual(len(lib), 70)
        self.assertLess(len(json.dumps(menu)), 40000)                            # well under the 128 KB request bound
        self.assertTrue(all(len(json.dumps(MR.fill_tool(k, m))) < 20000 for k, m in lib.items()))

    def test_family_metrics_validate_in_the_metric_layer(self):
        lib = metrics.library()
        for mid in ('patient_cohort', 'encounter_volume', 'visit_followup', 'medical_claims', 'feed_quality', 'pharmacy_returns_to_stock'):
            self.assertEqual(metrics.validate_metric(lib[mid], mid)['id'], mid)
            self.assertIsNone(lib[mid].get('sql_template'))                     # builders never resolve for SQL lint by accident
        bad = {**lib['patient_cohort'], 'builder': 'nope'}
        with self.assertRaises(ValueError): metrics.validate_metric(bad)
        bad = {**lib['patient_cohort'], 'slots': {'x': {'type': 'galaxy'}}}
        with self.assertRaises(ValueError): metrics.validate_metric(bad)
        builders = {k for k, m in lib.items() if m.get('builder')}               # no match rules: lint/pins never pick a family
        self.assertFalse(builders & {mid for _, mid in metrics.candidates('How many ED visits per 1,000 patients by month in 2029?')})


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.lib = MR.routable()

    def test_slots_are_typed_and_defaults_filled(self):
        m = self.lib['patient_cohort']
        s = MR.validate_slots(m, {'population': DIABETES})
        self.assertEqual(s['returns'], 'count'); self.assertEqual(s['group_by'], 'none')
        for bad, msg in [({'population': DIABETES, 'colour': 'red'}, 'unknown slots'), ({}, 'required'),
                         ({'population': DIABETES, 'returns': 'histogram'}, 'must be one of'),
                         ({'population': 'diabetics'}, 'list of atoms')]:
            with self.assertRaisesRegex(ValueError, msg): MR.validate_slots(m, bad)
        with self.assertRaisesRegex(ValueError, 'integer'):
            MR.validate_slots(self.lib['visit_followup'], {'index_classes': ['ed'], 'followup_classes': ['primary_care'], 'days_max': '7'})
        with self.assertRaisesRegex(ValueError, r"\['spa'\] not in"):
            MR.validate_slots(self.lib['encounter_volume'], {'visit_classes': ['spa']})

    def test_atoms_and_periods_are_validated_when_compiled(self):
        m = self.lib['patient_cohort']
        for atoms, msg in [([{'kind': 'condition', 'concepts': ['dragon_pox']}], 'concept library'),
                           ([{'kind': 'lab', 'concept': 'hba1c', 'op': 'gt'}], 'needs value'),
                           ([{'kind': 'medication', 'groups': ['statin'], 'evidence': 'rumour'}], 'evidence'),
                           ([{'kind': 'demographic', 'field': 'coverage_type', 'values': ['gold']}], 'values from'),
                           ([{'kind': 'lab', 'concept': 'ldl', 'period': {'preset': 'year', 'year': 2031}}], 'year up to'),
                           ([{'kind': 'medication', 'ingredients': ["x'); DROP TABLE patient; --"]}], 'ingredient')]:
            with self.assertRaisesRegex(ValueError, msg):
                MR.compile_metric('patient_cohort', m, MR.validate_slots(m, {'population': atoms}), AS_OF)

    def test_periods(self):
        self.assertEqual(MF.period_bounds({'preset': 'trailing_months', 'months': 12}, AS_OF), ('2028-08-16', AS_OF))
        self.assertEqual(MF.period_bounds({'preset': 'trailing_days', 'days': 180}, AS_OF), ('2029-02-16', AS_OF))
        self.assertEqual(MF.period_bounds({'preset': 'ytd'}, AS_OF), ('2029-01-01', AS_OF))
        self.assertEqual(MF.period_bounds({'preset': 'year', 'year': 2028}, AS_OF), ('2028-01-01', '2028-12-31'))
        self.assertEqual(MF.period_bounds({'preset': 'before_days', 'days': 30}, AS_OF), (None, '2029-07-16'))
        self.assertEqual(MF.period_bounds({'preset': 'all_time'}, AS_OF), (None, None))
        with self.assertRaises(ValueError): MF.period_bounds({'preset': 'custom', 'start': '2029-05-01', 'end': '2029-01-01'}, AS_OF)

    def test_every_number_in_the_question_must_land_in_a_slot(self):
        m = self.lib['patient_cohort']
        q = 'How many patients have had an LDL of 190 or higher in the last 12 months?'
        dropped = MR.validate_slots(m, {'population': [{'kind': 'lab', 'concept': 'ldl', 'op': 'exists'}]})
        self.assertEqual(MR.unaccounted_numbers(q, m, dropped, AS_OF), [12.0, 190.0])
        kept = MR.validate_slots(m, {'population': [{'kind': 'lab', 'concept': 'ldl', 'op': 'ge', 'value': 190,
                                                     'period': {'preset': 'trailing_months', 'months': 12}}]})
        self.assertEqual(MR.unaccounted_numbers(q, m, kept, AS_OF), [])
        # numbers stated by the metric's own definition (KED ages 18-85) and per-1,000 rates are accounted for
        ked = self.lib['kidney_health_evaluation_diabetes']
        self.assertEqual(MR.unaccounted_numbers('KED for diabetics 18-85 in 2028', ked, {'period': {'preset': 'year', 'year': 2028}}, AS_OF), [])


class FixtureCompute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = fixture_stack(Path(cls.tmp.name))
        from clinical_core.agere.clinical.age import register
        cls.db = register(sqlite3.connect(f'file:{cls.root / "semantic.sqlite3"}?mode=ro', uri=True))
        cls.lib = MR.routable()

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.tmp.cleanup()

    def run_metric(self, mid, **slots):
        m = self.lib[mid]
        node = MR.compile_metric(mid, m, MR.validate_slots(m, slots), AS_OF)
        return self.db.execute(node['parameters']['query']).fetchall(), node

    def test_builders_compute_on_the_fixture(self):
        self.assertEqual(self.run_metric('patient_cohort', population=DIABETES)[0], [(1,)])
        rows, node = self.run_metric('patient_cohort', population=[{'kind': 'medication', 'groups': ['beta_blocker']}], returns='patient_set')
        self.assertEqual((rows, node['parameters']['returns']), ([('P0',)], 'patient_set'))
        # LDL 195 is undated: any-time counts it, a dated window does not
        self.assertEqual(self.run_metric('patient_cohort', population=[{'kind': 'lab', 'concept': 'ldl', 'op': 'ge', 'value': 190}])[0], [(1,)])
        self.assertEqual(self.run_metric('patient_cohort', population=[{'kind': 'lab', 'concept': 'ldl', 'op': 'ge', 'value': 190,
                                                                        'period': {'preset': 'trailing_months', 'months': 12}}])[0], [(0,)])
        # latest eGFR (dated 70) is not < 60 even though an undated 60 exists
        self.assertEqual(self.run_metric('patient_cohort', population=[{'kind': 'lab', 'concept': 'egfr', 'op': 'lt', 'value': 65, 'aggregate': 'latest'}])[0], [(0,)])
        # repeat ED users: P1 has two ED visits (2028-12-01, 2029-03-01) in the trailing year
        self.assertEqual(self.run_metric('patient_cohort', population=[{'kind': 'visit', 'classes': ['ed'], 'min_count': 2,
                                                                        'period': {'preset': 'trailing_months', 'months': 12}}])[0], [(1,)])
        pct = self.run_metric('patient_cohort', population=[{'kind': 'social_need', 'needs': ['food'], 'screened_only': True}], returns='percent',
                              numerator=[{'kind': 'social_need', 'needs': ['food']}])[0]
        self.assertEqual(pct, [(50.0,)])
        self.assertEqual(self.run_metric('encounter_volume', visit_classes=['ed'], period={'preset': 'ytd'}, group_by='month')[0],
                         [('2029-02', 1), ('2029-03', 1)])
        # governed SQL returns full precision; presentation rounds (a scorer comparing at 2 dp must not see 33.3)
        self.assertAlmostEqual(self.run_metric('medical_claims', measures=['first_pass_denial_rate'], period={'preset': 'year', 'year': 2028})[0][0][0], 100 / 3)
        self.assertAlmostEqual(self.run_metric('medical_claims', measures=['ever_denied_rate'], anchor='benefit_year',
                                               period={'preset': 'year', 'year': 2028})[0][0][0], 100 / 3)
        self.assertEqual(self.run_metric('feed_quality', stream='labs', measures=['rows'])[0], [(0,)])
        self.assertEqual(self.run_metric('visit_followup', index_classes=['ed'], followup_classes=['primary_care'], days_max=7,
                                         period={'preset': 'year', 'year': 2029}, returns='count')[0], [(1,)])  # E1 -> V1 four days later

    def test_templated_and_measure_metrics_compile(self):
        node = MR.compile_metric('ed_visits', self.lib['ed_visits'], {'period': {'preset': 'year', 'year': 2029}}, AS_OF)
        self.assertIn("BETWEEN '2029-01-01' AND '2029-12-31'", node['parameters']['query'])
        # The fixture has one deduplicated all-source ED event; its two EHR
        # encounter rows are no longer the definition of this named metric.
        self.assertEqual(self.db.execute(node['parameters']['query']).fetchall(), [(1,)])
        # all_time is every observed date to the snapshot, not the metric's default year-to-date window
        node = MR.compile_metric('ed_visits', self.lib['ed_visits'], {'period': {'preset': 'all_time'}}, AS_OF)
        self.assertIn(f"BETWEEN '1900-01-01' AND '{AS_OF}'", node['parameters']['query'])
        node = MR.compile_metric('measure.cbp', self.lib['measure.cbp'], {'period': {'preset': 'year', 'year': 2028}, 'group_by': 'none'}, AS_OF)
        self.assertEqual((node['operation'], node['parameters']['measure'], node['parameters']['period']),
                         ('vbc', 'cbp', {'start': '2028-01-01', 'end': '2028-12-31'}))


class RouteTests(FixtureCompute):
    def test_metric_choice_computes_deterministically_and_states_the_metric(self):
        ask = Script(choose('patient_cohort'), fill('patient_cohort', population=DIABETES))
        graph, info = MR.route('How many diabetics do we have?', AS_OF, ask=ask, path=self.root)
        self.assertEqual(info['decision'], 'metric'); self.assertEqual(info['metric'], 'patient_cohort')
        self.assertIn(MR.NO_METRIC, ask.calls[0]['tools']); self.assertEqual(ask.calls[1]['forced'], MR.tool_name('patient_cohort'))
        node = graph['nodes'][-1]
        self.assertEqual(node['operation'], 'sql'); self.assertIn('concept_patient', node['parameters']['query'])
        self.assertTrue(graph['interpretation'].startswith('computed as metric patient_cohort@v1'))
        self.assertTrue(info['resolution']['statement'].startswith('computed as metric patient_cohort@v1'))

    def test_no_metric_falls_through(self):
        graph, info = MR.route('Why are readmissions rising?', AS_OF, ask=Script((MR.NO_METRIC, {'reason': 'a why question'})))
        self.assertIsNone(graph); self.assertEqual((info['decision'], info['reason']), (MR.NO_METRIC, 'a why question'))

    def test_invalid_slots_get_one_repair_then_fall_through(self):
        good = Script(choose('patient_cohort'), fill('patient_cohort', population=[{'kind': 'lab', 'concept': 'ldl', 'op': 'exists'}]),
                      fill('patient_cohort', population=[{'kind': 'lab', 'concept': 'ldl', 'op': 'ge', 'value': 190}]))
        graph, info = MR.route('How many patients ever had an LDL of 190 or more?', AS_OF, ask=good)
        self.assertIsNotNone(graph); self.assertIn('190', good.calls[2]['feedback'])          # the dropped threshold is named
        bad = Script(choose('patient_cohort'), fill('patient_cohort', population='diabetics'), fill('patient_cohort', returns='lots'))
        graph, info = MR.route('How many diabetics?', AS_OF, ask=bad)
        self.assertIsNone(graph); self.assertEqual(info['decision'], 'fill_failed'); self.assertEqual(len(info['rejected']), 2)

    def test_ambiguous_choice_returns_both_readings(self):
        amb = (MR.AMBIGUOUS, {'metrics': [MR.tool_name('patient_cohort'), MR.tool_name('encounter_volume')], 'difference': 'patients vs visits'})
        ask = Script(amb, fill('patient_cohort', population=[{'kind': 'visit', 'classes': ['ed']}]), fill('encounter_volume', visit_classes=['ed']))
        graph, info = MR.route('How much ED use do we have?', AS_OF, ask=ask, path=self.root)
        self.assertEqual(info['decision'], 'readings')
        r = info['readings']
        self.assertEqual([x['default'] for x in r['readings']], [True, False])
        self.assertEqual([x['result'] for x in r['readings']], [2, 3])                           # 2 patients, 3 visits
        self.assertIn('patients vs visits', r['clarifying_question'])
        answer = MR.finalize_answer({'status': 'answered', 'answer_type': 'count', 'count': 2},
                                    {'route': 'metric_router', 'metric_router': info})
        self.assertEqual(len(answer['readings']), 2); self.assertTrue(answer['clarifying_question'])
        self.assertTrue(any(c['control'] == 'QC-READINGS' for c in answer['quality_checks']))
        # two metrics that agree are one answer, not two readings
        ask = Script(amb, fill('patient_cohort', population=[{'kind': 'visit', 'classes': ['ed']}]),
                     fill('encounter_volume', visit_classes=['ed'], measure='patients'))
        graph, info = MR.route('How many ED users?', AS_OF, ask=ask, path=self.root)
        self.assertEqual(info['decision'], 'metric'); self.assertNotIn('readings', info)


class FreeFormAcceptance(unittest.TestCase):
    Q = 'How many claims had a pending remittance mismatch?'

    def test_acceptance_policy(self):
        independent = {'agreement': 'primary=compiler', 'verification': {'status': 'accepted', 'basis': 'independent'}}
        self.assertEqual(MR.acceptance(self.Q, 'SELECT 1', independent, AS_OF)['status'], 'verified')
        for receipt in ({'agreement': 'primary=second'}, {'agreement': 'judge_own'},
                        {'agreement': 'primary=second', 'verification': {'status': 'accepted', 'basis': 'same_family_with_oracles'}}):
            self.assertEqual(MR.acceptance(self.Q, 'SELECT 1', receipt, AS_OF)['status'], 'low_confidence')
        # a question resolving to a governed metric: lint-clean SQL is verified, a departing query is not
        q = 'How many ED visits did we have in 2029?'
        clean = "SELECT count(DISTINCT encounter_id) FROM visit WHERE type_concept = 'emergency_visit' AND event_date BETWEEN '2029-01-01' AND '2029-08-15'"
        self.assertEqual(MR.acceptance(q, clean, {'agreement': 'primary=second'}, AS_OF)['status'], 'verified')
        departs = "SELECT count(*) FROM visit WHERE type_concept = 'emergency_visit' AND available_date >= '2029-01-01'"
        with mock.patch.object(metrics, 'lint', return_value=[{'rule': 'ML-ANCHOR', 'message': 'uses available_date'}]):
            self.assertEqual(MR.acceptance(q, departs, {'agreement': 'primary=second'}, AS_OF)['status'], 'low_confidence')

    def test_unverified_free_form_sql_is_low_confidence_not_a_confident_number(self):
        answer = {'status': 'answered', 'answer_type': 'count', 'count': 41, 'missing': [], 'grounding': [], 'unknown_count': 0}
        receipt = {'route': 'hosted_verified_sql', 'acceptance': {'status': 'low_confidence', 'basis': 'hosted-only agreement'}}
        out = MR.finalize_answer(answer, receipt)
        # confidence is not completeness: the exact value stays answered, flagged low with the reason
        self.assertEqual((out['status'], out['confidence'], out['count']), ('answered', 'low', 41))
        self.assertIn('hosted-only agreement', out['confidence_reason'])
        self.assertTrue(any(c['control'] == 'QC-CONFIDENCE' for c in out['quality_checks']))
        with mock.patch.dict(os.environ, {'AGERE_FREEFORM_POLICY': 'abstain'}):
            out = MR.finalize_answer(answer, receipt)
        self.assertEqual((out['status'], out['answer_type'], out['count']), ('partial', 'unsupported', None))
        self.assertTrue(out['missing'][0].startswith('Withheld'))
        # acceptance 'verified' passes the free-form check, but the check's own error rate is uncertified: unverified
        verified = MR.finalize_answer(answer, {'route': 'hosted_verified_sql', 'acceptance': {'status': 'verified', 'basis': 'independent'}})
        self.assertEqual((verified['status'], verified['confidence']), ('answered', 'unverified'))
        self.assertIn('freeform_sql.verified', verified['confidence_reason'])
        # an answer that is genuinely incomplete keeps its partial status
        incomplete = MR.finalize_answer({**answer, 'status': 'partial', 'unknown_count': 3}, receipt)
        self.assertEqual((incomplete['status'], incomplete['confidence']), ('partial', 'low'))
        with mock.patch.dict(os.environ, {'AGERE_FREEFORM_POLICY': 'off'}):
            self.assertNotIn('confidence', MR.finalize_answer(answer, receipt))

    def test_low_confidence_pairs_are_never_learned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'run.json').write_text(json.dumps({'source_digest': 'x'}))
            pr = {'route': 'hosted_verified_sql', 'acceptance': {'status': 'low_confidence'},
                  'hosted_sql': {'capture': {'plan': {'query': 'SELECT 1', 'returns': 'count'}, 'signature': '[]'}}}
            out = learning.capture_verified(root, self.Q, pr, {'status': 'partial'})
        self.assertFalse(out['recorded']); self.assertTrue(any('low-confidence' in r for r in out['reasons']))


class RungOrdering(unittest.TestCase):
    def stack(self, root):
        return SimpleNamespace(path=root, receipts=root, allow_live=True,
                               retriever=SimpleNamespace(encoder=None, meta={'source_digest': 'fixture', 'snapshot': {'as_of': AS_OF}}))

    def patches(self, order):
        def op_route(*a, **k):
            order.append('operator_router')
            return None, {'decision': 'literal_lookup', 'calls': []}

        def hosted(path, question, as_of, *a, **k):
            order.append('hosted_sql')
            return {'query': 'SELECT 1', 'returns': 'count'}, {'value': 1, 'rows': [[1]], 'columns': ['n']}, \
                {'attempts': [{'role': 'primary'}, {'role': 'second'}], 'agreement': 'primary=second'}

        class Model:
            def __init__(self, *a, model='m', **k): self.model, self.cost = model, 0.0
        return [mock.patch('clinical_core.agere.clinical.source_profile.load', return_value={'streams': {}}),
                mock.patch('clinical_core.agere.clinical.local_compiler.compile', return_value=(None, {'route': 'fallback', 'reason': 'test'})),
                mock.patch.object(operator_router, 'route', side_effect=op_route),
                mock.patch('clinical_core.agere.clinical.nl2sql.verified_answer', side_effect=hosted),
                mock.patch('clinical_core.agere.clinical.nl2sql.HostedModel', Model),
                mock.patch('clinical_core.agere.clinical.nl2sql.Decider', Model),
                mock.patch('clinical_core.agere.gateway.BudgetLedger', Model),
                mock.patch.dict(os.environ, {'AGERE_LOCAL_MODEL': 'off', 'AGERE_SEMANTIC_CACHE': 'off', 'AGERE_VERIFICATION_V2': 'off'})]

    def plan(self, root, question, ask, order):
        def fake_ask(q, as_of, ledger, out, tools, forced=None, feedback=None, model=None):
            order.append('metric_router')
            return ask(q, as_of, tools=tools, forced=forced, feedback=feedback)
        ps = self.patches(order) + [mock.patch.object(MR, '_ask', side_effect=fake_ask)]
        for p in ps: p.start()
        try:
            # The ladder order is what legacy_plan preserves; planner.plan is the shared cascade (proposers.py), which
            # verifies and executes each proposal and so needs a real stack, not this fake.
            return planner.legacy_plan(self.stack(root), question, SimpleNamespace(path=root / 'ledger.sqlite3'), None)
        finally:
            for p in ps: p.stop()

    def test_metric_rung_runs_before_operator_router_and_free_form_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture_stack(Path(tmp))
            order = []
            graph, receipt = self.plan(root, 'How many diabetics do we have?', Script(choose('patient_cohort'), fill('patient_cohort', population=DIABETES)), order)
            self.assertEqual(receipt['route'], 'metric_router'); self.assertEqual(receipt['metric'], 'patient_cohort')
            self.assertEqual(order, ['metric_router', 'metric_router']); self.assertEqual(receipt['new_paid_api_calls'], 2)
            self.assertEqual(graph['nodes'][-1]['purpose'], 'Governed metric patient_cohort@v1')
            # no_metric: the question continues to the operator router, then free-form SQL, which is low-confidence
            order = []
            graph, receipt = self.plan(root, 'How many claims had a pending remittance mismatch?', Script((MR.NO_METRIC, {'reason': 'no fit'})), order)
            self.assertEqual(order, ['metric_router', 'operator_router', 'hosted_sql'])
            self.assertEqual(receipt['route'], 'hosted_verified_sql')
            self.assertEqual(receipt['local_compiler_declined']['metric_router']['decision'], MR.NO_METRIC)
            self.assertEqual(receipt['acceptance']['status'], 'low_confidence')
            # the rung can be switched off
            order = []
            with mock.patch.dict(os.environ, {'AGERE_METRIC_ROUTER': 'off'}):
                graph, receipt = self.plan(root, 'How many diabetics do we have?', Script(), order)
            self.assertEqual(order, ['operator_router', 'hosted_sql'])

    def test_engine_lean_puts_operator_router_before_metric_rung(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture_stack(Path(tmp))
            lean = {'label': 'monitor', 'probability': 0.75, 'threshold': planner.ENGINE_LEAN, 'lean': True}
            order = []
            with mock.patch.object(planner, '_engine_lean', return_value=lean):
                # (a count question: the scripted cohort metric must fit the requested shape, or metric_router.shape_misfit declines it)
                graph, receipt = self.plan(root, 'Has the number of diabetics shifted recently?', Script(choose('patient_cohort'), fill('patient_cohort', population=DIABETES)), order)
            # the operator router declined (literal lookup) here, so the metric rung still answers
            self.assertEqual(order[0], 'operator_router'); self.assertIn('metric_router', order)
            self.assertEqual(receipt['route'], 'metric_router')
            self.assertEqual(receipt['local_compiler_declined']['engine_lean'], lean)
            # a literal lean keeps the default order
            order = []
            with mock.patch.object(planner, '_engine_lean', return_value={**lean, 'label': 'literal_lookup', 'lean': False}):
                self.plan(root, 'How many diabetics do we have?', Script(choose('patient_cohort'), fill('patient_cohort', population=DIABETES)), order)
            self.assertEqual(order[0], 'metric_router')


@unittest.skipUnless((STACK / 'semantic.sqlite3').exists() and REFERENCES.exists(), 'v1.23 stack not present')
class ReproducesReferences(unittest.TestCase):
    """Each governed metric family reproduces the benchmark reference answer (bench/sql) for a question of its family on
    the v1.23 stack, opened read only. One question per metric keeps this light; tools/metric_first/replay.py runs all."""

    def test_metrics_reproduce_reference_answers(self):
        refs = json.loads(REFERENCES.read_text())['references']
        oracle = json.loads(ORACLE.read_text())['oracle']
        lib = MR.routable()
        source = sqlite3.connect(f'file:{STACK / "semantic.sqlite3"}?mode=ro', uri=True)
        db = age.register(sqlite3.connect(':memory:'))  # metric SQL uses age_at(birth_date), registered like the governed connection
        source.backup(db); source.close()
        # The v1.23 delivery carries reported ages only and age is derived from birth dates alone (age.py), so the in-memory copy
        # gets the birth date that reproduces each reported age at the snapshot; the read-only stack itself is untouched.
        db.execute("UPDATE patient SET birth_date = printf('%04d-01-01', CAST(substr((SELECT value FROM meta WHERE key = 'as_of'), 1, 4) AS INTEGER) - age_latest) "
                   'WHERE birth_date IS NULL AND age_latest IS NOT NULL')
        seen, checked = set(), []
        try:
            for qid in sorted(refs):
                entry, ref = oracle[qid], refs[qid]
                mid = entry['metric']
                if mid in seen or mid in ('patient_ar_outstanding',): continue  # one per metric; skip the slowest scan
                if lib[mid].get('builder') is None and MR.kind(lib[mid]) != 'template': continue
                seen.add(mid)
                m = lib[mid]
                node = MR.compile_metric(mid, m, MR.validate_slots(m, entry.get('slots') or {}), ref['as_of'], ref['question'])
                rows = [list(r) for r in db.execute(node['parameters']['query']).fetchall()]
                exp, kind = ref['expected'], ref['answer_type']
                if kind in ('count', 'value'):
                    self.assertAlmostEqual(rows[0][0], exp[kind], delta=max(ref['tolerance'], 1e-6), msg=qid)
                elif kind == 'patient_set':
                    self.assertEqual(sorted(r[0] for r in rows), exp['patient_ids'], qid)
                else:
                    # governed SQL returns full precision: compare numbers within the reference tolerance (column order free)
                    tol = max(ref['tolerance'], 1e-6)
                    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
                    norm = lambda r: (sorted(json.dumps(v) for v in r if not num(v)), sorted(float(v) for v in r if num(v)))
                    got, want = sorted(map(norm, rows)), sorted(norm(list(t.values())) for t in exp['table'])
                    self.assertEqual([g[0] for g in got], [w[0] for w in want], qid)
                    for (_, g), (_, w) in zip(got, want):
                        self.assertEqual(len(g), len(w), qid)
                        for a, b in zip(g, w): self.assertAlmostEqual(a, b, delta=tol, msg=qid)
                checked.append((qid, mid))
        finally:
            db.close()
        self.assertGreaterEqual(len(checked), 20)
        families = {mid for _, mid in checked}
        self.assertTrue({'patient_cohort', 'encounter_volume', 'visit_followup', 'length_of_stay', 'medical_claims', 'feed_quality',
                         'pharmacy_returns_to_stock', 'patient_payments'} <= families, families)


if __name__ == '__main__':
    unittest.main()
