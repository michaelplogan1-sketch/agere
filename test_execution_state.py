"""General composition/continuation regressions; no providers or answer keys."""
import copy,json,sqlite3,unittest
from types import SimpleNamespace
from clinical_core.agere.clinical import execution_state as E,result_assembly as A,semantic_analyst as S,backstop_agent as B


def result(rows,columns=None):
 return {'columns':columns or list(dict.fromkeys(k for r in rows for k in r)),'rows':rows,'truncated':False}

class Assembly(unittest.TestCase):
 def setUp(self):
  self.sources={'people':result([{'patient_id':'A','rate_percent':75},{'patient_id':'B','rate_percent':0}]),
   'notes':result([{'patient_id':'A','record_id':'n1','date':'2030-01-02','quote':'Cost prevents use.','value':'Cost barrier'},
                   {'patient_id':'A','record_id':'n2','date':'2030-02-02','quote':'Paid fill collected.','value':'Later fill'}])}
 def test_collections_preserve_conflicting_dated_observations_and_membership(self):
  original=copy.deepcopy(self.sources);out=A.compose(self.sources,base='people',keys=['patient_id'],joins=[{'result':'notes','as':'observations','on':{'patient_id':'patient_id'},'mode':'collect'}])
  self.assertEqual(len(out['rows']),2);self.assertEqual(out['rows'][0]['observations'],self.sources['notes']['rows']);self.assertIsNone(out['rows'][1]['observations']);self.assertEqual(self.sources,original)
 def test_financial_provider_year_grain_and_key_mapping(self):
  sources={'cost':result([{'provider':'C1','year':2030,'paid_dollars':120},{'provider':'C2','year':2030,'paid_dollars':90}]),
   'quality':result([{'clinician':'C1','measurement_year':2030,'quality_percent':80}])}
  out=A.compose(sources,base='cost',keys=['provider','year'],joins=[{'result':'quality','as':'quality','on':{'provider':'clinician','year':'measurement_year'},'mode':'one'}])
  self.assertEqual(out['rows'][0]['quality']['quality_percent'],80);self.assertIsNone(out['rows'][1]['quality']);self.assertEqual([r['paid_dollars']for r in out['rows']],[120,90])
 def test_episode_grain_never_collapses_patient_visits(self):
  sources={'visits':result([{'patient_id':'A','encounter_id':'e1'},{'patient_id':'A','encounter_id':'e2'}]),'social':result([{'patient_id':'A','need':'transport'}])}
  with self.assertRaisesRegex(ValueError,'grain'):A.compose(sources,base='visits',keys=['patient_id'])
  out=A.compose(sources,base='visits',keys=['patient_id','encounter_id'],joins=[{'result':'social','as':'social','on':{'patient_id':'patient_id'},'mode':'one'}]);self.assertEqual(len(out['rows']),2)
 def test_scalar_join_refuses_arbitrary_selection(self):
  with self.assertRaisesRegex(ValueError,'not unique'):A.compose(self.sources,base='people',keys=['patient_id'],joins=[{'result':'notes','as':'note','on':{'patient_id':'patient_id'},'mode':'one'}])
 def test_truncated_and_foreign_results_cannot_be_composed(self):
  self.sources['people']['truncated']=True
  with self.assertRaisesRegex(ValueError,'Complete'):A.compose(self.sources,base='people',keys=['patient_id'])
  with self.assertRaisesRegex(ValueError,'executed'):A.compose(self.sources,base='invented',keys=['patient_id'])
 def test_missing_identity_is_not_merged_into_another_missing_identity(self):
  self.sources['people']['rows'][0]['patient_id']=None
  with self.assertRaisesRegex(ValueError,'missing key'):A.compose(self.sources,base='people',keys=['patient_id'])
 def test_alias_collision_and_bad_key_are_repairable(self):
  for alias,on in [('patient_id',{'patient_id':'patient_id'}),('notes',{'rate_percent':'patient_id'})]:
   with self.assertRaises(ValueError):A.compose(self.sources,base='people',keys=['patient_id'],joins=[{'result':'notes','as':alias,'on':on,'mode':'collect'}])
 def test_unmatched_and_missing_right_id_rows_are_accounted_for(self):
  self.sources['notes']['rows']+=[{'patient_id':'C'},{'patient_id':None}]
  out=A.compose(self.sources,base='people',keys=['patient_id'],joins=[{'result':'notes','as':'notes','on':{'patient_id':'patient_id'},'mode':'collect'}]);c=out['assembly']['joins'][0]
  self.assertEqual(c['source_rows_outside_base_keys'],1);self.assertEqual(c['source_rows_with_missing_key'],1)
 def test_exact_empty_population_stays_empty(self):
  self.sources['people']=result([],['patient_id'])
  out=A.compose(self.sources,base='people',keys=['patient_id'],joins=[{'result':'notes','as':'notes','on':{'patient_id':'patient_id'},'mode':'collect'}]);self.assertEqual(out['rows'],[])
 def test_materialization_copies_intact_composed_rows(self):
  out=A.compose(self.sources,base='people',keys=['patient_id'],joins=[{'result':'notes','as':'notes','on':{'patient_id':'patient_id'},'mode':'collect'}]);out['result_view']='agere_result_3';answer={'table_from_tool':'agere_result_3'};B.materialize_table(answer,[{'result':out}]);self.assertEqual(answer['table'],out['rows'])

 def test_projection_preserves_requested_columns_without_mutation(self):
  original=copy.deepcopy(self.sources)
  out=A.compose(self.sources,base='people',keys=['patient_id'],base_columns=['patient_id'],joins=[{'result':'notes','as':'notes','on':{'patient_id':'patient_id'},'mode':'collect','columns':['record_id','date','quote']}])
  self.assertEqual(set(out['rows'][0]),{'patient_id','notes'});self.assertEqual(out['rows'][0]['notes'][0],{k:self.sources['notes']['rows'][0][k]for k in ['record_id','date','quote']});self.assertEqual(self.sources,original)
 def test_duplicate_join_rows_outside_base_do_not_block_an_applicable_scalar(self):
  self.sources['notes']['rows']=[{'patient_id':'A','record_id':'n1'},{'patient_id':'C'},{'patient_id':'C'}]
  out=A.compose(self.sources,base='people',keys=['patient_id'],joins=[{'result':'notes','as':'note','on':{'patient_id':'patient_id'},'mode':'one'}]);self.assertEqual(out['rows'][0]['note']['record_id'],'n1')

 def test_document_and_passage_result_views_are_composable(self):
  self.sources['passages']={'passages':[{'patient_id':'A','record_id':'r1','text':'Documented source.'}],'result_view':'passages'}
  out=A.compose(self.sources,base='people',keys=['patient_id'],joins=[{'result':'passages','as':'sources','on':{'patient_id':'patient_id'},'mode':'collect'}]);self.assertEqual(out['rows'][0]['sources'][0]['text'],'Documented source.')

