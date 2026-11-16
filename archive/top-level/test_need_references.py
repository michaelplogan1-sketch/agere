import copy,json,unittest
from unittest.mock import patch
import test_need_candidates as base
from clinical_core.agere.clinical import need_references as refs
from clinical_core.agere.clinical import stable_reconciliation as stable

class ReferenceQuestions(unittest.TestCase):
 setUpBase=base.Candidates.setUpBase
 put=base.Candidates.put
 chain=base.Candidates.chain
 create=base.Candidates.create
 hit=base.Candidates.hit
 def setUp(self):
  base.Candidates.setUp(self)
  self.context=self.create(['a','b','c','d']);self.answer['context_id']=self.context['id']
  units=self.context['model_context']['evidence'];self.assertGreater(len(units),2)
  self.answer['passes']=[{'pass_number':i+1,'result':{'unit_reviews':[{'unit':'u'+str(j+1),'source_unit_id':u['id']} for j,u in enumerate(units[i::2])]}} for i in range(2)]
  self.answer['status']='complete';self.answer['reconciliation']={'groups':[{}]}
  self.answer['reference_manifest']=stable.manifest(self.answer,self.context)
  self.reset('Compare p1:u1 with p2:u1.', ['r1_f1'])
 def reset(self,text,findings=None):
  self.answer['review_steps'][0].update(question=text,finding_ids=['r1_f1'] if findings is None else findings)
  receipt=refs.candidates.needs.prepare(self.path,self.answer['id'])
  self.request={'receipt_id':receipt['id'],'need_id':'need1'}
 def prepare(self):return refs.prepare(self.path,self.request)
 def test_qualified_reference_resolves_exact_units_across_passes(self):
  r=self.prepare();self.assertEqual([x['name'] for x in r['references']],['p1:u1','p2:u1'])
  self.assertNotEqual(*[x['source_unit_id'] for x in r['references']]);self.assertFalse(r['question_resolved']);self.assertFalse(r['question_premise_endorsed'])
  for x in r['references']:self.assertEqual(x['source_unit']['id'],self.answer['reference_manifest'][x['name']]['source_unit_id'])
  self.assertEqual(refs.inspect(self.path,r['id']),r);self.assertEqual(self.prepare(),r)
 def test_qualified_reference_does_not_inherit_surrounding_pass(self):
  self.reset('Inspect p2:u1.', ['p1_f1']);self.assertEqual(self.prepare()['references'][0]['name'],'p2:u1')
 def test_bare_alias_only_resolves_with_single_own_pass_namespace(self):
  self.reset('Inspect u1.', ['p2_f1']);self.assertEqual(self.prepare()['references'][0]['name'],'p2:u1')
  for findings in (['r1_f1'],['p1_f1','p2_f2'],[]):
   self.reset('Inspect u1.',findings);self.assertEqual(self.prepare()['references'],[])
 def test_named_or_qualified_other_pass_blocks_bare_alias(self):
  for text in ('Does pass 2 contain u1?', 'Compare p2:u1 and u1.'):
   self.reset(text,['p1_f1']);r=self.prepare();self.assertTrue(any(x['reason']=='unit_alias_namespace_unresolved' for x in r['unresolved_mentions']))
   self.assertFalse(any(x['name']=='p1:u1' for x in r['references']))
 def test_abbreviated_missing_and_unknown_aliases_never_guess(self):
  self.reset('Compare p1:u1... u1… s1 p9:u99 WHM-R-d028… WHL-R-absent.', ['p1_f1']);r=self.prepare()
  self.assertEqual(r['references'],[]);self.assertEqual(len(r['unresolved_mentions']),6)
 def test_repeat_mentions_are_one_exact_reference(self):
  self.reset('Compare p1:u1 with p1:u1.');r=self.prepare();self.assertEqual(len(r['references']),1);self.assertEqual(len(r['references'][0]['mentions']),2)
 def test_malformed_suffix_cannot_backtrack_to_valid_prefix(self):
  self.reset('p1:u1...continuation p1:u1x p1:u1:u2 xp1:u1')
  self.assertEqual(self.prepare()['references'],[])
 def test_literal_record_outside_original_context_can_be_located(self):
  self.reset('Inspect extra.');r=self.prepare();self.assertEqual(r['references'][0]['name'],'record:extra');self.assertNotIn('extra',self.context['record_scope']['record_ids'])
 def test_changed_manifest_is_rejected(self):
  self.answer['reference_manifest']['p1:u1']['record_id']='extra';self.reset('Inspect p1:u1.')
  with self.assertRaisesRegex(ValueError,'manifest'):self.prepare()
 def test_explicit_subset_keeps_unresolved_mentions_and_original_question(self):
  self.reset('Compare p1:u1 and p2:u1 with s1.');r=self.prepare();s=refs.select(self.path,{'reference_id':r['id'],'reference_names':['p1:u1']})
  self.assertEqual(s['unselected_reference_names'],['p2:u1']);self.assertEqual(len(s['unresolved_mentions']),1)
  self.assertFalse(s['meaning_resolved']);self.assertEqual(s['original_question'],self.answer['review_steps'][0]['question'])
  self.assertEqual(refs.record_context.inspect(self.path,s['context_id'])['parameters']['question'],s['original_question']);self.assertEqual(refs.inspect_selection(self.path,s['id']),s)
  self.assertEqual(refs.candidates.needs.inspect(self.path,self.request['receipt_id'])['needs'][0]['question'],s['original_question'])
 def test_unselected_unknown_and_duplicate_reference_names_rejected(self):
  r=self.prepare()
  for names in ([],False,['p1:u1','p1:u1'],['p9:u1'],['record:extra'],[{}]):
   with self.assertRaises(ValueError):refs.select(self.path,{'reference_id':r['id'],'reference_names':names})
 def test_tampering_and_changed_original_question_rejected(self):
  r=self.prepare();p=refs.candidates.location(self.path,r['id'],'need-references');raw=json.loads(p.read_text());raw['original_question']='changed';p.write_text(json.dumps(raw))
  with self.assertRaisesRegex(ValueError,'identity'):refs.inspect(self.path,r['id'])
  p.write_text(json.dumps(r));self.answer['review_steps'][0]['question']='Changed'
  with self.assertRaises(ValueError):refs.inspect(self.path,r['id'])
 def test_changed_source_invalidates_lookup_and_selection(self):
  from test_warehouse_interpretation import record
  r=self.prepare();s=refs.select(self.path,{'reference_id':r['id'],'reference_names':['p1:u1']});self.put(record('other','financial_screens',difficulty_paying_medical_bills=True))
  for fn in (lambda:refs.inspect(self.path,r['id']),lambda:refs.inspect_selection(self.path,s['id'])):
   with self.assertRaises(ValueError):fn()
 def test_change_during_selection_cannot_save_receipt(self):
  r=self.prepare();original=refs.record_context.create
  def changed(*args):
   row=original(*args);self.answer['review_steps'][0]['question']='Changed';return row
  with patch.object(refs.record_context,'create',side_effect=changed):
   with self.assertRaises(ValueError):refs.select(self.path,{'reference_id':r['id'],'reference_names':['p1:u1']})
  self.assertFalse((self.path/'reference-contexts').exists())
 def test_exact_followup_attachment_idempotence_and_stale_listing(self):
  r=self.prepare();s=refs.select(self.path,{'reference_id':r['id'],'reference_names':['p1:u1']})
  answer={'id':'b'*64,'context_id':s['context_id'],'patient_id':'P-1','status':'complete','findings':[],'review_steps':[]}
  req={**self.request,'selection_kind':'exact_references','selection_id':s['id'],'answer_id':answer['id']}
  with patch.object(refs.source_synthesis,'inspect',side_effect=lambda path,key:copy.deepcopy(answer if key==answer['id'] else self.answer)):
   a=refs.attach(self.path,req);self.assertEqual(a,refs.attach(self.path,req));self.assertFalse(a['human_review_recorded']);self.assertFalse(a['requirement_resolved'])
   self.assertTrue(refs.answers(self.path,self.request)['answers'][0]['current'])
   answer['patient_id']='P-2';self.assertFalse(refs.answers(self.path,self.request)['answers'][0]['current'])
   with self.assertRaisesRegex(ValueError,'patient'):refs.attach(self.path,req)
 def test_answer_cannot_be_attached_to_another_question_or_context(self):
  r=self.prepare();s=refs.select(self.path,{'reference_id':r['id'],'reference_names':['p1:u1']})
  req={**self.request,'selection_kind':'exact_references','selection_id':s['id'],'answer_id':self.answer['id']}
  with self.assertRaisesRegex(ValueError,'exact context'):refs.attach(self.path,req)
  with self.assertRaisesRegex(ValueError,'different question'):refs.attach(self.path,{**req,'need_id':'other'})
 def test_foreign_record_cannot_enter_reference_projection(self):
  records=refs.record_context.received(self.path,'P-1')[0];records[0]['patient_id']='P-2'
  with self.assertRaisesRegex(ValueError,'same-patient'):refs.resolve(self.answer['review_steps'][0],self.answer,self.context,records)

if __name__=='__main__':unittest.main()
