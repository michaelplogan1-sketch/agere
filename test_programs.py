"""VBC program rulebook: cited, versioned program terms the engines execute (synthetic data only)."""
import json, unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.clinical import programs, vbc, local_compiler, source_profile, analysis_graph
from clinical_core.agere.clinical import operator_router as router

ROOT = Path(__file__).resolve().parent
AS_OF = '2029-08-15'


def cites(res, name):
    leaf = res['parameters'][name]
    return [res['doc']['citations'][c]['url'] for c in leaf['citations']]


class Rulebook(unittest.TestCase):
    def test_every_file_is_valid_and_every_parameter_cited(self):
        self.assertEqual(programs.validate_all(), {})
        docs = programs.load()
        self.assertGreaterEqual(len(docs), 12)
        schema = json.loads((programs.ROOT / '_schema.json').read_text())
        for doc in docs.values():
            for field in schema['required']: self.assertIn(field, doc, doc['_file'])
            groups = [doc['parameters']] + [v.get('parameters') or {} for v in (doc.get('variants') or {}).values()]
            for params in groups:
                for name, leaf in programs._leaves(params):
                    self.assertTrue(leaf['citations'], f"{doc['_file']} {name}")
                    for cid in leaf['citations']:
                        c = doc['citations'][cid]
                        self.assertTrue(c['url'].startswith('https://') and c['retrieved'] and c['section'] and c['applies_to'])
            if doc['kind'] != 'typical_range':  # government programs cite government sources
                self.assertTrue(all(programs._is_primary(c['url']) for c in doc['citations'].values()), doc['_file'])

    def test_validator_catches_uncited_and_bad_settlement(self):
        doc = json.loads((programs.ROOT / 'mssp_enhanced.json').read_text())
        doc['parameters']['sharing_rate']['citations'] = []
        doc['settlement']['made_up'] = '$nothing'
        errs = programs.validate_program(doc)
        self.assertTrue(any('sharing_rate has no citation' in e for e in errs))
        self.assertTrue(any('made_up' in e for e in errs))
        typical = json.loads((programs.ROOT / 'commercial_shared_savings_typical.json').read_text())
        typical['settlement'] = {'sharing_rate': '$provider_sharing_rate_most_common'}
        self.assertTrue(any('never resolve' in e for e in programs.validate_program(typical)))

    def test_excerpts_are_short(self):
        for doc in programs.load().values():
            for c in doc['citations'].values():
                self.assertLessEqual(len(c.get('excerpt', '').split()), 25)


