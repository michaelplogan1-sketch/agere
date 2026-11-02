"""The workspace shows complete saved rows without duplicating native execution payloads."""
import copy
import json
import gzip
import unittest

from clinical_core.agere.deliverables.action_center import inspection_view, encode_inspection


class WorkspacePayload(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
