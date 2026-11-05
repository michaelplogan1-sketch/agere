"""Study references retain source grain, unknowns and distinct amount clocks."""
import json,unittest
from pathlib import Path
from tools import ordinary_stack_scoring as S
from tools.ordinary_stack_comparison import source_boundary,ROOT


class OrdinaryReference(unittest.TestCase):
    def test_historical_discharge_scope_never_uses_present_survival_or_fans_out_payers(self):
        events=[{'patient_id':p,'kind':'inpatient','end_date':d}for p,d in
            [('later_died','2028-12-31'),('later_died','2029-01-01'),('overlap','2029-02-01'),('unknown','2029-02-01')]]
        spans=[{'patient_id':'later_died','payer_id':'P','coverage_start':'2028-01-01','coverage_end':'2029-01-01'},
               {'patient_id':'overlap','payer_id':'P','coverage_start':'2029-01-01','coverage_end':None},
               {'patient_id':'overlap','payer_id':'Q','coverage_start':'2029-01-01','coverage_end':None}]
        r=S.q01(events,spans)
        self.assertEqual(r['totals'],{'discharges_2028':1,'discharges_2029':3})
        self.assertEqual(sum(row['discharges_2029']for row in r['rows']),3)
        row=next(row for row in r['rows']if row['payer_ids']==['P'])
        self.assertEqual(row['percentage_change'],0)
        other=next(row for row in r['rows']if row['payer_ids']==['P','Q'])
        self.assertEqual(other['discharges_2029'],1);self.assertIsNone(other['percentage_change'])
        self.assertEqual(r['payer_assignment_counts'],{'received':2,'conflicting_or_multiple':1,'unknown':1})

    def test_cash_is_separate_from_adjudicated_paid_and_cohort_membership(self):
        claims=[{'claim_id':'A','payer':'P','service_date':'2029-03-01','allowed_cents':100,
                 'net_payer_paid_cents':80,'net_payer_cash_cents':40},
                {'claim_id':'B','payer':'P','service_date':'2029-06-30','allowed_cents':0,
                 'net_payer_paid_cents':None,'net_payer_cash_cents':None},
                {'claim_id':'C','payer':'P','service_date':'2029-07-01','allowed_cents':999,
                 'net_payer_paid_cents':999,'net_payer_cash_cents':999}]
        allocations=[{'claim_id':'A','allocated_cents':50},{'claim_id':'A','allocated_cents':-10},
                     {'claim_id':'C','allocated_cents':999},{'claim_id':'missing','allocated_cents':777}]
        r=S.q02(claims,allocations);t=r['totals'];attribution=r['cash_allocation_attribution']
        self.assertEqual(t['claims'],2);self.assertEqual(t['net_payer_paid_cents']['observed_sum_cents'],80)
        self.assertEqual(t['net_payer_paid_cents']['missing_values'],1)
        self.assertEqual(t['allowed_cents']['received_values'],2)
        self.assertEqual(attribution['service_cohort']['observed_sum_cents'],40)
        self.assertEqual(attribution['other_received_service_cohort']['observed_sum_cents'],999)
        self.assertEqual(attribution['unlinked_claim']['observed_sum_cents'],777)

    def test_los_definition_difference_zero_and_absent_dates_remain_distinct(self):
        stays=[{'patient_id':'A','type':'inpatient','organization_id':None,'admit_date':'2029-01-01','discharge_date':'2029-01-01','los_days':5},
               {'patient_id':'B','type':'inpatient','organization_id':None,'admit_date':'2029-01-01','discharge_date':'2029-01-03','los_days':0},
               {'patient_id':'C','type':'inpatient','organization_id':None,'admit_date':'2029-01-01','discharge_date':None,'los_days':100},
               {'patient_id':'D','type':'observation','organization_id':None,'admit_date':'2029-01-01','discharge_date':'2029-01-03','los_days':999}]
        r=S.q03(stays);t=r['totals']
        self.assertEqual((t['admissions'],t['discharge_received'],t['discharge_absent']),(3,2,1))
        self.assertEqual(t['recorded_los_days']['sum_days'],5)
        self.assertEqual(t['calendar_date_difference']['sum_days'],2)
        self.assertEqual(t['recorded_los_days']['zero_n'],1)
        self.assertEqual(t['calendar_date_difference']['zero_n'],1)
        self.assertEqual(t['recorded_los_days']['received_n'],2)

    def test_received_outcome_overrides_booked_status_and_retains_unknown_provider(self):
        rows=[{'clinician_id':None,'scheduled_date':'2029-03-01','final_status':'booked','outcome':'kept','kept':1},
              {'clinician_id':None,'scheduled_date':'2029-03-02','final_status':'booked','outcome':'no_show','kept':0},
              {'clinician_id':'P','scheduled_date':'2029-03-03','final_status':'strange','outcome':None,'kept':0},
              {'clinician_id':'P','scheduled_date':'2030-03-03','final_status':'fulfilled','outcome':'kept','kept':1}]
        r=S.q15(rows);t=r['totals']
        self.assertEqual(t['appointments'],3);self.assertEqual(t['kept'],1)
        self.assertEqual(t['no_show'],1);self.assertEqual(t['scheduled'],0)
        self.assertEqual(t['other_or_unknown'],1)
        self.assertEqual(sum(row['appointments']for row in r['rows']),3)

    def test_neither_study_arm_can_read_evaluator_references(self):
        path=ROOT/'artifacts/agere-ordinary-scoring-2026-11-05/Q01.json'
        with source_boundary(ROOT/'artifacts/authored-study-scratch'):
            with self.assertRaisesRegex(RuntimeError,'Protected or unrelated source'):
                path.read_text()


if __name__=='__main__':unittest.main()
