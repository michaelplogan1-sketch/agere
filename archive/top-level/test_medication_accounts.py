"""Medication item provenance, independent clocks, copies and adverse inputs."""
import copy,json,unittest
from clinical_core.agere.clinical import medication_accounts as m,native_requirements as n,evidence_requirements as er,evidence_resolution as resolution
from test_warehouse_interpretation import record,ASOF
from test_native_requirements import NativePersistenceTests


def chain():
    item={'requested_name':'Carvedilol','original_dispense_record_id':'d','dispensed_date':'2028-08-01',
          'dispensing_label':{'medication_name':'carvedilol 6.25 mg tablet','label_dose':1,'label_dose_unit':'tablet','label_times_per_day':2}}
    review={'reported_entry_record_id':'p','medication_name':'Carvedilol','dispensing_label_available':True,
            'current_use_statement':'reports_taking','disposition':'historical_dispensing_label_available_current_regimen_requires_confirmation',
            'prescriber_instruction_changed':False}
    return [record('i','medication_history_interviews',completeness='partial_history_pending_source_comparison'),
      record('h','medication_lists',name='Carvedilol',dose='6.25 mg',frequency='twice daily',event_date=None),
      record('p','reported_medication_entries',interview_record_id='i',historical_list_record_id='h',
             medication_name_reported='Carvedilol',use_statement='reports_taking',dose_reported='6.25 mg',frequency_reported='twice daily',directions_verification='not_confirmed'),
      record('q','medication_information_requests',interview_record_id='i'),
      record('o','pharmacy_information_responses',request_record_id='q',items=[item],response_status='records_supplied'),
      record('c','received_pharmacy_information',request_record_id='q',original_response_record_id='o',items=[copy.deepcopy(item)],response_status='records_supplied'),
      record('d','pharmacy_dispenses',medication_name='carvedilol 6.25 mg tablet',status='dispensed'),
      record('r','medication_review_decisions',interview_record_id='i',received_pharmacy_information_record_id='c',entries=[review]),
      record('x','medication_clarification_contacts',review_decision_record_id='r',communication_result='conversation_completed',directions_newly_verified=False)]


def by_id(rows,key):return next(r for r in rows if r['record_id']==key)


