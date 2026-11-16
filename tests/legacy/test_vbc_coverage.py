"""VBC question library gaps: correctness (no data graph for questions the data cannot answer), data-aware refusals
(race/ethnicity, language, CPT on v2.0 vs v1.23), engine choice (risk-adjusted provider comparisons -> profile), settlement
without terms (asks for program or terms), local grammar for common VBC phrasings, and no-steal checks on the acceptance sets.

Compiles read-only against the cached source profiles of the two synthetic stacks; skipped when a stack is absent."""
import copy, glob, json, sqlite3, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from clinical_core.agere.clinical import aggregate, local_compiler, measures, vbc  # noqa: E402

ARTIFACTS = Path('/Users/mikel/clinical-insights-local/artifacts')
V123 = ARTIFACTS / 'agere-healthcare-v123/stack'
V20 = ARTIFACTS / 'agere-healthcare-v20/stack'
AS_OF = {'v123': '2029-08-15', 'v20': '2030-05-27'}
LIBRARY = {q['id']: q for q in json.loads((ROOT / 'config/agere/vbc_questions.json').read_text())['questions']}
_PROFILES = {}


def profile(name):
    if name not in _PROFILES:
        import vbc_question_coverage as cov
        _PROFILES[name] = cov.load_profile(V123 if name == 'v123' else V20)
    return _PROFILES[name]


def without_v2_fields(p):
    """A v2.0 profile with race/ethnicity/language and CPT removed: data-awareness must follow the profile, not the release name."""
    p = copy.deepcopy(p)
    for f in ('race', 'ethnicity', 'preferred_language', 'interpreter_needed'): p['patients']['fields'].pop(f, None)
    for s in ('medical_claim_lines', 'medical_claims'):
        p['streams'][s]['fields']['service_code_system']['values'] = {'synthetic_local': 1}
    return p


def compile_q(q, name, prof=None):
    q = LIBRARY[q]['question'] if q in LIBRARY else q
    return local_compiler.compile(q, prof or profile(name), AS_OF[name])


def op(graph):
    return graph['nodes'][-1]['operation'] if graph and graph.get('nodes') else None


HAVE_BOTH = (V123 / 'run.json').exists() and (V20 / 'run.json').exists()


