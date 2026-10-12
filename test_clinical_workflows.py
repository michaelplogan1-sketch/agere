import json,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical.facts import extract,validate_model_facts
from clinical_core.agere.clinical.reconcile import reconcile
from clinical_core.agere.clinical.workflows import execute
from clinical_core.agere.clinical.challenges import cases,public_cases
from clinical_core.agere.clinical.model_reader import requests,parse,compare,score
from clinical_core.agere.operators import patient_analysis
from clinical_core.agere.policy import Program
from clinical_core.agere.gateway import BudgetLedger,PersistentGateway

CUT='2026-09-30'

def r(text='',**kw):return {'record_id':'r','patient_id':'p','source_table':'notes','event_date':'2026-09-01','available_date':'2026-09-02','identity_status':'matched','synthetic':True,'text':text,**kw}

def go(rows):
 b=extract('p',rows,CUT);s=reconcile(b)
 ps=[Program(x) for x in json.loads(Path('config/agere/programs.json').read_text())]
 return s,execute(s,rows,patient_analysis({'patient_id':'p'},rows,CUT,ps))

class FactAndWorkflowTest(unittest.TestCase):
 def test_cross_patient_future_identity_rejected(self):
  for kw in ({'patient_id':'q'},{'available_date':'2026-10-01'},{'identity_status':'unresolved'}):
   with self.assertRaises(ValueError):extract('p',[r(**kw)],CUT)
 def test_same_day_financial_conflict_abstains(self):
  rows=[r('Patient cannot afford medication.'),r('',record_id='f',source_table='financial',difficulty_paying_medical_bills=False)]
  s,w=go(rows);self.assertEqual(s['affordability']['status'],'conflicting_reports')
  self.assertEqual(w['affordability']['status'],'reconcile_conflicting_affordability_reports')
  self.assertIsNone(s['affordability']['value']);self.assertFalse(w['affordability']['care_gap_established'])
 def test_arrival_does_not_overwrite_newer_statement(self):
  rows=[r('',source_table='financial',difficulty_paying_medical_bills=True,event_date='2026-08-01',available_date='2026-09-20'),
        r('',record_id='f',source_table='financial',difficulty_paying_medical_bills=False)]
  s,w=go(rows);self.assertFalse(s['affordability']['value']);self.assertTrue(s['affordability']['prior_disagreement'])
  self.assertFalse(w['affordability']['review_candidate'])
 def test_copy_forward_does_not_refresh_hardship(self):
  rows=[r('Patient cannot afford medication.',event_date='2026-07-01',available_date='2026-07-02'),
        r('Patient cannot afford medication.',record_id='copy',event_date='2026-09-20',available_date='2026-09-20'),
        r('',record_id='f',source_table='financial',difficulty_paying_medical_bills=False)]
  s,w=go(rows);self.assertFalse(s['affordability']['value'])
 def test_historical_ef_not_current_phenotype(self):
  s,w=go([r('Prior EF 30%.')]);self.assertEqual(s['ef']['status'],'historical_only')
  self.assertEqual(w['heart_failure']['status'],'current_phenotype_unresolved')
 def test_updated_ef_excludes_old_low_ef_screen(self):
  s,w=go([r('Prior EF 30%; current EF 60%.')]);self.assertEqual(s['ef']['latest_range'],[60,60])
  self.assertFalse(w['heart_failure']['review_candidate'])
 def test_disjoint_same_day_ef_ranges_conflict(self):
  s,w=go([r('EF 30%.'),r('EF 60%.',record_id='b')]);self.assertEqual(s['ef']['status'],'conflicting_reports')
  self.assertEqual(w['heart_failure']['status'],'resolve_ef_conflict')
 def test_family_member_not_hf_candidate(self):
  s,w=go([r('Her brother has EF 25%; her EF is 60%.')]);self.assertEqual(s['ef']['latest_range'],[60,60])
 def test_order_does_not_establish_use_or_dose_appropriateness(self):
  s,w=go([r('Current EF 30%.'),r('',record_id='o',source_table='medication_orders',drug_class='mra',drug='spironolactone',dose='25 mg daily')])
  m=next(m for m in w['heart_failure']['medications'] if m['drug_class']=='mra')
  self.assertEqual(m['evidence_state'],'order_only');self.assertEqual(m['maximum_tolerated_dose'],'unknown')
 def test_cash_supply_covers_missing_insurance_claim(self):
  s,w=go([r('Current EF 30%.'),r('',record_id='cash',source_table='medication_history',drug_class='sglt2',status='dispensed',days_supply=90,payment_channel='cash',dose='10 mg daily')])
  m=next(m for m in w['heart_failure']['medications'] if m['drug_class']=='sglt2')
  self.assertEqual(m['evidence_state'],'supply_documented');self.assertEqual(m['actual_current_use'],'unknown')
 def test_hold_and_multiple_drugs_not_assigned_to_every_drug(self):
  s,w=go([r('Current EF 30%. Spironolactone held because of symptoms; empagliflozin continues.')])
  ms={m['drug_class']:m for m in w['heart_failure']['medications']}
  self.assertEqual(ms['mra']['evidence_state'],'exception_needs_reconciliation')
  self.assertNotEqual(ms['sglt2']['evidence_state'],'exception_needs_reconciliation')
 def test_reversed_claim_not_supply(self):
  s,w=go([r('EF 30%.'),r('',record_id='paid',source_table='pharmacy_claims',drug_class='mra',status='dispensed',transaction_type='paid',dispense_id='d',days_supply=90),
          r('',record_id='reverse',source_table='pharmacy_claims',drug_class='mra',status='dispensed',transaction_type='reversed',dispense_id='d',days_supply=90,available_date='2026-09-03')])
  self.assertNotEqual(next(m for m in w['heart_failure']['medications'] if m['drug_class']=='mra')['evidence_state'],'supply_documented')
 def test_kidney_requested_referral_not_missing_care(self):
  rows=[r('',record_id=str(i),source_table='labs',test='egfr',value=v,unit='mL/min/1.73m2',event_date=d,available_date=d) for i,(d,v) in enumerate([('2026-01-01',45),('2026-05-01',35),('2026-09-01',25)])]
  rows+=[r('',record_id='ref',source_table='referrals',service='nephrology',status='requested')]
  s,w=go(rows);self.assertEqual(w['kidney']['status'],'verify_referral_followthrough')
  self.assertFalse(w['kidney']['care_gap_established']);self.assertEqual(w['kidney']['creatinine_trend']['status'],'insufficient_distinct_dates')
 def test_missing_data_not_negative_findings(self):
  s,w=go([]);self.assertEqual(w['affordability']['status'],'affordability_unknown')
  self.assertEqual(w['kidney']['status'],'insufficient_kidney_data')
 def test_unseen_concept_model_fact_grounding_not_authority(self):
  p={'kind':'symptom','concept':'orthostatic_dizziness','value':'reported','assertion':'affirmed','subject':'patient','source_id':'x','quote':'Dizzy on standing'}
  v=validate_model_facts([p],[{'id':'x','text':'Dizzy on standing today.'}])[0]
  self.assertFalse(v['decision_authority']);self.assertEqual(v['source_spans'],[{'start':0,'end':17}])
 def test_invented_quote_or_source_rejected(self):
  p={'kind':'diagnosis','concept':'ckd','value':'present','assertion':'affirmed','subject':'patient','source_id':'x','quote':'CKD present'}
  with self.assertRaises(ValueError):validate_model_facts([p],[{'id':'x','text':'CKD absent'}])
  p['source_id']='invented'
  with self.assertRaises(ValueError):validate_model_facts([p],[{'id':'x','text':'CKD present'}])
 def test_duplicate_quote_not_invented_unique_location(self):
  p={'kind':'symptom','concept':'dyspnea','value':'present','assertion':'affirmed','subject':'patient','source_id':'x','quote':'dyspnea'}
  x=validate_model_facts([p],[{'id':'x','text':'dyspnea and dyspnea'}])[0];self.assertFalse(x['unique_span']);self.assertEqual(len(x['source_spans']),2)

