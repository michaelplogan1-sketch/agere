"""Received names and measurements stay source-bound; they confer no care role."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.deliverables import patient_evidence as P,answer_context as A

class Profiles(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/'build'/'stack';self.path.mkdir(parents=True)
  (self.path/'run.json').write_text(json.dumps({'source_digest':'source','snapshot':{'release':'release','as_of':'2030-05-27'}}))
  with sqlite3.connect(self.path/'stack.sqlite3')as db:
   db.execute('CREATE TABLE patients(id PRIMARY KEY,payload)')
   for pid,name,day in [('A','Alex Example','2026-01-01'),('B','Future Name','2031-01-01')]:
    db.execute('INSERT INTO patients VALUES(?,?)',(pid,json.dumps({'patient_id':pid,'display_name':name,'birth_date':'1970-06-01','profile_captured_date':day,'sex_reported':'F'})))
  with sqlite3.connect(self.path.parent/'warehouse.sqlite3')as db:
   db.execute('CREATE TABLE source_entities(release,kind,id,payload)')
   for release,name in [('release','Dr River Example'),('other','Wrong Release')]:
    db.execute('INSERT INTO source_entities VALUES(?,?,?,?)',(release,'clinicians','C1',json.dumps({'clinician_id':'C1','display_name':name,'specialty':'Cardiology'})))
  with sqlite3.connect(self.path/'semantic.sqlite3')as db:
   db.executescript("CREATE TABLE meta(key,value); INSERT INTO meta VALUES('source_digest','source'),('as_of','2030-05-27'),('version','test'); CREATE TABLE patient(patient_id PRIMARY KEY,pcp_clinician_id,coverage_type); INSERT INTO patient VALUES('A','C1','commercial'),('B',NULL,NULL); CREATE TABLE measurement_latest(patient_id,concept,event_date,value_canonical,unit_canonical,record_id);")
   db.execute('INSERT INTO measurement_latest VALUES(?,?,?,?,?,?)',('A','potassium','2030-05-01',4.2,'mmol/L','K1'))
  self.answer={'status':'partial','answer_type':'table','headline':'Review source evidence','missing':['Tolerance unresolved'],
   'table':[{'patient_id':'A','care_assignments':json.dumps([{'clinician_id':'C1','role':'consulting_specialist','assigned_at_snapshot':0}])}]}

 def test_exact_names_clocks_measurements_and_role_separation(self):
  p=P.capture_profiles(self.path,['A'],self.answer)
  self.assertEqual(p['patients']['A']['display_name'],'Alex Example')
  self.assertEqual(p['patients']['A']['age_at_snapshot'],59)
  self.assertEqual(p['clinicians']['C1']['display_name'],'Dr River Example')
  self.assertEqual(p['patients']['A']['latest_measurements'][0]['record_id'],'K1')
  self.assertNotIn('assigned_at_snapshot',p['clinicians']['C1'])
  self.assertEqual(self.answer['table'][0]['care_assignments'],json.dumps([{'clinician_id':'C1','role':'consulting_specialist','assigned_at_snapshot':0}]))

 def test_future_profile_does_not_leak_name_or_birth_date(self):
  p=P.capture_profiles(self.path,['B'],{'table':[{'patient_id':'B'}]})['patients']['B']
  self.assertEqual(p['identity_status'],'profile_after_snapshot')
  self.assertIsNone(p['display_name']);self.assertIsNone(p['birth_date']);self.assertIsNone(p['age_at_snapshot'])

 def test_current_native_shape_keeps_source_assignment_clock_and_fill_scope(self):
  row={'patient_id':'A','on_raas_inhibitor':1,'on_beta_blocker':0,'on_mra':0,'on_sglt2i':0,
   'missing_pillars':'beta blocker','dose_evidence':'[]',
   'structured_care_team':[{'patient_id':'A','role':'primary_care','clinician_id':'C1','assignment_status':'documented_assignment'}]}
  native={'patient_id':'A','role':'primary_care','clinician_id':'C1','assigned_at_snapshot':1,
   'assignment_status':'documented_assignment','effective_start':'2029-01-01'}
  calls=[{'name':'query_sql','input':{'sql':'SELECT * FROM {{drug_fill:ace_inhibitor}}'},'result':{}},
   {'name':'query_sql','input':{'sql':'SELECT * FROM {{care_team}}'},'result':{'result_view':'agere_result_2','columns':list(native),'rows':[native],'truncated':False}}]
  answer={'table':[row]};before=json.dumps(answer,sort_keys=True)
  profiles=P.capture_profiles(self.path,['A'],answer)
  profiles['patients']['A']['registered_cohort_evidence']=[{'view':'phenotype_hfref','as_of':'2030-05-27',
   'fields':{'patient_id':'A','hfref_basis':'coded_hfref','lowest_documented_ef':30}}]
  c={'answer':answer,'patient_profiles':profiles,'investigation':{'tool_calls':calls},'question':'Review','source':{}}
  card=P.review_cards(c)['A']
  self.assertEqual(len(card['therapy']),4)
  self.assertEqual(card['care_team'][0]['current'][0]['name'],'Dr River Example')
  self.assertIn('Historical fill',card['therapy'][0]['observed_status'])
  self.assertIn('no recency window',card['therapy'][0]['fill_scope'])
  self.assertIn('returned history',card['program_review']['rationale'][1])
  self.assertTrue(any(v['label']=='Lowest documented EF'for v in card['clinical_context']))
  self.assertEqual(json.dumps(answer,sort_keys=True),before)
  answer['grounding']=['Pillar use: picked-up fills in the 12 months ending 2030-05-27. Historical queries remain for audit.']
  card=P.review_cards(c)['A']
  self.assertEqual(card['therapy'][0]['observed_status'],'Fill observed in the saved analysis')
  self.assertIn(answer['grounding'][0],card['therapy'][0]['fill_scope'])
  row.update(fill_window_start_exclusive='2029-05-27',fill_window_end_inclusive='2030-05-27')
  card=P.review_cards(c)['A']
  self.assertEqual(card['therapy'][0]['observed_status'],'Recent fill observed')
  self.assertIn('after 2029-05-27 through 2030-05-27',card['therapy'][0]['fill_scope'])
  from clinical_core.agere.deliverables.patient_dashboard import render
  self.assertIn('esc(t.fill_scope)',render(c))

 def test_unknown_identity_does_not_acquire_another_persons_name(self):
  p=P.capture_profiles(self.path,['UNKNOWN'],{'table':[{'patient_id':'UNKNOWN'}]})
  self.assertEqual(p['patients'],{});self.assertEqual(p['clinicians'],{})

 def test_changed_profile_invalidates_new_work_without_erasing_saved_name(self):
  c=A.capture(self.path,'Inspect the selected patients',{'answer':self.answer})
  self.assertFalse(A.load(self.path,c['id'])['stale'])
  with sqlite3.connect(self.path/'stack.sqlite3')as db:
   row=json.loads(db.execute("SELECT payload FROM patients WHERE id='A'").fetchone()[0]);row['display_name']='Corrected Name'
   db.execute("UPDATE patients SET payload=? WHERE id='A'",(json.dumps(row),))
  old=A.load(self.path,c['id']);self.assertTrue(old['stale'])
  self.assertEqual(old['patient_profiles']['patients']['A']['display_name'],'Alex Example')
  with self.assertRaisesRegex(ValueError,'data changed'):A.load(self.path,c['id'],current=True)

 def test_staging_identity_mismatch_is_rejected(self):
  with sqlite3.connect(self.path/'stack.sqlite3')as db:db.execute("UPDATE patients SET payload=? WHERE id='A'",(json.dumps({'patient_id':'B','display_name':'Wrong'}),))
  with self.assertRaisesRegex(ValueError,'differs'):P.capture_profiles(self.path,['A'],self.answer)

 def test_clinical_review_retains_pillar_sources_and_does_not_infer_current_care(self):
  row=self.answer['table'][0]
  row.update({'hfref_basis':'coded_hfref','lowest_documented_ef':30,'latest_low_ef_date':'2029-01-01',
   'on_rasi_pillar':1,'on_beta_blocker_pillar':0,'on_mra_pillar':0,'on_sglt2i_pillar':0,'pillars_missing':'beta_blocker MRA SGLT2i',
   'recent_gdmt_fill_observations':json.dumps([{'drug_class':'ace_inhibitor','drug_name':'Lisinopril','latest_fill_date_in_window':'2030-05-01'}]),
   'gdmt_dose_rows':json.dumps([{'drug_key':'metoprolol_succinate','statement_state':'planned','documented_daily_mg':None,'reference_max_status':'unknown_dose'}]),
   'documentary_findings':json.dumps([{'entity':{'id':'raas_inhibitor'},'relation':'treatment_reason','quote':'Previously stopped for dizziness.','date':day,'record_id':rid,'polarity':'affirmed','temporality':'historical','subject':'patient'}for day,rid in [('2028-01-01','R1'),('2029-01-01','R2')]])})
  assignments=json.loads(row['care_assignments']);assignments[0]['specialty']='Cardiology';row['care_assignments']=json.dumps(assignments)
  c={'answer':self.answer,'patient_profiles':P.capture_profiles(self.path,['A'],self.answer),'investigation':{}}
  card=P.review_cards(c)['A'];self.assertEqual(card['patient_name'],'Alex Example')
  self.assertEqual(card['care_team'][1]['current'],[])
  self.assertEqual(card['care_team'][1]['other_records'][0]['name'],'Dr River Example')
  self.assertEqual(card['therapy'][0]['fills'][0]['drug_name'],'Lisinopril')
  self.assertEqual(card['therapy'][1]['dose_observations'][0]['statement_state'],'planned')
  reasons=card['therapy'][0]['documented_reasons'];self.assertEqual(len(reasons),1)
  self.assertEqual({s['record_id']for s in reasons[0]['sources']},{'R1','R2'})
  self.assertEqual(len(reasons[0]['findings']),2)
  self.assertFalse(card['program_review']['eligibility_established'])
  self.assertNotIn('date',card['clinical_context'][1])
  from clinical_core.agere.deliverables.patient_dashboard import exports,render
  c.update({'question':'Review these patients','source':{'as_of':'2030-05-27'}})
  out=exports(c);data=json.loads(out['json']);self.assertEqual(data['rows'],self.answer['table'])
  import csv,io
  exported=list(csv.DictReader(io.StringIO(out['csv'].decode())))[0]
  self.assertEqual(exported['patient_name'],'Alex Example');self.assertEqual(exported['patient_id'],'A')
  self.assertEqual(exported['documentary_findings'],row['documentary_findings'])
  text=render(c);self.assertIn('Clinical review',text);self.assertIn('Alex Example',text)

class CurrentNativeCardContracts(unittest.TestCase):
 def context(self):
  def finding(entity,relation,quote,record):
   return {'entity':{'id':entity},'relation':relation,'quote':quote,'record_id':record,'date':'2028-01-01','polarity':'affirmed','temporality':'historical','subject':'patient'}
  findings=[finding('hf_evidence_based_beta_blocker','reason_not_on_therapy','Carvedilol was held for dizziness.','H1'),
   finding('beta_blocker','reason_not_on_therapy','Atenolol was previously stopped.','B1'),
   finding('gdmt','documented_medication_regimen','Carvedilol 12.5 mg twice daily was recorded in 2028.','R1'),
   finding('gdmt','dose_titration_reason','Further carvedilol titration was held for dizziness in 2028.','T1'),
   finding('gdmt','individual_maximum_tolerated_dose','That dose was described as the individual maximum tolerated at the 2028 visit.','M1')]
  dose={'drug_key':'carvedilol','documented_daily_mg':25,'current_use_established':0,'statement_state':'planned','dose_date':'2028-01-01'}
  row={'patient_id':'A','raas_pillar':1,'beta_blocker_pillar':0,'mra_pillar':0,'sglt2_pillar':1,
   'gdmt_dose_evidence':json.dumps([dose]),'documentary_findings':json.dumps(findings),
   'care_team_assignments':json.dumps([{'clinician_id':'C1','role':'primary_care','assigned_at_snapshot':1},
                                    {'clinician_id':'C2','role':'consulting_specialist','specialty':'Cardiology','assigned_at_snapshot':0}]),
   'fill_window_start_exclusive':'2029-05-27','fill_window_end_inclusive':'2030-05-27',
   'registered_pillar_gap_step':'Reconcile the missing fill observations.',
   'registered_dose_step':'Verify the current medication regimen.',
   'cardiology_pcp_step':'Verify the responsible clinicians.'}
  profiles={'patients':{'A':{'display_name':'Alex Example','registered_cohort_evidence':[{'fields':{'on_rasi_pillar':0,'on_beta_blocker_pillar':1,'hfref_basis':'coded_hfref'}}]}},
   'clinicians':{'C1':{'display_name':'Dr River Example'},'C2':{'display_name':'Dr Lake Example'}}}
  return {'answer':{'status':'partial','table':[row]},'patient_profiles':profiles,'investigation':{}}

 def test_registered_result_fields_take_precedence_over_cohort_context(self):
  context=self.context();before=copy.deepcopy(context);card=P.review_cards(context)['A']
  self.assertEqual(len(card['therapy']),4)
  self.assertEqual([t['observed_status']for t in card['therapy']],['Recent fill observed','No recent fill observed','No recent fill observed','Recent fill observed'])
  self.assertEqual(card['all_dose_observations'],json.loads(context['answer']['table'][0]['gdmt_dose_evidence']))
  self.assertEqual(card['therapy'][1]['dose_observations'],card['all_dose_observations'])
  self.assertEqual(card['care_team'][0]['current'][0]['name'],'Dr River Example')
  self.assertEqual(card['care_team'][1]['current'],[])
  self.assertEqual(context,before)

 def test_subtype_reasons_are_visible_without_promoting_a_parent_class_reason(self):
  card=P.review_cards(self.context())['A'];accounts=card['therapy'][1]['documented_reasons']
  self.assertEqual([g['quote']for g in accounts],['Carvedilol was held for dizziness.'])
  self.assertIn('Atenolol was previously stopped.',card['original_result_row']['documentary_findings'])

 def test_regimen_cause_and_individual_tolerance_remain_independent_dated_accounts(self):
  context=self.context();card=P.review_cards(context)['A']
  self.assertEqual([g['relation']for g in card['regimen_statements']],['documented_medication_regimen'])
  self.assertEqual([g['relation']for g in card['dose_limiting_statements']],['dose_titration_reason'])
  self.assertEqual([g['relation']for g in card['individual_tolerance_statements']],['individual_maximum_tolerated_dose'])
  self.assertTrue(all(g['temporality']=='historical'for key in ['regimen_statements','dose_limiting_statements','individual_tolerance_statements']for g in card[key]))
  self.assertFalse(card['program_review']['eligibility_established'])
  self.assertIn('current',card['individual_max_tolerated_dose'].lower())
  self.assertEqual(card['program_review']['proposed_steps'],['Reconcile the missing fill observations.','Verify the current medication regimen.','Verify the responsible clinicians.'])

 def test_absent_tolerance_stays_unknown_without_suppressing_a_regimen(self):
  c=self.context();row=c['answer']['table'][0];row['documentary_findings']=json.dumps([v for v in json.loads(row['documentary_findings'])if v['relation']=='documented_medication_regimen'])
  card=P.review_cards(c)['A'];self.assertTrue(card['regimen_statements']);self.assertEqual(card['individual_tolerance_statements'],[])
  self.assertEqual(card['individual_max_tolerated_dose'],'Not established by the reviewed evidence')

if __name__=='__main__':unittest.main()