@unittest.skipUnless(HAVE_BOTH, 'synthetic v1.23 and v2.0 stacks not present')
class Correctness(unittest.TestCase):
    def assertUnsupported(self, q, name, needle):
        g, info = compile_q(q, name)
        self.assertEqual((g or {}).get('coverage'), 'unsupported', (q, name, info.get('reason')))
        self.assertEqual(info['route'], 'local_unsupported', q)
        self.assertTrue(any(needle in m for m in g['unmet_requirements']), (q, g['unmet_requirements']))
        return g

    def test_deprivation_and_cms_report_never_get_a_data_graph(self):
        for name in ('v123', 'v20'):
            self.assertUnsupported('VQ-175', name, 'deprivation index')  # was a vbc attribution graph by practice
            self.assertUnsupported('VQ-312', name, 'CMS ACO program reports')  # was a vbc cost graph
            self.assertUnsupported('VQ-051', name, 'enrollment/entitlement status')  # was a knowledge answer to a data question
            self.assertUnsupported('VQ-219', name, 'settlement results')
        g = self.assertUnsupported('VQ-175', 'v20', 'deprivation index')
        self.assertIn('ZIP', g['unmet_requirements'][0])  # names what the release does carry

    def test_no_unsupported_library_question_compiles_to_a_data_graph(self):
        for name in ('v123', 'v20'):
            for qid, item in LIBRARY.items():
                if item['expected_engine'] != 'unsupported': continue
                g, _ = compile_q(qid, name)
                if op(g) is None: continue
                # v2.0 carries ICD-10 on claims: CMS-HCC questions become answerable by risk_adjustment (data-aware, not a wrong engine)
                self.assertTrue(name == 'v20' and op(g) == 'risk_adjustment', (name, qid, op(g), item['question']))

    def test_survives_is_not_mortality(self):
        for name in ('v123', 'v20'):
            g, info = compile_q('R-DISCOVERY-04', name)
            self.assertFalse(any('mortality' in m for m in (g or {}).get('unmet_requirements', [])), info)
            g, _ = compile_q('How many heart failure patients died after discharge?', name)
            self.assertEqual(g['coverage'], 'unsupported')

    def test_comparisons_are_not_program_effect_refusals(self):
        for qid in ('VQ-134', 'VQ-325', 'VQ-328', 'R-BEHAV-01'):
            g, _ = compile_q(qid, 'v20')
            self.assertFalse(any('named program' in m for m in (g or {}).get('unmet_requirements', [])), qid)
        g, _ = compile_q('Did our program reduce ED visits?', 'v20')
        self.assertTrue(any('named program' in m for m in g['unmet_requirements']))
        g, _ = compile_q('VQ-156', 'v20')  # a TCM visit is a transitions-of-care exposure
        self.assertEqual((op(g), g['nodes'][-1]['parameters']['program']), ('program_effect', 'transitions_of_care'))

    def test_risk_adjustment_comparison_is_not_silently_dropped(self):
        g, info = compile_q('VQ-090', 'v20')  # RAF of AWV vs no AWV: the report has no such split
        self.assertIsNone(g); self.assertEqual(info['route'], 'fallback')

    def test_explanation_of_data_question_is_not_knowledge(self):
        for qid in ('VQ-065', 'VQ-095', 'VQ-324'):
            g, _ = compile_q(qid, 'v123')
            self.assertNotEqual(op(g), 'knowledge', qid)


