"""Authored mathematical alarm-spacing and qualification contracts; no saved detector verdicts or clinical inputs."""
import copy
import json
import math
import statistics
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import monitor as M, changepoint_qualification as C


def exact_walk_arl(levels, up_probability):
    # First-step equations for a reflected +/- one walk, absorbed at levels.
    total=0.; difference=0.
    for _ in range(levels):
        difference=(1+(1-up_probability)*difference)/up_probability;total+=difference
    return total


def row(method, delay, rate=.9, n=1200):
    detections=[1]*round(rate*n)+[0]*(n-round(rate*n))
    loss=[delay/25 if d else 1. for d in detections]
    metrics={'false_alarm_rate':.02,'zero_gap_false_alarm_rate':.01,'missing_batch_false_alarm_rate':.01,
             'gap_false_alarm_rate':.01,'lag_false_alarm_rate':.03,'alarm_before_change_rate':.01,'detection_rate':rate}
    counts={k:{'events':round(v*n),'n':n} for k,v in metrics.items()}
    counts['gap_false_alarm_rate']={'events':2*round(.01*n),'n':2*n}
    return {'method':method,'variant':'gap_aware','threshold':3.,'median_delay_weeks':delay,
            **metrics,'scenario_counts':counts,
            'evaluation_identity':'same authored independent shift trials','shift_loss_samples':loss,
            'shift_detection_samples':detections,'miss_adjusted_delay_loss':statistics.mean(loss)}


class AlarmCalibration(unittest.TestCase):
    def test_attainable_boundary_replaces_fictional_interpolated_arl(self):
        r=M.simulate_control_limit([1/3],4.,100,seed=7,n_chains=64,length=600)
        level=math.ceil(r['h']/math.log(2));truth=exact_walk_arl(level,1/3)
        self.assertEqual(level,5);self.assertAlmostEqual(truth,171)
        self.assertAlmostEqual(r['arl0_simulated']/truth,1.,delta=.2)
        self.assertNotEqual(r['arl0_simulated'],100)
        self.assertGreater(r['validation']['signals'],0);self.assertIsNotNone(r['arl0_ci95_approx'][0])
        self.assertFalse(r['calibration_qualified']);json.dumps(r,allow_nan=False)

    def test_constrained_grid_reports_actual_shorter_spacing_not_requested_target(self):
        r=M.simulate_control_limit([1/3],4.,100,seed=7,n_chains=64,length=600,step=2.771,h_max=2.771)
        self.assertEqual(r['h'],2.771);self.assertAlmostEqual(exact_walk_arl(4,1/3),78.)
        self.assertAlmostEqual(r['arl0_simulated']/78,1.,delta=.2)
        self.assertNotEqual(r['arl0_simulated'],r['arl0_target'])

    def test_improvement_direction_and_threshold_rounding_have_actual_receipts(self):
        r=M.simulate_control_limit([2/3],.25,100,seed=8,n_chains=64,length=600,step=.050004)
        self.assertGreaterEqual(r['h'],r['grid_threshold'])
        level=math.ceil(r['h']/math.log(2));truth=exact_walk_arl(level,1/3)
        self.assertAlmostEqual(r['arl0_simulated']/truth,1.,delta=.25)
        self.assertIn('distinct',r['validation']['first_window_probability_contract'])

    def test_no_alarm_and_censoring_are_unknown_not_exact_infinite_arl(self):
        r=M._restart_arl([0.],2.,20.,8,10,7)
        self.assertIsNone(r['estimate']);self.assertEqual(r['ci95_approx'],[None,None])
        self.assertEqual(r['first_passages_censored'],8);self.assertEqual(r['restricted_first_passage_mean'],10)
        self.assertEqual(r['first_window_any_alarm_rate'],0);json.dumps(r,allow_nan=False)
        sparse=M.simulate_control_limit([1e-6],2.,100,n_chains=16,length=12,step=20.,h_max=20.)
        self.assertIsNone(sparse['arl0_simulated']);self.assertEqual(sparse['arl0_ci95_approx'],[None,None])
        self.assertEqual(sparse['validation']['first_passages_censored'],16)
        self.assertEqual(sparse['target_assessment'],'not_established');json.dumps(sparse,allow_nan=False)