class ReaderContractTest(unittest.TestCase):
 def test_references_not_sent_to_any_provider(self):
  for provider in ('jev','perplexity','claude'):
   body=json.dumps(requests(cases(),provider));self.assertNotIn('reference_rationale',body);self.assertNotIn('reference_status',body)
 def test_typed_schema_and_probabilities(self):
  c=cases()[0];opts=list(c['options']);support=c['evidence'][0]['id']
  raw={'model':PersistentGateway.MODELS['jev'],'answers':{c['case_id']:{'type':'choice','choice':'no','probabilities':{'yes':.1,'no':.8,'unknown':.1}},c['case_id']+'_support':{'type':'choice','choice':support,'probabilities':{support:.9,'unknown':.1}}}}
  result=parse([c],'jev',raw);self.assertEqual(result[0]['label'],'no')
  raw['answers'][c['case_id']]['probabilities']['yes']=float('nan')
  with self.assertRaises(ValueError):parse([c],'jev',raw)
 def test_frontier_semantic_proposals_not_promoted(self):
  c=cases()[0];v={c['case_id']:{'label':'no','evidence_ids':[c['evidence'][0]['id']],'facts':[]}}
  raw={'model':PersistentGateway.MODELS['claude'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(v)}]}
  self.assertEqual(parse([c],'claude',raw)[0]['semantic_validation'],'pending')
  raw['stop_reason']='max_tokens'
  with self.assertRaises(ValueError):parse([c],'claude',raw)
 def test_offline_never_reserves(self):
  with tempfile.TemporaryDirectory() as d:
   l=BudgetLedger(Path(d)/'budget');x=compare(cases()[:1],'perplexity',l)
   self.assertEqual(x['status'],'prepared');self.assertEqual(l.receipt()['current_reserved_usd'],0);l.close()

