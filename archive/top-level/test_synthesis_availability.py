"""Availability is transport metadata, never unsent source content or clinical truth."""
import copy
import json
import unittest
from unittest.mock import patch
import test_default_stable_synthesis as fixture
from clinical_core.agere.clinical import synthesis_availability as a, availability_source_stage as stage
from clinical_core.agere.clinical import availability_reconciliation as rec, source_stage_synthesis as old
from clinical_core.agere.clinical import source_synthesis as router, case_passes as cp, synthesis_work

class Availability(unittest.TestCase):
    setUp=fixture.DefaultStable.setUp
    raw=fixture.DefaultStable.raw
    answer=fixture.DefaultStable.answer
    sender=fixture.DefaultStable.sender
    run_default=fixture.DefaultStable.run_default

    def packet(self):
        links=cp.dependencies(self.path,self.row);received=a.snapshot(self.path,self.row)
        bundle,links=a.plan(self.row,links,received)
        body,units=a.pass_request(bundle['passes'][0],links,self.row,received)
        return body,units,links,received

    def test_all_units_accounted_and_envelope_in_transport_budget(self):
        row,bundle,links,packets,key=stage.prepare(self.path,self.row['id'])
        self.assertEqual(sorted(bundle['unit_manifest']),sorted(u['id'] for _,us in packets for u in (v['unit'] for v in us.values())))
        for body,units in packets:
            self.assertLessEqual(cp.request_bytes(body),20000)
            scope=json.loads(body['messages'][0]['content'])['received_source_availability']
            self.assertEqual(scope['request_complete_units'],len(units))
            self.assertEqual(scope['selected_units'],len(self.row['model_context']['evidence']))
            self.assertFalse(scope['clinical_meaning_validated'])

    def test_received_outside_record_contents_never_submitted(self):
        outside={**self.records[0],'record_id':'outside','text':'UNSENT_RECORD_SECRET_CONTENT','original_text':'HIDDEN_GENERATOR_TRUTH'}
        self.db.execute('INSERT INTO records VALUES (?,?,?)',('outside','p',json.dumps(outside)));self.db.commit()
        self.row['parameters']['question']='Is outside available?'
        body,units,links,received=self.packet();wire=json.loads(body['messages'][0]['content']);scope=wire['received_source_availability']
        rows={r[0]:dict(zip(scope['records']['columns'],r)) for r in scope['records']['rows']}
        self.assertEqual(rows['outside']['availability'],'received');self.assertEqual(rows['outside']['selected_units'],0)
        self.assertEqual(scope['received_records_outside_selection'],1)
        self.assertNotIn('UNSENT_RECORD_SECRET_CONTENT',json.dumps(body));self.assertNotIn('HIDDEN_GENERATOR_TRUTH',json.dumps(body))
        self.assertEqual(scope['literal_known_record_mentions'],['outside'])

    def test_literal_known_id_boundary_and_unknown_prose_not_missing_claim(self):
        self.row['parameters']['question']='r1-long is not r1; unknown-looking WHL-R-123 remains prose.'
        body,*_=self.packet();scope=json.loads(body['messages'][0]['content'])['received_source_availability']
        self.assertEqual(scope['literal_known_record_mentions'],['r1'])
        self.assertNotIn('WHL-R-123',json.dumps(scope))

    def test_absent_typed_reference_is_scoped_not_absent_care(self):
        body,units,links,received=self.packet()
        rid=next(iter(units.values()))['unit']['source_ref']
        links['unresolved_references']=[{'source_record':rid,'record_id':'unreceived','reason':'missing_referenced_source'}]
        wire=json.loads(a.envelope(body,units,links,received)['messages'][0]['content']);scope=wire['received_source_availability']
        value=next(r for r in scope['records']['rows'] if r[0]=='unreceived')
        self.assertEqual(value[1],'not_in_received_same_patient_snapshot');self.assertEqual(value[-3:],[0,0,0])
        self.assertEqual(scope['unresolved_typed_references'],links['unresolved_references'])

    def test_excerpt_stage_never_claims_full_units_were_submitted(self):
        out=self.run_default()
        for g in out['reconciliation']['groups']:
            scope=g['source_availability'];self.assertEqual(scope['request_complete_units'],0)
            self.assertEqual(scope['scope'],'cited_excerpts_only');self.assertGreater(scope['request_excerpt_units'],0)
            self.assertTrue(all(r[-2]==0 for r in scope['records']['rows']))
        self.assertFalse(out['clinical_state_established']);self.assertFalse(out['clinical_execution_authorized'])
        self.assertEqual(len(out['reference_manifest']),len(self.row['model_context']['evidence']))

    def test_added_received_source_invalidates_saved_availability(self):
        out=self.run_default();r={**self.records[0],'record_id':'outside'}
        self.db.execute('INSERT INTO records VALUES (?,?,?)',('outside','p',json.dumps(r)));self.db.commit()
        with self.assertRaisesRegex(ValueError,'dependenc|changed'):router.inspect(self.path,out['id'])

    def test_snapshot_rejects_non_synthetic_or_foreign_payload(self):
        for change in ({'synthetic':False},{'patient_id':'other'}):
            r={**self.records[0],**change};self.db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),'r0'));self.db.commit()
            with self.assertRaisesRegex(ValueError,'same-patient synthetic'):a.snapshot(self.path,self.row)

    def test_indivisible_availability_envelope_overflow_blocks_before_inference(self):
        self.row['parameters']['question']='Which records?'
        with patch.object(a,'INSTRUCTIONS','x'*22000),patch.object(stage.single,'send') as send:
            with self.assertRaisesRegex(ValueError,'indivisible'):router.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
            send.assert_not_called()

    def test_old_source_stage_resumes_original_contract_without_new_source_calls(self):
        from clinical_core.agere.clinical import stable_reconciliation
        with patch.object(stage.single,'send',side_effect=self.sender),patch.object(stage.single,'token_check',return_value={'input_tokens':500,'provider_count_cached':False}):
            parent=old.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
            before=self.ledger.receipt()['calls']['validated'];out=router.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
        self.assertEqual(out['schema'],stable_reconciliation.VERSION)
        self.assertEqual(out['parent_answer_id'],parent['id'])
        self.assertEqual(self.ledger.receipt()['calls']['validated']-before,len(out['reconciliation']['groups']))
        self.assertEqual(router.inspect(self.path,parent['id']),parent)

    def test_availability_cannot_be_cited_as_original_text(self):
        body,units,_,_=self.packet();raw=self.answer(body);v=json.loads(raw['content'][0]['text'])
        v['findings'][0]['citations'][0]['quote']='not_in_received_same_patient_snapshot'
        with self.assertRaises(ValueError):cp.parse_pass(self.raw(body,v),body,units,self.row)

    def test_work_context_retains_answer_and_unreviewed_proposals(self):
        out=self.run_default();contexts=synthesis_work.contexts(self.path,[out['id']],['p'])
        self.assertEqual(contexts['p']['authority'],'machine_proposal_context_only')
        self.assertEqual(contexts['p']['answers'][0]['answer_id'],out['id'])
        self.assertTrue(all(not r['execution_authorized'] for r in contexts['p']['answers'][0]['review_proposals']))
        synthesis_work.verify(self.path,list(contexts.values()))

if __name__=='__main__':unittest.main()
