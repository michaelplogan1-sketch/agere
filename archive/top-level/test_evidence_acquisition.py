import json,unittest
from clinical_core.agere.clinical import evidence_acquisition as acquisition
from test_native_requirements import NativePersistenceTests,use


class AcquisitionTests(unittest.TestCase):
    setUp=NativePersistenceTests.setUp
    tearDown=NativePersistenceTests.tearDown
    save=NativePersistenceTests.save

    def test_complete_explicit_population_and_cached_repeat(self):
        first=acquisition.prepare(self.root,['native_medication_nonuse'],['P-1','P-2'])
        self.assertEqual(first['patients_evaluated'],2)
        self.assertEqual(first['counts']['patients_with_requests'],1)
        self.assertEqual(first['counts']['requests'],1)
        page=acquisition.list_items(self.root,first['id']);self.assertEqual(page['items'][0]['patient_id'],'P-2')
        repeat=acquisition.prepare(self.root,['native_medication_nonuse'],['P-2','P-1'])
        self.assertEqual(first['id'],repeat['id']);self.assertTrue(repeat['cache_hit'])
        self.assertEqual(repeat['requirement_processing_counts'],{'patient_cache_hits':2})
        self.assertEqual(repeat['requests_sent'],0)
        item=acquisition.inspect_item(self.root,repeat['id'],page['items'][0]['id'])
        self.assertEqual(item['requirements'][0]['patient_id'],'P-2')

    def test_changed_evidence_expires_old_queue_and_recomputes_only_affected_patient(self):
        first=acquisition.prepare(self.root,['native_medication_nonuse'],['P-1','P-2'])
        self.save('P-2',[use('b',patient_id='P-2')])
        with self.assertRaisesRegex(ValueError,'source or review changed'):acquisition.list_items(self.root,first['id'])
        refreshed=acquisition.prepare(self.root,['native_medication_nonuse'],['P-1','P-2'])
        self.assertEqual(refreshed['counts']['requests'],0)
        self.assertEqual(refreshed['requirement_processing_counts'],{'patient_cache_hits':1,'patient_contracts_computed':1})
        self.assertNotEqual(first['id'],refreshed['id'])
        # History retained for audit, but is never returned as current work.
        self.assertEqual(acquisition.load(self.root,first['id'],verify=False)['counts']['requests'],1)

    def test_empty_selected_set_is_never_expanded_to_every_patient(self):
        result=acquisition.prepare(self.root,['native_medication_nonuse'],[])
        self.assertEqual(result['patients_evaluated'],0);self.assertEqual(result['counts']['requests'],0)
        with self.assertRaises(ValueError):acquisition.prepare(self.root,['native_medication_nonuse'],['invented'])

    def test_deep_inspection_rejects_changed_raw_field(self):
        self.save('P-1',[use('a',event_date=None)])
        result=acquisition.prepare(self.root,['native_medication_nonuse'],['P-1'])
        page=acquisition.list_items(self.root,result['id'])
        changed=use('a',event_date='2028-09-01')
        self.db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(changed),'a'));self.db.commit()
        with self.assertRaisesRegex(ValueError,'hash changed'):
            acquisition.inspect_item(self.root,result['id'],page['items'][0]['id'])

    def test_pagination_does_not_change_denominator(self):
        self.save('P-1',[use('a',event_date=None)])
        result=acquisition.prepare(self.root,['native_medication_nonuse'],['P-1','P-2'])
        first=acquisition.list_items(self.root,result['id'],limit=1)
        second=acquisition.list_items(self.root,result['id'],offset=first['next_offset'],limit=1)
        self.assertEqual(first['counts']['requests'],2);self.assertEqual(len(first['items']),1)
        self.assertNotEqual(first['items'][0]['id'],second['items'][0]['id'])
        self.assertIsNone(second['next_offset'])


if __name__=='__main__':unittest.main()
