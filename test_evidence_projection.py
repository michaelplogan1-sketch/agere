"""Coverage/support dependency closure from authored evidence; no providers."""
import copy,hashlib,json,unittest
from clinical_core.agere.clinical import evidence_projection as E,backstop_agent as B
from clinical_core.agere.deliverables.receipts import uncompact_json


def finding(record,quote):
    return {'record_id':record,'date':'2030-01-02','quote':quote,'value':'Candidate source interpretation'}


class Projection(unittest.TestCase):
    def test_coverage_references_global_basis_without_changing_source_support(self):
        from clinical_core.contracts import digest
        row={'patient_id':'A','record_id':'r1','date':'2030-01-02',
             'text':'Received consultation; continuing responsibility unknown.'}
        call={'name':'query_sql','input':{'sql':'SELECT * FROM record'},'result':{
            'result_view':'received','columns':list(row),'rows':[row]}}
        answer={'headline':'Received consultation; continuing responsibility unknown.',
            'table':[row], 'table_from_tool':'received','table_binding':{'result_view':'received'},
            'computed_prose':[{'field':'headline','template':'Received consultation; continuing responsibility unknown.',
                'rendered':'Received consultation; continuing responsibility unknown.',
                'bindings':{},'basis':[{'result_view':'received','row':0}],
                'sources':{'basis':{'binding':{'result_view':'received','row':0},'source_row':row}}}]}
        before=copy.deepcopy(answer)
        support_before=B.support_packets('Show received consultation and responsibility.',answer,[call],[],'2030-01-02')
        projected=E.coverage_answer(answer)
        source=projected['computed_prose'][0]['sources']['basis']
        self.assertNotIn('source_row',source)
        self.assertEqual(source['source_row_reference']['row_digest'],digest(row))
        self.assertFalse(source['source_row_reference']['source_support_established'])
        self.assertEqual(source['binding'],{'result_view':'received','row':0})
        self.assertEqual(projected['headline'],answer['headline'])
        self.assertEqual(answer,before)
        self.assertEqual(B.support_packets('Show received consultation and responsibility.',answer,[call],[],'2030-01-02'),support_before)
        changed=copy.deepcopy(answer);changed['computed_prose'][0]['sources']['basis']['source_row']['date']='2029-01-02'
        self.assertNotEqual(E.coverage_answer(changed)['computed_prose'][0]['sources']['basis']['source_row_reference'],source['source_row_reference'])

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

    def test_coverage_reads_native_explanation_and_unfinished_referral_equally(self):
        rows=[{'patient_id':'A','explanation':'Agent Z was declined because its cost was unaffordable.',
               'next_step':'Review coverage alternatives with the patient; current use is unknown.',
               'findings':json.dumps([finding('rA','Could not afford Agent Z.')])},
              {'patient_id':'B','explanation':'Read the findings for the reason.',
               'next_step':'Review each missing treatment in the findings.',
               'findings':[finding('rB','Requested the written instructions first.')]}]
        original=copy.deepcopy(rows);view=E.coverage_answer({'table':rows})
        for actual,projected in zip(rows,view['table']):
            for field in ('patient_id','explanation','next_step'):
                self.assertEqual(projected[field],actual[field])
        edges=view['coverage_contract']['dependency_edges']
        self.assertEqual([e['field']for e in edges],['findings','findings'])
        self.assertEqual(E.restore_table(view['table'],edges,{'patient_A':rows[:1],'patient_B':rows[1:]}),rows)
        self.assertEqual(rows,original)
        # Support still checks complete native text, exactly as before; the
        # coverage fix must not invalidate every cached source packet.
        support,dependencies=E.table_view(rows,defer_native_cells=True)
        self.assertIn('clinical_evidence_dependency',support[0]['explanation'])
        self.assertEqual(E.restore_table(support,dependencies,{'patient_A':rows[:1],'patient_B':rows[1:]}),rows)

    def test_coverage_preserves_all_scalar_text_without_sampling_or_field_name_rules(self):
        row={'patient_id':'A','arbitrary':'[not JSON]','long_text':'No source established this. '*2000,
             'numeric_text':'42','quoted_text':'"unknown"','empty':'','missing':None,
             'structured':' {"evidence": ["retained"]} '}
        view=E.coverage_answer({'table':[row]})
        for field in row:
            if field!='structured':self.assertEqual(view['table'][0][field],row[field])
        self.assertEqual([e['field']for e in view['coverage_contract']['dependency_edges']],['structured'])

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
        self.assertTrue(all(p['source_dependency_patient_id']=='A'for p in parts))
        for part in parts[1:]:
            body=uncompact_json(json.loads(part['text']))
            self.assertNotIn('source_claim_assertions',body)
            self.assertEqual(part['source_claim_assertions'],{claim['id']:(row['observation'].get('assertion')or row['observation'])
                for claim,row in zip(body['claim_manifest'],body['answer_rows'])})
        parent=uncompact_json(json.loads(parts[0]['text']))
        self.assertEqual(parent['answer_rows'][0]['amount'],42)
        self.assertEqual(parent['answer_rows'][0]['findings']['source_dependency_json_string']['format'],'native_ascii')
        leaves=[uncompact_json(json.loads(p['text']))for p in parts[1:]]
        supplied=[d for leaf in leaves for d in leaf['source_contexts']]
        observations=[r['observation']for leaf in leaves for r in leaf['answer_rows']]
        self.assertEqual(supplied,state['source_contexts'])
        self.assertEqual(observations,json.loads(state['answer_rows'][0]['findings']))
        self.assertTrue(all(r['final_field_uses'][0]['field']=='findings'for leaf in leaves for r in leaf['answer_rows']))
        self.assertIn('no clinical fact',parent['scope'])

    def test_pure_source_claims_move_to_children_but_mixed_interpretation_stays_in_parent(self):
        from clinical_core.agere.deliverables.receipts import readable_json
        packet,state=self.packet();state['answer_rows'][0].pop('amount');state['answer_rows'][0].pop('literal')
        state['claim_manifest']=state['claim_manifest'][:1];packet['claim_manifest']=state['claim_manifest']
        packet['text']=json.dumps(readable_json(state));parts=E.source_dependency_packets(packet)
        self.assertTrue(parts[0]['source_dependency_mechanical_only']);self.assertEqual(parts[0]['claim_manifest'],[])
        values=json.loads(state['answer_rows'][0]['findings'])
        state['answer_rows'][0]['findings']={'interpretation':'Current therapy is established','observations':values}
        packet['text']=json.dumps(readable_json(state));parts=E.source_dependency_packets(packet)
        self.assertFalse(parts[0]['source_dependency_mechanical_only'])
        self.assertEqual(parts[0]['claim_manifest'],state['claim_manifest'])

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


