"""Focused attention must preserve scope, complete context and honest gaps."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import document_review as D, note_evidence as N, typed_document_review as T


def question(entity):
    return {'question': 'What does the source establish about '+entity+'?',
            'entity': {'kind': 'service', 'id': entity, 'aliases': [entity]},
            'relation': 'reason_for_deferral', 'subject': 'patient'}


class FocusedAttention(unittest.TestCase):
    def medication_question(self,identity):
        return {'question':'What regimen does the source document for this medication class?',
                'entity':{'kind':'medication_class','id':identity,'aliases':[identity.replace('_',' ')]},
                'relation':'documented_regimen','subject':'patient'}

    def test_registered_groups_find_ingredients_and_brands_without_actor_alias_lists(self):
        docs=[{'record_id':str(i),'date':'2030-01-01','text':text}for i,text in enumerate([
            'Carvedilol 12.5 mg twice daily.','Atenolol 50 mg daily.',
            'Entresto was discussed.','Furosemide and Jardiance are listed.',
            'Dietary carbohydrate counseling only.'])]
        q=self.medication_question('gdmt');selected,receipt=N.focused_documents(docs,{'regimen':q})
        expected={digest([d['record_id'],d['date'],d['text']])for d in [docs[0],docs[2],docs[3]]}
        self.assertEqual(selected['regimen'],expected)
        self.assertEqual(receipt['inventory_documents'],5);self.assertFalse(receipt['absence_established'])
        self.assertIn('hf_evidence_based_beta_blocker',receipt['registered_class_members']['regimen'])
        for doc in docs:
            self.assertEqual(D.entity_source_link_issue(q,doc)is None,doc in [docs[0],docs[2],docs[3]])

    def test_registered_entity_link_keeps_subtype_combo_and_cross_class_boundaries(self):
        for identity,text,linked in [
            ('hf_evidence_based_beta_blocker','Metoprolol tartrate 25 mg daily.',False),
            ('hf_evidence_based_beta_blocker','Metoprolol succinate 25 mg daily.',True),
            ('arb','Entresto 24/26 mg.',False),('arni','Entresto 24/26 mg.',True),
            ('sglt2_inhibitor','Furosemide and Jardiance were discussed.',True),
            ('antihypertensive','Atenolol 50 mg daily.',True),
            ('dementia_medication','Donepezil was stopped.',True),
            ('statin','Only losartan is listed.',False)]:
            with self.subTest(identity=identity,text=text):
                q=self.medication_question(identity);doc={'record_id':'r','date':'','text':text}
                self.assertEqual(D.entity_source_link_issue(q,doc)is None,linked)
                selected,_=N.focused_documents([doc],{'field':q})
                self.assertEqual(bool(selected['field']),linked)

    def test_native_class_link_does_not_supply_a_relationship_or_current_patient_use(self):
        q=self.medication_question('gdmt');doc={'record_id':'r','date':'2030-01-01','text':'Her father once took carvedilol. No patient regimen is stated.'}
        seen=[]
        def judge(subject,named,evidence,**kw):
            seen.extend(evidence)
            from clinical_core.agere.clinical.drug_classes import GROUPS
            self.assertTrue(all(v['instructions']['native_entity_contract']['member_classes']==sorted(GROUPS['gdmt'])for v in named.values()))
            return {'judgments':{k:{'label':'unknown','decided_by':None}for k in named},'steps':[],'cost_usd':0}
        tools=SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False))
        with patch.dict(os.environ,{'AGERE_DOCUMENT_LOCAL_READERS':''}):
            out=T.read(tools,'A',[doc],{'regimen':q},None,judge=judge,ranking=False)
        self.assertEqual(out['findings']['regimen'],[])
        self.assertTrue(out['unresolved']);self.assertEqual(seen[0]['text'],doc['text'])

    def test_all_matches_retain_negation_history_and_full_context_without_top_k(self):
        docs=[{'record_id': str(i), 'date': '2029-01-01', 'text': 'Prior dialysis plan.\n\nCorrection: it was declined.'} for i in range(160)]
        docs.append({'record_id':'noise','date':'2030-01-01','text':'A parking receipt.'})
        fields, receipt=N.focused_documents(docs, {'reason':question('dialysis')})
        self.assertEqual(len(fields['reason']),160)
        self.assertEqual(receipt['selected_documents'],160)
        self.assertFalse(receipt['absence_established'])
        batches=T.source_batches(docs,{'reason':question('dialysis')},{},field_documents=fields)
        self.assertEqual(sum(map(len,batches)),320)
        self.assertTrue(all(d['text'].startswith('Prior dialysis plan.') for batch in batches for _,_,_,d in batch))

    def test_each_field_uses_its_own_sources_and_an_implicit_reference_stays_a_gap(self):
        docs=[{'record_id':'a','date':'','text':'Dialysis declined.'},
              {'record_id':'b','date':'','text':'MRI deferred.'},
              {'record_id':'c','date':'','text':'The previously discussed treatment was postponed.'}]
        qs={'renal':question('dialysis'),'imaging':question('MRI')}
        fields,receipt=N.focused_documents(docs,qs)
        jobs=T.source_batches(docs,qs,{},field_documents=fields)
        self.assertEqual([(f,d['record_id'])for batch in jobs for f,_,_,d in batch],[('renal','a'),('imaging','b')])
        self.assertEqual(receipt['sources'][2]['selected_fields'],[])
        self.assertFalse(receipt['complete_chart_review'])
        self.assertIn('expansion',receipt)

    def test_continued_document_keeps_its_field_membership(self):
        doc={'record_id':'r','date':'','text':'Dialysis. '+('source '*30000)}
        fields,_=N.focused_documents([doc],{'reason':question('dialysis')})
        segment={**doc,'text':doc['text'][180000:],'source_document_identity':digest(['r','',doc['text']])}
        jobs=T.source_batches([segment],{'reason':question('dialysis')},{},field_documents=fields)
        self.assertTrue(jobs)

    def test_population_preserved_and_field_scope_does_not_drop_literal_correspondence(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            db=sqlite3.connect(':memory:')
            db.executescript("CREATE TABLE patient(patient_id);INSERT INTO patient VALUES ('A'),('B'),('C')")
            source={'A':[('a','note','notes','clinical','2030-01-01','Dialysis deferred.',''),
                         ('role','note','notes','letter','2029-01-01','Sincerely,\nMaya Chen, MD\nCardiology','')],
                    'B':[('b','note','notes','bill','2030-01-01','A parking receipt.','')],'C':[]}
            tools=SimpleNamespace(db=db,as_of='2030-05-27',stack=SimpleNamespace(path=p,ledger_path=p/'ledger.sqlite3',allow_live=False),
                in_scope=lambda _:None,continuation_identity=lambda:{'source':'fixture'},_texts=lambda pid:source[pid],
                document_reviews=[],subtool_accounting={'new_paid_api_calls':0,'estimated_cost_usd':0})
            seen=[]
            def read(tools,pid,docs,qs,ledger,**kw):
                seen.append((pid,docs,kw['field_documents']))
                return {'findings':{k:[]for k in qs},'checks':[],'unresolved':[],'version':T.VERSION,'coverage':[],
                        'ranking':{},'all_blocks_processed':False}
            with patch.object(T,'read',side_effect=read),patch('clinical_core.agere.clinical.analysis_tools.population',return_value=['A']),patch.dict(os.environ,{'AGERE_DOCUMENT_REVIEW':'typed_spans'}):
                out=D.review(tools,['A','B','C'],{'reason':question('dialysis')},source_scope='focused',
                             field_patient_queries={'reason':'SELECT eligible patient_id'})
            self.assertEqual([r['patient_id']for r in out['rows']],['A','B','C'])
            self.assertEqual([s[0]for s in seen],['A'])
            self.assertEqual(len(seen[0][1]),1)
            self.assertIn('Maya Chen',out['rows'][0]['source_clinicians_json'])
            for row in out['rows']:
                scope=json.loads(row['evidence_scope_json'])
                self.assertFalse(scope['absence_established'])
            self.assertEqual(out['rows'][1]['documents_reviewed'],0)
            db.close()

    def test_scope_expansion_and_legacy_full_scope_remain_explicit(self):
        with self.assertRaisesRegex(ValueError,'explicit entity'):
            N.focused_documents([],{'reason':'Why was it deferred?'})
        doc={'record_id':'x','date':'','text':'The procedure was deferred.'}
        qs={'reason':question('MRI')}
        fields,_=N.focused_documents([doc],qs);self.assertEqual(fields['reason'],set())
        expanded=copy.deepcopy(qs);expanded['reason']['entity']['aliases'].append('procedure')
        fields,_=N.focused_documents([doc],expanded);self.assertEqual(len(fields['reason']),1)

    def test_focused_frontier_receives_only_suspected_positives_and_keeps_other_gaps_addressable(self):
        with tempfile.TemporaryDirectory()as temp:
            p=Path(temp);db=sqlite3.connect(':memory:');db.executescript("CREATE TABLE patient(patient_id);INSERT INTO patient VALUES('A')")
            text='Dialysis was deferred.'
            tools=SimpleNamespace(db=db,as_of='2030-05-27',stack=SimpleNamespace(path=p,ledger_path=p/'ledger.sqlite3',allow_live=False),
                in_scope=lambda _:None,continuation_identity=lambda:{'source':'fixture'},
                _texts=lambda pid:[('r','note','notes','clinical','2030-01-01',text,'')],document_reviews=[],
                subtool_accounting={'new_paid_api_calls':0,'estimated_cost_usd':0})
            base={'field':'reason','record_id':'r','quote':text,'span_start':0,'span_end':len(text),'document_text_digest':digest(text)}
            pending=[{**base,'reason':'Unresolved assertion axes','reading':{'label':'finding','decided_by':None}},
                     {**base,'reason':'Uncertain relevance','reading':{'label':'unknown','decided_by':None}}]
            def read(*a,**kw):return {'findings':{'reason':[]},'checks':[],'unresolved':pending,'version':T.VERSION,
                                       'coverage':[],'ranking':{},'all_blocks_processed':False}
            repairs=[]
            def repair(tools,pid,unresolved,*a):
                repairs.extend(unresolved);return {'findings':{'reason':[]},'failures':[],'receipt':{}}
            with patch.object(T,'read',side_effect=read),patch('clinical_core.agere.clinical.span_repair.repair',side_effect=repair),patch.dict(os.environ,{'AGERE_DOCUMENT_REVIEW':'typed_spans'}):
                out=D.review(tools,['A'],{'reason':question('dialysis')},source_scope='focused')
            self.assertEqual([u['reading']['label']for u in repairs],['finding'])
            deferred=json.loads(out['rows'][0]['review_unresolved_json'])
            self.assertEqual(len(deferred),1);self.assertEqual(deferred[0]['record_id'],'r')
            self.assertEqual(out['rows'][0]['review_status'],'needs_review')
            db.close()


class StableObservations(unittest.TestCase):
    @patch.dict(os.environ,{'AGERE_DOCUMENT_TYPED_ORDER':'jev,perplexity'})
    def test_rebatch_reuses_only_exact_source_question_date_and_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            tools=SimpleNamespace(stack=SimpleNamespace(path=Path(temp)))
            calls=[]
            def judge(subject,named,evidence,**kw):
                calls.append(list(named))
                return {'judgments':{k:{'label':'finding','decided_by':'jev','provider':'jev','probabilities':{'finding':.95,'irrelevant':.03,'unknown':.02}} for k in named},
                        'steps':[{'provider':'jev','request_hash':'a'*64,'cache_hit':False,'cost_usd':.001}],'cost_usd':.001}
            checked=T.cached_source_judge(tools,judge)
            q={'type':'choice','instructions':{'source_context_id':'a','target_block':'Dialysis deferred.'},'criteria':T.OPTIONS}
            evidence=[{'id':'a','date':'2030-01-01','text':'Dialysis deferred.'}, {'id':'b','date':'2030-01-01','text':'Unrelated source.'}]
            checked('source-task',{'f0':q},evidence,as_of='2030-05-27')
            hit=checked('source-task',{'different_transport_id':q},evidence[:1],as_of='2030-05-27')
            self.assertEqual(len(calls),1)
            self.assertEqual(hit['steps'][0]['provider'],'source_field_cache')
            changed=copy.deepcopy(q);changed['instructions']['target_block']='A different relation.'
            checked('source-task',{'f0':changed},evidence,as_of='2030-05-27')
            changed_source=copy.deepcopy(evidence);changed_source[0]['text']='Corrected: not deferred.'
            checked('source-task',{'f0':q},changed_source,as_of='2030-05-27')
            checked('source-task',{'f0':q},evidence,as_of='2030-05-28')
            with patch.dict(os.environ,{'AGERE_DOCUMENT_TYPED_ORDER':'perplexity,jev'}):
                T.cached_source_judge(tools,judge)('source-task',{'f0':q},evidence,as_of='2030-05-27')
            self.assertEqual(len(calls),5)
            T.cached_source_judge(tools,judge,patient_id='A')('source-task',{'f0':q},evidence,as_of='2030-05-27')
            T.cached_source_judge(tools,judge,patient_id='B')('source-task',{'f0':q},evidence,as_of='2030-05-27')
            self.assertEqual(len(calls),7)

    def test_unknown_or_prepared_never_becomes_a_reusable_negative(self):
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            def judge(subject,named,evidence,**kw):
                calls.append(1)
                return {'judgments':{k:{'label':'unknown','decided_by':None}for k in named},'steps':[],'cost_usd':0.0}
            check=T.cached_source_judge(SimpleNamespace(stack=SimpleNamespace(path=Path(temp))),judge)
            q={'type':'choice','instructions':{'source_context_id':'a'},'criteria':T.OPTIONS}
            for _ in range(2):check('task',{'f0':q},[{'id':'a','text':'Unknown.','date':'unknown'}],as_of='2030-05-27')
            self.assertEqual(len(calls),2)

    def test_observed_uncertainty_is_reused_as_uncertainty_and_can_seed_no_negative(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);calls=[]
            def judge(subject,named,evidence,**kw):
                calls.append(1)
                return {'judgments':{k:{'label':'unknown','decided_by':None,'provider':'jev',
                    'probabilities':{'unknown':.6,'finding':.2,'irrelevant':.2}}for k in named},
                    'steps':[{'request_hash':'b'*64,'cache_hit':False,'cost_usd':.001}],'cost_usd':.001}
            check=T.cached_source_judge(SimpleNamespace(stack=SimpleNamespace(path=path)),judge)
            q={'type':'choice','instructions':{'source_context_id':'a','question':'What is documented?'},'criteria':T.OPTIONS}
            evidence=[{'id':'a','record_id':'a','text':'Implicit reference.','date':'2030-01-01'}]
            first=check('task',{'x':q},evidence,as_of='2030-05-27')
            second=check('task',{'x':q},evidence,as_of='2030-05-27')
            self.assertEqual(len(calls),1);self.assertEqual(first['judgments'],second['judgments'])
            self.assertIsNone(second['judgments']['x']['decided_by'])
            self.assertEqual(T.prior_source_candidates(path,[{'record_id':'a','date':'2030-01-01','text':'Implicit reference.'}],{'field':question('new concept')}),{'field':set()})

    def test_prior_positive_with_unchanged_text_seeds_retrieval_but_changed_sources_do_not(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);doc={'record_id':'r','date':'2030-01-01','text':'The previously discussed procedure was postponed.'}
            qs={'procedure':question('MRI')}
            def judge(subject,named,evidence,**kw):
                return {'judgments':{k:{'label':'finding','decided_by':'jev','probabilities':{'finding':1.0}}for k in named},
                        'steps':[{'request_hash':'c'*64,'cache_hit':False,'cost_usd':.001}],'cost_usd':.001}
            check=T.cached_source_judge(SimpleNamespace(stack=SimpleNamespace(path=path)),judge)
            named={'x':{'type':'choice','instructions':{'source_context_id':'r','question':qs['procedure']['question']},'criteria':T.OPTIONS}}
            check('task',named,[{'id':'r','record_id':'r','date':doc['date'],'text':doc['text']}],as_of='2030-05-27')
            seeds=T.prior_source_candidates(path,[doc],qs)
            fields,receipt=N.focused_documents([doc],qs,prior_sources=seeds)
            self.assertEqual(len(fields['procedure']),1)
            changed={**doc,'text':'Corrected: unrelated.'}
            self.assertEqual(T.prior_source_candidates(path,[changed],qs),{'procedure':set()})

    def test_search_limits_are_code_owned_even_when_answer_omits_them(self):
        limits=D.source_scope_limits([{'source_scope':'focused'}])
        self.assertTrue(limits);self.assertIn('empty finding',limits[0])
        self.assertEqual(D.source_scope_limits([{'source_scope':'all'}]),[])

    def test_saved_report_carries_selected_scope_without_promoting_unknown_fields(self):
        from clinical_core.agere.deliverables.patient_evidence import projection
        scope={'inventory_documents':10,'selected_documents':2,'field_documents':{'reason':2},
               'field_applicability':{'reason':True},'complete_chart_review':False,'absence_established':False,
               'sources':[{'record_id':'unselected'}]}
        context={'investigation':{'document_reviews':[{'source_scope':'focused','questions':{'reason':question('MRI')},
            'rows':[{'patient_id':'A','review_json':'{}','source_clinicians_json':'[]',
                     'evidence_scope_json':json.dumps(scope),'review_unresolved_json':'[{"field":"reason"}]'}]}]}}
        row=projection(context)['notes']['A'][0]
        self.assertEqual(row['source_scope'],'focused');self.assertEqual(row['evidence_scope']['selected_documents'],2)
        self.assertFalse(row['evidence_scope']['absence_established']);self.assertEqual(row['unresolved_source_fields'],1)
        self.assertNotIn('sources',row['evidence_scope'])
        self.assertEqual(context['investigation']['document_reviews'][0]['rows'][0]['evidence_scope_json'],json.dumps(scope))

    def test_native_proposed_review_steps_are_recommendations_but_counts_are_not(self):
        from clinical_core.agere.deliverables.patient_evidence import recommendation_field
        for field in ('proposed_review_steps','proposed_followup_actions','review_plan','next_steps'):
            with self.subTest(field=field):self.assertTrue(recommendation_field(field))
        for field in ('review_status','review_step_count','proposed_action_type','source_findings'):
            with self.subTest(field=field):self.assertFalse(recommendation_field(field))


if __name__=='__main__':unittest.main()
