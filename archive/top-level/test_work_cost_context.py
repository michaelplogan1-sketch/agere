import copy,sqlite3,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_work_cost as fixture
from clinical_core.agere.clinical import work_cost_context as c
from clinical_core.contracts import digest

class CostContext(unittest.TestCase):
 prepare=fixture.CostWork.prepare
 queue=fixture.CostWork.queue
 update=fixture.CostWork.update
 event=fixture.CostWork.event
 def setUp(self):
  fixture.CostWork.setUp(self);self.cost=c.C.compare(self.path,self.cost_request);self.ids=c.population(self.path,self.cost)
  self.prep={'base':{'parameters':{'available_by':'2026-01-01'},'source_digest':'source1'},'patients':{pid:'records-'+pid for pid in self.ids}}
  self.reports={pid:{'id':c.F.receipt_id(self.prep,pid),'patient_id':pid,'available_by':'2026-01-01','base':copy.deepcopy(self.prep['base']),'accounts':[{'account_id':'a-'+pid,'currency':'USD','snapshot_amounts':{'balance_cents':100},'status':'review_required'}],'unassigned_sources':[{'record_id':'u-'+pid}],'status_counts':{'review_required':1}} for pid in self.ids}
  self.executions=[]
  def prep(path,p):
   if p!={'available_by':'2026-01-01'}:raise ValueError('Invalid cutoff')
   return copy.deepcopy(self.prep)
  def read(path,key):return copy.deepcopy(next(r for r in self.reports.values() if r['id']==key))
  def execute(path,ids,p,**kw):self.executions.append(list(ids));return {}
  for name,fun in [('prepare',prep),('inspect',read),('execute',execute)]:
   p=patch.object(c.F,name,side_effect=fun);p.start();self.addCleanup(p.stop)
  self.selection={'cost_id':self.cost['id'],'available_by':'2026-01-01'}
 def save(self):
  row=c.preview(self.path,self.selection);return c.prepare(self.path,{**self.selection,'preview_id':row['id']})
 def test_exact_task_population_and_received_reports_without_netting(self):
  row=self.save();self.assertEqual(row['patient_ids'],self.ids);self.assertEqual(row['summary'],{'patients':2,'accounts':2,'account_status_counts':{'review_required':2},'unassigned_sources':2})
  self.assertTrue(all(ids==self.ids for ids in self.executions));self.assertEqual(row['question'],self.cost['question']);self.assertFalse(row['program_account_attribution_established']);self.assertFalse(row['program_revenue_established']);self.assertFalse(row['amounts_netted_against_cost'])
  for item in row['financial_reports']:self.assertEqual(item['report'],self.reports[item['patient_id']]);self.assertEqual(item['receipt_hash'],digest(item['report']))
 def test_extra_patient_cutoff_and_authority_are_rejected(self):
  for change in ({'patient_ids':['foreign']},{'available_by':'bad'},{'verified_contract':True}):
   with self.assertRaises(ValueError):c.preview(self.path,{**self.selection,**change})
 def test_changed_source_preview_requires_new_preview(self):
  row=c.preview(self.path,self.selection);self.reports[self.ids[0]]['accounts'][0]['snapshot_amounts']['balance_cents']=300
  with self.assertRaisesRegex(ValueError,'preview'):c.prepare(self.path,{**self.selection,'preview_id':row['id']})
  self.assertEqual(c.history(self.path,self.cost['id'])['contexts'],[])
 def test_changed_received_report_preserves_historical_attachment(self):
  row=self.save();self.reports[self.ids[0]]['accounts'][0]['snapshot_amounts']['balance_cents']=200
  old=c.history(self.path,self.cost['id'])['contexts'][0];self.assertFalse(old['current']);self.assertEqual(old['financial_reports'],row['financial_reports'])
 def test_parent_change_preserves_history(self):
  row=self.save();self.event(self.first,action='hold');old=c.history(self.path,self.cost['id'])['contexts'][0];self.assertFalse(old['current']);self.assertEqual(old['id'],row['id'])
 def test_wrong_patient_or_cutoff_report_cannot_attach(self):
  for field,value in [('patient_id','foreign'),('available_by','2025-01-01'),('base',{})]:
   old=copy.deepcopy(self.reports[self.ids[0]]);self.reports[self.ids[0]][field]=value
   with self.assertRaisesRegex(ValueError,'scope'):c.preview(self.path,self.selection)
   self.reports[self.ids[0]]=old
 def test_missing_source_population_mapping_cannot_partial_attach(self):
  self.prep['patients'].pop(self.ids[0])
  with self.assertRaisesRegex(ValueError,'mapping'):c.preview(self.path,self.selection)
 def test_source_fingerprint_race_cannot_attach(self):
  original=c.F.prepare;calls=[]
  def raced(*a):
   row=original(*a);calls.append(1)
   if len(calls)>1:row['patients'][self.ids[0]]='changed'
   return row
  with patch.object(c.F,'prepare',side_effect=raced):
   with self.assertRaisesRegex(ValueError,'sources changed'):c.preview(self.path,self.selection)
 def test_concurrent_replay_and_immutable_context(self):
  row=c.preview(self.path,self.selection);req={**self.selection,'preview_id':row['id']}
  with ThreadPoolExecutor(max_workers=2) as ex:rows=list(ex.map(lambda _:c.prepare(self.path,req),range(2)))
  self.assertEqual(rows[0],rows[1]);self.assertEqual(len(c.history(self.path,self.cost['id'])['contexts']),1)
  db=c.database(self.path)
  with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM contexts')
  db.close()
 def test_no_accounts_remains_no_received_accounts_not_zero_revenue(self):
  for r in self.reports.values():r.update(accounts=[],status_counts={})
  row=self.save();self.assertEqual(row['summary']['accounts'],0);self.assertFalse(row['program_revenue_established']);self.assertFalse(row['amounts_netted_against_cost'])
 def test_currency_and_unresolved_amounts_are_preserved_without_aggregation(self):
  self.reports[self.ids[0]]['accounts'][0].update(currency=None,snapshot_amounts=None);self.reports[self.ids[1]]['accounts'][0]['currency']='EUR'
  row=self.save();self.assertIsNone(row['financial_reports'][0]['report']['accounts'][0]['currency']);self.assertNotIn('revenue',row);self.assertNotIn('profit',row)
 def test_original_cost_and_task_history_unchanged(self):
  before=c.C.parent(self.path,'dependency',self.dep['id']);self.save();self.assertEqual(c.parent(self.path,self.cost['id']),self.cost);self.assertEqual(c.C.parent(self.path,'dependency',self.dep['id']),before)
 def test_corrupt_saved_context_rejected(self):
  self.save();db=c.database(self.path);db.execute('DROP TRIGGER contexts_UPDATE');db.execute("UPDATE contexts SET payload=replace(payload,'review_required','verified')");db.commit();db.close()
  with self.assertRaisesRegex(ValueError,'integrity'):c.history(self.path,self.cost['id'])

if __name__=='__main__':unittest.main()
