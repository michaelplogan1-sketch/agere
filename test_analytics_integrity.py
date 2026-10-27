"""Authored input/denominator/accounting regressions; no fitting or provider sends."""
from collections import defaultdict
from datetime import date,timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import (target_trial as TT,profile,vbc,survival,risk_outcomes as RO,
                                         demand,discharge_forecast as DF,cost_telemetry as CT,
                                         contract_reimbursement as CR,data_domains,request_plan as RP,
                                         shared_planner as SP,general_analysis as GA)
from clinical_core.agere.gateway import BudgetLedger


class ClinicalInputs(unittest.TestCase):
    def db(self,ddl):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.executescript(ddl);return db

    def test_all_source_event_rows_agree_before_provider_adjustment(self):
        db=self.db("""CREATE TABLE encounter(patient_id,event_date,type_concept);
          INSERT INTO encounter VALUES('A','2029-01-01','emergency_visit');
          CREATE TABLE utilization_event(patient_id,event_date,kind);
          INSERT INTO utilization_event VALUES('A','2029-01-01','ed_visit'),('A','2029-01-01','ed_visit'),('A','2029-02-01','ed_visit');
          CREATE TABLE stay(patient_id,admit_date,discharge_date,readmitted_30d);""")
        self.assertEqual(len(TT._events(db,'ed_visit',None,{'A'})['A']),3)
        people={'A':{'months':['2029-01','2029-02'],'age':60,'sex':'F'}}
        attrs={'A':{'pcp':'doctor','practice':'practice'}}
        with patch.object(vbc,'_period',return_value=('2029-01-01','2029-12-31')),patch.object(vbc,'eligibility',return_value=people),patch.object(vbc,'attribution',return_value=attrs):
            rows,_=profile.members(db,{'A'},{'metric':'ed_visits','unit':'pcp','adjust':'none'},'2030-05-27')
        self.assertEqual(rows[0][3],3)
        for grouping in ('none','year','pcp'):
            rows,_=vbc._utilization(db,people,attrs,{'group_by':grouping,'denominator':'member_years'},'2029-01-01','2029-12-31')
            self.assertEqual(sum(r['ed_visits'] for r in rows),3)

    def test_enrollment_eligibility_is_independent_of_grouping(self):
        db=self.db("""CREATE TABLE utilization_event(patient_id,event_date,kind);
          INSERT INTO utilization_event VALUES('A','2030-01-10','ed_visit'),('A','2030-03-10','ed_visit');
          CREATE TABLE encounter(patient_id,event_date,type_concept,record_id);
          INSERT INTO encounter VALUES('A','2030-01-10','emergency_visit','jan'),('A','2030-03-10','emergency_visit','mar');
          CREATE TABLE stay(patient_id,admit_date,discharge_date,readmitted_30d);
          INSERT INTO stay VALUES('A','2030-01-11','2030-01-12',0),('A','2030-03-11','2030-03-12',0);""")
        people={'A':{'months':['2030-01']}};attrs={'A':{'pcp':'doctor'}}
        for grouping in ('none','year','month','pcp'):
            rows,_=vbc._utilization(db,people,attrs,{'group_by':grouping,'denominator':'member_years'},'2030-01-01','2030-06-30')
            self.assertEqual((sum(r['ed_visits'] for r in rows),sum(r['admissions'] for r in rows)),(1,1))
            self.assertAlmostEqual(sum(r['member_years'] for r in rows),.0833,places=4)

    def test_historical_attribution_ignores_future_or_ended_assignments(self):
        db=self.db('CREATE TABLE record(patient_id,stream,payload); CREATE TABLE visit(patient_id,attending_clinician_id,organization_id,event_date,type_concept);')
        for cid,when,end in [('historical','2029-01-01',None),('future','2031-01-01',None),('ended','2030-02-01','2030-03-01')]:
            db.execute('INSERT INTO record VALUES(?,?,?)',('A','care_team_relationships',json.dumps({'role':'primary_care','status':'assigned','effective_start':when,'effective_end':end,'clinician_id':cid,'organization_id':'practice'})))
        out=vbc.attribution(db,{'A':{}},'2030-01-01','2030-12-31','prospective')
        self.assertEqual(out['A']['pcp'],'historical')

    def test_cancelled_or_superseded_measurements_cannot_establish_survival_events(self):
        db=self.db("""CREATE TABLE patient(patient_id);INSERT INTO patient VALUES('A');
          CREATE TABLE measurement(patient_id,concept,event_date,value_canonical,result_current,excluded_reason);
          INSERT INTO measurement VALUES('A','fixture_lab','2030-01-10',9,0,'cancelled_result');""")
        p={'index':{'type':'date','date':'2030-01-01'},'event':{'type':'measurement','concept':'fixture_lab','op':'gt','value':5},'horizon_days':30}
        rows,excluded=survival.units(db,['A'],p,'2030-01-31')
        self.assertEqual(rows,[]);self.assertEqual(excluded['no_result_after_time_zero'],1)
        self.assertEqual(dict(TT._labs(db,['fixture_lab'],{'A'})),{})
        db.execute('UPDATE measurement SET result_current=1,excluded_reason=NULL')
        rows,_=survival.units(db,['A'],p,'2030-01-31')
        self.assertEqual((rows[0][2],rows[0][3]),(9,1))
        self.assertEqual(len(TT._labs(db,['fixture_lab'],{'A'})['fixture_lab']['A']),1)

    def test_readmission_risk_set_excludes_documented_index_death_but_los_keeps_it(self):
        db=self.db("""CREATE TABLE patient(patient_id,sex_reported,pcp_clinician_id,pcp_practice_id,death_date);
          INSERT INTO patient VALUES('dead','F','doctor','practice','2030-01-05'),('survivor','F','doctor','practice',NULL);
          CREATE TABLE stay(patient_id,admit_date,discharge_date,type,organization_id,discharge_known,los_days);
          INSERT INTO stay VALUES('dead','2030-01-01','2030-01-05','inpatient','H',1,4),
          ('survivor','2030-01-01','2030-01-05','inpatient','H',1,4),('survivor','2030-01-15','2030-01-20','inpatient','H',1,5);""")
        p={'outcome':'readmission','unit':'hospital','window_days':30,'period':{'start':'2030-01-01','end':'2030-01-10'},'period_days':365,'include_observation':True,'adjust':'none'}
        cap={'stays':True,'visits':False,'death':('patient','death_date')}
        rows,period=RO.index_cases(db,cap,None,p,'2030-03-01')
        self.assertEqual([r[3]['patient_id'] for r in rows],['survivor'])
        self.assertEqual(sum(r[1] for r in rows)/len(rows),1)
        self.assertEqual(period['excluded_index_deaths'],1)
        self.assertFalse(period['absence_confirms_survival'])
        rows,_=RO.index_cases(db,cap,None,{**p,'outcome':'length_of_stay'},'2030-03-01')
        self.assertEqual({r[3]['patient_id'] for r in rows},{'dead','survivor'})


