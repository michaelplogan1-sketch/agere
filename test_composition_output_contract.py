"""Native completion requirements survive protocol errors and constrain output."""
import copy,json,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import backstop_agent as B,execution_state as E,semantic_analyst as S
from clinical_core.agere import subscription_transport as T
from clinical_core.contracts import digest


class CompositionOutputContract(unittest.TestCase):
    def setUp(self):
        self.calls=[{'name':'query_sql','input':{'sql':'SELECT clinic_id,events FROM received'},
            'result':{'result_view':'agere_result_1','columns':['clinic_id','events'],
                      'rows':[['A',2],['B',None]],'truncated':False}}]
        self.draft={'status':'partial','answer_type':'table','headline':'Interpretation remains unfinished.',
            'table_from_tool':'agere_result_1','missing':['The row explanations remain unfinished.']}
        B.materialize_table(self.draft,self.calls)
        self.composition={'targets':[{'row':0,'clinic_id':'A','reason':'Visible deferral.'}],
                          'judgments':{},'calls':0,'cost_usd':0}

    def feedback(self,candidate=None):
        return B.incomplete_composition({},copy.deepcopy(self.composition),candidate or self.draft)

    def completed(self):
        candidate=copy.deepcopy(self.draft)
        candidate['row_prose']={'keys':['clinic_id'],'rows':[{
            'key':{'clinic_id':key},'basis':[{'result_view':'agere_result_1','row':i}],
            'fields':{'explanation':{'template':'Received events: {n}. Responsibility remains unknown.',
                       'bindings':{'n':{'basis':0,'column':'events'}}},
                      'next_review':'Confirm current responsibility with the recorded source.'}}
            for i,key in enumerate(['A','B'])]}
        return candidate

    def test_schema_uses_full_native_membership_not_only_located_rows(self):
        before=copy.deepcopy(self.calls);schema=E.composition_output_schema(self.draft,self.feedback(),self.calls)
        rows=schema['properties']['row_prose']['properties']['rows']
        self.assertEqual(rows['minItems'],2);self.assertEqual(rows['maxItems'],2)
        self.assertEqual(rows['items']['properties']['key']['enum'],[{'clinic_id':'A'},{'clinic_id':'B'}])
        self.assertIn('row_prose',schema['required']);self.assertEqual(self.calls,before)
        self.assertIsNone(E.composition_contract_error(self.completed(),schema))
        B.bind_computed_prose(self.completed(),self.calls)

    def test_stale_unbound_or_incomplete_review_cannot_constrain_an_answer(self):
        for mutation in ('stale','kind','target_index','target_entity','target_key','duplicate_native','incomplete_native'):
            draft=copy.deepcopy(self.draft);feedback=self.feedback();calls=copy.deepcopy(self.calls)
            if mutation=='stale':draft['headline']='A different draft.'
            elif mutation=='kind':feedback['repair_kind']='unresolved_verification'
            elif mutation=='target_index':feedback['composition']['targets'][0]['row']=True
            elif mutation=='target_entity':feedback['composition']['targets'][0]['clinic_id']='B'
            elif mutation=='target_key':feedback['composition']['targets'][0]['invented']='A'
            elif mutation=='duplicate_native':calls[0]['result']['rows'][1][0]='A'
            else:calls[0]['result']['truncated']=True
            with self.subTest(mutation=mutation):self.assertIsNone(E.composition_output_schema(draft,feedback,calls))

    def test_existing_composite_key_order_is_preserved(self):
        calls=copy.deepcopy(self.calls)
        calls[0]['result']['columns'].append('as_of')
        for row in calls[0]['result']['rows']:row.append('2030-05-27')
        draft=self.completed();draft['row_prose']['keys']=['clinic_id','as_of']
        for entry in draft['row_prose']['rows']:entry['key']['as_of']='2030-05-27'
        composition=copy.deepcopy(self.composition);composition['targets'][0]['as_of']='2030-05-27'
        feedback=B.incomplete_composition({},composition,draft)
        schema=E.composition_output_schema(draft,feedback,calls)
        self.assertEqual(schema['properties']['row_prose']['properties']['keys']['enum'],[['clinic_id','as_of']])
        self.assertIsNone(E.composition_contract_error(draft,schema))

    def test_omitted_duplicate_foreign_and_incomplete_fields_fail_before_review(self):
        base=self.completed();schema=E.composition_output_schema(base,self.feedback(base),self.calls)
        for mutation in ('missing_spec','missing_row','duplicate_row','foreign_key','empty_fields','missing_field'):
            candidate=copy.deepcopy(base)
            if mutation=='missing_spec':candidate.pop('row_prose')
            elif mutation=='missing_row':candidate['row_prose']['rows'].pop()
            elif mutation=='duplicate_row':candidate['row_prose']['rows'][1]=copy.deepcopy(candidate['row_prose']['rows'][0])
            elif mutation=='foreign_key':candidate['row_prose']['rows'][0]['key']['clinic_id']='C'
            elif mutation=='empty_fields':candidate['row_prose']['rows'][0]['fields']={}
            else:candidate['row_prose']['rows'][0]['fields'].pop('next_review')
            with self.subTest(mutation=mutation):self.assertIsNotNone(E.composition_contract_error(candidate,schema))
        self.assertIsNone(E.composition_contract_error({'status':'unsupported'},None))

    def test_protocol_failure_retains_review_and_incomplete_replacement_cannot_erase_work(self):
        for bad in ('<invoke name="note">'+json.dumps(self.completed())+'</invoke>\n'+json.dumps(self.draft),'{"unfinished":'):
            sent=[];reviewed=[];responses=iter([bad,json.dumps(self.draft),json.dumps(self.completed())])
            def review(candidate,native):
                reviewed.append(copy.deepcopy(candidate))
                if len(reviewed)==1:return self.feedback(candidate)
                B.bind_computed_prose(candidate,native)
                return {'status':'repair','feedback':'Independent source support remains unresolved.',
                    'retry_disposition':'retain_partial_until_new_evidence','review_stop_reason':'unresolved_source_support'}
            def send(body):
                sent.append(copy.deepcopy(body));state=json.loads(body['messages'][0]['content'])
                self.assertIn('row_prose',body['output_config']['format']['schema']['required'])
                if len(sent)>1:
                    self.assertEqual(state['current_review_feedback']['repair_kind'],'incomplete_composition')
                    self.assertEqual(state['last_draft_digest'],digest(self.draft))
                    self.assertIn('pending_protocol_response',state);self.assertIn('repair_protocol_feedback',state)
                    self.assertFalse(state['pending_protocol_response']['accepted_answer'])
                    self.assertFalse(state['pending_protocol_response']['tool_actions_executed'])
                return {'content':[{'type':'text','text':next(responses)}]},0.,True
            with self.subTest(bad=bad[:30]):
                out=S.tool_loop('Explain each clinic and propose review steps.','system',[],
                    lambda use:self.fail('No unknown invocation can execute'),send=send,model='scripted',max_tools=None,
                    initial_calls=self.calls,initial_answer=self.draft,review=review,compact_execution_state=True,format_retries=1)
                self.assertEqual(len(sent),3);self.assertEqual(len(reviewed),2)
                self.assertEqual(out['review_stopped'],'unresolved_source_support')
                self.assertEqual(out['answer']['status'],'partial');self.assertEqual(len(out['answer']['table']),2)
                self.assertIn('events: unknown',out['answer']['table'][1]['explanation'])
                self.assertEqual(out['protocol_repair_responses'][0]['content'][0]['text'],bad)
                self.assertEqual(out['tool_calls'],self.calls)

    def test_native_cli_schema_uses_existing_transport_and_does_not_accept_source_meaning(self):
        schema=E.composition_output_schema(self.draft,self.feedback(),self.calls)
        body={'model':'fixture','system':'system','messages':[{'role':'user','content':'request'}],
            'tools':[],'tool_choice':{'type':'none'},'output_config':{'format':{'type':'json_schema','schema':schema}}}
        proposal=self.completed()
        with patch.object(T,'run_cli',return_value={'structured_output':proposal,'usage':{}})as cli:
            response=T.respond(body)
        self.assertEqual(cli.call_args.kwargs['json_schema'],schema)
        self.assertEqual(json.loads(response['content'][0]['text']),proposal)
        proposal['row_prose']['rows'][0]['fields']['explanation']='There were 99 events.'
        self.assertIsNone(E.composition_contract_error(proposal,schema))
        with self.assertRaises(B.ComputedProseError):B.bind_computed_prose(proposal,self.calls)


if __name__=='__main__':unittest.main()
