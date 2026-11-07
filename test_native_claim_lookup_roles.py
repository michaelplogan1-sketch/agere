"""Saved financial answers distinguish cash links from provider monetary bases."""
import copy,json,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,plan_fence as F,system_one_gate as G
from clinical_core.agere.deliverables import answer_context as C
from clinical_core.agere.clinical.clinical_definitions import superseded
import test_documentary_tool_handoff as fixture

QUESTION='Compare recorded allowed and adjudicated payer-paid amounts with separately received cash by payer.'

class NativeClaimLookupRoles(unittest.TestCase):
 def setUp(self):
  self.fixture=fixture.DocumentaryHandoff();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
  db=sqlite3.connect(self.fixture.path/'semantic.sqlite3')
  db.execute("INSERT INTO claim(claim_id,patient_id,payer,service_date,allowed_cents,net_payer_paid_cents) VALUES('C-A','p00','P-A','2029-01-02',900,800)")
  db.execute("INSERT INTO payer_claim(payer_claim_id,patient_id,payer_id,service_from,counts_in_metrics,allowed_cents,paid_cents) VALUES('A-A','p00','P-A','2029-01-02',1,1200,1000)")
  for stream,ident in [('payer_cash_allocations','CASH-A'),('patient_payments','PATIENT-A')]:
   db.execute('INSERT INTO record(record_id,patient_id,stream,event_date,available_date,payload)VALUES(?,?,?,?,?,?)',
    (ident,'p00',stream,'2029-01-03','2029-01-04',json.dumps({'claim_id':'C-A','allocated_cents':750})))
  db.commit();db.close();self.tools=self.fixture.tools();self.calls=[]
 def sql(self,query):
  result=self.tools({'name':'query_sql','input':{'sql':query}})
  self.assertFalse(result.get('error'),result.get('error'))
  self.calls.append({'name':'query_sql','input':{'sql':query},'result':result});return result
 def answer(self,result):
  a={'status':'partial','answer_type':'table','headline':'Received amounts.','table_from_tool':result['result_view'],'missing':[]}
  B.materialize_table(a,self.calls);return a
 def graph(self):
  return {'nodes':[{'id':'q'+str(i),'operation':'sql','parameters':{'query':c['result']['executed_query'],'returns':'table'}}for i,c in enumerate(self.calls)if c['name']=='query_sql']}
 def issues(self,answer):
  return F.premise_issues(self.fixture.path,QUESTION,self.graph(),native_calls=self.calls,answer=answer)
 def cash_sql(self,expression="SUM(json_extract(r.payload,'$.allocated_cents'))",*,stream='payer_cash_allocations',where='',join=True):
  return f"SELECT c.payer,{expression} AS amount FROM record r JOIN claim c ON "+(
   "c.claim_id=json_extract(r.payload,'$.claim_id') AND c.patient_id=r.patient_id"if join else'1=1')+f" WHERE r.stream='{stream}' {where} GROUP BY c.payer"
 def test_complete_cash_link_and_payer_basis_save_reopen_without_source_reexecution(self):
  self.sql('SELECT SUM(allowed_cents) AS abandoned_provider_amount FROM claim')
  paid=self.sql('SELECT payer_id AS payer,SUM(paid_cents)AS payer_paid FROM payer_claim WHERE counts_in_metrics=1 GROUP BY payer_id')
  cash=self.sql("WITH links AS(SELECT claim_id,patient_id,payer,service_date,allowed_cents FROM claim) SELECT c.payer,SUM(json_extract(r.payload,'$.allocated_cents'))AS cash FROM record r JOIN links c ON c.claim_id=json_extract(r.payload,'$.claim_id')AND c.patient_id=r.patient_id WHERE r.stream='payer_cash_allocations' GROUP BY c.payer")
  result=self.sql(f"SELECT p.payer,p.payer_paid,c.cash FROM {paid['result_view']} p LEFT JOIN {cash['result_view']} c USING(payer)")
  a=self.answer(result);self.assertEqual(self.issues(a),[])
  self.assertTrue(any('definitions:'in e for e in F.premise_issues(self.fixture.path,QUESTION,self.graph(),native_calls=self.calls)))
  before=copy.deepcopy(self.calls)
  with patch('clinical_core.agere.clinical.sql_query.execute',side_effect=AssertionError('No source execution during saved validation')):
   saved=C.capture(self.fixture.path,QUESTION,{'answer':a,'graph':self.graph(),'investigation':{'tool_calls':self.calls}})
   opened=C.load(self.fixture.path,saved['id'],current=True)
  self.assertEqual(opened['answer'],a);self.assertFalse(opened['invalidated']);self.assertEqual(self.calls,before)
 def test_cash_word_never_waives_provider_amount_count_filter_or_scope(self):
  queries=[self.cash_sql('SUM(c.allowed_cents)'),self.cash_sql('COUNT(c.claim_id)'),
   self.cash_sql(where='AND c.net_payer_paid_cents>0'),self.cash_sql(stream='patient_payments'),
   self.cash_sql(where="OR r.stream='patient_payments'"),self.cash_sql(join=False),
   "WITH cash AS(SELECT payload FROM record WHERE stream='payer_cash_allocations') SELECT COUNT(*)AS amount FROM claim",
   "SELECT payer,COUNT(*)AS amount FROM claim GROUP BY payer"]
  for query in queries:
   with self.subTest(query=query):
    result=self.sql(query);self.assertTrue(any('definitions:'in e for e in self.issues(self.answer(result))))
 def test_scalar_prose_cannot_smuggle_a_provider_monetary_basis(self):
  result=self.sql(self.cash_sql());a=self.answer(result)
  old=self.sql('SELECT SUM(allowed_cents) AS provider_amount FROM claim')
  a['computed_prose']=[{'field':'headline','sources':{'value':{'binding':{'result_view':old['result_view'],'row':0,'column':'provider_amount'}}}}]
  self.assertTrue(any('definitions:'in e for e in self.issues(a)))
 def test_existing_typed_scalar_composition_keeps_the_same_source_role_proof(self):
  paid=self.sql('SELECT payer_id AS payer,SUM(paid_cents)AS payer_paid FROM payer_claim WHERE counts_in_metrics=1 GROUP BY payer_id')
  cash=self.sql(self.cash_sql())
  args={'base':paid['result_view'],'keys':['payer'],'joins':[{'result':cash['result_view'],
   'as':'cash','on':{'payer':'payer'},'mode':'one','columns':['amount']}]}
  result=self.tools({'name':'compose_table','input':args});self.assertFalse(result.get('error'))
  self.calls.append({'name':'compose_table','input':args,'result':result})
  self.assertEqual(self.issues(self.answer(result)),[])
  changed=copy.deepcopy(self.calls);changed[-1]['result']['assembly']['base_result_digest']='changed'
  self.assertTrue(F.premise_issues(self.fixture.path,QUESTION,self.graph(),native_calls=changed,answer=self.answer(result)))
 def test_shared_cash_cte_keeps_complete_attributed_and_unlinked_branches(self):
  query="""WITH a AS(SELECT patient_id,json_extract(payload,'$.claim_id')cid,
   json_extract(payload,'$.allocated_cents')amt FROM record WHERE stream='payer_cash_allocations')
   SELECT c.payer,SUM(a.amt)cash FROM a JOIN claim c ON c.claim_id=a.cid AND c.patient_id=a.patient_id GROUP BY c.payer
   UNION ALL SELECT 'Unlinked',SUM(a.amt)FROM a LEFT JOIN claim c ON c.claim_id=a.cid WHERE c.claim_id IS NULL"""
  result=self.sql(query);self.assertEqual(self.issues(self.answer(result)),[])
  result=self.sql(query.replace("stream='payer_cash_allocations'","stream='payer_cash_allocations' OR stream='patient_payments'"))
  self.assertTrue(any('definitions:'in e for e in self.issues(self.answer(result))))
 def test_compiled_scalar_checks_its_column_and_keeps_unrelated_sources_in_history(self):
  cash=self.sql(self.cash_sql());a=self.answer(cash)
  mixed=self.sql(f"SELECT (SELECT SUM(amount)FROM {cash['result_view']}) AS cash_total,(SELECT SUM(allowed_cents)FROM claim)AS unasked_amount")
  for field,valid in [('cash_total',True),('unasked_amount',False)]:
   a['computed_prose']=[{'field':'headline','sources':{'value':{'binding':{
    'result_view':mixed['result_view'],'row':0,'column':field}}}}]
   with self.subTest(field=field):self.assertEqual(not bool(self.issues(a)),valid)
 def test_cash_record_classification_cardinality_is_not_a_provider_claim_count(self):
  query="""SELECT MAX(n)AS maximum FROM(SELECT r.record_id,COUNT(DISTINCT
   CASE WHEN c.claim_id IS NULL THEN 'unlinked' WHEN c.patient_id<>r.patient_id THEN 'wrong_subject' ELSE 'linked' END)n
   FROM record r LEFT JOIN claim c ON c.claim_id=json_extract(r.payload,'$.claim_id')
   WHERE r.stream='payer_cash_allocations'GROUP BY r.record_id)"""
  result=self.sql(query);self.assertEqual(self.issues(self.answer(result)),[])
 def test_current_review_locates_source_definition_before_paid_votes_or_binding_repairs(self):
  result=self.sql('SELECT payer,SUM(allowed_cents)AS allowed FROM claim GROUP BY payer');a=self.answer(result)
  a['facts']={'count':'4 groups.'}
  with patch.object(G,'ready',side_effect=AssertionError('No provider gate before source definition')):
   review=B.review_final(self.fixture.stack,QUESTION,a,self.calls,self.tools,ledger=None,as_of='2029-08-15')
  self.assertEqual(review['repair_kind'],'source_definition');self.assertNotIn('prose_repair',review)
  self.assertEqual(a['table'],result['rows'])
 def test_newly_compiled_provider_scalar_fails_before_a_model_vote(self):
  cash=self.sql(self.cash_sql());a=self.answer(cash)
  amount=self.sql('SELECT SUM(allowed_cents) AS provider_amount FROM claim')
  a['headline']={'template':'Recorded amount {v} cents.','bindings':{'v':{
   'result_view':amount['result_view'],'row':0,'column':'provider_amount'}}}
  with patch.object(G,'ready',side_effect=AssertionError('No vote on an unasked source')):
   review=B.review_final(self.fixture.stack,QUESTION,a,self.calls,self.tools,ledger=None,as_of='2029-08-15')
  self.assertEqual(review['repair_kind'],'source_definition')
 def test_current_schema_and_exact_query_receipts_still_fail_closed(self):
  source=self.sql(self.cash_sql());result=self.sql('SELECT payer,amount FROM '+source['result_view']);a=self.answer(result)
  self.assertEqual(self.issues(a),[])
  changed=copy.deepcopy(self.calls);changed[-1]['result']['executed_query']+=' LIMIT 1'
  self.assertTrue(F.premise_issues(self.fixture.path,QUESTION,self.graph(),native_calls=changed,answer=a))
  schema=F._schema(self.fixture.path);schema['claim'].remove('payer')
  self.assertTrue(F.premise_issues(self.fixture.path,QUESTION,self.graph(),native_calls=self.calls,answer=a,schema=schema))
  plan={'query':'SELECT SUM(allowed_cents) FROM claim WHERE allowed_cents>0','provider_lookup_only':True}
  self.assertTrue(superseded(QUESTION,plan,F._schema(self.fixture.path)))

if __name__=='__main__':unittest.main()
