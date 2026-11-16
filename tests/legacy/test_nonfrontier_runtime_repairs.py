"""General handoff, provenance and native-completion contracts; no live models."""
import json,os,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.providers import JevProvider,EvidenceTask
from clinical_core.contracts import digest
from clinical_core.agere.clinical import spec_path as P,spec_fill as F,system_one as S,typed_document_review as T,document_review as D,document_readers as R,local_request_worker as W

GOOD={'type':'choice','choice':'finding','probabilities':{'finding':.9,'irrelevant':.05,'unknown':.05}}
class Replay:
 def __init__(self,answers):self.answers=answers
 def send(self,*a,**k):return {'model':'test-model','answers':self.answers}
def question():return {'type':'choice','instructions':'Source statement present?','criteria':T.OPTIONS}
def receipt(name,usd):return {'fillers':{name:{'status':'valid'}},'calls':1,'usd':usd,'cost_known':True,'seconds':1}
def decline(name):
 e=F.SpecNotFilled('declined');e.receipt=receipt(name,.001);return e

class PartialChoices(unittest.TestCase):
 def test_valid_siblings_survive_but_invalid_missing_or_invented_fields_do_not(self):
  q={k:question()for k in ['a','b','c','d']}
  nonmaximum={'type':'choice','choice':'finding','probabilities':{'finding':.1,'irrelevant':.85,'unknown':.05}}
  result=JevProvider(Replay({'a':GOOD,'b':None,'c':nonmaximum,'invented':GOOD})).classify(EvidenceTask('q','2030-05-27',{'unknown':'u'},[],questions=q))
  self.assertEqual(set(result['judgments']),set(q));self.assertEqual(result['judgments']['a']['label'],'finding')
  self.assertEqual(set(result['invalid_fields']),{'b','c','d'});self.assertEqual(result['unexpected_fields'],['invented'])
  self.assertTrue(all(result['judgments'][k]['label']=='unknown'for k in result['invalid_fields']))
 def test_only_unresolved_fields_reach_the_next_provider(self):
  calls=[]
  class Gateway:
   def classify(self,provider,task):
    calls.append((provider,tuple(task.questions)))
    answers={k:GOOD for k in task.questions};answers.update({'b':None}if provider=='jev'else{})
    r=JevProvider(Replay(answers)).classify(task)
    return {**r,'request_hash':provider,'cache_hit':False,'cost_estimate':{'estimated_usd':.001}}
  with patch.object(S,'_open_ledger',return_value=(None,False)),patch.object(S,'_gateway',return_value=Gateway()):
   out=S.judge_fields('q',{k:question()for k in ['a','b']},[],as_of='2030-05-27',order=['jev','perplexity'])
  self.assertEqual(calls,[('jev',('a','b')),('perplexity',('b',))]);self.assertEqual(out['cost_usd'],.002);self.assertEqual(out['steps'][0]['status'],'partial');self.assertEqual(set(out['steps'][0]['invalid_fields']),{'b'})

class SequentialSpec(unittest.TestCase):
 def test_valid_cheap_spec_does_not_start_claude(self):
  with patch.dict(os.environ,{'AGERE_NONFRONTIER_FIRST':'on'}),patch.object(F,'fill',return_value={'winner':'s1_slots','receipt':receipt('s1_slots',.001)})as call:
   out=P.fill_in_order(None,None,'unchanged original',fillers=('grammar','s1_slots','haiku'))
  self.assertEqual(out['winner'],'s1_slots');self.assertEqual(call.call_count,1);self.assertEqual(call.call_args.kwargs['fillers'],('grammar','s1_slots'))
 def test_hosted_follows_decline_and_both_attempts_remain_costed(self):
  with patch.dict(os.environ,{'AGERE_NONFRONTIER_FIRST':'on'}),patch.object(F,'fill',side_effect=[decline('s1_slots'),{'winner':'haiku','receipt':receipt('haiku',.01)}])as call:
   out=P.fill_in_order(None,None,'unchanged original',fillers=('grammar','s1_slots','haiku'))
  self.assertEqual([c.kwargs['fillers']for c in call.call_args_list],[('grammar','s1_slots'),('haiku',)])
  self.assertTrue(all(c.args[2]=='unchanged original'for c in call.call_args_list));self.assertEqual(out['receipt']['calls'],2);self.assertAlmostEqual(out['receipt']['usd'],.011)
 def test_existing_parallel_default_is_preserved(self):
  with patch.dict(os.environ,{'AGERE_NONFRONTIER_FIRST':'off'}),patch.object(F,'fill',return_value={})as call:P.fill_in_order(None,None,'q',fillers=('grammar','s1_slots','haiku'))
  self.assertEqual(call.call_count,1);self.assertEqual(call.call_args.kwargs['fillers'],('grammar','s1_slots','haiku'))

