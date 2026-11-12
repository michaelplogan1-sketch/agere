import json,unittest,copy
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,document_review as D,harness_improvement as H
from clinical_core.agere.deliverables.receipts import uncompact_json

class Composition(unittest.TestCase):
 def test_recovered_reading_cannot_bypass_source_suspension(self):
  q,m,docs,old=self.fixture();found=json.loads(old['rows'][0]['review_json'])
  found['therapy_reason'][0]['assertion']={'id':'retained-assertion'}
  old['rows'][0]['review_json']=json.dumps(found)
  for status in ('current','suspended','unavailable'):
   with self.subTest(status=status),H.owned_recovery([],question='q',document_reviews=[old]),patch(
       'clinical_core.agere.clinical.typed_document_review.assertion_disposition',return_value={'status':status})as check:
    out=H.recover_document_observations(q,m,docs,path='fixture')
    check.assert_called_once_with('fixture',['retained-assertion'])
    self.assertEqual(out is not None,status=='current')

 def test_focused_recovery_compares_selection_but_not_how_many_prior_seeds_found_it(self):
  q,m,docs,old=self.fixture()
  m[0]['source_selection']={'schema':'focused-document-retrieval/1','prior_source_seeds':{'matched':1},
                            'sources':[{'record_id':'r','selected_fields':['therapy_reason']}]}
  old['manifest']=copy.deepcopy(m);m[0]['source_selection']['prior_source_seeds']['matched']=2
  with H.owned_recovery([],question='q',document_reviews=[old]):
   self.assertIsNotNone(H.recover_document_observations(q,m,docs))
   m[0]['source_selection']['sources'][0]['selected_fields']=[]
   self.assertIsNone(H.recover_document_observations(q,m,docs))

 def test_literal_signature_is_historical_not_current_assignment(self):
  text='Provider: Jane Doe, MD\nDear Amir Jones, MD,\nSincerely,\nJane Doe, MD\nPulmonology\ncc: Amir Jones, MD\n'
  rows=D.correspondence_roles([{'record_id':'r','date':'2030-01-01','text':text}]);self.assertTrue(rows)
  author=next(r for r in rows if r['relationship']=='signed_correspondence_author')
  self.assertEqual(author['current_assignment'],'not_established');self.assertEqual(author['documented_role'],'Pulmonology');self.assertIn(author['quote'],text)
  self.assertFalse(any(r['documented_role']=='primary_care'for r in rows))
 def test_only_explicit_pcp_label_is_pcp_source_statement(self):
  rows=D.correspondence_roles([{'record_id':'r','date':'2030-01-01','text':'PCP: Jane Doe, MD\n'}])
  self.assertEqual(rows[0]['relationship'],'explicit_pcp_label');self.assertEqual(rows[0]['current_assignment'],'dated_source_statement_only')
 def test_middle_initial_or_missing_provider_header_does_not_drop_signed_author(self):
  for header in ['Provider: Jane A. Smith, MD\n','']:
   text=header+'Sincerely,\nJane Smith, MD\nCardiology\n'
   rows=D.correspondence_roles([{'record_id':'r','date':'2030-01-01','text':text}])
   self.assertEqual(len(rows),1);self.assertEqual(rows[0]['name'],'Jane Smith, MD')
   self.assertEqual(rows[0]['current_assignment'],'not_established');self.assertIn(rows[0]['quote'],text)
 def test_doctor_initials_and_explicit_pcp_labels_keep_dated_roles(self):
  text='PCP: Dr. J. Smith\nSincerely,\nDr. A. Jones\nEndocrinology\n'
  rows=D.correspondence_roles([{'record_id':'r','date':'2030-01-01','text':text}])
  self.assertEqual({r['relationship']for r in rows},{'signed_correspondence_author','explicit_pcp_label'})
  self.assertEqual({r['name']for r in rows},{'Dr. J. Smith','Dr. A. Jones'})
 def test_practice_name_remains_unclassified_text_not_a_specialty(self):
  text='Sincerely,\nJane Smith, MD\nExample Medical Group\n'
  row=D.correspondence_roles([{'record_id':'r','date':'2030-01-01','text':text}])[0]
  self.assertIsNone(row['documented_role']);self.assertEqual(row['unclassified_signed_text'],'Example Medical Group')
  self.assertEqual(row['relationship'],'signed_correspondence_author')
 def fixture(self):
  q={'therapy_reason':'What happened to therapy?'};m=[{'patient_id':'A','document_digest':'d'}]
  docs={'A':[[{'record_id':'r','date':'2030-01-01','text':'Therapy held.'}]]}
  row={'patient_id':'A','review_json':json.dumps({'therapy_reason':[{'value':'Therapy held.','quote':'Therapy held.','date':'2030-01-01','record_id':'r'}]})}
  old={'questions':q,'manifest':m,'rows':[row]};return q,m,docs,old
 def test_reuse_needs_explicit_ownership_and_exact_questions_source(self):
  q,m,docs,old=self.fixture();self.assertIsNone(H.recover_document_observations(q,m,docs))
  with H.owned_recovery([],question='q',document_reviews=[old]):
   self.assertTrue(H.recover_document_observations(q,m,docs)['observation_reuse']['all_quotes_revalidated'])
   self.assertIsNone(H.recover_document_observations({'therapy_reason':'Different question'},m,docs))
   self.assertIsNone(H.recover_document_observations(q,[{'patient_id':'A','document_digest':'changed'}],docs))
 def test_reuse_revalidates_quote_record_and_date(self):
  for key,value in [('quote','Invented.'),('record_id','foreign'),('date','2030-02-01')]:
   q,m,docs,old=self.fixture();f=json.loads(old['rows'][0]['review_json']);f['therapy_reason'][0][key]=value;old['rows'][0]['review_json']=json.dumps(f)
   with H.owned_recovery([],question='q',document_reviews=[old]),self.assertRaises(ValueError):H.recover_document_observations(q,m,docs)
 def test_all_patient_dependencies_and_shared_scalars_preserved(self):
  rows=[{'patient_id':'A','measure':1},{'patient_id':'B','measure':2}];calls=[{'name':'query_sql','input':{'sql':'q'},'result':{'rows':rows}},{'name':'query_sql','input':{},'result':{'rows':[{'people':2}]}}]
  packets=B.support_packets('question',{'table':rows,'headline':'Two people'},calls,[],'2030-01-01')
  self.assertEqual(len(packets),3)
  by={p['id']:uncompact_json(json.loads(p['text']))for p in packets}
  self.assertEqual(by['summary']['all_final_rows'],rows)
  self.assertEqual(by['patient_A']['evidence'],[rows[0]]);self.assertEqual(by['patient_B']['evidence'],[rows[1]])
  self.assertEqual(by['patient_A']['shared_evidence'],[{'people':2}])
 def test_one_unknown_dependency_cannot_confirm_the_answer(self):
  p=[{'id':'a','date':'2030-01-01','text':'a','bytes':1},{'id':'b','date':'2030-01-01','text':'b','bytes':1}]
  def decide(*a,**kw):return {'label':'supported'if a[2][0]['id']=='a'else'unknown','decided_by':'scripted'if a[2][0]['id']=='a'else None,'steps':[]}
  out=B.check_support_packets(p,decide,as_of='2030-01-01',ledger=None);self.assertIsNone(out['decided_by']);self.assertEqual(out['label'],'unknown')
 def test_oversized_dependency_is_retained_and_never_sent_or_confirmed(self):
  p=[{'id':'a','date':'2030-01-01','text':'complete','bytes':240001}]
  with patch('clinical_core.agere.clinical.system_one.decide')as f:
   out=B.check_support_packets(p,f,as_of='2030-01-01',ledger=None)
  f.assert_not_called();self.assertEqual(out['packets'][0]['id'],'a');self.assertEqual(out['label'],'unknown')

