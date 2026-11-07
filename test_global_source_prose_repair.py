"""Global source-meaning deltas cannot replace native or unrelated answer work."""
import copy,json,unittest
from clinical_core.agere.clinical import execution_state as E,backstop_agent as B,semantic_analyst as S
from clinical_core.contracts import digest

class GlobalSourceProseRepair(unittest.TestCase):
 def setUp(self):
  self.calls=[{'name':'query_sql','input':{'sql':'SELECT period,prior_period FROM received'},'result':{
   'result_view':'agere_result_1','columns':['period','prior_period'],'rows':[[2029,2028]],'truncated':False}}]
  self.draft={'status':'partial','answer_type':'table','headline':'Recorded periods.',
   'facts':{'comparison':'The comparison is against 2027.','scope':'Received data only.'},
   'table_from_tool':'agere_result_1','table':[{'period':2029,'prior_period':2028}],
   'global_prose_basis':[{'path':['facts','comparison'],'basis':[{'result_view':'agere_result_1','row':0}]}]}
  B.materialize_table(self.draft,self.calls)
  self.manifest=[{'id':'headline','field':'headline','path':['answer','headline']},
   {'id':'fact_0','field':'facts.comparison','path':['answer','facts','comparison']},
   {'id':'fact_1','field':'facts.scope','path':['answer','facts','scope']}]
  self.packet={'id':'summary','date':'2030-05-27','text':json.dumps({'answer':self.draft,'claim_manifest':self.manifest,
   'all_final_rows':self.draft['table'],'native_bindings':[self.calls[0]['result']]}),
   'claim_manifest':self.manifest,'source_context_complete':True,'dependency_complete':True}
  self.support={'all_dependencies_checked':True,'packets':[{'id':'summary','label':'contradicted','decided_by':'typed_claim_checks',
   'field_review':{'packet_digest':digest(self.packet['text']),'manifest_digest':digest(self.manifest)},
   'claim_feedback':[{'packet_id':'summary','claim_id':'fact_0','field':'facts.comparison','path':['answer','facts','comparison'],
    'label':'wrong_time','decided_by':'reviewer','reason':'The named source periods differ.'}]}]}
 def feedback(self):
  repair=E.source_prose_repair(self.draft,self.support,[self.packet],{'label':'answers_fully','decided_by':'reviewer'})
  self.assertIsNotNone(repair)
  return {'status':'repair','feedback':'Inspect the located source interpretation.','prose_repair':repair}
 def edit(self,value=None):
  return {'repair_draft':{'draft_digest':digest(self.draft),'fields':[{'path':['facts','comparison'],
   'value':value or {'template':'The comparison is against {prior}.','bindings':{'prior':{
    'result_view':'agere_result_1','row':0,'column':'prior_period'}}}}]}}
 def test_real_tool_loop_changes_one_bound_field_and_keeps_review_required(self):
  feedback=self.feedback();before=copy.deepcopy(self.draft);reviews=[]
  def review(candidate,calls):
   reviews.append(copy.deepcopy(candidate))
   if len(reviews)==1:return feedback
   B.bind_computed_prose(candidate,calls)
   self.assertEqual(candidate['facts']['comparison'],'The comparison is against 2028.')
   self.assertEqual(candidate['facts']['scope'],before['facts']['scope'])
   self.assertEqual(candidate['table'],before['table']);self.assertEqual(candidate['global_prose_basis'],before['global_prose_basis'])
   return {'status':'repair','feedback':'Source qualification remains undecided.',
    'retry_disposition':'retain_partial_until_new_evidence','review_stop_reason':'unresolved_source_support'}
  def send(body):
   focus=json.loads(body['messages'][0]['content'])['focused_prose_repair']
   self.assertEqual(focus['source_packets'][0]['text'],self.packet['text'])
   self.assertEqual(focus['intent'],'source_meaning_review');self.assertNotIn('bind_literals',str(body['system']))
   return {'content':[{'type':'text','text':json.dumps(self.edit())}]},0.,True
  result=S.tool_loop('Explain period comparisons.','system',[],lambda u:self.fail('No repeated source query'),
   send=send,model='scripted',max_tools=None,review=review,initial_calls=self.calls,initial_answer=self.draft,compact_execution_state=True)
  self.assertEqual(result['review_stopped'],'unresolved_source_support');self.assertEqual(result['answer']['verification'],'unverified')
  self.assertEqual(result['prose_repairs'][0]['changed_answer_fields'],1);self.assertEqual(self.draft,before)
 def test_tampered_or_incomplete_producers_cannot_authorize_a_patch(self):
  for name in ['packet','manifest','field','path','duplicate','value','source','dependency']:
   p=copy.deepcopy(self.packet);s=copy.deepcopy(self.support)
   if name=='packet':s['packets'][0]['field_review']['packet_digest']='stale'
   elif name=='manifest':s['packets'][0]['field_review']['manifest_digest']='stale'
   elif name=='field':s['packets'][0]['claim_feedback'][0]['field']='facts.scope'
   elif name=='path':s['packets'][0]['claim_feedback'][0]['path']=['all_final_rows',0,'period']
   elif name=='duplicate':s['packets'][0]['claim_feedback']*=2
   elif name=='value':
    state=json.loads(p['text']);state['answer']['facts']['comparison']='Different';p['text']=json.dumps(state)
    s['packets'][0]['field_review']['packet_digest']=digest(p['text'])
   elif name=='source':p['source_context_complete']=False
   else:p['dependency_complete']=False
   with self.subTest(name=name):self.assertIsNone(E.source_prose_repair(self.draft,s,[p],{'label':'answers_fully','decided_by':'reviewer'}))
 def test_wrong_draft_paths_value_digest_and_packet_rejected(self):
  feedback=self.feedback()
  for name in ['draft','value','packet','path']:
   f=copy.deepcopy(feedback)
   if name=='draft':f['prose_repair']['draft_digest']='stale'
   elif name=='value':f['prose_repair']['answer_targets'][0]['value_digest']='stale'
   elif name=='packet':f['prose_repair']['source_packets'][0]['text']+=' '
   else:f['prose_repair']['answer_targets'][0]['path']=['table']
   with self.subTest(name=name):self.assertIsNone(E.answer_prose_repair_targets(self.draft,f))
  for path in [['headline'],['facts','scope'],['table',0,'period']]:
   edit=self.edit();edit['repair_draft']['fields'][0]['path']=path
   with self.subTest(path=path),self.assertRaises(ValueError):E.apply_prose_repair(edit,self.draft,calls=self.calls,feedback=feedback)
 def test_unknown_allows_inspection_and_noop_never_becomes_acceptance(self):
  self.support['packets'][0]['claim_feedback'][0].update(label='unknown',decided_by=None)
  feedback=self.feedback();self.assertFalse(feedback['prose_repair']['answer_targets'][0]['source_fault_established'])
  with self.assertRaises(E.UnchangedProseRepair):
   E.apply_prose_repair(self.edit(self.draft['facts']['comparison']),self.draft,calls=self.calls,feedback=feedback)
 def test_new_unbound_quantities_still_fail_compilation(self):
  fixed=E.apply_prose_repair(self.edit('There are 2044 received events.'),self.draft,calls=self.calls,feedback=self.feedback())
  with self.assertRaises(B.ComputedProseError):B.bind_computed_prose(fixed,self.calls)

if __name__=='__main__':unittest.main()