class NativeObservationTransport(unittest.TestCase):
    def test_bound_global_uses_complete_native_closure_and_unbound_prose_keeps_history(self):
        calls=[{'name':'query_sql','input':{'sql':'SELECT name FROM sqlite_master'},
                'result':{'result_view':'agere_result_1','columns':['name'],'rows':[{'name':'unrelated_schema'}]}},
               {'name':'query_sql','input':{'sql':'SELECT patient_id,record_id FROM record'},
                'result':{'result_view':'agere_result_2','columns':['patient_id','record_id'],
                          'rows':[{'patient_id':'A','record_id':'one'}]}},
               {'name':'query_sql','input':{'sql':'SELECT COUNT(*) AS n FROM agere_result_2'},
                'result':{'result_view':'agere_result_3','columns':['n'],'rows':[{'n':1}]}}]
        binding={'result_view':'agere_result_3','row':0,'column':'n'}
        answer={'headline':'There is 1 received record.','facts':{},'table':[{'n':1}],
                'table_binding':{'result_view':'agere_result_3'},'computed_prose':[
                    {'field':'headline','template':'There is {n} received record.','rendered':'There is 1 received record.',
                     'bindings':{'n':binding},'sources':{'n':{'binding':binding,'value':1,'source_row':{'n':1}}}}]}
        def state(a):return uncompact_json(json.loads(B.support_packets('Count received records.',a,calls,[],'2030-01-02')[0]['text']))
        closed=state(answer)
        self.assertEqual(closed['shared_evidence'],[{'n':1}])
        self.assertEqual(closed['native_dependency_scope']['required_result_views'],['agere_result_2','agere_result_3'])
        self.assertEqual(len(closed['native_bindings']),3)
        self.assertEqual(closed['global_patient_evidence'][0]['native_observations'],[{'patient_id':'A','record_id':'one'}])
        answer['headline']='An unbound interpretation.'
        open_state=state(answer)
        self.assertIn({'name':'unrelated_schema'},open_state['shared_evidence'])
        self.assertNotIn('native_dependency_scope',open_state)

    def groups(self):
        groups=[]
        for i in range(64):
            small={'patient_id':str(i),'record_id':'source-'+str(i),'text':'exact source statement','value':None,
                   **{'native_column_'+str(k):str(i)+'_'+str(k)for k in range(20)}}
            large={**small,'relation':'unresolved','date':None,'literal':{'encoding':'not a transport reference'}}
            groups.append({'patient_id':str(i),'native_observations':[small,large]})
        return groups

    def test_overlapping_rows_restore_every_field_order_and_observation(self):
        groups=self.groups();small=groups[0]['native_observations'][0]
        view=E.native_observation_view(groups)
        self.assertIsInstance(view,dict)
        self.assertEqual(len(view['patients'][0]['basis_rows']),1)
        restored=E.restore_native_observations(view)
        self.assertEqual(restored,groups)
        self.assertEqual(list(restored[0]['native_observations'][0]),list(small))
        self.assertEqual(len(restored[0]['native_observations']),2)
        self.assertNotIn('relation',restored[0]['native_observations'][0])

    def test_different_patients_sources_and_value_types_stay_separate(self):
        text='literal source context '*100
        groups=[{'patient_id':pid,'native_observations':[
            {'patient_id':pid,'record_id':'one','text':text,'value':True},
            {'patient_id':pid,'record_id':'one','text':text,'value':1},
            {'patient_id':pid,'record_id':'two','text':text,'value':True}]}for pid in ['A','B']]
        view=E.native_observation_view(groups)
        self.assertEqual(E.restore_native_observations(view),groups)
        if isinstance(view,dict):self.assertTrue(all(len(p['basis_rows'])==3 for p in view['patients']))
        with self.assertRaisesRegex(ValueError,'own patient'):
            E.native_observation_view([{'patient_id':'A','native_observations':[{'patient_id':'B'}]}])

    def test_modified_shapes_or_cross_patient_bases_are_rejected(self):
        view=E.native_observation_view(self.groups())
        for mode in ['patient','negative','duplicate_column','missing_column','value','order']:
            bad=copy.deepcopy(view)
            if mode=='patient':bad['patients'][0]['basis_rows'][0]['patient_id']='B'
            elif mode=='negative':bad['patients'][0]['observation_sequence'][0][0]=-1
            elif mode=='duplicate_column':bad['column_shapes'][0].append(bad['column_shapes'][0][0])
            elif mode=='missing_column':bad['column_shapes'][0].append('absent_source_field')
            elif mode=='value':bad['patients'][0]['basis_rows'][0]['text']='Changed source value'
            else:bad['patients'][0]['observation_sequence'].reverse()
            with self.subTest(mode=mode),self.assertRaises((ValueError,KeyError)):
                E.restore_native_observations(bad)