class NativeCompletion(unittest.TestCase):
 def test_completion_uses_checkpoint_and_memory_capacity(self):
  self.assertEqual(W.completion_allowance(1000,{'max_position_embeddings':32768},1024,budget_gb=1),31768)
  self.assertEqual(W.completion_allowance(1000,{'max_position_embeddings':262144},1024**2,budget_gb=1),24)
  self.assertEqual(W.completion_allowance(100,{'text_config':{'max_position_embeddings':200}},0),100)
 def test_exhausted_or_unknown_context_declines_without_shortening(self):
  with self.assertRaises(W.LocalDeclined):W.completion_allowance(200,{'max_position_embeddings':200},0)
  with self.assertRaises(W.LocalDeclined):W.completion_allowance(10,{},1024)

class ReviewProvenance(unittest.TestCase):
 def accepted(self,subject,questions,evidence,**kw):return {'judgments':{k:{'label':'finding','decided_by':'scripted','provider':'scripted'}for k in questions},'steps':[],'cost_usd':0}
 def test_named_questions_keep_source_bindings(self):
  out=T.read(SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False)),'A',[{'record_id':'r','date':'','text':'Source statement.'}],{'reason':'What is documented?'},None,judge=self.accepted,ranking=False)
  self.assertEqual(out['checks'][0]['question_bindings']['f0'],{'field':'reason','span_start':0,'span_end':17,'source_block_digest':digest('Source statement.'),'question_digest':digest('What is documented?')})
 def test_native_predictions_and_statistics_are_retained(self):
  class Reader:
   identity='scripted-native'
   def __call__(self,state,questions):return {'answers':{k:GOOD for k in questions},'stats':{'input_tokens':123,'seconds':.2}}
  with patch.object(R,'get',return_value=Reader()),patch.dict(os.environ,{'AGERE_DOCUMENT_LOCAL_READERS':'kev,d1'}):
   out=T.local_readers_then_recheck(SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False)),{'a':question()},[],{'judgments':{},'steps':[],'cost_usd':0},None,self.accepted)
  self.assertEqual(len(out['local_steps']),1);self.assertEqual(out['local_steps'][0]['native_answers']['a']['choice'],'finding');self.assertEqual(out['local_steps'][0]['native_calls'][0]['stats']['input_tokens'],123)
 def test_cost_completeness_does_not_hide_validation_errors(self):
  with tempfile.TemporaryDirectory()as temp:
   p=Path(temp);db=sqlite3.connect(':memory:');db.executescript("create table patient(patient_id);insert into patient values('A')")
   tools=SimpleNamespace(db=db,as_of='2030-05-27',stack=SimpleNamespace(path=p,ledger_path=p/'ledger.sqlite3',allow_live=False),in_scope=lambda p:None,continuation_identity=lambda:{'source':'fixture'},_texts=lambda p:[('r','clinical_note','notes','office','2030-01-01','Source statement.','')],document_reviews=[],subtool_accounting={'new_paid_api_calls':0,'estimated_cost_usd':0})
   for cost,known in [(.001,True),(None,False)]:
    payload={'findings':{'field':[]},'checks':[{'steps':[{'provider':'jev','status':'error','error':'Invalid field','cost_usd':cost,'attempted':True}]}],'unresolved':[],'version':T.VERSION,'coverage':[],'ranking':{},'all_blocks_processed':True}
    with patch.object(T,'read',return_value=payload),patch.dict(os.environ,{'AGERE_DOCUMENT_REVIEW':'typed_spans'}),patch('clinical_core.agere.clinical.together._ledgered_send'):
     result=D.review(tools,['A'],{'field':'What is documented?'})
    self.assertIs(result['accounting']['api_cost_known'],known);self.assertEqual(tools.document_reviews[-1]['typed_checks'][0]['steps'][0]['error'],'Invalid field')
   db.close()

if __name__=='__main__':unittest.main()
