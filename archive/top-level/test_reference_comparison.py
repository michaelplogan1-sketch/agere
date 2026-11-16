import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import reference_comparison as c
import test_need_references as fixture
from test_warehouse_interpretation import record

class LiteralValues(unittest.TestCase):
 def compare(self,a,b):return c.compare_values({'value':a},{'value':b})
 def test_exact_text_and_no_normalization(self):
  for a,b in [('same','same'),('A','a'),('x ','x'),('é','e\u0301'),('a\r\nb','a\nb')]:
   r=self.compare(a,b);self.assertEqual(r['literal_equal'],a==b);self.assertFalse(r['normalization_applied'])
 def test_type_preserving_numbers_booleans_and_nulls(self):
  for a,b in [(False,0),(True,1),(1,1.0),('1',1),(None,False)]:self.assertFalse(self.compare(a,b)['literal_equal'])
  r=self.compare(None,None);self.assertTrue(r['literal_equal']);self.assertEqual(r['status'],'null_value_present');self.assertFalse(r['observed_values_on_both_sides'])
 def test_unicode_difference_ranges_reconstruct_original_values(self):
  for a,b in [('α🌻end','αXend'),('','a'),('ab','abcd'),('xab','ab'),('prefixXYZsuffix','prefixABsuffix')]:
   t=self.compare(a,b)['text'];p=t['common_prefix_characters'];s=t['common_suffix_characters'];la,lb=t['difference_ranges']['left'],t['difference_ranges']['right']
   self.assertEqual(a[:p]+a[la[0]:la[1]]+(a[len(a)-s:] if s else ''),a)
   self.assertEqual(b[:p]+b[lb[0]:lb[1]]+(b[len(b)-s:] if s else ''),b)
   self.assertEqual(a[:p],b[:p]);self.assertEqual(a[len(a)-s:] if s else '',b[len(b)-s:] if s else '')
 def test_long_strings_are_compared_completely_with_bounded_display_windows(self):
  a='x'*5000+'a'+'y'*5000;b='x'*5000+'b'+'y'*5000;t=self.compare(a,b)['text']
  self.assertEqual(t['difference_ranges'],{'left':[5000,5001],'right':[5000,5001]});self.assertTrue(all(len(w['text'])<=220 for w in t['first_difference_windows']))
 def test_scalar_bounds_and_nonfinite_values(self):
  for v in ({},[],float('inf'),float('nan'),'x'*(c.MAX_VALUE_BYTES+1)):
   with self.assertRaises(ValueError):c.scalar(v)
  self.assertEqual(c.scalar(False)['type'],'boolean')
 def test_unit_span_is_distinct_from_entire_received_field(self):
  r=record('x','medication_information_requests',text='before TARGET after');u={'kind':'narrative','field':'text','start':7,'end':13,'text':'TARGET'}
  refs={'p1:u1':{'record_id':'x','record_hash':digest(r),'source_unit':u,'unit_hash':digest(u)}}
  narrow=c.operand(refs,{'x':r},{'reference_name':'p1:u1','field':'text','scope':'source_unit'});whole=c.operand(refs,{'x':r},{'reference_name':'p1:u1','field':'text','scope':'received_field'})
  self.assertEqual(narrow['value'],'TARGET');self.assertFalse(narrow['span']['complete_received_field']);self.assertTrue(whole['span']['complete_received_field']);self.assertNotEqual(narrow['value'],whole['value'])
  u['text']='not the source'
  with self.assertRaisesRegex(ValueError,'span'):c.operand(refs,{'x':r},{'reference_name':'p1:u1','field':'text','scope':'source_unit'})
 def test_missing_forbidden_and_unmapped_fields_are_not_null(self):
  r=record('x','medication_information_requests',truth='SECRET',target='SECRET',text='safe',reported=None)
  names={'record:x':{'record_id':'x','record_hash':digest(r)}}
  for field in ['missing','truth','target','original_text','_warehouse_source']:
   with self.assertRaises(ValueError):c.operand(names,{'x':r},{'reference_name':'record:x','field':field,'scope':'received_field'})
  self.assertIsNone(c.operand(names,{'x':r},{'reference_name':'record:x','field':'reported','scope':'received_field'})['value'])
 def test_nested_received_narrative_path_is_exact(self):
  r=record('x','medication_information_requests',text=[{'text':'Exact nested string'}]);names={'record:x':{'record_id':'x','record_hash':digest(r)}}
  self.assertEqual(c.operand(names,{'x':r},{'reference_name':'record:x','field':'text[0].text','scope':'received_field'})['value'],'Exact nested string')

