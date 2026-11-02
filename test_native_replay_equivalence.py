"""Typed operational provenance cannot mask changed source or business evidence."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_core.agere.clinical import harness_improvement as H
from clinical_core.agere.clinical.backstop_agent import output_table, define_result
from clinical_core.agere.clinical.sql_query import native_result_source, portable_native_query


def review(seeds=0):
    scope={'schema':'focused-document-retrieval/1','prior_source_seeds':{'reason':seeds},
           'queries':{'reason':'Why was treatment withheld?'},'sources':[{'record_id':'R1','date':'2030-01-01','digest':'abc'}],
           'field_applicability':{'reason':True},'selected_documents':1,'inventory_documents':2,
           'complete_chart_review':False,'absence_established':False}
    row={'patient_id':'A','review_json':json.dumps({'reason':[{'value':'documented reason','quote':'quoted source','record_id':'R1','date':'2030-01-01'}]}),
         'evidence_scope_json':json.dumps(scope),'field_patient_scope_json':json.dumps({'reason':{'applicable':True}}),
         'accounting':{'elapsed_s':12,'cache_hit':True},'elapsed_s':7}
    return {'columns':list(row),'rows':[row],'coverage':[{'patient_id':'A','document_digest':'abc','source_selection':scope}],
            'source_scope':'focused','result_view':'agere_result_1','accounting':{'new_paid_api_calls':1 if seeds==0 else 0}}


def projection(source,query='SELECT patient_id, review_json FROM agere_result_1'):
    raw=native_result_source(*output_table(source))
    db=sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TEMP VIEW agere_result_1 AS '+raw)
        cursor=db.execute(query);columns=[c[0] for c in cursor.description];rows=[dict(zip(columns,row)) for row in cursor.fetchall()]
    finally:db.close()
    return {'columns':columns,'rows':rows,'read_views':['agere_result_1'],
            'declared_executed_query':query,'executed_query':portable_native_query(query,{'agere_result_1':raw},['agere_result_1']),
            'source_contracts':{'as_of':'2030-05-27','source_digest':'v1'},'result_view':'agere_result_2','elapsed_s':.1}


class NativeEquivalence(unittest.TestCase):
    def test_same_findings_and_exact_portable_binding_ignore_only_typed_seed_counts(self):
        old,new=review(),review(3);raw=copy.deepcopy((old,new))
        self.assertEqual(H.semantic_result(old),H.semantic_result(new))
        a,b=projection(old),projection(new)
        self.assertEqual(a['rows'],b['rows']);self.assertNotEqual(a['executed_query'],b['executed_query'])
        self.assertEqual(H.comparison_result('query_sql',a,[{'result':old}]),H.comparison_result('query_sql',b,[{'result':new}]))
        self.assertEqual((old,new),raw)

    def test_patient_date_source_field_coverage_and_clinical_mutations_remain_visible(self):
        original=review()
        mutations={
            'patient':lambda v:v['rows'][0].update(patient_id='B'),
            'cohort':lambda v:v['rows'].append(copy.deepcopy(v['rows'][0])),
            'finding':lambda v:v['rows'][0].update(review_json='{}'),
            'field_scope':lambda v:v['rows'][0].update(field_patient_scope_json='{}'),
            'source_digest':lambda v:v['coverage'][0].update(document_digest='changed'),
            'coverage_patient':lambda v:v['coverage'][0].update(patient_id='B'),
            'document_date':lambda v:v['coverage'][0]['source_selection']['sources'][0].update(date='2031-01-01'),
            'selected_source':lambda v:v['coverage'][0]['source_selection']['sources'][0].update(record_id='R2'),
            'question':lambda v:v['coverage'][0]['source_selection']['queries'].update(reason='A different relation'),
            'absence':lambda v:v['coverage'][0]['source_selection'].update(absence_established=True),
            'coverage':lambda v:v['coverage'][0]['source_selection'].update(complete_chart_review=True),
            'applicability':lambda v:v['coverage'][0]['source_selection']['field_applicability'].update(reason=False),
            'business_elapsed':lambda v:v['rows'][0].update(elapsed_s=8),
            'business_nested_cache_hit':lambda v:v['rows'][0]['accounting'].update(cache_hit=False),
        }
        for name,change in mutations.items():
            with self.subTest(name=name):
                changed=copy.deepcopy(original);change(changed)
                self.assertNotEqual(H.semantic_result(original),H.semantic_result(changed))
        for field,value in [('as_of','2031-05-27'),('source_digest','v2')]:
            a=projection(original);b=copy.deepcopy(a);b['source_contracts'][field]=value
            self.assertNotEqual(H.comparison_result('query_sql',a,[{'result':original}]),H.comparison_result('query_sql',b,[{'result':original}]))

    def test_arbitrary_json_business_fields_and_untyped_counts_are_not_normalized(self):
        for mutate in [lambda v:v.update(source_scope='all'),
                       lambda v:v['coverage'][0]['source_selection'].update(schema='business/1')]:
            a,b=review(),review(3);mutate(a);mutate(b)
            self.assertNotEqual(H.semantic_result(a),H.semantic_result(b))
        a={'columns':['evidence_scope_json'],'rows':[{'evidence_scope_json':review()['rows'][0]['evidence_scope_json']}]}
        b=copy.deepcopy(a);b['rows'][0]['evidence_scope_json']=review(3)['rows'][0]['evidence_scope_json']
        self.assertNotEqual(H.semantic_result(a),H.semantic_result(b))
        a,b=review(),review();b['coverage'][0]['source_selection']['prior_source_seeds']['reason']='untrusted'
        self.assertNotEqual(H.semantic_result(a),H.semantic_result(b))

    def test_portable_sql_cannot_hide_changed_literal_binding_or_missing_source(self):
        source=review();a=projection(source);expected=H.comparison_result('query_sql',a,[{'result':source}])
        for key,value in [('executed_query',a['executed_query']+' WHERE 1=1'),
                          ('declared_executed_query','SELECT patient_id, review_json FROM agere_result_1 WHERE 1=1')]:
            b=copy.deepcopy(a);b[key]=value
            self.assertNotEqual(expected,H.comparison_result('query_sql',b,[{'result':source}]))
        self.assertEqual(H.comparison_result('query_sql',a,[]),H.semantic_result(a))
        self.assertEqual(H.comparison_result('read_document',a,[{'result':source}]),H.semantic_result(a))
        changed=copy.deepcopy(source);changed['rows'][0]['patient_id']='B'
        self.assertNotEqual(expected,H.comparison_result('query_sql',projection(changed),[{'result':changed}]))

    def test_native_sql_renderer_preserves_quoted_text_columns_and_cte_shadowing(self):
        source=review();source['rows'][0]['review_json']="O'Brien's source";source['columns'].append('a"b');source['rows'][0]['a"b']='value'
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        observed=[];semantic=SimpleNamespace(db=db,_authorize=lambda *a:sqlite3.SQLITE_OK,register_native_result=lambda *a:observed.append(a))
        sql=define_result(semantic,'agere_result_1',*output_table(source),receipt=source)
        self.assertEqual(sql,native_result_source(*output_table(source)))
        self.assertEqual(db.execute('SELECT review_json FROM agere_result_1').fetchone()[0],"O'Brien's source")
        query='WITH agere_result_1 AS (SELECT 1 AS unused) SELECT patient_id FROM temp.agere_result_1'
        a=projection(source,query);new=review(3);new['rows'][0].update(source['rows'][0]);new['rows'][0]['evidence_scope_json']=review(3)['rows'][0]['evidence_scope_json'];new['columns']=source['columns']
        b=projection(new,query)
        self.assertEqual(H.comparison_result('query_sql',a,[{'result':source}]),H.comparison_result('query_sql',b,[{'result':new}]))

    def test_actual_candidate_validation_replay_and_final_partial_rejection(self):
        with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{'AGERE_RECURSIVE_IMPROVEMENT':'on','AGERE_EVALUATION':'0'}):
            class Executor:
                def __init__(self,seeds):
                    self.stack=SimpleNamespace(path=Path(root));self.db=sqlite3.connect(':memory:');self.semantic=SimpleNamespace(close=lambda:None)
                    self.subtool_accounting={'new_paid_api_calls':0,'estimated_cost_usd':0};self.seeds=seeds;self.source='v1'
                def continuation_identity(self):return {'source':self.source,'as_of':'2030-05-27','scope':['A']}
                def __call__(self,use):
                    if use['name']=='review_documents':self.review=review(self.seeds);return self.review
                    return projection(self.review)
            original=Executor(0);self.addCleanup(original.db.close)
            calls=[]
            for name in ('review_documents','query_sql'):
                calls.append({'name':name,'input':{},'actor_callback_id':name,'result':original({'name':name})})
            fresh=lambda:Executor(3)
            candidate=H.propose(original,'question',{},'fixed',calls,fresh)
            self.assertEqual(candidate['status'],'validated_pending_complete_workflow')
            H.finish(Path(root),{'answer':{'status':'answered'},'workflow':[],'investigation':{'recursive_improvement':{'candidate':candidate}}})
            executor=fresh();self.addCleanup(executor.db.close)
            replay=H.replay(executor,'question',{},'fixed');self.assertEqual(len(replay['certificates']),2);self.assertEqual(replay['used'],candidate['id'])
            H.finish(Path(root),{'answer':{'status':'partial'},'workflow':[],'investigation':{'recursive_improvement':{'replay':replay}}})
            executor=fresh();self.addCleanup(executor.db.close)
            self.assertIsNone(H.replay(executor,'question',{},'fixed')['used'])


if __name__=='__main__':unittest.main()
