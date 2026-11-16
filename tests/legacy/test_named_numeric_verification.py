"""Independent quantitative claim checks with shared state, no provider calls."""
import copy,json,tempfile,unittest,os
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
  partial=p.classify(task)
  self.assertIn('second',partial['invalid_fields'])
  self.assertEqual(partial['judgments']['second']['label'],'unknown')
  self.assertEqual(partial['judgments']['second']['probabilities'],{})
  self.assertEqual(partial['judgments']['first']['label'],'consistent')

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

 def test_unqualified_numeric_prototype_is_off_by_default(self):
  rows,call,a=self.data()
  # This test isolates selection of the old prototype. Quantitative prose has
  # its own earlier compiler checks and is exercised in computed-answer tests.
  a['headline']='Cohort details';a['facts']={}
  decider=SimpleNamespace(decide=lambda *a,**kw:{'label':'supported','decided_by':'fixture','steps':[]})
  with patch.dict(os.environ,{}),patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=decider),patch.object(G,'check',return_value={'label':'answers_fully','calls':0,'cost_usd':0}),patch.object(B,'numeric_role_review',side_effect=AssertionError('Unselected prototype')):
   os.environ.pop('AGERE_QUANTITATIVE_ROLE_CHECK',None)
   out=B.review_final(SimpleNamespace(),'Count the cohort',a,[call],SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
  self.assertEqual(out['numeric_roles']['status'],'unselected_after_native_regressions')
  self.assertEqual(a['table'],rows)

 def test_selected_category_is_compared_in_code_without_showing_counts_to_chooser(self):
  rows,call,a=self.data();B.materialize_table(a,[call]);seen=[]
  class Decider:
   def judge_fields(self,subject,questions,evidence,**kw):
    seen.append((questions,json.loads(evidence[0]['text'])))
    stage=len(seen)
    return {'judgments':{k:{'label':'column_1' if stage==1 else 'value_1','decided_by':'fixture'} for k in questions},'steps':[]}
  result=B.numeric_role_review('Count the cohort',a,[call],decider=Decider(),as_of='2030-05-27',ledger=None)
  self.assertEqual(result['judgments']['claim_1']['label'],'contradicted')
  self.assertTrue(all(v['computed_value']==1 for v in result['bindings'].values()))
  self.assertNotIn('exact_final_table_row_counts',seen[0][1])
  self.assertEqual(seen[0][1]['claims']['claim_1']['text'],'2 patients, 2 narrative-only')
  self.assertIn('basis',json.dumps(seen[0][0]));self.assertIn('narrative_only',json.dumps(seen[1][0]))

 def test_uncertain_role_stays_unknown_and_dates_ids_are_not_count_mentions(self):
  rows,call,a=self.data();B.materialize_table(a,[call]);a['facts']={'Date':'2030-05-27 patient P-1234, 2 patients'}
  seen=[]
  class Decider:
   def judge_fields(self,subject,questions,evidence,**kw):
    seen.append(questions)
    return {'judgments':{k:{'label':'unknown','decided_by':None} for k in questions},'steps':[]}
  out=B.numeric_role_review('Count',a,[call],decider=Decider(),as_of='2030-05-27',ledger=None)
  self.assertEqual(len(out['bindings']),1);self.assertEqual(out['judgments']['claim_1']['label'],'unknown')
  self.assertEqual(len(seen),1)

 def test_hierarchy_retains_all_categories_beyond_provider_option_limit(self):
  a={'table_binding':{'result_view':'final'},'table':[{'category':str(i)} for i in range(600)],'facts':{'Count':'7 in category 599'}}
  seen=[]
  class Decider:
   def judge_fields(self,subject,questions,evidence,**kw):
    seen.append(questions)
    out={}
    for k,q in questions.items():
     options=q['criteria'];self_outer.assertLessEqual(len(options),255)
     if 'column_0' in options:label='column_0'
     elif 'branch_2' in options:label='branch_2'
     else:label='value_599'
     out[k]={'label':label,'decided_by':'fixture'}
    return {'judgments':out,'steps':[]}
  self_outer=self
  out=B.numeric_role_review('Count',a,[],decider=Decider(),as_of='2030-05-27',ledger=None)
  self.assertTrue(any('branch_2' in json.dumps(x) for x in seen))
  self.assertEqual(out['judgments']['claim_1']['label'],'contradicted')

 def test_contradicted_numeric_role_repairs_before_global_check_and_keeps_computed_table(self):
  rows,call,a=self.data()
  scalar={'name':'query_sql','input':{'sql':'SELECT count(*) AS patients FROM agere_result_1'},
          'result':{'result_view':'agere_result_2','columns':['patients'],'rows':[[2]],'table_rows':1}}
  a['headline']='Cohort details'
  a['facts']={'People':{'template':'{n} narrative-only',
                      'bindings':{'n':{'result_view':'agere_result_2','row':0,'column':'patients'}}}}
  class Decider:
   def judge_fields(self,*args,**kw):raise AssertionError('Mapping tested separately')
   def decide(self,*a,**kw):raise AssertionError('No global pass can override a known role contradiction')
  numeric={'claims':{'claim_1':{'field':'People','text':'2 narrative-only'}},'judgments':{'claim_1':{'label':'contradicted','decided_by':'code_after_typed_role_binding'}},'steps':[{'provider':'jev','cost_usd':.001}]}
  with patch.dict(os.environ,{'AGERE_QUANTITATIVE_ROLE_CHECK':'on'}),patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=Decider()),patch.object(B,'numeric_role_review',return_value=numeric):
   review=B.review_final(SimpleNamespace(),'Count the cohort',a,[call,scalar],SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
  self.assertEqual(review['status'],'repair');self.assertIn('Quantitative meaning',review['feedback'])
  self.assertEqual(a['table'],rows);self.assertEqual(review['calls'],1);self.assertEqual(review['cost_usd'],.001)

if __name__=='__main__':unittest.main()
