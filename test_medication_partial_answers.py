import copy,sqlite3,unittest
from unittest.mock import patch
import test_medication_interpretation as fixture
from clinical_core.agere.clinical import medication_partial_answers as P

class PartialAnswers(unittest.TestCase):
 patch=fixture.MedicationInterpretation.patch
 def setUp(self):
  fixture.MedicationInterpretation.setUp(self)
  self.s=P.I.select(self.path,self.request);self.req={'selection_id':self.s['id']}
  self.a={'id':'a'*64,'context_id':self.s['context_id'],'patient_id':'P-1','status':'complete','passes':[{},{}],
    'findings':[{'citations':[{}]}],'review_steps':[],'question':self.s['question'],'source_scope':['notes'],'record_scope':self.s['record_scope'],
    'coverage':{'submitted_units':3,'model_accounted_units':1,'system_unassessed_units':2},'quarantined_source_passes':[{}]}
  self.patch(P.recovery,'install',lambda:None);self.patch(P.U,'prepare',lambda *args:(None,None,None,None,self.a['id']))
  self.patch(P.U,'inspect',lambda *args:copy.deepcopy(self.a))
  self.patch(P.C,'preview',lambda *args:(_ for _ in ()).throw(ValueError('All pass findings exceed the reconciliation request bound')))
 def test_partial_is_idempotent_separate_and_cannot_pass_completed_attachment(self):
  r=P.attach(self.path,self.req);self.assertEqual(r,P.attach(self.path,self.req));self.assertFalse(r['reconciliation_complete']);self.assertFalse(r['original_questions_changed'])
  self.assertEqual(r['summary']['system_unassessed_units'],2);self.assertEqual(P.I.listing(self.path,{'review_id':self.review['id']})['answers'],[])
  self.patch(P.I.S,'inspect',lambda *args:copy.deepcopy(self.a))
  with self.assertRaisesRegex(ValueError,'reconciliation'):P.I.attach(self.path,{'selection_id':self.s['id'],'answer_id':self.a['id']})
 def test_immutable_and_stale_history(self):
  r=P.attach(self.path,self.req);db=P.database(self.path)
  with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM partials')
  db.close();self.a['findings'].append({'citations':[]});history=P.listing(self.path,{'review_id':self.review['id']})
  self.assertFalse(history['partials'][0]['current']);self.assertEqual(history['partials'][0]['id'],r['id'])
 def test_mismatched_patient_or_context_rejected(self):
  for key,value in [('patient_id','other'),('context_id','other')]:
   old=self.a[key];self.a[key]=value
   with self.assertRaises(ValueError):P.attach(self.path,self.req)
   self.a[key]=old
 def test_available_reconciliation_or_unknown_failure_not_bypassed(self):
  with patch.object(P.C,'preview',return_value={}):
   with self.assertRaisesRegex(ValueError,'completed interpretation'):P.attach(self.path,self.req)
  with patch.object(P.C,'preview',side_effect=ValueError('Unknown failure')):
   with self.assertRaisesRegex(ValueError,'Unknown failure'):P.attach(self.path,self.req)
 def test_changes_during_attachment_do_not_commit(self):
  original=P.binding;calls=0
  def changed(*args):
   nonlocal calls
   calls+=1
   if calls==2:self.a['findings'].append({'citations':[]})
   return original(*args)
  with patch.object(P,'binding',side_effect=changed),self.assertRaisesRegex(ValueError,'during attachment'):P.attach(self.path,self.req)
  self.assertEqual(P.listing(self.path,{'review_id':self.review['id']})['partials'],[])
 def test_other_task_view_is_preserved(self):
  expected={'schema':'older','answers':[{'answer_id':'previous'}]}
  with patch.object(P.I,'task_availability',return_value=expected):self.assertEqual(P.task_availability(self.path,'other'),expected)

if __name__=='__main__':unittest.main()
