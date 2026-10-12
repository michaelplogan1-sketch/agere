"""Pinned documentary inputs, protocol versions and patient-specific work safety."""
import copy,json,sqlite3,unittest,zlib
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import care_protocols as C,evidence_requirements as R,program_work as W
from clinical_core.agere.clinical import selection_context as S
import test_native_requirements as fixture
from test_warehouse_interpretation import record,ASOF


class RequirementBindings(unittest.TestCase):
 def setUp(self):
  self.f=fixture.NativePersistenceTests();self.f.setUp();self.addCleanup(self.f.tearDown);self.root=self.f.root
  meta=json.loads((self.root/'run.json').read_text());meta['source_run']='fixture';(self.root/'run.json').write_text(json.dumps(meta))
  for pid in ('P-1','P-2'):self.f.db.execute('UPDATE patients SET payload=? WHERE id=?',(json.dumps({'patient_id':pid}),pid))
  self.f.db.commit();self.save([self.screen()]);self.p={'protocol':'affordability_action_review','filter':'supported'}

 def screen(self,rid='screen',**kwargs):return record(rid,'financial_screens',**{'account_id':'bill','difficulty_paying_medical_bills':True,**kwargs})
 def save(self,records):self.f.save('P-1',records)
 def run_protocol(self,ids=None):return C.execute(self.root,ids or ['P-1','P-2'],self.p)

 def test_source_truth_unknown_and_negative_survive_exact_receipts_and_replay(self):
  yes,unknown,out=self.run_protocol();self.assertEqual(yes,{'P-1'});self.assertEqual(unknown,{'P-2'})
  for p in out['patients']:
   row=C.inspect(self.root,p['id']);v=row['bindings']['barrier'];ref=v['requirement_reference']
   self.assertEqual(ref['receipt_hash'],digest(R.inspect(self.root,ref['receipt_id'])))
   self.assertFalse(row['clinical_eligibility_established']);self.assertEqual(row['protocol']['version'],'2')
  self.assertEqual(self.run_protocol()[2]['processing_counts']['patient_cache_hits'],2)
  self.save([self.screen(difficulty_paying_medical_bills=False)])
  yes,unknown,changed=self.run_protocol();self.assertFalse(yes);self.assertEqual(unknown,{'P-2'})
  self.assertEqual(changed['processing_counts']['patient_cache_hits'],1)
  with self.assertRaises(ValueError):C.inspect(self.root,next(p['id'] for p in out['patients'] if p['patient_id']=='P-1'))

 def test_competing_account_and_undated_evidence_remain_unresolved(self):
  for day,status in [('2028-09-01','conflicting_evidence'),(None,'insufficient_evidence')]:
   self.save([self.screen(),self.screen('second',difficulty_paying_medical_bills=False,event_date=day)])
   yes,unknown,out=self.run_protocol(['P-1']);self.assertFalse(yes);self.assertEqual(unknown,{'P-1'})
   self.assertEqual(out['patients'][0]['status'],status)
   row=C.inspect(self.root,out['patients'][0]['id']);self.assertIsNone(row['documentary_readiness'])
   self.assertTrue(row['bindings']['barrier']['requirement_evidence']['bindings']['barrier']['state']['pending_accounts'])

 def test_changed_deleted_or_added_raw_source_cannot_hide_behind_cached_analysis(self):
  self.run_protocol(['P-1'])
  for mode in ('changed','deleted','added'):
   self.f.db.execute("DELETE FROM records WHERE stream='financial_screens'");self.f.db.commit();self.save([self.screen()])
   if mode=='changed':self.f.db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(self.screen(difficulty_paying_medical_bills=False)),'screen'))
   elif mode=='deleted':self.f.db.execute("DELETE FROM records WHERE id='screen'")
   else:
    r=self.screen('new',event_date='2028-09-03',difficulty_paying_medical_bills=False)
    self.f.db.execute('INSERT INTO records VALUES (?,?,?,?)',('new','P-1','financial_screens',json.dumps(r)))
   self.f.db.commit()
   with self.assertRaisesRegex(ValueError,'Native requirement inputs changed'):self.run_protocol(['P-1'])

 def test_missing_wrong_patient_or_wrong_version_receipt_is_rejected(self):
  self.run_protocol(['P-1']);spec=C.definition(self.p['protocol']);binding=spec['bindings']['barrier']
  snap=R.snapshot(self.root,{'contract':'native_affordability_report','filter':'all'});key=R.receipt_id(snap,'P-1');row=R.inspect(self.root,key)
  for bad in (None,{**row,'patient_id':'P-2'},{**row,'as_of':'2020-01-01'},{**row,'contract':{**row['contract'],'version':'changed'}},{**row,'value':False}):
   with self.assertRaises(ValueError):C.requirement_binding(bad,binding,'P-1',ASOF)
  raw=copy.deepcopy(spec);raw.pop('hash');raw.pop('authority');raw['bindings']['barrier']['contract_hash']='bad'
  with self.assertRaisesRegex(ValueError,'hash changed'):C.validate_definition(raw)

 def test_three_valued_composition_can_use_requirement_for_readiness_only(self):
  _,_,out=self.run_protocol(['P-1']);row=C.inspect(self.root,out['patients'][0]['id']);requirement=row['bindings']['barrier']['requirement_evidence']
  spec=copy.deepcopy(C.definition(self.p['protocol']));spec['bindings']['adult']={'kind':'age','label':'adult','op':'ge','value':18};spec['trigger']='adult';spec['readiness']={'all':['adult','barrier']}
  result=C.evaluate({'patient_id':'P-1','birth_date':'1980-01-01'},[],[],None,spec,ASOF,requirement_rows={'native_affordability_report':requirement})
  self.assertTrue(result['trigger']);self.assertTrue(result['documentary_readiness'])
  requirement=copy.deepcopy(requirement);requirement.update(value=None,status='unresolved')
  result=C.evaluate({'patient_id':'P-1','birth_date':'1980-01-01'},[],[],None,spec,ASOF,requirement_rows={'native_affordability_report':requirement})
  self.assertTrue(result['trigger']);self.assertIsNone(result['documentary_readiness'])
  for expected,value in [('contradicted',False),('known',True)]:
   b={**spec['bindings']['barrier'],'expected':expected};self.assertIs(C.requirement_binding(row['bindings']['barrier']['requirement_evidence'],b,'P-1',ASOF)['value'],value)

 def test_old_definition_is_exact_archived_source_not_reinterpreted(self):
  spec=C.definition('affordability_action_review@1');self.assertEqual(spec['bindings']['barrier']['kind'],'documentary')
  self.assertEqual(spec['hash'],digest(json.loads(C.HISTORY.read_text())['protocols'][0]))
  self.assertEqual(C.definition('affordability_action_review@2'),C.definition('affordability_action_review'))
  with self.assertRaises(ValueError):C.definition('affordability_action_review@99')

 def test_current_and_explicit_version_selection_provenance_is_separate(self):
  self.run_protocol();prep=C.prepare(self.root,self.p)
  ctx=S.contexts(self.root,['P-1'],[{'id':'rule','parameters':self.p}],{'rule':prep},evaluated_patients={'rule':{'P-1'}})
  self.assertEqual(ctx['P-1']['protocols'][0]['protocol_id'],'affordability_action_review@2');S.verify(self.root,list(ctx.values()))
  self.save([self.screen(difficulty_paying_medical_bills=False)])
  with self.assertRaises(ValueError):S.verify(self.root,list(ctx.values()))

 def test_program_keeps_requirement_evidence_and_stops_stale_work(self):
  key='protocol:affordability_action_review';r=W.prepare(self.root,key,['P-1','P-2']);self.assertEqual(r['counts']['candidates'],1)
  W.materialize(self.root,r['proposal_id'],'ISOLATED TEST FIXTURE');item=W.list_items(self.root)['items'][0]
  a=W.inspect_item(self.root,item['id'])['assessment'];self.assertIn('screen',{e['record_id'] for e in a['evidence']})
  self.assertEqual(a['review']['protocol_evidence']['bindings']['barrier']['requirement_reference']['version'],'1')
  self.save([self.screen(difficulty_paying_medical_bills=False)])
  with self.assertRaises(W.WorkConflict):W.materialize(self.root,r['proposal_id'],'ISOLATED TEST FIXTURE')
  with self.assertRaises(W.WorkConflict):W.change(self.root,{'item_id':item['id'],'expected_revision':item['revision'],'action':'assign','actor':'ISOLATED TEST FIXTURE','request_id':'stale','data':{'owner':'Fixture','due_date':None}})


if __name__=='__main__':unittest.main()
