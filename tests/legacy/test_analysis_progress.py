"""Recorded progress is per attempt, incremental, read-only and content-free."""
import json,sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere import request_lineage as L

class AnalysisProgress(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name);self.key='a'*32
 def test_resume_uses_its_own_observed_activity_without_claiming_acceptance(self):
  with L.begin(self.path,'/api/stack/investigation/resume',{'context_id':'ANS-authored'},tracking=self.key)as execution:
   L.observe('component_finished',component='investigator_sql',status='completed',metrics={'rows':124})
   execution.finish({'answer':{'status':'partial'}})
  value=L.progress(self.path,self.key)
  self.assertEqual(value['answer_status'],'partial')
  self.assertIn('Source query finished · 124 result rows',[v['label']for v in value['updates']])
  with self.assertRaisesRegex(ValueError,'not repeated'):L.begin(self.path,'/api/stack/investigation/resume',{},tracking=self.key)
 def test_missing_attempt_is_unknown_and_creates_no_database(self):
  r=L.progress(self.path,self.key);self.assertEqual(r['status'],'waiting');self.assertEqual(list(self.path.iterdir()),[])
 def test_observed_steps_are_incremental_and_terminal_does_not_mean_answered(self):
  with L.begin(self.path,'/api/stack/compose',{'question':'fixture'},tracking=self.key)as execution:
   with L.component('governed_sql'):
    first=L.progress(self.path,self.key)
    self.assertEqual(first['status'],'active_or_interrupted')
    self.assertEqual(first['updates'][-1]['state'],'running')
   second=L.progress(self.path,self.key,after=first['sequence'])
   self.assertEqual([e['kind']for e in second['updates']],['component_finished'])
   self.assertEqual(second['updates'][0]['state'],'completed')
   execution.finish({'answer':{'status':'partial'}})
  final=L.progress(self.path,self.key,after=second['sequence'])
  self.assertEqual(final['status'],'complete');self.assertEqual(final['answer_status'],'partial')
  self.assertEqual(L.progress(self.path,self.key,after=final['sequence'])['updates'],[])
 def test_wrong_attempt_and_duplicate_tracking_cannot_mix_or_repeat_execution(self):
  with L.begin(self.path,'/api/stack/compose',{},tracking=self.key)as e:e.finish({})
  before=L.progress(self.path,self.key)
  self.assertEqual(L.progress(self.path,'b'*32)['updates'],[])
  with self.assertRaisesRegex(ValueError,'not repeated'):L.begin(self.path,'/api/stack/compose',{},tracking=self.key)
  self.assertEqual(L.progress(self.path,self.key),before)
 def test_projection_never_exposes_record_text_queries_prompts_or_paths(self):
  secret='PRIVATE_SENTINEL'
  with L.begin(self.path,'/api/stack/compose',{'question':secret},tracking=self.key)as e:
   L.observe('component_started',component='investigator_sql',invocation_id='read',sql=secret,notes=secret)
   L.observe('reserved',request_hash='f'*64,provider=secret,model=secret,ledger_path=secret)
   L.observe('component_finished',component='investigator_sql',invocation_id='read',status='completed',metrics={'record':secret})
   L.observe('request_finished',request_hash='f'*64,status='failed',error=secret)
   e.finish({'answer':{'status':'clarify','headline':secret}})
  text=json.dumps(L.progress(self.path,self.key));self.assertNotIn(secret,text);self.assertNotIn('f'*64,text)
  self.assertTrue(any(e['state']=='failed'for e in L.progress(self.path,self.key)['updates']))
 def test_invalid_keys_and_cursors_are_rejected_before_sql(self):
  for key in ['', 'x'*32,"' OR 1=1 --",None]:
   with self.subTest(key=key),self.assertRaises(ValueError):L.progress(self.path,key)
  for cursor in [-2,True,2**63,'1']:
   with self.subTest(cursor=cursor),self.assertRaises(ValueError):L.progress(self.path,self.key,after=cursor)
 def test_reading_progress_does_not_change_the_journal(self):
  with L.begin(self.path,'/api/stack/compose',{},tracking=self.key)as e:e.finish({})
  target=self.path/'program-work.sqlite3';before=target.read_bytes()
  L.progress(self.path,self.key);self.assertEqual(target.read_bytes(),before)
 def test_row_count_describes_only_returned_rows_and_keeps_truncation_visible(self):
  with L.begin(self.path,'/api/stack/compose',{},tracking=self.key)as e:
   L.observe('component_finished',component='investigator_sql',status='completed',metrics={'rows':2,'truncated':True})
   e.finish({'answer':{'status':'partial'}})
  labels=[u['label']for u in L.progress(self.path,self.key)['updates']]
  self.assertIn('Source query finished · 2 result rows (partial)',labels)
  self.assertNotIn('patients',json.dumps(labels))
 def test_unfinished_or_unknown_component_is_not_presented_as_success(self):
  with L.begin(self.path,'/api/stack/compose',{},tracking=self.key):
   L.observe('component_started',component='not_a_known_UI_stage',label='all clinical evidence verified')
  r=L.progress(self.path,self.key);self.assertEqual(r['status'],'active_or_interrupted')
  self.assertEqual(len(r['updates']),1);self.assertFalse(any(e['group']=='verify'for e in r['updates']))

if __name__=='__main__':unittest.main()
