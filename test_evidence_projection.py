"""Coverage/support dependency closure from authored evidence; no providers."""
import copy,hashlib,json,unittest
from clinical_core.agere.clinical import evidence_projection as E,backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json


def finding(record,quote):
    return {'record_id':record,'date':'2030-01-02','quote':quote,'value':'Candidate source interpretation'}


class Projection(unittest.TestCase):
    def test_readable_native_json_keeps_field_names_and_checks_integrity(self):
        from clinical_core.agere.deliverables.receipts import readable_json
        native={'patient_id':'A','reason':[finding('r','MRA held.')]};encoded=readable_json(native)
        self.assertEqual(uncompact_json(encoded),native);self.assertIn('reason',encoded['payload'])
        changed=copy.deepcopy(encoded);changed['payload']['patient_id']='B'
        with self.assertRaises(ValueError):uncompact_json(changed)

    def test_exact_string_cells_and_episode_grain_restore_without_mutation(self):
        table=[{'patient_id':'A','episode':'e1','findings':json.dumps([finding('r1','Could not afford it.')],indent=2),'cost':42},
               {'patient_id':'A','episode':'e2','findings':[finding('r2','Later paid fill.')],'cost':0},
               {'patient_id':'B','episode':'e3','findings':[],'cost':None}]
        original=copy.deepcopy(table);view,edges=E.table_view(table)
        leaves={'patient_A':table[:2],'patient_B':table[2:]}
        self.assertEqual(E.restore_table(view,edges,leaves),original);self.assertEqual(table,original)
        self.assertEqual([e['answer_row']for e in edges],[0,1]);self.assertEqual(view[2],table[2])

    def test_financial_and_provider_scalars_are_kept_without_entity_guessing(self):
        table=[{'provider':'C1','year':2030,'paid_dollars':120,'quality_percent':80},
               {'provider':'C2','year':2030,'paid_dollars':0,'quality_percent':None}]
        view,edges=E.table_view(table);self.assertEqual(view,table);self.assertEqual(edges,[])

    def test_arbitrary_bracket_text_and_marker_like_source_data_remain_literal(self):
        table=[{'patient_id':'A','text':'[not JSON]','data':{'clinical_evidence_dependency':'literal source'},'other':[{'record_id':'r','value':'No quote'}]}]
        view,edges=E.table_view(table);self.assertEqual(view,table);self.assertEqual(edges,[])

    def test_changed_source_or_entity_cannot_satisfy_a_dependency(self):
        table=[{'patient_id':'A','finding':[finding('r','Documented refusal.')]}];view,edges=E.table_view(table)
        for change in ('quote','patient'):
            bad=copy.deepcopy(table);changed_edges=copy.deepcopy(edges)
            if change=='quote':bad[0]['finding'][0]['quote']='Invented statement.'
            else:changed_edges[0]['patient_id']='B'
            with self.assertRaises(ValueError):E.restore_table(view,changed_edges,{'patient_A':bad})
        with self.assertRaises(KeyError):E.restore_table(view,edges,{})

    def test_indexed_dependencies_restore_every_exact_cell_and_reject_swapped_indices(self):
        table=[{'patient_id':'A','reason':[finding('a','A source.')],'role':'Dated role'},
               {'patient_id':'B','reason':[finding('b','B source.')],'role':'Unknown'}]
        view,edges=E.table_view(table,defer_native_cells=True,index_dependencies=True)
        leaves={'patient_A':table[:1],'patient_B':table[1:]}
        self.assertEqual(view[0]['reason']['clinical_evidence_dependency'],0)
        self.assertEqual(E.restore_table(view,edges,leaves),table)
        for changed in (True,1,-1,999):
            bad=copy.deepcopy(view);bad[0]['reason']['clinical_evidence_dependency']=changed
            with self.subTest(changed=changed),self.assertRaises(ValueError):E.restore_table(bad,edges,leaves)

    def test_coverage_keeps_uncertainty_and_native_executable_provenance(self):
        answer={'status':'partial','table':[{'patient_id':'A','findings':[finding('r','Dose unknown.')]}],
                'table_binding':{'result_view':'v','rows':1,'query':'SELECT '+('1,'*20000)+'1'},
                'missing':['Current medication use unknown'],'investigation_review':{'status':'repair','steps':['old operational receipt']}}
        old=copy.deepcopy(answer);view=E.coverage_answer(answer)
        self.assertLess(len(json.dumps(view)),5000);self.assertEqual(view['missing'],answer['missing'])
        self.assertEqual(view['table_binding']['result_view'],'v');self.assertIn('native_executable_provenance',view['table_binding'])
        self.assertEqual(view['coverage_contract']['dependency_edges'][0]['packet'],'patient_A');self.assertEqual(answer,old)

    def test_every_patient_dependency_keeps_full_findings_and_global_claims_are_separate(self):
        table=[{'patient_id':f'P{i}','findings':[finding(f'r{i}','A long dated source statement. '*200)]}for i in range(64)]
        answer={'table':table,'facts':{'scope':'Current responsibility is unknown.'}}
        packets=B.support_packets('Care gaps and access',answer,[],[],'2030-01-02')
        native={p['id']:uncompact_json(json.loads(p['text']))for p in packets}
        self.assertEqual(len(packets),65)
        leaves={key:p['answer_rows']for key,p in native.items()if key.startswith('patient_')}
        summary=native['summary'];self.assertEqual(E.restore_table(summary['all_final_rows'],summary['required_clinical_dependencies'],leaves),table)
        for i,row in enumerate(table):
            self.assertEqual(native[f'patient_P{i}']['answer_rows'],[row])
            self.assertNotIn('global_claims_context',native[f'patient_P{i}'])
        self.assertEqual(native['summary']['answer']['facts'],answer['facts'])

    def test_native_text_dependencies_restore_legacy_flattened_cells_exactly(self):
        table=[{'patient_id':'A','reason':'A long literal source account. '*1000,
            'clinician':'Historical author; current responsibility unknown.','literal':'[not JSON]','amount':42}]
        view,edges=E.table_view(table,defer_native_cells=True)
        self.assertEqual(len(edges),3);self.assertLess(len(json.dumps(view)),2000)
        self.assertEqual(E.restore_table(view,edges,{'patient_A':table}),table)
        self.assertEqual(view[0]['amount'],42)

    def test_supported_summary_cannot_override_one_contradicted_clinical_dependency(self):
        packets=B.support_packets('Care gaps',{'table':[{'patient_id':'A','finding':[finding('r','Therapy was stopped.')]}]},[],[],'2030-01-02')
        def decide(question,options,evidence,**kwargs):
            return {'label':'contradicted'if evidence[0]['id']=='patient_A'else'supported','decided_by':'scripted','steps':[]}
        result=B.check_support_packets(packets,decide,as_of='2030-01-02',ledger=None)
        self.assertEqual(result['label'],'contradicted');self.assertEqual(result['failed_packet_ids'],['patient_A'])