class SelectedJSONInput(unittest.TestCase):
    def fixture(self):
        import sqlite3
        from clinical_core.agere.clinical.sql_query import native_result_source
        data=[['A',{'lines':[{'amount':10,'currency':'USD','unused':'raw1'},
            {'amount':0,'currency':'USD'},{'amount':10,'currency':'USD','unused':'raw2'}]}],
            ['B',{}],['C',{'lines':[{'amount':5,'currency':None}]}],['D',{'lines':None}]]
        profiles=[['A','Received A'],['B','Received B']]
        query="SELECT f.patient_id,p.name,json_extract(a.value,'$.amount') AS amount,json_extract(a.value,'$.currency') AS currency FROM agere_result_1 f JOIN json_each(f.payload,'$.lines') a LEFT JOIN agere_result_2 p ON p.patient_id=f.patient_id WHERE typeof(json_extract(a.value,'$.amount'))='integer' AND json_extract(a.value,'$.amount')>0 ORDER BY f.patient_id,amount"
        with sqlite3.connect(':memory:')as db:
            db.execute('CREATE TEMP TABLE agere_result_1 AS '+native_result_source(['patient_id','payload'],data))
            db.execute('CREATE TEMP TABLE agere_result_2 AS '+native_result_source(['patient_id','name'],profiles))
            cursor=db.execute(query);columns=[x[0]for x in cursor.description];rows=[list(r)for r in cursor]
        by={'agere_result_1':{'result':{'columns':['patient_id','payload'],'rows':data,'result_view':'agere_result_1'}},
            'agere_result_2':{'result':{'columns':['patient_id','name'],'rows':profiles,'result_view':'agere_result_2'}},
            'agere_result_3':{'result':{'columns':columns,'rows':rows,'result_view':'agere_result_3'}}}
        return by,{'result_view':'agere_result_3','query':query}

    def test_selected_elements_preserve_duplicates_nulls_missing_branches_and_inputs(self):
        by,consumer=self.fixture();old=copy.deepcopy(by)
        proof=E.selected_json_input('agere_result_1','payload',[consumer],by)
        self.assertIsNotNone(proof);self.assertEqual(by,old)
        self.assertEqual(proof['selected_element_indices'],[[0,[0,2]],[2,[0]]])
        self.assertEqual(json.loads(proof['row_values'][0][1]),[{'amount':10,'currency':'USD'},{'amount':10,'currency':'USD'}])
        self.assertEqual(proof['row_values'][1],[False,None])
        self.assertEqual(proof['row_values'][3],[True,'null'])
        self.assertEqual(json.loads(proof['row_values'][2][1]),[{'amount':5,'currency':None}])
        self.assertTrue(proof['checks'][0]['complete_original_output_exact'])

    def test_changed_output_cannot_be_qualified_by_a_matching_field_or_count(self):
        by,consumer=self.fixture();by['agere_result_3']['result']['rows'][0][2]=11
        self.assertIsNone(E.selected_json_input('agere_result_1','payload',[consumer],by))

    def test_whole_elements_positions_dynamic_paths_and_aggregates_keep_full_input(self):
        by,consumer=self.fixture()
        for text in ("a.value","a.key","json_extract(a.value,p.name)","COUNT(*)"):
            altered={**consumer,'query':consumer['query'].replace("json_extract(a.value,'$.amount') AS amount",text+' AS amount',1)}
            with self.subTest(text=text):self.assertIsNone(E.selected_json_input('agere_result_1','payload',[altered],by))

    def test_union_of_actual_consumers_retains_each_predicate_output(self):
        import sqlite3
        from clinical_core.agere.clinical.sql_query import native_result_source
        by,consumer=self.fixture();query=consumer['query'].replace("'$.amount')>0","'$.amount')>=0")
        with sqlite3.connect(':memory:')as db:
            for name in ('agere_result_1','agere_result_2'):
                result=by[name]['result'];db.execute('CREATE TEMP TABLE '+name+' AS '+native_result_source(result['columns'],result['rows']))
            cursor=db.execute(query);by['agere_result_4']={'result':{'columns':[x[0]for x in cursor.description],'rows':[list(r)for r in cursor]}}
        proof=E.selected_json_input('agere_result_1','payload',[consumer,{'result_view':'agere_result_4','query':query}],by)
        self.assertEqual(proof['selected_element_indices'],[[0,[0,1,2]],[2,[0]]])
        self.assertEqual([c['output_rows']for c in proof['checks']],[3,4])

    def patient_projection(self):
        from clinical_core.contracts import digest
        by,consumer=self.fixture();selection=E.selected_json_input('agere_result_1','payload',[consumer],by)
        rows=[[r[0]]for r in by['agere_result_1']['result']['rows']]
        return {'schema':'native-column-projection/1','result_view':'agere_result_1',
            'columns':['patient_id'],'rows':rows,'row_count':len(rows),'projected_rows_digest':digest(rows),
            'complete_receipt_digest':selection['complete_receipt_digest'],
            'json_path_contract':'native-replayed-json-selection/1',
            'json_path_observations':[{k:selection[k]for k in ('column','path','value_encoding','row_values','scope')}],
            'json_selection_proofs':[{k:v for k,v in selection.items()if k!='row_values'}]}

    def test_required_patient_inputs_keep_every_selected_value_and_unknown(self):
        projection=self.patient_projection();old=copy.deepcopy(projection)
        summary,patients=E.patient_selected_json_inputs(projection,{'A','C'})
        self.assertEqual(projection,old);self.assertEqual(set(patients),{'A','C'})
        self.assertEqual(summary['unused_native_input_rows'],2)
        self.assertEqual(patients['A']['indexed_rows'],[[0,['A']]])
        self.assertEqual(json.loads(patients['A']['indexed_json_values'][0]['indexed_values'][0][1][1]),
            [{'amount':10,'currency':'USD'},{'amount':10,'currency':'USD'}])
        self.assertEqual(json.loads(patients['C']['indexed_json_values'][0]['indexed_values'][0][1][1]),
            [{'amount':5,'currency':None}])
        self.assertIsNone(E.patient_selected_json_inputs(projection,{'A'}))

    def test_unwitnessed_values_or_missing_selected_elements_cannot_disappear(self):
        for change in ('unused','selected','source'):
            projection=self.patient_projection()
            if change=='unused':projection['json_path_observations'][0]['row_values'][1]=[True,'[{"amount":100}]']
            elif change=='selected':projection['json_path_observations'][0]['row_values'][0]=[True,'[]']
            else:projection['json_selection_proofs'][0]['complete_receipt_digest']='changed'
            with self.subTest(change=change),self.assertRaises(ValueError):
                E.patient_selected_json_inputs(projection,{'A','C'})


