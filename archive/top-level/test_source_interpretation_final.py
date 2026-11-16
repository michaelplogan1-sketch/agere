"""Final native/machine conjunction, coverage, source review and saved result."""
import copy,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import assertion_source_review as R,backstop_agent as B,system_one
from clinical_core.agere.clinical import evidence_projection as E,semantic_analyst as S,system_one_gate as G
from clinical_core.contracts import digest
import test_assertion_source_interpretation as fixtures


class FinalInterpretation(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.Interpretation();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.tools=self.fixture.tools;self.audit=self.fixture.audit
        with patch.object(system_one,'judge_fields',side_effect=self.fixture.source_judge):
            first=self.fixture.call()
            self.result=self.tools({'name':'review_documents','input':{'assertion_result_view':first['result_view']}})
        self.question='Review received source proposals, show source-interpreted qualifiers and unresolved documentary conflicts.'
        self.answer={'status':'answered','answer_type':'table','table_from_tool':self.result['result_view'],
            'headline':'Received proposals and source-supported machine interpretations are shown separately; opposing documentary reports remain unresolved.',
            'facts':{},'missing':[],'limitations':['Source interpretations are machine observations, not clinical adjudication.'],'sources':[],'workflow':[]}
        B.materialize_table(self.answer,self.tools.native_calls)

    def contract(self):return R.final_contract(self.tools,self.answer,self.tools.native_calls,self.tools.as_of)

    def test_complete_source_judgments_and_native_state_conjoin_without_provider_calls(self):
        before=digest(self.tools.native_calls);counter=dict(self.tools.subtool_accounting)
        with patch.object(system_one,'judge_fields',side_effect=AssertionError('No new source votes')):
            proof=self.contract()
        self.assertEqual(proof['rows'],17);self.assertEqual(proof['input_patients'],12)
        self.assertEqual(proof['status_counts'],{'original_tuple_supported':14,'changed_tuple_source_supported':3})
        self.assertFalse(proof['clinical_qualification']);self.assertEqual(proof['unreviewed_bound_source_accounts'],0)
        self.assertEqual(self.tools.subtool_accounting,counter);self.assertEqual(digest(self.tools.native_calls),before)
        for row in self.result['rows']:
            self.assertIn('source_date',row);self.assertIn('arrival_date',row);self.assertIn('source_evidence',row)
        view=E.coverage_answer(self.answer,native_interpretation=proof)
        self.assertEqual(view['table'],[]);self.assertEqual(view['source_interpretation_contract'],proof)
        self.assertLess(len(json.dumps(view)),len(json.dumps(self.answer)))
        packets=B.support_packets(self.question,self.answer,self.tools.native_calls,[],self.tools.as_of,native_interpretation=proof)
        self.assertEqual(len(packets),1);self.assertLess(packets[0]['bytes'],20000)
        self.assertEqual(packets[0]['representation'],'source_interpreted_result_and_global_meaning_review')

    def test_changed_omitted_or_clinically_promoted_results_cannot_get_compact_contract(self):
        original=copy.deepcopy(self.answer)
        for mutation in ('value','omit','duplicate','source_clock','new_field','prose'):
            self.answer=copy.deepcopy(original)
            if mutation=='value':self.answer['table'][0]['source_interpretation']['assertion']['subject']='family'
            elif mutation=='omit':self.answer['table'].pop()
            elif mutation=='duplicate':self.answer['table'].append(self.answer['table'][0])
            elif mutation=='source_clock':self.answer['table'][0]['source_date']='2030-01-01'
            elif mutation=='new_field':self.answer['table'][0]['clinical_eligibility']=True
            else:self.answer['row_prose']={}
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.contract()
        self.answer=original;proof=self.contract();proof['clinical_qualification']=True
        with self.assertRaises(ValueError):E.coverage_answer(self.answer,native_interpretation=proof)

    def test_actual_final_review_keeps_global_meaning_separate_from_bound_source_rows(self):
        seen=[]
        def support(q,options,evidence,**kw):
            from clinical_core.agere.deliverables.receipts import uncompact_json
            seen.append(uncompact_json(json.loads(evidence[0]['text'])))
            return {'label':'supported','decided_by':'authored-global-review','probabilities':{'supported':1.},'steps':[]}
        checker=SimpleNamespace(decide=support,judge_fields=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No individual source vote')))
        with(patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=checker),
             patch.object(G,'check',return_value={'label':'answers_fully','decided_by':'authored-coverage','p':1.,'calls':0,'cost_usd':0.}),
             patch.object(system_one,'judge_fields',side_effect=AssertionError('No source model call'))):
            result=B.review_final(self.tools.stack,self.question,self.answer,self.tools.native_calls,self.tools,
                ledger=self.tools.ledger,as_of=self.tools.as_of)
        self.assertEqual(result['status'],'confirmed',result)
        self.assertEqual(result['evidence_coverage']['model_view'],'source-interpreted-native-result/1')
        self.assertFalse(result['evidence_coverage']['full_results_supplied']);self.assertEqual(len(seen),1)
        self.assertIn('source_interpretation_contract',seen[0]['answer'])

    def test_frontier_preview_does_not_duplicate_operational_source_receipts(self):
        value=copy.deepcopy(self.result);meta=value['assertion_source_interpretation']
        meta['typed_checks']*=500;meta['documentary_groups']*=500;before=digest(value)
        shown=S.actor_result(value)
        self.assertEqual(digest(value),before)
        self.assertIsInstance(shown['assertion_source_interpretation']['typed_checks'],dict)
        self.assertLess(len(json.dumps(shown)),30000)

    def test_registered_interpretation_cannot_gain_clinical_authority_or_extra_claims(self):
        original=copy.deepcopy(self.result)
        for mutation in ('clinical','authority','extra'):
            self.result.clear();self.result.update(copy.deepcopy(original))
            target=next(r['source_interpretation']for r in self.result['rows']if r['source_support']=='not_supported')
            if mutation=='clinical':target['clinical_qualification']=True
            elif mutation=='authority':target['authority']='human_source_review'
            else:target['eligible_for_treatment']=True
            self.tools.semantic.register_native_result(self.result['result_view'],self.result)
            B.materialize_table(self.answer,self.tools.native_calls)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.contract()

    def test_global_uncertainty_does_not_become_confirmed_from_a_structured_result(self):
        checker=SimpleNamespace(decide=lambda *a,**kw:{'label':'unknown','decided_by':None,'probabilities':{'unknown':1.},'steps':[]},
            judge_fields=lambda *a,**kw:{'judgments':{},'steps':[]})
        with(patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=checker),
             patch.object(G,'check',return_value={'label':'answers_fully','decided_by':'authored','p':1.,'calls':0,'cost_usd':0.})):
            result=B.review_final(self.tools.stack,self.question,self.answer,self.tools.native_calls,self.tools,
                ledger=self.tools.ledger,as_of=self.tools.as_of)
        self.assertNotEqual(result['status'],'confirmed')

    def test_compiler_repair_composition_uses_bound_source_contract_without_resending_rows(self):
        self.answer['headline']='There are 1234567 received accounts.'
        seen=[]
        def composition(question,answer,**kw):
            seen.append(answer)
            return {'targets':[],'calls':0,'cost_usd':0.}
        with(patch.object(G,'ready',return_value=True),patch.object(G,'check_composition',side_effect=composition),
             patch.object(system_one,'judge_fields',side_effect=AssertionError('No repeated source reading'))):
            result=B.review_final(self.tools.stack,self.question,self.answer,self.tools.native_calls,self.tools,
                ledger=self.tools.ledger,as_of=self.tools.as_of)
        self.assertEqual(result['repair_kind'],'deterministic_compiler')
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0]['table'],[])
        self.assertEqual(seen[0]['source_interpretation_contract']['rows'],17)
        self.assertLess(len(json.dumps(seen[0])),30000)
        self.assertEqual(len(self.answer['table']),17)

    def test_pending_work_sees_completed_source_contract_without_waiving_new_work(self):
        proof=self.contract();seen=[]
        def judge(subject,questions,evidence,**kw):
            state=json.loads(evidence[0]['text']);seen.append(state)
            self.assertEqual(state['source_interpretation_contract'],proof)
            return {'judgments':{'missing_0':{'label':'verification_uncertain','probability':.99,'decided_by':'authored'},
                'missing_1':{'label':'pending_work','probability':.99,'decided_by':'authored'}},'steps':[]}
        self.answer['missing']=['Clinical adjudication remains unavailable.','Requested additional comparison has not run.']
        with patch.object(G,'s1',return_value=SimpleNamespace(judge_fields=judge)):
            out=G.check_pending_work(self.question,self.answer,self.tools.native_calls,as_of_date=self.tools.as_of,
                native_interpretation=proof)
        self.assertEqual(out['targets'],[{'path':['answer','missing',1],'text':self.answer['missing'][1]}])
        self.assertEqual(seen[0]['source_interpretation_contract']['unreviewed_bound_source_accounts'],0)
        self.assertLess(len(json.dumps(seen[0])),40000)

    def test_pending_work_rejects_changed_table_or_stale_source_contract_before_dispatch(self):
        proof=self.contract();self.answer['missing']=['Source interpretation remains uncertain.']
        stale=copy.deepcopy(proof);stale['as_of']='2029-01-01'
        with patch.object(G,'s1',return_value=SimpleNamespace(judge_fields=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No dispatch')))):
            with self.assertRaises(ValueError):
                G.check_pending_work(self.question,self.answer,self.tools.native_calls,as_of_date=self.tools.as_of,native_interpretation=stale)
            self.answer['table'][0]['source_date']='2029-01-01'
            with self.assertRaises(ValueError):
                G.check_pending_work(self.question,self.answer,self.tools.native_calls,as_of_date=self.tools.as_of,native_interpretation=proof)

    def test_unread_source_accounts_cannot_be_reported_as_completed_unknowns(self):
        fresh=fixtures.Interpretation();fresh.setUp();self.addCleanup(fresh.doCleanups)
        def prepared(s,q,e,**kw):return {'judgments':{key:{'label':'unknown','decided_by':None}for key in q},
                                       'steps':[{'provider':'jev','status':'prepared','cost_usd':0.}]}
        with patch.object(system_one,'judge_fields',side_effect=prepared):
            primary=fresh.call();out=fresh.tools({'name':'review_documents','input':{'assertion_result_view':primary['result_view']}})
        answer={**self.answer,'table_from_tool':out['result_view']};answer.pop('table_binding',None);answer.pop('table',None)
        with patch.object(G,'check',side_effect=AssertionError('Do not buy a global review for unread sources')):
            result=B.review_final(fresh.tools.stack,self.question,answer,fresh.tools.native_calls,fresh.tools,
                ledger=fresh.tools.ledger,as_of=fresh.tools.as_of)
        self.assertEqual(result['status'],'repair');self.assertEqual(result['repair_kind'],'unfinished_source_interpretation')

    def test_sql_can_flatten_every_interpreted_row_without_reopening_source_votes(self):
        query=(f"SELECT audit_account_id,patient_id,source_date,arrival_date,"
               "json_extract(proposed_assertion,'$.subject') AS received_subject,"
               "json_extract(source_interpretation,'$.assertion.subject') AS interpreted_subject,"
               "json_extract(source_interpretation,'$.assertion.clinical_date') AS clinical_date,"
               "json_extract(source_evidence,'$.quote') AS source_quote,"
               "json_extract(source_interpretation,'$.interpreted_conditional_effects') AS interpreted_effects "
               f"FROM {self.result['result_view']} ORDER BY audit_account_id")
        result=self.tools({'name':'query_sql','input':{'sql':query}})
        answer={**self.answer,'table_from_tool':result['result_view']};answer.pop('table_binding',None);answer.pop('table',None)
        B.materialize_table(answer,self.tools.native_calls)
        with patch.object(system_one,'judge_fields',side_effect=AssertionError('No new source judgment')):
            proof=R.final_contract(self.tools,answer,self.tools.native_calls,self.tools.as_of)
        self.assertEqual(proof['rows'],17);self.assertEqual(proof['native_projection']['executed_query'],query)
        self.assertEqual(proof['native_projection']['fields']['interpreted_subject']['source_column'],'source_interpretation')
        self.assertEqual(proof['status_counts'],{'original_tuple_supported':14,'changed_tuple_source_supported':3})
        self.assertTrue(all(r['clinical_date']is None for r in answer['table']))

    def test_sql_scope_changes_clock_swaps_and_unbound_expressions_are_not_compact_proofs(self):
        root=self.result['result_view']
        for query in [f'SELECT *FROM {root} LIMIT 1',f'SELECT *FROM {root} WHERE patient_id=\'p06\'',
                      f'SELECT audit_account_id,patient_id,arrival_date AS source_date FROM {root}',
                      f'SELECT audit_account_id,patient_id,source_date AS clinical_date FROM {root}',
                      f'SELECT audit_account_id,patient_id,1 AS clinically_qualified FROM {root}']:
            result=self.tools({'name':'query_sql','input':{'sql':query}})
            answer={'answer_type':'table','table_from_tool':result['result_view']};B.materialize_table(answer,self.tools.native_calls)
            with self.subTest(query=query),self.assertRaises(ValueError):
                R.final_contract(self.tools,answer,self.tools.native_calls,self.tools.as_of)


if __name__=='__main__':unittest.main()