if __name__=='__main__':unittest.main()

class IncrementalReview(unittest.TestCase):
 def test_added_question_does_not_repeat_unchanged_fields(self):
  q,m,docs,old=Composition().fixture()
  with H.owned_recovery([],question='q',document_reviews=[old]):
   got=H.recover_document_fields({**q,'new_role':'Who is the primary clinician?'},m,docs)
  self.assertEqual(got['fields'],q);self.assertEqual(set(got['rows']['A']),{'therapy_reason'})
  self.assertFalse(got['clinical_acceptance_inherited'])
 def test_changed_question_is_not_reused_by_field_name(self):
  q,m,docs,old=Composition().fixture()
  with H.owned_recovery([],question='q',document_reviews=[old]):self.assertIsNone(H.recover_document_fields({'therapy_reason':'A different clinical task'},m,docs))
 def test_only_explicit_continuation_skips_already_attempted_global_proposals(self):
  from clinical_core.agere.clinical.proposers import Cascade
  with H.owned_recovery([{'name':'query_sql','input':{'sql':'select 1'}}],question='q'):
   self.assertTrue(H.recovering('q'));self.assertFalse(H.recovering('other'))
   c=SimpleNamespace(question='q');self.assertEqual(Cascade.order(c),('backstop_agent',))
  self.assertFalse(H.recovering('q'))
 def test_cross_document_schedule_preserves_all_pairs_and_source_bindings(self):
  import os
  from clinical_core.agere.clinical import typed_document_review as T
  docs=[{'record_id':'r'+str(i),'date':'2030-01-01','text':'Observed source '+str(i)+'.'}for i in range(100)]
  q={'a':'What role is documented?','b':'What date is stated?'}
  batches=T.source_batches(docs,q,{},cross_document=True)
  self.assertEqual(sum(map(len,batches)),200);self.assertEqual(len(batches),4)
  observed=[]
  def judge(subject,questions,evidence,**kw):
   contexts={x['id']:x['text']for x in evidence}
   for value in questions.values():
    ins=value['instructions'];self.assertIn(ins['target_block'],contexts[ins['source_context_id']]);observed.append((ins['source_record_id'],ins['question']))
   return {'judgments':{k:{'label':'finding','decided_by':'scripted','provider':'scripted'}for k in questions},'steps':[],'cost_usd':0}
  with patch.dict(os.environ,{'AGERE_DOCUMENT_CROSS_BATCH':'on'}):
   result=T.read(SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False)),'A',docs,q,None,judge=judge,ranking=False)
  self.assertEqual(len(observed),200);self.assertEqual(len(set(observed)),200)
  self.assertEqual(sum(len(v)for v in result['findings'].values()),200)
  self.assertEqual(result['provider_batches'],4);self.assertEqual(len(result['coverage']),100)
  for field,values in result['findings'].items():
   for value in values:self.assertEqual(value['quote'],docs[int(value['record_id'][1:])]['text'])

