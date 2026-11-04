"""Localized edits retain the entire candidate and receive complete validation."""
import copy,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import execution_state as E,backstop_agent as B,semantic_analyst as S
from clinical_core.agere import model_policy as M
from clinical_core.contracts import digest


class ProseRepairs(unittest.TestCase):
    def test_compact_literal_binding_runs_existing_full_review_without_rewriting(self):
        feedback=self.located_failure();original=copy.deepcopy(self.base);reviews=[]
        address={'result_view':'agere_result_1','where':[],'column':'events'}
        def review(candidate,calls):
            reviews.append(copy.deepcopy(candidate))
            if len(reviews)==1:return feedback
            self.compile(candidate);return {'status':'confirmed'}
        def send(body):
            state=json.loads(body['messages'][0]['content'])
            self.assertIn('bind_literals',state['focused_prose_repair']['instruction'])
            from clinical_core.agere import subscription_transport as T
            proposal=self.patch(review={'bind_literals':[{'literal':'two','binding':address}]})
            schema=body['output_config']['format']['schema']
            self.assertEqual(schema['required'],['repair_draft']);self.assertFalse(schema['additionalProperties'])
            with patch.object(T,'run_cli',return_value={'structured_output':proposal})as call:
                result=T.respond(body)
            self.assertEqual(call.call_args.kwargs['json_schema'],schema)
            return result,0.,False
        out=S.tool_loop('Explain both entities',[],[],lambda use:self.fail('No new source needed'),
            send=send,model=M.SONNET,max_tools=None,initial_calls=self.calls,initial_answer=self.base,
            review=review,compact_execution_state=True)
        self.assertEqual(len(reviews),2);self.assertEqual(out['prose_repairs'][0]['changed_fields'],1)
        self.assertEqual(out['answer']['table'][0]['review'],'Review 2 events on 2030-01-01; preserve the uncertainty.')
        self.assertEqual(out['answer']['sources'],original['sources'])
        self.assertEqual(out['answer']['row_prose']['rows'][1],original['row_prose']['rows'][1])
        self.assertEqual(self.base,original)

    def test_literal_binding_preserves_placeholders_braces_and_original_occurrence_indices(self):
        address={'result_view':'agere_result_1','row':0,'column':'events'}
        native={'agere_result_1':self.calls[0]['result']}
        value={'template':'Literal {{prior}}: {n} events; two before two after.', 'bindings':{'n':address}}
        operation={'bind_literals':[{'literal':'two','occurrence':1,'binding':address},
                                   {'literal':'two','occurrence':0,'binding':address}]}
        before=copy.deepcopy(value);out=E.bind_prose_literals(value,operation,native)
        self.assertEqual(E.repair_rendered_value(out,native),'Literal {prior}: 2 events; 2 before 2 after.')
        self.assertEqual(out['bindings']['n'],address);self.assertEqual(value,before)
        self.assertEqual(len(out['bindings']),3)
        for edits in ([{'literal':'two','binding':address}],
                      [{'literal':'two','binding':address,'occurrence':True}],
                      [{'literal':'two','binding':address,'occurrence':2}],
                      [{'literal':'n','binding':address}],
                      [{'literal':'three','binding':address}],
                      [{'literal':'two','binding':address,'occurrence':0}]*2):
            with self.subTest(edits=edits),self.assertRaises(ValueError):
                E.bind_prose_literals(value,{'bind_literals':edits},native)
        with self.assertRaisesRegex(ValueError,'preserve the exact value'):
            E.bind_prose_literals('Three events',{'bind_literals':[{'literal':'Three','binding':address}]},native)

    def test_word_literal_cannot_change_a_drug_name_or_other_larger_word(self):
        native={'agere_result_1':{'columns':['patient_id','n'],'rows':[['A',1]],'truncated':False}}
        address={'result_view':'agere_result_1','column':'n','where':[]}
        text='Spironolactone: one observation; someone recorded one more.'
        operation={'bind_literals':[{'literal':'one','binding':address,'occurrence':i}for i in (0,1)]}
        out=E.bind_prose_literals(text,operation,native,key={'patient_id':'A'})
        self.assertEqual(E.repair_rendered_value(out,native),'Spironolactone: 1 observation; someone recorded 1 more.')
        self.assertEqual(E.canonical_repair_prose(text),E.canonical_repair_prose(E.repair_rendered_value(out,native)))
        with self.assertRaisesRegex(ValueError,'matches'):
            E.bind_prose_literals('Spironolactone',{'bind_literals':[{'literal':'one','binding':address}]},native,key={'patient_id':'A'})

    def test_compact_binding_cannot_bypass_entity_basis_or_source_meaning_boundary(self):
        feedback=self.located_failure();self.calls[0]['result']['rows'][1][1]=2
        address={'result_view':'agere_result_1','row':1,'column':'events'}
        proposal=self.patch(review={'bind_literals':[{'literal':'two','binding':address}]})
        with self.assertRaisesRegex(E.BindingReferenceError,'another entity'):
            E.apply_prose_repair(proposal,self.base,calls=self.calls,feedback=feedback)
        before=copy.deepcopy(self.base)
        with self.assertRaisesRegex(ValueError,'binding-only'):
            E.apply_prose_repair(proposal,self.base,calls=self.calls,feedback={**feedback,
                'prose_repair':{**feedback['prose_repair'],'intent':'source_meaning_review'}})
        with self.assertRaisesRegex(ValueError,'binding-only'):
            E.apply_prose_repair(proposal,self.base,calls=self.calls)
        self.assertEqual(self.base,before)

    def test_binding_schema_is_bound_to_current_digest_and_located_fields_only(self):
        feedback=self.located_failure();focus=E.focused_prose_state(self.base,feedback,self.calls)
        schema=E.binding_repair_schema(focus)
        repair=schema['properties']['repair_draft']['properties'];row=repair['rows']['items']['properties']
        self.assertEqual(repair['draft_digest']['enum'],[digest(self.base)])
        self.assertEqual(row['key']['properties']['patient_id']['enum'],['A'])
        self.assertEqual(set(row['fields']['properties']),{'review'})
        self.assertEqual(row['fields']['properties']['review']['required'],['bind_literals'])
        self.assertNotIn('basis_additions',row)
        binding=row['fields']['properties']['review']['properties']['bind_literals']['items']['properties']['binding']
        self.assertEqual(set(binding['properties']),{'result_view','column','where'})
        operation={'bind_literals':[{'literal':'two','binding':{'result_view':'agere_result_1','row':0,'column':'events'}}]}
        proposal=self.patch(review=operation)
        self.compile(E.apply_prose_repair(proposal,self.base,calls=self.calls,feedback=feedback))
        for mutation in ('digest','key','field','unknown','negative'):
            changed=copy.deepcopy(proposal);r=changed['repair_draft']
            if mutation=='digest':r['draft_digest']='stale'
            if mutation=='key':r['rows'][0]['key']['patient_id']='B'
            if mutation=='field':r['rows'][0]['fields']={'explanation':operation}
            if mutation=='unknown':r['rows'][0]['fields']['review']['extra']='changed'
            if mutation=='negative':r['rows'][0]['fields']['review']['bind_literals'][0]['binding']['row']=-1
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                E.apply_prose_repair(changed,self.base,calls=self.calls,feedback=feedback)
        self.assertIsNone(E.binding_repair_schema({**focus,'intent':'source_meaning_review'}))
        self.assertIsNone(E.binding_repair_schema({**focus,'targets':[{**focus['targets'][0],'code':'invalid_row_prose'}]}))

    def test_keyed_binding_resolves_reordered_source_rows_and_derives_bases(self):
        feedback=self.located_failure();original=copy.deepcopy(self.base)
        for rows in ([['B','b',2],['A','old',9],['A','current',2]],
                     [['A','current',2],['B','b',2],['A','old',9]]):
            calls=copy.deepcopy(self.calls)+[{'name':'query_sql','result':{'result_view':'agere_result_2',
                'columns':['patient_id','record_id','events'],'rows':rows,'truncated':False}}]
            binding={'result_view':'agere_result_2','column':'events','where':[{'column':'record_id','value':'current'}]}
            proposal=self.patch(review={'bind_literals':[{'literal':'two','binding':binding}]})
            merged=E.apply_prose_repair(proposal,self.base,calls=calls,feedback=feedback)
            row=merged['row_prose']['rows'][0];index=rows.index(['A','current',2])
            self.assertEqual(row['basis'],original['row_prose']['rows'][0]['basis']+[{'result_view':'agere_result_2','row':index}])
            self.assertEqual(next(iter(row['fields']['review']['bindings'].values())),{'result_view':'agere_result_2','row':index,'column':'events'})
            # No accepted answer is created by the selector; the existing full
            # compiler must still consume the resolved source-address contract.
            B.materialize_table(merged,calls);B.bind_computed_prose(merged,calls)
            self.assertEqual(E.repair_rendered_value(row['fields']['review'],{c['result']['result_view']:c['result']for c in calls}),
                'Review 2 events on 2030-01-01; preserve the uncertainty.')
            self.assertEqual(self.base,original)

    def test_key_selection_rejects_ambiguity_foreign_missing_incomplete_and_bad_filters(self):
        feedback=self.located_failure();original=copy.deepcopy(self.base)
        extra={'name':'query_sql','result':{'result_view':'agere_result_2','columns':['patient_id','record_id','events'],
            'rows':[['A','a',2],['A','b',2],['B','b',2]],'truncated':False}}
        good={'result_view':'agere_result_2','column':'events','where':[{'column':'record_id','value':'a'}]}
        for kind in ('ambiguous','missing','foreign','column','duplicate','incomplete','mixed_address','nonscalar','wrong_value'):
            calls=copy.deepcopy(self.calls)+[copy.deepcopy(extra)];binding=copy.deepcopy(good)
            if kind=='ambiguous':binding['where']=[]
            if kind=='missing':binding['where'][0]['value']='absent'
            if kind=='foreign':binding['where']=[{'column':'patient_id','value':'B'}]
            if kind=='column':binding['where'][0]['column']='absent'
            if kind=='duplicate':binding['where']*=2
            if kind=='incomplete':calls[-1]['result']['truncated']=True
            if kind=='mixed_address':binding['row']=0
            if kind=='nonscalar':binding['where'][0]['value']={}
            if kind=='wrong_value':calls[-1]['result']['rows'][0][-1]=3
            with self.subTest(kind=kind),self.assertRaises(E.BindingReferenceError)as error:
                E.apply_prose_repair(self.patch(review={'bind_literals':[{'literal':'two','binding':binding}]}),
                    self.base,calls=calls,feedback=feedback)
            self.assertEqual(error.exception.detail['key'],{'patient_id':'A'})
            self.assertEqual(error.exception.detail['field'],'review');self.assertEqual(self.base,original)

    def test_key_selector_requires_all_compound_keys_and_type_exact_values(self):
        native={'agere_result_2':{'columns':['entity','period','record','value'],
            'rows':[['A',2030,1,2],['A',2029,True,2],['A',2030,None,2]],'truncated':False}}
        binding={'result_view':'agere_result_2','column':'value','where':[{'column':'record','value':1}]}
        self.assertEqual(E.resolve_prose_binding(binding,native,{'entity':'A','period':2030})['row'],0)
        for key in ({'entity':'A','period':2029},{'entity':'A','period':2030,'missing':'key'},None):
            with self.assertRaises(ValueError):E.resolve_prose_binding(binding,native,key)
        binding['where'][0]['value']=None
        self.assertEqual(E.resolve_prose_binding(binding,native,{'entity':'A','period':2030})['row'],2)

    def test_ambiguous_literal_feedback_names_exact_field_literal_and_match_count(self):
        feedback=self.located_failure();self.base['row_prose']['rows'][0]['fields']['review']='two and two'
        feedback=B.review_final(SimpleNamespace(),'Explain each entity',self.base,self.calls,
            SimpleNamespace(read={'note_A':{'text':'Dated observation'}}),ledger=None,as_of='2030-05-27')
        proposal=self.patch(review={'bind_literals':[{'literal':'two','binding':{'result_view':'agere_result_1','column':'events','where':[]}}]})
        with self.assertRaises(E.BindingReferenceError)as error:
            E.apply_prose_repair(proposal,self.base,calls=self.calls,feedback=feedback)
        self.assertEqual(error.exception.detail['field'],'review')
        self.assertIn('"matches": 2',error.exception.detail['reason']);self.assertIn('"literal": "two"',error.exception.detail['reason'])

    def test_binding_reading_selects_explicit_records_without_hiding_native_rows(self):
        for cite in (True,False):
            with self.subTest(cite=cite):
                self.setUp();self.located_failure()
                if cite:self.base['row_prose']['rows'][0]['fields']['review']='Review two events reported in note_A.'
                columns=['patient_id','record_id','text'];rows=[['A','note_A','Complete cited observation.'],
                    ['A','note_A_extra','Complete separate observation.'],['B','note_B','Other entity.']]
                source={'result_view':'agere_result_2','columns':columns,'rows':rows,'truncated':False,
                    'actor_reading':{'mode':'complete_rows','rows_digest':digest(rows),'columns_digest':digest(columns)}}
                calls=self.calls+[{'name':'query_sql','input':{'sql':'SELECT patient_id,record_id,text FROM notes','read_all_rows':True},'result':source}]
                feedback=B.review_final(SimpleNamespace(),'Explain each entity',self.base,calls,
                    SimpleNamespace(read={'note_A':{'text':'Dated observation'}}),ledger=None,as_of='2030-05-27')
                before=copy.deepcopy(calls)
                state=json.loads(E.messages('Both entities',calls,candidate=self.base,feedback=feedback)[0]['content'])
                reading=state['executed_results'][1]['repair_reading']
                self.assertEqual(reading['native_row_indices'],[0]if cite else[0,1])
                self.assertEqual(reading['native_total_rows'],3);self.assertTrue(reading['complete_native_result_retained'])
                self.assertEqual(reading['rows'][0]['text'],'Complete cited observation.')
                self.assertEqual(calls,before)
                self.assertEqual(state['focused_prose_repair']['complete_row_prose_rows'],2)
                if cite:
                    # Source-meaning review must still see the broader entity
                    # evidence even when the proposed sentence cites one note.
                    self.base['row_prose']['rows'][0]['fields']['review']='Inspect note_A; current responsibility remains unknown.'
                    self.compile(self.base)
                    packet,support=self.source_review()
                    source_feedback={'repair_kind':'unresolved_verification',
                        'prose_repair':E.source_prose_repair(self.base,support,[packet],{}),'support':support}
                    wider=json.loads(E.messages('Both entities',calls,candidate=self.base,feedback=source_feedback)[0]['content'])
                    self.assertEqual(wider['focused_prose_repair']['intent'],'source_meaning_review')
                    self.assertEqual(wider['executed_results'][1]['repair_reading']['native_row_indices'],[0,1])

    def test_date_pair_equivalence_preserves_calendar_values_and_other_quantities(self):
        self.assertEqual(E.canonical_repair_prose('Recorded on 2026-09-03/17.'),
                         E.canonical_repair_prose('Recorded on 2026-09-03 and 2026-09-17.'))
        for text in ('2026-02-03/30','2026-13-03/17','10/20 mg','2026-09-03/17/20','2026-09-03/17.5','2026-09-03/17mg'):
            with self.subTest(text=text):self.assertEqual(E.expand_date_pairs(text),text)
        for changed in ('2026-09-03 and 2026-09-18','2026-09-03 and 2026-10-17'):
            self.assertNotEqual(E.canonical_repair_prose('2026-09-03/17'),E.canonical_repair_prose(changed))

    def test_mixed_binding_batch_retains_valid_fields_then_reviews_remaining_field(self):
        self.base['status']='partial'
        self.calls[0]['result']['rows'][1][1]=3
        self.base['headline']=E.draft_state(self.base)['last_draft']['headline']
        for row in self.base['row_prose']['rows']:
            row['fields']['review']='Review '+('two'if row['key']['patient_id']=='A'else'three')+' events on 2030-01-01; preserve the uncertainty.'
        original=copy.deepcopy(self.base);sent=[];target_counts=[]
        def review(a,c):
            checked=copy.deepcopy(a);B.materialize_table(checked,c)
            try:B.bind_computed_prose(checked,c)
            except B.ComputedProseError:
                r=B.review_final(SimpleNamespace(),'Explain each entity',a,c,SimpleNamespace(read={'note_A':{'text':'Dated observation'}}),ledger=None,as_of='2030-05-27')
                target_counts.append(len(r['prose_repair']['targets']));return r
            a.update(checked)
            return {'status':'confirmed'}
        def send(body):
            state=json.loads(body['messages'][0]['content']);sent.append(state)
            f=state['focused_prose_repair'];rows=[]
            if len(sent)==1:
                rows=[{'key':{'patient_id':'A'},'fields':{'review':self.bound_review()}},
                      {'key':{'patient_id':'B'},'fields':{'review':'Review the events on 2030-01-01; preserve the uncertainty.'}}]
            else:
                self.assertEqual(len(f['targets']),1);self.assertEqual(f['targets'][0]['key'],{'patient_id':'B'})
                self.assertIn('expected',state['repair_protocol_feedback'])
                rows=[{'key':{'patient_id':'B'},'fields':{'review':self.bound_review(row=1)}}]
            return {'content':[{'type':'text','text':json.dumps({'repair_draft':{'draft_digest':f['draft_digest'],'rows':rows}})}]},0.,False
        out=S.tool_loop('Explain both entities',[],[],lambda use:self.fail('No new source needed'),send=send,
            model=M.SONNET,max_tools=None,initial_calls=self.calls,initial_answer=self.base,review=review,compact_execution_state=True)
        self.assertEqual(target_counts,[2,1]);self.assertEqual(len(sent),2)
        self.assertEqual([p['changed_fields']for p in out['prose_repairs']],[1,1])
        self.assertEqual(out['prose_repairs'][0]['rejected_fields'][0]['key'],{'patient_id':'B'})
        self.assertEqual([{k:s[k]for k in original['sources'][i]}for i,s in enumerate(out['answer']['sources'])],original['sources'])
        self.assertEqual(out['answer']['table'][1]['review'],'Review 3 events on 2030-01-01; preserve the uncertainty.')

    def test_binding_subset_never_salvages_a_structurally_invalid_patch(self):
        feedback=self.located_failure();proposal=self.patch(review=self.bound_review())
        proposal['repair_draft']['rows'].append({'key':{'patient_id':'C'},'fields':{'review':'No entity'}})
        before=copy.deepcopy(self.base);rejected=[]
        with self.assertRaisesRegex(ValueError,'cannot add or rename'):
            E.apply_prose_repair(proposal,self.base,calls=self.calls,feedback=feedback,binding_rejections=rejected)
        self.assertEqual(self.base,before);self.assertEqual(rejected,[])

    def test_repeated_binding_failure_is_stable_across_new_result_aliases(self):
        feedback=self.located_failure();sent=[]
        def send(body):
            sent.append(body)
            if len(sent)==2:
                return {'content':[{'type':'tool_use','id':'query','name':'query_sql','input':{'sql':'SELECT patient_id,events FROM scoped'}}]},0.,False
            proposal=self.patch(review={'template':'Only {n} events.',
                'bindings':{'n':{'result_view':'agere_result_'+str(1 if len(sent)==1 else 2),'row':0,'column':'events'}}})
            return {'content':[{'type':'text','text':json.dumps(proposal)}]},0.,False
        out=S.tool_loop('Explain entities',[],[{'name':'query_sql','input_schema':{'type':'object'}}],
            lambda use:{**copy.deepcopy(self.calls[0]['result']),'result_view':'agere_result_2'},send=send,
            model=M.SONNET,max_tools=None,initial_calls=self.calls,initial_answer=self.base,
            review=lambda a,c:feedback,compact_execution_state=True)
        self.assertEqual(len(sent),3);self.assertEqual(out['review_stopped'],'no_progress')
        state=json.loads(sent[1]['messages'][0]['content'])
        self.assertIn('"patient_id": "A"',state['feedback']['feedback'] if isinstance(state.get('feedback'),dict)else json.dumps(state))

    def test_focused_repair_rejects_full_answer_before_review_and_retains_all_fields(self):
        self.base['status']='partial';packet,support=self.source_review()
        feedback={'status':'repair','repair_kind':'unresolved_verification','feedback':'Uncertain source meaning',
                  'prose_repair':E.source_prose_repair(self.base,support,[packet],{}),'support':support}
        sent=[];reviewed=[];original=copy.deepcopy(self.base)
        def review(candidate,calls):
            reviewed.append(copy.deepcopy(candidate));return feedback
        def send(body):
            sent.append(body);state=json.loads(body['messages'][0]['content'])
            self.assertIn('focused_prose_repair',state)
            if len(sent)==1:answer={'status':'answered','headline':'Replacement with omitted patient prose','table_from_tool':'agere_result_1'}
            else:
                self.assertIn('repair_protocol_feedback',state)
                answer={'repair_draft':{'draft_digest':digest(original),'rows':[]}}
            return {'content':[{'type':'text','text':json.dumps(answer)}],'usage':{'input_tokens':1,'output_tokens':1}},0.,False
        out=S.tool_loop('Complete original request','',[],lambda a:self.fail('No new native request'),send=send,
            model=M.HAIKU,max_tools=None,initial_calls=self.calls,initial_answer=self.base,review=review,compact_execution_state=True)
        self.assertEqual(len(reviewed),1);self.assertEqual(len(sent),2)
        self.assertEqual(out['answer']['row_prose'],original['row_prose']);self.assertEqual(out['answer']['table'],original['table'])
        self.assertEqual(out['answer']['status'],'partial');self.assertEqual(out['review_stopped'],'no_supported_prose_change')
        self.assertEqual(out['protocol_repairs'][0]['reason'],'Replacement answer rejected at focused repair boundary')

    def test_repeated_replacement_never_reaches_source_review_or_changes_draft(self):
        self.base['status']='partial';packet,support=self.source_review()
        feedback={'status':'repair','repair_kind':'unresolved_verification','feedback':'Uncertain source meaning',
                  'prose_repair':E.source_prose_repair(self.base,support,[packet],{}),'support':support}
        reviewed=[]
        def review(candidate,calls):reviewed.append(candidate);return feedback
        def send(body):return {'content':[{'type':'text','text':'{"status":"answered","headline":"Replacement"}'}]},0.,False
        out=S.tool_loop('Complete original request','',[],lambda a:None,send=send,model=M.HAIKU,max_tools=None,
            initial_calls=self.calls,initial_answer=self.base,review=review,compact_execution_state=True)
        self.assertEqual(len(reviewed),1);self.assertEqual(out['review_stopped'],'no_progress')
        self.assertEqual(out['answer']['row_prose'],self.base['row_prose']);self.assertEqual(out['new_paid_api_calls'],2)

    def test_malformed_repair_keeps_targets_and_rejects_replacement_before_valid_patch(self):
        for malformed in ('{"repair_draft":', json.dumps({'tool_use':{'name':'unregistered','input':{}}})):
            with self.subTest(malformed=malformed):
                self.setUp();feedback=self.located_failure();original=copy.deepcopy(self.base)
                sent=[];reviewed=[]
                def review(candidate,calls):
                    reviewed.append(copy.deepcopy(candidate))
                    if len(reviewed)==1:return feedback
                    self.compile(candidate)
                    return {'status':'confirmed','feedback':None}
                def send(body):
                    state=json.loads(body['messages'][0]['content']);sent.append(state)
                    focus=state['focused_prose_repair']
                    self.assertEqual(focus['draft_digest'],digest(original))
                    self.assertEqual(focus['complete_row_prose_rows'],2)
                    self.assertEqual(focus['targets'][0]['field'],'review')
                    if len(sent)==1:text=malformed
                    elif len(sent)==2:
                        self.assertIn('repair_protocol_feedback',state)
                        text=json.dumps({'status':'partial','headline':'Replacement omitting patient prose','table_from_tool':'agere_result_1'})
                    else:text=json.dumps(self.patch(review=self.bound_review()))
                    return {'content':[{'type':'text','text':text}]},0.,False
                out=S.tool_loop('Explain both entities',[],[],lambda use:self.fail('Unexpected source execution'),
                    send=send,model=M.SONNET,max_tools=None,initial_calls=self.calls,initial_answer=self.base,
                    review=review,format_retries=1,compact_execution_state=True)
                self.assertIsNone(out.get('error'));self.assertEqual(len(sent),3);self.assertEqual(len(reviewed),2)
                self.assertEqual(out['answer']['table'][0]['review'],'Review 2 events on 2030-01-01; preserve the uncertainty.')
                self.assertEqual(out['answer']['row_prose']['rows'][1],original['row_prose']['rows'][1])
                self.assertEqual(out['answer']['sources'],original['sources'])
                self.assertTrue(any(p['reason']=='Replacement answer rejected at focused repair boundary'for p in out['protocol_repairs']))

    def test_repeated_malformed_json_retains_source_unverified_draft_without_certification(self):
        self.base['status']='partial';packet,support=self.source_review()
        feedback={'status':'repair','repair_kind':'unresolved_verification','feedback':'Uncertain source meaning',
                  'prose_repair':E.source_prose_repair(self.base,support,[packet],{}),'support':support}
        original=copy.deepcopy(self.base);sent=[]
        def send(body):
            state=json.loads(body['messages'][0]['content']);sent.append(state)
            self.assertEqual(state['focused_prose_repair']['draft_digest'],digest(original))
            return {'content':[{'type':'text','text':'{"repair_draft":'}]},0.,False
        out=S.tool_loop('Explain both entities',[],[],lambda use:None,send=send,model=M.SONNET,
            max_tools=None,initial_calls=self.calls,initial_answer=self.base,review=lambda a,c:feedback,
            format_retries=1,compact_execution_state=True)
        self.assertTrue(out.get('error'));self.assertEqual(len(sent),2)
        self.assertEqual(out['answer']['row_prose'],original['row_prose'])
        self.assertEqual(out['answer']['verification'],'unverified')
        self.assertNotIn('prose_repairs',out)

    def test_empty_compiler_patch_escalates_same_draft_and_cannot_mean_no_source_change(self):
        for repair in (True,False):
            with self.subTest(repair=repair):
                self.setUp();feedback=self.located_failure();original=copy.deepcopy(self.base);sent=[];reviews=[]
                def review(candidate,calls):
                    reviews.append(copy.deepcopy(candidate))
                    if len(reviews)==1:return feedback
                    self.compile(candidate);return {'status':'confirmed','feedback':None}
                def send(body):
                    sent.append(body);focus=json.loads(body['messages'][0]['content'])['focused_prose_repair']
                    self.assertEqual(focus['draft_digest'],digest(original))
                    p=(self.patch(review=self.bound_review())if len(sent)==2 and repair else
                       {'repair_draft':{'draft_digest':digest(original),'rows':[]}})
                    return {'content':[{'type':'text','text':json.dumps(p)}]},0.,False
                out=S.tool_loop('Explain both entities',[],[],lambda use:None,send=send,model=M.HAIKU,
                    escalation_model=M.SONNET,max_tools=None,initial_calls=self.calls,initial_answer=self.base,
                    review=review,compact_execution_state=True)
                self.assertEqual([b['model']for b in sent],[M.HAIKU,M.SONNET])
                self.assertEqual(len(reviews),2 if repair else 1)
                if repair:self.assertEqual(out['answer']['table'][0]['review'],'Review 2 events on 2030-01-01; preserve the uncertainty.')
                else:
                    self.assertEqual(out['review_stopped'],'compiler_unresolved')
                    self.assertNotIn('prose_repairs',out)

    def setUp(self):
        self.calls=[{'name':'query_sql','input':{'sql':'SELECT patient_id,events FROM scoped'},
            'result':{'result_view':'agere_result_1','columns':['patient_id','events'],
                      'rows':[['A',2],['B',None]],'truncated':False}}]
        self.base={'status':'answered','answer_type':'table','table_from_tool':'agere_result_1',
            'headline':{'template':'Received count: {n}.','bindings':{'n':{'result_view':'agere_result_1','row':0,'column':'events'}}},
            'sources':[{'record_id':'note_A','quote':'Dated observation','date':'2030-01-01'}],
            'missing':['Current responsibility is unestablished.'],
            'row_prose':{'keys':['patient_id'],'rows':[
                {'key':{'patient_id':pid},'basis':[{'result_view':'agere_result_1','row':i}],
                 'fields':{'explanation':'A dated observation; current responsibility is unknown.',
                           'review':'Confirm current responsibility.'}}for i,pid in enumerate(['A','B'])]}}
        self.compile(self.base)

    def compile(self,a):
        B.materialize_table(a,self.calls);B.bind_computed_prose(a,self.calls)

    def patch(self,**fields):
        return {'repair_draft':{'draft_digest':digest(self.base),'rows':[{'key':{'patient_id':'A'},
            'fields':fields or {'review':'Review the dated observation before confirming current responsibility.'}}]}}

    def test_only_named_prose_changes_and_all_native_values_sources_bases_survive(self):
        original=copy.deepcopy(self.base);patch=self.patch();before=copy.deepcopy(patch)
        merged=E.apply_prose_repair(patch,self.base)
        self.assertIsInstance(merged['headline'],dict)
        self.compile(merged)
        for i in range(2):
            for key,value in original['table'][i].items():
                if i==0 and key=='review':continue
                self.assertEqual(merged['table'][i][key],value)
        self.assertEqual(merged['sources'],original['sources'])
        self.assertEqual(merged['missing'],original['missing'])
        self.assertEqual(merged['headline'],original['headline'])
        self.assertEqual([r['basis']for r in merged['row_prose']['rows']],[r['basis']for r in original['row_prose']['rows']])
        self.assertEqual(self.base,original);self.assertEqual(patch,before)
        self.assertEqual(E.draft_state(self.base)['localized_prose_repair']['draft_digest'],digest(self.base))

    def test_stale_digest_unknown_entities_duplicates_native_fields_and_deletions_rejected(self):
        for kind in ('stale','entity','duplicate','native','delete','basis','extra_top','key_type'):
            p=self.patch();r=p['repair_draft'];row=r['rows'][0]
            if kind=='stale':r['draft_digest']='stale'
            if kind=='entity':row['key']['patient_id']='C'
            if kind=='duplicate':r['rows'].append(copy.deepcopy(row))
            if kind=='native':row['fields']={'events':'changed'}
            if kind=='delete':row['fields']={'review':None}
            if kind=='basis':row['basis']=[]
            if kind=='extra_top':p['status']='answered'
            if kind=='key_type':row['key']['patient_id']=1
            before=copy.deepcopy(self.base)
            with self.subTest(kind=kind),self.assertRaises(ValueError):E.apply_prose_repair(p,self.base)
            self.assertEqual(self.base,before)

    def test_patch_cannot_bypass_numeric_or_same_entity_compiler_checks(self):
        for value in ('There were 42 events.',{'template':'Events: {n}.','bindings':{'n':{'result_view':'agere_result_1','row':1,'column':'events'}}}):
            merged=E.apply_prose_repair(self.patch(explanation=value),self.base)
            with self.subTest(value=value),self.assertRaises(ValueError):self.compile(merged)

    def test_numeric_source_quotation_requires_an_executed_same_entity_scalar(self):
        # The old feedback said verified quotes could remain literal even though
        # row prose correctly rejected them. Exercise the usable native route.
        import sqlite3
        text='Drug X: reports use on 0 of 14 days; no tablets left.'
        unbound=E.apply_prose_repair(self.patch(explanation='Note note_A states "'+text+'".'),self.base)
        with self.assertRaises(B.ComputedProseError) as failure:
            B.bind_computed_prose(unbound,self.calls,verified_quotes=[{'record_id':'note_A','quote':text}])
        self.assertIn('numeric quotations also need scalar bindings',str(failure.exception))
        self.assertNotIn('may remain quoted',str(failure.exception))
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE source_note(patient_id,record_id,event_date,text)')
            db.execute('INSERT INTO source_note VALUES(?,?,?,?)',('A','note_A','2030-01-01',text))
            sql='SELECT patient_id,record_id,event_date,text AS quotation FROM source_note'
            cursor=db.execute(sql)
            result={'result_view':'agere_result_2','columns':[x[0]for x in cursor.description],
                    'rows':[list(r)for r in cursor],'truncated':False}
        calls=[*self.calls,{'name':'query_sql','input':{'sql':sql},'result':result}]
        value={'template':'Note note_A states "{quote}".',
               'bindings':{'quote':{'result_view':'agere_result_2','row':0,'column':'quotation'}}}
        proposal=self.patch(explanation=value)
        proposal['repair_draft']['rows'][0]['basis_additions']=[{'result_view':'agere_result_2','row':0}]
        merged=E.apply_prose_repair(proposal,self.base,calls=calls)
        B.materialize_table(merged,calls);B.bind_computed_prose(merged,calls)
        self.assertEqual(merged['table'][0]['explanation'],'Note note_A states "'+text+'".')
        self.assertEqual(merged['table'][1],self.base['table'][1])
        self.assertNotEqual(merged.get('verification'),'grounded')
        result['rows'][0][0]='B'
        with self.assertRaisesRegex(ValueError,'another entity'):
            E.apply_prose_repair(proposal,self.base,calls=calls)

    def test_noop_patch_is_not_a_new_repair(self):
        with self.assertRaisesRegex(ValueError,'no changed fields'):
            E.apply_prose_repair(self.patch(review=self.base['row_prose']['rows'][0]['fields']['review']),self.base)

    def test_changed_native_values_are_read_again_by_the_compiler(self):
        merged=E.apply_prose_repair(self.patch(),self.base)
        self.calls[0]['result']['rows'][0][1]=3
        self.compile(merged)
        self.assertEqual(merged['table'][0]['events'],3)
        self.assertIn('3',merged['headline'])

    def test_loop_merges_then_reviews_complete_answer_without_reexecuting_sources(self):
        sent=[];reviewed=[]
        original=copy.deepcopy(self.base)
        def review(a,c):
            self.assertEqual(c,self.calls);self.compile(a);reviewed.append(copy.deepcopy(a))
            if len(reviewed)==1:return {'status':'repair','feedback':'Repair the proposed review for A.'}
            self.assertEqual(len(a['table']),2)
            self.assertEqual(a['table'][1],original['table'][1])
            self.assertEqual(a['sources'],original['sources'])
            return {'status':'confirmed'}
        def send(body):
            sent.append(body)
            proposal=E.draft_state(self.base)['last_draft'] if len(sent)==1 else self.patch()
            if len(sent)==1:proposal['sources']=copy.deepcopy(self.base['sources'])
            else:proposal['repair_draft']['draft_digest']=digest(reviewed[-1])
            return {'content':[{'type':'text','text':json.dumps(proposal)}]},.001,False
        out=S.tool_loop('Explain each entity',[],[],lambda use:self.fail('Unexpected source execution'),send=send,
            model=M.SONNET,max_tools=None,initial_calls=self.calls,review=review)
        self.assertEqual(len(reviewed),2);self.assertEqual(len(sent),2)
        self.assertIn('repair_draft',sent[1]['messages'][-1]['content'])
        self.assertEqual(out['prose_repairs'][0]['changed_fields'],1)
        self.assertTrue(out['prose_repairs'][0]['complete_review_required'])
        self.assertEqual(out['answer']['status'],'answered')

    def test_repeated_invalid_patch_keeps_partial_answer_and_does_not_run_source_review(self):
        sent=[];checks=[]
        def send(body):
            sent.append(body)
            p=E.draft_state(self.base)['last_draft'] if len(sent)==1 else self.patch()
            if len(sent)>1:p['repair_draft']['draft_digest']='stale'
            return {'content':[{'type':'text','text':json.dumps(p)}]},.001,False
        def review(a,c):checks.append(a);return {'status':'repair','feedback':'Inspect the proposed review.'}
        out=S.tool_loop('Explain each entity',[],[],lambda use:None,send=send,model=M.SONNET,
            max_tools=None,initial_calls=self.calls,review=review)
        self.assertEqual(len(sent),3);self.assertEqual(len(checks),1)
        self.assertEqual(out['review_stopped'],'no_progress')
        self.assertEqual(out['answer']['status'],'partial')
        self.assertEqual(out['answer']['verification'],'unverified')

    def located_failure(self):
        self.base['status']='partial'
        self.base['row_prose']['rows'][0]['fields']['review']='Review two events on 2030-01-01; preserve the uncertainty.'
        # Restore quantitative headline templates, as the real draft state does.
        self.base['headline']=E.draft_state(self.base)['last_draft']['headline']
        return B.review_final(SimpleNamespace(),'Explain each entity',self.base,self.calls,
            SimpleNamespace(read={'note_A':{'text':'Dated observation'}}),ledger=None,as_of='2030-05-27')

    def bound_review(self,view='agere_result_1',row=0):
        return {'template':'Review {n} events on 2030-01-01; preserve the uncertainty.',
            'bindings':{'n':{'result_view':view,'row':row,'column':'events'}}}

    def test_compiler_targets_bind_exact_field_and_mixed_global_failures_disable_focus(self):
        feedback=self.located_failure();targets=E.prose_repair_targets(self.base,feedback)
        self.assertEqual(len(targets),1);self.assertEqual(targets[0]['field'],'review')
        self.assertEqual(targets[0]['key'],{'patient_id':'A'})
        self.assertEqual(targets[0]['code'],'unbound_literal')
        self.base['headline']='There are 99 events.'
        mixed=B.review_final(SimpleNamespace(),'Explain each entity',self.base,self.calls,
            SimpleNamespace(read={'note_A':{'text':'Dated observation'}}),ledger=None,as_of='2030-05-27')
        self.assertNotIn('prose_repair',mixed)
        self.assertIsNone(E.focused_prose_state(self.base,mixed,self.calls))

    def test_same_entity_basis_addition_keeps_prior_bases_and_requires_complete_compilation(self):
        feedback=self.located_failure()
        self.calls.append({'name':'query_sql','input':{'sql':'SELECT patient_id,events FROM source'},
            'result':{'result_view':'agere_result_2','columns':['patient_id','events'],
                'rows':[['A',2],['B',7]],'truncated':False}})
        p=self.patch(review=self.bound_review('agere_result_2'))
        p['repair_draft']['rows'][0]['basis_additions']=[{'result_view':'agere_result_2','row':0}]
        before=copy.deepcopy(self.base)
        merged=E.apply_prose_repair(p,self.base,calls=self.calls,feedback=feedback);self.compile(merged)
        self.assertEqual(merged['row_prose']['rows'][0]['basis'],before['row_prose']['rows'][0]['basis']+[{'result_view':'agere_result_2','row':0}])
        self.assertEqual(merged['table'][0]['review'],'Review 2 events on 2030-01-01; preserve the uncertainty.')
        self.assertEqual(self.base,before)
        for kind in ('foreign','missing','duplicate','incomplete','unsupported'):
            q=copy.deepcopy(p);calls=copy.deepcopy(self.calls);edit=q['repair_draft']['rows'][0]
            if kind=='foreign':edit['basis_additions'][0]['row']=1
            if kind=='missing':edit['basis_additions'][0]['row']=8
            if kind=='duplicate':edit['basis_additions']=[{'result_view':'agere_result_1','row':0}]
            if kind=='incomplete':calls[-1]['result']['truncated']=True
            if kind=='unsupported':edit['basis_extra']=edit.pop('basis_additions')
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                E.apply_prose_repair(q,self.base,calls=calls,feedback=feedback)
            self.assertEqual(self.base,before)

    def test_binding_repair_cannot_delete_or_change_sentence_values_units_or_dates(self):
        feedback=self.located_failure()
        good=self.bound_review()
        for kind in ('quantity','date','meaning','unit','wrong_scalar'):
            value=copy.deepcopy(good)
            if kind=='quantity':value='Review the events on 2030-01-01; preserve the uncertainty.'
            if kind=='date':value['template']=value['template'].replace('2030-01-01','2030-01-02')
            if kind=='meaning':value['template']=value['template'].replace('; preserve the uncertainty','')
            if kind=='unit':value['template']=value['template'].replace('events','mg')
            if kind=='wrong_scalar':value['bindings']['n']['row']=1
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'preserve the original'):
                E.apply_prose_repair(self.patch(review=value),self.base,calls=self.calls,feedback=feedback)
        self.compile(E.apply_prose_repair(self.patch(review=good),self.base,calls=self.calls,feedback=feedback))
        self.assertEqual(E.canonical_repair_prose('Dose 10.0 mg; three events.'),E.canonical_repair_prose('Dose ten mg; 3 events.'))

    def test_scoped_state_keeps_all_native_handles_and_exact_selected_readings(self):
        feedback=self.located_failure()
        reading=copy.deepcopy(self.calls[0]);reading['result']['result_view']='agere_result_2'
        reading['result']['actor_reading']={'mode':'complete_rows','rows_digest':digest(reading['result']['rows']),
            'columns_digest':digest(reading['result']['columns'])}
        calls=self.calls+[reading,{'name':'metadata','input':{},'result':{'result_view':'metadata','kind':'non_table'}}]
        before=copy.deepcopy(calls)
        state=json.loads(E.messages('Both entities remain requested',calls,candidate=self.base,feedback=feedback)[0]['content'])
        focus=state['focused_prose_repair']
        self.assertEqual(focus['repair_entities'],1);self.assertEqual(focus['native_answer_rows'],2)
        self.assertEqual(focus['complete_row_prose_rows'],2);self.assertNotIn('last_draft',state)
        self.assertEqual(focus['native_scalar_rows'][0]['values'],{'patient_id':'A','events':2})
        self.assertEqual(len(state['executed_results']),len(calls));self.assertEqual(calls,before)
        selected=state['executed_results'][1]['repair_reading']
        self.assertEqual(selected['rows'],[{'patient_id':'A','events':2}])
        self.assertEqual(selected['native_total_rows'],2);self.assertEqual(selected['native_row_indices'],[0])
        for mutation in ('digest','field','basis','key','duplicate'):
            changed=copy.deepcopy(feedback);target=changed['prose_repair']['targets'][0]
            if mutation=='digest':changed['prose_repair']['draft_digest']='stale'
            if mutation=='field':target['field']='missing'
            if mutation=='basis':target['basis']=[]
            if mutation=='key':target['key']={'patient_id':'B'}
            if mutation=='duplicate':changed['prose_repair']['targets'].append(copy.deepcopy(target))
            with self.subTest(mutation=mutation):
                full=json.loads(E.messages('Both entities',calls,candidate=self.base,feedback=changed)[0]['content'])
                self.assertIn('last_draft',full);self.assertNotIn('focused_prose_repair',full)

    def test_invalid_patch_keeps_preservation_guard_on_the_next_attempt(self):
        feedback=self.located_failure();sent=[];reviews=[]
        def send(body):
            sent.append(body)
            p=self.patch(review='Review the events; omit the numeric detail.')
            if len(sent)==1:p['repair_draft']['rows'][0]['basis_extra']=[]
            return {'content':[{'type':'text','text':json.dumps(p)}]},0,False
        def review(a,c):reviews.append(copy.deepcopy(a));return feedback
        out=S.tool_loop('Explain both entities',[],[],lambda use:self.fail('Unexpected source execution'),send=send,
            model=M.SONNET,max_tools=None,initial_calls=self.calls,initial_answer=self.base,
            review=review,compact_execution_state=True)
        self.assertEqual(len(reviews),1);self.assertNotIn('prose_repairs',out)
        self.assertEqual(out['review_stopped'],'no_progress')
        self.assertTrue(all('focused_prose_repair'in json.loads(b['messages'][0]['content'])for b in sent))
        self.assertIn('preserve the original',sent[-1]['messages'][0]['content'])

    def test_quantity_deleting_patch_requires_native_query_before_next_patch(self):
        for obey in (True,False):
            with self.subTest(obey=obey):
                self.setUp();feedback=self.located_failure();sent=[];reviewed=[];executed=[]
                def review(a,c):
                    reviewed.append(copy.deepcopy(a))
                    if len(reviewed)==1:return feedback
                    B.materialize_table(a,c);B.bind_computed_prose(a,c)
                    self.assertEqual(a['table'][0]['review'],'Review 2 events on 2030-01-01; preserve the uncertainty.')
                    return {'status':'confirmed'}
                def send(body):
                    sent.append(body)
                    if len(sent)==2:
                        self.assertEqual(body['tool_choice'],{'type':'tool','name':'query_sql'})
                        if obey:return {'content':[{'type':'tool_use','id':'native','name':'query_sql',
                            'input':{'sql':'SELECT patient_id,events FROM scoped'}}]},0.,False
                    proposal=self.patch(review='Review the events; omit the numeric detail.')
                    if len(sent)==3:
                        self.assertEqual(body['tool_choice'],{'type':'auto'})
                        proposal=self.patch(review=self.bound_review('agere_result_2'))
                        proposal['repair_draft']['rows'][0]['basis_additions']=[{'result_view':'agere_result_2','row':0}]
                    return {'content':[{'type':'text','text':json.dumps(proposal)}]},0.,False
                def execute(use):
                    executed.append(use)
                    return {**copy.deepcopy(self.calls[0]['result']),'result_view':'agere_result_2'}
                out=S.tool_loop('Explain both entities',[],[{'name':'query_sql','input_schema':{'type':'object'}}],
                    execute,send=send,model=M.HAIKU,max_tools=None,initial_calls=self.calls,initial_answer=self.base,
                    review=review,compact_execution_state=True)
                if obey:
                    self.assertEqual(len(executed),1);self.assertEqual(len(reviewed),2)
                    self.assertEqual(out['prose_repairs'][0]['changed_fields'],1)
                    self.assertEqual(out['answer']['table'][1],{
                        **self.base['table'][1],**self.base['row_prose']['rows'][1]['fields']})
                else:
                    self.assertEqual(executed,[]);self.assertEqual(len(reviewed),1)
                    self.assertEqual(out['review_stopped'],'binding_evidence_required')
                    self.assertNotIn('prose_repairs',out)

    def test_source_free_keyed_constants_do_not_establish_patient_evidence(self):
        feedback=self.located_failure()
        self.calls.append({'name':'query_sql','input':{'sql':"SELECT 'A' AS patient_id,2 AS events"},
            'result':{'result_view':'agere_result_2','columns':['patient_id','events'],'rows':[['A',2]],
                'native_sql_contract':B.NATIVE_SQL_CONTRACT,'read_views':[]}})
        p=self.patch(review=self.bound_review('agere_result_2'))
        p['repair_draft']['rows'][0]['basis_additions']=[{'result_view':'agere_result_2','row':0}]
        merged=E.apply_prose_repair(p,self.base,calls=self.calls,feedback=feedback)
        with self.assertRaisesRegex(B.ComputedProseError,'source-free SQL constants')as caught:self.compile(merged)
        self.assertEqual(caught.exception.repair_targets[0]['code'],'missing_scalar_source')
        # A retained observation is still compiled, with semantic source review
        # required afterwards. This is not a general SQL lineage proof.
        self.calls[-1]['input']['sql']='SELECT patient_id,events FROM received_events'
        self.calls[-1]['result']['read_views']=['received_events']
        self.compile(merged)

    def source_review(self,field='review'):
        from clinical_core.agere.deliverables.receipts import readable_json
        row=copy.deepcopy(self.base['table'][0])
        claims=[{'id':'claim_'+str(i),'field':k,'path':['answer_rows',0,k]}
                for i,k in enumerate(k for k in row if k!='patient_id')]
        state={'patient_id':'A','answer_rows':[row],'claim_manifest':claims,'source_context_gaps':[],
            'source_contexts':[{'patient_id':'A','record_id':'note_A','date':'2030-01-01',
                'text':'A dated observation. The received note does not establish current responsibility.'}]}
        text=json.dumps(readable_json(state),separators=(',',':'))
        p={'id':'patient_A','date':'2030-05-27','text':text,'bytes':len(text.encode()),'claim_manifest':claims,
            'patient_dependency_bound':True,'source_dependency_patient_id':'A','source_context_complete':True,'dependency_complete':True}
        def judge(task,named,evidence,**kw):
            return {'steps':[],'judgments':{k:{'label':'unknown'if v['instructions']['claim']['field']==field else'supported',
                'decided_by':None if v['instructions']['claim']['field']==field else'fixture',
                'probability':.9,'threshold':.8}for k,v in named.items()}}
        r=B.localize_support_failure(p,judge,as_of=p['date'],ledger=None)
        support={'all_dependencies_checked':True,'packets':[{'id':p['id'],'label':'unknown','decided_by':None,
            'field_review':r['field_review'],'claim_feedback':r['feedback']}]}
        return p,support

    def test_source_repair_uses_exact_complete_context_without_calling_unknown_a_fault(self):
        p,support=self.source_review()
        target=E.source_prose_repair(self.base,support,[p],{'label':'answers_partially','decided_by':None})
        self.assertIsNotNone(target);self.assertFalse(target['targets'][0]['source_fault_established'])
        feedback={'prose_repair':target,'support':support}
        focus=E.focused_prose_state(self.base,feedback,self.calls)
        self.assertEqual(focus['source_contexts'],target['source_contexts'])
        self.assertEqual(focus['native_answer_rows'],2);self.assertEqual(focus['repair_entities'],1)
        self.assertIn('not a proven error',focus['instruction']);self.assertTrue(focus['coverage_still_required'])
        proposal=self.patch(review='Confirm the current responsible clinician; the dated note does not establish this.')
        merged=E.apply_prose_repair(proposal,self.base,calls=self.calls,feedback=feedback);self.compile(merged)
        self.assertEqual(merged['table'][1],self.base['table'][1])
        self.assertEqual(merged['table'][0]['events'],self.base['table'][0]['events'])
        with self.assertRaisesRegex(ValueError,'producer-located'):
            E.apply_prose_repair(self.patch(explanation='Unrequested change'),self.base,calls=self.calls,feedback=feedback)

    def test_mixed_native_and_prose_uncertainty_preserves_independent_scoped_repair(self):
        p,support=self.source_review()
        _,native=self.source_review(field='events')
        support['packets'][0]['claim_feedback']+=native['packets'][0]['claim_feedback']
        support['packets'].append({'id':'unresolved_child','label':'unknown','decided_by':None})
        target=E.source_prose_repair(self.base,support,[p],{})
        self.assertIsNotNone(target)
        self.assertEqual([t['field']for t in target['targets']],['review'])
        self.assertEqual(len(target['remaining_dependencies']),2)
        self.assertEqual(target['remaining_dependencies'][0]['field'],'events')
        self.assertTrue(target['coverage_still_required'])
        self.assertFalse(target['clinical_qualification'])
        feedback={'prose_repair':target,'support':support}
        self.assertIsNotNone(E.focused_prose_state(self.base,feedback,self.calls))
        merged=E.apply_prose_repair(self.patch(review='Current responsibility is unresolved in the received note.'),
            self.base,calls=self.calls,feedback=feedback)
        self.compile(merged)
        self.assertEqual(merged['table'][0]['events'],self.base['table'][0]['events'])
        self.assertEqual(support['packets'][-1]['label'],'unknown')
        with self.assertRaises(ValueError):
            E.apply_prose_repair(self.patch(events='Changed native count'),self.base,calls=self.calls,feedback=feedback)

    def test_unlocalized_summary_unknown_does_not_disable_exact_patient_prose_repair(self):
        p,support=self.source_review()
        summary={'id':'summary','text':json.dumps({'summary':'Unresolved global scope'}),'claim_manifest':[]}
        support['packets'].insert(0,{'id':'summary','label':'unknown','decided_by':None})
        target=E.source_prose_repair(self.base,support,[summary,p],{})
        self.assertIsNotNone(target)
        self.assertEqual([t['field']for t in target['targets']],['review'])
        self.assertEqual([x['packet_id']for x in target['remaining_dependencies']],['summary'])
        self.assertTrue(target['coverage_still_required'])
        self.assertFalse(target['clinical_qualification'])

    def test_source_focus_rejects_native_unlocalized_stale_or_incomplete_obligations(self):
        p,support=self.source_review()
        for mutation in ('packet','manifest','source_gap','wrong_patient','source_empty','unlocalized','native_field','coverage','unchecked'):
            q=copy.deepcopy(p);r=copy.deepcopy(support);coverage={'label':'answers_fully','decided_by':'fixture'}
            if mutation=='packet':r['packets'][0]['field_review']['packet_digest']='stale'
            elif mutation=='manifest':q['claim_manifest']=q['claim_manifest'][:-1]
            elif mutation=='source_gap':q['source_context_complete']=False
            elif mutation=='wrong_patient':q['source_dependency_patient_id']='B'
            elif mutation=='source_empty':
                from clinical_core.agere.deliverables.receipts import uncompact_json
                state=uncompact_json(json.loads(q['text']));state['source_contexts'][0]['text']=''
                q['text']=json.dumps(state);r['packets'][0]['field_review']['packet_digest']=digest(q['text'])
            elif mutation=='unlocalized':r['packets'][0]['claim_feedback']=[]
            elif mutation=='native_field':q,r=self.source_review('events')
            elif mutation=='coverage':coverage={'label':'answers_partially','decided_by':'fixture'}
            elif mutation=='unchecked':r['all_dependencies_checked']=False
            with self.subTest(mutation=mutation):self.assertIsNone(E.source_prose_repair(self.base,r,[q],coverage))

    def test_source_context_or_reviewed_value_changes_invalidate_repair_view(self):
        p,support=self.source_review();target=E.source_prose_repair(self.base,support,[p],{})
        for mutation in ('source_text','source_patient','reviewed_value'):
            t=copy.deepcopy(target)
            if mutation=='source_text':t['source_contexts'][0]['text']='Changed source'
            elif mutation=='source_patient':
                t['source_contexts'][0]['patient_id']='B';t['source_contexts_digest']=digest(t['source_contexts'])
            elif mutation=='reviewed_value':t['targets'][0]['reviewed_value_digest']='stale'
            with self.subTest(mutation=mutation):self.assertIsNone(E.focused_prose_state(self.base,{'prose_repair':t},self.calls))

    def test_partitioned_parent_repair_keeps_full_original_sources_and_requires_children(self):
        from clinical_core.agere.clinical import evidence_projection as P
        from clinical_core.agere.deliverables.receipts import uncompact_json,readable_json
        p,support=self.source_review();parent=copy.deepcopy(p)
        state=uncompact_json(json.loads(p['text']));original_sources=copy.deepcopy(state['source_contexts'])
        state['source_contexts'][0]['text']={'checked_source_dependency':'child_A'}
        parent.update(text=json.dumps(readable_json(state)),required_source_packet_ids=['child_A'])
        support['packets'][0]['field_review']['packet_digest']=digest(parent['text'])
        support['packets'].append({'id':'child_A','label':'supported','decided_by':'fixture'})
        with patch.object(P,'partition_support_packet',return_value=[parent]):
            target=E.source_prose_repair(self.base,support,[p],{})
            self.assertEqual(target['source_contexts'],original_sources)
            self.assertEqual(target['targets'][0]['source_packet_digest'],digest(p['text']))
            for mutation in ('missing','uncertain','fault'):
                changed=copy.deepcopy(support)
                if mutation=='missing':changed['packets'].pop()
                elif mutation=='uncertain':changed['packets'][1]['decided_by']=None
                else:changed['packets'][1]['label']='contradicted'
                with self.subTest(mutation=mutation):self.assertIsNone(E.source_prose_repair(self.base,changed,[p],{}))

    def source_compiler_fixture(self):
        self.base['status']='partial'
        p,support=self.source_review()
        feedback={'status':'repair','repair_kind':'unresolved_verification','feedback':'Review the exact source meaning.',
            'prose_repair':E.source_prose_repair(self.base,support,[p],{})}
        candidate=E.apply_prose_repair(self.patch(review='Review 42 events before confirming responsibility.'),
            self.base,calls=self.calls,feedback=feedback)
        assessment=B.review_final(SimpleNamespace(),'Explain each entity',candidate,self.calls,
            SimpleNamespace(read={'note_A':{'text':'Dated observation'}}),ledger=None,as_of='2030-05-27')
        self.assertEqual(assessment['repair_kind'],'deterministic_compiler')
        return feedback,candidate,assessment

    def test_source_edit_remains_editable_after_compiler_failure_without_expanding_scope(self):
        feedback,candidate,assessment=self.source_compiler_fixture()
        continued=E.continue_source_prose_repair(self.base,feedback,candidate,assessment)
        self.assertTrue(continued['prose_repair']['pending_compiler'])
        self.assertEqual(continued['prose_repair']['source_contexts'],feedback['prose_repair']['source_contexts'])
        proposal={'repair_draft':{'draft_digest':digest(candidate),'rows':[{'key':{'patient_id':'A'},
            'fields':{'review':'Confirm responsibility from the dated source; the current clinician is unknown.'}}]}}
        repaired=E.apply_prose_repair(proposal,candidate,calls=self.calls,feedback=continued)
        self.compile(repaired)
        self.assertEqual(repaired['table'][1],self.base['table'][1])
        self.assertEqual(repaired['table'][0]['events'],2)
        self.assertIn('not a sentence-preserving',E.focused_prose_state(candidate,continued,self.calls)['instruction'])

    def test_source_compiler_continuation_rejects_changed_native_other_prose_and_stale_source(self):
        feedback,candidate,assessment=self.source_compiler_fixture()
        for kind in ('native','other_prose','global','basis','stale','source','pure_binding','string_feedback'):
            c=copy.deepcopy(candidate);f=copy.deepcopy(feedback)
            if kind=='native':c['table'][0]['events']=99
            elif kind=='other_prose':c['row_prose']['rows'][1]['fields']['review']='Changed unrelated patient.'
            elif kind=='global':c['missing']=[]
            elif kind=='basis':c['row_prose']['rows'][0]['basis']=[]
            elif kind=='stale':f['prose_repair']['draft_digest']='stale'
            elif kind=='source':f['prose_repair']['source_contexts'][0]['text']='Changed'
            elif kind=='pure_binding':f={'prose_repair':assessment['prose_repair']}
            elif kind=='string_feedback':f='Protocol error'
            with self.subTest(kind=kind):
                self.assertEqual(E.continue_source_prose_repair(self.base,f,c,assessment),assessment)

    def test_list_layout_is_not_a_quantity_but_doses_counts_and_inline_numbers_still_bind(self):
        for style in ('{}.','{})','({})'):
            text='\n'.join(style.format(i)+' '+s for i,s in enumerate(('Confirm responsibility.','Review the source.'),1))
            a=E.apply_prose_repair(self.patch(review=text),self.base);self.compile(a)
            self.assertEqual(a['table'][0]['review'],text)
        for text in ('1. Confirm 10 mg.\n2. Review the source.',
                     '1. Review two events.\n2. Confirm responsibility.',
                     '1. Confirm responsibility.', '1. Confirm responsibility.\n3. Review the source.',
                     'Confirm (1) event; review (2) events.'):
            with self.subTest(text=text),self.assertRaises(B.ComputedProseError):
                self.compile(E.apply_prose_repair(self.patch(review=text),self.base))

    def test_loop_keeps_complete_answer_on_failed_compiler_and_clears_marker_after_repair(self):
        for finish in (False,True):
            feedback,_,_=self.source_compiler_fixture();seen=[];sent=[]
            def review(a,c):
                if not seen:
                    seen.append(copy.deepcopy(a));return copy.deepcopy(feedback)
                trial=copy.deepcopy(a)
                try:self.compile(trial)
                except B.ComputedProseError:
                    assessed=B.review_final(SimpleNamespace(),'Explain each entity',a,c,
                        SimpleNamespace(read={'note_A':{'text':'Dated observation'}}),ledger=None,as_of='2030-05-27')
                else:
                    a.clear();a.update(trial);assessed={'status':'confirmed'}
                seen.append(copy.deepcopy(a));return assessed
            def send(body):
                sent.append(body)
                text='Confirm current responsibility from the dated note.'if finish and len(sent)>1 else'Review 42 events.'
                proposal={'repair_draft':{'draft_digest':digest(seen[-1]),'rows':[
                    {'key':{'patient_id':'A'},'fields':{'review':text}}]}}
                return {'content':[{'type':'text','text':json.dumps(proposal)}]},0,False
            out=S.tool_loop('Explain both entities',[],[],lambda use:self.fail('Unexpected query'),send=send,
                model=M.SONNET,max_tools=None,initial_calls=self.calls,initial_answer=self.base,review=review,
                compact_execution_state=True)
            with self.subTest(finish=finish):
                self.assertEqual(out['answer']['table'][1],self.base['table'][1])
                if finish:
                    self.assertNotIn('unaccepted_draft',out)
                    self.assertEqual(out['answer']['table'][0]['review'],'Confirm current responsibility from the dated note.')
                else:
                    self.assertEqual(out['answer']['table'],self.base['table'])
                    self.assertEqual(out['answer']['verification'],'unverified')
                    self.assertEqual(out['unaccepted_draft']['returned_answer'],'last_structurally_complete_candidate')

    def test_empty_patch_escalates_unfinished_compiler_work_but_preserves_complete_answer(self):
        feedback,_,_=self.source_compiler_fixture();seen=[];sent=[]
        def review(a,c):
            seen.append(copy.deepcopy(a))
            if len(seen)==1:return copy.deepcopy(feedback)
            return B.review_final(SimpleNamespace(),'Explain each entity',a,c,
                SimpleNamespace(read={'note_A':{'text':'Dated observation'}}),ledger=None,as_of='2030-05-27')
        def send(body):
            sent.append(body);focus=json.loads(body['messages'][0]['content'])['focused_prose_repair']
            rows=[{'key':{'patient_id':'A'},'fields':{'review':'Review 42 events.'}}]if len(sent)==1 else[]
            return {'content':[{'type':'text','text':json.dumps({'repair_draft':{'draft_digest':focus['draft_digest'],'rows':rows}})}]},0.,False
        out=S.tool_loop('Explain both entities',[],[],lambda use:self.fail('Unexpected query'),send=send,
            model=M.HAIKU,escalation_model=M.SONNET,max_tools=None,initial_calls=self.calls,initial_answer=self.base,
            review=review,compact_execution_state=True)
        self.assertEqual([b['model']for b in sent],[M.HAIKU,M.HAIKU,M.SONNET])
        self.assertEqual(len(seen),2);self.assertEqual(out['review_stopped'],'compiler_unresolved')
        self.assertEqual(out['answer']['table'],self.base['table']);self.assertEqual(out['answer']['verification'],'unverified')


if __name__=='__main__':unittest.main()
