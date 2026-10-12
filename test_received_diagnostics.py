"""Received nested measurement fields and reconciliation accounts retain meaning."""
import unittest
from clinical_core.agere.clinical import clinical_inputs as ci,warehouse_interpretation as w
from clinical_core.agere.narrative_sources import text_fields
from test_warehouse_interpretation import record,ASOF


def sources(**changes):
    r=record('report','diagnostic_test_reports',test_kind='echocardiogram',report_status='final',
        test_quality='adequate',measurements={'LVEF_percent':35},order_record_id='order',session_record_id='session')
    r.update(changes)
    return [r,record('order','diagnostic_orders'),record('session','diagnostic_test_sessions',status='performed')]


class ReceivedDiagnostics(unittest.TestCase):
    def test_nested_percent_has_literal_field_and_dependency_provenance(self):
        row=ci.measurements('P-1',sources(),[],ASOF)['lvef'][0]
        self.assertEqual(row['lower'],35);self.assertIsNone(row['exclusion'])
        self.assertEqual(row['evidence'][0]['field'],'measurements.LVEF_percent')
        self.assertEqual(row['evidence'][0]['unit_authority'],'explicit_adapter_field_unit')
        self.assertEqual({x['record_id'] for x in row['linked_sources']},{'order','session'})
        self.assertIsNone(row['clinical_date'])
        binding={'clock':'source_statement_date','selection':'any_history','op':'le','value':40}
        self.assertIs(ci.measurement_binding([row],binding,ASOF)['value'],True)
        self.assertIsNone(ci.measurement_binding([row],{**binding,'clock':'explicit_clinical_date'},ASOF)['value'])

    def test_bad_quality_status_value_and_links_do_not_become_usable_measurements(self):
        for changes in [{'test_quality':'limited'},{'report_status':'preliminary'},
                        {'measurements':{'LVEF_percent':None}},{'measurements':{'LVEF_percent':True}},
                        {'measurements':{'LVEF_percent':101}},{'session_record_id':'missing'}]:
            row=ci.measurements('P-1',sources(**changes),[],ASOF)['lvef'][0]
            self.assertIsNotNone(row['exclusion'])
        rows=sources();rows[-1]['source_table']='encounters'
        self.assertEqual(ci.measurements('P-1',rows,[],ASOF)['lvef'][0]['link_issues'][0]['reason'],'wrong_linked_source_type')

    def test_mapping_requires_exact_test_and_native_adapter(self):
        for changes in [{'test_kind':'renal_ultrasound'},{'_warehouse_source':{'adapter':'unknown'}}]:
            self.assertFalse(ci.measurements('P-1',sources(**changes),[],ASOF).get('lvef'))

    def test_imported_report_keeps_original_report_link_without_inventing_date(self):
        rows=sources();copy=record('received','received_diagnostic_reports',test_kind='echocardiogram',
            report_status='final',test_quality='adequate',measurements={'LVEF_percent':35},
            order_record_id='order',original_report_record_id='report')
        obs=ci.measurements('P-1',[*rows,copy],[],ASOF)['lvef']
        self.assertEqual(len(obs),2);self.assertTrue(all(o['clinical_date'] is None for o in obs))
        self.assertEqual(obs[1]['linked_sources'][1]['record_id'],'report')

    def test_narrative_history_and_findings_reach_shared_source_adapter(self):
        for field in ('history','findings','comparison','indication','patient_stated_goal'):
            r=record('r','diagnostic_review_interviews',**{field:'Source text: breathing limits walking.'})
            self.assertEqual(list(text_fields(r)),[{'field':field,'text':r[field]}])

    def test_medication_interview_use_is_not_directions_administration_or_stop_order(self):
        rows=[record('i','medication_history_interviews',completeness='partial_history_pending_source_comparison'),
              record('list','medication_lists',name='Furosemide'),
              record('entry','reported_medication_entries',medication_name_reported='Furosemide',
                interview_record_id='i',historical_list_record_id='list',use_statement='reports_taking',
                dose_reported=None,frequency_reported=None,directions_verification='not_confirmed',
                prescriber_stop_order_established=False,administration_observed=False)]
        out=w.interpret({'patient_id':'P-1'},rows,ASOF)
        entry=next(s for s in out['statements'] if s['relation']=='reported_product_use')
        self.assertIsNone(entry['reported_use']);self.assertEqual(entry['entity'],'list')
        self.assertIsNone(entry['values']['dose_reported']);self.assertFalse(entry['clinical_truth_established'])
        self.assertEqual({x['target']['record_id'] for x in entry['linked_sources']},{'i','list'})

    def test_missing_pharmacy_response_remains_source_dependency(self):
        rows=[record('review','medication_review_decisions',received_pharmacy_information_record_id='missing',
            entries=[],review_status='partial_reconciliation_unresolved_items_retained')]
        out=w.interpret({'patient_id':'P-1'},rows,ASOF)
        self.assertEqual(out['states'][0]['status'],'source_link_unresolved')
        self.assertFalse(out['clinical_state_established'])


if __name__=='__main__':unittest.main()