class TargetedRepair(unittest.TestCase):
 def test_invalid_sibling_does_not_discard_valid_source_finding(self):
  docs=[{'record_id':'r','date':'2030-01-01','text':'Medication held.'}]
  good,errors=D.validate_fields({'findings':{'a':[{'value':'held','record_id':'r','quote':'Medication held.'}]}},{'a':'What happened?','b':'Which role?'},docs)
  self.assertEqual(set(good),{'a'});self.assertEqual(set(errors),{'b'})
 def test_foreign_record_filter_fails_before_provider_work(self):
  import sqlite3,tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory()as temp:
   db=sqlite3.connect(':memory:');db.execute('create table patient(patient_id)');db.execute("insert into patient values('A')")
   tools=SimpleNamespace(db=db,stack=SimpleNamespace(path=Path(temp),allow_live=False),as_of='2030-05-27',in_scope=lambda p:None,continuation_identity=lambda:{'source':'same'},_texts=lambda p:[('r','note','notes','office','2030-01-01','Full source.','')])
   with self.assertRaisesRegex(ValueError,'outside this received'):D.review(tools,['A'],{'x':'role'},record_ids=['foreign'])
   db.close()
 def test_native_source_bound_chunk_keeps_complete_relevant_document(self):
  import os
  from clinical_core.agere.clinical import typed_document_review as T,document_readers as R
  evidence=[{'id':'one','text':'Complete relevant source.'},{'id':'two','text':'Unrelated separate source.'}]
  seen=[]
  class Reader:
   identity='scripted-native'
   def __call__(self,state,questions):
    seen.extend(json.loads(state)['evidence']);return {'answers':{k:{'choice':'finding'}for k in questions},'stats':{}}
  q={'a':{'type':'choice','instructions':{'source_context_id':'one'},'criteria':T.OPTIONS}}
  def judge(*a,**kw):return {'judgments':{'a':{'label':'finding','decided_by':'scripted'}},'steps':[],'cost_usd':0}
  with patch.object(R,'get',return_value=Reader()),patch.dict(os.environ,{'AGERE_DOCUMENT_LOCAL_READERS':'kev,d1'}):
   got=T.local_readers_then_recheck(SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False)),q,evidence,{'judgments':{},'steps':[],'cost_usd':0},None,judge)
  self.assertEqual(seen,[evidence[0]]);self.assertEqual(got['local_steps'][0]['native_calls'][0]['source_context_ids'],['one'])

class ReuseSourceOccurrence(unittest.TestCase):
 def test_exact_later_occurrence_is_not_replaced_by_first_whitespace_match(self):
  docs=[{'record_id':'r','date':'2030-01-01','text':'Drug held.\nLater: Drug   held.'}]
  found={'x':[{'value':'Dated hold','record_id':'r','date':'2030-01-01','quote':'Drug   held.'}]}
  self.assertEqual(H.validate_saved_source_fields(found,{'x':'What happened?'},docs),found)
 def test_isolate_source_context_before_any_native_handoff(self):
  from clinical_core.agere.clinical import typed_document_review as T
  questions={'a':{'instructions':{'source_context_id':'one'}},'b':{'instructions':{'source_context_id':'two'}}}
  evidence=[{'id':'one','text':'Whole first source.'},{'id':'two','text':'Whole second source.'}];seen=[]
  def judge(subject,q,e,**kw):
   self.assertEqual(len(e),1);seen.append(e[0]['id']);return {'judgments':{k:{'label':'finding','decided_by':'scripted'}for k in q},'steps':[],'cost_usd':0}
  result=T.isolate_unresolved_sources(SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False)),questions,evidence,{'judgments':{'a':{'decided_by':None},'b':{'decided_by':None}},'steps':[],'cost_usd':0},None,judge)
  self.assertEqual(seen,['one','two']);self.assertTrue(all(v['decided_by']for v in result['judgments'].values()))
