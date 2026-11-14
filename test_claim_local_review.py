"""Local source checks retain dependencies and report actionable field faults."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from clinical_core.agere.clinical import backstop_agent as B,execution_state as E,system_one_gate as G
from clinical_core.agere.deliverables.receipts import uncompact_json


class LocalReview(unittest.TestCase):
    def fixture(self):
        rows=[{'patient_id':'A','rasi_reason':[{'record_id':'n1','date':'2030-01-01','quote':'MRA held for potassium.'}],
               'current_assignment':'Unknown from this evidence.'},
              {'patient_id':'B','rasi_reason':[],'current_assignment':'Unknown from this evidence.'}]
        calls=[{'name':'query_sql','input':{'sql':'SELECT * FROM received'},'result':{'result_view':'base','rows':copy.deepcopy(rows)}},
            {'name':'query_sql','input':{'sql':'SELECT 4 AS definition'},'result':{'result_view':'reference','rows':[{'definition':4}]}},
            {'name':'query_sql','input':{'sql':'SELECT base.* FROM base CROSS JOIN reference'},'result':{'result_view':'final','rows':copy.deepcopy(rows)}}]
        answer={'table_binding':{'result_view':'final'},'table':copy.deepcopy(rows),'headline':'Current responsibility remains unknown.','facts':{}}
        return answer,calls

    def packets(self,answer,calls,reviews=None):
        return {p['id']:p for p in B.support_packets('Review the exact recorded care relationships.',answer,calls,reviews or [],'2030-05-27')}

    def test_global_wording_and_unrelated_scalar_edits_preserve_patient_requests(self):
        answer,calls=self.fixture();before=self.packets(answer,calls)
        answer['headline']='Dated roles are recorded; current responsibility is unknown.'
        calls.append({'name':'query_sql','input':{'sql':'SELECT 999 AS unrelated'},'result':{'result_view':'unrelated','rows':[{'unrelated':999}]}})
        after=self.packets(answer,calls)
        self.assertEqual(before['patient_A']['text'],after['patient_A']['text']);self.assertEqual(before['patient_B']['text'],after['patient_B']['text'])
        self.assertNotEqual(before['summary']['text'],after['summary']['text'])
        local=uncompact_json(json.loads(after['patient_A']['text']))
        self.assertEqual(local['shared_evidence'],[{'definition':4}]);self.assertNotIn('native_bindings',local)

    def test_an_actual_shared_dependency_change_invalidates_local_check(self):
        answer,calls=self.fixture();before=self.packets(answer,calls)
        calls[1]['result']['rows'][0]['definition']=5
        self.assertNotEqual(before['patient_A']['text'],self.packets(answer,calls)['patient_A']['text'])

    def test_executed_source_contracts_are_bound_to_rows_without_other_patient_values(self):
        answer,calls=self.fixture();before=self.packets(answer,calls)
        local=uncompact_json(json.loads(before['patient_B']['text']))
        self.assertEqual({c['result_view']for c in local['native_calculation_contracts']},{'base','reference','final'})
        self.assertTrue(all(b['row_digest']and b['origins']for b in local['evidence_provenance']))
        calls[0]['result']['rows'][0]['rasi_reason'][0]['quote']='A different source passage.'
        after=self.packets(answer,calls)
        self.assertNotEqual(before['patient_A']['text'],after['patient_A']['text'])
        self.assertEqual(before['patient_B']['text'],after['patient_B']['text'])
        calls[0]['input']['sql']='SELECT * FROM another_received_domain'
        self.assertNotEqual(before['patient_B']['text'],self.packets(answer,calls)['patient_B']['text'])

    def test_summary_receives_calculation_contracts_and_scalar_origins(self):
        answer,calls=self.fixture()
        before=self.packets(answer,calls)
        summary=uncompact_json(json.loads(before['summary']['text']))
        contracts={c['result_view']:c for c in summary['native_calculation_contracts']}
        self.assertEqual(contracts['reference']['input'],{'sql':'SELECT 4 AS definition'})
        self.assertEqual(summary['native_bindings'][1]['result_view'],'reference')
        self.assertEqual(summary['shared_evidence_provenance'][0]['origins'][0]['result_view'],'reference')
        self.assertIn('Earlier intermediate results',summary['source_precedence'])
        calls[1]['input']['sql']='SELECT 4 AS definition FROM another_source'
        after=self.packets(answer,calls)
        self.assertNotEqual(before['summary']['text'],after['summary']['text'])
        self.assertEqual(uncompact_json(json.loads(after['summary']['text']))['native_calculation_contracts'][1]['input'],calls[1]['input'])

    def test_unresolved_lineage_keeps_all_shared_evidence(self):
        answer,calls=self.fixture();answer['table_binding']['result_view']='unavailable'
        calls.append({'name':'query_sql','input':{},'result':{'result_view':'extra','rows':[{'extra':9}]}})
        local=uncompact_json(json.loads(self.packets(answer,calls)['patient_A']['text']))
        self.assertIn({'extra':9},local['shared_evidence'])

    def test_one_patients_source_change_does_not_invalidate_another_review(self):
        answer,calls=self.fixture();review={'questions':{'rasi_reason':'What is stated?'},
            'manifest':[{'patient_id':p,'document_digest':p} for p in ('A','B')],
            'rows':[{'patient_id':p,'review_json':'{}'}for p in ('A','B')]}
        before=self.packets(answer,calls,[review]);review['rows'][0]['review_json']='{"rasi_reason":[]}'
        after=self.packets(answer,calls,[review]);self.assertNotEqual(before['patient_A']['text'],after['patient_A']['text'])
        self.assertEqual(before['patient_B']['text'],after['patient_B']['text'])

    def test_direct_document_result_binds_only_to_its_patient(self):
        answer,calls=self.fixture();calls.append({'name':'read_document','input':{'record_id':'b1'},
            'result':{'patient_id':'B','record_id':'b1','text':'B has a documented account.'}})
        packets=self.packets(answer,calls)
        self.assertNotIn('B has a documented account.',packets['patient_A']['text'])
        self.assertIn('B has a documented account.',packets['patient_B']['text'])

    def test_legacy_document_judgments_partition_only_with_complete_native_identity(self):
        rows=[{'patient_id':'A','finding':'Unknown'},{'patient_id':'B','finding':'Unknown'}]
        judgments=[{'record_id':'a','date':'2029-01-01','label':'unknown','passage':'A denies current use.'},
                   {'record_id':'b','date':'2029-01-02','label':'taking','passage':'B reports taking it.'}]
        result={'result_view':'judged','question':'What use is reported?','documents':judgments,'latest':judgments[1],
            'labels':['taking'],'unknown_documents':1}
        calls=[{'name':'judge_documents','input':{},'result':result},
            {'name':'query_sql','input':{'sql':'SELECT * FROM judged'},'result':{'result_view':'final','rows':rows}}]
        answer={'table_binding':{'result_view':'final'},'table':rows}
        docs={rid:{'patient_id':pid,'date':date,'text':text,'available':date}for rid,pid,date,text in (
            ('a','A','2029-01-01','A denies current use.'),('b','B','2029-01-02','B reports taking it.'))}
        original=copy.deepcopy(calls)
        def packets(cache):return {p['id']:uncompact_json(json.loads(p['text']))for p in
            B.support_packets('Inspect reported use.',answer,calls,[],'2030-05-27',source_documents=cache)}
        got=packets(docs)
        for pid,other in (('A','B'),('B','A')):
            local=json.dumps(got['patient_'+pid]);self.assertIn(pid+' '+('denies'if pid=='A'else'reports'),local)
            self.assertNotIn(other+' '+('denies'if other=='A'else'reports'),local)
        self.assertEqual(got['summary']['native_document_judgment_contracts'][0]['global_metadata']['unknown_documents'],1)
        self.assertEqual(calls,original)
        for changed in ({}, {**docs,'b':{**docs['b'],'date':'2029-02-01'}},
                        {**docs,'b':{**docs['b'],'available':'2031-01-01'}}):
            with self.subTest(changed=changed):
                conservative=packets(changed)
                self.assertIn('B reports taking it.',json.dumps(conservative['patient_A']['shared_evidence']))

    def test_global_source_claim_receives_exact_cited_document_without_changing_patient_claims(self):
        answer,calls=self.fixture();calls.append({'name':'read_document','input':{'record_id':'n1'},
            'result':{'patient_id':'A','record_id':'n1','date':'2030-01-01','text':'Prior entry: MRA held for potassium.'}})
        before=self.packets(answer,calls)
        answer['facts']['dated_attribution']='The prior MRA hold is historical (n1).'
        citation={'record_id':'n1','date':'2030-01-01','quote':'Prior entry: MRA held for potassium.'}
        answer['sources']=[citation]
        after=self.packets(answer,calls);summary=uncompact_json(json.loads(after['summary']['text']))
        self.assertEqual(summary['cited_source_evidence'],[{'citation':citation,'executed_source_document':calls[-1]['result']}])
        only_cached=B.support_packets('Check source claims',answer,calls[:-1],[],'2030-05-27',source_documents={'n1':{k:v for k,v in calls[-1]['result'].items()if k!='record_id'}})
        self.assertEqual(uncompact_json(json.loads(only_cached[0]['text']))['cited_source_evidence'],summary['cited_source_evidence'])
        for pid in ('A','B'):self.assertEqual(before['patient_'+pid]['text'],after['patient_'+pid]['text'])
        for changed in ({**citation,'date':'2029-01-01'},{**citation,'quote':'Invented current contraindication.'}):
            answer['sources']=[changed]
            self.assertEqual(uncompact_json(json.loads(self.packets(answer,calls)['summary']['text']))['cited_source_evidence'],[])

    def test_bound_assertions_require_complete_matching_context_and_never_buy_a_vote_for_a_gap(self):
        from test_document_relations import question
        from clinical_core.agere.clinical import document_review as D
        answer,calls=self.fixture();text='Prior entry: MRA held for potassium. Current applicability is not documented.'
        doc={'patient_id':'A','record_id':'n1','date':'2030-01-01','text':text}
        quote='MRA held for potassium.';start=text.index(quote)
        finding=D.relation_finding('A','rasi_reason',question(),doc,{'start':start,'end':start+len(quote),'text':quote},
            {'polarity':'affirmed','temporality':'historical','subject':'patient'},'fixture')
        answer['table'][0]['rasi_reason']=[finding]
        def packets(cache):return {p['id']:p for p in B.support_packets('Check exact source',answer,calls,[],'2030-05-27',source_documents=cache)}
        complete=packets({'n1':doc});state=uncompact_json(json.loads(complete['patient_A']['text']))
        self.assertEqual(state['source_contexts'],[doc]);self.assertEqual(state['source_context_gaps'],[])
        self.assertTrue(complete['patient_A']['source_context_complete'])
        for cache in ({},{'n1':{**doc,'text':quote}},{'n1':{**doc,'patient_id':'B'}},{'n1':{**doc,'date':'2029-01-01'}}):
            with self.subTest(cache=cache):
                incomplete=packets(cache);self.assertFalse(incomplete['patient_A']['source_context_complete'])
                judge=Mock(side_effect=AssertionError('No vote can replace full source context'))
                out=B.check_support_packets([incomplete['patient_A']],judge,as_of='2030-05-27',ledger=None,field_decider=judge)
                self.assertEqual(out['label'],'unknown');judge.assert_not_called()
                self.assertEqual(complete['patient_B']['text'],incomplete['patient_B']['text'])

    def test_failed_packet_localizes_entity_error_and_preserves_supported_unknown(self):
        answer,calls=self.fixture();packet=self.packets(answer,calls)['patient_A'];seen=[]
        def decide(*args,**kw):return {'label':'contradicted','decided_by':'fixture','steps':[]}
        def fields(task,questions,evidence,**kw):
            seen.append(questions)
            return {'judgments':{key:{'label':'wrong_entity' if value['instructions']['claim']['field']=='rasi_reason' else 'supported','decided_by':'fixture'}
                for key,value in questions.items()},'steps':[]}
        out=B.check_support_packets([packet],decide,as_of='2030-05-27',ledger=None,field_decider=fields)
        self.assertEqual(len(out['claim_feedback']),1);failure=out['claim_feedback'][0]
        self.assertEqual(failure['field'],'rasi_reason');self.assertEqual(failure['source_record_ids'],['n1']);self.assertEqual(failure['result_view'],'final')
        self.assertEqual(failure['path'],['answer_rows',0,'rasi_reason']);self.assertTrue(seen)
        state=json.loads(E.messages('q',calls,feedback={'status':'repair','support':out})[0]['content'])
        self.assertEqual(state['current_review_feedback']['unresolved_dependencies'][0]['claim_feedback'],out['claim_feedback'])

    def test_clean_supported_unknown_does_not_trigger_extra_reading(self):
        answer,calls=self.fixture();judge=Mock(side_effect=AssertionError('No diagnostic calls for clean support'))
        out=B.check_support_packets(list(self.packets(answer,calls).values()),lambda *a,**kw:{'label':'supported','decided_by':'fixture','steps':[]},
            as_of='2030-05-27',ledger=None,field_decider=judge)
        self.assertEqual(out['label'],'supported');self.assertEqual(out['claim_feedback'],[]);judge.assert_not_called()

    def test_uncertain_packet_can_be_localized_without_lowering_thresholds(self):
        answer,calls=self.fixture();packet=self.packets(answer,calls)['patient_A']
        def fields(task,questions,evidence,**kw):
            return {'judgments':{key:{'label':'wrong_entity' if q['instructions']['claim']['field']=='rasi_reason' else 'supported','decided_by':'fixture'}for key,q in questions.items()},'steps':[]}
        out=B.check_support_packets([packet],lambda *a,**kw:{'label':'not_established','decided_by':None,'probabilities':{'not_established':.7},'steps':[]},
            as_of='2030-05-27',ledger=None,field_decider=fields)
        self.assertEqual(out['label'],'contradicted');self.assertEqual(out['claim_feedback'][0]['field'],'rasi_reason')

    def test_supported_local_fields_cannot_promote_an_unresolved_complete_check(self):
        answer,calls=self.fixture();packet=self.packets(answer,calls)['patient_A']
        def fields(task,questions,evidence,**kw):
            return {'judgments':{key:{'label':'supported','decided_by':'fixture'}for key in questions},'steps':[]}
        for broad in ({'label':'not_established','decided_by':None,'probabilities':{'not_established':.7},'steps':[]},
                      {'label':'contradicted','decided_by':'fixture','steps':[]}):
            out=B.check_support_packets([packet],lambda *a,**kw:broad,
                as_of='2030-05-27',ledger=None,field_decider=fields)
            self.assertEqual(out['label'],'unknown');self.assertIsNone(out['decided_by'])
            self.assertEqual(out['claim_feedback'],[])

    def test_supported_field_receipts_survive_without_promoting_complete_support(self):
        from clinical_core.contracts import digest
        answer,calls=self.fixture();packet=self.packets(answer,calls)['patient_A']
        def fields(task,questions,evidence,**kw):
            return {'judgments':{key:{'label':'supported','decided_by':'fixture','probability':.93,'threshold':.8}
                for key in questions},'steps':[{'provider':'fixture','status':'answered','request_hash':'retained-input','fields':list(questions)}]}
        broad={'label':'supported','decided_by':None,'probabilities':{'supported':.79},'steps':[]}
        out=B.check_support_packets([packet],lambda *a,**kw:broad,as_of='2030-05-27',ledger=None,field_decider=fields)
        self.assertEqual(out['label'],'unknown');self.assertIsNone(out['decided_by'])
        review=out['packets'][0]['field_review']
        self.assertEqual(review['supported_count'],2);self.assertEqual(review['unresolved_count'],0)
        self.assertEqual(review['remaining_obligation'],'complete_dependency_support')
        self.assertEqual(review['packet_digest'],digest(packet['text']))
        self.assertEqual(review['manifest_digest'],digest(packet['claim_manifest']))
        self.assertEqual({r['field']for r in review['supported_claims']},{'rasi_reason','current_assignment'})
        for row in review['supported_claims']:
            self.assertEqual(row['verification_observation']['state'],'supported')
            self.assertEqual(row['verification_observation']['model_judgment']['threshold'],.8)
            self.assertEqual(row['value_digest'],digest(B._claim_value(uncompact_json(json.loads(packet['text'])),row['path'])))
        state=json.loads(E.messages('q',calls,feedback={'status':'repair','support':out})[0]['content'])
        observed=state['current_review_feedback']['unresolved_dependencies'][0]['field_review']
        self.assertEqual(observed['supported_count'],2)
        self.assertEqual(observed['remaining_obligation'],'complete_dependency_support')
        self.assertNotIn('supported_claims',observed)  # full bindings remain in the native receipt

    def test_missing_or_unsettled_fields_never_receive_supported_receipts(self):
        answer,calls=self.fixture();packet=self.packets(answer,calls)['patient_A']
        def fields(task,questions,evidence,**kw):
            first=next(iter(questions))
            return {'judgments':{first:{'label':'supported','decided_by':None,'probability':.79,'threshold':.8}},'steps':[]}
        out=B.localize_support_failure(packet,fields,as_of='2030-05-27',ledger=None)
        self.assertEqual(out['field_review']['supported_count'],0)
        self.assertEqual(out['field_review']['unresolved_count'],2)
        self.assertEqual(len(out['feedback']),2)

    def test_verifier_outage_does_not_purchase_a_second_diagnostic(self):
        answer,calls=self.fixture();judge=Mock(side_effect=AssertionError('Outage is not a source fault'))
        out=B.check_support_packets([self.packets(answer,calls)['patient_A']],
            lambda *a,**kw:{'label':'unknown','decided_by':None,'steps':[{'provider':'fixture','status':'error'}]},
            as_of='2030-05-27',ledger=None,field_decider=judge)
        self.assertIsNone(out['decided_by']);judge.assert_not_called()

    def test_unresolved_provider_reading_is_retained_without_rewriting_and_outage_does_not_retry(self):
        answer,calls=self.fixture()
        calls[-1]['result']['columns']=list(calls[-1]['result']['rows'][0])
        answer.update(status='answered',answer_type='table',table_from_tool='final')
        answer.pop('table_binding');answer.pop('table')
        tools=SimpleNamespace(read={},document_reviews=[],rows_by_record={})
        def judge(task,questions,evidence,**kw):
            return {'judgments':{key:{'label':'unknown','decided_by':None}for key in questions},'steps':[]}
        for responded in (True,False):
            model=SimpleNamespace(decide=lambda *a,**kw:{'label':'unknown','decided_by':None,
                'probabilities':{'unknown':.9}if responded else {},
                'steps':[{'provider':'fixture','status':'answered'if responded else 'error','error':None if responded else 'outage'}]},judge_fields=judge)
            with self.subTest(responded=responded),patch.object(G,'ready',return_value=True),patch.object(G,'s1',return_value=model),patch.object(G,'check',return_value={'label':'answers_fully','calls':0,'cost_usd':0}):
                out=B.review_final(SimpleNamespace(),'Review the exact recorded care relationships.',copy.deepcopy(answer),calls,tools,ledger=None,as_of='2030-05-27')
                if responded:
                    self.assertEqual(out['status'],'repair')
                    self.assertEqual(out['retry_disposition'],'retain_partial_until_new_evidence')
                    self.assertEqual(out['completion']['verification'],'uncertain')
                    self.assertTrue(any(f['field']=='current_assignment'for f in out['completion']['unverified_fields']))
                    self.assertIn('no identified defect',out['feedback'])
                else:
                    self.assertEqual(out['status'],'unavailable');self.assertIsNone(out['feedback'])


class ObservationFeedback(unittest.TestCase):
    def test_inventory_cached_source_keeps_its_native_arrival_clock_on_recheck(self):
        import sqlite3
        tools=B.Tools.__new__(B.Tools);tools.db=sqlite3.connect(':memory:');tools.as_of='2030-05-27'
        tools.db.execute('CREATE TABLE record(record_id,patient_id,available_date)')
        tools.db.execute("INSERT INTO record VALUES ('n1','A','2030-01-08')")
        tools.read={'n1':{'patient_id':'A','date':'2030-01-01','text':'Exact original note.'}}
        try:
            doc=tools._doc('n1');self.assertEqual(doc['available'],'2030-01-08')
            self.assertEqual(doc['date'],'2030-01-01');self.assertEqual(doc['text'],'Exact original note.')
            doc['available']='2030-06-01'
            with self.assertRaisesRegex(ValueError,'not visible'):tools._doc('n1')
        finally:tools.db.close()

    def test_only_confident_singular_source_fault_suspends_its_machine_observation(self):
        from clinical_core.agere.clinical import span_repair as S
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)
            for ident in ['a','b','c','d','e']:
                S.remember_observation(path,ident,{'label':'finding'},['original-'+ident],assertion_id=ident)
            values={'disputed':{'assertion':{'id':'a'},'record_id':'n1'},
                    'bare':{'binding_version':'documentary-relation/1','id':'e','source':{'record_id':'n5'}},
                    'ordinary_id':{'id':'b','record_id':'n2'},
                    'uncertain':{'assertion':{'id':'b'},'record_id':'n2'},
                    'aggregate':[{'assertion':{'id':'c'}},{'assertion':{'id':'d'}}]}
            claims=[{'id':k,'field':k,'path':[k]}for k in values]
            from clinical_core.agere.deliverables.receipts import compact_json
            text=json.dumps(compact_json(values));packet={'id':'source','date':'2030-05-27','bytes':len(text),'text':text,'claim_manifest':claims}
            def fields(task,named,evidence,**kw):
                return {'steps':[],'judgments':{k:{'label':'unknown'if k=='uncertain'else'wrong_time',
                                                  'decided_by':None if k=='uncertain'else'fixture'}for k in named}}
            got=B.check_support_packets([packet],lambda *a,**k:{'label':'unknown','decided_by':None,'probabilities':{'unknown':.9},'steps':[]},
                as_of='2030-05-27',ledger=None,field_decider=fields,source_observation_path=path)
            self.assertEqual(got['label'],'contradicted')
            feedback={f['field']:f for f in got['claim_feedback']}
            self.assertEqual(feedback['disputed']['source_assertion_id'],'a')
            self.assertEqual(feedback['disputed']['source_observations_quarantined'],1)
            self.assertEqual(feedback['bare']['source_assertion_id'],'e')
            self.assertEqual(feedback['bare']['source_observations_quarantined'],1)
            self.assertTrue(S.cached_observation(path,'e')['quarantined'])
            self.assertNotIn('source_assertion_id',feedback['ordinary_id'])
            self.assertNotIn('source_assertion_id',feedback['aggregate'])
            self.assertTrue(S.cached_observation(path,'a')['quarantined'])
            for ident in ['b','c','d']:self.assertFalse(S.cached_observation(path,ident)['quarantined'])
            db=S.observation_store(path);self.assertEqual(db.execute('SELECT COUNT(*) FROM span_observations').fetchone()[0],5);db.close()


class SupportScheduling(unittest.TestCase):
    def packet(self,name):
        return {'id':name,'date':'2030-05-27','text':name,'bytes':len(name)}

    def test_summary_fault_defers_other_patients_without_accepting_them(self):
        seen=[]
        def decide(question,options,evidence,**kw):
            seen.append(evidence[0]['id'])
            return {'label':'contradicted','decided_by':'fixture','steps':[]}
        out=B.check_support_packets([self.packet(k)for k in ('summary','patient_A','patient_B')],decide,
                                    as_of='2030-05-27',ledger=None)
        self.assertEqual(seen,['summary'])
        self.assertEqual(out['failed_packet_ids'],['summary'])
        self.assertEqual(out['unchecked_packet_ids'],['patient_A','patient_B'])
        self.assertFalse(out['all_dependencies_checked'])
        self.assertTrue(all(p['label']=='unknown'and p['decided_by']is None for p in out['packets'][1:]))

    def test_deferred_source_children_keep_their_patient_parent_and_complete_manifest(self):
        child={**self.packet('patient_A__sources_0'),'parent_packet_id':'patient_A'}
        out=B.check_support_packets([self.packet('summary'),child],
            lambda *a,**kw:{'label':'contradicted','decided_by':'fixture','steps':[]},as_of='2030-05-27',ledger=None)
        self.assertTrue(out['packet_manifest_complete']);self.assertFalse(out['all_dependencies_checked'])
        self.assertEqual(out['packets'][1]['parent_packet_id'],'patient_A')
        self.assertEqual(out['packets'][1]['status'],'awaiting_dependency_repair')

    def test_missing_source_context_keeps_its_patient_parent_without_model_work(self):
        child={**self.packet('patient_A__sources_0'),'parent_packet_id':'patient_A','source_context_complete':False}
        out=B.check_support_packets([child],lambda *a,**kw:self.fail('Missing context must not dispatch a model'),as_of='2030-05-27',ledger=None)
        self.assertEqual(out['packets'][0]['parent_packet_id'],'patient_A')
        self.assertEqual(out['packets'][0]['label'],'unknown')

    def test_independent_patients_overlap_without_sharing_the_sqlite_handle(self):
        import threading
        from types import SimpleNamespace
        barrier=threading.Barrier(2);seen=[]
        def decide(question,options,evidence,**kw):
            self.assertEqual(kw['ledger'],'one-ledger.sqlite3')
            ident=evidence[0]['id'];seen.append(ident)
            if ident!='summary':barrier.wait(timeout=2)
            return {'label':'supported','decided_by':'fixture','steps':[{'provider':'fixture','cost_usd':0}]}
        with patch.dict('os.environ',{'AGERE_SYSTEM_ONE_WORKERS':'2'}):
            out=B.check_support_packets([self.packet(k)for k in ('summary','patient_A','patient_B')],decide,
                as_of='2030-05-27',ledger=SimpleNamespace(path='one-ledger.sqlite3'))
        self.assertEqual(set(seen),{'summary','patient_A','patient_B'})
        self.assertEqual([p['id']for p in out['packets']],['summary','patient_A','patient_B'])
        self.assertEqual(out['label'],'supported');self.assertTrue(out['all_dependencies_checked'])
        self.assertEqual(len(out['steps']),3)

    def test_custom_read_only_ledger_is_preserved_on_its_calling_thread(self):
        import threading
        ledger=SimpleNamespace(path='must-not-reopen.sqlite3',db=object())
        owner=threading.get_ident()
        def decide(question,options,evidence,**kw):
            self.assertIs(kw['ledger'],ledger);self.assertEqual(threading.get_ident(),owner)
            return {'label':'supported','decided_by':'fixture','steps':[]}
        out=B.check_support_packets([self.packet(k)for k in ('summary','patient_A')],decide,
                                    as_of='2030-05-27',ledger=ledger)
        self.assertEqual(out['label'],'supported')

    def test_inflight_receipts_survive_a_fault_and_unsent_patients_remain_unchecked(self):
        import threading
        found=threading.Event();seen=[]
        def decide(question,options,evidence,**kw):
            ident=evidence[0]['id'];seen.append(ident)
            if ident=='patient_A':found.set()
            if ident=='patient_B':self.assertTrue(found.wait(timeout=2))
            return {'label':'contradicted'if ident=='patient_A'else'supported','decided_by':'fixture',
                    'steps':[{'provider':'fixture','request_hash':ident,'cost_usd':.01}]}
        with patch.dict('os.environ',{'AGERE_SYSTEM_ONE_WORKERS':'2'}):
            out=B.check_support_packets([self.packet(k)for k in ('summary','patient_A','patient_B','patient_C')],decide,
                                        as_of='2030-05-27',ledger=None)
        self.assertEqual(set(seen),{'summary','patient_A','patient_B'})
        self.assertEqual(out['unchecked_packet_ids'],['patient_C'])
        self.assertEqual({s['request_hash']for s in out['steps']},set(seen))
        self.assertNotEqual(out['label'],'supported')


if __name__=='__main__':unittest.main()