class Qualification(unittest.TestCase):
    def decide(self, candidate=None, reference=None):
        return C.decide([reference or row('cusum',10),candidate or row('bayesian_blocks',5)],.05)

    def test_fast_candidate_with_joint_evidence_can_qualify(self):
        r=self.decide();self.assertEqual(r['qualified_method'],'bayesian_blocks')
        self.assertLess(r['challengers'][0]['loss_difference_upper_bound'],0)
        self.assertTrue(all(v<=.05 for v in r['challengers'][0]['evidence']['false_alert_upper_bounds'].values()))

    def test_each_false_alarm_field_independently_rejects_an_unsafe_candidate(self):
        for field in C.FALSE_ALERT_FIELDS:
            with self.subTest(field=field):
                candidate=row('bayesian_blocks',5);candidate[field]=.9;candidate['scenario_counts'][field]['events']=1080
                r=self.decide(candidate);self.assertIsNone(r['qualified_method']);self.assertFalse(r['challengers'][0]['qualifies'])

    def test_all_invalid_sets_retain_only_an_unqualified_diagnostic_reference(self):
        a=row('cusum',10);b=row('bayesian_blocks',5)
        for r in (a,b):r['lag_false_alarm_rate']=.9;r['scenario_counts']['lag_false_alarm_rate']['events']=1080
        result=self.decide(b,a);self.assertIsNone(result['qualified_method']);self.assertIsNone(result['qualified'])
        self.assertFalse(result['diagnostic_reference']['qualified'])

    def test_sensitivity_collapse_and_slow_candidate_do_not_qualify(self):
        for candidate in [row('bayesian_blocks',5,rate=.01),row('bayesian_blocks',9)]:
            self.assertIsNone(self.decide(candidate)['qualified_method'])

    def test_missing_counts_uncertainty_identity_or_loss_cannot_be_a_certificate(self):
        for field in ['scenario_counts','evaluation_identity','shift_loss_samples','shift_detection_samples','miss_adjusted_delay_loss']:
            with self.subTest(field=field):
                candidate=row('bayesian_blocks',5);candidate.pop(field)
                self.assertIsNone(self.decide(candidate)['qualified_method'])
        candidate=row('bayesian_blocks',5);candidate['evaluation_identity']='different shift trials'
        self.assertIsNone(self.decide(candidate)['qualified_method'])

    def test_small_samples_and_unreconciled_metrics_remain_unqualified(self):
        self.assertIsNone(self.decide(row('bayesian_blocks',5,n=20),row('cusum',10,n=20))['qualified_method'])
        candidate=row('bayesian_blocks',5);candidate['false_alarm_rate']=0.
        self.assertIsNone(self.decide(candidate)['qualified_method'])

    def test_reader_recomputes_selection_and_legacy_labels_cannot_activate_annotations(self):
        legacy={'version':'changepoint-qualification/1','qualified_method':'bayesian_blocks','qualified':{'method':'bayesian_blocks'}}
        self.assertIsNone(C.summary(legacy)['qualified']);self.assertIsNone(C.chart_signals([],doc=legacy))
        rows=[row('cusum',10),row('bayesian_blocks',5)];doc={'version':C.VERSION,'false_alert_budget':.05,'results':rows,**C.decide(rows,.05)}
        doc['qualified_method']='ewma';doc['qualified']={'method':'ewma'}
        self.assertEqual(C.summary(doc)['qualified_method'],'bayesian_blocks')
        self.assertIn('paired miss-adjusted',C.note(doc))

    def test_score_records_misses_counts_and_actual_threshold(self):
        sets={k:[{'counts':[1]*10,'change':5 if k=='shift' else None}] for k in ['null_cal','null_test','gap','batch','lag','shift']}
        with patch.object(C,'paths',return_value={5:0.,6:0.}):
            r=C.score('cusum','naive',sets,.05)
        self.assertEqual(r['scenario_counts']['detection_rate'],{'events':0,'n':1})
        self.assertEqual(r['shift_loss_samples'],[1.]);self.assertEqual(r['shift_detection_samples'],[0])
        self.assertTrue(r['evaluation_identity'])


if __name__=='__main__':unittest.main()
