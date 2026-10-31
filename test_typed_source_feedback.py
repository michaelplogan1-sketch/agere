"""Exact source feedback suspends reuse without fabricating a corrected label."""
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import copy,json,sqlite3,unittest
from clinical_core.agere.clinical import typed_document_review as T,backstop_agent as B

class TypedSourceFeedback(unittest.TestCase):
 def setUp(self):
  self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name);self.calls=[]
  self.tools=SimpleNamespace(stack=SimpleNamespace(path=self.path))
  self.evidence=[{'id':'note','date':'2030-01-01','text':'The source documents two independent observations.'}]
  self.named={k:{'instructions':{'source_context_id':'note','question':'Question '+k,'target_block':k}}for k in ('a','b')}
  def judge(task,named,evidence,**kwargs):
   self.calls.append(list(named));return {'judgments':{k:{'label':'finding','provider':'fixture','decided_by':'fixture',
    'probabilities':{'finding':.9,'irrelevant':.05,'unknown':.05}}for k in named},
    'steps':[{'request_hash':'request-'+str(len(self.calls)),'provider':'fixture','status':'answered'}],'cost_usd':0}
  self.check=T.cached_source_judge(self.tools,judge,patient_id='A')
 def read(self,evidence=None):return self.check('Source-bound task',self.named,self.evidence if evidence is None else evidence,as_of='2030-05-27')

 def test_unchanged_disputed_reading_abstains_without_dispatch_or_changing_other_readings(self):
  first=self.read();old=copy.deepcopy(first['judgments']);a=old['a']['source_observation_id'];b=old['b']['source_observation_id']
  self.assertEqual(T.bind_assertion_observations(self.path,'assertion-a',[a]),1)
  self.assertEqual(T.bind_assertion_observations(self.path,'assertion-b',[b]),1)
  self.assertEqual(T.quarantine_assertions(self.path,['assertion-a'],'Unadjudicated objection'),1)
  self.assertEqual(T.quarantine_assertions(self.path,['assertion-a'],'Unadjudicated objection'),0)
  later=self.read();self.assertEqual(len(self.calls),1)
  self.assertEqual(later['judgments']['a']['label'],'unknown');self.assertIsNone(later['judgments']['a']['decided_by'])
  self.assertEqual(later['judgments']['b'],old['b'])
  with sqlite3.connect(T.observation_path(self.path))as db:
   payload=json.loads(db.execute('SELECT payload FROM observations WHERE id=?',(a,)).fetchone()[0])
   self.assertEqual(payload['reading']['label'],'finding')
   self.assertFalse(json.loads(db.execute('SELECT payload FROM typed_observation_events').fetchone()[0])['feedback_is_clinical_truth'])
   db.execute('DELETE FROM observations WHERE id=?',(a,))
  self.assertEqual(self.read()['judgments']['a']['label'],'unknown');self.assertEqual(len(self.calls),1)

 def test_source_changes_require_their_own_judgment_and_bindings_are_immutable(self):
  first=self.read();a=first['judgments']['a']['source_observation_id']
  T.bind_assertion_observations(self.path,'assertion-a',[a]);T.quarantine_assertions(self.path,['assertion-a'],'Unadjudicated objection')
  changed=copy.deepcopy(self.evidence);changed[0]['text']+=' Updated source.';new=self.read(changed)
  self.assertEqual(len(self.calls),2);self.assertNotEqual(new['judgments']['a']['source_observation_id'],a)
  with sqlite3.connect(T.observation_path(self.path))as db:
   with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE typed_observation_bindings SET assertion_id='changed'")
   with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM typed_observation_events')
  with self.assertRaisesRegex(ValueError,'absent'):T.bind_assertion_observations(self.path,'invented',['missing'])

 def test_final_source_review_feedback_reaches_only_its_exact_mapped_typed_reading(self):
  first=self.read();T.bind_assertion_observations(self.path,'assertion-a',[first['judgments']['a']['source_observation_id']])
  values={'a':{'assertion':{'id':'assertion-a'},'record_id':'note'},'b':{'assertion':{'id':'assertion-b'},'record_id':'other'}}
  from clinical_core.agere.deliverables.receipts import compact_json
  claims=[{'id':k,'field':k,'path':[k]}for k in values];text=json.dumps(compact_json(values));packet={'id':'source','date':'2030-05-27','bytes':len(text),'text':text,'claim_manifest':claims}
  def localize(task,named,evidence,**kw):return {'steps':[],'judgments':{k:{'label':'missing_evidence'if k=='a'else'unknown','decided_by':'fixture'if k=='a'else None}for k in named}}
  review=B.check_support_packets([packet],lambda *a,**k:{'label':'unknown','decided_by':None,'probabilities':{'unknown':.9},'steps':[]},
   as_of='2030-05-27',ledger=None,field_decider=localize,source_observation_path=self.path)
  a=next(f for f in review['claim_feedback']if f['field']=='a');self.assertEqual(a['typed_source_observations_quarantined'],1)
  after=self.read();self.assertEqual(after['judgments']['a']['label'],'unknown');self.assertEqual(after['judgments']['b'],first['judgments']['b']);self.assertEqual(len(self.calls),1)

 def test_context_isolation_and_local_handoff_do_not_bypass_source_quarantine(self):
  from unittest.mock import patch
  evidence=[*self.evidence,{'id':'other','date':'2030-01-01','text':'Another source.'}]
  result={'judgments':{'a':T._disputed_reading('suspended')},'steps':[],'cost_usd':0}
  tools=SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=True))
  def forbidden(*args,**kwargs):raise AssertionError('Quarantine must not turn into a reread')
  with patch.dict('os.environ',{'AGERE_DOCUMENT_LOCAL_READERS':'unavailable_fixture_reader'}):
   isolated=T.isolate_unresolved_sources(tools,{'a':self.named['a']},evidence,result,None,forbidden)
   local=T.local_readers_then_recheck(tools,{'a':self.named['a']},evidence,isolated,None,forbidden)
  self.assertIs(local,result);self.assertNotIn('local_steps',result);self.assertEqual(result['steps'],[])

 def test_competing_source_readings_are_preserved_and_quarantined(self):
  named={'a':self.named['a']};calls=[]
  def answer(label,request):
   return {'judgments':{'a':{'label':label,'decided_by':'fixture','provider':'fixture','probabilities':{label:.95,'unknown':.05}}},
           'steps':[{'request_hash':request}],'cost_usd':0}
  other=T.cached_source_judge(self.tools,lambda *a,**k:answer('irrelevant','competing-request'),patient_id='A')
  def racing(task,named,evidence,**kwargs):
   calls.append(task);other(task,named,evidence,**kwargs);return answer('finding','original-request')
  main=T.cached_source_judge(self.tools,racing,patient_id='A')
  first=main('Source-bound task',named,self.evidence,as_of='2030-05-27')
  self.assertEqual(first['judgments']['a']['label'],'unknown')
  again=main('Source-bound task',named,self.evidence,as_of='2030-05-27');self.assertEqual(again['judgments']['a']['label'],'unknown');self.assertEqual(len(calls),1)
  with sqlite3.connect(T.observation_path(self.path))as db:
   old=json.loads(db.execute('SELECT payload FROM observations').fetchone()[0]);event=json.loads(db.execute('SELECT payload FROM typed_observation_events').fetchone()[0])
   self.assertEqual(old['reading']['label'],'irrelevant');self.assertEqual(event['reading']['label'],'finding');self.assertFalse(event['feedback_is_clinical_truth'])

if __name__=='__main__':unittest.main()
