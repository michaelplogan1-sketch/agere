"""Complete dependency fallback preserves abstention, fault and cost semantics."""
import copy,json,os,unittest,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B
from test_complete_claim_composition import packet


class SourceReview(unittest.TestCase):
    def verdict(self,p):
        return {'supported_claim_ids':[c['id']for c in p['claim_manifest']],
            'findings':[],'joint_support':'supported','joint_reason':'Dated consultation and medication statement match the source; current care remains unknown.'}

    def reviewer(self,p,ledger=None):
        with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription','ANTHROPIC_API_KEY':'subscription-transport'}),patch(
            'clinical_core.agere.clinical.legacy_proposers._send',return_value=lambda _:None),patch(
            'clinical_core.agere.clinical.semantic_analyst.tool_loop',return_value={
                'answer':self.verdict(p),'model':'authored-reviewer','new_paid_api_calls':1,'estimated_cost_usd':None}):
            return B.frontier_source_review(SimpleNamespace(),'Report dated care roles and medication statements.',p,ledger=ledger)

    def test_whole_dependency_reaches_frontier_once_and_global_uncertainty_remains(self):
        p=packet();summary={**p,'id':'summary','claim_manifest':[],'patient_dependency_bound':False}
        for global_label in ('supported','unknown'):
            def typed(q,options,evidence,**kw):
                label=global_label if evidence[0]['id']=='summary'else'unknown'
                return {'label':label,'decided_by':'authored'if label=='supported'else None,'steps':[]}
            from clinical_core.agere.gateway import BudgetLedger
            with tempfile.TemporaryDirectory()as folder,patch.object(self,'reviewer',wraps=self.reviewer)as frontier:
                ledger=BudgetLedger(Path(folder)/'attempts.sqlite3')
                try:out=B.check_support_packets([summary,p],typed,as_of=p['date'],ledger=ledger,frontier_decider=frontier)
                finally:ledger.close()
            self.assertEqual(frontier.call_count,1);self.assertEqual(out['label'],global_label)
            result=out['packets'][1]
            self.assertEqual(result['label'],'supported');self.assertEqual(result['typed_review']['label'],'unknown')
            self.assertIsNone(result['calibrated_probability']);self.assertFalse(result['clinical_qualification'])
            self.assertEqual(out['frontier_review_calls'],1);self.assertFalse(out['frontier_review_cost_known'])
            self.assertIsNone(out['frontier_review_cost_usd'])

    def test_lead_candidate_sends_one_frontier_review_before_the_rest(self):
        import threading
        from clinical_core.agere.gateway import BudgetLedger
        base=packet();summary={**base,'id':'summary','claim_manifest':[],'patient_dependency_bound':False}
        patients=[{**base,'id':'patient_'+str(i)}for i in range(6)]
        def typed(q,options,evidence,**kw):
            label='supported'if evidence[0]['id']=='summary'else'unknown'
            return {'label':label,'decided_by':'authored'if label=='supported'else None,'steps':[]}
        def run(first_label,flag):
            # Patches are entered once around the whole schedule: entering them inside worker threads is not thread-safe.
            sent=[];guard=threading.Lock()
            def loop(user,*a,**kw):
                claims=json.loads(user)['claim_manifest']
                with guard:sent.append(1);label=first_label if len(sent)==1 else'supported'
                verdict={'supported_claim_ids':[c['id']for c in claims],'findings':[],'joint_support':'supported','joint_reason':'Authored'}
                if label!='supported':
                    verdict.update(supported_claim_ids=[c['id']for c in claims][1:],joint_support=label,findings=[{
                        'claim_id':claims[0]['id'],'label':label,'reason':'Authored fault','evidence_paths':['/source_contexts/0/text']}])
                return {'answer':verdict,'model':'authored-reviewer','new_paid_api_calls':1,'estimated_cost_usd':None}
            def frontier(p,ledger=None):return B.frontier_source_review(SimpleNamespace(),'Report dated care roles and medication statements.',p,ledger=ledger)
            with tempfile.TemporaryDirectory()as folder,patch.dict(os.environ,{'AGERE_FRONTIER_REVIEW_LEAD':flag,
                    'AGERE_CLAUDE_TRANSPORT':'subscription','ANTHROPIC_API_KEY':'subscription-transport'}),patch(
                    'clinical_core.agere.clinical.legacy_proposers._send',return_value=lambda _:None),patch(
                    'clinical_core.agere.clinical.semantic_analyst.tool_loop',side_effect=loop):
                ledger=BudgetLedger(Path(folder)/'attempts.sqlite3')
                try:return B.check_support_packets([summary,*patients],typed,as_of=base['date'],ledger=ledger,frontier_decider=frontier),sent
                finally:ledger.close()
        out,sent=run('not_established','on')
        self.assertEqual(len(sent),1);self.assertEqual(len(out['failed_packet_ids']),1)
        self.assertFalse(out['all_dependencies_checked']);self.assertEqual(len(out['unchecked_packet_ids']),5)
        waiting=[r for r in out['packets']if r.get('status')=='awaiting_dependency_repair']
        self.assertEqual(len(waiting),5);self.assertTrue(all(r['label']=='unknown'and not r.get('decided_by')for r in waiting))
        out,sent=run('supported','on')
        self.assertEqual(len(sent),6);self.assertEqual(out['label'],'supported');self.assertEqual(out['unchecked_packet_ids'],[])
        out,sent=run('not_established','off')
        self.assertGreater(len(sent),1)  # today's behavior: in-flight packets are already sent

    def test_claim_scope_candidate_supplies_only_inputs_the_unsettled_fields_read(self):
        from clinical_core.contracts import digest
        row=lambda **k:{'patient_id':'A',**k}
        final=row(referral_json='structured referral',author_json='structured authors',findings='no attendance statement found')
        evidence=[row(referral_json='structured referral',author_json='structured authors'),          # sql base
                  row(review_status='needs_review',documents_reviewed=3,source_findings_json='[]',source_clinicians_json='[x]',diagnostic='unused'),
                  final,row(source_navigation_scope='30 documents unexamined')]
        views=['base','review','final',None];tools=['query_sql','review_documents','compose_table','plan_evidence_expansion']
        state={'id':'patient_A','patient_id':'A','final_result_view':'final','answer_rows':[final],'evidence':evidence,
            'evidence_provenance':[{'row_digest':digest(e),'origins':[{'kind':'executed_native_result','result_view':v,'tool':t}]}for e,v,t in zip(evidence,views,tools)],
            'native_calculation_contracts':[{'result_view':'base','tool':'query_sql','input':{'sql':'SELECT * FROM referral'}},
                {'result_view':'review','tool':'review_documents','input':{'questions':'{}'}},
                {'result_view':'final','tool':'compose_table','input':{'base':'base','keys':['patient_id'],
                    'joins':[{'result':'review','as':'findings','items_from':'source_findings_json','mode':'collect','on':{'patient_id':'patient_id'}}]}}],
            'source_contexts':[],'review_scope':[{'questions':{'q':'attendance'}}]}
        pending=[{'id':'claim_2','field':'findings','path':['answer_rows',0,'findings']}]
        scoped=B.claim_scoped_state(copy.deepcopy(state),pending)
        self.assertEqual(scoped['claim_scope']['reads'],{'final':['findings'],'review':['source_findings_json']})
        self.assertEqual(scoped['claim_scope']['omitted_result_views'],['base'])
        self.assertEqual(scoped['evidence'],[row(review_status='needs_review',documents_reviewed=3,source_findings_json='[]'),
            row(findings='no attendance statement found'),row(source_navigation_scope='30 documents unexamined')])
        self.assertEqual(scoped['answer_rows'],[row(referral_json={'settled_field_value_digest':digest('structured referral')},
            author_json={'settled_field_value_digest':digest('structured authors')},findings='no attendance statement found')])
        self.assertEqual([c['result_view']for c in scoped['native_calculation_contracts']],['review','final'])
        self.assertEqual(scoped['review_scope'],state['review_scope'])
        self.assertNotIn('structured authors',json.dumps(scoped));self.assertNotIn('unused',json.dumps(scoped))
        # A field copied from SQL keeps every column of every result that SQL names.
        chained=copy.deepcopy(state);chained['native_calculation_contracts'][0]['input']['sql']='SELECT * FROM review'
        sql_scoped=B.claim_scoped_state(chained,[{'id':'claim_0','field':'referral_json','path':['answer_rows',0,'referral_json']}])
        self.assertEqual(sql_scoped['claim_scope']['reads'],{'base':['referral_json'],'final':['referral_json'],'review':'all columns'})
        self.assertIn('unused',json.dumps(sql_scoped['evidence']))
        # Anything not fully explicit leaves the complete packet in place.
        for change in ({'final_result_view':'unavailable'},{'source_contexts':[{'text':'raw document'}]},{'computed_claims':[{'field':'x'}]}):
            self.assertIsNone(B.claim_scoped_state({**copy.deepcopy(state),**change},pending))
        unknown=copy.deepcopy(state);unknown['native_calculation_contracts'][2]['tool']='execute_analysis'
        self.assertIsNone(B.claim_scoped_state(unknown,pending))
        # The flag gates the whole behavior and the dispatched manifest stays the unsettled claims.
        p=packet();checked={'field_review':{}}
        with patch.object(B,'bound_field_support',return_value=[{'claim_id':p['claim_manifest'][0]['id']}]):
            with patch.dict(os.environ,{'AGERE_FRONTIER_CLAIM_SCOPE':'off'}),patch.object(B,'claim_scoped_state')as scope:
                B.unresolved_source_packet(p,checked);scope.assert_not_called()
            with patch.dict(os.environ,{'AGERE_FRONTIER_CLAIM_SCOPE':'on'}):
                target,resolved=B.unresolved_source_packet(p,checked)
        self.assertEqual(resolved,[p['claim_manifest'][0]['id']]);self.assertEqual(target['claim_manifest'],p['claim_manifest'][1:])

    def test_deciding_fault_missing_evidence_native_repair_and_partition_do_not_escalate(self):
        base={'id':'patient_A','label':'unknown','decided_by':None}
        changes=[({}, {'label':'contradicted','decided_by':'jev'}),({}, {'label':'supported','decided_by':'jev'}),
            ({'dependency_complete':False},{}),({'source_context_complete':False},{}),
            ({'required_source_packet_ids':['child']},{}),({'source_dependency_parent_id':'parent'},{}),
            ({},{'status':'requires_native_program_repair'}),({},{'status':'awaiting_semantic_dependency'}),
            ({},{'routed_judgment':{'label':'contradicted','decided_by':'jev'}}),
            ({},{'claim_feedback':[{'label':'wrong_role','decided_by':'jev'}]})]
        for source_change,review_change in changes:
            p={**packet(),**source_change};old={**base,**review_change}
            with patch.object(self,'reviewer',wraps=self.reviewer)as frontier:
                out,r=B.resolve_source_abstention(p,old,frontier,ledger=None)
            self.assertEqual(out,old);self.assertIsNone(r);frontier.assert_not_called()

    def test_source_fault_is_localized_and_invalid_or_unavailable_output_stays_unknown(self):
        p=packet();good=self.verdict(p);fault=copy.deepcopy(good)
        fault.update(supported_claim_ids=['claim_1'],joint_support='contradicted',findings=[{
            'claim_id':'claim_0','label':'contradicted','reason':'Authored fault control',
            'evidence_paths':['/source_contexts/0/text']}])
        for kind,verdict in [('fault',fault),('omitted',{**good,'supported_claim_ids':[]}),('unavailable',{})]:
            with patch.object(self,'verdict',return_value=verdict):
                out,review=B.resolve_source_abstention(p,{'id':p['id'],'label':'unknown','decided_by':None},self.reviewer,ledger=None)
            self.assertEqual(out['label'],'contradicted'if kind=='fault'else'unknown')
            if kind=='fault':
                self.assertEqual(out['claim_feedback'][0]['path'],['answer_rows',0,'relationship'])
                from clinical_core.contracts import digest
                self.assertEqual(out['field_review']['packet_digest'],digest(p['text']))
                self.assertEqual(out['field_review']['manifest_digest'],digest(p['claim_manifest']))

    def test_wrong_patient_or_embedded_source_gap_never_dispatches(self):
        for key,value in [('patient_id','B'),('source_context_gaps',['missing note'])]:
            from clinical_core.agere.deliverables.receipts import uncompact_json
            p=packet();state=uncompact_json(json.loads(p['text']));state[key]=value;p['text']=json.dumps(state)
            with self.assertRaises(ValueError):self.reviewer(p)

    def test_actual_subscription_limit_is_propagated(self):
        from clinical_core.agere.gateway import ProviderUnavailable
        def limited(*a,**kw):raise ProviderUnavailable('subscription limit',kind='rate_limit',attempts=[{'status':'failed'}])
        with self.assertRaises(ProviderUnavailable):
            B.resolve_source_abstention(packet(),{'label':'unknown','decided_by':None},limited,ledger=None)


