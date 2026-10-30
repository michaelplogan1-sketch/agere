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
 def test_expanded_recovery_draft_stays_native_before_original_request_encoding(self):
  feedback=['Repair the current unresolved source check.',{'unverified_retained_draft':{
   'table':[{'patient_id':'A','quote':'retained source '*20000}],
   'table_from_tool':'agere_result_16','status':'partial','missing':['Current tolerance unknown'],
   'limitations':['Historical source only'],'verification':'unverified',
   'table_binding':{'result_view':'agere_result_16','rows':1,'query':'SELECT '+"'source'"*20000}}}]
  db=sqlite3.connect(':memory:');self.addCleanup(db.close)
  tools=SimpleNamespace(semantic=SimpleNamespace(db=db,_authorize=lambda *args:sqlite3.SQLITE_OK),result_sources={},executed_results={},seen=[],rows_by_record={})
  tools.retain_result=lambda out:B.Tools.retain_result(tools,out)
  replay={'calls':[],'downstream_review_feedback':feedback,'recovered_read_only_actions':19};before=copy.deepcopy(replay)
  projected=E.recovery_context(replay,tools);ref=projected['downstream_review_native']
  self.assertEqual(json.loads(db.execute('SELECT feedback_json FROM "'+ref['result_view']+'"').fetchone()[0]),feedback)
  state=json.loads(E.messages(json.dumps({'question':'Review current evidence','execution_reuse':projected}),[])[0]['content'])
  self.assertLess(len(json.dumps(state)),8000)
  draft=projected['downstream_review_feedback'][1]['unverified_retained_draft']
  self.assertEqual(draft['last_draft']['missing'],['Current tolerance unknown'])
  self.assertEqual(draft['last_draft']['status'],'partial')
  self.assertEqual(draft['last_draft_table_binding']['result_view'],'agere_result_16')
  self.assertEqual(replay,before);self.assertEqual(E.recovery_context(replay,tools),projected)

 def test_dated_role_collections_survive_empty_preview_and_compact_state(self):
  roles=[{'name':'Alex Rivera','relationship':'signed_correspondence_author','date':'2030-01-01',
          'current_assignment':'not_established','record_id':'letter','quote':'Signed by Alex Rivera.'}]
  rows=[{'patient_id':'A','review_json':json.dumps({'reason':[{'value':'x'*16000}]}),
         'source_clinicians_json':json.dumps(roles)},
        {'patient_id':'B','review_json':'{}','source_clinicians_json':'[]'}]
  value={'result_view':'agere_result_roles',**result(rows)};before=copy.deepcopy(value)
  shown=S.actor_result(value)
  self.assertEqual(shown['rows'],[])
  manifest=shown['structured_collection_manifest']['source_clinicians_json']
  self.assertEqual(manifest['rows_observed'],2)
  self.assertEqual(manifest['collections']['$']['rows_with_items'],1)
  self.assertEqual(manifest['collections']['$']['items'],1)
  self.assertIn('current_assignment',manifest['collections']['$']['item_fields'])
  state=json.loads(E.messages('Identify dated care relationships',[
   {'name':'review_documents','input':{},'result':value}])[0]['content'])
  entry=state['executed_results'][0]
  self.assertEqual(entry['structured_collection_manifest'],shown['structured_collection_manifest'])
  self.assertEqual(entry['result_view'],'agere_result_roles');self.assertEqual(value,before)
  db=sqlite3.connect(':memory:')
  try:
   db.execute('create table native(patient_id,source_clinicians_json)')
   db.executemany('insert into native values(?,?)',[(r['patient_id'],r['source_clinicians_json'])for r in rows])
   actual=db.execute("select n.patient_id,json_extract(j.value,'$.relationship'),json_extract(j.value,'$.current_assignment') from native n,json_each(n.source_clinicians_json) j").fetchall()
   self.assertEqual(actual,[('A','signed_correspondence_author','not_established')])
  finally:db.close()

 def test_collection_manifest_handles_contract_and_episode_collections_without_clinical_rules(self):
  rows=[{'contract_id':'C1','clauses_json':json.dumps({'terms':[{'record_id':'t1','rate_percent':15}],'exceptions':[]}),
         'episodes_json':[{'encounter_id':'e1','status':'reported'}]},
        {'contract_id':'C2','clauses_json':'{invalid','episodes_json':None}]
  before=copy.deepcopy(rows);m=S.collection_manifest(rows)
  self.assertEqual(m['clauses_json']['unparseable_rows'],1)
  self.assertEqual(m['clauses_json']['collections']['$."terms"']['items'],1)
  self.assertEqual(m['clauses_json']['collections']['$."exceptions"']['items'],0)
  self.assertEqual(m['episodes_json']['null_rows'],1)
  self.assertEqual(m['episodes_json']['collections']['$']['item_fields'],['encounter_id','status'])
  self.assertEqual(rows,before)

 def test_missing_json_is_not_reported_as_an_empty_collection(self):
  m=S.collection_manifest([{'events_json':'null'},{'events_json':None},{'events_json':'bad'},{'events_json':'[]'}])['events_json']
  self.assertEqual(m['rows_observed'],4);self.assertEqual(m['noncollection_rows'],1)
  self.assertEqual(m['null_rows'],1);self.assertEqual(m['unparseable_rows'],1)
  self.assertEqual(m['collections']['$'],{'rows_with_items':0,'items':0,'item_fields':[]})

 def test_rejected_draft_keeps_binding_and_uncertainty_without_expanded_native_sql(self):
  query='SELECT '+"'native evidence'"*20000
  candidate={'table':[{'patient_id':'A','cost_dollars':42}],
   'table_binding':{'result_view':'agere_result_1','rows':1,'columns':['patient_id','cost_dollars'],'query':query},
   'missing':['Current treatment unknown'],'limitations':['No causal inference'],'verification':'unverified',
   'investigation_review':{'steps':[{'raw':'old receipt'*20000}]}}
  before=copy.deepcopy(candidate);state=json.loads(E.messages('Paid claims and clinical context',[],candidate=candidate)[0]['content'])
  self.assertLess(len(json.dumps(state)),5000)
  self.assertEqual(state['last_draft_table_binding']['result_view'],'agere_result_1')
  self.assertEqual(state['last_draft_table_binding']['rows'],1)
  self.assertTrue(state['last_draft_table_binding']['native_query_digest'])
  self.assertEqual(state['last_draft']['missing'],candidate['missing'])
  self.assertEqual(state['last_draft']['limitations'],candidate['limitations'])
  self.assertEqual(state['last_draft']['verification'],'unverified');self.assertEqual(candidate,before)

 def test_review_retains_exact_repairs_and_every_unresolved_dependency(self):
  feedback={'status':'repair','feedback':'Reconcile the episode denominator.','reason':'Conflicting dates',
   'coverage':{'label':'unknown','reasons':['One period is unresolved'],'reject':False,'probabilities':{'unknown':.9}},
   'support':{'failed_packet_ids':['patient_A'],'packets':[{'id':'patient_A','label':'contradicted'},
    {'id':'patient_B','label':'unknown','reason':'Source unavailable'},{'id':'patient_C','label':'supported'}]},
   'steps':[{'provider':'test','raw':'operational receipt'*20000}]}
  before=copy.deepcopy(feedback);state=json.loads(E.messages('Admissions and outcomes',[],feedback=feedback)[0]['content']);view=state['current_review_feedback']
  self.assertLess(len(json.dumps(state)),5000);self.assertEqual(view['feedback'],feedback['feedback'])
  self.assertEqual(view['reason'],feedback['reason']);self.assertEqual(view['coverage']['label'],'unknown')
  self.assertEqual(view['failed_dependency_ids'],['patient_A'])
  self.assertEqual([r['id']for r in view['unresolved_dependencies']],['patient_A','patient_B'])
  self.assertEqual(view['unresolved_dependencies'][1]['reason'],'Source unavailable')
  self.assertTrue(state['current_review_native_digest']);self.assertEqual(feedback,before)

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