class Forecasts(unittest.TestCase):
    def test_every_declared_demand_measure_can_finish_without_model_execution(self):
        for measure,(_,_,label) in demand.MEASURES.items():
            with self.subTest(measure=measure),patch.object(demand,'timestral_sizes',return_value=[]):
                p=demand.validate({'measure':measure})
                out=demand._finish(p,Path('/unused'),'2030-05-27',label,'week',[],[],[],{},None)
                self.assertEqual(out['parameters']['measure'],measure)
                self.assertTrue(out['method'])

    def test_discharge_baseline_score_never_claims_to_validate_the_twin(self):
        asof='2030-05-27'
        rows=[{'patient_id':str(i),'has_inpatient':True,'has_observation':False,'discharge_known':True,
               'start_date':(date.fromisoformat(asof)-timedelta(days=i+1)).isoformat(),
               'end_date':(date.fromisoformat(asof)-timedelta(days=i+1)).isoformat()} for i in range(140)]
        seen=[]
        for level in (10,20):
            db=sqlite3.connect(':memory:')
            with patch.object(DF.H,'connect',return_value=(db,asof)),patch.object(DF.H,'visits_in',return_value=(rows,'authored',('2030-01-01',asof),None,[])),\
                 patch.object(DF.C,'fit',return_value={'los_hours':[24],'rates':[1]*7,'census':[1],'ed_share':0}),\
                 patch.object(DF.C,'simulate',side_effect=lambda *a,**kw:a[1]),\
                 patch.object(DF.C,'metrics',side_effect=lambda arr,h,beds:{'discharges':[level]*h,'census':[1]*h}):
                out=DF.execute(Path('/unused'),None,{'beds':1,'horizon_days':2,'replications':5},asof)
            seen.append(out)
            self.assertIn('weekday-mean baseline backtest',DF.headline(out))
            self.assertEqual(out['receipt']['backtest']['validation_target'],'weekday_mean_baseline')
            self.assertEqual(out['receipt']['forecast_validation']['status'],'unmeasured')
            self.assertIn('baseline backtest',DF.headline(out))
        self.assertEqual([r['receipt']['expected_discharges_total'] for r in seen],[20,40])
        self.assertEqual([r['receipt']['backtest']['mae_weekday_mean'] for r in seen],[0,0])


