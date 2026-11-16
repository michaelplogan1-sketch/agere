"""Care reference semantics and temporal failures, not clinical qualification."""
import copy
import unittest
from clinical_core.agere.clinical import native_care_assertions as care, assertion_reconciliation as ar, interpretation_state as interp, source_state as state, pre_event_state as pre
from test_native_assertions import record
from test_task_context import META
from test_pre_event_state import fixture as temporal_fixture, AP, SPEC

P = {'streams': list(care.STREAMS), 'concept': care.ALL, 'relation': 'care_completion', 'clock': 'source_statement_date', 'filter': 'all'}


def records():
    common = dict(event_date='2026-07-01', available_date='2026-07-02')
    return [
        record('referrals', 'referral_type', 'dental_care_access', record_id='request', status='requested', **common),
        record('referral_status_updates', 'service_performed_confirmed', False, record_id='booking', status='appointment_reported_booked', referral_record_id='request', **common),
        record('imported_documents', 'service_date', '2026-07-10', record_id='report', referral_record_id='request', content_extent='summary_only', reported_findings={'status': 'completed_limited_exam'}, event_date='2026-07-12', available_date='2026-07-14'),
        record('outside_report_status', 'performed_service_documented', True, record_id='completion', status='received', referral_record_id='request', imported_record_id='report', event_date='2026-07-13', available_date='2026-07-13')]


def interpreted(rows, params=P):
    base = ar.patient_receipt('p', rows, [], {'streams': params['streams'], 'concepts': [params['concept']]}, META); base['id'] = 'base'
    result = interp.patient_receipt(base, [], []); result['id'] = 'interpreted'
    return result