@unittest.skipUnless(HAVE_BOTH, 'synthetic v1.23 and v2.0 stacks not present')
class DataAware(unittest.TestCase):
    def test_race_ethnicity_refused_on_v123_answered_on_v20(self):
        q = 'What is our A1c poor control rate by race?'
        g, info = compile_q(q, 'v123')
        self.assertEqual(info['route'], 'local_unsupported'); self.assertIn('race/ethnicity', g['unmet_requirements'][0])
        g, _ = compile_q(q, 'v20')
        self.assertEqual((op(g), g['nodes'][-1]['parameters']['group_by']), ('vbc', 'race'))
        g, _ = compile_q('What is our colorectal cancer screening rate stratified by race and ethnicity?', 'v20')
        self.assertEqual((op(g), g['nodes'][-1]['parameters']['group_by'], g['nodes'][-1]['parameters']['measure']), ('vbc', 'race_ethnicity', 'col'))
        g, _ = compile_q('What is the race breakdown of our diabetic patients?', 'v20')
        self.assertEqual((op(g), g['nodes'][-1]['parameters']['group_by']), ('aggregate', 'race'))
        self.assertTrue(any(a.get('concept') == 'diabetes' for a in g['nodes'][0]['parameters']['specification']['all']))
        g, _ = compile_q('What is PMPM by ethnicity?', 'v20')
        self.assertEqual(g['nodes'][-1]['parameters'], {'metric': 'cost', 'group_by': 'ethnicity'})
        # same profile without the fields: refused again (data-aware, not release-name-aware)
        g, info = compile_q('What is PMPM by ethnicity?', 'v20', without_v2_fields(profile('v20')))
        self.assertEqual(info['route'], 'local_unsupported')

    def test_language_and_interpreter(self):
        g, info = compile_q('How many patients have diabetes by preferred language?', 'v123')
        self.assertEqual(info['route'], 'local_unsupported')
        g, _ = compile_q('How many patients have diabetes by preferred language?', 'v20')
        self.assertEqual((op(g), g['nodes'][-1]['parameters']['group_by']), ('aggregate', 'language'))
        g, _ = compile_q('ED visits per 1,000 by interpreter need', 'v20')
        self.assertEqual((op(g), g['nodes'][-1]['parameters']['group_by']), ('vbc', 'interpreter'))

    def test_subgroup_words_are_never_ignored(self):
        for q in ('How many Hispanic patients have diabetes?', 'How many of our patients need an interpreter at visits?',
                  'How many Spanish-speaking members have hypertension?', 'Are our Hispanic patients getting their A1c under control at the same rate as everyone else?'):
            g, info = compile_q(q, 'v20')
            self.assertIsNone(g, q); self.assertEqual(info['route'], 'fallback', q)
            g, info = compile_q(q, 'v123')
            self.assertEqual((g or {}).get('coverage'), 'unsupported', q)  # not received: refused, never a count without the filter

    def test_cpt_literal_lookups(self):
        q = 'How many patients had CPT 99214 or 99215 in 2029?'
        g, info = compile_q(q, 'v123')
        self.assertEqual(info['route'], 'local_unsupported'); self.assertIn('CPT/HCPCS', g['unmet_requirements'][0])
        g, _ = compile_q(q, 'v20')
        sql = g['nodes'][-1]['parameters']['query']
        self.assertEqual(op(g), 'sql')
        self.assertIn("IN ('99214', '99215')", sql); self.assertIn('count(DISTINCT patient_id)', sql); self.assertIn("'2029-01-01' AND '2029-12-31'", sql)
        g, _ = compile_q('What are the top 5 CPT codes by volume?', 'v20')
        self.assertIn('LIMIT 5', g['nodes'][-1]['parameters']['query'])
        g, _ = compile_q('How many claim lines have HCPCS G0438?', 'v20')
        self.assertIn("'G0438'", g['nodes'][-1]['parameters']['query'])
        g, info = compile_q('How many CPT 99214 visits were in practices with high denial rates?', 'v20')
        self.assertIsNone(g)  # unread words: never a plain count

    def test_cpt_query_runs_on_v20(self):
        g, _ = compile_q('How many claim lines have CPT 99214?', 'v20')
        db = sqlite3.connect(f'file:{V20 / "semantic.sqlite3"}?mode=ro', uri=True)
        try:
            n = db.execute(g['nodes'][-1]['parameters']['query']).fetchone()[0]
        finally:
            db.close()
        self.assertGreater(n, 0)

    def test_rvu_always_unsupported_and_named(self):
        for name in ('v123', 'v20'):
            g, info = compile_q('What is our total RVU volume by CPT code?', name)
            self.assertEqual(info['route'], 'local_unsupported')
            self.assertIn('RVU', g['unmet_requirements'][0])
        g, _ = compile_q('What is our total RVU volume by CPT code?', 'v20')
        self.assertIn('carries CPT/HCPCS', g['unmet_requirements'][0])