class Economics(unittest.TestCase):
    def test_overlapping_questions_conserve_the_cost_of_one_cached_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'ledger.sqlite3';ledger=BudgetLedger(path)
            a=CT.start('A',ledger_path=path);b=CT.start('B',ledger_path=path)
            with patch('clinical_core.agere.economics.usage_cost',return_value={'estimated_usd':.25}):
                CT.CURRENT.set(a.question_id);ledger.reserve('one','jev','fixture')
                ledger.finish('one',response={'usage':{'input_tokens':10,'output_tokens':2}})
                CT.CURRENT.set(b.question_id);ledger.reserve('one','jev','fixture')
                ra=a.finish({'answer':{'status':'answered'}});rb=b.finish({'answer':{'status':'answered'}})
            ledger.close()
            self.assertEqual((ra['api_calls'],rb['api_calls']),(1,0))
            self.assertAlmostEqual(ra['api_usd']+rb['api_usd'],.25)
            self.assertTrue(rb['attempts'][0]['cache_hit'])

    def test_unreadable_ledger_preserves_answer_and_unknown_cost(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'invalid.sqlite3';path.write_text('invalid SQLite')
            result={'answer':{'status':'answered','count':1},'new_paid_api_calls':0}
            out=CT.start('Observed count',ledger_path=path).finish(result)
            receipt=CT.receipt(out,result)
            self.assertFalse(out['usd_known']);self.assertFalse(out['accounting_complete'])
            self.assertIsNone(receipt['usd']);self.assertIsNone(receipt['paid_calls'])
            self.assertFalse(receipt['ledger_consistent']);self.assertFalse(receipt['usd_known'])
            self.assertEqual(result['answer']['count'],1)

    def test_repricing_does_not_require_an_unrelated_current_contract_basis(self):
        p=CR.validate({'view':'reprice','reprice':{'P':1.3},'min_lines':5})
        with patch.object(CR,'load',return_value=([('line','P','product','C',1,100,100)],{'C':50})):
            out=CR.run(None,None,p,'2030-05-27')
            with_basis=CR.run(None,None,{**p,'terms':{'P':{'rates':{'C':100},'pct_of_medicare':None}}},'2030-05-27')
        self.assertEqual(out['modelled_lines'],0)
        self.assertEqual(out['reprice'],with_basis['reprice'])
        self.assertEqual((out['reprice'][0]['repriced'],out['reprice'][0]['delta']),(65,-35))


class Discovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
        with sqlite3.connect(self.path/'semantic.sqlite3') as db:
            db.executescript("""CREATE TABLE meta(key,value);INSERT INTO meta VALUES('as_of','2030-05-27'),('version','authored');
              CREATE TABLE patient(patient_id);INSERT INTO patient VALUES('A');
              CREATE TABLE stay(patient_id,admit_date);INSERT INTO stay VALUES('A','2029-01-01');
              CREATE TABLE record(record_id,patient_id,stream,event_date,available_date,payload);
              INSERT INTO record VALUES('note','A','notes','2029-01-01','2029-01-02','{"text":"BRCA1 pathogenic variant detected"}');""")
        self.catalog=RP.Catalog.load(self.path)

    def test_unmapped_narrative_finding_is_investigated(self):
        self.assertIsNone(data_domains.check(self.catalog,'What BRCA1 genetic test results are documented?'))
        q='How many admissions in 2029 and what were patient satisfaction scores?'
        found=data_domains.check(self.catalog,q)
        self.assertFalse(data_domains.answer(q,*found)['answer']['data_absence_established'])

    def test_missing_domain_cannot_preempt_the_supported_part_of_a_compound_request(self):
        q='How many admissions occurred in 2029 and what were patient satisfaction scores?'
        spec={'steps':[{'name':'counted','purpose':'Observed admissions','sql':"SELECT count(*) AS admissions FROM stay WHERE admit_date BETWEEN '2029-01-01' AND '2029-12-31'"}],
              'returns':'count','requirements':[{'quote':'How many admissions occurred in 2029','applied_in':['counted']}],
              'unresolved':[{'quote':'what were patient satisfaction scores','reason':'No mapped survey scores; narrative coverage unassessed'}]}
        contract=GA.prepare(self.catalog,q,spec)
        receipt={'request_contract':contract,'interpretation':{'execution_accepted':True},'new_paid_api_calls':0}
        stack=SimpleNamespace(path=self.path,allow_live=False)
        with patch.object(SP,'plan',return_value=(GA.graph_for(contract),receipt)):
            out=SP._compose_once(stack,q)
        self.assertEqual(out['answer']['status'],'partial')
        self.assertEqual(out['answer']['count'],1)
        self.assertTrue(out['answer']['missing'])

