"""Finite empirical tail mass, including payout atoms and downstream utility."""
from fractions import Fraction
import unittest
from unittest.mock import patch
import numpy as np
from clinical_core.agere.clinical import contract_strategy as C


def exact_tail(net,confidence):
    losses=sorted([-Fraction(str(float(x))) for x in net],reverse=True)
    remaining=mass=len(losses)*(1-Fraction(str(confidence)));total=Fraction(0)
    for loss in losses:
        take=min(remaining,Fraction(1));total+=loss*take;remaining-=take
        if not remaining:break
    return max(0.,float(total/mass))


def ru_tail(net,confidence):
    losses=[-Fraction(str(float(x))) for x in net]
    mass=len(losses)*(1-Fraction(str(confidence)))
    return max(0.,float(min(v+sum(max(loss-v,0) for loss in losses)/mass for v in set(losses))))


def fixture():
    costs=np.array([200]*10+[101]*490+[90]*500,dtype=float)
    options={'two_sided':{'sharing_rate':1.,'two_sided':True,'loss_rate':1.,'msr':.02,'mlr':.02},
             'one_sided':{'sharing_rate':.4,'two_sided':False,'msr':.02}}
    return costs,options


class TailMass(unittest.TestCase):
    def check(self,net,confidence):
        a,b=exact_tail(net,confidence),ru_tail(net,confidence)
        self.assertAlmostEqual(a,b);self.assertAlmostEqual(C.risk_measures(net,confidence)[1],a)

    def test_rare_loss_at_zero_atom(self):
        net=np.array([-100]+[0]*99,dtype=float);self.check(net,.95)
        self.assertAlmostEqual(C.risk_measures(net,.95)[1],20)

    def test_fractional_scenario_boundary(self):
        self.check([-50,-40,-30,-20,-10,0,10],.8)
        self.assertAlmostEqual(C.risk_measures([-50,-40,-30,-20,-10,0,10],.8)[1],330/7)

    def test_tied_capped_and_constant_scenarios(self):
        for net in [[-7]*20,[0]*20,[3]*20,[-10]*4+[0]*30+[10]*66,[-10,-10,-5,-5,0,1,1,8]]:
            for confidence in (.5,.8,.95,.999):
                with self.subTest(net=net[:4],confidence=confidence):self.check(net,confidence)

    def test_untied_integral_tail_and_var_convention(self):
        net=np.arange(-99,101,dtype=float);var,cvar=C.risk_measures(net,.95)
        self.assertAlmostEqual(cvar,94.5);self.assertEqual(var,max(0.,-float(np.quantile(net,.05))))

    def test_pointwise_improvement_cannot_raise_tail_loss(self):
        old=np.array([-100]+[0]*99,dtype=float);better=np.array([-100]+[0]*3+[.5]+[1]*95)
        self.assertTrue(np.all(better>=old));self.assertLess(C.risk_measures(better,.95)[1],C.risk_measures(old,.95)[1])
        self.check(better,.95)

    def test_scale_replication_permutation_and_subscenario_tail(self):
        net=np.array([-50,-40,-30,-20,-10,0,10]);self.check(net,.999)
        risk=C.risk_measures(net,.8)[1]
        self.assertAlmostEqual(C.risk_measures(net*10,.8)[1],risk*10)
        self.assertAlmostEqual(C.risk_measures(np.repeat(net,3),.8)[1],risk)
        self.assertEqual(C.risk_measures(net[::-1],.8),C.risk_measures(net,.8))

    def test_bad_inputs_are_explicit(self):
        for values,confidence in [([],.95),([0,float('nan')],.95),([[1,2]],.95),([0],1),([0],0),([0],True)]:
            with self.assertRaises(ValueError):C.risk_measures(values,confidence)


class ContractUtility(unittest.TestCase):
    def test_native_payouts_means_and_ranking_on_common_draws(self):
        costs,options=fixture();stats={}
        for name,terms in options.items():
            stats[name],pay=C.value(costs,100.,1.,terms)
            self.assertAlmostEqual(stats[name]['cvar'],exact_tail(pay,.95))
        self.assertEqual(stats['two_sided']['expected_net'],4.)
        self.assertAlmostEqual(stats['two_sided']['cvar'],20.)
        self.assertAlmostEqual(stats['two_sided']['risk_adjusted_value'],-6.)
        self.assertEqual(stats['one_sided']['risk_adjusted_value'],2.)
        self.assertEqual(sorted(stats,key=lambda k:-stats[k]['risk_adjusted_value']),['one_sided','two_sided'])

    def test_bargaining_utility_uses_corrected_tail(self):
        costs,options=fixture();sim=C.Simulation.from_draws(costs)
        cp={'sharing_rate_min':1.,'sharing_rate_max':1.,'msr_min':.02,'msr_max':.02}
        with patch.object(C,'typical_bounds',return_value={'label':'authored','sources':[]}):
            report=C.negotiate(sim,options['two_sided'],100.,0.,{'confidence':.95,'risk_aversion':.5},cp)
        self.assertAlmostEqual(report['surface'][0]['aco_value'],-6.)
        self.assertEqual(report['surface'][0]['aco_expected_net'],4.)
        self.assertIsNone(report['nash']);self.assertFalse(report['agreement_zone'])

    def test_break_even_and_var_capacity_keep_common_metric(self):
        costs,options=fixture();sim=C.Simulation.from_draws(costs);p={'confidence':.95,'risk_aversion':.5}
        flips=C.break_even(sim,options['one_sided'],options['two_sided'],100.,0.,p)
        b=flips['benchmark_pmpm'];self.assertIsNotNone(b)
        def diff(bench):
            return C._evaluate(sim,options['one_sided'],bench,0.,p)[0]['risk_adjusted_value']-C._evaluate(sim,options['two_sided'],bench,0.,p)[0]['risk_adjusted_value']
        self.assertLessEqual(diff(b-.01)*diff(b+.01),0)
        # Capacity intentionally remains VaR-based rather than silently changing its contract.
        self.assertEqual(C.max_loss_rate(sim,options['two_sided'],100.,0.,p,0.),1.)