class NativeIntegrityModelView(unittest.TestCase):
    def global_table_packet(self):
        rows=[{'provider':'Clinic '+str(i),'observed':i,'unknown':None,
               'source_statement':'Preference unknown; no cause established.'}for i in range(40)]
        answer={'headline':'Observed values.','facts':{'range':'Unverified range claim.'}}
        claims=[{'id':'headline','field':'headline','path':['answer','headline']},
                {'id':'fact_0','field':'facts.range','path':['answer','facts','range']}]
        for i,row in enumerate(rows):
            for key in row:
                claims.append({'id':'table_'+str(len(claims)),'field':'table.'+str(i)+'.'+key,
                               'path':['all_final_rows',i,key]})
        return {'id':'summary','answer':answer,'all_final_rows':rows,
                'claim_manifest':claims,'shared_evidence':[{'text':'Received source statement.'}],
                'native_calculation_contracts':[{'input':{'sql':'SELECT * FROM received'}}]}

    def test_complete_global_address_manifest_is_native_while_every_value_remains_visible(self):
        from clinical_core.agere.deliverables.receipts import readable_json
        original=self.global_table_packet();before=copy.deepcopy(original)
        wire=E.native_integrity_model_view(json.dumps(readable_json(original)),support_dependencies=True)
        model=uncompact_json(json.loads(wire['text']))
        self.assertTrue(wire['projected']);self.assertTrue(wire['native_projection']['roundtrip_exact'])
        for key in ('answer','all_final_rows','shared_evidence','native_calculation_contracts'):
            self.assertEqual(model[key],original[key])
        reference=model['claim_manifest']['native_receipt_reference']
        self.assertEqual(wire['native_projection']['proofs'][reference],original['claim_manifest'])
        summary=model['native_integrity_projection']['claim_manifest']
        self.assertEqual(summary['global_claims'],original['claim_manifest'][:2])
        self.assertEqual(summary['table_cells'],160)
        self.assertEqual(original,before)

    def test_incomplete_changed_extra_or_reordered_manifest_never_gets_projection(self):
        from clinical_core.agere.deliverables.receipts import readable_json
        for mutation in ('missing','changed','extra','reordered','patient'):
            state=self.global_table_packet()
            if mutation=='missing':state['claim_manifest'].pop()
            if mutation=='changed':state['claim_manifest'][-1]['path'][-1]='wrong'
            if mutation=='extra':state['claim_manifest'].append(copy.deepcopy(state['claim_manifest'][-1]))
            if mutation=='reordered':state['claim_manifest'].reverse()
            if mutation=='patient':state['all_final_rows'][0]['patient_id']='P1'
            text=json.dumps(readable_json(state))
            with self.subTest(mutation=mutation):
                wire=E.native_integrity_model_view(text,support_dependencies=True)
                self.assertFalse(wire['projected']);self.assertEqual(wire['text'],text)

    def test_global_manifest_projection_requires_support_summary_context(self):
        from clinical_core.agere.deliverables.receipts import readable_json
        text=json.dumps(readable_json(self.global_table_packet()))
        self.assertFalse(E.native_integrity_model_view(text)['projected'])
        self.assertFalse(E.native_integrity_model_view(text,coverage_dependencies=True)['projected'])

    def test_repair_packet_deduplicates_manifest_without_mutating_bound_producer(self):
        from clinical_core.agere.deliverables.receipts import readable_json
        from clinical_core.contracts import digest
        state=self.global_table_packet()
        packet={'id':'summary','text':json.dumps(readable_json(state)),
                'claim_manifest':state['claim_manifest'],'source_context_complete':True,'dependency_complete':True}
        before=copy.deepcopy(packet);model=E.source_packet_model_view(packet)
        self.assertEqual(packet,before);self.assertIsInstance(model['claim_manifest'],dict)
        self.assertEqual(model['original_packet_text_digest'],digest(packet['text']))
        projected=uncompact_json(json.loads(model['text']))
        self.assertEqual(projected['all_final_rows'],state['all_final_rows'])
        self.assertEqual(projected['shared_evidence'],state['shared_evidence'])
        packet['claim_manifest']=packet['claim_manifest'][:-1]
        self.assertEqual(E.source_packet_model_view(packet),packet)

    def test_support_dependency_addresses_restore_every_cell_without_source_rewriting(self):
        from clinical_core.agere.deliverables.receipts import readable_json,uncompact_json
        table=[{'patient_id':str(i),'statement':[finding(str(i),'Dated source, preference unknown.')],
            'amount_cents':None if i%2 else 0}for i in range(60)]
        view,edges=E.table_view(table,defer_native_cells=True,index_dependencies=True)
        original={'id':'summary','all_final_rows':view,'required_clinical_dependencies':edges,
            'native_column_inputs':[{'indexed_json_values':[{'digest':'a'*64,'quote':'Unknown source response.'}]}]}
        wire=E.native_integrity_model_view(json.dumps(readable_json(original)),support_dependencies=True)
        self.assertTrue(wire['projected']);model=uncompact_json(json.loads(wire['text']))
        self.assertEqual(model['all_final_rows'],view)
        self.assertEqual(model['native_column_inputs'],original['native_column_inputs'])
        proof=wire['native_projection'];model.pop('native_integrity_projection')
        for path in reversed(proof['proof_paths']):
            parent=model
            for key in path[:-1]:parent=parent[key]
            parent[path[-1]]=proof['proofs'][parent[path[-1]]['native_receipt_reference']]
        self.assertEqual(model,original)
        bad=copy.deepcopy(original);bad['required_clinical_dependencies'][0]['packet']='patient_59'
        with self.assertRaises(ValueError):E.native_integrity_model_view(json.dumps(readable_json(bad)),support_dependencies=True)

    def test_coverage_coordinates_roundtrip_without_hiding_visible_values(self):
        from clinical_core.agere.deliverables.receipts import readable_json,uncompact_json
        table=[{'patient_id':pid,'account_id':f'{pid}-{i}','balance_cents':0 if i==0 else None,
            'explanation':'Current hardship remains unknown.',
            'source':json.dumps([finding(f'{pid}-{i}','No preference established. '+str(i))])}
            for pid in ('A','B')for i in range(40)]
        answer=E.coverage_answer({'status':'partial','table':table,'missing':['Current preference unknown']})
        original={'question':'Recorded balances and source statements','result':{'answer':answer}}
        text=json.dumps(readable_json(original));wire=E.native_integrity_model_view(text,coverage_dependencies=True)
        self.assertTrue(wire['projected']);self.assertLess(wire['bytes'],len(text.encode()))
        model=uncompact_json(json.loads(wire['text']))
        self.assertEqual(model['result']['answer']['table'],answer['table'])
        self.assertEqual(model['result']['answer']['missing'],answer['missing'])
        model.pop('native_integrity_projection')
        proof=wire['native_projection']
        for path in reversed(proof['proof_paths']):
            parent=model
            for key in path[:-1]:parent=parent[key]
            parent[path[-1]]=proof['proofs'][parent[path[-1]]['native_receipt_reference']]
        self.assertEqual(model,original)
        self.assertEqual(E.restore_table(answer['table'],answer['coverage_contract']['dependency_edges'],
            {'patient_A':table[:40],'patient_B':table[40:]}),table)

    def test_coverage_missing_changed_or_repeated_coordinates_fail_before_dispatch(self):
        from clinical_core.agere.deliverables.receipts import readable_json
        base={'result':{'answer':E.coverage_answer({'table':[
            {'patient_id':'A','source':[finding('rA','Unknown preference.')]},
            {'patient_id':'B','source':[finding('rB','Dated statement.')]}
        ]})}}
        for change in ('patient','field','ordinal','digest','missing','duplicate','bool'):
            state=copy.deepcopy(base);a=state['result']['answer'];edges=a['coverage_contract']['dependency_edges']
            if change=='patient':edges[0]['patient_id']='B'
            elif change=='field':edges[0]['field']='another_field'
            elif change=='ordinal':edges[1]['answer_row']=1
            elif change=='digest':edges[0]['source_cell_digest']='not-a-hash'
            elif change=='missing':a['table'][0].pop('source')
            elif change=='duplicate':a['table'][1]['source']['clinical_evidence_dependency']=0
            else:a['table'][0]['source']['clinical_evidence_dependency']=False
            with self.subTest(change=change),self.assertRaises(ValueError):
                E.native_integrity_model_view(json.dumps(readable_json(state)),coverage_dependencies=True)

    def test_coverage_projection_is_explicit_and_does_not_rewrite_source_fields(self):
        from clinical_core.agere.deliverables.receipts import readable_json,uncompact_json
        value='a'*64
        original={'question':value,'result':{'answer':E.coverage_answer({'table':[
            {'patient_id':'A','digest':value,'source':[finding('r',value)]}]})}}
        text=json.dumps(readable_json(original));wire=E.native_integrity_model_view(text)
        self.assertFalse(wire['projected'])
        model=uncompact_json(json.loads(E.native_integrity_model_view(text,coverage_dependencies=True)['text']))
        self.assertEqual(model['question'],value)
        self.assertEqual(model['result']['answer']['table'][0]['digest'],value)

    def test_native_hashes_are_addresses_while_source_sql_and_clinical_values_stay_literal(self):
        from clinical_core.agere.clinical.evidence_projection import native_integrity_model_view
        from clinical_core.agere.deliverables.receipts import readable_json,uncompact_json
        from clinical_core.contracts import digest
        value='a'*64;sql="SELECT * FROM records WHERE exact_value='"+value+"'"
        state={'answer_rows':[{'patient_id':'A','digest':value,'quote':'Denies taking it.'}],
            'source_contexts':[{'patient_id':'A','text':'Denies taking it. '+value}],
            'native_bindings':[{'result_digest':str(i).zfill(64),'result_view':'r'+str(i)}for i in range(100)],
            'native_calculation_contracts':[{'input':{'sql':sql},'source_definition_keys':[value]}],
            'executed_source_definitions':{value:{'query':sql,'source_digest':value}},
            'evidence_provenance':[{'row_digest':value,'origins':[{'native_column_projection':{
                'schema':'authored-validation-proof','complete_receipt_digest':value,'rows':[value]*50}}]}]}
        text=json.dumps(readable_json(state));result=native_integrity_model_view(text)
        self.assertTrue(result['projected']);model=uncompact_json(json.loads(result['text']))
        self.assertEqual(model['answer_rows'],state['answer_rows']);self.assertEqual(model['source_contexts'],state['source_contexts'])
        self.assertEqual(model['native_calculation_contracts'][0]['input']['sql'],sql)
        key=model['native_calculation_contracts'][0]['source_definition_keys'][0]
        self.assertEqual(model['executed_source_definitions'][key]['query'],sql)
        self.assertEqual(model['native_integrity_projection']['complete_native_packet_digest'],digest(state))
        self.assertTrue(result['native_projection']['roundtrip_exact'])
        self.assertEqual(native_integrity_model_view(text),result)
        changed=copy.deepcopy(state);changed['native_bindings'][0]['result_digest']='b'*64
        self.assertNotEqual(native_integrity_model_view(json.dumps(readable_json(changed)))['text'],result['text'])
        self.assertEqual(state['evidence_provenance'][0]['row_digest'],value)

    def test_plain_source_with_digest_named_fields_is_unchanged(self):
        from clinical_core.agere.clinical.evidence_projection import native_integrity_model_view
        from clinical_core.agere.deliverables.receipts import readable_json
        text=json.dumps(readable_json({'answer_rows':[{'digest':'a'*64,'native_column_projection':{'quote':'No use reported.'}}]}))
        self.assertEqual(native_integrity_model_view(text),{'text':text,'bytes':len(text.encode()),'projected':False})


