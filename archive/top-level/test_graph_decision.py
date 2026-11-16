"""Graph origin invariants plus existing task/acquisition transactions.

Fixtures stub source-currentness and source-state reads; live HTTP verification
covers the original warehouse separately. No clinical qualification claims.
"""
import copy,json,sqlite3,unittest,zlib
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from clinical_core.contracts import digest
from clinical_core.agere.clinical import graph_decision as G, question_decision as Q, decision_acquisition as A
import test_question_decision as old_tests
from test_native_care_assertions import records, interpreted, P

class GraphDecision(unittest.TestCase):
 def setUp(self):
  self.fixture=old_tests.Decision();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
  self.path=self.fixture.path;self.retriever=self.fixture.retriever
  self.original_origin=Q.care.origin;self.addCleanup(setattr,Q.care,'origin',self.original_origin)
  cap=copy.deepcopy(self.fixture.fixture.cap);cap['need_tags']=['care_navigation'];cap['pathway_ids']=[]
  Q.care.save_portfolio(self.path,{'buyer_id':'buyer1','expected_revision':1,'actor':'fixture','capabilities':[cap]})
  r=G.state.project(interpreted(records()[:2]),{**P,'filter':'all'})
  r=json.loads(json.dumps(r).replace('"p"','"p1"'));r.update(id='e'*64,code_identity='fixture')
  self.receipt=r;self.receipt['interpretation']={'sources':{'r1':{'patient_id':'p1','record_hash':digest(self.fixture.source_record)}}}
  self.bare={k:v for k,v in r.items() if k!='interpretation'}
  self.dep={'snapshot':{'as_of':'2026-10-10'},'roster_count':1,'roster_digest':digest(['p1']),'version':1,'evaluated_source_inventory':{'fixture':'version1'}}
  self.patch(G,'dependencies',side_effect=lambda *args:copy.deepcopy(self.dep))
  self.patch(G.state,'inspect',side_effect=lambda *args:copy.deepcopy(self.receipt))
  self.graph={'review_source_fingerprint':self.dep['evaluated_source_inventory'],'result_id':'b'*64,'question':'What is documented for this exact request?',
   'graph':{'nodes':[{'id':'care','operation':'source_state','parameters':P}]},'answer_coverage':{'status':'partial_evidence'},
   'results':[{'node':{'id':'care','operation':'source_state','parameters':P},'result':{'review_members':[{'patient_id':'p1','receipt_id':'e'*64,'receipt_digest':digest(self.bare),'membership':True,'care_account_counts':r['care_account_counts']}],
    'patients_evaluated':1,'population_digest':digest(['p1']),'input_membership':{'counts':{'evaluated':1,'matched':1,'unknown':0}},'care_account_counts':r['care_account_counts'],'care_report_counts':r['care_report_counts']}}]}
  with sqlite3.connect(self.path/'stack.sqlite3') as db:
   db.execute('CREATE TABLE result_cache(id TEXT PRIMARY KEY,payload TEXT)');db.execute('INSERT INTO result_cache VALUES (?,?)',(self.graph['result_id'],json.dumps(self.graph)))
  self.patch(G.Q.patient_context,'prepare',return_value={'id':'fixture'})
  self.patch(G.Q.participation_work.evidence,'inspect',return_value={})
  self.patch(G.Q.patient_context,'association',return_value=None)
  from clinical_core.agere.clinical import analysis_graph
  self.patch(analysis_graph,'execute',side_effect=lambda *a,**kw:copy.deepcopy(self.graph))
  self.stack=SimpleNamespace(path=self.path,retriever=self.retriever)
  self.request={'result_id':self.graph['result_id'],'buyer_id':'buyer1'}
 def patch(self,obj,name,**kw):
  p=patch.object(obj,name,**kw);p.start();self.addCleanup(p.stop)
 def prepare(self):return G.prepare(self.stack,self.request)
 def member(self):
  d=self.prepare();return G.open_member(self.retriever,{'decision_id':d['id'],'patient_id':'p1'})
 def queue(self,m):return Q.queue(self.retriever,{'decision_id':m['id'],'option_id':m['matching']['options'][0]['id'],'actor':'fixture'})
 def update_graph(self):
  with sqlite3.connect(self.path/'stack.sqlite3') as db:db.execute('UPDATE result_cache SET payload=?',(json.dumps(self.graph),))
 def test_bound_question_scope_source_and_existing_task(self):
  d=self.prepare();m=self.member();self.assertEqual(d,self.prepare());self.assertEqual(m,self.member())
  self.assertEqual(m['question'],d['question']);self.assertEqual(m['population'],d['population'])
  self.assertNotIn('answer_id',m);self.assertEqual(m['evidence_gaps'][0]['request_id'],'request')
  t=self.queue(m);self.assertTrue(self.queue(m)['idempotent_replay']);self.assertNotIn('answer_digest',t['decision_binding'])
  view=Q.reopen(self.retriever,m['id']);self.assertTrue(view['dependencies_current']);self.assertEqual(len(view['tasks']),1)
  self.assertEqual(len(Q.reopen(self.retriever,d['id'])['task_links']),1)
 def test_no_evaluator_question_patient_or_service_override(self):
  for extra in ({'question':'other'},{'patient_id':'other'},{'need_tags':['medication_review']}):
   with self.assertRaises(ValueError):G.prepare(self.stack,{**self.request,**extra})
 def test_outside_member_rejected(self):
  d=self.prepare()
  with self.assertRaisesRegex(ValueError,'outside'):G.open_member(self.retriever,{'decision_id':d['id'],'patient_id':'other'})
 def test_denominator_and_duplicate_members_refused(self):
  self.graph['results'][0]['result']['review_members']*=2;self.update_graph()
  with self.assertRaisesRegex(ValueError,'denominator'):self.prepare()
 def test_legacy_questionless_cache_refused(self):
  del self.graph['question'];self.update_graph()
  with self.assertRaisesRegex(ValueError,'original question'):self.prepare()
 def test_cross_patient_source_refused(self):
  self.receipt['interpretation']['sources']['r1']['patient_id']='other'
  with self.assertRaisesRegex(ValueError,'Cross-patient'):self.member()
 def test_changed_receipt_refused(self):
  self.receipt['status']='changed'
  with self.assertRaisesRegex(ValueError,'integrity'):self.member()
 def test_changed_graph_stales_parent_and_prevents_new_work(self):
  d=self.prepare();m=self.member();self.dep['version']=2
  self.assertFalse(Q.reopen(self.retriever,d['id'])['dependencies_current'])
  self.assertFalse(Q.reopen(self.retriever,m['id'])['dependencies_current'])
  with self.assertRaises(ValueError):self.queue(m)
 def test_no_matching_service_visible_without_unrelated_program(self):
  self.graph['results'][0]['result']['review_members'][0]['membership']=False;self.update_graph()
  m=self.member();self.assertIsNone(m['matching']);self.assertEqual(m['status'],'no_supported_service_option')
  self.assertNotIn('program',m)
 def test_acquisition_keeps_exact_gap_and_never_invents_record(self):
  m=self.member();task=self.queue(m)['task'];p=A.preview(self.retriever,{'decision_id':m['id'],'task_id':task['id']})
  dep=p['dependencies'][0];self.assertIsNone(dep['observation']['original_gap']['target_record_id'])
  self.assertEqual(dep['observation']['status'],'completion_not_observed')
  r=A.create(self.retriever,{'decision_id':m['id'],'task_id':task['id'],'preview_id':p['id'],'dependency_id':dep['id'],'actor':'fixture'})
  check=A.check(self.retriever,{'request_id':r['result_id']})
  self.assertFalse(check['request_sent']);self.assertFalse(check['task_state_changed']);self.assertFalse(check['changes'])
  self.dep['version']=2;check=A.check(self.retriever,{'request_id':r['result_id']})
  self.assertEqual(check['status'],'graph_evidence_changed_requires_reanswer');self.assertTrue(check['changes'])
 def test_cas_preview_and_foreign_dependency_refused(self):
  m=self.member();task=self.queue(m)['task'];p=A.preview(self.retriever,{'decision_id':m['id'],'task_id':task['id']})
  body={'decision_id':m['id'],'task_id':task['id'],'preview_id':p['id'],'dependency_id':p['dependencies'][0]['id'],'actor':'fixture'}
  for change in ({'preview_id':'bad'},{'dependency_id':'other'}):
   with self.assertRaises(ValueError):A.create(self.retriever,{**body,**change})
 def test_historical_scope_remains_in_acquisition(self):
  scope={'before_date_exclusive':'2026-09-01','available_by':'2026-09-20'}
  self.receipt['temporal_scope']=scope;self.bare['temporal_scope']=scope
  self.graph['results'][0]['result']['review_members'][0]['receipt_digest']=digest(self.bare);self.update_graph()
  m=self.member();self.assertEqual(m['findings'][0]['temporal_scope'],scope)
  task=self.queue(m)['task'];p=A.preview(self.retriever,{'decision_id':m['id'],'task_id':task['id']})
  self.assertEqual(p['dependencies'][0]['observation']['original_gap']['temporal_scope'],scope)

