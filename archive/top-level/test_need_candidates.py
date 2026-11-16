"""Local candidate discovery is not an exact need binding or care-gap finding."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import test_record_evidence as fixture
from test_warehouse_interpretation import record
from clinical_core.contracts import digest
from clinical_core.agere.clinical import need_candidates as c


class Candidates(unittest.TestCase):
    setUpBase=fixture.RecordContext.setUpBase
    put=fixture.RecordContext.put
    chain=fixture.RecordContext.chain
    create=fixture.RecordContext.create

    def setUp(self):
        fixture.RecordContext.setUp(self)
        self.context=self.create(['extra'])
        self.answer={'id':'a'*64,'patient_id':'P-1','context_id':self.context['id'],'findings':[],
            'review_steps':[{'id':'need1','finding_ids':[],'question':'Which sources document outside care?'}]}
        p=patch.object(c.source_synthesis,'inspect',side_effect=lambda *_:copy.deepcopy(self.answer));p.start();self.addCleanup(p.stop)
        self.need_receipt=c.needs.prepare(self.path,self.answer['id'])
        self.request={'receipt_id':self.need_receipt['id'],'need_id':'need1','query':'Outside care follow-through'}
        self.rows={r['record_id']:r for r in c.record_context.received(self.path,'P-1')[0]}
        self.retriever=SimpleNamespace(meta={'source_digest':'fixture'},encoder=SimpleNamespace(identity='encoder'),reranker=None,
            search=Mock(return_value={'evidence':[self.hit('a'),self.hit('extra')],'method':'fixture retrieval','candidate_passages':6,'cache_hit':False,'elapsed_seconds':.1}))

    def hit(self,key):
        row=self.rows[key];rep=next(c.retrieval.representations(row))
        return {'text':rep['text'],'source':{'record_id':key,'patient_id':'P-1','record_hash':digest(row),
            'field':rep['field'],'start':0,'end':len(rep['text'])}}

    def prepare(self):return c.prepare(self.path,self.request,self.retriever)

    def test_search_bounds_cache_and_original_scope_are_explicit(self):
        r=self.prepare();self.assertEqual(r['summary']['outside_original_scope'],1);self.assertEqual(r['summary']['already_in_original_scope'],1)
        self.assertIsNone(r['context_id']);self.assertFalse(r['need_binding_changed']);self.assertEqual(r['semantic_resolution'],'pending')
        self.retriever.search.assert_called_once_with(self.request['query'],patient='P-1',limit=8)
        again=self.prepare();self.assertTrue(again['cache_hit']);self.assertEqual(r['id'],again['id']);self.assertEqual(self.retriever.search.call_count,1)
        self.assertEqual(c.needs.inspect(self.path,self.need_receipt['id']),self.need_receipt)

    def test_selection_preserves_whole_reference_closure_and_unbound_need(self):
        r=self.prepare();s=c.select(self.path,{'candidate_id':r['id'],'record_ids':['a']})
        self.assertEqual(s['record_scope']['record_ids'],['a','b','c','d']);self.assertFalse(s['meaning_resolved']);self.assertFalse(s['need_binding_changed'])
        self.assertEqual(c.inspect_selection(self.path,s['id']),s)
        self.assertFalse(c.needs.inspect(self.path,self.need_receipt['id'])['needs'][0]['selectors'])
        self.assertEqual(s,c.select(self.path,{'candidate_id':r['id'],'record_ids':['a']}))

    def test_foreign_stale_or_wrong_passage_rejected(self):
        for field,value in [('patient_id','P-2'),('record_hash','changed'),('end',999999)]:
            bad=self.hit('a');bad['source'][field]=value;self.retriever.search.return_value['evidence']=[bad]
            with self.assertRaises(ValueError):self.prepare()
        bad=self.hit('a');bad['text']='Invented evidence';self.retriever.search.return_value['evidence']=[bad]
        with self.assertRaisesRegex(ValueError,'span'):self.prepare()

    def test_no_hits_does_not_establish_absent_care(self):
        self.retriever.search.return_value['evidence']=[];r=self.prepare()
        self.assertEqual(r['status'],'no_candidates_returned');self.assertFalse(r['retrieval_recall_assessed']);self.assertEqual(r['summary']['received_records_not_returned'],6)
        with self.assertRaises(ValueError):c.select(self.path,{'candidate_id':r['id'],'record_ids':[]})

    def test_selection_cannot_substitute_or_duplicate_records(self):
        r=self.prepare()
        for ids in [['other'],['a','a'],[],['unknown'],False]:
            with self.assertRaises(ValueError):c.select(self.path,{'candidate_id':r['id'],'record_ids':ids})

    def test_changed_sources_invalidate_search_and_selection(self):
        r=self.prepare();s=c.select(self.path,{'candidate_id':r['id'],'record_ids':['a']})
        self.put(record('other','financial_screens',difficulty_paying_medical_bills=True))
        for action in [lambda:c.inspect(self.path,r['id']),lambda:c.inspect_selection(self.path,s['id']),self.prepare]:
            with self.assertRaises(ValueError):action()

    def test_tampered_receipt_and_unknown_need_rejected(self):
        with self.assertRaisesRegex(ValueError,'belonging'):c.prepare(self.path,{**self.request,'need_id':'unknown'},self.retriever)
        r=self.prepare();target=c.location(self.path,r['id']);saved=json.loads(target.read_text());saved['candidates'][0]['record_id']='other';target.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError,'integrity'):c.inspect(self.path,r['id'])

    def test_query_refinement_keeps_original_question_and_changes_cache(self):
        first=self.prepare();self.request['query']='Financial assistance account balance';second=self.prepare()
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(first['original_question'],second['original_question'])
        self.retriever.encoder.identity='new-encoder';third=self.prepare();self.assertNotEqual(second['id'],third['id'])

    def test_source_change_during_retrieval_cannot_save_candidate(self):
        def changed(*args,**kwargs):
            self.put(record('other','financial_screens',difficulty_paying_medical_bills=True));return self.retriever.search.return_value
        self.retriever.search.side_effect=changed
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse((self.path/'need-candidates').exists())

    def test_oversized_passage_count_and_invalid_query_rejected(self):
        for value in ['',False,'x'*1501]:
            with self.assertRaises(ValueError):c.prepare(self.path,{**self.request,'query':value},self.retriever)
        self.retriever.search.return_value['evidence']=[self.hit('a')]*9
        with self.assertRaisesRegex(ValueError,'bound'):self.prepare()


if __name__=='__main__':unittest.main()
