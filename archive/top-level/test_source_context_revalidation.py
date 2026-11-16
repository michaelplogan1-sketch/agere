"""Historical source IDs require exact current native content, never a stale waiver."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import reference_table_context as F,context_student as C
import test_retrieval_source_plan as fixtures
import test_synthesis_availability as availability_fixture


class SourceSelectionRevalidation(unittest.TestCase):
    setUp=fixtures.SourcePlanTests.setUp
    record=fixtures.SourcePlanTests.record
    prepare=fixtures.SourcePlanTests.prepare
    # Reuse the authored source fixture; exercise the ordinary native inspector.
    def test_reference_and_local_code_metadata_can_change_without_new_selection(self):
        from clinical_core.agere.clinical import retrieval_source_plan as P
        old=self.prepare()['plan']
        with patch.object(P.F,'identity',return_value='f'*64):
            current=P.inspect(self.r,{'id':old['id']})
        self.assertEqual(current['plan'],old)
        proof=json.loads((self.r.path/'source-plan-current-revalidation'/(old['id']+'.json')).read_text())
        self.assertTrue(proof['all_current_selection_fields_equal'])
        self.assertFalse(proof['clinical_qualification_transferred'])
        self.assertEqual(proof['provider_calls'],0)

    def test_reference_metadata_drift_cannot_hide_source_or_scope_changes(self):
        from clinical_core.agere.clinical import retrieval_source_plan as P
        old=self.prepare()['plan'];original=deepcopy(self.records)
        mutations=[lambda:self.records[0].update(text='Different received text'),
                   lambda:self.records[0].update(patient_id='someone-else'),
                   lambda:self.records[0].update(event_date='2029-06-02'),
                   lambda:self.records[0].update(available_date='2029-07-02'),
                   lambda:self.records.pop()]
        for mutate in mutations:
            self.records[:]=deepcopy(original);mutate()
            with self.subTest(records=self.records),patch.object(P.F,'identity',return_value='f'*64):
                with self.assertRaises(ValueError):P.inspect(self.r,{'id':old['id']})

    def test_different_date_contract_or_question_stays_stale(self):
        from clinical_core.agere.clinical import retrieval_source_plan as P
        old=self.prepare()['plan']
        with patch.object(P.E,'code_identity',return_value='new-date-contract'),patch.object(P.F,'identity',return_value='f'*64):
            with self.assertRaisesRegex(ValueError,'changed'):P.inspect(self.r,{'id':old['id']})
        self.report['request']['question']='A different question'
        with patch.object(P.F,'identity',return_value='f'*64):
            with self.assertRaisesRegex(ValueError,'changed'):P.inspect(self.r,{'id':old['id']})

    def test_saved_context_mutation_stays_blocked_after_metadata_revalidation(self):
        from clinical_core.agere.clinical import retrieval_source_plan as P
        old=self.prepare()['plan'];key=old['groups'][0]['context_id']
        self.contexts[key]['extra_claim']='unreceived'
        with patch.object(P.F,'identity',return_value='f'*64):
            with self.assertRaisesRegex(ValueError,'context changed'):P.inspect(self.r,{'id':old['id']})


class ReferenceContextRevalidation(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);path=Path(temp.name)
        parent={'id':'a'*64,'schema':'selected-record-context/1','parameters':{'max_bytes':60000},
            'record_scope':{'record_ids':['r1']},'missing_evidence':{'record_scope':{'kind':'explicit_received_records'}},
            'model_context':{'sources':{'r1':{'source':{'record_id':'r1','patient_id':'P','event_date':'2030-01-01'}}},
                             'evidence':[{'id':'u','text':'Exact source.'}]},'budget':{}}
        row=F.transform(parent);target=F.location(path,row['id']);target.parent.mkdir();target.write_text(json.dumps(row))
        return path,parent,row

    def test_historical_stamp_keeps_id_only_after_complete_current_reconstruction(self):
        path,parent,row=self.fixture();before=F.location(path,row['id']).read_bytes()
        with patch.object(F.C,'inspect',return_value=parent),patch.object(F,'identity',return_value='f'*64):
            self.assertEqual(F.inspect(path,row['id']),row)
        self.assertEqual(F.location(path,row['id']).read_bytes(),before)
        proof=json.loads((path/'reference-context-current-revalidation'/(row['id']+'.json')).read_text())
        self.assertTrue(proof['all_current_context_fields_equal']);self.assertFalse(proof['clinical_qualification_transferred'])

    def test_parent_source_time_or_missingness_changes_are_rejected(self):
        path,parent,row=self.fixture()
        variants=[deepcopy(parent)for _ in range(4)]
        variants[0]['model_context']['evidence'][0]['text']='Changed text'
        variants[1]['model_context']['sources']['r1']['source']['patient_id']='other'
        variants[2]['model_context']['sources']['r1']['source']['event_date']='2030-02-01'
        variants[3]['model_context']['new_null']=None
        for changed in variants:
            with self.subTest(changed=changed),patch.object(F.C,'inspect',return_value=changed),patch.object(F,'identity',return_value='f'*64):
                with self.assertRaisesRegex(ValueError,'differs'):F.inspect(path,row['id'])

    def test_current_transformation_change_is_not_waived_by_old_stamp(self):
        path,parent,row=self.fixture();transform=F.transform
        def changed(*args,**kwargs):
            result=transform(*args,**kwargs);result['record_scope']['new_claim']='unsupported';return result
        with patch.object(F.C,'inspect',return_value=parent),patch.object(F,'identity',return_value='f'*64),patch.object(F,'transform',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'differs'):F.inspect(path,row['id'])

    def test_legacy_saved_response_revalidation_call_signature_is_preserved(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp);(path/'request.json').write_text(json.dumps({'response':{'model':'fixture','content':[]}}))
            result,receipt=C._revalidate_saved('request','fixture','fixture',path,lambda r:r,'authored_offline_recovery')
            self.assertEqual(result['model'],'fixture');self.assertTrue(receipt['cache_hit'])
            self.assertTrue(receipt['prior_failed_reservation_preserved'])


class SourceStageRevalidation(unittest.TestCase):
    setUp=availability_fixture.Availability.setUp
    raw=availability_fixture.Availability.raw
    answer=availability_fixture.Availability.answer
    sender=availability_fixture.Availability.sender

    def test_earlier_source_contract_without_availability_keeps_current_snapshot(self):
        from clinical_core.agere.clinical import synthesis_availability as A
        received=A.snapshot(self.path,self.row)
        self.assertIs(A.historical_snapshot(received,self.row,{'dependencies':{}}),received)

    def test_malformed_historical_stamp_cannot_replace_current_availability(self):
        from clinical_core.agere.clinical import synthesis_availability as A
        received=A.snapshot(self.path,self.row)
        historical={'context_digest':digest(self.row),'dependencies':{'availability_dependency':'not-a-version'}}
        with self.assertRaisesRegex(ValueError,'provenance'):A.historical_snapshot(received,self.row,historical)

    def stage(self):
        from clinical_core.agere.clinical import availability_source_stage as stage,synthesis_availability as A
        received=A.snapshot(self.path,self.row)
        self.row['patient_source_fingerprint']=digest(list(received['records'].values()))
        with patch.object(stage.single,'send',side_effect=self.sender),patch.object(stage.single,'token_check',return_value={'input_tokens':500,'provider_count_cached':False}):
            saved=stage.synthesize(self.path,self.row['id'],self.ledger,allow_live=True)
        return stage,A,saved

    def test_transport_revision_reuses_only_exact_requests_and_current_parser_results(self):
        stage,A,saved=self.stage();before=list(self.ledger.db.execute('select * from calls order by id'))
        with patch.object(A,'identity',return_value='f'*64):
            self.assertEqual(stage.inspect(self.path,saved['id']),saved)
        self.assertEqual(before,list(self.ledger.db.execute('select * from calls order by id')))
        proof=json.loads((stage.location(self.path,saved['id']).parent/'current-revalidation'/(saved['id']+'.json')).read_text())
        self.assertTrue(proof['complete_current_reconstruction_equal']);self.assertFalse(proof['clinical_qualification_transferred'])

    def test_unselected_patient_content_change_cannot_hide_behind_a_historical_stamp(self):
        stage,A,saved=self.stage()
        value=deepcopy(self.records[0]);value.update(record_id='zz-unselected-new',text='An additional received source account.')
        self.db.execute('insert into records values(?,?,?)',(value['record_id'],'p',json.dumps(value)));self.db.commit()
        with patch.object(A,'identity',return_value='f'*64):
            with self.assertRaisesRegex(ValueError,'fingerprint'):
                A.historical_snapshot(A.snapshot(self.path,self.row),self.row,saved)
            with self.assertRaisesRegex(ValueError,'source records changed|fingerprint'):stage.inspect(self.path,saved['id'])

    def test_returned_interpretation_change_fails_even_when_its_json_still_parses(self):
        stage,A,saved=self.stage();request=saved['passes'][0]['usage']['request_hash']
        target=stage.location(self.path,saved['id']).parent/'provider'/(request+'.json')
        response=json.loads(target.read_text());value=json.loads(response['response']['content'][0]['text'])
        value['findings'][0]['text']+=' Changed interpretation.'
        response['response']['content'][0]['text']=json.dumps(value);target.write_text(json.dumps(response))
        with self.assertRaisesRegex(ValueError,'parser|scope|output'):stage.inspect(self.path,saved['id'])


if __name__=='__main__':unittest.main()
