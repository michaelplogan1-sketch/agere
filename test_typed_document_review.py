import json,os,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import typed_document_review as T,document_review as D,semantic_analyst as S

class TypedDocuments(unittest.TestCase):
 def test_every_source_character_continued_and_no_population_sampling(self):
  text='Name: specialist\nRole: cardiology\n\n'+('long source '*1000)+'\n\nDeclined due to cost.'
  spans=T.blocks(text)
  self.assertEqual(''.join(c for s in spans for c in s['text'] if not c.isspace()),''.join(c for c in text if not c.isspace()))
  self.assertTrue(all(text[s['start']:s['end']]==s['text']for s in spans))
  self.assertTrue(all(len(s['text'])<=2048 for s in spans))
 def test_typed_selection_copies_exact_source_and_keeps_full_context(self):
  source='Signed: Dr Example\nSpecialty: oncology\n\nMedication withheld because potassium was high.'
  doc={'record_id':'r','date':'2030-01-01','text':source};seen=[]
  def judge(subject,questions,evidence,**kw):
   self.assertEqual(evidence[0]['text'],source);seen.extend(questions)
   return {'judgments':{k:{'label':'finding'if 'potassium'in v['instructions']['target_block']else 'irrelevant','decided_by':'scripted','provider':'scripted'}for k,v in questions.items()},'steps':[],'cost_usd':0}
  out=T.read(SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False)),'A',[doc],{'reason':'Documented medication deferral reason?'},None,judge=judge,ranking=False)
  self.assertEqual(len(seen),len(T.blocks(source)));self.assertEqual(out['findings']['reason'][0]['quote'],'Medication withheld because potassium was high.')
  self.assertEqual(out['unresolved'],[]);self.assertTrue(out['all_blocks_processed'])
 def test_unknown_is_retained_as_field_specific_repair_not_a_negative(self):
  def judge(subject,questions,evidence,**kw):return {'judgments':{k:{'label':'unknown','decided_by':None}for k in questions},'steps':[],'cost_usd':0}
  out=T.read(SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False)),'A',[{'record_id':'r','date':'','text':'Unclear current use.'}],{'use':'Current use?'},None,judge=judge,ranking=False)
  self.assertEqual(out['findings']['use'],[]);self.assertEqual(out['unresolved'][0]['field'],'use')
 def test_large_preview_retains_named_field_counts_from_all_patients(self):
  rows=[{'patient_id':'A','review_json':json.dumps({'care_team':[{'value':'x'*16000}]})},{'patient_id':'B','review_json':json.dumps({'care_team':[{'value':'Named clinician'}],'dose_reason':[]})}]
  shown=S.actor_result({'columns':['patient_id','review_json'],'rows':rows,'result_view':'v'})
  self.assertEqual(shown['narrative_field_manifest']['fields']['care_team']['patients_with_findings'],2)
  self.assertEqual(shown['narrative_field_manifest']['fields']['dose_reason']['findings'],0)
  self.assertEqual(shown['rows'],[])
 def test_reader_uses_no_claude_for_resolved_fields_and_preserves_no_document_patient(self):
  with tempfile.TemporaryDirectory()as temp:
   p=Path(temp);db=sqlite3.connect(':memory:');db.executescript("create table patient(patient_id);insert into patient values('A'),('B')")
   tools=SimpleNamespace(db=db,as_of='2030-05-27',stack=SimpleNamespace(path=p,ledger_path=p/'ledger.sqlite3',allow_live=False),in_scope=lambda p:None,continuation_identity=lambda:{'source':'fixture'},_texts=lambda p:[('r','clinical_note','notes','office','2030-01-01','Documented source.','')]if p=='A'else[],document_reviews=[],subtool_accounting={'new_paid_api_calls':0,'estimated_cost_usd':0})
   payload={'findings':{'field':[{'value':'Documented source.','quote':'Documented source.','date':'2030-01-01','record_id':'r'}]},'checks':[],'unresolved':[],'version':T.VERSION,'coverage':[],'ranking':{},'all_blocks_processed':True}
   with patch.dict(os.environ,{'AGERE_DOCUMENT_REVIEW':'typed_spans'}),patch.object(T,'read',return_value=payload),patch('clinical_core.agere.clinical.together._ledgered_send')as sender:
    result=D.review(tools,['A','B'],{'field':'What is documented?'})
   sender.return_value.assert_not_called();self.assertEqual([r['review_status']for r in result['rows']],['reviewed','no_documents']);db.close()


