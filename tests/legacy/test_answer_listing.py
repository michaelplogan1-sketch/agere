"""Listing metadata must not decode full native investigation histories."""
import json,tempfile,unittest,os,sqlite3
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.deliverables import answer_context as A

class AnswerListing(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
 def save(self,i,question='Question',padding=''):
  key='ANS-'+format(i,'024x');d=A.root(self.path)/key;d.mkdir(parents=True,exist_ok=True)
  data={'schema':'agere-answer-context/1','context_policy_identity':'fixture','question':question,
        'investigation':padding,'patient_ids':['A','B'],'input_patient_ids':['A','B','C'],
        'answer':{'answer_type':'patient_set'},'id':key,'created_at':'2026-11-06T12:00:00+00:00'}
  target=d/'answer.json';target.write_text(json.dumps(data));return target,data
 def test_new_index_keeps_exact_scope_without_reading_investigation(self):
  target,data=self.save(1,padding='large native evidence '*6000);A.publish_listing(target,data)
  original=Path.read_text
  def read(path,*args,**kw):
   if path.name=='answer.json':raise AssertionError('Listing must not decode the native answer')
   return original(path,*args,**kw)
  with patch.object(Path,'read_text',read):row=A.listing(self.path)[0]
  self.assertEqual(row,A.listing_row(data));self.assertEqual(row['patients'],2)
 def test_atomic_capture_publishes_a_current_index_after_unlinking_its_partial(self):
  with sqlite3.connect(self.path/'semantic.sqlite3')as db:
   db.executescript("CREATE TABLE meta(key,value);INSERT INTO meta VALUES('version','fixture'),('source_digest','catalog-fixture'),('as_of','2030-05-27');")
  result={'answer':{'status':'partial','answer_type':'unsupported','headline':'Authored catalog fixture','patient_ids':[]},
          'investigation':{'retained_unverified_text':'x'*100000}}
  context=A.capture(self.path,'Authored catalog fixture',result);target=A.root(self.path)/context['id']/'answer.json'
  index=json.loads(target.with_name('listing.json').read_text());self.assertEqual(index['file'],A.listing_stamp(target))
  original=Path.read_text
  def read(path,*args,**kw):
   if path.name=='answer.json':raise AssertionError('A newly captured large answer needs no full read in its listing')
   return original(path,*args,**kw)
  with patch.object(Path,'read_text',read):row=A.listing(self.path)[0]
  self.assertEqual(row['patients'],0);self.assertEqual(row['question'],'Authored catalog fixture')
 def test_large_legacy_headers_preserve_exact_title_and_date_but_not_invent_scope(self):
  q='Long Unicode question '+('🍃 clinical data '*1000)
  target,data=self.save(2,question=q,padding='large native evidence '*20000);before=target.read_bytes()
  original=Path.read_text
  def read(path,*args,**kw):
   if path.name=='answer.json':raise AssertionError('No full text read for legacy history')
   return original(path,*args,**kw)
  with patch.object(Path,'read_text',read):row=A.listing(self.path)[0]
  self.assertEqual(row['question'],q);self.assertEqual(row['created_at'],data['created_at'])
  self.assertIsNone(row['patients']);self.assertIsNone(row['result_scope_available'])
  self.assertEqual(before,target.read_bytes());self.assertFalse(target.with_name('listing.json').exists())
 def test_changed_context_invalidates_derived_metadata(self):
  target,data=self.save(3);A.publish_listing(target,data)
  data['question']='Changed';target.write_text(json.dumps(data))
  self.assertEqual(A.listing(self.path)[0]['question'],'Changed')
 def test_existing_display_limit_is_selected_before_payload_reads(self):
  for i in range(105):
   target,_=self.save(i+10);os.utime(target,(1000+i,1000+i))
  with patch.object(A,'legacy_listing',wraps=A.legacy_listing)as read:rows=A.listing(self.path)
  self.assertEqual(len(rows),100);self.assertEqual(read.call_count,100)
  self.assertEqual(rows[0]['id'],'ANS-'+format(114,'024x'))
 def test_small_legacy_context_keeps_original_metadata(self):
  _,data=self.save(4);self.assertEqual(A.listing(self.path),[A.listing_row(data)])
 def test_unrecognized_large_header_is_supported_unknown_not_a_payload_scan(self):
  key='ANS-'+'f'*24;d=A.root(self.path)/key;d.mkdir(parents=True)
  (d/'answer.json').write_text(json.dumps({'answer':'evidence '*20000,'question':'At the end','id':key,'created_at':'2026-11-06'}))
  row=A.listing(self.path)[0];self.assertIn('Historical investigation',row['question']);self.assertIsNone(row['patients'])

if __name__=='__main__':unittest.main()
