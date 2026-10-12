import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import case_synthesis as cs, context_builder as ctx
from clinical_core.agere.gateway import BudgetLedger

class CaseSynthesis(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)
  self.records=[{'record_id':'a','patient_id':'p','source_table':'notes','event_date':'2026-01-01','available_date':'2026-01-02','synthetic':True,'identity_status':'matched','text':'Patient reports €10 was unaffordable.'},
   {'record_id':'b','patient_id':'p','source_table':'notes','event_date':'2026-02-01','available_date':'2026-02-02','synthetic':True,'identity_status':'matched','text':'Medication obtained; actual use remains unconfirmed.'}]
  self.params={'question':'What is known about access and use?','streams':['notes'],'concepts':[],'contract':'','program_ids':[],'max_bytes':16000}
  self.row=ctx.pack('p',self.params,ctx.units('p',self.records,[],self.params,'2026-03-01'),{'snapshot':{'as_of':'2026-03-01'}})
  self.row.update(id='a'*64,identity='source-v1',parameters=self.params)
  self.db=sqlite3.connect(self.path/'stack.sqlite3');self.addCleanup(self.db.close);self.db.execute('CREATE TABLE records(id TEXT,patient TEXT,payload TEXT)')
  for r in self.records:self.db.execute('INSERT INTO records VALUES (?,?,?)',(r['record_id'],'p',json.dumps(r)))
  self.db.commit()
  self.inspect_patch=patch.object(cs.context,'inspect',return_value=self.row);self.inspect_patch.start();self.addCleanup(self.inspect_patch.stop)
  self.body,self.units=cs.request(self.row)
  self.a=next(k for k,v in self.units.items() if v['text']==self.records[0]['text'])
  self.b=next(k for k,v in self.units.items() if v['text']==self.records[1]['text'])
  self.answer={'findings':[{'id':'f1','kind':'documented_account','text':'The sources document a reported cost barrier and later receipt, with actual use unresolved.',
   'citations':[{'unit':self.a,'quote':self.records[0]['text']},{'unit':self.b,'quote':self.records[1]['text']}],'unresolved':['Current use is not established.']}],
   'unanswered':['Whether medication is currently taken.'],'review_steps':[{'kind':'obtain_missing_evidence','finding_ids':['f1'],'question':'What source confirms actual current use?'}]}
  self.ledger=BudgetLedger(self.path/'ledger.sqlite3');self.addCleanup(self.ledger.close)
 def raw(self,answer=None):return {'model':self.body['model'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(answer or self.answer)}]}
 def parse(self,answer=None):return cs.parse(self.raw(answer),self.body,self.units,self.row)
 def tokens(self):return {'input_tokens':500,'provider_count_cached':False}
 def sender(self,body,provider,ledger,out,*,parse):return parse(self.raw()),{'cache_hit':False,'request_hash':'fixture','usage':{'input_tokens':500,'output_tokens':150}}
 def test_exact_unicode_citations_and_pending_authority(self):
  x=self.parse();f=x['findings'][0];self.assertEqual(f['semantic_validation'],'pending')
  for c in f['citations']:
   record=next(r for r in self.records if r['record_id']==c['source']['record_id'])
   self.assertEqual(record['text'][c['start']:c['end']],c['quote']);self.assertTrue(c['citation_verified'])
 def test_unknown_invented_and_ambiguous_citations_rejected(self):
  for ref in [{'unit':'u999','quote':'abc'},{'unit':self.a,'quote':'Invented diagnosis'}]:
   a=copy.deepcopy(self.answer);a['findings'][0]['citations']=[ref]
   with self.assertRaises(ValueError):self.parse(a)
  units=copy.deepcopy(self.units);units[self.a]['text']='same same';a=copy.deepcopy(self.answer);a['findings'][0]['citations']=[{'unit':self.a,'quote':'same'}]
  with self.assertRaisesRegex(ValueError,'ambiguous'):cs.parse(self.raw(a),self.body,units,self.row)
 def test_conflicts_need_distinct_units_and_hypotheses_need_unknowns(self):
  for kind in ['conflicting_accounts','hypothesis']:
   a=copy.deepcopy(self.answer);f=a['findings'][0];f['kind']=kind;f['citations']=f['citations'][:1];f['unresolved']=[]
   with self.assertRaises(ValueError):self.parse(a)
 def test_model_revision_truncation_and_bad_review_references_rejected(self):
  for field,value in [('model','other'),('stop_reason','max_tokens')]:
   raw=self.raw();raw[field]=value
   with self.assertRaises(ValueError):cs.parse(raw,self.body,self.units,self.row)
  a=copy.deepcopy(self.answer);a['review_steps'][0]['finding_ids']=['f8']
  with self.assertRaisesRegex(ValueError,'unknown'):self.parse(a)
 def test_incomplete_scope_blocks_before_calls(self):
  self.row['dispatch']['source_review_allowed']=False
  with patch.object(cs,'send') as send,patch.object(cs,'token_check') as tokens:
   with self.assertRaisesRegex(ValueError,'Complete declared'):cs.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
   send.assert_not_called();tokens.assert_not_called()
 def test_new_updated_deleted_or_wrong_identity_sources_invalidate(self):
  cs.verified_context(self.path,self.row['id'])
  self.db.execute('INSERT INTO records VALUES (?,?,?)',('c','p',json.dumps({**self.records[0],'record_id':'c'})));self.db.commit()
  with self.assertRaisesRegex(ValueError,'changed'):cs.verified_context(self.path,self.row['id'])
  self.db.execute("DELETE FROM records WHERE id='c'");self.db.commit()
  self.db.execute("UPDATE records SET payload=? WHERE id='a'",(json.dumps({**self.records[0],'identity_status':'unknown'}),));self.db.commit()
  with self.assertRaisesRegex(ValueError,'matched'):cs.verified_context(self.path,self.row['id'])
  self.db.execute("DELETE FROM records WHERE id='a'");self.db.commit()
  with self.assertRaisesRegex(ValueError,'changed'):cs.verified_context(self.path,self.row['id'])
 def test_success_replay_is_offline_and_review_steps_are_proposals(self):
  with patch.object(cs,'send',side_effect=self.sender) as send,patch.object(cs,'token_check',return_value=self.tokens()) as tokens:
   x=cs.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
   y=cs.synthesize(self.path,self.row['id'],self.ledger,allow_live=False)
   self.assertEqual(x['id'],y['id']);self.assertEqual(y['new_paid_api_calls'],0);self.assertTrue(y['cache_hit']);self.assertEqual(send.call_count,1);self.assertEqual(tokens.call_count,1)
   self.assertFalse(x['clinical_state_established']);self.assertFalse(x['review_steps'][0]['execution_authorized']);self.assertEqual(x['review_steps'][0]['status'],'proposed')
 def test_source_race_keeps_paid_receipt_out_of_current_answer(self):
  def mutate(*args,**kwargs):
   result=self.sender(*args,**kwargs);self.db.execute("DELETE FROM records WHERE id='b'");self.db.commit();return result
  with patch.object(cs,'send',side_effect=mutate),patch.object(cs,'token_check',return_value=self.tokens()):
   with self.assertRaisesRegex(ValueError,'changed'):cs.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
  self.assertEqual(list((self.path/'case-synthesis').glob('*.json')),[])
 def test_request_size_never_silently_truncates(self):
  row=copy.deepcopy(self.row)
  next(u for u in row['model_context']['evidence'] if u['kind']=='narrative')['text']='x'*21000
  with self.assertRaisesRegex(ValueError,'gateway bound'):cs.request(row)
 def test_native_token_limit_fails_before_synthesis(self):
  body={k:self.body[k] for k in ('model','messages','system','output_config')};key=digest(body)
  (self.path/(key+'-tokens.json')).write_text(json.dumps({'request_hash':key,'input_tokens':16001}))
  with self.assertRaisesRegex(ValueError,'token'):cs.token_check(self.body,self.path,allow_live=False)
 def test_disabled_live_and_exhausted_budget_block_provider_calls(self):
  with patch.object(cs,'token_check') as tokens:
   with self.assertRaisesRegex(ValueError,'disabled'):cs.synthesize(self.path,self.row['id'],self.ledger)
   with patch.object(self.ledger,'receipt',return_value={'remaining_reservation_usd':0}):
    with self.assertRaisesRegex(ValueError,'exhausted'):cs.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
   tokens.assert_not_called()
 def test_invalid_answer_shapes_are_rejected(self):
  for field,value in [('unanswered',{}),('findings',[]),('review_steps',[{'kind':'execute_treatment'}])]:
   a=copy.deepcopy(self.answer);a[field]=value
   if field=='findings':a['unanswered']=[];a['review_steps']=[]
   with self.assertRaises(ValueError):self.parse(a)

if __name__=='__main__':unittest.main()
