import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import source_plan_answers as A, source_plan_reconciliation as R
from clinical_core.agere.clinical import reference_reconciliation_transport as T, reference_table_context as F


class Answers(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.r=SimpleNamespace(path=Path(temp.name))
        self.contexts=[];self.answers={};groups=[]
        for i in (1,2):
            record='record'+str(i);u={'id':'unit'+str(i),'source_ref':record,'kind':'narrative','field':'text','start':0,'text':'Documented account '+str(i)}
            c={'id':digest(['context',i]),'patient_id':'P-1','parameters':{'question':'review'},'record_scope':{'record_ids':[record]},
               'model_context':{'snapshot':{'as_of':'2029-08-15'},'sources':{record:{'source':{'record_id':record,'patient_id':'P-1','identity_status':'matched'}}},'evidence':[u]}}
            self.contexts.append(c);groups.append({'context_id':c['id'],'context_hash':digest(c),'record_ids':[record],'group_number':i})
            a={'id':digest(['answer',i]),'status':'complete','context_id':c['id'],'patient_id':'P-1','question':'review',
               'context_digest':digest(c),'record_scope':c['record_scope'],
               'passes':[{'pass_number':1,'source_units':[u['id']],'result':{'unit_reviews':[{'unit':'u1','source_unit_id':u['id'],'status':'discussed'}],
                   'findings':[{'id':'p1_f1','kind':'documented_account','text':'p1:u1 original claim','unresolved':[],
                     'citations':[{'source_unit_id':u['id'],'source':{'record_id':record},'quote':u['text'],'field':'text','kind':'narrative'}]}], 'review_steps':[],'unanswered':[]}}],
               'dependencies':{'edges':[],'unresolved_references':[]},
               'coverage':{'complete_declared_scope':True,'received_units':1,'submitted_units':1,'model_accounted_units':1,'unassessed_units':[],'cross_group_relations_assessed':True}}
            self.answers[a['id']]=a
        self.plan={'id':digest('plan'),'groups':groups,'preview':{'patient_id':'P-1','review_question':'review','original_question':'original',
            'selected_record_ids':['record1','record2'],'excluded_records':20,'analysis_window':{'basis':'all_observed'}}}
        self.result={'plan':self.plan,'contexts':self.contexts}
        for target,name,effect in [(A.P,'inspect',lambda *args:deepcopy(self.result)),(A.S,'inspect',lambda p,k:deepcopy(self.answers[k]))]:
            p=patch.object(target,name,side_effect=effect);p.start();self.addCleanup(p.stop)
        self.ids=list(self.answers)

    def attach(self,index=0):return A.attach(self.r,{'plan_id':self.plan['id'],'answer_id':self.ids[index]})
    def snapshot(self):return A.prepare(self.r,{'plan_id':self.plan['id'],'attachment_ids':[self.attach(i)['attachment']['id'] for i in (0,1)]})

    def test_missing_then_attached_and_explicit_snapshot_replay(self):
        self.assertEqual(A.listing(self.r,{'plan_id':self.plan['id']})['answered_groups'],0)
        self.assertFalse(self.attach()['cache_hit']);self.assertTrue(self.attach()['cache_hit'])
        self.assertEqual(A.listing(self.r,{'plan_id':self.plan['id']})['answered_groups'],1)
        s=self.snapshot();self.assertEqual(s['snapshot']['source_units'],2)
        self.assertEqual(s['snapshot']['cross_group_interpretation'],'not_assessed')
        self.assertFalse(s['snapshot']['clinical_execution_authorized']);self.assertTrue(self.snapshot()['cache_hit'])
        self.assertEqual(A.inspect(self.r,{'id':s['snapshot']['id']})['snapshot'],s['snapshot'])

    def test_foreign_context_patient_question_and_scope_rejected(self):
        for key,value in [('context_id',digest('foreign')),('patient_id','P-other'),('question','other'),('context_digest','stale'),('status','partial'),('record_scope',{'record_ids':['other']})]:
            with self.subTest(key=key):
                original=self.answers[self.ids[0]][key];self.answers[self.ids[0]][key]=value
                with self.assertRaises(ValueError):self.attach()
                self.answers[self.ids[0]][key]=original

    def test_omitted_or_duplicate_source_unit_rejected(self):
        a=self.answers[self.ids[0]];reviews=a['passes'][0]['result']['unit_reviews']
        for replacement in ([],reviews+reviews,[{**reviews[0],'source_unit_id':'foreign'}]):
            a['passes'][0]['result']['unit_reviews']=replacement
            with self.assertRaisesRegex(ValueError,'every group'):self.attach()
        a['passes'][0]['result']['unit_reviews']=reviews

    def test_coverage_counts_and_incomplete_disallowed(self):
        coverage=self.answers[self.ids[0]]['coverage']
        for key in ('received_units','submitted_units','model_accounted_units','complete_declared_scope'):
            old=coverage[key];coverage[key]=0
            with self.assertRaises(ValueError):self.attach()
            coverage[key]=old

    def test_partial_and_duplicate_selections_rejected(self):
        a=self.attach()['attachment']['id']
        for ids in ([a],[a,a],[],[None]):
            with self.assertRaises(ValueError):A.prepare(self.r,{'plan_id':self.plan['id'],'attachment_ids':ids})

    def test_multiple_answers_never_implicitly_promoted(self):
        self.attach();other=deepcopy(self.answers[self.ids[0]]);other['id']=digest('alternate');self.answers[other['id']]=other
        A.attach(self.r,{'plan_id':self.plan['id'],'answer_id':other['id']})
        state=A.listing(self.r,{'plan_id':self.plan['id']})
        self.assertEqual(state['groups'][0]['status'],'answer_selection_required');self.assertEqual(len(state['groups'][0]['attachments']),2)

    def test_changed_answer_fails_reopen(self):
        s=self.snapshot();self.answers[self.ids[0]]['passes'][0]['result']['findings'][0]['text']='changed'
        with self.assertRaisesRegex(ValueError,'dependencies changed'):A.inspect(self.r,{'id':s['snapshot']['id']})

    def test_changed_plan_fails_reopen(self):
        s=self.snapshot();self.plan['preview']['excluded_records']=21
        with self.assertRaisesRegex(ValueError,'Parent source plan changed'):A.inspect(self.r,{'id':s['snapshot']['id']})

    def test_database_update_delete_disallowed(self):
        s=self.snapshot()
        for table in ('attachments','snapshots'):
            with A.database(self.r.path) as db:
                for sql in ('DELETE FROM '+table,'UPDATE '+table+" SET payload='{}'"):
                    with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)

    def test_exact_api_fields_and_id_required(self):
        for body in ({'plan_id':'../x','answer_id':self.ids[0]}, {'plan_id':self.plan['id'],'answer_id':self.ids[0],'answer':{}}):
            with self.assertRaises(ValueError):A.attach(self.r,body)

    def test_global_reference_manifest_no_group_alias_collision(self):
        result=self.snapshot();row,parent,aliases,claims=R.assemble(result)
        self.assertEqual([p['pass_number'] for p in parent['passes']],[1,2])
        refs=R.R.manifest(parent,row)
        self.assertEqual(refs['p1:u1']['record_id'],'record1');self.assertEqual(refs['p2:u1']['record_id'],'record2')
        self.assertEqual([c['input_id'] for c in claims],['g1_p1_f1','g2_p1_f1'])
        self.assertEqual([a['original_pass'] for a in aliases],[1,1])
        self.assertEqual(parent['passes'][1]['result']['findings'][0]['text'],'p1:u1 original claim')
        self.assertEqual(self.answers[self.ids[1]]['passes'][0]['result']['findings'][0]['id'],'p1_f1')

    def test_assemble_rejects_source_overlap(self):
        result=self.snapshot();result['contexts'][1]['model_context']['sources']=result['contexts'][0]['model_context']['sources']
        with self.assertRaisesRegex(ValueError,'overlap'):R.assemble(result)

    def test_assemble_rejects_different_snapshots(self):
        result=self.snapshot();result['contexts'][1]['model_context']['snapshot']['as_of']='2030-01-01'
        with self.assertRaisesRegex(ValueError,'snapshots differ'):R.assemble(result)

    def test_reconciliation_requires_all_claims_and_valid_excerpts(self):
        result=self.snapshot();row,parent,aliases,claims=R.assemble(result);refs=R.R.manifest(parent,row)
        job=R.L.request(parent,row,refs,{'records':[1]*22})
        def raw(value):return {'model':job['body']['model'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(value)}]}
        value={'findings':[],'claim_reviews':[{'input_id':i,'status':'unassessed','finding_ids':[],'reason':'Insufficient context'} for i in job['finding_ids']],
            'unanswered':['Meaning unassessed'],'review_steps':[]}
        self.assertEqual(len(R.L.parse(raw(value),job,refs)['claim_reviews']),2)
        value['claim_reviews'].pop()
        with self.assertRaisesRegex(ValueError,'Every original claim'):R.L.parse(raw(value),job,refs)
        value['findings']=[{'id':'f1','kind':'documented_account','text':'claim','excerpt_ids':['unknown'],'unresolved':[]}]
        with self.assertRaisesRegex(ValueError,'Unknown or duplicate excerpt'):R.L.parse(raw(value),job,refs)

    def test_uncited_unit_alias_cannot_be_used_in_generated_prose(self):
        self.answers[self.ids[1]]['passes'][0]['result']['findings']=[]
        s=self.snapshot()
        with patch.object(R.P,'dependencies',return_value={'edges':[],'unresolved_references':[]}),patch.object(R.V,'snapshot',return_value={'records':[1]*22}):
            _,refs,job,claims,key=R.prepare(self.r,{'snapshot_id':s['snapshot']['id']})
        self.assertIn('p2:u1',refs);self.assertNotIn('p2:u1',job['units'])
        value={'findings':[],'claim_reviews':[{'input_id':claims[0]['input_id'],'status':'unassessed','finding_ids':[],'reason':'Unknown'}],
            'unanswered':['p2:u1 has unsupplied information'],'review_steps':[]}
        raw={'model':job['body']['model'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(value)}]}
        with self.assertRaisesRegex(ValueError,'not supplied'):R.L.parse(raw,job,refs)


