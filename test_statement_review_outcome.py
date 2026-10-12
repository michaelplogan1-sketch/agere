"""Isolated documentary fixtures; no human review or clinical accuracy claim."""
import copy,json
from unittest.mock import patch
from test_reader_contract_routing import ContractRoute
from clinical_core.agere.clinical import reader_contract_routing as M
from clinical_core.contracts import digest

class ReviewOutcome(ContractRoute):
 def setUp(self):
  super().setUp()
  self.patch(M.R,'identity',return_value='ISOLATED_READER_CONTRACT')
  self.body['role']='kev'
  self.labels={'statement':'patient_affirmed','time':'current_at_source','relation':'clinical_observation'}
  self.meta={'source_digest':'fixture','snapshot':{'as_of':'2026-10-10'}}
  self.patch(M.R,'source',side_effect=lambda *a:(self.source_record,self.source_record['text'],self.meta))
  self.reading={'id':'b'*64,'target':self.target,'source_hash':digest(self.source_record),'source_identity':self.meta,
   'reader_identity':M.R.identity(),'reading':{'labels':self.labels},'context':{'context_complete':True},'provider':'kev'}
  with M.R.db(self.retriever.path) as db:db.execute('INSERT INTO readings VALUES (?,?,?)',(self.reading['id'],'p1',json.dumps(self.reading)))
  self.comparison={'id':'c'*64,'request':{'target':self.target,'cohort_id':self.body['cohort_id'],'roles':['kev']},
    'variants':{'kev':{'status':'completed','reading_id':self.reading['id'],'labels':self.labels,'cache_hit':False}}}
  self.prepared={'comparison':self.comparison,'cache_hit':True}
  self.patch(M.C,'prepare',side_effect=lambda *a:copy.deepcopy(self.prepared))
  self.patch(M.C,'inspect',side_effect=lambda *a:copy.deepcopy(self.comparison))
  self.statement=M.run(self.retriever,self.body)
 def saved_review(self,status='accepted',expected=0):
  return M.R.save_review(self.retriever.path,{'reading_id':self.reading['id'],'expected_revision':expected,
    'request_id':'fixture-'+str(expected),'actor':'ISOLATED ENGINEERING FIXTURE','status':status,
    'labels':self.labels,'rationale':'Simulated documentary review for tests, never clinical adjudication.'})
 def request(self,seconds=None):
  s=M.review_status(self.retriever,{'result_id':self.statement['result_id']})
  return {'result_id':self.statement['result_id'],**{k:s[k] for k in ('reading_id','review_revision','review_digest')},
    'effort':{'seconds':seconds,'declared_by':'ISOLATED TEST' if seconds is not None else None}}
 def events(self,e):
  with M.L.database(self.retriever.path) as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM question_request_events WHERE execution=? ORDER BY sequence',(e.id,))]
 def test_unreviewed_cannot_be_attached_or_claimed_human(self):
  s=M.review_status(self.retriever,{'result_id':self.statement['result_id']})
  self.assertEqual(s['status'],'not_reviewed');self.assertFalse(s['human_review_created'])
  with self.assertRaises(ValueError):M.attach_review(self.retriever,self.request())
 def test_actual_review_store_exact_replay_preserves_task_and_review_count(self):
  before=M.Q.reopen(self.retriever,self.row['id'])['tasks'];review=self.saved_review();req=self.request(45)
  a=M.attach_review(self.retriever,req);b=M.attach_review(self.retriever,req)
  self.assertEqual(a,b);self.assertEqual(M.review_open(self.retriever,{'result_id':a['result_id']}),a)
  self.assertEqual(a['review'],review);self.assertFalse(a['reviewer_identity_verified']);self.assertIsNone(a['effort']['cost_usd'])
  self.assertEqual(M.R.inspect(self.retriever.path,self.reading['id'])['review_revision'],1)
  self.assertEqual(M.Q.reopen(self.retriever,self.row['id'])['tasks'],before)
 def test_unknown_and_explicit_zero_effort_remain_distinct(self):
  self.saved_review()
  unknown=M.attach_review(self.retriever,self.request());zero=M.attach_review(self.retriever,self.request(0))
  self.assertIsNone(unknown['effort']['seconds']);self.assertEqual(zero['effort']['seconds'],0)
  self.assertEqual(unknown['effort']['basis'],'unknown');self.assertFalse(zero['effort']['verified'])
 def test_invalid_effort_or_extra_fields_rejected(self):
  self.saved_review()
  for effort in [{'seconds':v,'declared_by':'fixture'} for v in [-1,True,float('nan'),float('inf'),604801]]+[
    {'seconds':None,'declared_by':'fixture'},{'seconds':4,'declared_by':''},{'seconds':4,'declared_by':'fixture','cost_usd':1}]:
   with self.assertRaises(ValueError):M.attach_review(self.retriever,{**self.request(),'effort':effort})
 def test_wrong_reading_revision_digest_rejected(self):
  self.saved_review()
  for change in [{'reading_id':'d'*64},{'review_revision':0},{'review_revision':True},{'review_digest':'f'*64}]:
   with self.assertRaises(ValueError):M.attach_review(self.retriever,{**self.request(),**change})
 def test_later_review_invalidates_attachment_without_deleting_history(self):
  self.saved_review();req=self.request();row=M.attach_review(self.retriever,req)
  self.saved_review('unresolved',1)
  with self.assertRaises(ValueError):M.attach_review(self.retriever,req)
  with self.assertRaises(ValueError):M.review_open(self.retriever,{'result_id':row['result_id']})
  self.assertEqual(M.Q.saved_followup(self.retriever.path,row['result_id']),row)
 def test_task_or_source_change_rejects_review_attachment(self):
  self.saved_review();req=self.request();self.source_record['text']='Changed source'
  with self.assertRaises(ValueError):M.attach_review(self.retriever,req)
 def test_task_change_rejects_saved_outcome(self):
  self.saved_review();r=M.attach_review(self.retriever,self.request())
  self.fixture.cap['name']='Changed';self.fixture.update()
  with self.assertRaises(ValueError):M.review_open(self.retriever,{'result_id':r['result_id']})
 def test_revision_changes_during_attach_not_persisted(self):
  self.saved_review();req=self.request();old=M.review_status(self.retriever,{'result_id':req['result_id']})
  with patch.object(M,'review_status',side_effect=[old,{**old,'review_revision':2}]):
   with self.assertRaises(ValueError):M.attach_review(self.retriever,req)
  self.assertFalse(any(f['schema']==M.REVIEW_VERSION for f in M.Q.reopen(self.retriever,self.row['id'])['followups']))
 def test_cached_comparison_does_not_inherit_historical_inference(self):
  e=M.L.begin(self.retriever.path,'/api/stack/decision/statement-review',self.body)
  try:r=M.run(self.retriever,self.body);e.finish(r)
  finally:e.close()
  event=next(x for x in self.events(e) if x['kind']=='local_reader_returned')
  self.assertTrue(event['comparison_cache_hit']);self.assertFalse(event['inference_this_attempt'])
  self.assertIsNone(event['reading_cache_hit_this_attempt']);self.assertEqual(event['reading_id'],self.reading['id'])
 def test_current_reading_cache_inference_and_failed_states(self):
  for status,hit,want in [('completed',True,False),('completed',False,True),('failed',False,None)]:
   self.prepared['cache_hit']=False;self.comparison['variants']['kev'].update(status=status,cache_hit=hit,reason='fixture failure')
   e=M.L.begin(self.retriever.path,'/api/stack/decision/statement-review',self.body)
   try:r=M.run(self.retriever,self.body);e.finish(r)
   finally:e.close()
   event=next(x for x in self.events(e) if x['kind']=='local_reader_returned')
   self.assertIs(event['inference_this_attempt'],want)
   if status=='failed':self.assertEqual(r['status'],'reader_failed')
 def test_interrupted_local_call_preserves_started_identity(self):
  e=M.L.begin(self.retriever.path,'/api/stack/decision/statement-review',self.body)
  try:
   with patch.object(M.C,'prepare',side_effect=KeyboardInterrupt):
    with self.assertRaises(KeyboardInterrupt):M.run(self.retriever,self.body)
  finally:e.close()
  events=self.events(e);self.assertEqual(events[-1]['kind'],'local_reader_started');self.assertEqual(events[-1]['source_hash'],digest(self.source_record))
 def test_review_attempts_bind_failure_to_original_question(self):
  self.saved_review();req=self.request();e=M.L.begin(self.retriever.path,'/api/stack/decision/statement-review-attach',req)
  try:r=M.attach_review(self.retriever,req);e.finish(r)
  finally:e.close()
  self.assertEqual(self.events(e)[0]['input_links']['decision_id'],self.row['id'])
  self.assertEqual(self.events(e)[-1]['review_outcome']['review_revision'],1)
  self.saved_review('rejected',1);e=M.L.begin(self.retriever.path,'/api/stack/decision/statement-review-attach',req)
  try:
   with self.assertRaises(ValueError):M.attach_review(self.retriever,req)
   e.finish()
  finally:e.close()
  self.assertEqual(self.events(e)[0]['input_links']['decision_id'],self.row['id']);self.assertEqual(self.events(e)[-1]['kind'],'operation_failed')

for name in dir(ContractRoute):
 if name.startswith('test_') and name not in ReviewOutcome.__dict__:setattr(ReviewOutcome,name,None)
# Avoid unittest collecting imported base a second time.
del ContractRoute
