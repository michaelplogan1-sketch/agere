"""Repair retained presentation work without treating a machine critic as truth."""
import copy, importlib, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from clinical_core.agere.deliverables.receipts import Bundle, fingerprint
C = importlib.import_module('clinical_core.agere.deliverables.compose')

class RejectedDraft(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ws = SimpleNamespace(dir=Path(self.tmp.name), slug='authored')
        self.b = Bundle({'workspace':'authored','as_of':'2031-02-01','synthetic':True,
                         'scope':'exact received evidence','deliverable_purpose':'proposal','frozen_evidence':True})
        self.rid = self.b.add('saved_answer', {'context_id':'authored'},
                             {'answer':{'status':'partial','visits':17,'missing':['Eligibility unknown']}})
        self.ident = 'DLV-ABCDEF123456'
        self.body = '<h1>Existing useful design</h1><p>Eligibility unknown</p>'
        self.spec = {'id':self.ident,'status':'rejected','request':'Design an access service\nOld retry',
                     'verification':{'passed':False,'violations':[{'kind':'semantic_review','detail':{
                         'coverage':'incomplete','support':'supported','issues':[{'requirement':'Evidence scope','reason':'Label the historical observation'}]}}]}}
        C._save(self.ws,self.ident,doc=None,body=self.body,bundle=self.b,spec=self.spec)

    def test_same_received_evidence_and_brief_supply_draft_and_actual_criticism(self):
        before={p.name:p.read_bytes() for p in (Path(self.ws.dir)/'deliverables'/self.ident).iterdir()}
        out=C._retry_candidate(self.ws,self.b,'Design an access service\nCurrent retry')
        self.assertEqual(out['artifact'],self.ident);self.assertEqual(out['body'],self.body)
        self.assertEqual(out['violations'],self.spec['verification']['violations'])
        self.assertEqual(out['evidence_fingerprint'],fingerprint(self.b.results))
        self.assertEqual(before,{p.name:p.read_bytes() for p in (Path(self.ws.dir)/'deliverables'/self.ident).iterdir()})

    def test_data_receipt_scope_stage_and_brief_changes_prevent_reuse(self):
        for kind in ('data','receipt','scope','purpose','clock','nonfrozen','brief'):
            with self.subTest(kind=kind):
                b=Bundle.from_json(copy.deepcopy(self.b.to_json()));request='Design an access service'
                if kind=='data':b.results[self.rid]['answer']['visits']=23
                if kind=='receipt':b.receipts[self.rid]['source']='changed'
                if kind=='scope':b.meta['scope']='other population'
                if kind=='purpose':b.meta['deliverable_purpose']='final'
                if kind=='clock':b.meta['as_of']='2032-02-01'
                if kind=='nonfrozen':b.meta['frozen_evidence']=False
                if kind=='brief':request='Design a different service'
                self.assertIsNone(C._retry_candidate(self.ws,b,request))

    def test_timestamp_only_receipt_change_does_not_erase_matching_native_work(self):
        b=Bundle.from_json(copy.deepcopy(self.b.to_json()));b.receipts[self.rid]['computed_at']='later'
        self.assertIsNotNone(C._retry_candidate(self.ws,b,'Design an access service'))

    def test_delivered_or_dependency_failed_artifacts_never_supply_rejected_drafts(self):
        for status,dependency in [('delivered',False),('rejected',True)]:
            with self.subTest(status=status,dependency=dependency):
                spec=copy.deepcopy(self.spec);spec['status']=status
                if dependency:spec['verification']['dependency_failure']='Source changed'
                C._save(self.ws,self.ident,doc=None,body=self.body,bundle=self.b,spec=spec)
                self.assertIsNone(C._retry_candidate(self.ws,self.b,'Design an access service'))

    def test_other_workspace_and_missing_body_never_supply_a_draft(self):
        other=SimpleNamespace(dir=Path(self.tmp.name)/'other',slug='other')
        self.assertIsNone(C._retry_candidate(other,self.b,'Design an access service'))
        (Path(self.ws.dir)/'deliverables'/self.ident/'body.html').unlink()
        self.assertIsNone(C._retry_candidate(self.ws,self.b,'Design an access service'))

if __name__=='__main__':unittest.main()
