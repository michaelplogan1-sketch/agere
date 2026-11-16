import copy, json, sqlite3, tempfile, unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import medication_interpretation as I
from test_medication_source_review import sources, ASOF


class MedicationInterpretation(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=Path(temp.name)
  self.records=sources();self.questions=[{'id':'q1','status':'unresolved','contract':I.M.Q.definition('native_medication_nonuse')},
   {'id':'q2','status':'supported','contract':I.M.Q.definition('medication_regimen_confirmation')}]
  self.patch(I.M,'source',lambda p,r:(copy.deepcopy(self.questions),copy.deepcopy(self.records),{'snapshot':{'as_of':ASOF},'received_fingerprint':digest(self.records),'question_hashes':digest(self.questions),'implementation':I.M.identity()}))
  self.review=I.M.prepare(self.path,{'portfolio_id':'portfolio','patient_id':'P-1'})
  self.contexts={}
  def create(path,patient,question,ids):
   c={'patient_id':patient,'parameters':{'question':question},'record_scope':{'record_ids':sorted(ids),'complete_chart':False,'excluded_records':len(self.records)-len(ids)},'dispatch':{'source_review_allowed':True},'omissions':[]}
   c['id']=digest(c);self.contexts[c['id']]=copy.deepcopy(c);return c
  self.patch(I.RC,'create',create);self.patch(I.RC,'inspect',lambda p,k:copy.deepcopy(self.contexts[k]))
  def verify(p,k):
   c=copy.deepcopy(self.contexts[k])
   if c['omissions'] or not c['dispatch']['source_review_allowed']:raise ValueError('Incomplete context')
   return c
  self.patch(I.RC,'verified',verify)
  self.request={'review_id':self.review['id'],'question_receipt_ids':['q1','q2'],'record_ids':['report','copy','admin']}
 def patch(self,module,name,fn):
  p=patch.object(module,name,side_effect=fn);p.start();self.addCleanup(p.stop)
 def history(self):return I.listing(self.path,{'review_id':self.review['id']})
 def answer(self,selection):
  self.result={'id':'a'*64,'context_id':selection['context_id'],'patient_id':'P-1','status':'complete','passes':[{'pass_number':1}],
   'findings':[{'citations':[{}]}],'review_steps':[],'question':selection['question'],'source_scope':['medication_reconciliation'],
   'record_scope':selection['record_scope'],'coverage':{},'reconciliation':{'groups':[]}}
  self.needs={'id':'n'*64,'summary':{'proposed_questions':0}}
  self.patch(I.S,'inspect',lambda p,k:copy.deepcopy(self.result))
  self.patch(I.E,'prepare',lambda *a:copy.deepcopy(self.needs));self.patch(I.E,'inspect',lambda *a:copy.deepcopy(self.needs))
  return {'selection_id':selection['id'],'answer_id':self.result['id']}
 def test_exact_questions_sources_and_narrative_omissions(self):
  s=I.select(self.path,self.request)
  self.assertEqual([q['status'] for q in s['questions']],['unresolved','supported'])
  self.assertEqual(s['unselected_narrative_record_ids'],['note'])
  self.assertEqual({x['target_record_id'] for x in s['selected_source_links_outside_scope']},{'fill','order'})
  self.assertEqual(s['questions'][0]['binding_coverage'],self.review['questions'][0]['binding_coverage'])
  self.assertFalse(s['original_questions_changed']);self.assertFalse(s['current_regimen_established'])
 def test_selection_order_is_canonical_and_concurrent_idempotent(self):
  with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:I.select(self.path,self.request),range(2)))
  self.assertEqual(rows[0],rows[1]);reverse={**self.request,'record_ids':list(reversed(self.request['record_ids'])),'question_receipt_ids':['q2','q1']}
  self.assertEqual(rows[0],I.select(self.path,reverse));self.assertEqual(len(self.history()['selections']),1)
 def test_unknown_duplicate_empty_and_overbound_scopes_rejected(self):
  for changes in [{'record_ids':[]},{'record_ids':['other']},{'record_ids':['report','report']},{'record_ids':[str(i) for i in range(25)]},
                  {'question_receipt_ids':[]},{'question_receipt_ids':['q1','q1']},{'question_receipt_ids':['other']},{'clinical_execution_authorized':True}]:
   with self.subTest(changes=changes),self.assertRaises(ValueError):I.select(self.path,{**self.request,**changes})
  self.assertFalse(self.history()['selections'])
 def test_changed_question_or_source_preserves_stale_history(self):
  s=I.select(self.path,self.request);self.questions[0]['status']='unsupported'
  self.assertFalse(self.history()['selections'][0]['current'])
  with self.assertRaises(ValueError):I.inspect_selection(self.path,s['id'])
  self.assertEqual(self.history()['selections'][0]['questions'][0]['status'],'unresolved')
 def test_context_mutation_stale(self):
  s=I.select(self.path,self.request);self.contexts[s['context_id']]['extra']='changed'
  self.assertFalse(self.history()['selections'][0]['current'])
 def test_source_change_during_selection_does_not_commit(self):
  create=I.RC.create
  def changing(*args):
   c=create(*args);self.records[0]['dose']='changed';return c
  with patch.object(I.RC,'create',side_effect=changing),self.assertRaises(ValueError):I.select(self.path,self.request)
  db=I.database(self.path);self.assertEqual(db.execute('SELECT count(*) FROM selections').fetchone()[0],0);db.close()
 def test_incomplete_context_visible_but_cannot_attach(self):
  create=I.RC.create
  def incomplete(*args):
   c=create(*args);c['omissions']=['unit not packed'];self.contexts[c['id']]=copy.deepcopy(c);return c
  with patch.object(I.RC,'create',side_effect=incomplete):s=I.select(self.path,self.request)
  self.assertEqual(s['status'],'selected_scope_incomplete')
  with self.assertRaises(ValueError):I.attach(self.path,self.answer(s))
 def test_answer_returns_to_exact_review_and_questions_without_resolution(self):
  s=I.select(self.path,self.request);req=self.answer(s);a=I.attach(self.path,req)
  self.assertEqual(a,I.attach(self.path,req));self.assertEqual(a['review_id'],self.review['id']);self.assertEqual(a['question_receipt_ids'],['q1','q2'])
  self.assertFalse(a['human_review_established']);self.assertFalse(a['original_questions_changed']);self.assertTrue(self.history()['answers'][0]['current'])
 def test_wrong_answer_patient_context_status_and_reconciliation_rejected(self):
  s=I.select(self.path,self.request);req=self.answer(s)
  for key,value in [('patient_id','other'),('context_id','other'),('status','partial'),('passes',[{},{}])]:
   old=self.result[key];self.result[key]=value
   with self.assertRaises(ValueError):I.attach(self.path,req)
   self.result[key]=old
  with self.assertRaises(ValueError):I.attach(self.path,{**req,'original_questions_changed':True})
  self.assertFalse(self.history()['answers'])
 def test_answer_needs_change_or_race_is_detected(self):
  s=I.select(self.path,self.request);req=self.answer(s);I.attach(self.path,req);self.needs['summary']['proposed_questions']=1
  self.assertFalse(self.history()['answers'][0]['current'])
  def changing(*args):
   self.result['findings'].append({'citations':[]});return copy.deepcopy(self.needs)
  with patch.object(I.E,'prepare',side_effect=changing),self.assertRaisesRegex(ValueError,'during attachment'):I.attach(self.path,req)
  self.assertEqual(len(self.history()['answers']),1)
 def test_receipts_immutable_and_tamper_detected(self):
  s=I.select(self.path,self.request);I.attach(self.path,self.answer(s));db=I.database(self.path)
  try:
   for table in ('selections','answers'):
    with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM '+table)
  finally:db.close()
  bad=copy.deepcopy(s);bad['patient_id']='other'
  with self.assertRaises(ValueError):I.verified(bad)
 def task(self):
  self.task_value={'id':'task','revision':2,'patient_id':'P-1','plan_id':'plan'}
  self.plan={'id':'plan','patient_id':'P-1','origin':{'patient_id':'P-1','kind':I.M.KIND,'id':self.review['id'],'hash':digest(self.review)}}
  self.patch(I.R,'task',lambda *a:copy.deepcopy(self.task_value));self.patch(I.C,'inspect_plan',lambda *a:copy.deepcopy(self.plan))
 def test_existing_task_exposes_additive_answer_without_changing_revision(self):
  s=I.select(self.path,self.request);req=self.answer(s);I.attach(self.path,req);self.task()
  state=I.task_state(self.path,{'task_id':'task'});self.assertEqual(state['task_revision'],2)
  out=I.task_availability(self.path,'task');self.assertEqual(len(out['answers']),1);self.assertEqual(out['answers'][0]['question_receipt_ids'],['q1','q2'])
  self.assertEqual(self.task_value['revision'],2);self.assertFalse(out['clinical_execution_authorized'])
 def test_task_origin_patient_and_race_rejected(self):
  self.task();self.plan['patient_id']='other'
  with self.assertRaises(ValueError):I.task_state(self.path,{'task_id':'task'})
  self.plan['patient_id']='P-1';self.plan['origin']['kind']='answer'
  with self.assertRaises(ValueError):I.task_state(self.path,{'task_id':'task'})
  self.plan['origin']['kind']=I.M.KIND;original=I.listing
  def changed(*args):
   state=original(*args);self.task_value['revision']+=1;return state
  with patch.object(I,'listing',side_effect=changed),self.assertRaisesRegex(ValueError,'task changed'):I.task_state(self.path,{'task_id':'task'})
 def test_nonmedication_coverage_delegates_exactly(self):
  expected={'schema':'old','answers':[{'answer_id':'old'}]}
  with patch.object(I.old_view,'prepare',return_value=expected):self.assertEqual(I.task_availability(self.path,'other'),expected)
 def test_long_question_not_silently_truncated(self):
  self.review['questions'][0]['purpose']='Very long purpose. '*200
  with patch.object(I.M,'inspect',return_value=self.review),self.assertRaisesRegex(ValueError,'fewer questions'):I.select(self.path,self.request)

if __name__=='__main__':unittest.main()
