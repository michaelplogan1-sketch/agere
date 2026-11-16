"""Fixed global reference intervals; scoped evaluation on authored prediction pairs."""
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from clinical_core.agere.clinical import calibration as C,conformal as F,vbc as V,outreach as O,trajectory as T,analysis_graph as G

ASOF='2026-09-30';LIVE='2025-09-30';REFERENCE='2024-09-30'


def pairs(origin,earlier_n=400):
    return [{'patient_id':f'p{i:03d}','pred':100.,'y':200. if origin==LIVE and i<30 else 110.,'group':'65+'}
            for i in range(400 if origin==LIVE else earlier_n)]


class CalibrationPopulation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.dbfile=self.root/'semantic.sqlite3'
        (self.root/'run.json').write_text(json.dumps({'source_digest':'authored-calibration','snapshot':{'as_of':ASOF}}))
        db=sqlite3.connect(self.dbfile)
        db.execute('CREATE TABLE meta(key,value)')
        db.executemany('INSERT INTO meta VALUES (?,?)',[('as_of',ASOF),('source_digest','authored-calibration'),('version','authored/1')]);db.commit();db.close()
        self.db=sqlite3.connect(self.dbfile);self.addCleanup(self.db.close)
        self.affected=[f'p{i:03d}' for i in range(30)]
        self.unaffected=[f'p{i:03d}' for i in range(30,400)]
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.risk=self.stack.enter_context(patch.object(V,'_risk_pairs',side_effect=lambda db,origin,group_by:(pairs(origin),{},0.)))
        self.util=self.stack.enter_context(patch.object(O,'_utilization_pairs',side_effect=lambda db,origin:pairs(origin)))
        self.stack.enter_context(patch.object(O,'_calibration_origins',return_value=(LIVE,REFERENCE,[])))
        self.stack.enter_context(patch.object(C.semantic_layer,'ensure',return_value=self.dbfile))

    def report(self,ids=None,target='risk_scores',**kwargs):
        return C.execute(self.root,ids,{'target':target,**kwargs},ASOF)

    def test_both_cost_targets_evaluate_global_affected_unaffected_and_empty(self):
        for target in ['risk_scores','outreach_utilization']:
            for ids,n,coverage in [(None,400,.925),(self.affected,30,0.),(self.unaffected,370,1.),([],0,None)]:
                with self.subTest(target=target,n=n):
                    report=self.report(ids,target);row=report['rows'][0]
                    self.assertEqual(row['evaluation_cases'],n)
                    self.assertEqual(row['backtest_coverage'],coverage)
                    self.assertEqual(row['calibration_cases'],400)
                    self.assertEqual(row['reference_calibration_population'],'global')
                    self.assertEqual(row['evaluation_origin'],LIVE)
                    self.assertEqual(row['reference_origin'],REFERENCE)
                    self.assertEqual(row['evaluation_population'],F.evaluation_population(ids))
                    self.assertEqual(report['source_binding']['source_digest'],'authored-calibration')
                    json.dumps(report,allow_nan=False)

    def test_reference_tables_stay_fixed_while_only_evaluation_changes(self):
        for func,args in [(V.risk_calibration,(self.db,ASOF,'none')),(O.calibrate_utilization,(self.db,ASOF))]:
            global_=func(*args)
            for ids in [self.affected,self.unaffected,[]]:
                cal=func(*args,patient_ids=ids)
                self.assertEqual(cal['_table'],global_['_table'])
                self.assertEqual(cal['groups'],global_['groups'])
                self.assertEqual(cal['calibration_pairs'],400)
                self.assertEqual(cal['backtest_calibration_pairs'],400)

    def test_selected_and_excluded_denominators_are_separate(self):
        report=self.report(self.affected)
        cal=next(iter(report['receipts'].values()));e=cal['evaluation']
        self.assertEqual(e['available_pairs'],400)
        self.assertEqual(e['selected_pairs'],30);self.assertEqual(e['evaluable_pairs'],30)
        self.assertEqual(e['covered_pairs'],0);self.assertEqual(e['excluded_outside_population'],370)
        self.assertEqual(e['excluded_nonestimable_pairs'],0)
        self.assertEqual(e['requested_without_evaluable_pair'],0)
        self.assertEqual(cal['backtest_by_group'],{'65+':{'cases':30,'coverage':0.}})
        self.assertIn('30 evaluable pairs',report['reading']);self.assertIn('0.0%',report['reading'])

    def test_empty_is_not_omitted_or_reported_as_zero_coverage(self):
        empty=self.report([]);global_=self.report()
        self.assertNotEqual(empty['evaluation_population']['membership_identity'],global_['evaluation_population']['membership_identity'])
        row=empty['rows'][0]
        self.assertEqual(row['evaluation_candidates'],0);self.assertEqual(row['evaluation_cases'],0)
        self.assertIsNone(row['backtest_coverage']);self.assertEqual(row['evaluation_status'],'nonestimable')
        self.assertIn('selected cohort of 0',empty['reading'])
        self.assertNotIn('92.5%',empty['reading'])

    def test_missing_selected_members_do_not_become_failures_or_covered_cases(self):
        report=self.report(self.affected+['not-in-pairs'])
        row=report['rows'][0]
        self.assertEqual(row['evaluation_population']['requested_patients'],31)
        self.assertEqual(row['evaluation_cases'],30);self.assertEqual(row['requested_without_evaluable_pair'],1)
        self.assertEqual(row['backtest_coverage'],0.)

    def test_small_selected_diagnostic_is_not_capped_or_called_a_guarantee(self):
        row=self.report(['p000'])['rows'][0]
        self.assertEqual(row['evaluation_cases'],1);self.assertEqual(row['backtest_coverage'],0.)
        self.assertEqual(row['evaluation_status'],'descriptive')
        self.assertIn('unrestricted conditional', ' '.join(self.report(['p000'])['limitations']))

    def test_infinite_or_missing_intervals_are_nonestimable_not100_percent(self):
        data=pairs(LIVE)
        e=F.scoped_interval_coverage(data,np.zeros(400),np.full(400,np.inf),self.affected)
        self.assertIsNone(e['coverage']);self.assertEqual(e['evaluable_pairs'],0)
        self.assertEqual(e['excluded_nonestimable_pairs'],30)
        self.assertEqual(e['requested_without_evaluable_pair'],30)
        self.assertIsNone(F.scoped_interval_coverage(data,patient_ids=self.affected)['coverage'])

    def test_high_level_with_insufficient_reference_count_keeps_unknown(self):
        with patch.object(V,'_risk_pairs',side_effect=lambda db,origin,group_by:(pairs(origin,20),{},0.)):
            cal=V.risk_calibration(self.db,ASOF,'none',alpha=.01,patient_ids=self.affected)
        self.assertEqual(cal['backtest_calibration_pairs'],20)
        self.assertIsNone(cal['backtest_coverage']);self.assertEqual(cal['backtest_evaluation_pairs'],0)

    def test_nonfinite_selected_outcome_is_excluded_with_its_count(self):
        data=pairs(LIVE);data[0]['y']=float('nan')
        e=F.scoped_interval_coverage(data,np.full(400,90.),np.full(400,110.),self.affected)
        self.assertEqual(e['selected_pairs'],30);self.assertEqual(e['evaluable_pairs'],29)
        self.assertEqual(e['excluded_nonestimable_pairs'],1);self.assertEqual(e['coverage'],0.)
        self.assertEqual(e['requested_without_evaluable_pair'],1)

    def test_unavailable_reference_retains_requested_scope_and_counts(self):
        with patch.object(V,'_risk_pairs',return_value=(pairs(LIVE)[:5],{},0.)):
            cal=V.risk_calibration(self.db,ASOF,'none',patient_ids=self.affected)
        self.assertFalse(cal['available']);self.assertEqual(cal['evaluation']['selected_pairs'],5)
        self.assertEqual(cal['evaluation']['evaluable_pairs'],0)
        self.assertEqual(cal['evaluation']['population']['requested_patients'],30)

    def forecast_controls(self):
        series={r['patient_id']:[(1,1.)] for r in pairs(LIVE)}
        def load(db,concepts,as_of,keep):
            chosen={pid:v for pid,v in series.items() if keep is None or pid in keep}
            return {c:chosen for c in concepts}
        def calibrate(series,concept,snapshot,horizon,threshold,below,*,alpha,keep):
            self.assertEqual(set(series),set(series) if keep is None else set(series)&keep)
            return {'available':False,'level':1-alpha,'statement':'Authored forecast-boundary control; no predictor ran',
                    'backtest_evaluation_pairs':len(series),'backtest_calibration_pairs':len(series)}
        return patch.object(T,'load_series',side_effect=load),patch.object(T,'calibrate',side_effect=calibrate)

    def test_mixed_report_retains_scope_for_every_target(self):
        a,b=self.forecast_controls()
        with a,b:
            for ids,n in [(None,400),(self.affected,30),([],0)]:
                report=self.report(ids,'all')
                self.assertEqual(len(report['rows']),5)
                for row in report['rows']:
                    self.assertEqual(row['evaluation_population'],F.evaluation_population(ids))
                    self.assertEqual(row['evaluation_candidates'],n)
                    self.assertIsNotNone(row['reference_calibration_population'])
                    self.assertEqual(report['receipts'][row['prediction']]['evaluation']['population'],F.evaluation_population(ids))
                risk=[r for r in report['rows'] if r['prediction'].startswith('risk score:')][0]
                self.assertEqual(risk['evaluation_cases'],n)

    def test_native_final_answer_keeps_scope_and_source_date_bindings(self):
        for ids,expected_status in [(self.affected,'answered'),([],'partial')]:
            report=self.report(ids)
            plan={'coverage':'supported_observation','unmet_requirements':[],'interpretation':'Authored cohort calibration',
                  'nodes':[{'id':'cal','operation':'calibration','inputs':[],'parameters':{'target':'risk_scores'}}]}
            answer=G.build_answer(plan,[{'id':'cal','result':report}],{},'How well calibrated are these cost predictions?')
            self.assertEqual(answer['status'],expected_status)
            scope=answer['overall']['calibration']
            self.assertEqual(scope['evaluation_population'],F.evaluation_population(ids))
            self.assertEqual(scope['as_of'],ASOF);self.assertEqual(scope['source_binding']['source_digest'],'authored-calibration')
            self.assertEqual(answer['table'][0]['evaluation_population'],F.evaluation_population(ids))
            self.assertEqual(answer['table'][0]['evaluation_cases'],len(ids))

    def test_saved_answer_reopens_with_input_membership_and_row_scope(self):
        from clinical_core.agere.deliverables import answer_context as A
        report=self.report(self.affected)
        plan={'coverage':'supported_observation','unmet_requirements':[],'interpretation':'Authored calibration',
              'nodes':[{'id':'cal','operation':'calibration','inputs':[],'parameters':{'target':'risk_scores'}}]}
        results=[{'id':'cal','result':report}]
        answer=G.build_answer(plan,results,{},'How well calibrated are these cost predictions?')
        key=A.capture(self.root,'How well calibrated are these cost predictions?',
                      {'answer':answer,'graph':plan,'results':results,'request_contract':{'population':self.affected}})['id']
        reopened=A.load(self.root,key)
        self.assertEqual(reopened['input_patient_ids'],self.affected)
        self.assertEqual(reopened['answer']['table'][0]['evaluation_population'],F.evaluation_population(self.affected))
        self.assertEqual(reopened['answer']['overall']['calibration']['as_of'],ASOF)
        self.assertEqual(reopened['answer']['table'][0]['backtest_coverage'],0.)


if __name__=='__main__':unittest.main()
