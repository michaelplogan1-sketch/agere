"""Presentation approval is distinct from executing proposed services or clinical qualification."""
import importlib,json,unittest
from types import SimpleNamespace
from clinical_core.agere.deliverables.receipts import Bundle
C=importlib.import_module('clinical_core.agere.deliverables.compose')
class Scope(unittest.TestCase):
 def test_writer_and_reviewer_share_snapshot_clock_without_inventing_current_care(self):
  b=Bundle({'as_of':'2032-07-01','deliverable_purpose':'proposal'});seen=[]
  def send(body):seen.append(body);return {'content':[{'type':'text','text':'{"coverage":"complete","support":"supported","issues":[]}'}]},{'cache_hit':True}
  C._review_artifact('<p>Synthetic snapshot</p>',b,'Review',None,send,C.Ledger(),{'passed':True})
  writer='\n'.join(x['text'] for x in C._system_blocks(b));reviewer=seen[0]['system']
  for text in (writer,reviewer):
   self.assertIn('as_of="2032-07-01"',text)
   self.assertIn('not the page generation date',text)
   self.assertIn('do not prove a source contradiction',text)
   self.assertIn('Do not silently change the cutoff',text)
   self.assertIn('or infer current tolerance from historical findings',text)
  self.assertEqual(C._clock_rules(Bundle()),'')

 def test_writer_system_explicitly_allows_proposed_process_and_current_status(self):
  b=Bundle({'deliverable_purpose':'proposal','frozen_evidence':True});seen=[]
  def send(body):seen.append(body);return {'content':[{'type':'text','text':'TITLE: Plan\n```html\n<p>Proposed intake process</p>\n```'}]},{'cost_estimate':{'estimated_usd':.01}}
  C._compose_frontier(SimpleNamespace(frozen_evidence=True),b,'Design a service',None,send,C.Ledger())
  system='\n'.join(x['text'] for x in seen[0]['system'])
  self.assertIn('permits this requested proposed operating design',system)
  self.assertIn('Complete the proposed process rather than deferring',system)
  self.assertIn('partial/unverified clinical conclusions remain partial/unverified',system)
  self.assertIn('current_saved_status',system)
  self.assertNotIn('PROPOSED DESIGN CONTRACT','\n'.join(x['text'] for x in C._system_blocks(Bundle())))

 def test_proposal_review_keeps_unknowns_without_demanding_real_actions(self):
  b=Bundle({'deliverable_purpose':'proposal'});b.add('saved_answer',{}, {'answer':{'status':'partial','missing':['Clinical dose not established']}});seen=[]
  def send(body):seen.append(body);return {'content':[{'type':'text','text':json.dumps({'coverage':'complete','support':'supported','issues':[]})}]},{'cost_estimate':{'estimated_usd':.01}}
  report=C._review_artifact('<p>Proposed design</p>',b,'Design a service',None,send,C.Ledger(),{'passed':True})
  self.assertTrue(report['passed']);prompt=seen[0]['messages'][0]['content'];self.assertIn('Do not demand actual enrollment',prompt);self.assertIn('hidden contradictions still fail',prompt);self.assertIn('must remain explicitly partial/unverified',prompt)
 def test_legacy_final_does_not_inherit_proposal_semantics(self):
  b=Bundle();seen=[]
  def send(body):seen.append(body);return {'content':[{'type':'text','text':'{"coverage":"unknown","support":"unknown","issues":[]}'}]},{'cost_estimate':{'estimated_usd':.01}}
  r=C._review_artifact('<p>Report</p>',b,'Report',None,send,C.Ledger(),{'passed':True})
  self.assertFalse(r['passed']);self.assertNotIn('Do not demand actual enrollment',seen[0]['messages'][0]['content'])
if __name__=='__main__':unittest.main()
