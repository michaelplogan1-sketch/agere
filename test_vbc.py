"""Value-based-care engine on a small hand-checkable store."""
import unittest

from clinical_core.agere.clinical import semantic_layer, vbc, local_compiler, source_profile
from test_aggregate_explain import build, pcp, visit


def claim(pid, day, allowed, paid, status='paid', available=None):
    return (pid, 'medical_claims', {'service_date': day, 'allowed_cents': allowed, 'payer_paid_cents': paid, 'status': status,
                                    'event_date': day, 'available_date': available or day})


class VBC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patients = {'p1': {'age_reported': 70, 'sex_reported': 'F'}, 'p2': {'age_reported': 50, 'sex_reported': 'M'},
                    'p3': {'age_reported': 30, 'sex_reported': 'F'}, 'p4': {'age_reported': 40, 'sex_reported': 'M'}}
        cov = lambda pid, kind, status='active': (pid, 'coverage', {'coverage_type': kind, 'status': status, 'effective_start': '2028-01-01',
                                                                     'event_date': '2028-01-01'})
        recs = [cov('p1', 'medicare'), cov('p2', 'commercial'), cov('p3', 'commercial'), cov('p4', 'uninsured'),
                pcp('p1', 'drA', 'PR1'), pcp('p2', 'drA', 'PR1'), pcp('p3', 'drB', 'PR2'),
                visit('p1', 'primary_care', '2028-03-01'), visit('p1', 'primary_care', '2028-04-01'),
                visit('p3', 'primary_care', '2028-05-01'), visit('p3', 'office', '2028-06-01'),
                visit('p1', 'emergency', '2028-07-01'), visit('p1', 'inpatient', '2028-08-01', 'H1', '2028-08-03'),
                visit('p1', 'primary_care', '2028-08-06'),
                ('p1', 'conditions', {'description': 'Diabetes mellitus type 2', 'event_date': '2027-01-01'}),
                ('p1', 'labs', {'test': 'HbA1c', 'value': 9.6, 'unit': '%', 'event_date': '2028-06-01'}),
                ('p2', 'conditions', {'description': 'Essential hypertension', 'event_date': '2027-01-01'}),
                ('p2', 'vitals', {'blood_pressure_systolic': 128, 'blood_pressure_diastolic': 80, 'event_date': '2028-09-01'}),
                claim('p1', '2028-02-10', 30000, 24000), claim('p2', '2028-03-10', 6000, 4000),
                claim('p2', '2028-04-10', 6000, 0, status='denied'), claim('p4', '2028-05-10', 9999, 9999)]
        cls.path = build(recs, patients)
        semantic_layer.build(cls.path)
        cls.period = {'start': '2028-01-01', 'end': '2028-12-31'}

    def run_metric(self, **p):
        return vbc.execute(self.path, None, {'period': self.period, **p}, '2029-08-15')

    def test_eligibility_excludes_uninsured_and_counts_months(self):
        r = self.run_metric(metric='eligibility')
        self.assertEqual(r['rows'], [{'group': 'all', 'members': 3, 'member_months': 36}])

    def test_attribution_prefers_assigned_pcp_and_reports_agreement(self):
        r = self.run_metric(metric='attribution', group_by='practice')
        rows = {x['group']: x for x in r['rows']}
        self.assertEqual(rows['PR1']['members'], 2)
        self.assertEqual(rows['PR1']['assigned_pcp'], 2)

    def test_cost_pmpm_uses_paid_claims_in_enrolled_months_only(self):
        r = self.run_metric(metric='cost')
        row = r['rows'][0]
        self.assertEqual(row['member_months'], 36)
        self.assertAlmostEqual(row['allowed_pmpm'], round((300 + 60) / 36, 2))  # denied claim and uninsured member excluded
        self.assertAlmostEqual(row['paid_pmpm'], round((240 + 40) / 36, 2))

    def test_utilization_and_follow_up(self):
        row = self.run_metric(metric='utilization')['rows'][0]
        self.assertEqual((row['ed_visits'], row['admissions'], row['followup_7d'], row['discharges_7d_eligible']), (1, 1, 1, 1))
        self.assertAlmostEqual(row['ed_per_1000'], round(1000 / 3, 1))

    def test_quality_measures(self):
        rows = {x['measure']: x for x in self.run_metric(metric='quality')['rows']}
        self.assertEqual((rows['diabetes_a1c_poor_control']['numerator'], rows['diabetes_a1c_poor_control']['denominator']), (1, 1))
        self.assertEqual((rows['bp_control']['numerator'], rows['bp_control']['denominator']), (1, 1))

    def test_shared_savings_arithmetic(self):
        r = self.run_metric(metric='shared_savings', terms={'benchmark_pmpm': 20, 'sharing_rate': 0.5, 'msr': 0.02, 'quality_score': 1.0})
        row = r['rows'][0]
        actual = row['actual_pmpm_completed']
        self.assertEqual(row['outcome'], 'shared_savings_earned')
        self.assertAlmostEqual(row['payment_to_aco'], round((20 - actual) * 36 * 0.5, 2), places=1)
        within = self.run_metric(metric='shared_savings', terms={'benchmark_pmpm': actual * 1.01, 'sharing_rate': 0.5, 'msr': 0.02})
        self.assertEqual(within['rows'][0]['outcome'], 'within_corridor')
        with self.assertRaises(ValueError):
            vbc.validate({'metric': 'shared_savings', 'terms': {'sharing_rate': 0.5}})

    def test_change_decomposition_and_event_type_gap(self):
        r = vbc.execute(self.path, None, {'metric': 'change', 'measure': 'pmpm',
                                          'compare': {'a': {'start': '2028-01-01', 'end': '2028-02-29'},
                                                      'b': {'start': '2028-03-01', 'end': '2028-04-30'}}}, '2029-08-15')
        s = r['rows'][0]
        self.assertAlmostEqual(s['change'], s['rate_effect'] + s['mix_effect'], places=1)
        self.assertGreater(s['value_a'], s['value_b'])  # p1's $300 claim falls in period a

    def test_compiler_routes_vbc_questions_locally(self):
        profile = source_profile.load(self.path)
        g, info = local_compiler.compile('What is our allowed PMPM by payer?', profile, '2029-08-15')
        self.assertEqual(g['nodes'][-1]['parameters'], {'metric': 'cost', 'group_by': 'coverage_type'})
        g, info = local_compiler.compile('What is our projected ACO shared-savings payment under our actual payer contract?', profile, '2029-08-15')
        self.assertEqual(g['coverage'], 'unsupported')
        g, info = local_compiler.compile('What would our shared savings be with a benchmark of $90 PMPM and a sharing rate of 50%?', profile, '2029-08-15')
        self.assertEqual(g['nodes'][-1]['parameters']['terms'], {'benchmark_pmpm': 90.0, 'sharing_rate': 0.5})