@unittest.skipUnless(HAVE_BOTH, 'synthetic v1.23 and v2.0 stacks not present')
class Engines(unittest.TestCase):
    def test_risk_adjusted_provider_comparisons_go_to_profile(self):
        for qid, unit, metric in (('VQ-053', 'practice', 'pmpm'), ('VQ-283', 'pcp', 'pmpm'), ('VQ-290', 'pcp', 'admissions')):
            for name in ('v123', 'v20'):
                g, _ = compile_q(qid, name)
                p = g['nodes'][-1]['parameters']
                self.assertEqual((op(g), p['unit'], p['metric'], p['adjust']), ('profile', unit, metric, 'age_sex'), (qid, name))
        g, info = compile_q('VQ-286', 'v123')  # 'avoidable' has no implemented algorithm: never silently a plain ED rate
        self.assertIsNone(g)

    def test_settlement_without_terms_asks_for_program_or_terms(self):
        for qid in ('VQ-212', 'VQ-226', 'VQ-227'):
            for name in ('v123', 'v20'):
                g, info = compile_q(qid, name)
                self.assertEqual(info['route'], 'local_requires_input', qid)
                self.assertTrue(any('program or contract terms' in m for m in g['unmet_requirements']), qid)
                self.assertTrue(info['requires_input'])
        g, info = compile_q('What would we earn under MSSP ENHANCED?', 'v123')
        self.assertEqual(info['route'], 'local_requires_input')
        self.assertIn('benchmark_pmpm', info['requires_input'][0]); self.assertNotIn('sharing_rate (', info['requires_input'][0].split(';')[0])
        g, _ = compile_q('What would we earn under MSSP ENHANCED with a $1,000 benchmark?', 'v123')
        self.assertEqual((op(g), g['nodes'][-1]['parameters']['metric']), ('vbc', 'shared_savings'))
        g, info = compile_q('VQ-015', 'v123')  # roster counts per contract: no settlement asked, so not a request for terms
        self.assertEqual(info['route'], 'local_unsupported')

    def test_program_mechanics_explained_from_knowledge(self):
        for qid in ('VQ-063', 'VQ-110', 'VQ-235', 'VQ-224'):
            g, _ = compile_q(qid, 'v123')
            self.assertEqual(op(g), 'knowledge', qid)
        g, info = compile_q('VQ-249', 'v123')  # a probability about us is data, not an explanation
        self.assertEqual(info['route'], 'local_requires_input')


@unittest.skipUnless(HAVE_BOTH, 'synthetic v1.23 and v2.0 stacks not present')
class Grammar(unittest.TestCase):
    def params(self, q, name='v123'):
        g, info = compile_q(q, name)
        self.assertIsNotNone(g, (q, info.get('reason')))
        return op(g), g['nodes'][-1]['parameters']

    def test_calendar_periods(self):
        self.assertEqual(self.params('What is our PMPM by quarter?'), ('vbc', {'metric': 'cost', 'group_by': 'quarter'}))
        self.assertEqual(self.params('What are admissions per 1,000 by quarter?'), ('vbc', {'metric': 'utilization', 'group_by': 'quarter'}))
        g, _ = compile_q('What is our A1c control rate by quarter?', 'v123')
        self.assertIsNone(g)  # quality has no calendar-period grouping: router, never a dropped 'by quarter'

    def test_change_phrasings(self):
        o, p = self.params('VQ-045')
        self.assertEqual((o, p['metric'], p['measure']), ('vbc', 'change', 'ed_rate'))
        self.assertEqual(p['compare'], {'a': {'start': '2029-04-01', 'end': '2029-06-30'}, 'b': {'start': '2029-07-01', 'end': '2029-08-15'}})
        o, p = self.params('VQ-045', 'v20')  # Q3 2030 has not started at the v2.0 snapshot: last year's quarters
        self.assertEqual(p['compare']['b'], {'start': '2029-07-01', 'end': '2029-09-30'})
        o, p = self.params('What is our PMPM this year vs last year?')
        self.assertEqual(p['compare'], {'a': {'start': '2028-01-01', 'end': '2028-08-15'}, 'b': {'start': '2029-01-01', 'end': '2029-08-15'}})
        o, p = self.params('Why did PMPM go up this year compared with last year?')
        self.assertEqual((p['metric'], p['compare']['a']['start']), ('change', '2028-01-01'))

    def test_ibnr_projection_and_benchmark(self):
        o, p = self.params('VQ-238')
        self.assertEqual((o, p['metric'], p['period']), ('vbc', 'cost', {'start': '2029-06-01', 'end': '2029-08-15'}))
        g, info = compile_q('VQ-237', 'v123')  # projected year-end PMPM: not what the vbc engine reports
        self.assertIsNone(g)
        g, info = compile_q('VQ-050', 'v123')  # compare to a benchmark that is not received: ask for it
        self.assertEqual(info['route'], 'local_requires_input'); self.assertIn('benchmark_pmpm', info['requires_input'][0])

    def test_measures_monitor_followup(self):
        self.assertEqual(self.params('VQ-101'), ('vbc', {'metric': 'quality', 'measure': 'gsd_poor_control', 'group_by': 'practice'}))
        self.assertEqual(self.params('VQ-105'), ('vbc', {'metric': 'quality', 'measure': 'col', 'group_by': 'practice'}))
        g, _ = compile_q('VQ-112', 'v123')  # women 50-74 is not the measure's own age band: never silently dropped
        self.assertIsNone(g)
        o, p = self.params('VQ-152')
        self.assertEqual((o, p['check'], p['stream'], p['group_by']), ('monitor', 'feed', 'adt', 'facility'))
        self.assertEqual(self.params('VQ-146'), ('vbc', {'metric': 'quality', 'measure': 'fuh_7day_followup', 'group_by': 'practice'}))