class KnownValues(unittest.TestCase):
    def test_mssp_basic_level_e(self):
        res = programs.resolve('MSSP BASIC level E 2026')
        self.assertEqual((res['id'], res['variant'], res['year']), ('mssp_basic', 'E', 2026))
        t = programs.contract_terms(res)
        self.assertEqual((t['sharing_rate'], t['savings_cap_pct'], t['loss_rate'], t['loss_cap_revenue_pct'], t['loss_cap_pct'], t['two_sided']),
                         (0.5, 0.10, 0.30, 0.08, 0.04, True))
        self.assertIn('section-425.605', cites(res, 'sharing_rate')[0])
        self.assertTrue(any('414.1415' in u for u in cites(res, 'loss_cap_revenue_pct')))
        a = programs.contract_terms('MSSP BASIC level A')
        self.assertEqual((a['sharing_rate'], a['two_sided']), (0.4, False))
        self.assertNotIn('loss_rate', a)

    def test_mssp_enhanced(self):
        res = programs.resolve('MSSP ENHANCED 2026')
        t = programs.contract_terms(res)
        self.assertEqual((t['sharing_rate'], t['savings_cap_pct'], t['loss_cap_pct'], t['loss_rate'], t['two_sided']), (0.75, 0.20, 0.15, 0.40, True))
        self.assertIn('section-425.610', cites(res, 'loss_cap_pct')[0])
        # sliding scale: a lower quality score raises the loss rate (1 - 0.75 x q), floor 40% ceiling 75%
        self.assertAlmostEqual(programs.contract_terms(res, {'quality_score': 0.5})['loss_rate'], 0.625)
        self.assertEqual(programs.contract_terms(res, {'quality_score': 0.0})['loss_rate'], 0.75)
        self.assertIn('ecfr.gov', t['program']['sources'][0]['url'])

    def test_variable_msr_schedule(self):
        sched = programs.value(programs.resolve('MSSP ENHANCED'), 'msr_schedule')
        self.assertAlmostEqual(programs.variable_msr(5333, sched), 0.038, places=4)  # CMS worked example
        self.assertEqual(programs.variable_msr(60000, sched), 0.02)
        self.assertEqual(programs.variable_msr(120, sched), 0.122)
        self.assertAlmostEqual(programs.variable_msr(10000, sched), 0.03)

    def test_v28_phase_in_normalization_and_coding_intensity(self):
        self.assertEqual(programs.hcc_blend(2024), {'V24': 0.67, 'V28': 0.33})
        self.assertEqual(programs.hcc_blend(2025), {'V24': 0.33, 'V28': 0.67})
        self.assertEqual(programs.hcc_blend(2026)['V28'], 1.0)
        self.assertEqual(programs.hcc_blend(2027)['V28'], 1.0)
        self.assertEqual(programs.normalization_factor(2026, 'V28'), 1.067)
        self.assertEqual(programs.normalization_factor(2025, 'V24'), 1.153)
        self.assertEqual(programs.coding_intensity(2027), 0.059)
        expected = (0.33 * 1.2 / 1.153 + 0.67 * 1.1 / 1.045) * (1 - 0.059)
        self.assertAlmostEqual(programs.payment_risk_score({'V24': 1.2, 'V28': 1.1}, 2025), round(expected, 4))
        self.assertAlmostEqual(programs.payment_risk_score({'V28': 1.0}, 2026), round(1 / 1.067 * 0.941, 4))
        with self.assertRaises(ValueError): programs.hcc_blend(2023)
        doc = programs.load()['ma_risk_adjustment']
        urls = {doc['citations'][c]['url'] for c in doc['parameters']['model_blend']['citations']}
        self.assertIn('https://www.cms.gov/files/document/2025-announcement.pdf', urls)

    def test_stars_qbp_and_rebates(self):
        self.assertEqual(programs.qbp_bonus_pct(4.0), 0.05)
        self.assertEqual(programs.qbp_bonus_pct(4.5, double_bonus_county=True), 0.10)
        self.assertEqual(programs.qbp_bonus_pct(3.5), 0.0)
        self.assertEqual([programs.rebate_pct(s) for s in (5, 4.5, 4.0, 3.5, 3.0)], [0.70, 0.70, 0.65, 0.65, 0.50])

    def test_reach_by_year(self):
        g26, g25 = programs.contract_terms('ACO REACH Global 2026'), programs.contract_terms('ACO REACH Global PY2025')
        self.assertEqual((g26['discount_pct'], g26['quality_withhold_pct'], g26['corridors'][0]['to']), (0.04, 0.05, 0.10))
        self.assertEqual((g25['discount_pct'], g25['quality_withhold_pct'], g25['corridors'][0]['to']), (0.035, 0.02, 0.25))
        p = programs.contract_terms('ACO REACH Professional 2026')
        self.assertEqual((p['discount_pct'], p['corridors'][0]['rate']), (0.0, 0.5))
        with self.assertRaises(ValueError): programs.resolve('ACO REACH Global 2024')
        with self.assertRaisesRegex(ValueError, 'needs supplied option'): programs.resolve('ACO REACH')

    def test_reference_entries_never_settle(self):
        for name in ('commercial_shared_savings_typical', 'medicaid_vbp_typical', 'ma_star_ratings', 'episode_models'):
            with self.assertRaises(ValueError): programs.contract_terms(name)
        with self.assertRaisesRegex(ValueError, 'needs supplied program track'): programs.contract_terms('MSSP')


