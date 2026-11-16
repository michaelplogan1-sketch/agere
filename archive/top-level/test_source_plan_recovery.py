import json
import sqlite3
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import test_source_plan_answers as fixtures
from clinical_core.agere.clinical import source_plan_recovery as Q


class Recovery(unittest.TestCase):
    setUp=fixtures.Answers.setUp
    attach=fixtures.Answers.attach
    snapshot=fixtures.Answers.snapshot

    def fixture(self):
        snap=self.snapshot();request={'snapshot_id':snap['snapshot']['id']}
        with patch.object(Q.R.P,'dependencies',return_value={'edges':[],'unresolved_references':[]}),patch.object(Q.R.V,'snapshot',return_value={'records':[1]*22}):
            prepared=Q.R.prepare(self.r,request)
        result,refs,job,claims,key=prepared
        value={'findings':[{'id':'f1','kind':'documented_account','text':'One documented account','excerpt_ids':['e1'],'unresolved':[]}],
            'claim_reviews':[{'input_id':claims[0]['input_id'],'status':'qualified_by_supplied_excerpts','finding_ids':['f1'],'reason':'Related to u1'},
                {'input_id':claims[1]['input_id'],'status':'unassessed','finding_ids':[],'reason':'Insufficient excerpts'}],
            'unanswered':['Meaning remains unresolved'],'review_steps':[]}
        raw={'model':job['body']['model'],'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(value)}]}
        return request,prepared,value,raw

    def raw(self,old,value):return {**old,'content':[{'type':'text','text':json.dumps(value)}]}

    def test_quarantine_preserves_response_findings_and_other_reviews(self):
        _,(_,refs,job,*_),value,raw=self.fixture();before=deepcopy(raw)
        parsed,quarantined=Q.parsed_recovery(raw,job,refs)
        self.assertEqual(raw,before);self.assertEqual(len(quarantined),1)
        self.assertEqual(parsed['claim_reviews'][0]['status'],'unassessed');self.assertEqual(parsed['claim_reviews'][0]['finding_ids'],[])
        self.assertEqual(parsed['claim_reviews'][1],value['claim_reviews'][1])
        self.assertEqual(parsed['findings'][0]['text'],value['findings'][0]['text'])
        self.assertEqual(parsed['findings'][0]['citations'][0]['source']['record_id'],'record1')
        self.assertEqual(quarantined[0]['original_review'],value['claim_reviews'][0])

    def test_missing_or_duplicate_claim_cannot_be_recovered(self):
        _,(_,refs,job,*_),value,raw=self.fixture()
        for reviews in ([value['claim_reviews'][0]],[value['claim_reviews'][0]]*2):
            with self.assertRaises(ValueError):Q.parsed_recovery(self.raw(raw,{**value,'claim_reviews':reviews}),job,refs)

    def test_invalid_finding_cannot_be_recovered(self):
        _,(_,refs,job,*_),value,raw=self.fixture();value['findings'][0]['excerpt_ids']=['invented']
        with self.assertRaisesRegex(ValueError,'Unknown or duplicate excerpt'):Q.parsed_recovery(self.raw(raw,value),job,refs)

    def test_incomplete_provider_output_cannot_be_recovered(self):
        _,(_,refs,job,*_),_,raw=self.fixture()
        with self.assertRaisesRegex(ValueError,'Incomplete synthesis'):Q.parsed_recovery({**raw,'stop_reason':'max_tokens'},job,refs)

    def test_no_ambiguity_requires_original_route(self):
        _,(_,refs,job,*_),value,raw=self.fixture();value['claim_reviews'][0]['reason']='Related to p1:u1'
        with self.assertRaisesRegex(ValueError,'No supported'):Q.parsed_recovery(self.raw(raw,value),job,refs)

    def test_local_recovery_exact_failure_only_and_no_inference(self):
        request,prepared,value,raw=self.fixture();_,_,job,_,failed_id=prepared
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.execute('CREATE TABLE calls(id TEXT,status TEXT,error TEXT)')
        ledger=SimpleNamespace(db=db);key=Q.R.G.request_key(job['body'])
        with patch.object(Q.R,'prepare',return_value=prepared),patch.object(Q.R.G,'send') as send:
            with self.assertRaisesRegex(ValueError,'Exact saved'):Q.recover(self.r,request,ledger)
            db.execute('INSERT INTO calls VALUES (?,?,?)',(key,'failed','Ambiguous claim-review unit reference'))
            path=Q.R.location(self.r.path,failed_id).parent/'provider'/(key+'.json');path.parent.mkdir(parents=True)
            raw['usage']={'input_tokens':100,'output_tokens':100};path.write_text(json.dumps({'status':'received_pending_validation','response':raw}))
            saved=Q.recover(self.r,request,ledger);self.assertEqual(saved['coverage']['system_unassessed_claim_reviews'],1)
            self.assertEqual(saved['new_paid_api_calls'],0);self.assertEqual(saved['failed_requests_resent'],0)
            self.assertTrue(Q.recover(self.r,request,ledger)['cache_hit']);send.assert_not_called()
            self.assertEqual(db.execute('SELECT status FROM calls').fetchone()[0],'failed')
            changed=deepcopy(raw);changed['usage']['input_tokens']=200;path.write_text(json.dumps({'status':'received_pending_validation','response':changed}))
            with self.assertRaisesRegex(ValueError,'dependencies changed'):Q.inspect(self.r,{'id':saved['id']},ledger)


if __name__=='__main__':unittest.main()