class VerifiedSourceComposition(unittest.TestCase):
    def fixture(self):
        from test_final_claim_support import call
        question='Show the received relationship, preserving uncertainty.'
        rows=[{'patient_id':'A','relationship':'One-time consultation in 2029; ongoing care unknown.'}]
        answer={'headline':'Received relationship; ongoing care remains unknown.','table':rows,'table_from_tool':'roles'}
        packets=B.support_packets(question,answer,[call('roles',rows)],[],'2030-05-27')
        def typed(q,options,evidence,**kw):
            return {'label':'unknown'if evidence[0]['id']=='summary'else'supported',
                'decided_by':None if evidence[0]['id']=='summary'else'authored','steps':[]}
        support=B.check_support_packets(packets,typed,as_of='2030-05-27',ledger=None)
        return question,packets,support,rows

    def test_global_review_receives_exact_restored_values_and_cannot_certify_sources(self):
        from clinical_core.agere.deliverables.receipts import uncompact_json
        question,packets,support,rows=self.fixture();original=copy.deepcopy(packets)
        combined=B.compose_verified_summary(packets,support)
        state=uncompact_json(json.loads(combined['text']))
        self.assertEqual(state['all_final_rows'],rows);self.assertEqual(packets,original)
        self.assertFalse(state['verified_source_composition']['clinical_qualification'])
        def reviewer(user,*a,**kw):
            data=json.loads(user);self.assertEqual(data['original_question'],question)
            self.assertEqual(uncompact_json(data['packet_state'])['all_final_rows'],rows)
            return {'answer':{'coverage':{'label':'answers_fully','reason':'Exact requested relationship and uncertainty.','unaddressed_request_quotes':[]},
                'supported_claim_ids':[c['id']for c in combined['claim_manifest']],'findings':[],
                'joint_support':'supported','joint_reason':'Global interpretation matches exact source-bound values.'},
                'model':'authored','new_paid_api_calls':0,'estimated_cost_usd':0}
        with patch.dict(os.environ,{'AGERE_CLAUDE_TRANSPORT':'subscription','ANTHROPIC_API_KEY':'subscription-transport'}),patch(
            'clinical_core.agere.clinical.legacy_proposers._send',return_value=lambda _:None),patch(
            'clinical_core.agere.clinical.semantic_analyst.tool_loop',side_effect=reviewer):
            out=B.frontier_composed_review(SimpleNamespace(),question,packets,support,ledger=None)
        self.assertEqual(out['status'],'reviewed');self.assertFalse(out['clinical_qualification'])

    def test_unknown_fault_changed_source_and_missing_packet_block_global_review(self):
        for change in ('unknown','fault','changed_source','missing_packet','global_fault','wrong_date'):
            _,ps,s,_=self.fixture()
            if change=='unknown':s['packets'][1].update(label='unknown',decided_by=None)
            elif change=='fault':s['packets'][1].update(label='contradicted',decided_by='authored')
            elif change=='changed_source':ps[1]['text']+=' '
            elif change=='missing_packet':s['packets'].pop()
            elif change=='global_fault':s['packets'][0].update(label='contradicted',decided_by='authored')
            else:
                from clinical_core.agere.deliverables.receipts import uncompact_json
                from clinical_core.contracts import digest
                state=uncompact_json(json.loads(ps[1]['text']));state['as_of']='2028-01-01';ps[1]['text']=json.dumps(state)
                s['packets'][1]['packet_digest']=digest(ps[1]['text'])
            with self.subTest(change=change),self.assertRaises(ValueError):B.compose_verified_summary(ps,s)


if __name__=='__main__':unittest.main()