class AggregateJsonProjection(unittest.TestCase):
    def fixture(self):
        import sqlite3
        rows=[['A','received',json.dumps({'accounts':[
            {'currency':'USD','amounts':{'cents':-7},'unused_history':['source']*100},
            {'currency':'USD','amounts':{'cents':-7}},
            {'currency':'EUR','amounts':{'cents':None}},
            {'currency':'EUR','amounts':{}}], 'unassigned':[{'private_unused':'one'},{'private_unused':'two'}]})],
            ['B','unknown',json.dumps({'accounts':None,'unassigned':[]})],
            ['C','unknown','{}'],['D','received',json.dumps({'accounts':[],'unassigned':None})]]
        columns=['patient_id','status','payload'];by={'agere_result_1':{'name':'execute_analysis','result':{'columns':columns,'rows':rows}}}
        queries=["SELECT r.status,COUNT(*) AS patients,SUM(json_array_length(r.payload,'$.accounts')) AS accounts,SUM(json_array_length(r.payload,'$.unassigned')) AS unassigned FROM agere_result_1 r GROUP BY r.status",
                 "SELECT json_extract(a.value,'$.currency') AS currency,COUNT(*) AS accounts,SUM(json_extract(a.value,'$.amounts.cents')) AS cents FROM agere_result_1 r JOIN json_each(r.payload,'$.accounts') a GROUP BY 1 ORDER BY 1"]
        consumers=[]
        with sqlite3.connect(':memory:')as db:
            db.execute('CREATE TABLE agere_result_1(patient_id,status,payload)');db.executemany('INSERT INTO agere_result_1 VALUES (?,?,?)',rows)
            for i,query in enumerate(queries,2):
                key='agere_result_'+str(i);cursor=db.execute(query)
                by[key]={'result':{'columns':[c[0]for c in cursor.description],'rows':[list(r)for r in cursor]}}
                consumers.append({'result_view':key,'query':query,'columns_read':['payload','status']if i==2 else['payload']})
        return by,consumers

    def test_full_population_aggregate_preserves_values_cardinality_and_missing(self):
        by,consumers=self.fixture();before=copy.deepcopy(by)
        proof=E.native_json_projection('agere_result_1',by['agere_result_1']['result']['columns'],by['agere_result_1']['result']['rows'],consumers,by)
        self.assertEqual(proof['contract'],'native-replayed-json-array-fields/1')
        self.assertEqual(len(proof['checks']),2)
        arrays={r['path']:r for r in proof['observations']}
        accounts=arrays['$.accounts']['row_values']
        self.assertEqual(len(accounts),4)
        self.assertEqual(json.loads(accounts[0][1]),[{'amounts':{'cents':-7},'currency':'USD'},
            {'amounts':{'cents':-7},'currency':'USD'},{'amounts':{'cents':None},'currency':'EUR'},{'currency':'EUR'}])
        self.assertEqual(accounts[1:],[ [True,'null'],[False,None],[True,'[]'] ])
        self.assertEqual(json.loads(arrays['$.unassigned']['row_values'][0][1]),[{},{}])
        self.assertNotIn('unused_history',json.dumps(proof,default=sorted));self.assertEqual(by,before)
        self.assertEqual(by['agere_result_3']['result']['rows'],[[None,1,None],['EUR',2,None],['USD',2,-14]])

    def test_changed_amount_currency_or_output_prevents_projection(self):
        for change in ('amount','currency','output'):
            by,consumers=self.fixture()
            if change=='output':by['agere_result_3']['result']['rows'][-1][-1]=14
            else:
                rows=by['agere_result_1']['result']['rows'];payload=json.loads(rows[0][2])
                if change=='amount':payload['accounts'][0]['amounts']['cents']=7
                else:payload['accounts'][0]['currency']='GBP'
                rows[0][2]=json.dumps(payload)
            with self.subTest(change=change):self.assertIsNone(E.aggregate_json_input('agere_result_1','payload',consumers,by))

    def test_native_numeric_role_exposes_all_groups_without_model_recounting(self):
        by,consumers=self.fixture()
        for key,call in by.items():call['result']['result_view']=key
        proof=E.aggregate_json_input('agere_result_1','payload',consumers,by)
        projection={'result_view':'agere_result_1','consumers':consumers,
            'json_path_contract':proof['contract'],'json_path_observations':proof['observations'],
            'json_path_output_checks':proof['checks']}
        result=E.aggregate_json_calculation_view(projection,by['agere_result_1'],list(by.values()))
        self.assertTrue(result['full_replayed_outputs_exact'])
        self.assertEqual(result['complete_executed_outputs'][-1]['rows'],by['agere_result_3']['result']['rows'])
        accounts=result['profiles'][0]
        self.assertEqual((accounts['array_elements'],accounts['missing_arrays'],accounts['null_arrays']),(4,1,1))
        fields={f['path']:f for f in accounts['fields']}
        self.assertEqual((fields['$.amounts.cents']['present'],fields['$.amounts.cents']['null'],fields['$.amounts.cents']['absent']),(3,1,1))
        self.assertEqual(fields['$.currency']['complete_categories'],[{'value':'USD','elements':2},{'value':'EUR','elements':2}])
        self.assertNotIn('row_values',json.dumps(result))
        changed=copy.deepcopy(projection);changed['json_path_observations'][0]['row_values'][0][1]='[]'
        self.assertIsNone(E.aggregate_json_calculation_view(changed,by['agere_result_1'],list(by.values())))
        # SQLite can coerce text into a SUM. A native arithmetic review must
        # not hide those source values or treat narrative text as a quantity.
        rows=by['agere_result_1']['result']['rows'];value=json.loads(rows[0][2]);value['accounts'][0]['amounts']['cents']='-7';rows[0][2]=json.dumps(value)
        proof=E.aggregate_json_input('agere_result_1','payload',consumers,by)
        projection.update(json_path_observations=proof['observations'],json_path_output_checks=proof['checks'])
        self.assertIsNone(E.aggregate_json_calculation_view(projection,by['agere_result_1'],list(by.values())))

    def test_whole_dynamic_or_parse_position_reads_keep_complete_source(self):
        for query in ["SELECT r.payload FROM agere_result_1 r",
                      "SELECT json_array_length(r.payload,r.status) FROM agere_result_1 r",
                      "SELECT a.value FROM agere_result_1 r JOIN json_each(r.payload,'$.accounts') a",
                      "SELECT a.id FROM agere_result_1 r JOIN json_each(r.payload,'$.accounts') a",
                      "SELECT a.value FROM agere_result_1 r JOIN json_each(r.payload,'$.accounts') a WHERE r.payload LIKE '%source%'",
                      "SELECT json_array_length(r.payload,'$.accounts') FROM agere_result_1 r JOIN unrelated u ON u.patient_id=r.patient_id"]:
            by,_=self.fixture()
            with self.subTest(query=query):self.assertIsNone(E.aggregate_json_input('agere_result_1','payload',[{'query':query,'result_view':'agere_result_2'}],by))


if __name__=='__main__':unittest.main()
