"""line_of_therapy: episode, add-on / switch / restart / drop rules on hand-built fills, reversals excluded, routing, wiring."""
import json, os, sqlite3, unittest
from datetime import date, timedelta
from pathlib import Path

from clinical_core.agere import ask_view
from clinical_core.agere.clinical import analysis_graph, graph_contracts, line_of_therapy as LOT, local_compiler, semantic_layer, source_profile
from clinical_core.agere.clinical import operator_router as router
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS
from test_aggregate_explain import build

ROOT = Path(__file__).resolve().parents[2]
AS_OF = '2029-08-15'
REAL_V20 = Path(os.environ.get('RWE_STACK') or next(
    (p / 'artifacts' / 'agere-healthcare-v20' / 'stack' for p in (ROOT, *ROOT.parents) if (p / 'artifacts' / 'agere-healthcare-v20' / 'stack').exists()),
    ROOT / 'artifacts' / 'agere-healthcare-v20' / 'stack'))
D0 = date(2027, 1, 1)


def fills(pid, name, start, n, status='dispensed'):
    return [(pid, 'pharmacy_dispenses', {'medication_name': name, 'status': status, 'days_supply_reported': 30,
                                         'event_date': (start + timedelta(days=30 * k)).isoformat()}) for k in range(n)]


def fixture():
    """Eight patients with known patterns (see the assertions)."""
    met, sglt2, glp1, ins = 'metformin 500 mg tablet', 'empagliflozin 10 mg tablet', 'semaglutide 1 mg pen', 'insulin glargine 100 unit/ml'
    patients = {p: {'age_reported': 60, 'sex_reported': 'F'} for p in 'ABCDEFGH'}
    recs = [(p, 'conditions', {'description': 'Type 2 diabetes mellitus', 'icd10': 'E11.9', 'event_date': '2026-03-01'}) for p in 'ABCDEGH']
    recs += fills('A', 'levothyroxine 50 mcg tablet', date(2026, 1, 5), 1)  # starts the fill feed: washout ends 2026-07-04
    recs += fills('A', met, D0, 24) + fills('A', sglt2, date(2027, 7, 1), 12)          # add-on: metformin continues
    recs += fills('B', met, D0, 6) + fills('B', glp1, date(2027, 7, 10), 10)           # switch: metformin ends 06-29, GLP-1 starts 07-10
    recs += fills('C', met, D0, 3) + fills('C', met, date(2027, 8, 1), 3)              # 120-day gap: restart, same line
    recs += fills('D', met, D0, 12) + fills('D', sglt2, D0, 12)                        # dual first line
    recs += fills('E', met, date(2026, 2, 1), 30)                                      # prevalent (inside the washout)
    recs += fills('F', met, D0, 12)                                                    # no diabetes record
    recs += fills('G', met, D0, 3) + fills('G', met, date(2027, 4, 1), 1, 'returned_to_stock')  # returned fill never counts
    recs += fills('H', met, D0, 4) + fills('H', ins, date(2028, 6, 1), 6)              # metformin long gone: switch after a gap
    path = build(recs, patients)
    semantic_layer.build(path)
    return path


class Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = fixture()
        cls.r = LOT.execute(cls.path, None, {'area': 'diabetes'}, AS_OF)

    def test_counts_and_exclusions(self):
        c = self.r['counts']
        self.assertEqual(c['analysed_new_users'], 6)
        self.assertEqual(c['excluded_possible_prevalent_users'], 1)
        self.assertEqual(c['excluded_no_condition_record'], 1)

    def test_lines_add_on_and_switch(self):
        lines = {r['line']: r for r in self.r['lines']}
        self.assertEqual(lines[1]['patients'], 6)
        self.assertEqual((lines[2]['patients'], lines[2]['via_switch'], lines[2]['via_add_on']), (3, 2, 1))
        self.assertNotIn(3, lines)
        reg = {(r['line'], r['regimen']): r['patients'] for r in self.r['regimens']}
        self.assertEqual(reg[(1, 'metformin')], 5)
        self.assertEqual(reg[(1, 'metformin + sglt2_inhibitor')], 1)
        self.assertEqual(reg[(2, 'metformin + sglt2_inhibitor')], 1)  # A: the add-on keeps metformin
        self.assertEqual({(r['from_component'], r['to_component']): r['switches'] for r in self.r['switch_matrix']},
                         {('metformin', 'glp1_agonist'): 1, ('metformin', 'insulin'): 1})
        ex = {e['patient_id']: e['lines'] for e in self.r['examples']}
        self.assertEqual([x['via'] for x in ex['A']], ['first_line', 'add_on'])

    def test_restart_reversal_and_persistence(self):
        p = {'area': 'diabetes'}
        db = sqlite3.connect(f'file:{semantic_layer.ensure(self.path)}?mode=ro', uri=True)
        try:
            rows, _ = LOT.fill_rows(db)
        finally:
            db.close()
        self.assertEqual(sum(1 for r in rows if r[0] == 'G'), 3)  # the returned-to-stock dispense is not a fill
        c_eps = LOT.episodes({d: 30 for d in [(D0 + timedelta(days=30 * k)).toordinal() for k in range(3)] +
                              [(date(2027, 8, 1) + timedelta(days=30 * k)).toordinal() for k in range(3)]}, 60)
        self.assertEqual(len(c_eps), 2)
        per = {r['component']: r for r in self.r['persistence']}
        self.assertEqual(per['metformin']['first_line_starters'], 6)
        self.assertGreaterEqual(per['metformin']['discontinued'], 4)

    def test_add_on_same_line_rule(self):
        r = LOT.execute(self.path, None, {'area': 'diabetes', 'add_on_new_line': False}, AS_OF)
        lines = {x['line']: x for x in r['lines']}
        self.assertEqual((lines[2]['patients'], lines[2]['via_add_on']), (2, 0))

    def test_episode_carry_forward_and_pdc(self):
        d = date(2027, 1, 1).toordinal()
        self.assertEqual(LOT.episodes({d: 30, d + 10: 30}, 30), [(d, d + 59, 2)])  # overlap shifts forward
        self.assertEqual(LOT.episodes({d: 30, d + 100: 30}, 30), [(d, d + 29, 1), (d + 100, d + 129, 1)])
        self.assertAlmostEqual(LOT.pdc({d: 30, d + 60: 30}, d, d + 89), 60 / 90)

    def test_graph_wiring_and_view(self):
        plan = {'interpretation': 'lines of therapy for diabetes', 'coverage': 'supported_observation', 'unmet_requirements': [],
                'nodes': [{'id': 'answer', 'operation': 'line_of_therapy', 'inputs': [], 'parameters': {'area': 'diabetes'}, 'purpose': 'LoT'}]}
        checked = analysis_graph.validate(plan, AS_OF)
        answer = analysis_graph.build_answer(checked, [{'result': self.r}], {}, plan['interpretation'])
        json.dumps(answer, allow_nan=False, default=str)
        self.assertEqual(answer['answer_type'], 'table')
        self.assertIn('line_of_therapy', answer['overall'])
        self.assertIn('vizLines', ask_view.page())
        with self.assertRaises(ValueError): LOT.validate({'area': 'oncology'})


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = source_profile.load(fixture())

    def plan(self, q): return local_compiler.compile(q, self.profile, AS_OF)

    def test_registry_integration(self):
        for where in (PRODUCT, analysis_graph.OPERATIONS, CONTRACTS, graph_contracts.TYPED, analysis_graph.NO_BENCHMARK_OPERATIONS):
            self.assertIn('line_of_therapy', where)
        self.assertLess(len(json.dumps(router.tool('line_of_therapy'))), 20000)

    def test_phrasings_route_here(self):
        cases = {'What are the lines of therapy for type 2 diabetes?': ('diabetes', 'lines'),
                 'First-line and second-line regimens in heart failure': ('heart_failure', 'lines'),
                 'How often do hypertension patients switch from an ACE inhibitor to an ARB?': ('hypertension', 'switching'),
                 'Most common treatment sequences for patients newly diagnosed with type 2 diabetes': ('diabetes', 'sequences'),
                 'Persistence on first-line antidepressants for depression': ('depression', 'persistence'),
                 'For rheumatoid arthritis, what share of patients switch from a first biologic to a second, and to which class?': ('rheumatoid_arthritis', 'switching'),
                 'Treatment patterns for CKD patients': ('chronic_kidney_disease', 'all'),
                 'Show a Sankey of GDMT treatment sequences in HFrEF': ('heart_failure', 'sequences'),
                 'How long are patients on their first line of therapy for diabetes?': ('diabetes', 'lines'),
                 'discontinuation of first-line hypertension medications': ('hypertension', 'persistence')}
        for q, (area, focus) in cases.items():
            g, info = self.plan(q)
            self.assertIsNotNone(g, (q, info))
            self.assertEqual([n['operation'] for n in g['nodes']], ['line_of_therapy'], (q, g))
            p = analysis_graph.validate(g, AS_OF)['nodes'][-1]['parameters']
            self.assertEqual((p['area'], p['focus']), (area, focus), q)

    def test_acceptance_phrasings(self):
        cases = json.loads((ROOT / 'bench' / 'acceptance' / 'line_of_therapy.json').read_text())
        self.assertEqual(len(cases), 8)
        for case in cases:
            g, info = self.plan(case['question'])
            self.assertIsNotNone(g, (case['question'], info))
            self.assertEqual([n['operation'] for n in g['nodes']], [case['expect_operation']], case['question'])
            analysis_graph.validate(g, AS_OF)

    def test_oncology_is_named_unsupported(self):
        g, _ = self.plan('What are first-, second- and third-line regimens for metastatic breast cancer and how long does each line last?')
        self.assertEqual(g['coverage'], 'unsupported')
        self.assertTrue(any('antineoplastic' in m for m in g['unmet_requirements']))

    def test_survival_program_effect_and_pathway_not_stolen(self):
        for q in ('What is time to insulin initiation after metformin start for type 2 diabetes?',
                  'What is time to treatment discontinuation for SGLT2 inhibitors versus GLP-1 agonists in type 2 diabetes?',
                  'How long until readmission after discharge for heart failure patients?',
                  'Did starting an SGLT2 inhibitor reduce heart-failure admissions?',
                  'Where do patients go after an ED visit?', 'What is our statin PDC adherence rate?',
                  'What is persistence at 12 months for patients newly started on oral anticoagulants?',
                  'How long do new users of GLP-1 agonists stay on therapy before a 60-day gap?'):
            self.assertIsNone(local_compiler._line_of_therapy_question(q, self.profile), q)


def _v20_ready():
    try:
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)
        db.execute('SELECT count(*) FROM medication').fetchone(); db.close()
        return True
    except Exception:
        return False


@unittest.skipUnless(_v20_ready(), 'warehouse v2.0 not present')
class RealV20(unittest.TestCase):
    def test_smoke(self):
        db = sqlite3.connect(f"file:{REAL_V20 / 'semantic.sqlite3'}?mode=ro", uri=True)
        try:
            as_of = dict(db.execute('SELECT key, value FROM meta'))['as_of']
            r = LOT.run(db, None, {'area': 'diabetes'}, as_of)
        finally:
            db.close()
        self.assertGreater(r['counts']['patients_with_condition'], 50)
        self.assertTrue(any(w.startswith('Synthetic') for w in r['warnings']))
        self.assertEqual(sum(x['patients'] for x in r['regimens'] if x['line'] == 1), (r['lines'][0]['patients'] if r['lines'] else 0))


if __name__ == '__main__':
    unittest.main()