class State(unittest.TestCase):
 def test_catalog_keeps_all_views_failed_inputs_and_field_availability(self):
  calls=[{'name':'query_sql','input':{'sql':'select x'},'result':{'result_view':'agere_result_1',**result([{'patient_id':'A','review_json':json.dumps({'barrier':[{'quote':'Cost','record_id':'n','date':'2030-01-01','value':'Cost'}]})}])}},
   {'name':'query_sql','input':{'sql':'select bad'},'result':{'error':'Unknown column'}}]
  c=E.catalog(calls);self.assertEqual(c[0]['rows_computed'],1);self.assertEqual(c[0]['narrative_field_manifest']['barrier']['findings'],1);self.assertEqual(c[1]['failed_input'],{'sql':'select bad'});self.assertEqual(c[1]['error'],'Unknown column')
 def test_original_request_and_current_feedback_survive_without_rewriting(self):
  request={'question':'Admissions, PCP and social barriers?','period':'2029','scope':[]};m=E.messages(json.dumps(request),[],feedback={'feedback':'Missing PCP'},candidate={'table':[{'patient_id':'A'}],'table_from_tool':'v','headline':'Draft'})
  d=json.loads(m[0]['content']);self.assertEqual(json.loads(d['original_request']),request);self.assertEqual(d['current_review_feedback']['feedback'],'Missing PCP');self.assertEqual(d['last_draft_rows'],1)
 def test_non_table_engine_contract_is_not_dropped(self):
  observed={'parameters':{'group_by':'provider'},'assumptions':['No causal interpretation']};self.assertEqual(E.catalog([{'name':'describe_analysis','input':{},'result':observed}])[0]['observation'],observed)
 def test_compact_tool_loop_uses_native_results_and_finishes(self):
  sent=[]
  def send(body):
   sent.append(copy.deepcopy(body));turn=len(sent)
   if turn==1:return {'content':[{'type':'tool_use','id':'t1','name':'query_sql','input':{'sql':'select 25 as percent'}}]},0,False
   return {'content':[{'type':'text','text':json.dumps({'status':'answered','value':25,'unit':'percent','table_from_tool':'agere_result_1'})}]},0,False
  def execute(use):return {'result_view':'agere_result_1',**result([{'percent':25}])}
  got=S.tool_loop('q','sys',B.TOOLS,execute,send=send,model='scripted',max_tools=None,compact_execution_state=True)
  self.assertIsNone(got.get('error'));self.assertEqual(got['answer']['value'],25);state=json.loads(sent[1]['messages'][0]['content']);self.assertEqual(state['executed_results'][0]['computed_rows'],[{'percent':25}]);self.assertEqual(len(got['tool_calls']),1)
 def test_review_feedback_and_native_tools_survive_format_repair(self):
  sent=[];reviews=[]
  def send(body):
   sent.append(body);texts=['bad json',json.dumps({'status':'partial','value':0}),json.dumps({'status':'answered','value':25})];return {'content':[{'type':'text','text':texts[len(sent)-1]}]},0,False
  def review(a,calls):
   reviews.append(a['value']);return {'status':'repair','feedback':'Use executed percent25'}if a['value']==0 else {'status':'confirmed'}
  got=S.tool_loop('q','sys',[],lambda u:None,send=send,model='scripted',max_tools=None,compact_execution_state=True,format_retries=1,review=review)
  self.assertEqual(got['answer']['value'],25);self.assertEqual(reviews,[0,25]);self.assertIn('Use executed percent25',sent[2]['messages'][0]['content'])

if __name__=='__main__':unittest.main()
