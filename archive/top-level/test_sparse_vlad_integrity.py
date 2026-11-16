"""Independent finite-count checks on authored known risks, no fitted clinical risks."""
import itertools
import math
import unittest
from unittest.mock import patch

import numpy as np
from clinical_core.agere.clinical import monitor as M


def binomial(n,p):
    return np.array([math.comb(n,k)*p**k*(1-p)**(n-k) for k in range(n+1)])


def enumerate_counts(p):
    result=np.zeros(len(p)+1)
    for y in itertools.product((0,1),repeat=len(p)):
        result[sum(y)]+=math.prod(risk if event else 1-risk for event,risk in zip(y,p))
    return result


def chart(p,y):
    return M.ra_chart('authored-unit',[(f'2026-06-{i+1:02d}',str(i)) for i in range(len(p))],p,y,{'chart':'vlad'}, {})


class FiniteVlad(unittest.TestCase):
    def test_rare30_one_event_is_not_a998_percent_signal(self):
        p=np.full(30,.001);y=np.zeros(30);y[-1]=1
        v=M.vlad(p,y);c=chart(p,y)
        self.assertLess(v['z'][-1],-3.09) # descriptive residual is unchanged in meaning
        self.assertEqual(v['count_intervals']['0.998'][-1].tolist(),[0,1])
        self.assertFalse(v['outside']['0.998'][-1])
        self.assertEqual(c['signals'],[])
        self.assertAlmostEqual(c['vlad']['final_fixed_look_tail_p'],2*(1-.999**30),places=13)
        self.assertEqual(c['vlad']['path_columns'][3:],['lower_95','upper_95','lower_998','upper_998'])

    def test_sparse_and_dense_fixed_look_coverage_uses_independent_binomial_tails(self):
        for n,p in [(30,.001),(30,.005),(30,.99),(200,.5),(80,.05)]:
            v=M.vlad([p]*n,[0]*n);pmf=binomial(n,p)
            np.testing.assert_allclose(v['final_count_pmf'],pmf,atol=2e-14)
            for level in M.VLAD_LEVELS:
                lo,hi=v['count_intervals'][str(level)][-1]
                outside=pmf[:lo].sum()+pmf[hi+1:].sum()
                self.assertLessEqual(outside,1-level+2e-13,(n,p,level,outside))

    def test_heterogeneous_distribution_matches_all256_independent_outcomes(self):
        p=[.001,.01,.05,.2,.4,.6,.8,.99]
        pmf=enumerate_counts(p);v=M.vlad(p,[0]*8)
        np.testing.assert_allclose(v['final_count_pmf'],pmf,atol=2e-15)
        for level in M.VLAD_LEVELS:
            lo,hi=v['count_intervals'][str(level)][-1]
            self.assertLessEqual(pmf[:lo].sum()+pmf[hi+1:].sum(),1-level+1e-14)
        mean=sum(i*x for i,x in enumerate(pmf))
        var=sum((i-mean)**2*x for i,x in enumerate(pmf))
        self.assertAlmostEqual(mean,sum(p))
        self.assertAlmostEqual(var,sum(x*(1-x) for x in p))

    def test_permutation_preserves_final_distribution(self):
        p=np.array([.001,.9,.01,.75,.1,.4,.03,.5])
        a=M.vlad(p,[0]*len(p));b=M.vlad(p[::-1],[0]*len(p))
        np.testing.assert_allclose(a['final_count_pmf'],b['final_count_pmf'],atol=2e-16)
        for level in M.VLAD_LEVELS:
            np.testing.assert_array_equal(a['count_intervals'][str(level)][-1],b['count_intervals'][str(level)][-1])

    def test_complement_symmetry_in_count_tails_and_standardized_diagnostics(self):
        p=np.array([.001,.03,.25,.7,.99,.4,.8,.1]);y=np.array([0,0,0,1,1,0,1,0])
        a=M.vlad(p,y);b=M.vlad(1-p,1-y)
        np.testing.assert_allclose(a['cum'],-b['cum'],atol=2e-15)
        np.testing.assert_allclose(a['z'],-b['z'],atol=2e-13)
        np.testing.assert_allclose(a['fixed_look_tail_p'],b['fixed_look_tail_p'],atol=2e-15)
        np.testing.assert_allclose(a['final_count_pmf'],b['final_count_pmf'][::-1],atol=2e-16)

    def test_no_event_and_a_real_two_event_tail_remain_distinct(self):
        p=np.full(30,.001)
        self.assertEqual(chart(p,np.zeros(30))['signals'],[])
        y=np.zeros(30);y[-2:]=1
        c=chart(p,y)
        self.assertTrue(any(s['direction']=='deterioration' for s in c['signals']))
        final=c['signals'][-1]
        expected=2*binomial(30,.001)[2:].sum()
        self.assertAlmostEqual(final['fixed_look_tail_p'],expected,places=14)
        self.assertIn('not a Gaussian',final['z_kind'])

    def test_dense_limits_remain_reasonably_close_to_normal_approximation(self):
        v=M.vlad([.5]*200,[0]*200)
        for level,z in zip(M.VLAD_LEVELS,M.VLAD_Z):
            lo,hi=v['bands'][str(level)][-1]
            self.assertAlmostEqual(lo,-z*v['sd'][-1],delta=1.1)
            self.assertAlmostEqual(hi,z*v['sd'][-1],delta=1.1)
        self.assertIn('diagnostic only',v['limits_kind'])

    def test_deterministic_risks_and_empty_input_are_supported(self):
        v=M.vlad([0.,1.,0.,1.],[0,1,0,1])
        np.testing.assert_allclose(v['cum'],0)
        self.assertEqual(v['count_intervals']['0.998'][-1].tolist(),[2,2])
        self.assertEqual(v['final_count_pmf'].tolist(),[0,0,1,0,0])
        self.assertEqual(len(M.vlad([],[])['cum']),0)

    def test_invalid_risks_and_nonbinary_outcomes_are_explicit(self):
        for p,y in [([float('nan')],[0]),([1.1],[0]),([-.1],[0]),([.1],[.5]),([.1,.2],[0])]:
            with self.assertRaises(ValueError):M.vlad(p,y)

    def test_rounding_and_integer_boundary_do_not_create_a_signal(self):
        p=np.full(30,.001);y=np.zeros(30);y[-1]=1
        v=M.vlad(p,y)
        self.assertEqual(int(v['observed_counts'][-1]),int(v['count_intervals']['0.998'][-1,1]))
        self.assertEqual(chart(p,y)['signals'],[])

    def test_qualification_distinguishes_fixed_look_and_actual_fitted_risks(self):
        q=M.vlad([.001]*30,[0]*30)['qualification']
        self.assertTrue(q['known_independent_risks_fixed_look'])
        for field in ['fitted_risk','dependence','repeated_looks','multiple_units','clinical']:
            self.assertFalse(q[field])
        self.assertNotIn('exact for known risks', ' '.join(M.RA_ASSUMPTIONS))

    def test_native_report_uses_finite_tail_bits_and_explicit_limits(self):
        from test_risk_outcomes import fixture,AS_OF
        import shutil
        root=fixture()
        try:
            # No clinical risk fit/inference; authored fixed probabilities on the native received-fixture reader.
            with patch.object(M,'expected_risks',side_effect=lambda cov,y,adj:(np.full(len(y),.001),'authored fixed risks')):
                report=M.execute(root,None,{'chart':'vlad','unit':'none','min_cases':10},AS_OF)
            self.assertEqual(report['status'],'answered')
            self.assertTrue(report['vlad_limits']['fixed_look_only'])
            self.assertFalse(report['vlad_limits']['fitted_risk_qualified'])
            self.assertTrue(report['rows'])
            for flag in report['rows']:
                signal=next(s for s in report['signals'] if s['direction'] in flag['category'])
                self.assertAlmostEqual(flag['surprise_bits'],-math.log2(signal['fixed_look_tail_p']))
                self.assertFalse(flag['calibration_qualified'])
            self.assertIn('VLAD',M.summarize(report)[0])
        finally:shutil.rmtree(root,ignore_errors=True)


if __name__=='__main__':unittest.main()
