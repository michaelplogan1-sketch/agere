"""Source/entity integrity regressions, without provider or protected-data reads."""
import copy,json,os,sqlite3,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import document_review as D,typed_document_review as T,span_repair as S
from clinical_core.agere.clinical import harness_improvement as H,result_assembly as A


def question(entity='mra',subject='patient'):
    return {'question':'What documented reason concerns this entity?',
        'entity':{'kind':'therapy_class','id':entity,'aliases':[entity.upper()]},
        'relation':'withheld_for_reason','subject':subject}


class Relations(unittest.TestCase):
    def setUp(self):
        self.doc={'record_id':'r','date':'2030-01-01','text':'MRA held for potassium. RASI continued.'}
        self.tools=SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False))
        self.axes={'polarity':'affirmed','temporality':'historical','subject':'patient'}
        self.patch=patch.dict(os.environ,{'AGERE_DOCUMENT_LOCAL_READERS':''});self.patch.start();self.addCleanup(self.patch.stop)

    def finding(self,q=None):
        return D.relation_finding('A','reason',q or question(),self.doc,
            {'start':0,'end':len(self.doc['text']),'text':self.doc['text']},self.axes,'fixture')

    def test_explicit_binding_is_required_and_legacy_questions_survive(self):
        self.assertEqual(D.validate_questions({'reason':'What happened?'}),{'reason':'What happened?'})
        for q in [dict(question(),subject='unsure'),dict(question(),entity={'id':'mra'}),dict(question(),relation='')]:
            with self.assertRaises(ValueError):D.validate_questions({'reason':q})

    def test_accepted_history_only_paragraph_cannot_become_a_treatment_reason(self):
        doc={**self.doc,'text':'Problem history: chronic heart failure, obesity and anemia.'}
        calls=[]
        def judge(task,named,evidence,**kw):
            calls.append(task)
            return {'judgments':{k:{'label':'finding','decided_by':'fixture','provider':'fixture'}for k in named},'steps':[],'cost_usd':0}
        out=T.read(self.tools,'A',[doc],{'reason':question()},None,judge=judge,ranking=False)
        self.assertEqual(out['findings']['reason'],[])
        self.assertEqual(out['unresolved'][0]['record_id'],'r')
        self.assertEqual(out['unresolved'][0]['quote'],doc['text'])
        self.assertIn('complete source',out['unresolved'][0]['reason'])
        self.assertNotIn('Source-bound entity/relation assertion axes',calls)
        with self.assertRaisesRegex(ValueError,'Requested entity'):
            D.relation_finding('A','reason',question(),doc,{'start':0,'end':len(doc['text']),'text':doc['text']},self.axes,'fixture')

    def test_link_guard_uses_whole_source_and_is_not_a_medication_handler(self):
        q={'question':'Why was this service deferred?','entity':{'kind':'service','id':'service_7','aliases':['home visit']},
            'relation':'deferred_for_reason','subject':'patient'}
        doc={**self.doc,'text':'The home visit was discussed.\n\nIt was deferred until transport could be arranged.'}
        text='It was deferred until transport could be arranged.';start=doc['text'].index(text)
        f=D.relation_finding('A','reason',q,doc,{'start':start,'end':start+len(text),'text':text},self.axes,'fixture')
        self.assertEqual(f['quote'],text);self.assertEqual(f['entity']['id'],'service_7')
        self.assertIsNotNone(D.entity_source_link_issue(question('arb'),{'text':'Carbohydrate counseling was provided.'}))

    def test_correct_axes_cannot_upgrade_background_when_the_entity_is_elsewhere(self):
        doc={**self.doc,'text':'Problem history: heart failure and anemia.\n\nMRA held for potassium.'}
        def judge(task,named,evidence,**kw):
            judgments={}
            for key,value in named.items():
                ins=value['instructions'];axis=ins.get('axis')
                if axis=='relation_support':label='context_only'if ins['target_block'].startswith('Problem history')else'supported'
                else:label=self.axes[axis]if axis else'finding'
                judgments[key]={'label':label,'decided_by':'fixture','provider':'fixture'}
            return {'judgments':judgments,'steps':[],'cost_usd':0}
        out=T.read(self.tools,'A',[doc],{'reason':question()},None,judge=judge,ranking=False)
        self.assertEqual([f['quote']for f in out['findings']['reason']],['MRA held for potassium.'])
        self.assertEqual(len(out['unresolved']),1)
        self.assertEqual(out['unresolved'][0]['native_binding_issue'],'source_relation_context_only')

    def test_assignment_boundary_reaches_typed_and_frontier_source_readers(self):
        q={**question(), 'entity':{'kind':'clinician_role','id':'renal','aliases':['renal clinician']},
           'relation':'clinician_of_record','question':'Who is assigned ongoing renal care?'}
        seen=[]
        def judge(task,named,evidence,**kw):
            seen.extend(v['instructions']['task'] for v in named.values())
            return {'judgments':{k:{'label':'irrelevant','decided_by':'fixture'} for k in named},
                    'steps':[],'cost_usd':0}
        T.read(self.tools,'A',[{**self.doc,'text':'Renal clinician: Alice Rivera, MD, Nephrology'}],
               {'assigned_role':q},None,judge=judge,ranking=False)
        self.assertTrue(seen)
        self.assertTrue(all(D.RELATION_RULES in instruction for instruction in seen))
        self.assertIn(D.RELATION_RULES,D.SYSTEM)

    def test_medication_class_task_id_rejected_before_reading_but_real_identity_survives(self):
        q=question();q['entity']={'kind':'medication_class','id':'gdmt_dose','aliases':['target dose','titration']}
        with self.assertRaisesRegex(ValueError,'not a registered class or group'),patch.object(T,'rank')as rank:
            T.read(self.tools,'A',[self.doc],{'dose':q},None,judge=lambda *a,**k:self.fail('Provider must not be called'))
        rank.assert_not_called()
        from clinical_core.agere.clinical.drug_classes import CLASSES,GROUPS
        for identity in set(CLASSES)|set(GROUPS):
            bound={**q,'entity':{**q['entity'],'id':identity}}
            self.assertEqual(D.validate_questions({'dose':bound}),{'dose':bound})
        self.assertEqual(q['entity']['id'],'gdmt_dose') # no silent identity repair

    def test_native_unlinked_routing_retains_unknown_and_alias_repair_rereads_full_source(self):
        from unittest.mock import Mock
        doc={**self.doc,'text':'A home visit was deferred until transport could be arranged.'}
        q={'question':'Why was the home service deferred?', 'entity':{'kind':'service','id':'service_7','aliases':['domiciliary consultation']},
           'relation':'deferred_for_reason','subject':'patient'}
        judge=Mock(side_effect=AssertionError('No useful model judgment before entity binding'))
        out=T.read(self.tools,'A',[doc],{'reason':q},None,judge=judge,ranking=False)
        judge.assert_not_called();self.assertEqual(out['provider_batches'],0)
        self.assertEqual(out['findings']['reason'],[])
        self.assertEqual(out['unresolved'][0]['reading']['label'],'unknown')
        self.assertEqual(out['unresolved'][0]['native_binding_issue'],'source_entity_unlinked')
        self.assertEqual(out['unresolved'][0]['quote'],doc['text'])
        self.assertEqual(out['coverage'][0]['native_unlinked_field_decisions'],1)
        self.assertEqual(out['coverage'][0]['model_field_decisions'],0)
        def irrelevant(task,named,evidence,**kw):
            self.assertEqual(evidence[0]['text'],doc['text'])
            return {'judgments':{k:{'label':'irrelevant','decided_by':'fixture'}for k in named},'steps':[],'cost_usd':0}
        fixed={**q,'entity':{**q['entity'],'aliases':['home visit']}}
        repaired=T.read(self.tools,'A',[doc],{'reason':fixed},None,judge=irrelevant,ranking=False)
        self.assertEqual(repaired['provider_batches'],1);self.assertEqual(repaired['unresolved'],[])

    def test_unlinked_blocks_are_removed_before_provider_batch_packing(self):
        qs={'f'+str(i):{'question':'What is established about service '+str(i)+'?',
             'entity':{'kind':'service','id':'service_'+str(i),'aliases':['service '+str(i)]},
             'relation':'reason_for_deferral','subject':'patient'}for i in range(70)}
        doc={**self.doc,'text':'service 69 was discussed.'};calls=[]
        def judge(task,named,evidence,**kw):
            calls.append(len(named))
            return {'judgments':{k:{'label':'irrelevant','decided_by':'fixture'}for k in named},'steps':[],'cost_usd':0}
        out=T.read(self.tools,'A',[doc],qs,None,judge=judge,ranking=False)
        self.assertEqual(calls,[1]);self.assertEqual(out['provider_batches'],1)
        self.assertEqual(len(out['unresolved']),69)
        self.assertEqual(out['coverage'][0]['field_decisions'],70)

    def test_class_swap_changes_only_the_bound_finding_and_keeps_exact_source(self):
        seen=[]
        def judge(task,named,evidence,**kw):
            seen.extend(named.values());judgments={}
            for key,value in named.items():
                self.assertIn('unknown',value['criteria'])
                ins=value['instructions'];axis=ins.get('axis');bound=ins.get('relation_binding')
                label='supported'if axis=='relation_support'else self.axes[axis] if axis else 'finding' if bound['entity']['id']=='mra' else 'irrelevant'
                judgments[key]={'label':label,'decided_by':'fixture','provider':'fixture'}
            return {'judgments':judgments,'steps':[],'cost_usd':0}
        out=T.read(self.tools,'A',[self.doc],{'mra_reason':question(),'rasi_reason':question('rasi')},None,judge=judge,ranking=False)
        self.assertEqual(out['findings']['rasi_reason'],[])
        value=out['findings']['mra_reason'][0]
        self.assertEqual(value['quote'],self.doc['text']);self.assertEqual(value['entity']['id'],'mra')
        self.assertEqual(value['assertion']['clocks']['clinical_date'],None)
        self.assertEqual(value['assertion']['clocks']['source_date'],self.doc['date'])
        self.assertTrue(all(v['instructions']['relation_binding'] in [question(),question('rasi')] for v in seen))

    def test_unknown_or_wrong_experiencer_goes_to_targeted_repair(self):
        for subject in ('other','unknown'):
            def judge(task,named,evidence,**kw):
                return {'judgments':{k:{'label':subject if v['instructions'].get('axis')=='subject' else
                    'supported'if v['instructions'].get('axis')=='relation_support'else self.axes[v['instructions']['axis']] if v['instructions'].get('axis') else 'finding',
                    'decided_by':'fixture','provider':'fixture'} for k,v in named.items()},'steps':[],'cost_usd':0}
            out=T.read(self.tools,'A',[self.doc],{'reason':question()},None,judge=judge,ranking=False)
            self.assertEqual(out['findings']['reason'],[]);self.assertEqual(len(out['unresolved']),1)
            self.assertEqual(out['unresolved'][0]['relation_binding'],question())

    def test_axes_batching_does_not_drop_any_passages(self):
        docs=[{**self.doc,'text':'\n\n'.join(['MRA held.']*30)}];sizes=[]
        def judge(task,named,evidence,**kw):
            sizes.append(len(named))
            return {'judgments':{k:{'label':'supported'if v['instructions'].get('axis')=='relation_support'else self.axes[v['instructions']['axis']] if v['instructions'].get('axis') else 'finding',
                'decided_by':'fixture','provider':'fixture'}for k,v in named.items()},'steps':[],'cost_usd':0}
        out=T.read(self.tools,'A',docs,{'reason':question()},None,judge=judge,ranking=False)
        self.assertEqual(len(out['findings']['reason']),30);self.assertLessEqual(max(sizes),64)

    def test_source_binding_rejects_mutated_entity_date_subject_and_value(self):
        original=self.finding()
        D.validate_relation_finding(original,'reason',question(),[self.doc],pid='A')
        for key,val in [('entity',question('rasi')['entity']),('date','2030-02-01'),('subject','other'),('value','new interpretation')]:
            changed={**original,key:val}
            with self.assertRaises(ValueError):D.validate_relation_finding(changed,'reason',question(),[self.doc],pid='A')
        with self.assertRaises(ValueError):D.validate_relation_finding(original,'reason',question(),[self.doc],pid='B')

    def test_bound_recovery_never_upgrades_an_old_unbound_span(self):
        value=self.finding();old={k:value[k] for k in ('value','quote','record_id','date')}
        with self.assertRaises(ValueError):H.validate_saved_source_fields({'reason':[old]},{'reason':question()},[self.doc],patient_id='A')
        H.validate_saved_source_fields({'reason':[value]},{'reason':question()},[self.doc],patient_id='A')

    def test_candidate_review_uses_complete_native_observations_and_rejects_unbound_questions(self):
        value=self.finding();view={'columns':['patient_id','review_json'],'rows':[
            {'patient_id':'A','review_json':json.dumps({'reason':[value]})},
            {'patient_id':'B','review_json':json.dumps({'reason':[{**value,'quote':'Other patient'}]})}]}
        tools=SimpleNamespace(executed_results={'native':view})
        selected,receipt=D.retained_candidates(tools,'native',['A'],{'reason':question()})
        self.assertEqual(len(selected['A']),1);self.assertEqual(selected['A'][0]['quote'],value['quote'])
        self.assertEqual(receipt['requested_candidates'],1);self.assertIn('no whole-document absence',receipt['scope'])
        self.assertEqual(json.loads(view['rows'][0]['review_json'])['reason'],[value])
        with self.assertRaises(ValueError):D.retained_candidates(tools,'missing',['A'],{'reason':question()})
        with self.assertRaises(ValueError):D.retained_candidates(tools,'native',['A'],{'reason':'Unbound question'})
        view['truncated']=True
        with self.assertRaises(ValueError):D.retained_candidates(tools,'native',['A'],{'reason':question()})

    def test_candidate_review_preflights_sources_before_provider_and_preserves_empty_scope(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);db=sqlite3.connect(':memory:');self.addCleanup(db.close)
            db.executescript("CREATE TABLE patient(patient_id); INSERT INTO patient VALUES('A'),('B')")
            value=self.finding();legacy={'patient_id':'A','review_json':json.dumps({'reason':[value]})}
            tools=SimpleNamespace(db=db,as_of='2030-05-27',stack=SimpleNamespace(path=root,ledger_path=root/'ledger.sqlite3',allow_live=False),
                in_scope=lambda pid:None,continuation_identity=lambda:{'source':'fixture'},document_reviews=[],
                read={},
                executed_results={'native':{'rows':[legacy]}},subtool_accounting={'new_paid_api_calls':0,'estimated_cost_usd':0},
                semantic=SimpleNamespace(columns={'record':[],'scanned_document':[]}),
                _texts=lambda pid:[('r','note','notes','office',self.doc['date'],self.doc['text'],'')]if pid=='A'else[])
            def read(tools,pid,docs,questions,ledger,**kw):
                self.assertEqual(pid,'A');self.assertEqual(len(kw['candidates']),1)
                self.assertEqual(docs[0]['text'],self.doc['text'])
                return {'findings':{'reason':[value]},'checks':[],'unresolved':[],
                    'coverage':[],'ranking':{},'all_blocks_processed':False,'all_candidates_processed':True,'version':T.VERSION}
            with patch.object(T,'read',side_effect=read):out=D.review(tools,['A','B'],{'reason':question()},candidate_result_view='native')
            self.assertEqual([r['review_status']for r in out['rows']],['reviewed_candidates','no_candidates'])
            self.assertEqual(out['candidate_observation_review']['requested_candidates'],1)
            self.assertFalse(out['coverage'][1]['candidate_scope']['whole_document_absence_established'])
            self.assertEqual(tools.read['r']['text'],self.doc['text'])
            prior=copy.deepcopy(tools.document_reviews)
            with H.owned_recovery([],question='Recheck',document_reviews=prior),patch.object(T,'read',side_effect=AssertionError('Exact candidate observations must not be bought again')):
                reused=D.review(tools,['A','B'],{'reason':question()},candidate_result_view='native')
            self.assertEqual(reused['accounting']['new_paid_api_calls'],0)
            self.assertFalse(reused['observation_reuse']['clinical_acceptance_inherited'])
            self.assertEqual(json.loads(reused['rows'][0]['review_json'])['reason'],[value])
            local_scope=json.loads(reused['rows'][0]['candidate_review_scope'])
            self.assertNotIn('native_result_digest',local_scope);self.assertNotIn('requested_candidates',local_scope)
            for change in [{'quote':'Invented passage.'},{'date':'2029-01-01'},{'span_start':1}]:
                legacy['review_json']=json.dumps({'reason':[{**value,**change}]})
                with self.subTest(change=change),patch.object(T,'read',side_effect=AssertionError('No provider before exact-source validation')),self.assertRaises(ValueError):
                    D.review(tools,['A'],{'reason':question()},candidate_result_view='native')

    def test_patient_candidate_scope_does_not_include_other_patient_counts_or_result_digests(self):
        m={'patient_id':'A','candidate_scope':{'candidates':1,'candidate_digest':'source-A','whole_document_absence_established':False}}
        before=D.candidate_row_scope({'result_view':'native','requested_candidates':10,'native_result_digest':'old','scope':'Candidate only'},m)
        after=D.candidate_row_scope({'result_view':'native','requested_candidates':11,'native_result_digest':'new','scope':'Candidate only'},m)
        self.assertEqual(before,after)

    def test_frontier_cannot_accept_finding_without_axes_and_keeps_valid_siblings(self):
        pending=[{'field':k,'record_id':'r','quote':self.doc['text'],'span_start':0,'span_end':len(self.doc['text'])}for k in ('mra','rasi')]
        seen=[]
        def send(body):
            state=json.loads(body['messages'][0]['content']);seen.append(state)
            answers={key:dict(label='finding',**self.axes) if task['question']['entity']['id']=='mra' else 'finding' for key,task in state['decisions'].items()}
            return {'content':[{'type':'text','text':json.dumps({'answers':answers})}]},0,False
        got=S.repair(self.tools,'A',pending,[self.doc],{'mra':question(),'rasi':question('rasi')},send,threading.Event(),[])
        self.assertEqual(len(got['findings']['mra']),1);self.assertEqual(got['findings']['rasi'],[])
        self.assertEqual(len(seen[1]['decisions']),1);self.assertEqual(len(got['failures']),1)

    def test_access_and_service_bindings_share_the_same_assertion_and_composition(self):
        q={'question':'Why was the service not attended?', 'entity':{'kind':'service','id':'transport','aliases':['ride']},
            'relation':'access_barrier','subject':'patient'}
        doc={**self.doc,'text':'The ride was unavailable, so the service visit was missed.'}
        value=D.relation_finding('A','access',q,doc,{'start':0,'end':len(doc['text']),'text':doc['text']},self.axes,'fixture')
        views={'base':{'rows':[{'patient_id':'A'}]},'review':{'rows':[{'patient_id':'A','review_json':json.dumps({'access':[value]})}]}}
        result=A.compose(views,base='base',keys=['patient_id'],joins=[{'result':'review','as':'evidence','on':{'patient_id':'patient_id'},'mode':'one'}])
        self.assertEqual(json.loads(result['rows'][0]['evidence']['review_json'])['access'][0],value)

    def test_existing_documentary_reconciler_receives_typed_axes_and_unknown_clocks(self):
        value=self.finding();row={'patient_id':'A','review_json':json.dumps({'reason':[value]})}
        out=D.with_correspondence([row],{'A':[[self.doc]]},{'reason':question()},'2030-05-27')
        state=json.loads(out[0]['documentary_state_json'])
        self.assertEqual(state['assertions'],[value['assertion']]);self.assertFalse(state['current_clinical_state_established'])
        self.assertEqual(state['groups'][0]['status'],'unresolved_documentary_accounts')

    def test_analyst_prompt_formats_with_the_new_relation_contract(self):
        from clinical_core.agere.clinical import backstop_agent as B
        self.assertIn('entity:{kind,id,aliases}',B.RULES.format(tool_quota='No fixed quota'))

    def test_source_arrival_clock_uses_cached_schema_on_governed_connection(self):
        with tempfile.TemporaryDirectory() as temp:
            db=sqlite3.connect(':memory:');self.addCleanup(db.close)
            db.executescript("CREATE TABLE patient(patient_id); INSERT INTO patient VALUES('A'); CREATE TABLE record(record_id,patient_id,available_date); INSERT INTO record VALUES('r','A','2030-01-03')")
            db.set_authorizer(lambda action,*args:sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_PRAGMA else sqlite3.SQLITE_OK)
            root=Path(temp)
            tools=SimpleNamespace(db=db,semantic=SimpleNamespace(columns={'record':['record_id','patient_id','available_date']}),
                as_of=self.tools.as_of,stack=SimpleNamespace(path=root,ledger_path=root/'ledger.sqlite3',allow_live=False),
                in_scope=lambda pid:None,continuation_identity=lambda:{'fixture':1},document_reviews=[],
                subtool_accounting={'new_paid_api_calls':0,'estimated_cost_usd':0},
                _texts=lambda pid:[('r','note','notes','office',self.doc['date'],self.doc['text'],'')])
            def read(tool,pid,docs,questions,ledger,**kw):
                self.assertEqual(docs[0]['available_date'],'2030-01-03')
                value=D.relation_finding(pid,'reason',questions['reason'],docs[0],
                    {'start':0,'end':len(docs[0]['text']),'text':docs[0]['text']},{**self.axes,'temporality':'current'},'fixture')
                return {'findings':{'reason':[value]},'checks':[],'unresolved':[],'version':T.VERSION,
                    'coverage':[],'ranking':{},'all_blocks_processed':True}
            with patch.object(T,'read',side_effect=read):out=D.review(tools,['A'],{'reason':question()})
            state=json.loads(out['rows'][0]['documentary_state_json'])
            self.assertEqual(state['assertions'][0]['clocks']['arrival_date'],'2030-01-03')
            self.assertEqual(state['groups'][0]['status'],'dated_documentary_accounts')
            self.assertFalse(state['current_clinical_state_established'])

    def test_final_assembly_detects_wrong_field_patient_or_mutated_assertion(self):
        value=self.finding();reviews=[{'questions':{'mra':question(),'rasi':question('rasi')},
            'rows':[{'patient_id':'A','review_json':json.dumps({'mra':[value],'rasi':[]})}]}]
        answer={'table':[{'patient_id':'A','mra':[value]}]}
        self.assertEqual(D.relation_composition_issues(answer,reviews),[])
        flat={'table':[{'patient_id':'A','bound_findings':[{'field':'mra',**value}]}]}
        self.assertEqual(D.relation_composition_issues(flat,reviews),[])
        flat['table'][0]['bound_findings'][0]['field']='rasi'
        self.assertTrue(D.relation_composition_issues(flat,reviews))
        for row in [{'patient_id':'A','rasi':[value]},{'patient_id':'B','mra':[value]},
                    {'patient_id':'A','mra':[{**value,'quote':'changed'}]}]:
            self.assertTrue(D.relation_composition_issues({'table':[row]},reviews))


if __name__=='__main__':unittest.main()
