"""Authored temporal/cohort controls through the existing discovery engines.

No held-out keys or generator truth. These establish engineering scope and
accounting, not clinical or statistical-method qualification.
"""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import change_attribution as C, monitor as M
from clinical_core.agere.clinical import graph_contracts as G


class HistoricalDiscovery(unittest.TestCase):
    period={'start':'2028-01-01','end':'2029-12-31'}

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.db=sqlite3.connect(self.path/'semantic.sqlite3');self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE record(record_id,patient_id,stream,event_date,organization_id,payload);'
            'CREATE TABLE member_month(patient_id,month,line_of_business);'
            'CREATE TABLE utilization_event(patient_id,kind,event_date,organization_id);')
        self.months=[C._shift('2027-01',i)for i in range(36)]
        n=0
        for i in range(3):
            pid='P'+str(i);org='WH-PRACTICE-'+str(i)
            # The annotation's existing default uses eighteen baseline months;
            # provide all of them, rather than inventing six zero months.
            for month in [C._shift('2026-07',i)for i in range(42)]:
                # Known exposure is absent for one report month, though events
                # are present. Current eligibility must not fill that gap.
                if month!='2028-06':
                    self.db.execute('INSERT INTO member_month VALUES(?,?,?)',(pid,month,'baseline_payer'))
                self.db.execute('INSERT INTO utilization_event VALUES(?,?,?,?)',(pid,'ed_visit',month+'-12',org))
                if month=='2029-04':continue  # no received records != no care
                for stream in ['labs','encounters','adt']:
                    count=2 if stream=='labs'and month.startswith('2028')else 24
                    for j in range(count):
                        self.db.execute('INSERT INTO record VALUES(?,?,?,?,?,?)',
                            ('R'+str(n),pid,stream,month+'-12',org,'{}'));n+=1
        self.db.commit()

    def params(self,**kw):
        return {'group_by':'source_system','period':self.period,'baseline_months':12,
                'streams':['labs','encounters','adt'],**kw}

    def test_full_historical_calendar_baseline_missing_capture_and_unqualified_hypotheses(self):
        report=C.execute(self.path,None,self.params(output='monthly_coverage'),'2030-05-27')
        self.assertEqual(report['window_months'],self.months[12:])
        self.assertEqual(report['baseline_months'],['2027-01','2027-12'])
        self.assertEqual(len(report['monthly_coverage']),24)
        absent=next(r for r in report['monthly_coverage']if r['month']=='2029-04')
        self.assertEqual(absent['received_records_in_selected_streams'],0)
        self.assertEqual(absent['capture_status'],'no_received_records_capture_unknown')
        answer=C.answer(report,{'coverage':'supported_observation'},'Authored historical question')
        self.assertEqual([r['month']for r in answer['table']],self.months[12:])
        self.assertEqual(sum(r['anomaly_count']for r in answer['table']),len(report['anomalies']))
        self.assertEqual(sum(len(r['anomalies'])for r in answer['table']),len(report['anomalies']))
        self.assertFalse(report['independent_method_qualification'])
        self.assertIn('No FWER/FDR',report['multiplicity_handling'])
        self.assertTrue(all('2028-01'<=r['month']<='2029-12'for r in report['anomalies']))

    def test_future_records_and_payer_assignments_cannot_change_historical_fit_or_source_inventory(self):
        for group in ('source_system','home_practice','payer'):
            with self.subTest(group=group):
                params=self.params(group_by=group)
                before=C.execute(self.path,None,params,'2030-05-27')
                self.db.executemany('INSERT INTO record VALUES(?,?,?,?,?,?)',
                    [('future'+str(i),'P0','future_only_stream','2030-01-01','WH-PRACTICE-FUTURE','{}')for i in range(100)])
                self.db.execute("INSERT INTO member_month VALUES('P0','2030-01','future_payer')")
                self.db.commit()
                after=C.execute(self.path,None,params,'2030-05-27')
                self.assertEqual(before,after)
                self.db.execute("DELETE FROM record WHERE stream='future_only_stream'")
                self.db.execute("DELETE FROM member_month WHERE month='2030-01'");self.db.commit()

    def test_exact_population_and_no_future_only_streams_in_selected_source_scope(self):
        self.db.execute("INSERT INTO record VALUES('foreign','FOREIGN','foreign_only_stream','2028-01-02','FOREIGN','{}')")
        self.db.commit()
        report=C.execute(self.path,['P0'],self.params(streams=[]),'2030-05-27')
        self.assertEqual(report['groups'],['WH-PRACTICE-0'])
        self.assertNotIn('foreign_only_stream',report['source_scope']['received_streams_not_selected'])
        with self.assertRaisesRegex(ValueError,'No group'):C.execute(self.path,[],self.params(),'2030-05-27')

    def test_monitor_annotation_inherits_period_and_retains_every_anomaly(self):
        base={'feeds':[{'stream':'labs'}],'parameters':{'period':self.period},
              'period':{'report_months':['2028-01','2029-12']}}
        report=C.annotate_monitor(copy.deepcopy(base),self.path,'2030-05-27')
        a=report['change_attribution']
        self.assertEqual(a['window_months'],self.months[12:])
        self.assertEqual(len(a['monthly_coverage']),24)
        self.assertGreater(len(a['anomalies']),25)
        expected=C.execute(self.path,None,{'streams':['labs','encounters','payer_medical_claims'],
            'focus_streams':['labs'],'group_by':'source_system','period':self.period},'2030-05-27')
        self.assertEqual(a['anomalies'],expected['anomalies'])
        without_params={k:v for k,v in base.items()if k!='parameters'}
        self.assertEqual(C.annotate_monitor(without_params,self.path,'2030-05-27')['change_attribution']['window_months'],self.months[12:])

    def test_monthly_rate_engine_keeps_missing_historical_exposure_unknown(self):
        people={pid:{'months':self.months[:],'coverage_type':'current_coverage'}for pid in ['P0','P1','P2']}
        params={'check':'control_chart','series':['ed_rate'],'group_by':'none','period':self.period,
                'exposure_basis':'historical_member_months','output':'monthly_points'}
        with patch.object(M,'_connect',return_value=self.db),patch.object(M.vbc,'eligibility',return_value=people),\
                patch.object(M.vbc,'attribution',return_value={}),patch.object(M,'event_type_gaps',return_value={}),\
                patch('clinical_core.agere.clinical.changepoint_qualification.note',return_value=None):
            report=M.execute(self.path,None,params,'2030-05-27')
        rows=report['monthly_points'];self.assertEqual([r['month']for r in rows],self.months[12:])
        absent=next(r for r in rows if r['month']=='2028-06')
        self.assertEqual(absent['exposure'],0);self.assertIsNone(absent['rate'])
        self.assertEqual(absent['events_without_recorded_exposure'],3)
        self.assertEqual(absent['month_status'],'no_recorded_exposure_rate_unknown')

    def test_period_validation_complete_months_and_existing_default(self):
        p=C.validate({});months,report=C.windows('2030-05-27',p)
        self.assertEqual(months,C.months_for('2030-05-27',3,18));self.assertEqual(report,months[-3:])
        period={'start':'2028-01-15','end':'2028-03-15'}
        self.assertEqual(C.windows('2030-05-27',C.validate({'period':period}))[1],['2028-02'])
        self.assertEqual(M.windows(M.validate({'period':period}),'2030-05-27')[1],['2028-02'])
        for p in ({'start':'2028-02-30'}, {'start':'2029-01-01','end':'2028-01-01'},[],None):
            with self.subTest(period=p),self.assertRaises(ValueError):C.validate({'period':p})
        future={'period':{'start':'2030-05-01','end':'2030-05-27'}}
        for module in (C,M):
            with self.subTest(module=module.__name__),self.assertRaisesRegex(ValueError,'no complete'):
                module.windows('2030-05-27',module.validate(future))if module is C else module.windows(module.validate(future),'2030-05-27')
        schema=G.parameter_schema('change_attribution',{'cohort_fields':{'record_fields':{}},'evidence_catalog':{},'source_streams':[]})
        self.assertIn('period',schema['properties']);self.assertIn('monthly_coverage',schema['properties']['output']['enum'])

    def test_native_route_uses_supported_calendar_period_and_defers_unconsumed_years(self):
        plan,_=C.route('Was the drop in lab volume real or a feed outage in 2029?',{},'2030-05-27')
        self.assertEqual(plan['nodes'][0]['parameters']['period'],{'start':'2029-01-01','end':'2029-12-31'})
        self.assertIsNone(C.route('Was the drop in lab volume real during 2028 and 2029?',{},'2030-05-27'))


if __name__=='__main__':unittest.main()