@unittest.skipUnless(HAVE_BOTH, 'synthetic v1.23 and v2.0 stacks not present')
class NoSteal(unittest.TestCase):
    def test_acceptance_sets_keep_their_engines(self):
        for name in ('v123', 'v20'):
            for f in sorted(glob.glob(str(ROOT / 'bench/acceptance/*.json'))):
                if f.endswith('vbc_library.json'): continue
                for case in json.loads(Path(f).read_text()):
                    g, info = compile_q(case['question'], name)
                    if g and g.get('coverage') == 'unsupported':  # only HCC on a release without ICD-10 diagnoses
                        self.assertEqual((name, case['expect_operation']), ('v123', 'risk_adjustment'), case['question']); continue
                    self.assertIn(op(g), (None, case['expect_operation']), (name, case['question'], info.get('route')))


class Units(unittest.TestCase):
    def test_vbc_groups(self):
        self.assertEqual(vbc._time_key('2029-08', 'quarter'), '2029-Q3')
        self.assertEqual(vbc._time_key('2029-08', 'year'), '2029')
        self.assertEqual(vbc.demographic_key('interpreter', {'interpreter': 1}), 'interpreter needed')
        self.assertEqual(vbc.demographic_key('race_ethnicity', {'race': 'Asian'}), 'Asian / ethnicity not reported')
        self.assertEqual(vbc.validate({'metric': 'cost', 'group_by': 'quarter'})['group_by'], 'quarter')
        with self.assertRaises(ValueError): vbc.validate({'metric': 'quality', 'group_by': 'month'})
        self.assertEqual(vbc.validate({'metric': 'quality', 'measure': 'col', 'group_by': 'race'})['group_by'], 'race')
        self.assertEqual(aggregate.validate({'measure': 'patients', 'group_by': 'language'})['group_by'], 'language')
        self.assertIn('ethnicity', measures.GROUP_BY)

    def test_demographic_groups_follow_the_profile(self):
        none = {'patients': {'fields': {}}, 'streams': {}}
        have = {'patients': {'fields': {'race': {'populated': 10}, 'preferred_language': {'populated': 10}}}, 'streams': {}}
        self.assertEqual(local_compiler._demographic_groups(none), [])
        self.assertEqual({g for _, g in local_compiler._demographic_groups(have)}, {'race', 'ethnicity', 'race_ethnicity', 'language', 'interpreter'})

    def test_coverage_tool_counts_requires_input(self):
        import vbc_question_coverage as cov
        item = {'expected_engine': 'vbc'}
        self.assertEqual(cov.classify(item, {'coverage': 'unsupported', 'nodes': []}, {'route': 'local_requires_input'})[0], 'requires_input')
        self.assertEqual(cov.classify({'expected_engine': 'knowledge'}, {'nodes': [{'operation': 'knowledge', 'parameters': {}}]},
                                      {'route': 'local_compiler'})[0], 'local')


if __name__ == '__main__':
    unittest.main()
