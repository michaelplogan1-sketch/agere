"""Storage errors retain completed work without buying another analyst turn."""
import copy,errno,json,sqlite3,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from clinical_core.agere.clinical import semantic_analyst as S
TOOLS=[{'name':name,'input_schema':{'type':'object','additionalProperties':True}}
       for name in ('query_sql','review_documents','read_document')]

def full():
    e=sqlite3.OperationalError('database or disk is full')
    e.sqlite_errorcode=sqlite3.SQLITE_FULL;e.sqlite_errorname='SQLITE_FULL'
    return e

def response(content):
    return {'content':content,'usage':{'input_tokens':4,'output_tokens':5}},.012,False

def tool(ident,name,**args):
    return {'type':'tool_use','id':ident,'name':name,'input':args}


class NativeRuntimeRecovery(unittest.TestCase):
    def test_storage_error_stops_siblings_and_provider_continuation(self):
        actions=[tool('a','query_sql',sql='SELECT all_received_rows'),
                 tool('b','review_documents',patient_ids=['A','B']),tool('c','query_sql',sql='SELECT more_work')]
        received={'columns':['patient_id','known','unknown'],
                  'rows':[{'patient_id':'A','known':0,'unknown':None},{'patient_id':'B','known':2,'unknown':None}]}
        invoked=[]
        def execute(action):
            invoked.append(copy.deepcopy(action))
            if action['name']=='review_documents':raise full()
            return copy.deepcopy(received)
        send=Mock(side_effect=[response(actions)])
        out=S.tool_loop('q','',TOOLS,execute,send=send,model='entry',escalation_model='frontier',max_tools=None,
                        continuation_guard=lambda:{'source':'unchanged'})
        self.assertEqual(send.call_count,1);self.assertEqual(len(invoked),2)
        self.assertEqual(out['tool_calls'][0]['result'],received)
        self.assertEqual(out['tool_calls'][1]['result']['runtime_dependency']['sqlite_errorcode'],sqlite3.SQLITE_FULL)
        self.assertEqual(out['runtime_pause']['unexecuted_actions'],[actions[2]])
        self.assertFalse(out['runtime_pause']['pending_actions_executed'])
        self.assertEqual(out['review_stopped'],'native_runtime_unavailable')
        self.assertEqual(out['model'],'entry');self.assertFalse(out.get('model_escalations'))
        self.assertEqual(out['new_paid_api_calls'],1);self.assertEqual(out['estimated_cost_usd'],.012)
        self.assertEqual(out['answer']['status'],'partial');self.assertEqual(out['answer']['verification'],'unverified')
        self.assertFalse(out['answer']['data_absence_established']);self.assertFalse(out['answer']['actionable_cohort'])

    def test_completed_answer_and_native_receipts_survive_the_stop(self):
        old={'status':'partial','answer_type':'table','headline':'Source interpretation remains unresolved.',
             'table':[{'patient_id':'A','reported':0,'current':None}], 'sources':[{'record_id':'r','quote':'Exact source.'}]}
        native=[{'name':'query_sql','input':{'sql':'SELECT complete'},'result':{'rows':old['table']}}]
        review=Mock(return_value={'status':'repair','repair_kind':'incomplete_work','feedback':'Read the unresolved source.'})
        send=Mock(return_value=response([tool('a','read_document',record_id='r')]))
        out=S.tool_loop('q','',TOOLS,Mock(side_effect=OSError(errno.ENOSPC,'No space left on device')),
            send=send,model='entry',escalation_model='frontier',max_tools=None,
            initial_calls=native,initial_answer=old,review=review)
        self.assertEqual(out['answer']['table'],old['table']);self.assertEqual(out['answer']['sources'],old['sources'])
        self.assertEqual(out['tool_calls'][:1],native);self.assertEqual(review.call_count,1)
        self.assertEqual(send.call_count,1);self.assertEqual(out['runtime_dependency']['errno'],errno.ENOSPC)
        self.assertEqual(old['status'],'partial');self.assertNotIn('investigation_review',old)

    def test_bad_sql_and_source_text_are_not_storage_exceptions(self):
        for error in [ValueError('database or disk is full'),sqlite3.OperationalError('no such column: missing'),
                      OSError(errno.ENOENT,'missing optional path')]:
            with self.subTest(error=error):self.assertIsNone(S.native_runtime_failure(error))
        replies=[response([tool('a','query_sql',sql='SELECT missing')]),
                 response([{'type':'text','text':json.dumps({'status':'partial','answer_type':'composite',
                     'headline':'Received document says database or disk is full.'})}])]
        send=Mock(side_effect=replies)
        out=S.tool_loop('q','',TOOLS,Mock(side_effect=sqlite3.OperationalError('no such column: missing')),
                        send=send,model='entry',escalation_model='frontier',max_tools=None)
        self.assertEqual(send.call_count,2);self.assertNotIn('runtime_dependency',out)

    def test_missing_global_basis_uses_entry_model_before_escalation(self):
        reply=response([{'type':'text','text':json.dumps({'status':'partial','answer_type':'composite','headline':'A source observation.'})}])
        send=Mock(side_effect=[reply,reply])
        review=Mock(side_effect=[{'status':'repair','repair_kind':'global_source_dependencies','feedback':'Locate the existing native basis.'},
                                 {'status':'confirmed'}])
        out=S.tool_loop('q','',[],Mock(),send=send,model='entry',escalation_model='frontier',max_tools=None,review=review)
        self.assertEqual([c.args[0]['model']for c in send.call_args_list],['entry','entry'])
        self.assertFalse(out.get('model_escalations'))

    def test_other_filesystem_failures_keep_the_existing_terminal_path(self):
        send=Mock(return_value=response([tool('a','read_document',record_id='r')]))
        out=S.tool_loop('q','',TOOLS,Mock(side_effect=OSError(errno.ENOENT,'Missing path')),
                        send=send,model='entry',escalation_model='frontier',max_tools=None)
        self.assertEqual(send.call_count,1);self.assertNotIn('runtime_dependency',out)
        self.assertIn('FileNotFoundError',out['error']);self.assertFalse(out.get('model_escalations'))

    def test_repeated_identical_semantic_failure_can_still_escalate(self):
        reply=response([{'type':'text','text':json.dumps({'status':'partial','answer_type':'composite','headline':'A source observation.'})}])
        send=Mock(side_effect=[reply,reply,reply]);failed={'status':'repair','repair_kind':'global_source_dependencies','feedback':'Locate the existing native basis.'}
        review=Mock(side_effect=[failed,failed,{'status':'confirmed'}])
        out=S.tool_loop('q','',[],Mock(),send=send,model='entry',escalation_model='frontier',max_tools=None,review=review)
        self.assertEqual([c.args[0]['model']for c in send.call_args_list],['entry','entry','frontier'])
        self.assertEqual(len(out['model_escalations']),1)

    def test_backstop_consumer_retains_pause_without_another_paid_answer_gate(self):
        from clinical_core.agere.clinical import proposers as P,backstop_agent as B
        run=S.tool_loop('q','',TOOLS,Mock(side_effect=full()),
            send=Mock(return_value=response([tool('r','review_documents',patient_ids=['A'])])),
            model='entry',max_tools=None)
        cascade=P.Cascade.__new__(P.Cascade)
        cascade.stack=SimpleNamespace(allow_live=True);cascade.question='Review received evidence.'
        cascade.population=None;cascade._ledger=object();cascade.catalog=SimpleNamespace(identity='authored_fixture')
        cascade.spend=Mock();cascade.log=[]
        with patch.object(P,'claude_available',return_value=True),patch.object(B,'answer',return_value=run),patch.object(
            P,'shape_misfit',return_value=None),patch.object(P,'answer_gate',side_effect=AssertionError('No paid post-stop gate')):
            contract,interpretation=cascade.tier_backstop_agent()
        result=contract['executed']
        self.assertEqual(result['answer']['status'],'partial');self.assertEqual(result['answer']['verification'],'unverified')
        self.assertEqual(result['investigation']['runtime_dependency'],run['runtime_dependency'])
        self.assertEqual(result['investigation']['runtime_pause'],run['runtime_pause'])
        self.assertEqual(result['investigation']['tool_calls'],run['tool_calls'])
        self.assertIn('unavailable',str(result['answer']['investigation_review']))


if __name__=='__main__':unittest.main()
