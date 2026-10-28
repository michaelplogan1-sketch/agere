"""Independent quantitative claim checks with shared state, no provider calls."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.providers import EvidenceTask,JevProvider
from clinical_core.agere.clinical import system_one as S,backstop_agent as B,system_one_gate as G

OPTIONS={'consistent':'Correct numeric role','contradicted':'Wrong numeric role','unknown':'Unknown'}
QS={k:{'type':'choice','instructions':'Judge '+k,'criteria':OPTIONS} for k in ('first','second')}

def reading(label,p=1.):
 return {'label':label,'probabilities':{k:p if k==label else (1-p)/2 for k in OPTIONS}}

class Batch(unittest.TestCase):
 def test_native_named_questions_preserve_state_and_validate_every_answer(self):
  task=EvidenceTask('Check counts','2030-05-27',{'unknown':'unknown'},[],questions=QS)
  p=JevProvider(None);body=p.request(task);self.assertEqual(body['questions'],QS)
  raw={'model':p.model,'answers':{k:{'type':'choice','choice':'consistent','probabilities':reading('consistent')['probabilities']} for k in QS}}
  p.gateway=SimpleNamespace(send=lambda *a:raw)
  self.assertEqual(set(p.classify(task)['judgments']),set(QS))
  del raw['answers']['second']
  with self.assertRaisesRegex(ValueError,'omitted'):p.classify(task)

 def test_only_uncertain_field_falls_back_and_strong_primary_disagreement_is_not_overwritten(self):
  calls=[]
  class Gateway:
   def classify(self,provider,task):
    calls.append((provider,list(task.questions)))
    rows={'first':reading('contradicted'), 'second':reading('consistent',.6)} if provider=='jev' else {'second':reading('consistent')}
    return {'judgments':rows,'model':provider,'cost_estimate':{'estimated_usd':.001}}
  with tempfile.TemporaryDirectory() as d,patch.object(S,'_gateway',return_value=Gateway()):
   result=S.judge_fields('Check numbers',QS,[],as_of='2030-05-27',ledger=Path(d)/'cost.sqlite3')
  self.assertEqual(calls,[('jev',['first','second']),('perplexity',['second'])])
  self.assertEqual(result['judgments']['first']['label'],'contradicted')
  self.assertEqual(result['judgments']['first']['decided_by'],'jev')
  self.assertEqual(result['judgments']['second']['decided_by'],'perplexity');self.assertEqual(G.cost(result),(2,.002))

 def test_failed_providers_remain_unknown_and_no_claude_fallback_is_added(self):
  calls=[]
  class Gateway:
   def classify(self,provider,task):calls.append(provider);raise ValueError('fixture failure')
  with tempfile.TemporaryDirectory() as d,patch.object(S,'_gateway',return_value=Gateway()):
   result=S.judge_fields('Check numbers',QS,[],as_of='2030-05-27',ledger=Path(d)/'cost.sqlite3')
  self.assertEqual(calls,['jev','perplexity']);self.assertTrue(all(not r['decided_by'] for r in result['judgments'].values()))

 def test_explicit_empty_provider_order_makes_no_call(self):
  with tempfile.TemporaryDirectory() as d,patch.object(S,'_gateway',side_effect=AssertionError('No provider')):
   result=S.judge_fields('Check',QS,[],as_of='2030-05-27',ledger=Path(d)/'cost.sqlite3',order=[])
  self.assertEqual(result['steps'],[]);self.assertTrue(all(r['label']=='unknown' for r in result['judgments'].values()))

class Meaning(unittest.TestCase):
 def data(self):
  rows=[{'patient_id':'A','basis':'both'},{'patient_id':'B','basis':'narrative_only'}]
  call={'name':'query_sql','input':{'sql':'SELECT patient_id,basis FROM received'},'sql':'SELECT patient_id,basis FROM received',
        'result':{'columns':['patient_id','basis'],'rows':rows,'result_view':'agere_result_1'}}
  answer={'status':'answered','answer_type':'table','headline':'Two patients are narrative-only',
          'facts':{'People':'2 patients, 2 narrative-only'},'table_from_tool':'agere_result_1','sources':[],'workflow':[]}
  return rows,call,answer

 def test_per_claim_questions_get_code_counts_and_no_source_is_silently_relabelled(self):
  rows,call,a=self.data();B.materialize_table(a,[call]);seen=[]
  class Decider:
   def judge_fields(self,subject,questions,evidence,**kw):
    seen.append((questions,json.loads(evidence[0]['text'])))
    return {'judgments':{k:{'label':'contradicted','decided_by':'fixture'} for k in questions},'steps':[]}
  result=B.numeric_role_review('Count the cohort',a,[call],decider=Decider(),as_of='2030-05-27',ledger=None)
  questions,state=seen[0]
  self.assertEqual(state['exact_final_table_row_counts']['columns']['basis']['values'],[{'value':'both','rows':1},{'value':'narrative_only','rows':1}])
  self.assertEqual(state['claims']['claim_1']['text'],'2 patients, 2 narrative-only')
  self.assertEqual(len(questions),1);self.assertIn('claim_1',questions);self.assertEqual(len(result['judgments']),1)

 def test_contradicted_numeric_role_repairs_before_global_check_and_keeps_computed_table(self):
  rows,call,a=self.data()
  class Decider:
   def judge_fields(self,*args,**kw):return {'claims':{},'judgments':{'claim_1':{'label':'contradicted','decided_by':'fixture'}},'steps':[{'provider':'jev','cost_usd':.001}]}
   def decide(self,*a,**kw):raise AssertionError('No global pass can override a known role contradiction')
  with patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=Decider()):
   review=B.review_final(SimpleNamespace(),'Count the cohort',a,[call],SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
  self.assertEqual(review['status'],'repair');self.assertIn('Quantitative meaning',review['feedback'])
  self.assertEqual(a['table'],rows);self.assertEqual(review['calls'],1);self.assertEqual(review['cost_usd'],.001)

if __name__=='__main__':unittest.main()
