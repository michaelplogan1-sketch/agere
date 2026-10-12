"""Transport and recovery invariants, independent of model correctness."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.gateway import BudgetLedger
from clinical_core.agere.clinical import case_passes as cp, case_synthesis as cs, context_builder as ctx

class CasePasses(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
  self.records=[{'record_id':f'r{i}','patient_id':'p','source_table':'notes','event_date':'2026-01-01','available_date':'2026-01-02','synthetic':True,'identity_status':'matched','text':f'Record {i} documents an unconfirmed request. '+('Received text and qualifications. '*35)} for i in range(16)]
  self.params={'question':'What do requests establish?','streams':['notes'],'concepts':[],'contract':'','program_ids':[],'max_bytes':60000}
  self.row=ctx.pack('p',self.params,ctx.units('p',self.records,[],self.params,'2026-03-01'),{'snapshot':{'as_of':'2026-03-01'}})
  self.row.update(id='a'*64,identity='source-v1',parameters=self.params)
  self.db=sqlite3.connect(self.path/'stack.sqlite3');self.addCleanup(self.db.close);self.db.execute('CREATE TABLE records(id TEXT,patient TEXT,payload TEXT)')
  for r in self.records:self.db.execute('INSERT INTO records VALUES (?,?,?)',(r['record_id'],'p',json.dumps(r)))
  self.db.commit()
  p=patch.object(cs.context,'inspect',return_value=self.row);p.start();self.addCleanup(p.stop)
  self.ledger=BudgetLedger(self.path/'ledger.sqlite3');self.addCleanup(self.ledger.close)
 def raw(self,body,value):return {'model':body['model'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(value)}]}
 def answer(self,body):
  wire=json.loads(body['messages'][0]['content'])
  if wire['stage']=='source_pass':
   u=next(u for u in wire['units'] if u['kind']=='narrative')
   result={'findings':[{'id':'f1','kind':'documented_account','text':'The source reports a request; completion is unconfirmed.','citations':[{'unit':u['id'],'quote':u['text'].split('. ')[0]+'.'}],'unresolved':['Completion is unknown.']}], 'unanswered':['Was the requested care completed?'],'review_steps':[{'kind':'obtain_missing_evidence','finding_ids':['f1'],'question':'Which source establishes completion?'}]}
   result['unit_reviews']=[{'unit':v['id'],'status':'discussed' if v['id']==u['id'] else 'unresolved','reason':'Cited.' if v['id']==u['id'] else 'Meaning remains unresolved.'} for v in wire['units']]
  else:
   table=wire['original_source_excerpts'];e=dict(zip(table['columns'],table['rows'][0]))
   result={'findings':[{'id':'f1','kind':'hypothesis','text':'The cited request does not establish completion.','citations':[{'unit':e['unit'],'quote':e['excerpts'][0]}],'unresolved':['Completion needs another source.']}], 'unanswered':['Broader raw context was not revisited.'],'review_steps':[]}
  return self.raw(body,result)
 def sender(self,body,provider,ledger,out,*,parse):
  key=digest({'contract':'context-student/1','provider':provider,'body':body})
  old=ledger.db.execute('SELECT response FROM calls WHERE id=? AND status=?',(key,'validated')).fetchone()
  if old:return parse(json.loads(old[0])),{'request_hash':key,'cache_hit':True}
  ledger.reserve(key,provider,body['model']);raw=self.answer(body);result=parse(raw);ledger.finish(key,response=raw)
  return result,{'request_hash':key,'cache_hit':False,'usage':{'input_tokens':600,'output_tokens':200}}
 def run_synthesis(self,send=None):
  with patch.object(cs,'send',side_effect=send or self.sender),patch.object(cs,'token_check',return_value={'input_tokens':600,'provider_count_cached':False}):
   return cp.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
 def test_every_raw_unit_once_and_actual_transport_bounded(self):
  with self.assertRaisesRegex(ValueError,'gateway bound'):cs.request(self.row)
  bundle,links=cp.plan(self.row,cp.dependencies(self.path,self.row))
  ids=[u['id'] for p in bundle['passes'] for u in p['evidence']]
  self.assertEqual(sorted(ids),sorted(u['id'] for u in self.row['model_context']['evidence']));self.assertEqual(len(ids),len(set(ids)))
  for p in bundle['passes']:
   body,units=cp.pass_request(p,links);self.assertLessEqual(cp.request_bytes(body),20000)
   self.assertTrue(all(u['text'] for u in units.values()))
 def test_whole_unit_overflow_and_call_cap_block_without_dispatch(self):
  links=cp.dependencies(self.path,self.row)
  with self.assertRaisesRegex(ValueError,'cap'):cp.plan(self.row,links,1)
  r=copy.deepcopy(self.row);next(u for u in r['model_context']['evidence'] if u['kind']=='narrative')['text']='é'*20000
  with self.assertRaisesRegex(ValueError,'indivisible'):cp.plan(r,links)
 def test_success_replay_retains_all_findings_units_and_review_references(self):
  a=self.run_synthesis();b=cp.synthesize(self.path,self.row['id'],self.ledger)
  self.assertTrue(b['cache_hit']);self.assertEqual(b['new_paid_api_calls'],0);self.assertEqual(a['id'],b['id'])
  self.assertEqual(a['coverage']['submitted_units'],len(self.row['model_context']['evidence']))
  self.assertEqual(len(a['findings']),len(a['passes'])+1)
  self.assertTrue(a['coverage']['unassessed_units']);self.assertFalse(a['coverage']['interpretation_accuracy_assessed'])
  ids={f['id'] for f in a['findings']}
  self.assertTrue(all(set(s['finding_ids'])<=ids and not s['execution_authorized'] for s in a['review_steps']))
 def test_duplicate_coverage_and_uncited_discussed_units_rejected(self):
  bundle,links=cp.plan(self.row,cp.dependencies(self.path,self.row));body,units=cp.pass_request(bundle['passes'][0],links)
  raw=self.answer(body);v=json.loads(raw['content'][0]['text']);v['unit_reviews'][-1]=v['unit_reviews'][0]
  with self.assertRaisesRegex(ValueError,'duplicate'):cp.parse_pass(self.raw(body,v),body,units,self.row)
  v=json.loads(raw['content'][0]['text']);next(r for r in v['unit_reviews'] if r['status']=='unresolved')['status']='discussed'
  with self.assertRaisesRegex(ValueError,'must be cited'):cp.parse_pass(self.raw(body,v),body,units,self.row)
 def test_budget_and_disabled_live_preflight_no_calls(self):
  with patch.object(cs,'send') as send,patch.object(cs,'token_check') as tokens:
   with self.assertRaisesRegex(ValueError,'disabled'):cp.synthesize(self.path,self.row['id'],self.ledger)
   with patch.object(self.ledger,'receipt',return_value={'remaining_reservation_usd':.5}):
    with self.assertRaisesRegex(ValueError,'authorization'):cp.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
   send.assert_not_called();tokens.assert_not_called()
 def test_added_and_deleted_sources_invalidate_answer(self):
  a=self.run_synthesis();self.db.execute("DELETE FROM records WHERE id='r0'");self.db.commit()
  with self.assertRaisesRegex(ValueError,'changed'):cp.inspect(self.path,a['id'])
 def test_outside_scope_dependency_changes_invalidate(self):
  a=self.run_synthesis();r={**self.records[0],'record_id':'outside','source_table':'labs'}
  self.db.execute('INSERT INTO records VALUES (?,?,?)',('outside','p',json.dumps(r)));self.db.commit()
  with self.assertRaisesRegex(ValueError,'dependency'):cp.inspect(self.path,a['id'])
 def test_source_race_preserves_only_progress_and_provider_receipts(self):
  def mutate(*args,**kwargs):
   result=self.sender(*args,**kwargs);self.db.execute("DELETE FROM records WHERE id='r0'");self.db.commit();return result
  with self.assertRaisesRegex(ValueError,'changed'):self.run_synthesis(mutate)
  files=list((self.path/'case-passes').glob('*-progress.json'));self.assertEqual(len(files),1)
  r=json.loads(files[0].read_text());self.assertEqual(r['status'],'incomplete');self.assertFalse(r['reconciliation_assessed'])
  self.assertEqual(self.ledger.receipt()['calls']['validated'],1)
 def test_reconciliation_overflow_retains_resumable_passes(self):
  with patch.object(cp,'reconciliation_request',side_effect=ValueError('reconciliation request bound')):
   with self.assertRaisesRegex(ValueError,'reconciliation'):self.run_synthesis()
  before=self.ledger.receipt()['calls']['validated'];a=self.run_synthesis()
  self.assertEqual(a['new_paid_api_calls'],1);self.assertEqual(self.ledger.receipt()['calls']['validated'],before+1)
 def test_failed_attempt_cannot_silently_retry(self):
  def fail(body,provider,ledger,out,*,parse):
   key=digest({'contract':'context-student/1','provider':provider,'body':body});ledger.reserve(key,provider,body['model']);ledger.finish(key,error='fixture failure');raise RuntimeError('fixture failure')
  with self.assertRaises(RuntimeError):self.run_synthesis(fail)
  with patch.object(cs,'send') as send:
   with self.assertRaisesRegex(ValueError,'no silent paid retry'):cp.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
   send.assert_not_called()
 def test_cross_pass_edges_are_retained_without_interpreting_them(self):
  links=cp.dependencies(self.path,self.row);links['edges']=[{'from':'r0','to':'r9','field':'request_record_id','scope':'in_scope'}]
  bundle,links=cp.plan(self.row,links);self.assertEqual(links['cross_pass_edges'],links['edges'])
 def test_source_finding_and_review_dependencies_form_indivisible_components(self):
  links={'edges':[{'from':'r0','to':'r1'}]}
  passes=[{'result':{'findings':[
   {'id':'f1','citations':[{'source':{'record_id':'r1'}},{'source':{'record_id':'r2'}}]},
   {'id':'f2','citations':[{'source':{'record_id':'r3'}}]}],
   'review_steps':[{'finding_ids':['f1','f2']}]}}]
  groups=cp.source_components(self.row,links,passes)
  self.assertIn({'r0','r1','r2','r3'},groups)
  self.assertEqual(set().union(*groups),set(self.row['model_context']['sources']))
  self.assertEqual(sum(map(len,groups)),len(self.records))
 def test_reconciliation_packing_retains_findings_and_blocks_indivisible_overflow(self):
  bundle,links=cp.plan(self.row,cp.dependencies(self.path,self.row));passes=[]
  for p in bundle['passes']:
   body,units=cp.pass_request(p,links);r=cp.parse_pass(self.answer(body),body,units,self.row)
   passes.append({'pass_number':p['pass_number'],'result':cp._rename(r,'p'+str(p['pass_number'])+'_')})
  original=cp.reconciliation_request
  def limited(row,passes,links):
   if len(row['model_context']['sources'])>4:raise ValueError('reconciliation request bound')
   return original(row,passes,links)
  with patch.object(cp,'reconciliation_request',side_effect=limited):
   jobs=cp.reconciliation_jobs(self.row,passes,links)
   self.assertEqual(len(jobs),4)
   self.assertEqual(sorted(f for j in jobs for f in j['finding_ids']),sorted(f['id'] for p in passes for f in p['result']['findings']))
   linked={**links,'edges':[{'from':'r0','to':f'r{i}'} for i in range(1,5)]}
   with self.assertRaisesRegex(ValueError,'bound'):cp.reconciliation_jobs(self.row,passes,linked)
 def test_reconciler_cannot_quote_unsent_raw_text(self):
  bundle,links=cp.plan(self.row,cp.dependencies(self.path,self.row));passes=[]
  for p in bundle['passes']:
   body,units=cp.pass_request(p,links);r=cp.parse_pass(self.answer(body),body,units,self.row)
   passes.append({'pass_number':p['pass_number'],'result':cp._rename(r,'p'+str(p['pass_number'])+'_')})
  body,units,excerpts=cp.reconciliation_request(self.row,passes,links);raw=self.answer(body);v=json.loads(raw['content'][0]['text']);c=v['findings'][0]['citations'][0]
  c['quote']=units[c['unit']]['text'][:150]
  with self.assertRaisesRegex(ValueError,'not supplied'):cp.parse_reconciliation(self.raw(body,v),body,units,self.row,excerpts)

if __name__=='__main__':unittest.main()
