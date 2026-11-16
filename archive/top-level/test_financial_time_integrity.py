"""Authored snapshot, attribution and monetary-basis regressions; no model or provider execution."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import line_of_therapy as L, provider_network_graph as N, mlr as M, vbc, price_transparency as P


class TreatmentSnapshot(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.executescript('''
          CREATE TABLE medication(record_id,patient_id,event_date,drug_name,drug_class,evidence,status,fill_reversed);
          CREATE TABLE record(record_id,payload,available_date);
          CREATE TABLE condition(patient_id,event_date,recorded_date,available_date,concept,icd10);
        ''')
        self.fill('JAN', '2030-01-01', 'metformin', 'metformin', '2030-01-01')
        self.params = {'components':['metformin','sglt2_inhibitor'],'require_condition':False,'require_new_user':False}

    def fill(self, rid, day, name, cls, received, reversed=0):
        self.db.execute('INSERT INTO medication VALUES(?,?,?,?,?,?,?,?)', (rid,'A',day,name,cls,'dispensed','completed',reversed))
        self.db.execute('INSERT INTO record VALUES(?,?,?)', (rid,json.dumps({'days_supply':30}),received))

    def test_later_fill_cannot_change_earlier_lines_or_censoring(self):
        before = L.run(self.db,None,self.params,'2030-01-31')
        self.fill('JUNE','2030-06-01','empagliflozin','sglt2_inhibitor','2030-06-01')
        after = L.run(self.db,None,self.params,'2030-01-31')
        for field in ('counts','lines','persistence','examples','fill_feed_start'):
            self.assertEqual(before[field],after[field],field)
        self.assertIsNone(after['lines'][0]['median_time_to_next_treatment_days'])
        self.assertEqual(len(L.run(self.db,None,self.params,'2030-06-30')['lines']),2)
        self.assertEqual(L.run(self.db,[],self.params,'2030-06-30')['counts']['analysed_new_users'],0)
        self.db.execute("UPDATE medication SET fill_reversed=1 WHERE record_id='JUNE'")
        self.assertEqual(len(L.run(self.db,None,self.params,'2030-06-30')['lines']),1)

    def test_known_receipt_and_condition_cutoffs_precede_eligibility(self):
        self.fill('LATE','2030-01-05','empagliflozin','sglt2_inhibitor','2030-06-01')
        report=L.run(self.db,None,self.params,'2030-01-31')
        self.assertEqual(report['examples'][0]['lines'][0]['regimen'],'metformin')
        self.db.execute("INSERT INTO condition VALUES('A','2030-06-01','2030-06-01','2030-06-01','diabetes','E11.9')")
        params={'area':'diabetes','require_new_user':False}
        self.assertEqual(L.run(self.db,None,params,'2030-01-31')['counts']['analysed_new_users'],0)
        self.assertEqual(L.run(self.db,None,params,'2030-06-30')['counts']['analysed_new_users'],1)


class ReferralTiming(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.executescript('''
          CREATE TABLE clinician(clinician_id,organization_id,specialty,npi);
          INSERT INTO clinician VALUES('PCP','O','Family Medicine','1'),('DEST','O','Cardiology','2'),('OTHER','O','Family Medicine','3');
          CREATE TABLE organization(organization_id,npi,display_name);INSERT INTO organization VALUES('O','4','Fixture');
          CREATE TABLE patient(patient_id,pcp_clinician_id);INSERT INTO patient VALUES('A','PCP');
          CREATE TABLE payer_claim(record_id,patient_id,service_from,rendering_npi,billing_npi,network_status,paid_cents,counts_in_metrics);
          INSERT INTO payer_claim VALUES('PC','A','2030-01-05','1','4','in_network',100,1),('DC','A','2030-01-10','2','4','in_network',200,1);
          CREATE TABLE record(record_id,patient_id,payload,event_date,stream);
        ''')

    def edges(self):
        clin,_,_,_,pcp,claims,explicit=N.load(self.db,None,'2030-01-01','2030-12-31')
        return N.referral_edges(clin,pcp,claims,explicit,N.validate({}),'2030-01-01')

    def referral(self,rid,day,who='OTHER'):
        self.db.execute('INSERT INTO record VALUES(?,?,?,?,?)',(rid,'A',json.dumps({'requesting_clinician_id':who,'receiving_clinician_id':'DEST'}),day,'referrals'))

    def test_later_referral_does_not_rewrite_earlier_claim(self):
        before=self.edges();self.referral('JUNE','2030-06-01')
        self.assertEqual(before,self.edges())
        self.assertEqual(before[0][1:3],('PCP','DEST'));self.assertEqual(before[0][4],'pcp_sequence')
        self.referral('JAN','2030-01-08')
        self.assertEqual(self.edges()[0][1:3],('OTHER','DEST'));self.assertEqual(self.edges()[0][4],'referral_record')

    def test_unknown_expired_and_tied_referrals_have_deterministic_meaning(self):
        self.referral('UNKNOWN',None);self.referral('OLD','2029-01-01')
        self.assertEqual(self.edges()[0][1],'PCP')
        self.referral('B','2030-01-08','OTHER');self.referral('A','2030-01-08','PCP')
        self.assertEqual(self.edges()[0][1],'OTHER')
        self.db.execute("DELETE FROM record WHERE record_id='B'");self.referral('B','2030-01-08','OTHER')
        self.assertEqual(self.edges()[0][1],'OTHER')


class PaidCompletion(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript("CREATE TABLE member_month(patient_id,month,product);INSERT INTO member_month VALUES('A','2030-01','commercial'),('A','2030-12','commercial');")

    def cell(self,claims,completion=True,products=None):
        with patch.object(vbc,'_claims',return_value=claims):
            args=(self.db,'2030-01-01','2030-12-31',None,completion,'2030-12-31')
            return M.cells(*args,**({'products':products} if products is not None else {}))[('commercial','2030')]

    def test_paid_estimate_is_invariant_to_allowed_distribution(self):
        def claims(a,b):return [('A','2030-01',a,5000,'medical','2030-01-31'),('A','2030-01',b,5000,'medical','2030-07-31'),('A','2030-12',10000,10000,'medical','2030-12-31')]
        first=self.cell(claims(10000,90000));second=self.cell(claims(90000,10000))
        self.assertEqual(first['paid'],200);self.assertEqual(first['paid_completed'],300)
        self.assertEqual(first,second);self.assertEqual(self.cell(claims(10000,90000),False)['paid_completed'],200)
        self.assertNotEqual(vbc._completion(claims(10000,90000),'2030-12-31')[0]['2030-12'],vbc._completion(claims(90000,10000),'2030-12-31')[0]['2030-12'])

    def test_zero_allowed_pharmacy_and_other_population_or_product(self):
        claims=[('A','2030-01',0,5000,'pharmacy','2030-01-31'),('A','2030-01',0,5000,'pharmacy','2030-07-31'),('A','2030-12',0,10000,'pharmacy','2030-12-31')]
        self.assertEqual(self.cell(claims)['paid_completed'],300)
        self.db.execute("INSERT INTO member_month VALUES('B','2030-01','medicare_advantage')")
        extra=[('B','2030-01',10000000,10000000,'medical','2030-01-01'),('OUT','2030-01',10000000,10000000,'medical','2030-01-01'),('A','2030-12',1,99999,'medical','2031-01-01')]
        self.assertEqual(self.cell(claims+extra),self.cell(claims))
        self.assertEqual(self.cell(claims+extra,products=['commercial']),self.cell(claims))


class PublishedRateMeaning(unittest.TestCase):
    orgs={'1':{'organization_id':'ORG','tin':'TIN'}}
    def rate(self,kind='negotiated',rate=100,**kw):
        return {'payer':'Published Plan A','code':'99213','code_type':'CPT','arrangement':'ffs','negotiated_type':kind,
                'billing_class':'professional','rate':rate,'expiration_date':'2031-01-01','npis':['1'],'tins':[],**kw}
    def observed(self,payer='CLAIM-A',amount=100):return {'payer':payer,'code':'99213','npi':'1','median_allowed':amount,'lines':5}

    def test_percent_day_bundle_and_modifier_cannot_be_ratios_to_unit_dollars(self):
        for rate in [self.rate('percentage',40.5),self.rate('per diem'),self.rate(arrangement='bundle'),self.rate(billing_code_modifier=['26'])]:
            out=P.merge_mrf([rate],[self.observed()],self.orgs)[0]
            self.assertIsNone(out['published_vs_observed']);self.assertIsNone(out.get('published_vs_pooled_market'))
            self.assertEqual(out['rate'],rate['rate'])
        dollar=P.merge_mrf([self.rate()],[self.observed()],self.orgs)[0]
        self.assertEqual(dollar['published_vs_pooled_market'],0)

    def test_pooled_market_is_distinct_from_unknown_same_payer_reimbursement(self):
        outputs=[P.merge_mrf([self.rate()],rows,self.orgs)[0] for rows in
                 ([self.observed()],[self.observed(),self.observed('CLAIM-B',900)],[self.observed('CLAIM-B',900)])]
        self.assertEqual([r['observed_allowed_median'] for r in outputs],[100,500,900])
        for r in outputs:
            self.assertIsNone(r.get('same_payer_observed_allowed_median'));self.assertIsNone(r['published_vs_observed'])
            self.assertEqual(r.get('comparison_basis'),'pooled_market_provider_payer_cell_medians')
            self.assertIn('equal weight',r['weighting'])
        self.assertEqual({r['payer'] for r in outputs[1]['observed_contributors']},{'CLAIM-A','CLAIM-B'})

    def test_parser_preserves_rate_context(self):
        doc={'reporting_entity_name':'Publisher','in_network':[{'billing_code':'99213','billing_code_type':'CPT','negotiation_arrangement':'ffs',
             'negotiated_rates':[{'provider_groups':[{'npi':[1]}],'negotiated_prices':[{'negotiated_type':'percentage','negotiated_rate':40.5,
             'billing_class':'professional','service_code':['11'],'setting':'outpatient','billing_code_modifier':['26']}]}]}]}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'rates.json';path.write_text(json.dumps(doc));out=P.parse_mrf(path)[0]
        self.assertEqual(out.get('service_code'),['11']);self.assertEqual(out.get('billing_code_modifier'),['26']);self.assertEqual(out.get('setting'),'outpatient')