class ComparisonLifecycle(unittest.TestCase):
 setUpBase=fixture.ReferenceQuestions.setUpBase
 put=fixture.ReferenceQuestions.put
 chain=fixture.ReferenceQuestions.chain
 create=fixture.ReferenceQuestions.create
 hit=fixture.ReferenceQuestions.hit
 reset=fixture.ReferenceQuestions.reset
 prepare=fixture.ReferenceQuestions.prepare
 def setUp(self):
  fixture.ReferenceQuestions.setUp(self)
  self.r=self.prepare();self.preview_request={'reference_id':self.r['id'],'reference_names':['p1:u1','p2:u1']}
  self.compare_request={'reference_id':self.r['id'],'left':{'reference_name':'p1:u1','scope':'received_field','field':'event_date'},'right':{'reference_name':'p2:u1','scope':'received_field','field':'event_date'}}
 def test_preview_and_saved_local_result_link_original_question(self):
  p=c.preview(self.path,self.preview_request);self.assertEqual(len(p['sources']),2)
  r=c.prepare(self.path,self.compare_request);self.assertTrue(r['comparison']['literal_equal']);self.assertEqual(r['original_question'],self.r['original_question']);self.assertFalse(r['question_resolved']);self.assertFalse(r['human_review_recorded'])
  self.assertEqual(c.prepare(self.path,self.compare_request),r);listed=c.listing(self.path,self.request);self.assertEqual(len(listed['comparisons']),1);self.assertTrue(listed['comparisons'][0]['current'])
  self.assertEqual(c.refs.inspect(self.path,self.r['id']),self.r)
 def test_same_source_location_is_explicit(self):
  r=c.prepare(self.path,{**self.compare_request,'right':self.compare_request['left']});self.assertTrue(r['same_source_location'])
 def test_mutating_saved_comparison_is_rejected_by_database(self):
  r=c.prepare(self.path,self.compare_request);db=c.database(self.path)
  try:
   for sql in ('UPDATE comparisons SET payload=payload','DELETE FROM comparisons'):
    with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
  finally:db.close()
  self.assertEqual(c.verify(self.path,r),r)
 def test_source_changes_make_saved_comparison_stale(self):
  c.prepare(self.path,self.compare_request);self.put(record('other','financial_screens',difficulty_paying_medical_bills=True))
  r=c.listing(self.path,self.request);self.assertFalse(r['comparisons'][0]['current']);self.assertTrue(r['comparisons'][0]['stale_reasons'])
  with self.assertRaises(ValueError):c.prepare(self.path,self.compare_request)
 def test_tampered_result_and_implementation_rejected(self):
  r=c.prepare(self.path,self.compare_request);r['comparison']['literal_equal']=False
  with self.assertRaisesRegex(ValueError,'identity'):c.verify(self.path,r)
 def test_changes_during_comparison_do_not_create_receipt(self):
  old=c.compare_values
  def changed(*args):
   result=old(*args);self.answer['review_steps'][0]['question']='changed';return result
  with patch.object(c,'compare_values',side_effect=changed):
   with self.assertRaises(ValueError):c.prepare(self.path,self.compare_request)
  self.assertFalse((self.path/'reference-comparisons.sqlite3').exists())
 def test_invalid_names_fields_and_scopes_cannot_select_unreceived_data(self):
  for change in ({'reference_name':'other'},{'field':'truth'},{'field':'no_field'},{'scope':'whole_chart'}):
   with self.assertRaises(ValueError):c.prepare(self.path,{**self.compare_request,'left':{**self.compare_request['left'],**change}})
  for names in ([],['p1:u1'],['p1:u1','p1:u1'],[False,'p2:u1'],['foreign','p2:u1']):
   with self.assertRaises(ValueError):c.preview(self.path,{**self.preview_request,'reference_names':names})
 def test_oversized_preview_never_silently_compares_truncated_value(self):
  with patch.object(c.dispositions,'field_values',return_value={'text':'x'*(c.MAX_VALUE_BYTES+1)}):
   p=c.preview(self.path,self.preview_request);self.assertFalse(p['sources'][0]['fields'][0]['comparable'])
   with self.assertRaisesRegex(ValueError,'byte bound'):c.prepare(self.path,{**self.compare_request,'left':{**self.compare_request['left'],'field':'text'}})

if __name__=='__main__':unittest.main()
