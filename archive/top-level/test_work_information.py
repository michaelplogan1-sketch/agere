import copy,json,sqlite3,unittest
from unittest.mock import patch
import test_reference_applicability as fixture
from clinical_core.agere.clinical import work_information as W

def scenario(target):
 return {'target':target,'rationale':'Engineering assumption scenario; not patient estimates or an instruction to act.',
  'assumptions':{'prior':{'outside_report_exists':.5,'outside_report_absent':.5},
   'utilities':{'retrieve_existing_report':{'outside_report_exists':1,'outside_report_absent':0},'reassess_missing_evidence':{'outside_report_exists':0,'outside_report_absent':1}},
   'channels':{'hypothetical_record_check':{'available':True,'cost':.1,'basis':'Illustrative sensitivity; 80% correct report status assumed, not measured.',
    'likelihoods':{'outside_report_exists':{'found':.8,'not_found':.2},'outside_report_absent':{'found':.2,'not_found':.8}}},
    'repeat_same_uninformative_view':{'available':True,'cost':.05,'basis':'Illustrative uninformative repeat, not an evaluated clinical service.',
    'likelihoods':{'outside_report_exists':{'found':.5,'not_found':.5},'outside_report_absent':{'found':.5,'not_found':.5}}}},
   'utility_scale':'hypothetical review usefulness units','assumption_source':'Supplied engineering fixture. No probabilities, reliability, utility or cost learned from this patient.'}}

