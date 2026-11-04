"""The workspace shows complete saved rows without duplicating native execution payloads."""
import copy
import json
import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.agere.deliverables.action_center import inspection_view, encode_inspection, compose_view


class ComposePayload(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.key='ANS-'+'a'*24
        target=self.root/self.key;target.mkdir();self.file=target/'answer.json'
        self.patch=patch('clinical_core.agere.deliverables.answer_context.root',return_value=self.root)
        self.patch.start();self.addCleanup(self.patch.stop)
        large='Full native observation; do not turn this into an accepted clinical fact. '*3000
        graph={'interpretation':'Complete original scope','population':['A','B'],'nodes':[
            {'id':'extra','operation':'sql','inputs':[],'parameters':{'query':'SELECT '+large},'purpose':'Retained investigation'},
            {'id':'final','operation':'compose_table','inputs':['extra'],'parameters':{'base':'v'},'purpose':'Answer'}]}
        review={'status':'repair','support':{'label':'unknown','decided_by':None,'packets':[{'quote':large}]},'feedback':'Review needed.'}
        contract={'id':'contract','kind':'checked_graph','population':['A','B'],'graph':graph,'unresolved':['Eligibility unknown']}
        answer={'status':'partial','verification':'unverified','table':[{'patient_id':'A','value':None},{'patient_id':'B','value':3}],
                'row_prose':{'rows':[{'patient_id':'A','fields':{'reason':'Not documented.'}}]},'missing':['Eligibility unknown'],
                'investigation_review':review}
        self.result={'question':'Original question','answer':answer,'graph':graph,'request_contract':contract,
            'planner_receipt':{'route':'backstop_agent','request_contract':contract,'backstop':{'answer_reviews':[review]}},
            'investigation':{'model':'fixture','tool_calls':[{'name':'query_sql','input':{'sql':'SELECT 1'},'result':{'rows':[[1]],'trace':large}}],
                             'answer_reviews':[review],'recovery_checkpoint':{'draft':answer}},
            'action_context':{'id':self.key,'url':'/action-center?context='+self.key},
            'cost_receipt':{'usd':None,'usd_known':False,'paid_calls':2},'cost_telemetry':{'attempt_ids':['one','two']},
            'workflow':[],'conversation':{'id':'C','turn':2},'results':[{'complete':large}]}
        self.context={**copy.deepcopy(self.result),'id':self.key,'dependencies':{'derivation':'exact-native-snapshot'}}
        self.file.write_text(json.dumps(self.context))

    def test_browser_view_preserves_original_graph_and_every_answer_and_accounting_field(self):
        before=copy.deepcopy(self.result);native=self.file.read_bytes()
        view=compose_view(self.root,self.result)
        for key in ('answer','action_context','cost_receipt','cost_telemetry','workflow','conversation','question'):
            self.assertEqual(view[key],before[key])
        for shown,original in zip(view['graph']['nodes'],before['graph']['nodes']):
            for key in ('id','operation','inputs','purpose'):self.assertEqual(shown[key],original[key])
            ref=shown['parameters']['native_reference'];value=self.context
            for part in ref['path']:value=value[part]
            self.assertEqual(value,original['parameters']);self.assertEqual(ref['context_derivation'],'exact-native-snapshot')
        self.assertEqual(len(view['graph']['nodes']),len(before['graph']['nodes']))
        self.assertEqual(view['request_contract']['population'],['A','B'])
        self.assertEqual(view['request_contract']['unresolved'],['Eligibility unknown'])
        self.assertEqual(view['request_contract']['graph'],view['graph'])
        self.assertTrue(view['investigation']['draft_resume_available'])
        self.assertNotIn('tool_calls',view['investigation'])
        self.assertEqual(view['results']['native_reference']['path'],['results'])
        self.assertLess(len(json.dumps(view)),len(json.dumps(before))//3)
        self.assertEqual(self.result,before);self.assertEqual(self.file.read_bytes(),native)

    def test_missing_changed_or_unbound_native_record_falls_back_without_losing_the_answer(self):
        for change in ('missing','id','graph','review','receipt','answer'):
            context=copy.deepcopy(self.context)
            if change=='id':context['id']='ANS-'+'b'*24
            elif change=='graph':context['graph']['nodes'].pop()
            elif change=='review':context['investigation']['answer_reviews'][0]['status']='confirmed'
            elif change=='receipt':context['planner_receipt']['route']='other'
            elif change=='answer':context['answer']['table'][0]['value']=99
            self.file.write_text(json.dumps(context))
            if change=='missing':self.file.unlink()
            with self.subTest(change=change):self.assertIs(compose_view(self.root,self.result),self.result)
        self.file.write_text(json.dumps(self.context))
        for key in ('../../native','ANS-fixture',None):
            result={**self.result,'action_context':{'id':key}}
            self.assertIs(compose_view(self.root,result),result)

    def test_saved_membership_does_not_rewrite_the_returned_answer_or_workflow(self):
        self.context['answer']={**self.context['answer'],'patient_ids':['A','B'],'membership_role':'computed_population'}
        self.file.write_text(json.dumps(self.context));result={**self.result,'workflow':[{'kind':'monitor','status':'watching'}]}
        view=compose_view(self.root,result)
        self.assertEqual(view['answer'],result['answer']);self.assertEqual(view['workflow'],result['workflow'])
        self.assertFalse(view['investigation']['draft_resume_available'])

    def test_json_encoding_does_not_emit_nonstandard_nan(self):
        with self.assertRaises(ValueError):encode_inspection({'amount':float('nan')},'gzip')

    def test_exact_source_review_downgrade_keeps_current_answer_and_blocked_work(self):
        self.result['answer']['status']='answered'
        self.result['answer']['verification']='verified'
        self.context={**copy.deepcopy(self.result),'id':self.key,'dependencies':{'derivation':'exact-native-snapshot'}}
        self.file.write_text(json.dumps(self.context));native=self.file.read_bytes()
        result=copy.deepcopy(self.result)
        result.update(source_review_required=True,clinical_execution_authorized=False,
                      workflow=[{'kind':'simulation','status':'blocked','reason':'Source review required'}])
        result['answer']={**result['answer'],'status':'partial','verification':'unverified','captured_status':'answered'}
        before=copy.deepcopy(result);view=compose_view(self.root,result)
        self.assertIn('response_projection',view)
        for key in ('answer','workflow','source_review_required','clinical_execution_authorized','cost_receipt','cost_telemetry'):
            self.assertEqual(view[key],before[key])
        self.assertFalse(view['investigation']['draft_resume_available'])
        self.assertEqual(result,before);self.assertEqual(self.file.read_bytes(),native)

    def test_source_review_metadata_never_hides_an_answer_or_authority_mismatch(self):
        for change in ('capture','status','verification','row','review','missing_flag','authority','clinical_limit'):
            result=copy.deepcopy(self.result)
            result.update(source_review_required=True,clinical_execution_authorized=False)
            result['answer']=copy.deepcopy(result['answer'])
            result['answer']['captured_status']='partial'
            if change=='capture':result['answer']['captured_status']='answered'
            elif change=='status':result['answer']['status']='answered'
            elif change=='verification':result['answer']['verification']='verified'
            elif change=='row':result['answer']['table'][0]['value']=99
            elif change=='review':result['answer']['investigation_review']['status']='confirmed'
            elif change=='missing_flag':result.pop('source_review_required')
            elif change=='authority':result['clinical_execution_authorized']=True
            elif change=='clinical_limit':result['answer']['missing']=[]
            with self.subTest(change=change):self.assertIs(compose_view(self.root,result),result)


class WorkspacePayload(unittest.TestCase):
    def test_resume_availability_does_not_send_draft_checkpoint_to_browser(self):
        record={'context':{'id':'ANS-fixture','answer':{'status':'partial'},'workflow':[],
            'investigation':{'recovery_checkpoint':{'draft':{'headline':'Retained internal draft'}},
                'tool_calls':[{'name':'query_sql','input':{'sql':'select 1'},'result':{'rows':[[1]],'columns':['value']}}]}}}
        shown=inspection_view(record)['context']['investigation']
        self.assertTrue(shown['draft_resume_available']);self.assertNotIn('recovery_checkpoint',shown)
        record['context']['workflow']=[{'kind':'monitor'}]
        self.assertFalse(inspection_view(record)['context']['investigation']['draft_resume_available'])

    def test_native_history_is_referenced_without_changing_patients_judgments_or_work(self):
        rows = [{'patient_id': 'A', 'quote': 'MRA held; source interpretation unresolved'},
                {'patient_id': 'B', 'quote': 'Other source retained'}]
        call = {'name': 'query_sql', 'input': {'sql': 'SELECT * FROM patient'},
                'result': {'result_view': 'agere_result_1', 'rows': rows,
                           'executed_query': 'SELECT * FROM patient'}}
        answer = {'status': 'partial', 'table_from_tool': 'agere_result_1', 'table': rows,
                  'investigation_review': {'status': 'repair', 'source_flags': ['A']}}
        context = {'id': 'ANS-fixture', 'answer': answer,
                   'patient_ids': ['A', 'B'], 'patient_rows': [{'patient_id': r['patient_id'], 'evidence_rows': [r]} for r in rows],
                   'graph': {'nodes': [{'parameters': {'query': 'retained old native SQL'}}]},
                   'planner_receipt': {'request_contract': {'kind': 'checked_graph', 'graph': {'large': 'old'}}},
                   'investigation': {'tool_calls': [call], 'document_reviews': [], 'answer_reviews': [{'status': 'repair'}]},
                   'stale': False, 'invalidated': False}
        record = {'context': context, 'items': [{'id': 'WI-1', 'state': 'needs_review', 'revision': 2}], 'counts': {'needs_review': 1}}
        prior = copy.deepcopy(record)
        result = inspection_view(record)
        for key in ('answer', 'patient_ids', 'patient_rows', 'stale', 'invalidated'):
            self.assertEqual(result['context'][key], context[key])
        self.assertEqual(result['items'], record['items'])
        self.assertEqual(result['counts'], record['counts'])
        self.assertTrue(result['context']['investigation']['complete_native_history_retained'])
        self.assertEqual(result['context']['investigation']['answer_reviews'], [{'status': 'repair'}])
        self.assertEqual(result['context']['graph']['nodes'][0]['output']['result_view'], 'agere_result_1')
        self.assertEqual(result['context']['planner_receipt']['request_contract']['graph'], result['context']['graph'])
        self.assertNotIn('tool_calls', result['context']['investigation'])
        self.assertEqual(record, prior)

    def test_noninvestigation_context_is_unchanged(self):
        record = {'context': {'id': 'ANS-fixture', 'answer': {'status': 'answered'}, 'graph': {'nodes': []}}, 'items': []}
        self.assertIs(inspection_view(record), record)

    def test_large_native_parameters_and_outputs_are_referenced_without_censoring_answer_fields(self):
        large='Exact retained intermediate source text. '*10000
        calls=[{'name':'query_sql','input':{'sql':'invalid'},'result':{'error':'No column'}},
               {'name':'compose_table','input':{'base':'base','joins':[large]},
                'result':{'result_view':'final','rows':[{'patient_id':'A','value':None}],
                          'columns':['patient_id','value'],'assembly':{'expanded':large},
                          'candidate_observation_review':{'complete_intermediate':large}}}]
        answer={'status':'partial','table_from_tool':'final','table':[{'patient_id':'A','exact_quote':'Intolerance documented','dose':None}],
                'missing':['Maximum tolerated dose not established.'],'investigation_review':{'status':'repair','clinical_accuracy_established':False}}
        context={'id':'ANS-fixture','answer':answer,'patient_ids':['A'],'patient_rows':[{'patient_id':'A','evidence_rows':answer['table']}],
                 'dependencies':{'derivation':'sealed-fixture'},'source_review_required':True,
                 'source_disposition':{'status':'suspended'},'investigation':{'tool_calls':calls,'answer_reviews':[answer['investigation_review']]}}
        record={'context':context,'items':[{'id':'WI','revision':3,'state':'blocked'}],'workflow':{'steps':[{'status':'blocked'}]}}
        before=copy.deepcopy(record);view=inspection_view(record)
        for key in ('answer','patient_ids','patient_rows','source_review_required','source_disposition','dependencies'):
            self.assertEqual(view['context'][key],context[key])
        self.assertEqual(view['items'],record['items']);self.assertEqual(view['workflow'],record['workflow'])
        node=view['context']['graph']['nodes'][0]
        self.assertEqual(node['id'],'tool_1')
        for field in ('parameters','output'):
            ref=node[field]['native_reference'];value=context
            for part in ref['path']:value=value[part]
            self.assertEqual(value,calls[1]['input'if field=='parameters'else'result'])
            self.assertEqual(ref['context_derivation'],'sealed-fixture')
        self.assertLess(len(json.dumps(view)),len(json.dumps(record))//20)
        self.assertEqual(record,before)

    def test_http_gzip_is_lossless_and_an_explicit_refusal_remains_identity(self):
        value={'context':{'answer':{'status':'partial','table':[{'patient_id':'A','dose':None,'quote':'Exact β blocker quote'}]},
                          'patient_rows':[{'patient_id':'A'}],'source_review_required':True},'items':[{'revision':2}]}
        identity,encoding=encode_inspection(value)
        self.assertIsNone(encoding);self.assertEqual(json.loads(identity),value)
        for accept in ('gzip','br, gzip, deflate','GZip; q=0.5'):
            with self.subTest(accept=accept):
                raw,encoding=encode_inspection(value,accept);self.assertEqual(encoding,'gzip')
                self.assertEqual(gzip.decompress(raw),identity)
        for accept in ('gzip;q=0','gzip;q=0, *;q=1','gzip;q=invalid','br'):
            with self.subTest(accept=accept):
                raw,encoding=encode_inspection(value,accept);self.assertIsNone(encoding);self.assertEqual(raw,identity)


class ReviewProjection(unittest.TestCase):
    def record(self):
        claim={'packet_id':'patient_A','claim_id':'reason','field':'reason','label':'unknown','decided_by':None,
               'source_record_ids':['R1'],'quote':'Exact source quote',
               'verification_observation':{'state':'below_acceptance_threshold','model_judgment':{'probability':.6,'threshold':.8}}}
        feedback='Current evidence is unresolved. Review '+json.dumps([claim]*20)+' Preserve the quoted source.'
        review={'status':'repair','repair_kind':'unresolved_verification','feedback':feedback,
                'support':{'all_dependencies_checked':True,'claim_feedback':[claim],
                           'packets':[{'id':'patient_A','label':'unknown','decided_by':None,'claim_feedback':[claim]}]}}
        answer={'status':'partial','table':[{'patient_id':'A','quote':feedback,'limitations':feedback}],
                'missing':['Verify the current reason.'],'limitations':['Source limitation unchanged.','Before '+feedback+' After'],
                'investigation_review':review}
        receipt={'backstop':{'answer_reviews':[review],'seconds':1},'route':'backstop_agent'}
        return {'context':{'id':'ANS-fixture','dependencies':{'derivation':'sealed'},'answer':answer,
                          'patient_rows':[{'patient_id':'A','evidence_rows':answer['table']}],
                          'planner_receipt':{**receipt,'request_contract':{'kind':'checked_graph','graph':{},'legacy_receipt':receipt}},
                          'investigation':{'tool_calls':[{'name':'query_sql','input':{'sql':'SELECT 1'},'result':{'result_view':'v','columns':['patient_id'],'rows':[['A']]}}],
                                           'answer_reviews':[review]},'source_review_required':True},
                'items':[{'id':'WI','state':'blocked','revision':3}],'counts':{'blocked':1}}

    def resolve(self,context,reference):
        self.assertEqual(reference['native_context_id'],context['id']);self.assertEqual(reference['context_derivation'],'sealed')
        value=context
        for key in reference['path']:value=value[key]
        return value

    def test_duplicate_history_and_structured_prose_keep_exact_native_bindings(self):
        record=self.record();before=copy.deepcopy(record);context=record['context'];view=inspection_view(record);shown=view['context'];answer=shown['answer'];review=answer['investigation_review']
        for key in ('table','missing','status'):self.assertEqual(answer[key],context['answer'][key])
        self.assertEqual(shown['patient_rows'],context['patient_rows']);self.assertEqual(view['items'],record['items']);self.assertTrue(shown['source_review_required'])
        self.assertEqual(review['support']['claim_feedback'],context['answer']['investigation_review']['support']['claim_feedback'])
        packet=review['support']['packets'][0]
        self.assertEqual([self.resolve(context,r)for r in packet['claim_feedback_native_references']],context['answer']['investigation_review']['support']['packets'][0]['claim_feedback'])
        self.assertEqual(packet['label'],'unknown');self.assertIsNone(packet['decided_by'])
        for ref in review['feedback_native_spans']:
            text=self.resolve(context,ref);self.assertIsInstance(json.loads(text[ref['start']:ref['end']]),list)
        self.assertTrue(review['feedback'].startswith('Current evidence is unresolved. Review '));self.assertTrue(review['feedback'].endswith(' Preserve the quoted source.'))
        self.assertEqual(answer['limitations'][0],context['answer']['limitations'][0]);self.assertEqual(answer['limitations'][1],'Before '+review['feedback']+' After')
        self.assertEqual(self.resolve(context,answer['limitation_native_references'][0]),context['answer']['limitations'][1])
        for review_ref in (shown['investigation']['answer_reviews'][0],shown['planner_receipt']['backstop']['answer_reviews'][0],shown['planner_receipt']['request_contract']['legacy_receipt']['backstop']['answer_reviews'][0]):
            self.assertEqual(self.resolve(context,review_ref['native_reference']),context['answer']['investigation_review'])
            self.assertEqual(review_ref['status'],'repair')
        self.assertLess(len(json.dumps(view)),len(json.dumps(record)));self.assertEqual(record,before)

    def test_unmatched_packet_judgments_and_unbound_prose_are_never_removed(self):
        record=self.record();review=record['context']['answer']['investigation_review'];review['support']['packets'][0]['claim_feedback']=[{'claim_id':'not_in_global','label':'contradicted'}]
        record['context']['answer']['limitations'].append('Unbound '+json.dumps({'clinical_fact':'exact '*200}))
        original=copy.deepcopy(record);shown=inspection_view(record)['context']['answer']
        self.assertEqual(shown['investigation_review']['support']['packets'],review['support']['packets'])
        self.assertEqual(shown['limitations'][-1],record['context']['answer']['limitations'][-1]);self.assertEqual(record,original)


if __name__ == '__main__':
    unittest.main()