class SourceDependencies(unittest.TestCase):
    def packet(self):
        from clinical_core.agere.clinical import document_review as D
        from clinical_core.agere.deliverables.receipts import readable_json
        question={'question':'Does this source explicitly document why Agent Z stopped?',
            'entity':{'kind':'medication','id':'agent_z','aliases':['Agent Z']},'relation':'stopped_reason','subject':'patient'}
        docs=[];findings=[]
        for i in range(20):
            quote='Agent Z stopped because the label could not be verified. Dosis desconocida — '+str(i)+'.'
            filler=''.join(hashlib.sha256(f'{i}:{n}'.encode()).hexdigest()for n in range(256))
            doc={'patient_id':'A','record_id':'r'+str(i),'date':'2030-01-02','text':quote+'\n'+filler}
            docs.append(doc)
            f=D.relation_finding('A','reason',question,doc,{'start':0,'end':len(quote),'text':quote},
                {'polarity':'affirmed','temporality':'current','subject':'patient'},'authored_test')
            findings.append(f)
        row={'patient_id':'A','findings':json.dumps(findings,ensure_ascii=True),'amount':42,
             'literal':{'required_source_dependency':'literal, not a reference','kind':'contexts'}}
        state={'id':'patient_A','patient_id':'A','question':'Source review','as_of':'2030-01-02','answer_rows':[row],
            'source_contexts':docs,'review_scope':[{'questions':{'reason':question}}],
            'claim_manifest':[{'id':'f','field':'findings','path':['answer_rows',0,'findings']},
                              {'id':'a','field':'amount','path':['answer_rows',0,'amount']}]}
        text=json.dumps(readable_json(state),ensure_ascii=False,separators=(',',':'))
        return {'id':state['id'],'date':state['as_of'],'text':text,'bytes':len(text.encode()),
                'source_context_complete':True,'claim_manifest':state['claim_manifest']},state

    def test_complete_sources_and_every_observation_are_dispatched_with_exact_native_restoration(self):
        packet,state=self.packet();original=copy.deepcopy(packet)
        parts=E.source_dependency_packets(packet)
        self.assertGreater(len(parts),1);self.assertEqual(packet,original)
        self.assertTrue(all(p['bytes']<=230000 and p['source_dependency_roundtrip_verified']for p in parts))
        parent=uncompact_json(json.loads(parts[0]['text']))
        self.assertEqual(parent['answer_rows'][0]['amount'],42)
        self.assertEqual(parent['answer_rows'][0]['findings']['source_dependency_json_string']['format'],'native_ascii')
        leaves=[uncompact_json(json.loads(p['text']))for p in parts[1:]]
        supplied=[d for leaf in leaves for d in leaf['source_contexts']]
        observations=[r['observation']for leaf in leaves for r in leaf['answer_rows']]
        self.assertEqual(supplied,state['source_contexts'])
        self.assertEqual(observations,json.loads(state['answer_rows'][0]['findings']))
        self.assertIn('no clinical fact',parent['scope'])

    def test_missing_or_changed_source_and_forged_current_state_fail_closed(self):
        from clinical_core.agere.deliverables.receipts import readable_json
        from clinical_core.contracts import digest
        for change in ('missing','text','meaning','current'):
            packet,state=self.packet()
            if change=='missing':state['source_contexts'].pop()
            elif change=='text':state['source_contexts'][0]['text']+='changed'
            else:
                values=json.loads(state['answer_rows'][0]['findings']);a=values[0]['assertion']
                if change=='meaning':a['relation']='another_relation'
                else:a['current_clinical_state_established']=True
                a['id']=digest(['documentary-relation/1',{k:v for k,v in a.items()if k!='id'}])
                state['answer_rows'][0]['findings']=json.dumps(values)
            packet['text']=json.dumps(readable_json(state))
            with self.subTest(change=change),self.assertRaises(ValueError):E.source_dependency_packets(packet)

    def test_one_failed_or_unknown_source_cannot_be_overridden_by_supported_parent(self):
        packet,_=self.packet()
        for label in ('contradicted','unknown'):
            def decide(question,options,evidence,**kwargs):
                child='__sources_'in evidence[0]['id']
                return {'label':label if child else 'supported','decided_by':'scripted'if label!='unknown'or not child else None,'steps':[]}
            result=B.check_support_packets([packet],decide,as_of=packet['date'],ledger=None)
            self.assertEqual(result['label'],label)
            self.assertGreater(result['scheduled_source_packets'],1)
        def supported(*args,**kwargs):return {'label':'supported','decided_by':'scripted','steps':[]}
        accepted=B.check_support_packets([packet],supported,as_of=packet['date'],ledger=None)
        self.assertEqual(accepted['label'],'supported');self.assertTrue(accepted['all_dependencies_checked'])

    def test_missing_child_changed_binding_and_duplicate_schedule_cannot_pass(self):
        packet,_=self.packet();parts=E.source_dependency_packets(packet)
        def supported(*args,**kwargs):return {'label':'supported','decided_by':'scripted','steps':[]}
        for change in ('missing','binding','duplicate','orphan'):
            changed=copy.deepcopy(parts)
            if change=='missing':changed.pop()
            elif change=='binding':changed[-1]['source_dependency_fingerprint']='changed'
            elif change=='duplicate':changed.append(copy.deepcopy(changed[-1]))
            else:changed=changed[1:]
            with self.subTest(change=change),self.assertRaises(ValueError):
                B.check_support_packets(changed,supported,as_of=packet['date'],ledger=None)


if __name__=='__main__':unittest.main()
