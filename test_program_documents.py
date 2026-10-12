"""Quoted specifications share typed evidence without manufacturing eligibility."""
import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import program_documents as D,evidence_requirements as R,care_protocols as C
from clinical_core.agere.clinical import selection_context as S,program_work as W,program_registry as legacy
from clinical_core.agere.clinical.economical_planner import local_plan
from test_protocol_requirements import RequirementBindings as NativeFixture
from test_warehouse_interpretation import ASOF


def package(version='1',extra=False,known=False):
 rule=R.definition('native_affordability_report');document='SYNTHETIC ENGINEERING SPECIFICATION. Review received reports of difficulty paying medical bills. Attribution must be verified separately.'
 quote='reports of difficulty paying medical bills';start=document.index(quote)
 bindings={'report':{'start':start,'end':start+len(quote),'quote':quote}}
 inputs={'report':{'kind':'requirement','label':'Received affordability report','contract':rule['id'],'version':rule['version'],'contract_hash':rule['hash'],'expected':'known' if known else 'supported'}}
 predicate={'eq':['report',True]}
 if extra:
  quote='Attribution must be verified separately';start=document.index(quote);bindings['attribution']={'start':start,'end':start+len(quote),'quote':quote}
  inputs['attribution']={'kind':'unbound','label':'Program attribution','value_type':'boolean','reason':'No verified program attribution input is bound.'};predicate={'all':[predicate,{'eq':['attribution',True]}]}
 return {'document':document,'spec':{'id':'synthetic_affordability_review','version':version,'source':'synthetic://agere/documentary-review','effective_start':'2028-01-01','effective_end':'2030-12-31','purpose':'Review received affordability reports','predicate':predicate},'bindings':bindings,'input_bindings':inputs,'provenance':{'document_id':'synthetic-review','document_version':version,'publisher':'Agere synthetic engineering example','source_uri':'synthetic://agere/documentary-review','received_on':'2028-01-01'},'role':'denominator' if known else 'review'}


