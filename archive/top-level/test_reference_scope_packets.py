import copy,json,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_reference_scope_work as fixture
import test_internal_execution as auth_fixture
from clinical_core.agere.clinical import reference_scope_packets as p,reference_scope_work as scope
class Packets(unittest.TestCase):
 record=fixture.Scope.record;context=fixture.Scope.context;match=fixture.Scope.match;queue=fixture.Scope.queue;event=fixture.Scope.event
 credential=auth_fixture.Execution.credential;bearer=auth_fixture.Execution.bearer;review_packet=auth_fixture.Execution.review_packet
 def setUp(self):
  fixture.Scope.setUp(self);self.selection=scope.select(self.path,self.selection_request);self.answerreq=fixture.Scope.prepare_answer(self,self.selection)
  self.worker=self.credential(['packet_worker']);self.reviewer=self.credential(['packet_reviewer'])
  self.run=p.execution.run(self.path,{'task_id':self.task['id'],'expected_revision':self.task['revision'],'request_id':'run'},self.bearer())['run'];self.review_packet(self.run)
  self.req={'run_id':self.run['id'],'review_revision':1,'selection_id':self.selection['id'],'note':'Disposable engineering fixture only','request_id':'expanded-scope'}
 def prepare(self,**changes):return p.prepare(self.path,{**self.req,**changes},self.bearer(self.reviewer))
 def view(self):return p.inspect(self.path,self.task['id'])
 def test_exact_packet_question_and_selection(self):
  row=self.prepare();self.assertEqual(row['selection_id'],self.selection['id']);self.assertEqual(row['target']['target'],self.target)
  self.assertTrue(self.view()['followups'][0]['current']);self.assertFalse(row['requirement_resolved']);self.assertFalse(row['human_review_recorded']);self.assertFalse(row['care_delivered'])
 def test_credential_role_scope_revocation_and_no_secret_storage(self):
  for bearer in (None,self.bearer()):
   with self.assertRaises(PermissionError):p.prepare(self.path,self.req,bearer)
  wrong=self.credential(['packet_reviewer'],service_id='pharmacy',buyer_id='buyer1')
  with patch.object(p.execution,'authorize',side_effect=PermissionError('Wrong buyer')):
   with self.assertRaises(PermissionError):p.prepare(self.path,self.req,self.bearer(wrong))
  row=self.prepare();p.execution.revoke(self.path,self.reviewer['credential']['id'],'Fixture revocation')
  with self.assertRaises(PermissionError):self.prepare()
  self.assertNotIn(self.reviewer['token'],json.dumps(row));self.assertNotIn(self.reviewer['token'].encode(),(self.path/'internal-execution.sqlite3').read_bytes())
 def test_idempotent_concurrent_prepare_and_collision(self):
  with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:self.prepare(),range(2)))
  self.assertEqual(rows[0]['id'],rows[1]['id']);self.assertEqual(len(self.view()['followups']),1)
  with self.assertRaises(p.execution.care.work.WorkConflict):self.prepare(note='Changed')
 def test_wrong_review_and_foreign_question(self):
  with self.assertRaises(p.execution.care.work.WorkConflict):self.prepare(review_revision=2)
  with patch.object(p.packet_followup,'targets',return_value=[]):
   with self.assertRaisesRegex(ValueError,'this task'):self.prepare()
  self.assertEqual(self.view()['followups'],[])
 def test_changed_sources_and_context_rejected(self):
  self.contexts[self.selection['context_id']]['changed']=True
  with self.assertRaises(ValueError):self.prepare()
  self.assertEqual(self.view()['followups'],[])
 def test_new_review_preserves_stale_followup_and_blocks_answer(self):
  row=self.prepare();self.review_packet(self.run,expected_revision=1,request_id='review-2')
  self.assertFalse(self.view()['followups'][0]['current'])
  with self.assertRaises(p.execution.care.work.WorkConflict):p.attach(self.path,{'followup_id':row['id'],'answer_id':self.output['id']})
 def test_exact_answer_returns_to_packet_and_original_question(self):
  row=self.prepare();req={'followup_id':row['id'],'answer_id':self.output['id']};result=p.attach(self.path,req)
  self.assertEqual(result,p.attach(self.path,req));self.assertTrue(self.view()['answers'][0]['current'])
  answers=scope.listing(self.path,{'target':self.target})['answers'];self.assertEqual(len(answers),1);self.assertEqual(result['question_answer_id'],answers[0]['id'])
  self.assertFalse(result['requirement_resolved']);self.assertEqual(p.execution.inspect(self.path,self.task['id'])['runs'][0]['follow_up_state'],'due')
 def test_wrong_answer_patient_scope_and_extra_authority_rejected(self):
  row=self.prepare();req={'followup_id':row['id'],'answer_id':self.output['id']}
  for key in ('context_id','patient_id'):
   old=self.output[key];self.output[key]='foreign'
   with self.assertRaises(ValueError):p.attach(self.path,req)
   self.output[key]=old
  with self.assertRaises(ValueError):p.attach(self.path,{**req,'human_review_recorded':True})
  self.assertFalse(self.view()['answers'])
 def test_changed_answer_preserves_history(self):
  row=self.prepare();p.attach(self.path,{'followup_id':row['id'],'answer_id':self.output['id']});self.output['findings'].append({'citations':[]})
  self.assertFalse(self.view()['answers'][0]['current'])
 def test_binding_race_cannot_commit_followup(self):
  original=p.dependencies;calls=0
  def changing(*args):
   nonlocal calls
   result=original(*args);calls+=1
   return (*result[:2],{**result[2],'selection_hash':'changed'}) if calls>1 else result
  with patch.object(p,'dependencies',side_effect=changing):
   with self.assertRaises(p.execution.care.work.WorkConflict):self.prepare()
  self.assertFalse(self.view()['followups'])
 def test_question_attachment_can_recover_after_packet_commit_failure(self):
  row=self.prepare();req={'followup_id':row['id'],'answer_id':self.output['id']};original=p.answer_binding;calls=0
  def changing(*args):
   nonlocal calls
   result=original(*args);calls+=1
   return (*result[:2],{**result[2],'followup_hash':'changed'}) if calls>1 else result
  with patch.object(p,'answer_binding',side_effect=changing):
   with self.assertRaises(ValueError):p.attach(self.path,req)
  self.assertFalse(self.view()['answers']);self.assertEqual(len(scope.listing(self.path,{'target':self.target})['answers']),1)
  p.attach(self.path,req);self.assertEqual(len(self.view()['answers']),1)
 def test_immutable_packet_history(self):
  row=self.prepare();p.attach(self.path,{'followup_id':row['id'],'answer_id':self.output['id']});db=p.database(self.path)
  try:
   for table in ('reference_followups','reference_followup_answers'):
    with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM '+table)
  finally:db.close()
if __name__=='__main__':unittest.main()