class SourceInventory(unittest.TestCase):
 def test_new_deleted_changed_and_outside_scope_sources(self):
  import tempfile
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)
   graph={'results':[{'node':{'operation':'source_state','parameters':{'streams':['referrals']}},'result':{'review_members':[{'patient_id':'p1'}]}}]}
   with sqlite3.connect(path/'stack.sqlite3') as db:
    db.execute('CREATE TABLE records(id TEXT,patient TEXT,stream TEXT,payload TEXT)')
    db.execute('INSERT INTO records VALUES (?,?,?,?)',('r','p1','referrals','{"status":"requested"}'))
   original=G.source_fingerprint(path,graph)
   with sqlite3.connect(path/'stack.sqlite3') as db:
    db.execute('INSERT INTO records VALUES (?,?,?,?)',('outside','p2','referrals','{}'))
    db.execute('INSERT INTO records VALUES (?,?,?,?)',('unrelated','p1','labs','{}'))
   self.assertEqual(G.source_fingerprint(path,graph),original)
   with sqlite3.connect(path/'stack.sqlite3') as db:db.execute("UPDATE records SET payload='{}' WHERE id='r'")
   self.assertNotEqual(G.source_fingerprint(path,graph),original)
   with sqlite3.connect(path/'stack.sqlite3') as db:db.execute("DELETE FROM records WHERE id='r'")
   self.assertEqual(G.source_fingerprint(path,graph)['records'],0)
   with sqlite3.connect(path/'stack.sqlite3') as db:db.execute('INSERT INTO records VALUES (?,?,?,?)',('new','p1','referrals','{}'))
   self.assertNotEqual(G.source_fingerprint(path,graph),original)

if __name__=='__main__':unittest.main()
