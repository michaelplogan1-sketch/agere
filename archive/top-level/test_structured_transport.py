import copy,json,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_reference_scope_work as fixture
from clinical_core.contracts import digest
from clinical_core.agere.clinical import structured_transport as t,context_builder as c,reference_scope_work as s,case_passes as passes
REAL_VERIFIED=t._original_verified

def parent(records,patient,question,ids):
 selected=[r for r in records if r['record_id'] in ids];params=c.validate({'question':question,'streams':sorted({r['source_table'] for r in selected}),'concepts':[],'contract':'','program_ids':[],'max_bytes':60000});meta={'snapshot':{'as_of':'2026-10-10'}}
 row=c.pack(patient,params,c.units(patient,selected,[],params,meta['snapshot']['as_of']),meta)
 row.update(parameters=params,record_scope={'record_ids':sorted(ids),'selection_sufficiency':'unassessed'},schema='fixture-parent-context')
 row['missing_evidence']['record_scope']={'kind':'explicit_received_records','selected_records':len(ids),'selection_sufficiency':'unassessed'}
 row['id']=digest(row);return row

class Partition(unittest.TestCase):
 def test_nested_roundtrip_preserves_types_empty_containers_unicode_and_order(self):
  value={'01':{'a/b~c':[None,False,0,'',{},[],{'é':'μ'*400}]},'other':[{'v':str(i)*200} for i in range(12)]}
  split=t.partition(value,200);self.assertEqual(c.encode(t.restore(split)),c.encode(value));self.assertEqual(t.pointer(['a/b~c',0]),'/a~1b~0c/0')
 def test_atomic_strings_are_not_truncated(self):
  value={'large':'a'*10000};split=t.partition(value,100);self.assertEqual(split['parts'],[{'path':['large'],'value':'a'*10000}]);self.assertEqual(t.restore(split),value)
 def test_missing_duplicate_and_orphan_fragments_rejected(self):
  split=t.partition({'a':'abc','b':list(range(25))},10)
  for candidate in ({**split,'parts':split['parts'][:-1]},{**split,'parts':split['parts']+split['parts'][:1]},{**split,'parts':split['parts']+[{'path':['orphan'],'value':0}]}):
   with self.assertRaises(ValueError):t.restore(candidate)
 def test_deep_structure_rejected(self):
  value='x'*4000
  for _ in range(66):value={'a':value}
  with self.assertRaisesRegex(ValueError,'depth'):t.partition(value)

