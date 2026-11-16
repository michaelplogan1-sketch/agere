import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from clinical_core.contracts import digest
from clinical_core.agere.clinical import cohort_span_acquisition as a
from clinical_core.agere.gateway import BudgetLedger
from test_learning_cohorts import CohortTests


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=CohortTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.stack=self.fixture.stack;self.key=self.fixture.key
        self.plan=a.prepare(self.stack,self.key);self.session=a.Session(self.stack,self.key)
        self.item=next(r for r in self.plan['inventory'] if r['patient_id']=='b')['id']

    def raw(self,**changes):
        result={'scope_reason':'Reported occurrence requires source interpretation.',
            'proposals':[{'quote':'visit happened','occurrence':1,'concept':'reported visit completion','reason':'Retain the complete reported account.'}],
            'unresolved':['Event timing is not assessed.']};result.update(changes)
        return {'model':a.gateway.PersistentGateway.MODELS['claude'],'stop_reason':'end_turn',
                'content':[{'type':'text','text':json.dumps(result)}]}

    def propose(self,raw=None,mutate=None):
        def send(body,provider,ledger,out,parse):
            parsed=parse(raw or self.raw())
            if mutate:mutate()
            return parsed,{'cache_hit':False,'request_hash':'fixture'}
        ledger=BudgetLedger(self.fixture.root/'budget.sqlite3')
        try:
            with patch.object(a.gateway,'send',side_effect=send),patch.object(a.gateway,'token_check',return_value={'provider_count_cached':False,'input_tokens':10}):
                return self.session.propose(self.item,ledger,allow_live=True)
        finally:ledger.close()

    def test_preserves_cohort_and_proposes_exact_unicode_occurrence_without_labels(self):
        before=(self.fixture.output/'plan.json').read_bytes();r=self.propose()
        self.assertEqual(before,(self.fixture.output/'plan.json').read_bytes())
        self.assertFalse(r['abstained']);self.assertEqual(r['human_reviews_created'],0)
        selected=a.select(self.stack,{'cohort_id':self.key,'item_id':self.item,'proposal_id':r['proposals'][0]['id']})
        self.assertEqual(selected['target']['start'],self.fixture.fixture.sources['b']['text'].rindex('visit'))
        self.assertFalse(selected['qualification']['fit_eligible'])
        inspected=a.task(self.stack,self.key,selected['id']);self.assertEqual(inspected['readings'],[])
        self.assertEqual(inspected['partition'],r['partition'])
        ledger=BudgetLedger(self.fixture.root/'budget.sqlite3')
        try:
            with patch.object(a.gateway,'send') as send:
                self.assertTrue(self.session.propose(self.item,ledger)['cache_hit']);send.assert_not_called()
        finally:ledger.close()

    def test_wire_is_complete_and_hidden_content_and_split_are_not_provider_features(self):
        p=self.session.request(self.item);wire=p['wire']
        self.assertEqual(wire['complete_received_field'],self.fixture.fixture.sources['b']['text'])
        body=json.dumps(p['body'])
        for forbidden in ('HIDDEN CONTENT','patient_family','partition_counts','prior_unplanned_readings'):self.assertNotIn(forbidden,body)
        self.assertEqual(wire['source_availability']['received_same_patient_records'],1)

    def test_abstention_is_kept_in_denominator_without_target(self):
        self.propose(self.raw(proposals=[]));s=a.status(self.stack,self.key)
        self.assertEqual(s['counts']['abstained'],1);self.assertEqual(s['selected_tasks'],0)
        self.assertEqual(sum(s['counts'].values()),s['total'])

    def test_bad_quotes_occurrences_duplicate_targets_and_labels_reject(self):
        p=self.session.request(self.item);base=json.loads(self.raw()['content'][0]['text'])['proposals'][0]
        for change in ({'quote':'invented'}, {'occurrence':True},{'occurrence':-1},{'concept':''},{'labels':{}}):
            with self.assertRaises(ValueError):a.parse(p,self.raw(proposals=[{**base,**change}]))
        with self.assertRaises(ValueError):a.parse(p,self.raw(proposals=[base,base]))

    def test_pending_failed_request_not_retried(self):
        ledger=BudgetLedger(self.fixture.root/'budget.sqlite3')
        try:
            with patch.object(a.gateway,'token_check',side_effect=RuntimeError('offline')) as token:
                with self.assertRaises(RuntimeError):self.session.propose(self.item,ledger,allow_live=True)
                with self.assertRaisesRegex(ValueError,'no automatic'):self.session.propose(self.item,ledger,allow_live=True)
                self.assertEqual(token.call_count,1)
        finally:ledger.close()

    def test_source_mutation_during_call_keeps_failure_and_no_receipt(self):
        def mutate():
            db=sqlite3.connect(self.stack/'stack.sqlite3');r=self.fixture.fixture.sources['b'].copy();r['text']='Changed source'
            with db:db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),r['record_id']))
            db.close()
        with self.assertRaises(ValueError):self.propose(mutate=mutate)
        self.assertFalse((self.session.root/'receipts'/(self.item+'.json')).exists())
        self.assertTrue((self.session.root/'failures'/(self.item+'.json')).exists())

    def test_protection_change_prevents_provider_and_plan_tampering_rejects(self):
        with patch.object(a.cohorts.queue.learning,'protections',return_value={'changed':True}),patch.object(a.gateway,'send') as send:
            with self.assertRaisesRegex(ValueError,'protections changed'):self.session.request(self.item)
            send.assert_not_called()
        p=copy.deepcopy(self.plan);p['selected'][0]['partition']='validation'
        self.session.file.write_text(json.dumps(p))
        with self.assertRaisesRegex(ValueError,'integrity'):a.Session(self.stack,self.key)

    def test_conflicting_inherited_field_is_excluded_before_acquisition(self):
        # Create an alternative isolated fixture plan with two anchors tied to b.
        file=self.session.file;file.unlink()
        targets=self.fixture.output/'targets.json';rows=json.loads(targets.read_text())
        anchors=self.fixture.output/'anchors.json';prior=json.loads(anchors.read_text())
        source=a.field_source(self.stack,next(r for r in self.plan['inventory'] if r['id']==self.item))
        exemplar={'item_id':'exposure','patient_family':'b','source_hash':'fixture','template':'template',
            'input':source[4],'partition':'train'}
        prior.extend([exemplar,{**exemplar,'partition':'calibration'}]);a.cohorts.queue.write(anchors,prior)
        # Use a verified-input fixture boundary to exercise the real pre-dispatch partitioner.
        plan=json.loads((self.fixture.output/'plan.json').read_text())
        with patch.object(a.cohorts,'connected',return_value=(self.fixture.output,plan,rows)):
            acquired=a.prepare(self.stack,self.key)
        self.assertIn(self.item,{r['item_id'] for r in acquired['excluded']})
        self.assertNotIn(self.item,{r['item_id'] for r in acquired['selected']})

    def test_received_reference_metadata_does_not_submit_linked_text(self):
        r=self.fixture.fixture.sources['b'].copy();r.update(source_table='referral_status_updates',referral_record_id='ref')
        linked={**r,'record_id':'ref','source_table':'referrals','text':'LINKED CONTENT NOT SUBMITTED'};linked.pop('referral_record_id')
        db=sqlite3.connect(self.stack/'stack.sqlite3')
        with db:
            db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),r['record_id']))
            db.execute('INSERT INTO records VALUES (?,?,?,?)',('ref','b','referrals',json.dumps(linked)))
        db.close();av=a.availability.prepare(self.stack,r,candidate_ids=['ref','missing'])['wire']
        self.assertEqual(av['typed_references'][0]['status'],'received')
        self.assertFalse(av['typed_references'][0]['content_submitted'])
        self.assertEqual(av['candidate_record_availability'][1]['status'],'not_in_received_same_patient_snapshot')
        self.assertNotIn('LINKED CONTENT',json.dumps(av));self.assertNotIn('HIDDEN CONTENT',json.dumps(av))

    def test_receipt_tamper_and_bad_task_selection_rejected(self):
        r=self.propose()
        with self.assertRaises(ValueError):a.select(self.stack,{'cohort_id':self.key,'item_id':self.item,'proposal_id':'foreign'})
        file=self.session.root/'receipts'/(self.item+'.json');r['partition']='other';file.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'changed'):self.session.inspect(self.item)


if __name__=='__main__':unittest.main()
