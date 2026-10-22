"""Governed CQL -> ELM measures: parser, three-valued logic, governance digest, engine wiring, parity with the measure library."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_core.agere.clinical import cql_measures as C, engine_suite as E, graph_contracts
from clinical_core.agere.clinical.analysis_graph import validate as graph_validate
from clinical_core.agere.clinical.capability_registry import PRODUCT
from clinical_core.agere.clinical.graph_planner import CONTRACTS

AS_OF = '2030-05-27'
MINI = '''library Mini version '1.0.0'
valueset "HTN": {"type": "condition", "icd10_prefixes": ["I10"]}
valueset "A1c": {"type": "measurement", "concept": "hba1c"}
parameter "Measurement Period" Interval<Date> default Interval[@2029-01-01, @2029-12-31]
// comment line
define "Initial Population":
  AgeInYearsAt(end of "Measurement Period") between 18 and 75
  and exists ([Condition: "HTN"] C where (C.date during Interval[start of "Measurement Period" - 12 months, end of "Measurement Period"] or C.date is null))
define "Denominator": "Initial Population"
define "Numerator": Last([Observation: "A1c"] O where O.date during "Measurement Period").value < 8.0
'''


def run_with(rows, people, elm=None, period=('2029-01-01', '2029-12-31')):
    """A Run over fake evidence rows {(dataType, valueset): {patient: [(date, value, detail)]}} and people {patient: {...}}."""
    elm = elm or C.parse(MINI)
    r = C.Run.__new__(C.Run)
    from clinical_core.agere.clinical import measures as M
    r.M, r.lib, r.period, r.as_of = M, elm['library'], period, AS_OF
    r.vs = {v['name']: v['binding'] for v in r.lib['valueSets']}
    r.defs = {s['name']: s['expression'] for s in r.lib['statements']}
    r.people, r.rows, r.ctx, r.db = people, rows, None, None
    r.retrieved = lambda node: rows[(node['dataType'], node['valueSet'])]
    r.M = SimpleNamespace(_age_at=lambda info, on, as_of: info['age'], _num=M._num, _enrolled=lambda *a: True)
    return r


class Parse(unittest.TestCase):
    def test_parses_to_elm_with_populations(self):
        elm = C.parse(MINI)
        lib = elm['library']
        self.assertEqual(lib['identifier'], {'id': 'Mini', 'version': '1.0.0'})
        self.assertEqual([s['name'] for s in lib['statements']], ['Initial Population', 'Denominator', 'Numerator'])
        types = {n.get('type') for n in C._walk(lib['statements'])}
        self.assertTrue({'And', 'Exists', 'Query', 'Retrieve', 'Last', 'In', 'AgeInYearsAt', 'Subtract'} <= types)
        self.assertEqual(C.elm_sha256(elm), C.elm_sha256(C.parse(MINI)))
        self.assertNotEqual(C.elm_sha256(elm), C.elm_sha256(C.parse(MINI.replace('< 8.0', '< 9.0'))))

    def test_errors_name_the_line(self):
        for bad, frag in ((MINI.replace('define "Numerator"', 'define "Num"'), 'Numerator'),
                          (MINI.replace('define "Initial Population":\n  ', 'define "Initial Population":\n  "Numerator" and ', 1), 'before it is defined'),
                          (MINI.replace('[Condition: "HTN"]', '[Condition: "Nope"]'), 'declared valueset'),
                          (MINI.replace('[Observation: "A1c"]', '[Condition: "A1c"]'), 'bound to a'),
                          (MINI.replace('between 18 and 75', 'between 18 and'), 'line')):
            with self.assertRaises(C.CQLError) as cm:
                C.parse(bad)
            self.assertIn(frag, str(cm.exception))


class ThreeValuedLogic(unittest.TestCase):
    def setUp(self):
        self.rows = {('Condition', 'HTN'): {'A': [('2029-03-01', None, 'I10')], 'B': [(None, None, 'I10')], 'D': [('2020-01-01', None, 'I10')]},
                     ('Observation', 'A1c'): {'A': [('2029-05-01', 9.1, None), ('2029-06-01', 7.2, None), ('2029-06-01', 7.9, None)],
                                              'B': [('2029-02-01', 8.5, None)]}}
        self.people = {p: {'age': a} for p, a in (('A', 50), ('B', 60), ('C', 40), ('D', 80))}

    def test_population_numerator_and_undated(self):
        r = run_with(self.rows, self.people)
        a, b, c, d = (r.run(p) for p in 'ABCD')
        self.assertTrue(a['Initial Population'] and a['Numerator'])          # latest day 2029-06-01, lowest 7.2 < 8
        self.assertTrue(b['Initial Population'] and b['Numerator'] is False)  # undated diagnosis counts; 8.5 is not < 8
        self.assertFalse(c['Initial Population'])                            # no diagnosis
        self.assertFalse(d['Initial Population'])                            # age 80 and diagnosis outside the window
        self.assertIsNone(r.run('C')['Numerator'])                           # no result: unknown, never true

    def test_null_comparisons_never_make_membership(self):
        run = run_with(self.rows, {'C': {'age': 40}})
        out = run.run('C')
        self.assertIsNot(out['Numerator'], True)

    def test_and_or_not_truth_tables(self):
        r = run_with(self.rows, self.people)
        env = {'pid': 'A', 'cache': {}, 'alias': {}}
        T, F, N = ({'type': 'Literal', 'valueType': 'Boolean', 'value': True}, {'type': 'Literal', 'valueType': 'Boolean', 'value': False}, {'type': 'Null'})
        ev = lambda t, *ops: r.ev({'type': t, 'operand': list(ops) if t != 'Not' else ops[0]}, env)
        self.assertIs(ev('And', F, N), False); self.assertIsNone(ev('And', T, N)); self.assertIs(ev('Or', T, N), True)
        self.assertIsNone(ev('Or', F, N)); self.assertIsNone(ev('Not', N)); self.assertIs(ev('Not', F), True)


class BloodPressure(unittest.TestCase):
    def test_representative_reading_is_latest_day_with_both_lowest_of_each(self):
        cql = '''library BP version '1'
valueset "BP": {"type": "blood_pressure"}
parameter "Measurement Period" Interval<Date> default Interval[@2029-01-01, @2029-12-31]
define "Initial Population": true
define "Numerator": Last([Observation: "BP"] O where O.date during "Measurement Period").systolic < 140 and Last([Observation: "BP"] O where O.date during "Measurement Period").diastolic < 90
'''
        rows = {('Observation', 'BP'): {'A': [('2029-03-01', 150.0, 'systolic_bp'), ('2029-03-01', 95.0, 'diastolic_bp'),
                                              ('2029-06-01', 142.0, 'systolic_bp'), ('2029-06-01', 130.0, 'systolic_bp'), ('2029-06-01', 88.0, 'diastolic_bp'),
                                              ('2029-09-01', 120.0, 'systolic_bp')],  # later day with no diastolic: not representative
                                        'B': [('2029-06-01', 150.0, 'systolic_bp'), ('2029-06-01', 80.0, 'diastolic_bp')]}}
        r = run_with(rows, {'A': {'age': 50}, 'B': {'age': 50}, 'C': {'age': 50}}, C.parse(cql))
        self.assertTrue(r.run('A')['Numerator']); self.assertFalse(r.run('B')['Numerator']); self.assertIsNot(r.run('C')['Numerator'], True)


class Governance(unittest.TestCase):
    def test_registry_is_governed_and_tamper_is_visible(self):
        reg = C.registry()
        self.assertEqual(set(reg), {'cbp', 'gsd_control_lt8', 'spd_statin_diabetes'})
        self.assertTrue(all(v['governed'] for v in reg.values()))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for f in C.LIB_DIR.glob('*.cql'): (d / f.name).write_text(f.read_text())
            (d / 'registry.json').write_text((C.LIB_DIR / 'registry.json').read_text())
            (d / 'cbp.cql').write_text((d / 'cbp.cql').read_text().replace('< 140', '< 150'))
            changed = C.registry(d)
            self.assertFalse(changed['cbp']['governed']); self.assertTrue(changed['gsd_control_lt8']['governed'])
            C.govern(['cbp'], d)
            self.assertTrue(C.registry(d)['cbp']['governed'])

    def test_each_cql_names_every_population_and_a_reference_measure(self):
        from clinical_core.agere.clinical import measures as M
        lib = M.library()
        for mid, spec in C.registry().items():
            names = [s['name'] for s in spec['elm']['library']['statements']]
            for pop in C.POPULATIONS: self.assertIn(pop, names)
            self.assertIn(spec['reference_measure'], lib)


class EngineWiring(unittest.TestCase):
    def test_on_menu_with_contract_and_schema(self):
        self.assertIn('cql_measure', E.MODULES)
        self.assertIn('cql_measure', PRODUCT); self.assertIn('cql_measure', CONTRACTS); self.assertIn('cql_measure', graph_contracts.TYPED)
        self.assertIn('type', graph_contracts.parameter_schema('cql_measure', {'cohort_fields': {'record_fields': {}}, 'evidence_catalog': {}}))

    def test_free_compiler_routes(self):
        cases = {'Show the executable CQL logic for the blood pressure control measure': ('cbp', 'cql'),
                 'Run the CQL for controlling high blood pressure and tell me the rate': ('cbp', 'rate'),
                 'Do the CQL and the measure library agree for the diabetes A1c control measure?': ('gsd_control_lt8', 'check'),
                 'Why is patient P-1002 in the denominator of the glycemic control CQL measure?': ('gsd_control_lt8', 'eligibility'),
                 'Show the compiled ELM for the statin therapy for diabetes measure': ('spd_statin_diabetes', 'elm')}
        for q, (mid, out) in cases.items():
            g, _ = E.compile_route(q, {}, AS_OF)
            p = g['nodes'][0]['parameters']
            self.assertEqual((p['measure'], p['output']), (mid, out), q)
            graph_validate(g, AS_OF)
        self.assertIsNone(E.compile_route('What is our colorectal screening rate?', {}, AS_OF))  # no CQL ask: the library engine answers

    def test_validate_rejects_bad_parameters(self):
        for bad in ({'measure': 'cbp', 'output': 'nope'}, {'measure': 'cbp', 'output': 'eligibility'}, {'measure': 'CBP; drop'}, {'measure': 'cbp', 'x': 1},
                    {'measure': 'cbp', 'period': {'start': '2029', 'end': '2030-01-01'}}):
            with self.assertRaises(ValueError): C.validate(bad)


def _active():
    try:
        cfg = json.loads((Path(__file__).parent / 'config' / 'agere' / 'active_warehouse.json').read_text())
        s = Path(cfg['build']) / 'stack'
        return s if (s / 'semantic.sqlite3').exists() else None
    except (OSError, KeyError, ValueError):
        return None


@unittest.skipUnless(_active(), 'active synthetic warehouse build is not on this machine')
class ParityOnActiveWarehouse(unittest.TestCase):
    def test_cql_reproduces_the_measure_library_exactly(self):
        stack = _active()
        for mid in ('cbp', 'gsd_control_lt8', 'spd_statin_diabetes'):
            for period in (None, {'start': '2029-01-01', 'end': '2029-12-31'}):
                rep = C.execute(stack, None, {'measure': mid, 'output': 'check', **({'period': period} if period else {})}, AS_OF)
                c = rep['check']
                self.assertTrue(c['identical_counts'], (mid, c))
                self.assertTrue(c['identical_patient_sets'], (mid, c['differences']))
                self.assertGreater(c['cql']['denominator'], 100)

    def test_eligibility_trace_for_one_patient(self):
        rep = C.execute(_active(), None, {'measure': 'cbp', 'output': 'patients'}, AS_OF)
        pid = rep['patient_ids'][0]
        rep = C.execute(_active(), None, {'measure': 'cbp', 'output': 'eligibility', 'patient_id': pid}, AS_OF)
        tr = rep['patient']['trace']
        self.assertTrue(tr['Initial Population']); self.assertFalse(tr['Denominator Exclusion'])
        self.assertIn(pid, C.answer(rep, {'coverage': 'supported_observation'}, 'why')['value'])


if __name__ == '__main__':
    unittest.main()