class LocalHandoffs(unittest.TestCase):
 def test_system_one_uncertainty_visits_all_local_readers_before_frontier(self):
  from clinical_core.agere.clinical import document_readers as R
  calls=[]
  class Reader:
   identity='scripted-native-reader'
   def __init__(self,name):self.name=name
   def __call__(self,state,questions):
    calls.append((self.name,len(questions)))
    self_answers={k:{'choice':'finding','probabilities':{'finding':.9,'irrelevant':.05,'unknown':.05}}for k in questions}
    return {'answers':self_answers}
  questions={str(i):{'type':'choice','instructions':'task','criteria':T.OPTIONS}for i in range(7)}
  result={'judgments':{k:{'label':'unknown','decided_by':None}for k in questions},'steps':[],'cost_usd':0}
  def recheck(subject,named,evidence,**kw):
   return {'judgments':{k:{'label':'unknown','decided_by':None}for k in named},'steps':[],'cost_usd':0}
  with patch.object(R,'get',side_effect=lambda name:Reader(name)),patch.dict(os.environ,{'AGERE_DOCUMENT_LOCAL_READERS':'kev,d1'}):
   out=T.local_readers_then_recheck(SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False)),questions,[],result,None,recheck)
  self.assertEqual(calls,[('kev',3),('kev',3),('kev',1),('d1',3),('d1',3),('d1',1)])
  self.assertEqual(len(out['local_steps']),2);self.assertEqual(out['steps'],[])
 def test_unavailable_component_does_not_skip_remaining_components(self):
  from clinical_core.agere.clinical import document_readers as R
  seen=[]
  def unavailable(name):seen.append(name);raise FileNotFoundError(name)
  with patch.object(R,'get',side_effect=unavailable),patch.dict(os.environ,{'AGERE_DOCUMENT_LOCAL_READERS':'kev,d1'}):
   result=T.local_readers_then_recheck(SimpleNamespace(stack=None),{'x':{}},[],{'judgments':{},'steps':[],'cost_usd':0},None,None)
  self.assertEqual(seen,['kev','d1']);self.assertEqual(len(result['local_steps']),2)

class CandidateServing(unittest.TestCase):
 def test_unselected_adapter_is_only_available_under_explicit_isolated_manifest(self):
  from test_adapter_learning import base_fixture
  from clinical_core.agere.clinical import adapter_learning as A,component_learning as C
  with tempfile.TemporaryDirectory()as temp:
   p=Path(temp);base=base_fixture(p/'base');source=p/'weights';source.mkdir();(source/'adapter_config.json').write_text('{}');(source/'adapters.safetensors').write_bytes(b'fixture-weights')
   captured=A.capture(p/'training-registry','typed',source,base,{'selected':False},activate=False)
   runtime=p/'runtime';C.database(runtime).close();manifest=p/'candidates.json';manifest.write_text(json.dumps({'schema':'checked-adapter-candidates/1','authority':'isolated_checked_proposal','learners':{'typed':{'registry':str(p/'training-registry'),'revision':captured['id']}}}))
   with patch.dict(os.environ,{'AGERE_COMPONENT_RELEASE_ROOT':str(runtime),'AGERE_ADAPTER_CANDIDATES':''}):self.assertIsNone(A.release_identity('typed',base=base))
   with patch.dict(os.environ,{'AGERE_COMPONENT_RELEASE_ROOT':str(runtime),'AGERE_ADAPTER_CANDIDATES':str(manifest)}):
    value=A.release_identity('typed',base=base);self.assertTrue(value['candidate']);self.assertEqual(value['revision'],captured['id'])
    self.assertIsNotNone(C.active_identity());self.assertIsNone(C.active(p/'training-registry','slm_typed'))
    directory=Path(value['adapter']);(directory/'adapters.safetensors').write_bytes(b'changed')
    self.assertIsNone(A.release_identity('typed',base=base))
 def test_target_range_does_not_imply_reference_upper_dose(self):
  from clinical_core.agere.clinical import dose
  db=dose.register(sqlite3.connect(':memory:'));db.row_factory=sqlite3.Row
  db.execute('create table record(record_id,patient_id,stream,payload)');db.execute('insert into record values(?,?,?,?)',('r','A','medication_lists',json.dumps({'event_date':'2030-01-01','name':'losartan','dose':'50 mg','frequency':'daily','list_status':'active'})))
  row=dict(db.execute('select * from '+dose.current_dose_sql('2030-05-27',180)).fetchone());db.close()
  self.assertEqual(row['dose_status'],'at_target');self.assertEqual(row['reference_max_status'],'below_reference_upper');self.assertEqual(row['documented_reference_max_status'],'below_reference_upper');self.assertEqual(row['target_max_daily_mg'],150)

class StageOrder(unittest.TestCase):
 def test_all_specialists_precede_hosted_generation_without_losing_a_stage(self):
  from clinical_core.agere.clinical import proposers as P
  order=('exact_local_plan','spec_path','backstop_agent','atomic_system_one','qwen_typed_interpreter','bottled_sql','evidence_review','hosted_typed_interpreter')
  changed=P.nonfrontier_first(order)
  self.assertCountEqual(order,changed)
  self.assertEqual(changed[:5],('exact_local_plan','atomic_system_one','qwen_typed_interpreter','bottled_sql','evidence_review'))

class NoHostedPrefetch(unittest.TestCase):
 def test_nonfrontier_order_also_disables_speculative_claude(self):
  from clinical_core.agere.clinical import shared_planner as P,spec_fill
  with patch.dict(os.environ,{'AGERE_NONFRONTIER_FIRST':'on'}),patch.object(spec_fill,'start_early')as send:
   P.start_spec_early(SimpleNamespace(allow_live=True),None,'Any population analysis',None)
  send.assert_not_called()

class PopulationScope(unittest.TestCase):
 def test_atomic_population_defaults_to_all_and_preserves_explicit_limits(self):
  from clinical_core.agere.clinical import atomic_questions as A
  with patch.dict(os.environ,{},clear=True):
   self.assertIsNone(A.max_patients())
   p={'question':'q','sub_questions':[A._sub('a','Is it?',A.YES_NO)],'aggregation':'any','returns':'verdict'}
   self.assertIsNone(A.validate(p)['maximum_patients'])
   self.assertEqual(A.validate({**p,'maximum_patients':501})['maximum_patients'],501)
   with self.assertRaises(ValueError):A.validate({**p,'maximum_patients':True})

if __name__=='__main__':unittest.main()
