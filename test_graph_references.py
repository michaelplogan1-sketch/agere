"""Exact dated reference links remain patient-bound, unresolved and invalidatable."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import graph_references as R,graph_contracts as C,graph_decision as G

class ReferenceGraph(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
  with sqlite3.connect(self.path/'stack.sqlite3') as db:db.execute('CREATE TABLE result_cache(id PRIMARY KEY,payload)')
  self.ref=R.catalog(self.path)[0];self.p={'reference_id':self.ref['id'],'available_by':'2029-08-15','bindings':[{'question_key':'access','node_ids':['f']}]}
  self.n={'id':'r','operation':'reference_review','inputs':['all','f'],'parameters':self.p}
  self.f={'id':'f','operation':'financial_accounts','inputs':['all'],'parameters':{'available_by':'2029-08-15'}}
  self.members=[{'patient_id':'a','receipt_id':'f-a','receipt_digest':'financial-a','status':'received','membership':None,'report_only':True}]
  self.results=[{'node':self.f,'result':{'review_members':self.members,'population_digest':digest(['a'])}}]
 def run_ref(self):return R.execute(self.path,['a','b'],self.p,R.prepare(self.path,self.p),self.results,self.n,'Original clinical and operational question',{'counts':{'matched':2}})
 def test_full_population_missing_patient_and_clinical_limits(self):
  r=self.run_ref();self.assertEqual(r['patients_evaluated'],2);self.assertEqual(r['unresolved_questions'],12)
  self.assertEqual(r['linked_patient_counts_by_question'],{'access':1})
  self.assertTrue(all(m['membership'] is None for m in r['review_members']));self.assertEqual(G.review_tags(r['review_members']),[])
  for m in r['review_members']:
   f,gaps=R.evidence(self.path,m,self.n,[{'node_id':'f','operation':'financial_accounts',**self.members[0]}] if m['patient_id']=='a' else [])
   self.assertEqual(len(gaps),6);self.assertEqual(f['report']['original_question'],'Original clinical and operational question')
   self.assertFalse(f['report']['clinical_applicability_established']);self.assertFalse(f['report']['reference_currency']['current_at_patient_decision'])
   self.assertTrue(all(q['status']=='applicability_unresolved' for q in f['report']['questions']))
 def test_reference_cannot_be_cohort_or_first_input(self):
  for op in ['union','outcome','reference_review']:
   with self.assertRaises(ValueError):C.validate_inputs(op,['r'],{'r':'reference_review'})
  C.validate_inputs('reference_review',['all','f'],{'all':'cohort','f':'financial_accounts'})
  with self.assertRaises(ValueError):C.validate_inputs('reference_review',['all','other'],{'all':'cohort','other':'cohort'})
 def test_empty_binding_retains_explicit_missing_question(self):
  self.p['bindings'].append({'question_key':'population','node_ids':[]})
  R.validate(self.p,'2029-08-15',self.n['inputs'],[self.f]);r=self.run_ref()
  f,g=R.evidence(self.path,r['review_members'][0],self.n,[{'node_id':'f','operation':'financial_accounts',**self.members[0]}])
  self.assertEqual(next(q for q in f['report']['questions'] if q['key']=='population')['evidence_status'],'no_linked_patient_evidence')
 def test_undeclared_future_duplicate_and_omitted_bindings_rejected(self):
  R.validate(self.p,'2029-08-15',self.n['inputs'],[self.f])
  for delta in [{'bindings':[]},{'bindings':[{'question_key':'access','node_ids':['later']}]},{'bindings':self.p['bindings']*2},{'available_by':'2029-08-14'}]:
   with self.subTest(delta=delta),self.assertRaises(ValueError):R.validate({**self.p,**delta},'2029-08-15',self.n['inputs'],[self.f])
 def test_reference_inspection_cutoff_and_unknown_reference_question_rejected(self):
  for delta in [{'available_by':'2025-01-01'},{'reference_id':'0'*64},{'bindings':[{'question_key':'invented','node_ids':['f']}]}]:
   with self.subTest(delta=delta),self.assertRaises(ValueError):R.prepare(self.path,{**self.p,**delta})
 def test_pre_event_cutoff_follows_anchor_and_snapshot_state_is_not_backdated(self):
  temporal={'id':'f','operation':'pre_event_state','inputs':['event'],'parameters':{}}
  event={'id':'event','operation':'outcome','inputs':['all'],'parameters':{'available_by':'2029-08-15'}}
  with self.assertRaisesRegex(ValueError,'cutoff'):R.validate({**self.p,'available_by':'2029-08-14'},'2029-08-15',self.n['inputs'],[event,temporal])
  with self.assertRaisesRegex(ValueError,'cutoff'):R.validate({**self.p,'available_by':'2029-08-14'},'2029-08-15',self.n['inputs'],[{**self.f,'operation':'source_state','parameters':{}}])
 def test_exact_patient_and_upstream_receipt_cannot_be_substituted(self):
  r=self.run_ref()['review_members'][0];bound={'node_id':'f','operation':'financial_accounts',**self.members[0]}
  for wrong in [{**bound,'receipt_id':'other'},{**bound,'patient_id':'b'}]:
   with self.assertRaisesRegex(ValueError,'exact patient'):R.evidence(self.path,r,self.n,[wrong])
  with self.assertRaisesRegex(ValueError,'identity'):R.evidence(self.path,{**r,'patient_id':'b'},self.n,[bound])
 def test_tampered_saved_reference_receipt_is_rejected(self):
  r=self.run_ref()['review_members'][0]
  with sqlite3.connect(self.path/'stack.sqlite3') as db:
   row=json.loads(db.execute('SELECT payload FROM result_cache WHERE id=?',(r['receipt_id'],)).fetchone()[0]);row['clinical_applicability_established']=True
   db.execute('UPDATE result_cache SET payload=? WHERE id=?',(json.dumps(row),r['receipt_id']))
  with self.assertRaisesRegex(ValueError,'integrity'):R.evidence(self.path,r,self.n,[])
 def test_population_duplicate_or_digest_mismatch_rejected(self):
  for members in [self.members*2,[]]:
   self.results[0]['result']['review_members']=members
   with self.assertRaisesRegex(ValueError,'population'):self.run_ref()
 def test_changed_lifecycle_stales_links_and_recompute_preserves_original(self):
  first=self.run_ref();saved=first['review_members'][0];prep=R.prepare(self.path,self.p)
  state={**prep['lifecycle'],'status':'withdrawn_locally','revision':1}
  with patch.object(R.L,'state',return_value=state):
   with self.assertRaisesRegex(ValueError,'lifecycle changed'):R.evidence(self.path,saved,self.n,[])
   second=self.run_ref();self.assertNotEqual(first['reference_dependency'],second['reference_dependency'])
   self.assertEqual(second['member_status_counts'],{'reference_unavailable_requires_reassessment':2})
  with sqlite3.connect(self.path/'stack.sqlite3') as db:self.assertIsNotNone(db.execute('SELECT payload FROM result_cache WHERE id=?',(saved['receipt_id'],)).fetchone())
 def test_change_during_execution_rejected(self):
  with patch.object(R,'prepare',side_effect=[R.prepare(self.path,self.p),{'id':'changed'}]):
   with self.assertRaisesRegex(ValueError,'during'):self.run_ref()
 def test_schema_uses_exact_registered_reference_and_question_keys(self):
  s=C.parameter_schema('reference_review',{'cohort_fields':{'record_fields':{}},'evidence_catalog':{'scientific_references':[self.ref]}})
  self.assertEqual(s['properties']['reference_id']['enum'],[self.ref['id']])
  self.assertEqual(len(s['properties']['bindings']['items']['properties']['question_key']['enum']),6)

class ReferenceScope(unittest.TestCase):
 def test_selected_reference_keys_are_exact_and_consumed(self):
  from clinical_core.agere.clinical import planner_scope as S
  def raw(segment):return {'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps({'segments':[segment]})}]}
  good={'text':'Review science.','operations':['reference_review'],'source_streams':[],'reference_ids':['a'*64],'unmet':''}
  self.assertEqual(S.selection(raw(good),good['text'],['reference_review'],[],['a'*64])['segments'][0],good)
  for delta in [{'reference_ids':['b'*64]},{'reference_ids':[]},{'reference_ids':['a'*64]*2},{'operations':['cohort']}]:
   with self.subTest(delta=delta),self.assertRaises(ValueError):S.selection(raw({**good,**delta}),good['text'],['reference_review','cohort'],[],['a'*64])
 def test_provider_and_planning_bound_agree(self):
  from clinical_core.agere.clinical import planner_scope as S
  self.assertEqual(S.PLAN_MAX_BYTES,40000)
  self.assertFalse(S.bounded({'text':'x'*40000},S.PLAN_MAX_BYTES))

if __name__=='__main__':unittest.main()