class ItemTests(unittest.TestCase):
    def project(self,rows):return m.project('P-1',rows,ASOF)
    def test_chain_preserves_literal_items_and_no_current_use_claim(self):
        rows=chain();p=self.project(rows);s=p['statements'][0];c=s['reconciliation']
        self.assertEqual(p['states'][0]['status'],'documented_source_statement');self.assertEqual(s['entity'],'p')
        self.assertFalse(c['ingestion_established']);self.assertFalse(c['current_regimen_established'])
        self.assertEqual(c['reported_dose_notation']['nominal_daily_amount'],12.5)
        self.assertEqual(s['values']['review']['disposition'],'historical_dispensing_label_available_current_regimen_requires_confirmation')
        self.assertEqual(len(c['copy_lineage']),1);self.assertFalse(c['copy_lineage'][0]['independent_corroboration'])
        for e in s['evidence']:self.assertEqual(e['value'],m.value_at(by_id(rows,e['record_id']),e['field_path']))
        self.assertTrue(any(e['field_path']==['entries',0] for e in s['evidence']))
        self.assertTrue(any(e['field_path']==['items',0] for e in s['evidence']))

    def test_no_pharmacy_match_does_not_become_no_use(self):
        rows=chain()
        for key in ('o','c'):by_id(rows,key).update(items=[],response_status='no_matching_records_in_this_pharmacy_window')
        by_id(rows,'r')['entries'][0].update(dispensing_label_available=False,disposition='current_regimen_not_verified')
        p=self.project(rows);self.assertEqual(p['states'][0]['status'],'documented_source_statement')
        s=p['statements'][0];self.assertEqual(s['values']['reported']['use_statement'],'reports_taking')
        self.assertFalse(s['reconciliation']['pharmacy_items'])

    def test_copy_tampering_duplicate_items_and_wrong_links_are_unresolved(self):
        for change in ('copy','ambiguous','missing','type','label_disagrees','interview','literal_type','request_cycle'):
            rows=chain()
            if change=='copy':by_id(rows,'c')['items'][0]['dispensing_label']['label_dose']=2
            if change=='ambiguous':by_id(rows,'o')['items']*=2;by_id(rows,'c')['items']*=2
            if change=='missing':rows.remove(by_id(rows,'d'))
            if change=='type':by_id(rows,'d')['source_table']='medication_lists'
            if change=='label_disagrees':by_id(rows,'r')['entries'][0]['dispensing_label_available']=False
            if change=='interview':rows.append(record('j','medication_history_interviews'));by_id(rows,'q')['interview_record_id']='j'
            if change=='literal_type':by_id(rows,'d')['label_dose']='1'
            if change=='request_cycle':by_id(rows,'q')['original_request_record_id']='q'
            p=self.project(rows);self.assertEqual(p['states'][0]['status'],'source_link_unresolved',change)
            self.assertTrue(p['statements'][0]['link_issues'])

    def test_same_name_does_not_merge_two_reported_products(self):
        rows=chain();rows.append({**by_id(rows,'p'),'record_id':'p2'})
        p=self.project(rows);self.assertEqual(len(p['states']),2)
        self.assertIn('requested_name_matches_multiple_reported_entries',[i['reason'] for i in p['statements'][0]['link_issues']])
        self.assertTrue(all(s['status']=='source_link_unresolved' for s in p['states']))

    def test_no_review_yields_targeted_source_request(self):
        rows=[r for r in chain() if r['record_id']!='r'];p=self.project(rows)
        spec=er.definition('medication_label_report');row=er.evaluate(None,spec,native_report=p,patient_id='P-1',as_of=ASOF)
        row['id']='a'*64;self.assertEqual(row['status'],'unresolved');requests=resolution.project(row)['requests']
        self.assertTrue(any(r['kind']=='resolve_source_reference' and r['detail']['dependency']['reason']=='review_entry_not_received' for r in requests))

    def test_cross_patient_late_and_duplicate_records_rejected(self):
        for change in ('patient','late','duplicate'):
            rows=chain()
            if change=='patient':by_id(rows,'d')['patient_id']='P-2'
            if change=='late':by_id(rows,'d')['available_date']='2029-01-01'
            if change=='duplicate':rows.append(copy.deepcopy(rows[0]))
            with self.assertRaises(ValueError):self.project(rows)

    def test_same_day_reviews_conflict_and_unknown_date_does_not_win(self):
        for date,status in [('2028-09-01','conflicting'),(None,'undated')]:
            rows=chain();r=copy.deepcopy(by_id(rows,'r'));r.update(record_id='r2',event_date=date);r['entries'][0]['current_use_statement']='reports_not_taking';rows.append(r)
            p=self.project(rows);self.assertEqual(p['states'][0]['status'],status);self.assertEqual(len(p['states'][0]['selected_statement_ids']),2)

    def test_all_items_are_evaluated_not_only_first_or_first_drug(self):
        rows=chain();p2={**by_id(rows,'p'),'record_id':'p2','medication_name_reported':'Furosemide'};rows.append(p2)
        second={**by_id(rows,'r')['entries'][0],'reported_entry_record_id':'p2','medication_name':'Furosemide','dispensing_label_available':False}
        by_id(rows,'r')['entries'].append(second);p=self.project(rows)
        self.assertEqual({s['entity'] for s in p['statements']},{'p','p2'})
        self.assertTrue(all(s['status']=='documented_source_statement' for s in p['states']))


class ItemPersistenceTests(NativePersistenceTests):
    def test_item_change_invalidates_only_affected_patient(self):
        rows=chain();self.save('P-1',rows);params={'contract':'medication_label_report','filter':'supported'}
        yes,unknown,out=er.execute(self.root,['P-1','P-2'],params);self.assertEqual(yes,{'P-1'});self.assertEqual(unknown,{'P-2'})
        receipt=next(r['id'] for r in out['patients'] if r['patient_id']=='P-1');self.assertEqual(er.inspect(self.root,receipt)['status'],'supported')
        # Change only raw received evidence, with the legacy parent report unchanged.
        r=by_id(rows,'c');r['items'][0]['dispensing_label']['label_dose']=3
        self.db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(r),'c'));self.db.commit()
        with self.assertRaises(ValueError):er.inspect(self.root,receipt)
        yes,unknown,out=er.execute(self.root,['P-1','P-2'],params);self.assertFalse(yes);self.assertEqual(unknown,{'P-1','P-2'})
        self.assertEqual(out['processing_counts'],{'patient_contracts_computed':1,'patient_cache_hits':1})

if __name__=='__main__':unittest.main()