if __name__ == '__main__':
    unittest.main()


from pathlib import Path
STACK = Path(__file__).resolve().parent / 'artifacts' / 'agere-healthcare-v123' / 'stack'


class SettlementMath(unittest.TestCase):
    def test_settle_corridor_and_two_sided(self):
        from clinical_core.agere.clinical.vbc import settle
        t = {'sharing_rate': 0.5, 'msr': 0.02, 'two_sided': True, 'loss_rate': 0.4}
        self.assertEqual(settle(99.0, 100.0, 1000, t), 0.0)              # 1% savings: inside the corridor
        self.assertAlmostEqual(settle(90.0, 100.0, 1000, t), 5000.0)     # 10% savings, 50% shared
        self.assertAlmostEqual(settle(110.0, 100.0, 1000, t), -4000.0)   # 10% loss, 40% owed
        self.assertEqual(settle(110.0, 100.0, 1000, {**t, 'two_sided': False}), 0.0)

    @unittest.skipUnless(STACK.exists(), 'synthetic release not present')
    def test_shapley_sums_to_pooled_settlement(self):
        r = vbc.execute(STACK, None, {'metric': 'shared_savings', 'terms': {'benchmark_pmpm': 70, 'sharing_rate': 0.5, 'msr': 0.02,
                                                                           'two_sided': True, 'loss_rate': 0.4}})
        alloc = r['rows'][0]['fair_allocation']
        self.assertAlmostEqual(sum(x['shapley_share'] for x in alloc['allocation']), alloc['pooled_settlement'], delta=1.0)
        risk = r['rows'][0]['settlement_risk']
        self.assertAlmostEqual(risk['p_earn_savings'] + risk['p_owe_loss'] + risk['p_within_corridor'], 1.0, places=2)