class ProgramDocuments(unittest.TestCase):
 def setUp(self):
  self.f=NativeFixture();self.f.setUp();self.addCleanup(self.f.doCleanups);self.root=self.f.root
  self.pkg=package();self.key=D.install(self.root,self.pkg)['id'];self.p={'package':self.key,'filter':'supported'}

 def test_reuse_pinned_inputs_and_unknowns_with_exact_quotes(self):
  yes,unknown,out=D.execute(self.root,['P-1','P-2'],self.p)
  self.assertEqual(yes,{'P-1'});self.assertEqual(unknown,{'P-2'})
  row=D.inspect(self.root,next(r['id'] for r in out['patients'] if r['patient_id']=='P-1'));self.assertTrue(row['inputs']['report']['value'])
  self.assertEqual(row['inputs']['report']['quote'],self.pkg['bindings']['report']);self.assertFalse(row['clinical_eligibility_established']);self.assertEqual(row['review']['status'],'unreviewed')
  self.assertEqual(D.execute(self.root,['P-1','P-2'],self.p)[2]['processing_counts']['patient_cache_hits'],2)

 def test_document_mapping_and_span_immutable_even_when_predicate_unchanged(self):
  self.assertEqual(D.install(self.root,self.pkg)['id'],self.key)
  for change in ('document','input_bindings','provenance'):
   p=copy.deepcopy(self.pkg)
   if change=='document':p['document']+=' A new appendix.'
   elif change=='input_bindings':p['input_bindings']['report']['expected']='known'
   else:p['provenance']['publisher']='Different publisher'
   with self.assertRaisesRegex(ValueError,'immutable'):D.install(self.root,p)
  db=D.storage(self.root)
  try:
   with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM packages')
  finally:db.close()

 def test_missing_or_wrong_quotes_pins_types_and_executable_predicates_rejected(self):
  for part in ('quote','pin','type','sql','unbound'):
   p=copy.deepcopy(self.pkg)
   if part=='quote':p['bindings']['report']['quote']='wrong'
   elif part=='pin':p['input_bindings']['report']['version']='wrong'
   elif part=='type':p['spec']['predicate']={'eq':['report',1]}
   elif part=='sql':p['spec']['predicate']={'eval':['report','malicious']}
   else:p['input_bindings']={}
   with self.assertRaises(ValueError):D.install(self.root,p)

 def test_unbound_attribution_is_unknown_and_not_filled_from_coverage(self):
  key=D.install(self.root,package('2',extra=True))['id'];yes,unknown,out=D.execute(self.root,['P-1','P-2'],{'package':key,'filter':'supported'})
  self.assertFalse(yes);self.assertEqual(unknown,{'P-1','P-2'});row=D.inspect(self.root,out['patients'][0]['id'])
  self.assertEqual(row['inputs']['attribution']['status'],'unbound');self.assertIsNone(row['inputs']['attribution']['value'])
  self.f.save([self.f.screen(difficulty_paying_medical_bills=False)])
  _,_,out=D.execute(self.root,['P-1'],{'package':key,'filter':'all'});self.assertEqual(out['status_counts'],{'contradicted':1})
  self.assertIn('attribution',out['patients'][0]['evaluation']['missing'])

 def test_source_change_only_expires_affected_patient(self):
  _,_,out=D.execute(self.root,['P-1','P-2'],self.p);keys={r['patient_id']:r['id'] for r in out['patients']}
  self.f.save([self.f.screen(difficulty_paying_medical_bills=False)])
  with self.assertRaises(ValueError):D.inspect(self.root,keys['P-1'])
  self.assertEqual(D.inspect(self.root,keys['P-2'])['status'],'unresolved')
  out=D.execute(self.root,['P-1','P-2'],self.p)[2];self.assertEqual(out['processing_counts']['patient_cache_hits'],1)

 def test_raw_tampering_does_not_reuse_stale_native_analysis(self):
  D.execute(self.root,['P-1'],self.p);self.f.f.db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(self.f.screen(difficulty_paying_medical_bills=False)),'screen'));self.f.f.db.commit()
  with self.assertRaisesRegex(ValueError,'Native requirement inputs changed'):D.execute(self.root,['P-1'],self.p)

 def test_effective_period_and_document_receipt_clock(self):
  p=package('expired');p['spec']['effective_end']='2028-01-02';key=D.install(self.root,p)['id']
  self.assertEqual(D.execute(self.root,['P-1'],{'package':key,'filter':'all'})[2]['status_counts'],{'not_effective':1})
  p=package('future');p['provenance']['received_on']='2030-01-01';key=D.install(self.root,p)['id']
  self.assertEqual(D.execute(self.root,['P-1'],{'package':key,'filter':'all'})[2]['patients'][0]['evaluation']['status'],'document_not_received_by_cutoff')

 def test_protocol_input_reuses_existing_clinical_rule(self):
  p=package('protocol');spec=C.definition('affordability_action_review@2')
  p['input_bindings']['report']={'kind':'protocol','label':'Affordability review criterion','protocol':'affordability_action_review@2','protocol_hash':spec['hash'],'field':'trigger'};key=D.install(self.root,p)['id']
  yes,unknown,result=D.execute(self.root,['P-1','P-2'],{'package':key,'filter':'supported'});self.assertEqual(yes,{'P-1'});self.assertEqual(unknown,{'P-2'})
  row=D.inspect(self.root,result['patients'][0]['id']);self.assertEqual(row['inputs']['report']['evidence']['protocol']['version'],'2')

 def test_semantic_review_cas_blocks_prior_work_and_preserves_history(self):
  D.execute(self.root,['P-1'],self.p);prep=D.prepare(self.root,self.p);node={'id':'r','parameters':self.p}
  docs=D.contexts(self.root,['P-1'],[node],{'r':prep},evaluated_patients={'r':{'P-1'}})
  ctx=S.contexts(self.root,['P-1'],[],{},evaluated_patients={},program_documents=docs)
  proposal=W.prepare(self.root,'protocol:affordability_action_review@2',['P-1'],decision_contexts=ctx);W.materialize(self.root,proposal['proposal_id'],'ISOLATED TEST FIXTURE')
  event={'package':self.key,'expected_revision':0,'actor':'ISOLATED TEST FIXTURE','status':'changes_requested','note':'This is a synthetic test correction.'};D.review(self.root,event)
  with self.assertRaises(ValueError):D.review(self.root,event)
  with self.assertRaises(ValueError):S.verify(self.root,list(ctx.values()))
  with self.assertRaisesRegex(ValueError,'semantic review changed'):W.materialize(self.root,proposal['proposal_id'],'ISOLATED TEST FIXTURE')
  self.assertEqual(D.execute(self.root,['P-1'],self.p)[2]['patients'][0]['evaluation']['status'],'semantic_changes_requested')
  self.assertEqual(len(D.inspect_package(self.root,self.key)['review_history']),1)

 def test_legacy_quoted_intake_can_be_bound_but_unsourced_default_cannot(self):
  original={k:self.pkg[k] for k in ('document','spec','bindings')};legacy.install_candidate(self.root,original)
  bound=D.bind_existing(self.root,legacy.Program(self.pkg['spec']).hash,self.pkg['input_bindings'],self.pkg['provenance'],self.pkg['role']);self.assertEqual(bound['id'],self.key)
  with self.assertRaises(ValueError):D.bind_existing(self.root,'a'*64,{},self.pkg['provenance'],'review')

 def test_exact_question_retains_work_selection_and_rejects_extra_qualifier(self):
  q=D.question(self.key,'protocol:affordability_action_review@2');graph,route=local_plan(q,ASOF)
  self.assertEqual(route,'exact_imported_program_command');self.assertEqual(graph['nodes'][1]['operation'],'program_document');self.assertEqual(graph['nodes'][-1]['operation'],'worklist')
  self.assertIsNone(D.local_question(q+' Only adults.'))
  with self.assertRaises(ValueError):D.execute(self.root,['invented'],self.p)
  self.assertEqual(D.execute(self.root,[],self.p)[2]['patients_evaluated'],0)


if __name__=='__main__':unittest.main()
