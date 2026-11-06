"""Native primitive copying and semantic support are separate obligations."""
import copy,json,unittest
from clinical_core.agere.clinical import backstop_agent as B
from clinical_core.agere.clinical.evidence_projection import coverage_answer

class NativeTableCopy(unittest.TestCase):
    def setUp(self):
        self.columns=['patient_id','account_id','paid_cents','latest_date','latest_status']
        self.rows=[['P1','A1',11,'2029-12-31','open'],['P1','A2',None,None,'patient_credit'],['P2','A3',-2,'2030-01-01','open']]
        self.call={'name':'query_sql','input':{'sql':'SELECT patient_id,account_id,paid_cents,latest_date,latest_status FROM received_accounts'},'result':{
            'result_view':'agere_result_1','columns':self.columns,'rows':self.rows,'table_rows':3,
            'executed_query':'SELECT patient_id,account_id,paid_cents,latest_date,latest_status FROM received_accounts',
            'native_sql_contract':B.NATIVE_SQL_CONTRACT,
            'source_contracts':{'as_of':'2030-05-27','source_digest':'synthetic-source',
                'relations':[{'name':'received_accounts','columns':self.columns}],'definitions':[]}}}
        self.answer={'headline':'Received account values.','facts':{},'table_from_tool':'agere_result_1',
            'table_binding':{'result_view':'agere_result_1','rows':3,'columns':self.columns},
            'table':[dict(zip(self.columns,r))for r in self.rows]}
    def review(self):return B.native_table_copy_review(self.answer,[self.call],[],'2030-05-27')
    def test_all_primitive_rows_and_null_signed_amounts_are_checked_without_sampling(self):
        p=self.review();self.assertEqual(p['rows'],3);self.assertEqual(p['columns'],self.columns)
        self.assertIn('transport fidelity only',p['scope']);self.assertEqual(len(p['calculation_contracts']),1)
        self.assertEqual(self.answer['table'][1]['paid_cents'],None)
    def test_changed_omitted_duplicated_or_reordered_cell_never_gets_copy_proof(self):
        original=copy.deepcopy(self.answer)
        for mutation in ('changed','omitted','duplicate','reorder','extra_column'):
            self.answer=copy.deepcopy(original)
            if mutation=='changed':self.answer['table'][0]['paid_cents']=12
            if mutation=='omitted':self.answer['table'].pop()
            if mutation=='duplicate':self.answer['table'].append(self.answer['table'][0])
            if mutation=='reorder':self.answer['table'].reverse()
            if mutation=='extra_column':self.answer['table'][0]['other']=0
            with self.subTest(mutation=mutation):self.assertIsNone(self.review())
    def test_python_bool_int_equality_cannot_forge_cell_fidelity(self):
        self.rows[0][2]=1;self.answer['table'][0]['paid_cents']=True;self.assertIsNone(self.review())
    def test_string_notes_and_structured_findings_keep_source_review(self):
        for field,value in [('reason','Patient reports no financial hardship.'),('latest_status','no current treatment'),('latest_date','not dated'),('findings',[{'quote':'stopped'}])]:
            with self.subTest(field=field):
                a=copy.deepcopy(self.answer);a['table'][0][field]=value
                self.assertIsNone(B.native_table_copy_review(a,[self.call],[],'2030-05-27'))
    def test_documentary_or_composed_interpretation_cannot_be_certified_as_copy(self):
        for key,value in [('sources',[{'record_id':'R1'}]),('row_prose',{}),('computed_prose',[{'entity_key':{'patient_id':'P1'}}])]:
            a=copy.deepcopy(self.answer);a[key]=value
            with self.subTest(key=key):self.assertIsNone(B.native_table_copy_review(a,[self.call],[],'2030-05-27'))
        self.assertIsNone(B.native_table_copy_review(self.answer,[self.call],[{'rows':[]}],'2030-05-27'))
    def test_missing_truncated_foreign_clock_or_uncontracted_result_fails_closed(self):
        original=copy.deepcopy(self.call)
        for change in ('truncated','rowcount','error','contract','source','clock','relations'):
            self.call=copy.deepcopy(original);r=self.call['result']
            if change=='truncated':r['truncated']=True
            if change=='rowcount':r['table_rows']=4
            if change=='error':r['error']='failed'
            if change=='contract':r['native_sql_contract']='untrusted'
            if change=='source':r['source_contracts'].pop('source_digest')
            if change=='clock':r['source_contracts']['as_of']='2029-01-01'
            if change=='relations':r['source_contracts']['relations']=[]
            with self.subTest(change=change):self.assertIsNone(self.review())
    def test_duplicate_native_view_identity_and_unresolved_root_are_rejected(self):
        self.assertIsNone(B.native_table_copy_review(self.answer,[self.call,self.call],[],'2030-05-27'))
        self.answer['table_from_tool']='missing';self.assertIsNone(self.review())
    def test_complete_contract_without_optional_row_count_is_accepted(self):
        self.call['result'].pop('table_rows');self.assertEqual(self.review()['rows'],3)
    def test_numeric_classifiers_keep_the_existing_lineage_qualification_path(self):
        self.answer['table']=[{'patient_id':'P1','clinical_flag':1}]
        self.assertIsNone(self.review())
    def test_free_text_search_does_not_replace_complete_source_context_review(self):
        self.call['input']['sql']+=" WHERE note_text LIKE '%cannot afford%'"
        self.assertIsNone(self.review())
    def test_bound_global_scalar_value_and_full_source_row_are_revalidated(self):
        self.answer['facts']={'paid':'Received 11 cents.'};self.answer['computed_prose']=[{
            'field':'facts.paid','rendered':'Received 11 cents.','template':'Received {n} cents.','bindings':{'n':{'result_view':'agere_result_1','row':0,'column':'paid_cents'}},
            'sources':{'n':{'binding':{'result_view':'agere_result_1','row':0,'column':'paid_cents'},'value':11,'source_row':self.answer['table'][0].copy()}}}]
        self.assertIsNotNone(self.review());self.answer['computed_prose'][0]['sources']['n']['value']=99;self.assertIsNone(self.review())
    def test_coverage_keeps_all_requested_columns_and_method_without_row_votes(self):
        p=self.review();out=coverage_answer(self.answer,native_table=p)
        self.assertEqual(out['table'],[]);self.assertEqual(out['native_table']['rows'],3)
        self.assertEqual(out['native_table']['columns'],self.columns);self.assertEqual(len(self.answer['table']),3)
        self.assertIn('complete requested interpretation',out['coverage_contract']['scope'])
    def test_one_semantic_packet_cannot_accept_wrong_calculation_just_because_values_copy(self):
        packets=B.support_packets('Reconcile account balances',self.answer,[self.call],[],'2030-05-27')
        self.assertEqual(len(packets),1);self.assertTrue(packets[0]['native_table_copy']['meaning_review_required'])
        calls=[]
        def reject(*args,**kwargs):
            calls.append(args);return {'label':'contradicted','decided_by':'synthetic-semantic-review','probabilities':{'contradicted':1.},'steps':[]}
        result=B.check_support_packets(packets,reject,as_of='2030-05-27',ledger=None)
        self.assertEqual(len(calls),1);self.assertEqual(result['label'],'contradicted')
        self.assertEqual(result['failed_packet_ids'],['summary'])
    def test_unknown_semantics_is_not_promoted_to_supported(self):
        packets=B.support_packets('Reconcile account balances',self.answer,[self.call],[],'2030-05-27')
        result=B.check_support_packets(packets,lambda *a,**k:{'label':'unknown','decided_by':None,'steps':[]},as_of='2030-05-27',ledger=None)
        self.assertEqual(result['label'],'unknown');self.assertIsNone(result['decided_by'])
    def test_multiple_global_claims_use_existing_fingerprint_bound_field_reviewer(self):
        self.answer['facts']={'balance':'The claimed equation is wrong.'}
        packets=B.support_packets('Reconcile account balances',self.answer,[self.call],[],'2030-05-27')
        from clinical_core.agere.deliverables.receipts import uncompact_json
        state=uncompact_json(json.loads(packets[0]['text']))
        self.assertEqual(state['answer']['facts'],self.answer['facts'])
        def fields(question,named,evidence,**kwargs):
            return {'judgments':{k:{'label':'contradicted'if k=='fact_0'else'supported',
                'decided_by':'synthetic-review','probability':1.}for k in named},'steps':[]}
        result=B.check_support_packets(packets,lambda *a,**k:self.fail('Located fault must prevent joint approval'),
            as_of='2030-05-27',ledger=None,field_decider=fields)
        self.assertEqual(result['label'],'contradicted');self.assertEqual(result['claim_feedback'][0]['field'],'facts.balance')

if __name__=='__main__':unittest.main()
