import copy
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import decision_planner as p, question_decision as q
from clinical_core.contracts import digest
import test_question_decision as fixtures


class Planner(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Decision(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.answer['findings'][0]['kind']='documented_account'
        self.f.answer['findings'][0]['citations'][0]['source']['source_table']='notes'
        self.f.context['model_context']['sources']['r1']['source']['source_table']='notes'
        self.db=sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE calls(id TEXT,status TEXT)')
        self.ledger=SimpleNamespace(db=self.db)
        self.rows=[{'finding_id':'f1','status':'mapped','reason':'Documented medication account for review.',
            'links':[{'capability_id':'pharmacy','need_tag':'medication_review','citation_ids':['0'],'reason':'Review the cited account; current use is unknown.'}]}]
        self.wire=p.inputs(self.f.answer,self.f.context,self.f.binding,self.f.fixture.port)

    def raw(self,rows):return {'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps({'dispositions':rows})}]}
    def prepare(self):
        return q.prepare(self.f.retriever,{**self.f.req,'planner':p.METHOD},ledger=self.ledger,allow_live=True)

    def mocks(self):
        return patch.object(p,'send',side_effect=lambda *a,**kw:(kw['parse'](self.raw(self.rows)),{'cache_hit':False,'request_hash':'fake'}))

    def test_candidate_connects_unmapped_narrative_to_same_decision_task_and_cache(self):
        with self.mocks() as send,patch.object(p.case_synthesis,'token_check',return_value={'input_tokens':50}):
            row=self.prepare(); self.assertEqual(row,self.prepare()); self.assertEqual(send.call_count,1)
        self.assertEqual(row['baseline_comparison']['baseline_mapped_findings'],0)
        self.assertEqual(row['baseline_comparison']['candidate_mapped_findings'],1)
        self.assertEqual(row['planning']['unresolved'],self.f.answer['unanswered'])
        task=self.f.queue(row); self.assertEqual(task['owner']['id'],'fixture-pharmd')
        self.assertTrue(q.reopen(self.f.retriever,row['id'])['dependencies_current'])
        self.assertFalse(row['planning']['clinical_indication_established'])
        self.assertEqual(row['planning']['review_topics'][0]['support'][0]['citations'][0]['quote'],'Review needed')

    def test_complete_disposition_accounting(self):
        for rows in [[],self.rows*2,[{**self.rows[0],'finding_id':'invented'}]]:
            with self.assertRaises(ValueError):p.parse(self.raw(rows),self.wire)

    def test_invented_capabilities_tags_and_citations_rejected(self):
        for field,value in [('capability_id','other'),('need_tag','care_access'),('citation_ids',['wrong']),('citation_ids',[])]:
            rows=copy.deepcopy(self.rows);rows[0]['links'][0][field]=value
            with self.assertRaises(ValueError):p.parse(self.raw(rows),self.wire)

    def test_abstention_explicit_no_links(self):
        rows=[{**self.rows[0],'status':'abstain','links':[]}]
        self.assertEqual(p.parse(self.raw(rows),self.wire),rows)
        with self.assertRaises(ValueError):p.parse(self.raw([{**self.rows[0],'status':'abstain'}]),self.wire)

    def test_cross_patient_and_changed_quote_source_rejected(self):
        self.f.answer['findings'][0]['citations'][0]['source']['patient_id']='other'
        with self.assertRaises(ValueError):p.inputs(self.f.answer,self.f.context,self.f.binding,self.f.fixture.port)

    def test_bounded_complete_groups_no_truncation(self):
        wire=copy.deepcopy(self.wire);base=wire['findings'][0]
        wire['findings']=[{**base,'id':str(i),'text':'a'*4000} for i in range(5)]
        groups=p.batches(wire)
        self.assertGreater(len(groups),1)
        self.assertEqual([f for g in groups for f in g['findings']],wire['findings'])
        self.assertTrue(all(len(json.dumps(p.body(g)).encode())<=19500 for g in groups))
        wire['findings'][0]['text']='x'*25000
        with self.assertRaises(ValueError):p.batches(wire)

    def test_failed_request_cannot_retry(self):
        wire=p.inputs(self.f.answer,self.f.context,q.source_binding(self.f.retriever,self.f.answer['id'])[2],self.f.fixture.port)
        key=digest({'contract':'context-student/1','provider':'claude','body':p.body(p.batches(wire)[0])})
        self.db.execute('INSERT INTO calls VALUES (?,?)',(key,'failed'))
        with patch.object(p,'send') as send,patch.object(p.case_synthesis,'token_check') as count:
            with self.assertRaisesRegex(ValueError,'retry is prohibited'):self.prepare()
            send.assert_not_called();count.assert_not_called()

    def test_no_live_authority_does_not_dispatch(self):
        with patch.object(p,'send') as send:
            with self.assertRaisesRegex(ValueError,'Live planner route disabled'):
                q.prepare(self.f.retriever,{**self.f.req,'planner':p.METHOD},ledger=self.ledger)
            send.assert_not_called()

    def test_changed_buyer_and_candidate_rules_stale_but_baseline_is_stable(self):
        with self.mocks(),patch.object(p.case_synthesis,'token_check',return_value={}):row=self.prepare()
        with patch.object(p,'identity',return_value='changed'):
            self.assertFalse(q.reopen(self.f.retriever,row['id'])['dependencies_current'])
        self.f.fixture.cap['description']='New scope'; self.f.fixture.update()
        self.assertFalse(q.reopen(self.f.retriever,row['id'])['dependencies_current'])
        self.assertEqual(q.identity(),'4dab10a6b7d7db6e72bcc5b7b02babc14a30495f697b7173e4e5ddd03374147f')

    def test_cache_integrity_required(self):
        with self.mocks(),patch.object(p.case_synthesis,'token_check',return_value={}):self.prepare()
        target=next((self.f.path/'decision-planning').glob('*.json'))
        row=json.loads(target.read_text());row['unresolved']=[];target.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError,'integrity'):self.prepare()

    def test_baseline_rule_map_change_invalidates_identity(self):
        before=q.identity()
        with patch.dict(q.REVIEW_TOPICS,{'other_topic':['notes']}):self.assertNotEqual(q.identity(),before)


if __name__=='__main__':unittest.main()