class Settlement(unittest.TestCase):
    def test_settle_with_mssp_enhanced_terms(self):
        t = programs.contract_terms('MSSP ENHANCED 2026', {'msr': 0.02})
        self.assertEqual(t['mlr'], 0.02)
        self.assertAlmostEqual(vbc.settle(90, 100, 1000, t), 7500.0)     # 10% savings x 75%
        self.assertEqual(vbc.settle(99, 100, 1000, t), 0.0)              # inside the 2% corridor
        self.assertAlmostEqual(vbc.settle(120, 100, 1000, t), -8000.0)   # 20% loss x 40%
        self.assertAlmostEqual(vbc.settle(150, 100, 1000, t), -15000.0)  # loss limit 15% of benchmark
        self.assertAlmostEqual(vbc.settle(50, 100, 1000, t), 20000.0)    # savings cap 20% of benchmark

    def test_settle_with_basic_level_e_revenue_limit(self):
        t = programs.contract_terms('MSSP BASIC level E', {'msr': 0.0})
        self.assertAlmostEqual(vbc.settle(110, 100, 1000, t), -3000.0)   # 10% loss x 30%
        self.assertAlmostEqual(vbc.settle(200, 100, 1000, t), -4000.0)   # 4% of benchmark cap
        self.assertAlmostEqual(vbc.settle(200, 100, 1000, {**t, 'participant_revenue': 25000}), -2000.0)  # 8% of revenue
        a = programs.contract_terms('MSSP BASIC level A', {'msr': 0.02})
        self.assertEqual(vbc.settle(150, 100, 1000, a), 0.0)             # one-sided

    def test_settle_with_reach_corridors_discount_and_withhold(self):
        t = programs.contract_terms('ACO REACH Global 2026')
        # benchmark 100 -> 96 after the 4% discount (withhold fully earned); 20% savings: 10% at 100% + 10% at 50%
        self.assertAlmostEqual(vbc.settle(76.8, 100, 1000, t), 96 * 1000 * 0.15, places=6)
        self.assertAlmostEqual(vbc.effective_benchmark(100, {**t, 'quality_score': 0.6}), 94.0)
        p = programs.contract_terms('ACO REACH Professional 2026')
        self.assertAlmostEqual(vbc.settle(110, 100, 1000, p), -(0.05 * 0.5 + 0.05 * 0.35) * 100 * 1000)

    def test_plain_terms_unchanged(self):
        t = {'sharing_rate': 0.5, 'msr': 0.02, 'two_sided': True, 'loss_rate': 0.4}
        self.assertEqual((vbc.settle(99, 100, 1000, t), vbc.settle(90, 100, 1000, t), vbc.settle(110, 100, 1000, t)), (0.0, 5000.0, -4000.0))

    def test_execute_with_program_cites_the_rule(self):
        import test_vbc
        test_vbc.VBC.setUpClass()
        r = vbc.execute(test_vbc.VBC.path, None, {'metric': 'shared_savings', 'period': {'start': '2028-01-01', 'end': '2028-12-31'},
                                                  'program': 'MSSP ENHANCED 2026', 'terms': {'benchmark_pmpm': 20}}, AS_OF)
        row = r['rows'][0]
        self.assertEqual(row['terms']['msr'], 0.122)  # 3 members -> smallest band of the variable MSR schedule
        self.assertEqual(row['terms']['sharing_rate'], 0.75)
        self.assertIn('MSSP ENHANCED', r['method'])
        self.assertIn('ecfr.gov', r['method'])
        self.assertEqual(row['outcome'], 'shared_savings_earned')
        self.assertAlmostEqual(row['payment_to_aco'], min((20 - row['actual_pmpm_completed']) * row['member_months'] * 0.75,
                                                          20 * row['member_months'] * 0.20), places=1)
        with self.assertRaisesRegex(ValueError, r'needs supplied contract terms: benchmark_pmpm \(MSSP ENHANCED'):
            vbc.validate({'metric': 'shared_savings', 'program': 'MSSP ENHANCED 2026'})


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_vbc
        test_vbc.VBC.setUpClass()
        cls.profile = source_profile.load(test_vbc.VBC.path)

    def plan(self, q):
        return local_compiler.compile(q, self.profile, AS_OF)

    def test_acceptance_phrasings(self):
        cases = json.loads((ROOT / 'bench' / 'acceptance' / 'programs.json').read_text())
        self.assertEqual(len(cases), 8)
        for case in cases:
            g, info = self.plan(case['question'])
            self.assertIsNotNone(g, (case['question'], info))
            self.assertEqual([n['operation'] for n in g['nodes']], [case['expect_operation']], (case['question'], info))
            terms = g['nodes'][-1]['parameters']['terms']
            self.assertEqual(terms['program']['id'], case['expect_program'], case['question'])
            analysis_graph.validate(g, AS_OF)

    def test_program_phrasings_carry_program_terms(self):
        expect = {
            'What would we earn under MSSP ENHANCED with a $1,000 benchmark?': ('MSSP ENHANCED', 1000.0, 0.75),
            'Under MSSP BASIC level E with a $950 PMPM benchmark, what is our shared savings?': ('MSSP BASIC E', 950.0, 0.5),
            'What would our payment be under MSSP Level A with a $900 benchmark?': ('MSSP BASIC A', 900.0, 0.4),
            'Shared savings under the ENHANCED track for 2025 with a $980 PMPM benchmark': ('MSSP ENHANCED', 980.0, 0.75),
            'How much would we owe or earn in the MSSP basic track level C with a $1,000 benchmark?': ('MSSP BASIC C', 1000.0, 0.5),
            'With a $1,000 PMPM benchmark, how much shared savings would we earn under MSSP ENHANCED with a 2% MSR?': ('MSSP ENHANCED', 1000.0, 0.75),
        }
        for q, (label, bench, rate) in expect.items():
            g, info = self.plan(q)
            t = g['nodes'][-1]['parameters']['terms']
            self.assertEqual((t['program']['label'], t['benchmark_pmpm'], t['sharing_rate']), (label, bench, rate), q)
        g, _ = self.plan('Shared savings under the ENHANCED track for 2025 with a $980 PMPM benchmark')
        self.assertEqual(g['nodes'][-1]['parameters']['terms']['program']['year'], 2025)
        g, _ = self.plan('With a $1,000 PMPM benchmark, how much shared savings would we earn under MSSP ENHANCED with a 2% MSR?')
        t = g['nodes'][-1]['parameters']['terms']
        self.assertEqual((t['msr'], t['mlr']), (0.02, 0.02))
        self.assertNotIn('msr_schedule', t)
        g, _ = self.plan('What is our settlement under ACO REACH Global 2026 with a benchmark of $1,100 PMPM?')
        t = g['nodes'][-1]['parameters']['terms']
        self.assertEqual((t['discount_pct'], t['benchmark_pmpm']), (0.04, 1100.0))

    def test_missing_terms_are_named_exactly(self):
        g, _ = self.plan('What would we earn under MSSP ENHANCED?')
        self.assertEqual(g['coverage'], 'unsupported')
        self.assertEqual(len(g['unmet_requirements']), 1)
        self.assertIn('benchmark_pmpm', g['unmet_requirements'][0])
        self.assertNotIn('sharing', g['unmet_requirements'][0].split('supplies')[0])
        g, _ = self.plan('How much shared savings would we get under the MSSP with a $900 benchmark?')
        self.assertEqual(g['unmet_requirements'], ['Missing input: program track: MSSP BASIC level A-E or MSSP ENHANCED'])
        g, _ = self.plan('What would our shared savings be under ACO REACH with a $900 benchmark?')
        self.assertIn('needs supplied option: one of Professional, Global', g['unmet_requirements'][0])
        g, _ = self.plan('What would our shared savings be under a typical commercial ACO contract with a $900 benchmark?')
        self.assertEqual(g['coverage'], 'unsupported')
        self.assertIn('not settlement terms', g['unmet_requirements'][0])
        # questions without a program keep their existing behaviour
        g, _ = self.plan('What would our shared savings be with a benchmark of $90 PMPM and a sharing rate of 50%?')
        self.assertEqual(g['nodes'][-1]['parameters']['terms'], {'benchmark_pmpm': 90.0, 'sharing_rate': 0.5})

    def test_operator_router_names_only_the_missing_terms(self):
        choice = {'name': 'vbc', 'input': {'parameters': {'metric': 'shared_savings', 'group_by': 'none', 'program': 'MSSP ENHANCED 2026'}}}
        with mock.patch.object(router, '_ask', side_effect=lambda *a, op=None, **k: (({'name': 'vbc', 'input': {}} if op is None else choice), {'api_usd': 0.0})):
            graph, info = router.route('what would we make in the enhanced track', AS_OF, None, None, analysis_graph.validate)
        self.assertIsNone(graph)
        self.assertIn('benchmark_pmpm', info['requires_input'])
        self.assertNotIn('sharing_rate', info['requires_input'].split('(')[0])
        choice['input']['parameters']['terms'] = {'benchmark_pmpm': 1000}
        with mock.patch.object(router, '_ask', side_effect=lambda *a, op=None, **k: (({'name': 'vbc', 'input': {}} if op is None else choice), {'api_usd': 0.0})):
            graph, info = router.route('what would we make in the enhanced track with a 1000 benchmark', AS_OF, None, None, analysis_graph.validate)
        terms = graph['nodes'][-1]['parameters']['terms']
        self.assertEqual((terms['sharing_rate'], terms['program']['label']), (0.75, 'MSSP ENHANCED'))
        self.assertIn('program', router.tool('vbc')['input_schema']['properties']['parameters']['properties'])


if __name__ == '__main__':
    unittest.main()