class Codec(unittest.TestCase):
    def body(self):
        wire={'dependencies':{'columns':['from','to','field','scope'],'rows':[['a','b','x','in_scope']]*30,
              'unresolved_references':[{'id':'x','value':None},{'id':'x'}],'record_passes':{'a':[1]},'meaning':'documentary'},
              'received_source_availability':{'records':{'columns':['id','kind'],'rows':[['a','same']]*30},
                'typed_references':[{'a':'same','b':'value'}]*30,'unresolved_typed_references':[{'id':'x','value':None},{'id':'x'}]},
              'original_source_excerpts':{'rows':[['p1:u1','unchanged']]}}
        return {'messages':[{'role':'user','content':json.dumps(wire)}],'system':'instructions'}

    def test_reconciliation_round_trip_exact_and_missing_distinct_from_null(self):
        body=self.body();original=deepcopy(body);compressed=T.compact(body)
        restored=T.restore(json.loads(compressed['messages'][0]['content']))
        self.assertEqual(restored,json.loads(body['messages'][0]['content']));self.assertEqual(body,original)
        self.assertNotEqual(restored['dependencies']['unresolved_references'][0],restored['dependencies']['unresolved_references'][1])

    def test_wrong_codec_marker_rejected(self):
        wire=json.loads(T.compact(self.body())['messages'][0]['content']);wire['reference_table_transport']['schema']='other'
        with self.assertRaises(ValueError):T.restore(wire)

    def test_original_list_codec_output_preserved(self):
        body=self.body();wire=json.loads(body['messages'][0]['content']);wire['dependencies']=[{'from':'a','to':'b'}];body['messages'][0]['content']=json.dumps(wire)
        expected=F.compact_body(body);T.install();T.install();self.assertEqual(F.compact_body(body),expected)
        self.assertEqual(T.restore(json.loads(F.compact_body(self.body())['messages'][0]['content'])),json.loads(self.body()['messages'][0]['content']))


if __name__=='__main__':unittest.main()