class WorkInformation(unittest.TestCase):
 def setUp(self):
  fixture.ReferenceApplicability.setUp(self)
  self.req=scenario(self.target)
 def save(self):
  p=W.preview(self.path,self.req);return W.prepare(self.path,{'scenario':self.req,'preview_id':p['id']})
 def test_no_information_baseline_and_known_noisy_channel(self):
  p=self.save();c=p['calculation'];self.assertAlmostEqual(c['current_expected_utility'],.5)
  channels={x['channel']:x for x in c['channels']};self.assertAlmostEqual(channels['hypothetical_record_check']['gross_value'],.3);self.assertAlmostEqual(channels['hypothetical_record_check']['net_value'],.2)
  self.assertEqual(channels['repeat_same_uninformative_view']['gross_value'],0);self.assertEqual(channels['repeat_same_uninformative_view']['net_value'],-.05)
  self.assertEqual(c['preferred_channel_under_assumptions'],'hypothetical_record_check');self.assertEqual(len(p['baseline_actions']),2)
 def test_exact_question_sources_and_no_authority_transfer(self):
  before=W.D.inspect(self.path,self.target);p=self.save()
  self.assertEqual(p['question'],before['target']['question']);self.assertEqual(p['patient_id'],'p1')
  self.assertEqual(W.D.inspect(self.path,self.target),before)
  self.assertFalse(p['question_resolved']);self.assertFalse(p['automatic_acquisition']);self.assertFalse(p['probabilities_learned']);self.assertFalse(p['clinical_recommendation'])
  self.assertEqual(W.R.acquisitions(self.path,self.target),[])
 def test_replay_and_immutable_storage(self):
  p=self.save();self.assertEqual(self.save(),p);rows=W.listing(self.path,{'target':self.target})['scenarios'];self.assertEqual(len(rows),1);self.assertTrue(rows[0]['current'])
  db=W.database(self.path)
  try:
   for op in ('UPDATE scenarios SET payload=payload','DELETE FROM scenarios'):
    with self.assertRaises(sqlite3.IntegrityError):db.execute(op)
  finally:db.close()
 def test_changed_probability_creates_new_scenario_without_overwrite(self):
  first=self.save();self.req['assumptions']['prior']={'outside_report_exists':.9,'outside_report_absent':.1};second=self.save();self.assertNotEqual(first['id'],second['id'])
  rows=W.listing(self.path,{'target':self.target})['scenarios'];self.assertEqual(len(rows),2);self.assertTrue(all(x['current'] for x in rows))
 def test_source_change_stales_saved_history(self):
  p=self.save();self.records[0]['text']='Changed received source'
  rows=W.listing(self.path,{'target':self.target})['scenarios'];self.assertFalse(rows[0]['current']);self.assertEqual(rows[0]['id'],p['id'])
  with self.assertRaises(ValueError):W.prepare(self.path,{'scenario':self.req,'preview_id':p['id']})
 def test_review_revision_stales_history_without_resolution(self):
  self.save();W.D.save(self.path,{'target':self.target,'expected_revision':0,'request_id':'info-review','actor':'fixture','actor_kind':'synthetic_test','disposition':'unresolved','rationale':'Additional unresolved review observation','bindings':[]})
  self.assertFalse(W.listing(self.path,{'target':self.target})['scenarios'][0]['current']);self.assertFalse(W.D.inspect(self.path,self.target)['documentary_question_addressed'])
 def test_prepared_reference_context_stales_prior_scenario(self):
  self.save();fixture.ReferenceApplicability.acquire(self)
  rows=W.listing(self.path,{'target':self.target})['scenarios'];self.assertFalse(rows[0]['current']);p=self.save();self.assertEqual(len(p['dependencies']['prepared_reference_contexts']),1)
 def test_task_revision_requires_explicit_new_scenario(self):
  self.save();original=W.R.care_readiness.task
  def changed(path,key):return {**original(path,key),'revision':2}
  with patch.object(W.R.care_readiness,'task',side_effect=changed):self.assertFalse(W.listing(self.path,{'target':self.target})['scenarios'][0]['current'])
 def test_missing_stale_reference_preserves_history(self):
  self.save()
  with patch.object(W.D,'resolve_target',side_effect=ValueError('Reference withdrawn')):
   self.assertFalse(W.listing(self.path,{'target':self.target})['scenarios'][0]['current'])
   with self.assertRaises(ValueError):W.preview(self.path,self.req)
 def test_implementation_change_stales_saved_scenario(self):
  self.save()
  with patch.object(W,'identity',return_value='changed'):self.assertFalse(W.listing(self.path,{'target':self.target})['scenarios'][0]['current'])
 def test_tampered_storage_rejected(self):
  p=self.save();db=W.database(self.path)
  try:
   db.execute('DROP TRIGGER immutable_information_UPDATE');p['question']='tampered';db.execute('UPDATE scenarios SET payload=?',(json.dumps(p),));db.commit()
  finally:db.close()
  with self.assertRaises(ValueError):W.listing(self.path,{'target':self.target})
 def test_invalid_inputs_and_authority_rejected(self):
  for value in [float('nan'),float('inf'),True,-.1,1.1]:
   req=copy.deepcopy(self.req);req['assumptions']['prior']['outside_report_exists']=value
   with self.assertRaises(ValueError):W.preview(self.path,req)
  for extra in [{'clinical_recommendation':True},{'assumptions':{**self.req['assumptions'],'calibrated':True}},{'rationale':''}]:
   with self.assertRaises(ValueError):W.preview(self.path,{**self.req,**extra})
  for key,value in [('prior',{}),('channels',{}),('utility_scale',''),('assumption_source','')]:
   req=copy.deepcopy(self.req);req['assumptions'][key]=value
   with self.assertRaises(ValueError):W.preview(self.path,req)
 def test_unavailable_information_not_selected(self):
  self.req['assumptions']['channels']['hypothetical_record_check']['available']=False
  self.assertIsNone(W.preview(self.path,self.req)['calculation']['preferred_channel_under_assumptions'])
 def test_correlated_channels_are_not_added(self):
  a=self.req['assumptions'];a['channels']['identical_record_check']=copy.deepcopy(a['channels']['hypothetical_record_check'])
  p=self.save();self.assertEqual(len(p['calculation']['channels']),3)
  self.assertEqual(sum('gross_value' in x for x in p['calculation']['channels']),3);self.assertNotIn('total_information_value',p['calculation'])
  self.assertIn('must not have their values added',' '.join(p['limitations']))
 def test_perfect_information_and_zero_probability_branches(self):
  a=self.req['assumptions'];a['channels']['hypothetical_record_check']['likelihoods']={'outside_report_exists':{'found':1,'not_found':0,'impossible':0},'outside_report_absent':{'found':0,'not_found':1,'impossible':0}}
  p=self.save();channel=next(x for x in p['calculation']['channels'] if x['channel']=='hypothetical_record_check');self.assertAlmostEqual(channel['gross_value'],.5);self.assertEqual(len(channel['branches']),2)
 def test_generic_service_requirement_also_binds(self):
  self.req['target']=self.row['original_target']['target'];p=self.save();self.assertEqual(p['dependencies']['target']['target']['kind'],'service_requirement')
  self.assertIsNone(p['dependencies']['task_revision']);self.assertTrue(W.listing(self.path,{'target':self.req['target']})['scenarios'][0]['current'])

if __name__=='__main__':unittest.main()