class NativeCareTests(unittest.TestCase):
    def test_request_booking_and_limited_service_are_distinct(self):
        row = state.project(interpreted(records()), P)
        self.assertEqual(row['value'], 'affirmed')
        self.assertEqual(row['care_account_counts'], {'performed_service_documented': 1})
        a = row['selected_assertions'][0]
        self.assertEqual(a['care_request_id'], 'request')
        self.assertEqual(a['clocks']['clinical_date'], '2026-07-10')
        self.assertEqual(a['clocks']['source_date'], '2026-07-13')
        self.assertEqual(a['clocks']['arrival_date'], '2026-07-14')
        self.assertTrue(any(r['value'] == {'status': 'completed_limited_exam'} for r in a['reference_evidence']))
        self.assertIsNone(row['care_accounts'][0]['all_requested_care_complete'])

    def test_false_confirmation_never_negative_completion(self):
        row = state.project(interpreted(records()[:2]), P)
        self.assertEqual(row['status'], 'unobserved'); self.assertIsNone(state.membership(row, 'negated'))
        self.assertEqual(row['care_account_counts'], {'completion_not_observed': 1})
        self.assertEqual({a['relation'] for a in row['care_accounts'][0]['reports']}, {'care_order_plan', 'care_booking_report', 'care_status_report'})

    def test_mismatched_received_report_does_not_bind(self):
        rows = records(); rows[2]['referral_record_id'] = 'other'
        row = state.project(interpreted(rows), P)
        self.assertEqual(row['status'], 'unresolved_interpretation'); self.assertFalse(row['selected_assertions'])
        self.assertTrue(any(i['reason'] == 'report_request_reference_mismatch' for g in row['native_mapping_gaps'] for i in g['issues']))

    def test_missing_report_request_and_wrong_types_remain_unknown(self):
        for mutation in ('missing_report', 'missing_request', 'wrong_report_type', 'unreceived_report', 'non_native_report'):
            rows = records()
            if mutation == 'missing_report': rows.pop(2)
            elif mutation == 'missing_request': rows.pop(0)
            elif mutation == 'wrong_report_type': rows[2]['source_table'] = 'notes'
            elif mutation == 'unreceived_report': rows[2]['available_date'] = '2027-01-01'
            else: rows[2]['_warehouse_source'] = {}
            # assess deliberately accepts a mixed dependency inventory to reject
            # links itself; the broader context layer rejects future inputs too.
            mapped = [b for b in care.assess(rows, [care.ALL], META['snapshot']['as_of']) if b['relation'] == 'care_completion']
            self.assertTrue(mapped); self.assertTrue(all(b['status'] != 'mapped' for b in mapped), mutation)

    def test_cross_patient_link_never_maps(self):
        rows = records(); rows[2]['patient_id'] = 'other'
        mapped = [b for b in care.assess(rows, [care.ALL], META['snapshot']['as_of']) if b['relation'] == 'care_completion']
        self.assertTrue(all(b['status'] != 'mapped' for b in mapped))
        with self.assertRaisesRegex(ValueError, 'Cross-patient'): interpreted(rows)

    def test_multiple_requests_stay_separate_and_no_all_complete_claim(self):
        rows = records(); other = copy.deepcopy(rows[0]); other.update(record_id='other', referral_type='transport_assistance'); rows.append(other)
        row = state.project(interpreted(rows), P)
        self.assertEqual(row['care_account_counts'], {'performed_service_documented': 1, 'completion_not_observed': 1})
        self.assertEqual(len(row['care_accounts']), 2)

    def test_unfamiliar_literal_service_uses_same_binding(self):
        rows = records(); rows[0]['referral_type'] = 'community_hearing_support'
        p = {**P, 'concept': 'care community hearing support'}
        self.assertEqual(state.project(interpreted(rows, p), p)['value'], 'affirmed')

    def test_conflicting_service_names_remain_unknown(self):
        rows = records(); rows[0]['requested_specialty'] = 'Cardiology'
        row = state.project(interpreted(rows), P)
        self.assertEqual(row['status'], 'unresolved_interpretation')
        self.assertEqual(row['care_account_counts'], {'request_meaning_unresolved': 1})

    def test_no_service_field_preserves_the_request_denominator(self):
        rows = records()[:1]; rows[0].pop('referral_type')
        row = state.project(interpreted(rows), P)
        self.assertEqual(row['care_account_counts'], {'request_meaning_unresolved': 1})
        self.assertEqual(row['care_accounts'][0]['request_id'], 'request')
        self.assertTrue(row['care_accounts'][0]['mapping_gaps'][0]['reference_evidence'])

    def test_other_specialty_does_not_count(self):
        p = {**P, 'concept': 'care nephrology'}
        row = state.project(interpreted(records(), p), p)
        self.assertFalse(row['selected_assertions']); self.assertFalse(row['care_accounts'])

    def test_conversation_completed_is_not_service_completion(self):
        rows = records()[:2]
        rows.append(record('outside_followup_contacts', 'communication_result', 'conversation_completed', record_id='contact', additional_service_completed=False, report_record_id='note'))
        p = {**P, 'streams': P['streams'] + ['outside_followup_contacts']}
        row = state.project(interpreted(rows, p), p)
        self.assertEqual(row['status'], 'unobserved')

    def test_explicit_clock_requires_service_date(self):
        rows = records(); rows[2].pop('service_date')
        row = state.project(interpreted(rows), {**P, 'clock': 'explicit_clinical_date'})
        self.assertEqual(row['status'], 'unresolved_interpretation')
        self.assertEqual(row['care_account_counts'], {'completion_evidence_unresolved': 1})

    def test_pre_event_completion_qualifies_same_bound_clock(self):
        _, anchor = temporal_fixture()
        row = pre.project(interpreted(records()), anchor, SPEC, AP, {**P, 'clock': 'explicit_clinical_date'})
        self.assertEqual(row['value'], 'affirmed'); self.assertEqual(row['selected_date'], '2026-07-10')

    def test_late_linked_report_cannot_leak_into_prior_state(self):
        rows = records(); rows[2]['available_date'] = '2026-08-04'
        _, anchor = temporal_fixture()
        row = pre.project(interpreted(rows), anchor, SPEC, AP, {**P, 'clock': 'explicit_clinical_date'})
        self.assertEqual(row['status'], 'unobserved'); self.assertFalse(row['selected_assertions'])
        self.assertGreater(row['temporal_evidence']['assertion_reasons']['received_after_index'], 0)

    def test_late_request_date_and_same_day_dependency_unresolved(self):
        for day, expected in [('2026-08-05', 'unobserved'), ('2026-08-01', 'unresolved_temporal_evidence')]:
            rows = records(); rows[0]['event_date'] = day
            _, anchor = temporal_fixture()
            row = pre.project(interpreted(rows), anchor, SPEC, AP, P)
            self.assertEqual(row['status'], expected)


if __name__ == '__main__': unittest.main()
