"""Source integrity, exception retention and honest budget/missingness behavior."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zlib

from clinical_core.contracts import digest
from clinical_core.agere.clinical import context_builder as ctx
from clinical_core.agere.clinical.analysis_graph import validate as validate_graph
from clinical_core.agere.clinical.economical_planner import local_plan
from clinical_core.agere.clinical.graph_workbench import context_examples

META={'snapshot':{'as_of':'2026-09-30'}}
PARAMS={'question':'What does the received evidence document?', 'streams':['notes'],
        'concepts':[], 'contract':'', 'program_ids':[], 'max_bytes':24000}


def record(text, rid='n', patient='p', **extra):
    return {'record_id':rid,'patient_id':patient,'source_table':'notes',
            'event_date':'2026-01-02','available_date':'2026-01-03','text':text,**extra}


class ContextTests(unittest.TestCase):
    def test_quoted_program_context_preserves_source_unknowns_review_and_budget(self):
        from clinical_core.agere.clinical import program_documents as documents
        from test_program_documents import package
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);meta={'synthetic':True,'snapshot':{'as_of':'2029-02-15'},'source_digest':'fixture'}
            (root/'run.json').write_text(json.dumps(meta))
            spec=package('2',extra=True);key=documents.install(root,spec)['id'];params={**PARAMS,'program_ids':[key]}
            def connection(_):
                db=sqlite3.connect(':memory:');db.execute('CREATE TABLE events(stream TEXT,concept TEXT)');return db,{}
            with patch.object(ctx.longitudinal,'connect',side_effect=connection),patch('clinical_core.agere.clinical.program_registry.packages',return_value=[]):
                initial,_,_,policies=ctx.dependency_identity(root,params)
                self.assertEqual(policies[0]['document'],spec['document']);self.assertEqual(policies[0]['bindings'],spec['bindings'])
                self.assertEqual(policies[0]['input_bindings']['attribution']['kind'],'unbound')
                self.assertEqual(policies[0]['semantic_review']['status'],'unreviewed')
                result=ctx.pack('p',params,ctx.units('p',[record('Source report.')],[],params,'2029-02-15'),meta,policies=policies)
                self.assertEqual(result['model_context']['versioned_program_context'],policies)
                self.assertFalse(result['dispatch']['clinical_decision_allowed'])
                documents.review(root,{'package':key,'expected_revision':0,'actor':'ISOLATED TEST FIXTURE','status':'changes_requested','note':'Fixture only'})
                changed,_,_,policies=ctx.dependency_identity(root,params);self.assertNotEqual(initial,changed)
                self.assertEqual(policies[0]['semantic_review']['status'],'changes_requested')
                late=package('late');late['provenance']['received_on']='2030-01-01';latekey=documents.install(root,late)['id']
                with self.assertRaisesRegex(ValueError,'not received'):ctx.dependency_identity(root,{**params,'program_ids':[latekey]})

    def test_declared_but_absent_source_scope_is_distinct_from_invented_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'run.json').write_text(json.dumps({'synthetic':True,'snapshot':META['snapshot'],'source_digest':'fixture'}))
            def connection(_):
                db=sqlite3.connect(':memory:');db.execute('CREATE TABLE events(stream TEXT,concept TEXT)')
                db.execute('INSERT INTO events VALUES (?,?)',('notes','financial hardship'))
                return db,{'vocabulary':{'stream':['notes'],'concept':['financial hardship']}}
            with patch.object(ctx.longitudinal,'connect',side_effect=connection),patch('clinical_core.agere.clinical.program_registry.packages',return_value=[]):
                result=ctx.dependency_identity(root,{**PARAMS,'streams':['notes','financial']})
                self.assertTrue(result[0])
                with self.assertRaisesRegex(ValueError,'Unmapped context source stream'):
                    ctx.dependency_identity(root,{**PARAMS,'streams':['invented_source']})

    def test_exact_offsets_include_unannotated_text_and_literal_newlines(self):
        text='old text\\n  Held pending outside results.\nUnmapped new evidence.'
        source=record(text)
        units=ctx.units('p',[source],[],PARAMS,META['snapshot']['as_of'])
        narrative=[r for r in units if r['kind']=='narrative']
        self.assertEqual(len(narrative),3)
        for r in narrative:
            self.assertEqual(text[r['start']:r['end']],r['text'])
        self.assertTrue(narrative[1]['protected_reasons'])
        self.assertIn('Unmapped new evidence.',[r['text'] for r in narrative])

    def test_hidden_original_ocr_text_never_enters_context(self):
        source=record('unused',original_text='SECRET',truth={'answer':'SECRET'},
                      source_table='documents',extracted_text='received OCR',nested={'clean_text':'SECRET'})
        units=ctx.units('p',[source],[],dict(PARAMS,streams=['documents']),META['snapshot']['as_of'])
        self.assertNotIn('SECRET',json.dumps(units))
        self.assertNotIn('unused',json.dumps(units))
        self.assertIn('received OCR',json.dumps(units))

    def test_cross_patient_and_late_arrival_rejected(self):
        for source in (record('x',patient='other'),record('x',available_date='2026-10-01')):
            with self.assertRaises(ValueError):ctx.units('p',[source],[],PARAMS,META['snapshot']['as_of'])

    def test_annotation_requires_exact_quote_and_record_version(self):
        r=record('Held medication.')
        event={'patient_id':'p','kind':'text','source':{'record_id':'n','field':'text','start':0,'end':4,'quote':'WRONG'},
               'record_hash':digest(r)}
        with self.assertRaises(ValueError):ctx.units('p',[r],[event],PARAMS,META['snapshot']['as_of'])

    def test_protected_oversize_unit_is_never_silently_truncated(self):
        source=record('Held pending reconciliation. '+'x'*8000)
        units=ctx.units('p',[source],[],PARAMS,META['snapshot']['as_of'])
        result=ctx.pack('p',dict(PARAMS,max_bytes=2048),units,META)
        self.assertEqual(result['status'],'protected_overflow')
        self.assertFalse(result['dispatch']['source_review_allowed'])
        self.assertEqual(result['budget']['omitted_protected_units'],1)
        self.assertLessEqual(result['budget']['used'],2048)
        self.assertEqual(result['omissions'][0]['end'],len(source['text']))

    def test_any_budget_omission_blocks_complete_case_dispatch(self):
        units=ctx.units('p',[record('x'*8000)],[],PARAMS,META['snapshot']['as_of'])
        result=ctx.pack('p',dict(PARAMS,max_bytes=2048),units,META)
        self.assertEqual(result['status'],'bounded_partial')
        self.assertFalse(result['dispatch']['source_review_allowed'])

    def test_oversize_rules_or_requirements_block_without_erasure(self):
        result=ctx.pack('p',dict(PARAMS,max_bytes=2048),[],META,policies=[{'source':'x'*5000}])
        self.assertIsNone(result['model_context'])
        self.assertEqual(result['status'],'protected_overflow')
        self.assertGreater(result['budget']['header_required'],2048)

    def test_absent_concepts_and_sources_are_not_clinical_negatives(self):
        p=dict(PARAMS,streams=['notes','referrals'],concepts=['outside care'])
        units=ctx.units('p',[record('Documents received.')],[],p,META['snapshot']['as_of'])
        result=ctx.pack('p',p,units,META)
        self.assertEqual(result['missing_evidence']['concepts_without_annotations'],['outside care'])
        self.assertEqual(result['missing_evidence']['sources_without_observed_records'],['referrals'])
        self.assertFalse(result['clinical_eligibility_established'])
        self.assertFalse(result['complete_chart_guarantee'])

    def test_opposing_reports_keep_both_polarities_and_historical_dates(self):
        units=ctx.units('p',[record('Affirmed report.',rid='a'),record('Denied report.',rid='b')],[],PARAMS,META['snapshot']['as_of'])
        narratives=[u for u in units if u['kind']=='narrative']
        for u,assertion in zip(narratives,['affirmed','negated']):
            u['annotations']=[{'concept':'outside care','subject':'patient','assertion':assertion,'reference_date':'2025-01-01','time':'historical'}]
        result=ctx.pack('p',PARAMS,units,META)
        self.assertEqual(result['opposing_report_concepts'],['outside care'])
        for u in result['model_context']['evidence']:
            if u['annotations']:
                self.assertIn('opposing_reports_over_time',u['protected_reasons'])
                self.assertEqual(u['annotations'][0]['reference_date'],'2025-01-01')
                self.assertEqual(result['model_context']['sources'][u['source_ref']]['source']['event_date'],'2026-01-02')

    def test_source_bytes_are_not_called_tokens(self):
        units=ctx.units('p',[record('患者の記録')],[],PARAMS,META['snapshot']['as_of'])
        result=ctx.pack('p',PARAMS,units,META)
        self.assertEqual(result['budget']['used'],len(ctx.encode(result['model_context'])))
        self.assertFalse(result['budget']['provider_token_budget_verified'])

    def test_all_new_presets_compile_locally_and_validate(self):
        for example in context_examples():
            validate_graph(example['graph'],'2026-09-30')
            self.assertIsNotNone(local_plan(example['question'],'2026-09-30'))

    def test_native_token_guard_and_source_version_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);db=sqlite3.connect(path/'stack.sqlite3')
            db.execute('CREATE TABLE records(id TEXT,patient TEXT,payload TEXT)')
            r=record('A source statement.')
            db.execute('INSERT INTO records VALUES (?,?,?)',('n','p',json.dumps(r)));db.commit()
            result=ctx.pack('p',PARAMS,ctx.units('p',[r],[],PARAMS,'2026-09-30'),META)
            tokenizer=type('Tokenizer',(),{'encode':lambda self,text:list(text.encode())})()
            with patch.object(ctx,'inspect',return_value=result):
                with self.assertRaisesRegex(ValueError,'Native model token'):ctx.for_model(path,'id',tokenizer,100,10)
                accepted=ctx.for_model(path,'id',tokenizer,10000,100)
                self.assertLess(accepted['context_tokens']+accepted['reserved_tokens'],10000)
                db.execute('UPDATE records SET payload=?',(json.dumps(record('Altered.')),));db.commit()
                with self.assertRaisesRegex(ValueError,'Source record changed'):ctx.for_model(path,'id',tokenizer,10000,100)
            db.close()


if __name__=='__main__':unittest.main()