class Work(unittest.TestCase):
 record=fixture.Scope.record;context=fixture.Scope.context;match=fixture.Scope.match;queue=fixture.Scope.queue;event=fixture.Scope.event
 def setUp(self):
  fixture.Scope.setUp(self)
  self.records[-1]['documents']=[{'name':'Document '+str(i),'values':{'value':'v'*400,'number':i,'missing':None}} for i in range(20)]
  self.records[-1]['_warehouse_fields'].append('documents')
  self.pre=s.preview(self.path,self.request)
  def create(path,patient,question,ids):
   row=parent(self.records,patient,question,ids);self.contexts[row['id']]=row;return copy.deepcopy(row)
  self.patch(t.original,'create',side_effect=create)
  self.base=s.select(self.path,{'preview_id':self.pre['id'],'record_ids':['c','o','pkt']})
  self.patch(t,'_original_inspect',side_effect=lambda path,key:copy.deepcopy(self.contexts[key]))
  self.patch(t,'_original_verified',side_effect=REAL_VERIFIED)
  for module,name in [(t.original,'exists'),(t.original,'inspect'),(s,'inspect_selection')]:self.addCleanup(setattr,module,name,getattr(module,name))
  self.patch(t.original,'verified',side_effect=REAL_VERIFIED)
  # Mock attributes are not runtime installation markers.
  t.original.inspect.structured_transport=False;t.install();t.install()
  self.req={'selection_id':self.base['id']}
 def patch(self,module,name,**kw):
  p=patch.object(module,name,**kw);mock=p.start();self.addCleanup(p.stop);return mock
 def prepare(self):return t.prepare_selection(self.path,self.req)
 def test_original_context_and_scope_preserved_losslessly(self):
  old=copy.deepcopy(self.contexts[self.base['context_id']]);selected=self.prepare();row=t.inspect(self.path,selected['context_id'])
  self.assertEqual(old,self.contexts[self.base['context_id']]);self.assertEqual(row['record_scope'],old['record_scope']);self.assertEqual(row['parameters']['question'],old['parameters']['question'])
  self.assertEqual([u for u in old['model_context']['evidence'] if u['kind']=='narrative'],[u for u in row['model_context']['evidence'] if u['kind']=='narrative'])
  self.assertEqual(row['budget']['candidate_units'],len(row['model_context']['evidence']));self.assertGreater(row['budget']['selected_units'],old['budget']['selected_units'])
  self.assertTrue(REAL_VERIFIED(self.path,row['id']));self.assertEqual(s.inspect_selection(self.path,selected['id']),selected)
 def test_every_parent_reconstructs_exactly_using_manifest_and_transported_values(self):
  selected=self.prepare();row=t.inspect(self.path,selected['context_id']);original=self.contexts[self.base['context_id']]
  units={u['id']:u for u in row['model_context']['evidence']};parents={u['id']:u for u in original['model_context']['evidence']}
  for m in row['structured_transport']['parents']:
   split={'containers':m['containers'],'parts':[{'path':v['path'],'value':units[f['id']]['reported_fields'][v['json_pointer']]} for f in m['fragments'] for v in f['paths']]}
   self.assertEqual(c.encode(t.restore(split)),c.encode(parents[m['parent_unit_id']]['reported_fields']))
 def test_source_change_marks_new_and_old_scope_stale(self):
  self.prepare();self.records[-1]['documents'][0]['values']['value']='changed'
  self.assertTrue(all(not row['current'] for row in s.listing(self.path,{'target':self.target})['selections']))
 def test_original_context_mutation_and_fragment_tampering_rejected(self):
  row=self.prepare();path=t.location(self.path,row['context_id']);saved=json.loads(path.read_text());saved['model_context']['evidence'][-1]['reported_fields']='changed';path.write_text(json.dumps(saved))
  with self.assertRaises(ValueError):t.inspect(self.path,row['context_id'])
  self.assertFalse(s.listing(self.path,{'target':self.target})['selections'][-1]['current'])
 def test_concurrent_requests_and_idempotency(self):
  with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:self.prepare(),range(2)))
  self.assertEqual(rows[0],rows[1]);self.assertEqual(len(s.listing(self.path,{'target':self.target})['selections']),2)
 def test_answer_returns_to_original_question_with_derived_context_only(self):
  row=self.prepare();req=fixture.Scope.prepare_answer(self,row);answer=s.attach(self.path,req)
  self.assertEqual(answer['context_id'],row['context_id']);self.assertEqual(answer['question'],self.q['review_question']);self.assertFalse(answer['requirement_resolved'])
  self.output['context_id']=self.base['context_id']
  with self.assertRaisesRegex(ValueError,'exact reference'):s.attach(self.path,req)
 def test_exact_fragment_citation_uses_original_source_and_json_path(self):
  row=t.inspect(self.path,self.prepare()['context_id']);unit=next(u for u in row['model_context']['evidence'] if 'structured_fragment' in u and any('/documents/0' in p['json_pointer'] for p in u['structured_fragment']['paths']));text=c.encode(unit['reported_fields']).decode()
  raw={'model':'fixture','stop_reason':'end_turn','content':[{'type':'text','text':json.dumps({'findings':[{'id':'f1','kind':'documented_account','text':'Fixture summary only','citations':[{'unit':'u1','quote':text[:80]}],'unresolved':[]}],'unanswered':[],'review_steps':[]})}]}
  parsed=passes.single.parse(raw,{'model':'fixture'},{'u1':{'unit':unit,'text':text}},row);cite=parsed['findings'][0]['citations'][0]
  self.assertEqual(cite['field'],unit['field']);self.assertEqual(cite['source']['record_id'],unit['source_ref']);self.assertTrue(cite['citation_verified'])
 def test_double_transform_extra_authority_and_incomplete_parent_rejected(self):
  row=self.prepare()
  with self.assertRaises(ValueError):t.prepare_selection(self.path,{'selection_id':row['id']})
  with self.assertRaises(ValueError):t.prepare_selection(self.path,{**self.req,'human_review_recorded':True})
  self.contexts[self.base['context_id']]['dispatch']['source_review_allowed']=False
  with self.assertRaisesRegex(ValueError,'Complete declared'):t.create(self.path,self.base['context_id'])
 def test_unchanged_small_contexts_are_not_rewritten(self):
  original=copy.deepcopy(self.contexts[self.base['context_id']]);original['model_context']['evidence']=[u for u in original['model_context']['evidence'] if u['source_ref']!='pkt'];original['record_scope']['record_ids']=['c','o'];original['model_context']['sources'].pop('pkt');original['id']=digest(original)
  self.contexts[original['id']]=original
  with self.assertRaisesRegex(ValueError,'No structured'):t.create(self.path,original['id'])
if __name__=='__main__':unittest.main()
