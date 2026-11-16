"""Authored policy/quality/work regressions, with explicit summaries and no providers."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from clinical_core.agere.clinical import off_policy as O,library_measure as L,settlement_bridge as B,measures,vbc,outreach,monitor_sweep as MS
from clinical_core.agere.deliverables import answer_context as A,workflow as W


class PolicySupport(unittest.TestCase):
    def test_held_out_rewards_cannot_change_their_own_fallback_predictions(self):
        x=np.ones((6,1));actions=np.array([0,1]*3);pids=['A','A','B','B','C','C']
        r=np.zeros(6);changed=r.copy();changed[0]=1
        with patch.object(O,'_softmax_fit',side_effect=AssertionError('No fit needed for sparse training folds')),patch.object(O,'_logit_fit',side_effect=AssertionError('No fit needed')):
            _,before=O.crossfit(x,actions,r,pids,2)
            _,after=O.crossfit(x,actions,changed,pids,2)
        np.testing.assert_equal(before[:2],after[:2])
        self.assertTrue(np.isfinite(before).all())

    def test_one_patient_cannot_be_a_policy_winner_or_an_independent_bootstrap(self):
        rows=[{'patient_id':'one','channel':'mail' if i<10 else 'phone','responded':i<10,'risk_score':1,
               'repeat':False,'program':'fixture','risk_tier':'low','segment':'all','date':'2030-01-01'} for i in range(20)]
        with patch.object(O,'_softmax_fit',side_effect=AssertionError('No independent training data')):
            out=O.evaluate(rows,O.validate({'policies':['logged','best_arm'],'bootstrap':10}))
            _,q=O.crossfit(np.ones((20,1)),np.array([0]*10+[1]*10),np.zeros(20),['one']*20,2)
        self.assertEqual(out['status'],'insufficient_independent_patients')
        self.assertIsNone(out['recommendation']);self.assertEqual(out['logged_value'],.5)
        self.assertTrue(np.isnan(q).all())


class MeasurePatients(unittest.TestCase):
    def test_patient_output_survives_save_and_followup_without_becoming_gap_only(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            with sqlite3.connect(path/'semantic.sqlite3') as db:
                db.executescript("CREATE TABLE meta(key,value);INSERT INTO meta VALUES('version','fixture'),('as_of','2030-05-27');CREATE TABLE patient(patient_id);INSERT INTO patient VALUES('met'),('gap');")
            patient_rows=[{'patient_id':'met','met':True,'excluded':False},{'patient_id':'gap','met':False,'excluded':False}]
            report={'measure_name':'Fixture measure','measure_status':'internal','overall':{'numerator':1,'denominator':2,'rate':.5},
                    'rows':[],'patient_rows':patient_rows,'care_gaps':[{'patient_id':'gap'}],'reading':'Patient statuses',
                    'measure_id':'fixture','parameters':{'output':'patients','group_by':'none','follow_up_days':None}}
            answer=L.answer(report,{'coverage':'supported_observation'},'List patient statuses')
            self.assertEqual(answer['table'],patient_rows);self.assertEqual(answer['count'],2)
            self.assertEqual(answer['patient_ids'],['gap','met']);self.assertEqual(answer['membership_role'],'evaluated')
            key=A.capture(path,'List patient statuses',{'answer':answer})['id']
            self.assertEqual(A.load(path,key,current=True)['answer']['table'],patient_rows)
            self.assertEqual(len(A.followup(path,key,'List them')['answer']['table']),2)


class QualityUnknown(unittest.TestCase):
    def run_bridge(self,reports,assumption=None):
        terms={'program':{'id':'commercial_fixture','label':'Authored program','assumptions':[]},'sharing_rate':.5,'msr':0}
        lib={k:{'id':k,'name':k,'direction':'higher_is_better'} for k in reports}
        p={'program':'fixture','population':'all','benchmark_pmpm':1000,'trend':0,'measures':list(reports),
           'targets':{k:1 for k in reports},'closure_if_engaged':.6,'capacity_patients':1,'quality_score_assumption':assumption}
        with patch.object(B,'terms_for',return_value=(terms,None)),patch.object(vbc,'eligibility',return_value={'A':{'months':['2030-01']}}),\
             patch.object(vbc,'attribution',return_value={}),patch.object(vbc,'_cost',return_value=([{'member_months':12,'allowed_pmpm_completed':900}],'')),\
             patch.object(measures,'library',return_value=lib),patch.object(measures,'Context',return_value=None),\
             patch.object(measures,'evaluate_db',side_effect=lambda db,m,*a,**kw:copy.deepcopy(reports[m['id']])),\
             patch.object(outreach,'candidates',return_value=([],{})):
            return B.run(None,None,p,'2030-12-31')

    def test_missing_quality_does_not_become_full_credit_or_realized_money(self):
        missing={'declined':True,'missing_inputs':['No quality evidence']}
        out=self.run_bridge({'measure':missing})
        self.assertIsNone(out['quality_score']['current']);self.assertIsNone(out['waterfall'][0]['dollars'])
        self.assertFalse(out['actual_settlement_established']);self.assertEqual(out['status'],'partial')
        self.assertEqual(out['actual_pmpm'],900);self.assertEqual(out['member_months'],12)
        self.assertIn('unknown',B.headline(out))
        conditional=self.run_bridge({'measure':missing},assumption=1)
        self.assertIsNone(conditional['quality_score']['current'])
        self.assertEqual(conditional['waterfall'][0]['dollars'],600)
        self.assertEqual(conditional['waterfall'][0]['label'],'current_scenario')
        self.assertIn('assumed',B.headline(conditional));self.assertFalse(conditional['actual_settlement_established'])

    def test_partial_measure_coverage_stays_unknown_while_observed_zero_is_zero(self):
        known={'declined':False,'overall':{'numerator':0,'denominator':1},'care_gaps':[]}
        partial=self.run_bridge({'known':known,'missing':{'declined':True,'missing_inputs':['Absent']}})
        self.assertIsNone(partial['quality_score']['current'])
        self.assertEqual(partial['quality_coverage']['assessed'],1)
        observed=self.run_bridge({'known':known})
        self.assertEqual(observed['quality_score']['current'],0)
        self.assertEqual(observed['waterfall'][0]['dollars'],0)


class MonitorRecovery(unittest.TestCase):
    def test_local_read_failure_remains_pending_then_recovers_without_new_release(self):
        with tempfile.TemporaryDirectory() as d:
            stack=SimpleNamespace(path=Path(d));MS.schedule(stack.path)
            row={'context_id':'ANS-fixture','index':0,'mode':'answer','status':'watching','due':True}
            good={'steps':[{'observations':[{'changed':False,'source':{'as_of':'2030-05-27'}}],'reason':'Recovered'}],
                  'new_paid_api_calls':2,'known_api_estimate_usd':.25,'api_cost_known':True}
            with patch.object(MS,'status',side_effect=lambda path:([dict(row)],{'source':'v2'})),patch.object(W,'refresh',side_effect=[OSError('Temporary local read failure'),good]) as refresh:
                MS.sweep(stack)
                self.assertTrue((stack.path/MS.DUE_FILE).exists())
                out,_=MS.sweep_if_due(stack)
                self.assertEqual(refresh.call_count,2);self.assertEqual(out[0]['new_paid_api_calls'],2)
                self.assertEqual(out[0]['known_api_estimate_usd'],.25)
                self.assertFalse((stack.path/MS.DUE_FILE).exists())

    def test_provider_limit_stays_visible_without_automatic_resend(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        with tempfile.TemporaryDirectory() as d:
            stack=SimpleNamespace(path=Path(d));MS.schedule(stack.path)
            row={'context_id':'ANS-fixture','index':0,'mode':'answer','status':'watching','due':True}
            with patch.object(MS,'status',side_effect=lambda path:([dict(row)],{'source':'v2'})),\
                 patch.object(A,'source_identity',return_value={'source':'v2'}),\
                 patch.object(W,'refresh',side_effect=ProviderUnavailable('Subscription limit',kind='rate_limit')) as refresh:
                with self.assertRaises(ProviderUnavailable):MS.sweep(stack)
                self.assertIsNone(MS.sweep_if_due(stack));self.assertEqual(refresh.call_count,1)
                self.assertEqual(json.loads((stack.path/MS.DUE_FILE).read_text())['state'],'requires_attention')


class RefreshUsage(unittest.TestCase):
    def test_new_engine_usage_is_returned_once_and_unchanged_snapshot_is_free(self):
        import test_general_analysis_workflow as fixture
        from clinical_core.agere.clinical import general_analysis as G
        test=fixture.GeneralAnalysisWorkflow();test.setUp();self.addCleanup(test.doCleanups)
        _,saved=test.execute(population=['A','B','C']);W.materialize(test.path,saved['id'])
        test.change_source("INSERT INTO utilization_event VALUES('A','ed_visit','2029-05-02','ehr',1);")
        original=G.execute
        for known in (True,False):
            if not known:test.change_source("INSERT INTO utilization_event VALUES('A','ed_visit','2029-05-03','ehr',1);")
            def execute(*a,**kw):
                out=original(*a,**kw)
                return {**out,'new_paid_api_calls':2,'planning_economics':{'known_api_estimate_usd':.25,'new_api_estimate_usd':.25 if known else None,'api_cost_known':known}}
            with patch.object(G,'execute',side_effect=execute):
                out=W.refresh(test.stack,{'context_id':saved['id'],'index':2})
            self.assertEqual(out['new_paid_api_calls'],2);self.assertEqual(out['known_api_estimate_usd'],.25)
            self.assertEqual(out['api_cost_known'],known)
            repeated=W.refresh(test.stack,{'context_id':saved['id'],'index':2})
            self.assertEqual(repeated['new_paid_api_calls'],0);self.assertEqual(repeated['known_api_estimate_usd'],0)