if __name__=='__main__':unittest.main()

class AdditionalBoundaryTest(unittest.TestCase):
 def test_flat_frontier_contract(self):
  c=cases()[0];v={'answers':[{'case_id':c['case_id'],'label':'no','evidence_ids':[c['evidence'][0]['id']],'facts':[]}]}
  raw={'model':PersistentGateway.MODELS['claude'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(v)}]}
  self.assertEqual(parse([c],'claude',raw)[0]['label'],'no')
  v['answers'][0]['evidence_ids']=['another_patient_source'];raw['content'][0]['text']=json.dumps(v)
  with self.assertRaises(ValueError):parse([c],'claude',raw)
 def test_future_or_duplicate_requests_rejected_offline(self):
  c=cases()[0]
  with self.assertRaises(ValueError):requests([c,c],'jev')
  c['evidence'][0]['date']='2027-01-01'
  with self.assertRaises(ValueError):requests([c],'claude')
 def test_non_string_model_context_rejected(self):
  p={'kind':'symptom','concept':'dyspnea','value':'present','assertion':'affirmed','subject':[],'source_id':'x','quote':'dyspnea'}
  with self.assertRaises(ValueError):validate_model_facts([p],[{'id':'x','text':'dyspnea'}])
 def test_unsupported_natural_question_not_full_answer(self):
  from clinical_core.agere.clinical.query import query
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'heart_failure.json').write_text(json.dumps({'workflow':'heart_failure'}))
   self.assertFalse(query(p,'Find every HFrEF patient not on maximum tolerated dose')['question_fully_answered'])
   self.assertTrue(query(p,'Review heart failure treatment evidence')['question_fully_answered'])
   self.assertEqual(query(p,'Why sepsis readmissions?')['status'],'unsupported_question')
 def test_failed_fact_quote_does_not_invalidate_valid_decision(self):
  c=cases()[0];v={c['case_id']:{'label':'no','evidence_ids':[c['evidence'][0]['id']],'facts':[{'kind':'laboratory','concept':'ef','value':'25','assertion':'affirmed','subject':'patient','source_id':c['evidence'][0]['id'],'quote':'invented words'}]}}
  raw={'model':PersistentGateway.MODELS['claude'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(v)}]}
  out=parse([c],'claude',raw)[0];self.assertEqual(out['label'],'no');self.assertEqual(out['extracted_facts'],[]);self.assertEqual(len(out['rejected_facts']),1)

class SettlementTest(unittest.TestCase):
 def test_settlement_preserves_failed_pending_and_cap(self):
  with tempfile.TemporaryDirectory() as d:
   l=BudgetLedger(Path(d)/'db',cap_usd=2)
   for key in ('success','failed','pending'):l.reserve(key,'jev','jev-1.13.0')
   l.finish('success',response={'model':'jev-1.13.0','usage':{'input_tokens':1000,'output_tokens':5}})
   l.finish('failed',error='HTTP 400')
   entries=l.settle_completed();self.assertEqual(len(entries),1)
   self.assertEqual(dict(l.db.execute('SELECT id,reserved FROM calls')),{'success':.02,'failed':.5,'pending':.5})
   self.assertEqual(l.receipt()['authorized_cap_usd'],2);self.assertEqual(l.settle_completed(),[]);l.close()
 def test_unknown_usage_never_released(self):
  with tempfile.TemporaryDirectory() as d:
   l=BudgetLedger(Path(d)/'db');l.reserve('x','jev','jev-1.13.0');l.finish('x',response={'model':'jev-1.13.0'})
   self.assertEqual(l.settle_completed(),[]);self.assertEqual(l.receipt()['current_reserved_usd'],.5);l.close()
