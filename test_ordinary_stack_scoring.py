"""Study references retain source grain, unknowns and distinct amount clocks."""
import copy,json,tempfile,unittest
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

    def test_reported_availability_does_not_invent_a_warehouse_receipt_clock(self):
        records=[{'stream':'labs','event_date':'2029-12-31','available_date':'2030-01-01'},
                 {'stream':'labs','event_date':'2029-01-01','available_date':None},
                 {'stream':'labs','event_date':'2029-01-01','available_date':'2029-01-02','warehouse_receipt_date':'2029-01-03'}]
        row=S.q04(records)['rows'][0]
        self.assertEqual(row['reported_available_by_2029_end'],1)
        self.assertEqual(row['reported_available_later'],1);self.assertEqual(row['reported_availability_unknown'],1)
        self.assertEqual(row['explicit_warehouse_receipt_known'],1)
        self.assertEqual(row['explicit_warehouse_receipt_unknown'],2)

    def test_exposure_keeps_unknown_attribution_and_outside_membership_events_separate(self):
        months=[{'patient_id':'A','month':'2029-01','attribution_practice_id':'P'},
                {'patient_id':'A','month':'2029-02','attribution_practice_id':None}]
        events=[{'patient_id':p,'kind':'inpatient','event_date':d}for p,d in
                [('A','2029-01-15'),('A','2029-02-15'),('B','2029-01-15')]]
        r=S.exposure(months,events,2029,'inpatient');t=r['totals']
        self.assertEqual(t,{'member_months':2,'exposed_people':1,'eligible_events':2,'events_without_received_member_month':1})
        self.assertEqual(next(x for x in r['rows']if x['attribution_practice_id']is None)['events_without_received_member_month'],1)
        with self.assertRaises(ValueError):S.exposure(months*2,events,2029,'inpatient')

    def test_development_is_signed_financial_events_not_final_claim_counts_or_cash(self):
        rows=[{'payer_claim_id':'A','service_from':'2028-12-01','adjudication_date':d,
               'available_date':d,'paid_cents':v}for d,v in[('2029-01-01',100),('2029-02-01',-40)]]
        rows.append({'payer_claim_id':'D','service_from':'2028-12-01','adjudication_date':'2029-01-01','available_date':'2029-01-01','paid_cents':999})
        r=S.q12(rows,[{'payer_claim_id':'A','duplicate_submission':0},{'payer_claim_id':'D','duplicate_submission':1}],'2030-05-27')
        self.assertEqual(r['totals']['observed_sum_cents'],60)
        self.assertEqual(r['totals']['adjudication_events'],2)
        self.assertEqual([x['adjudication_lag_months']for x in r['rows']],[1,2])
        self.assertEqual(r['excluded']['identified_duplicate_submission'],1)

    def test_account_stocks_are_not_summed_and_payment_cutoff_and_identity_are_exact(self):
        def snapshot(rid,day,received,balance):
            return {'record_id':rid,'source_table':'patient_billing_accounts','patient_id':'A','account_id':'ACC','currency':'USD',
                'event_date':day,'event_time':day+'T12:00:00+00:00','billed_cents':100,
                'patient_responsibility_cents':100,'received_cents':received,'balance_cents':balance,
                **{k:0 for k in S.ACCOUNT_FIELDS if k not in('billed_cents','patient_responsibility_cents','received_cents','balance_cents')}}
        records=[snapshot('old','2029-01-01',0,100),snapshot('new','2029-02-01',80,20)]
        for pid,day,amount in [('A','2029-01-30',80),('A','2029-02-02',20),('B','2029-01-30',999)]:
            records.append({'record_id':pid+day,'source_table':'patient_payments','patient_id':pid,'account_id':'ACC','currency':'USD',
                'event_date':day,'status':'received','amount_cents':amount})
        r=S.q13(records);self.assertEqual(r['totals']['accounts_with_snapshot'],1)
        row=r['rows'][0];self.assertEqual(row['snapshot_record_id'],'new')
        self.assertEqual(row['received_cents'],80);self.assertEqual(row['patient_payment_events_through_snapshot']['observed_sum_cents'],80)
        self.assertEqual(row['patient_balance_residual_cents'],0);self.assertEqual(row['billed_allocation_residual_cents'],0)
        self.assertTrue(any(x.get('patient_id')=='B'for x in r['unresolved_links']))

    def test_capacity_does_not_impute_service_times_or_staffing(self):
        r=S.q16([{'scheduled_date':'2029-01-01'},{'scheduled_date':'2029-02-01','duration_minutes':30},
                 {'scheduled_date':'2029-02-01','duration_minutes':-1}])['rows'][0]
        self.assertEqual(r['appointment_demand_2029'],3);self.assertAlmostEqual(r['scenario_demand'],3.3)
        self.assertEqual(r['received_positive_duration_n'],1);self.assertEqual(r['missing_or_invalid_duration_n'],2)

    def test_change_detection_baseline_retains_all_months_and_unknown_exposure(self):
        r=S.q17([{'patient_id':'A','kind':'ed_visit','event_date':'2029-01-01'},
                  {'patient_id':'B','kind':'ed_visit','event_date':'2029-01-01'}],
                 [{'patient_id':'A','month':'2029-01'}])
        self.assertEqual(len(r['rows']),24);self.assertEqual(r['totals']['eligible_ed_events'],1)
        self.assertEqual(r['totals']['ed_without_received_exposure'],1)
        self.assertIsNone(r['rows'][0]['eligible_ed_per_1000_member_months'])

    def test_followup_uses_first_admission_and_contiguous_coverage_and_keeps_unknowns(self):
        events=[{'patient_id':'A','kind':'inpatient','event_date':'2028-01-01','end_date':'2028-01-02','record_id':'a'},
                {'patient_id':'A','kind':'inpatient','event_date':'2028-02-01','end_date':'2028-02-02','record_id':'b'},
                {'patient_id':'B','kind':'inpatient','event_date':'2028-03-01','end_date':None,'record_id':'c'},
                {'patient_id':'C','kind':'inpatient','event_date':'2027-01-01','end_date':'2027-01-02','record_id':'d'},
                {'patient_id':'C','kind':'inpatient','event_date':'2028-01-01','end_date':'2028-01-02','record_id':'e'}]
        spans=[{'patient_id':'A','coverage_start':'2028-01-01','coverage_end':'2028-01-20'},
               {'patient_id':'A','coverage_start':'2028-01-21','coverage_end':'2028-02-20'}]
        r=S.q18(events,spans,[],'2030-05-27');rows={x['patient_id']:x for x in r['rows']}
        self.assertEqual(set(rows),{'A','B'});self.assertEqual(rows['A']['status'],'next_observed_admission')
        self.assertEqual(rows['A']['days'],30);self.assertEqual(rows['A']['censor_date'],'2028-02-20')
        self.assertEqual(rows['B']['status'],'unknown_index_discharge')

    def test_every_fixed_case_has_explicit_engineering_scoring_with_all_ten_areas_retained(self):
        curriculum=json.loads(S.CURRICULUM.read_text());config=json.loads(S.SCORING.read_text())
        self.assertEqual(set(config['cases']),{c['id']for c in curriculum['cases']})
        self.assertEqual(len(config['cases']),25)
        self.assertEqual({a for c in curriculum['cases']for a in c['areas']},set(range(1,11)))
        for case in config['cases']:
            self.assertGreaterEqual(len(S.rubric(case)),8)
            self.assertEqual(S.score_review(case,{})['engineering_state'],'unassessed')
            self.assertFalse(S.score_review(case,{})['clinical_qualification'])

    def test_cell_comparison_catches_omissions_duplicates_extra_groups_and_wrong_types(self):
        expected=[{'group':None,'count':2,'amount':{'cents':100},'unknown':None}]
        self.assertEqual(S.compare_rows(expected,copy.deepcopy(expected),['group'])['state'],'passed')
        for kind in ['omitted_group','extra_group','wrong_count','wrong_type','omitted_null','extra_unscored_field']:
            observed=copy.deepcopy(expected)
            if kind=='omitted_group':observed=[]
            elif kind=='extra_group':observed.append({'group':'X','count':0})
            elif kind=='wrong_count':observed[0]['count']=3
            elif kind=='wrong_type':observed[0]['count']='2'
            elif kind=='omitted_null':observed[0].pop('unknown')
            else:observed[0]['new_unchecked_quantity']=999
            with self.subTest(kind=kind):self.assertEqual(S.compare_rows(expected,observed,['group'])['state'],'failed')
        with self.assertRaises(ValueError):S.compare_rows(expected,expected*2,['group'])

    def test_no_engineering_disposition_or_hidden_fault_can_pass_without_evidence(self):
        with self.assertRaises(ValueError):S.score_review('Q01',{'quantities':{'state':'passed'}})
        with self.assertRaises(ValueError):S.score_review('Q01',{'invented_check':{'state':'failed'}})
        reference={'path':str(S.SCORING.relative_to(ROOT)),'sha256':S.file_sha(S.SCORING)}
        review={k:{'state':'passed','reason':'Authored contract-check fixture only','evidence':[reference]}for k in S.rubric('Q01')}
        r=S.score_review('Q01',review);self.assertEqual(r['engineering_state'],'passed');self.assertFalse(r['clinical_qualification'])
        review['quantities']['state']='failed';self.assertEqual(S.score_review('Q01',review)['engineering_state'],'failed')
        review['quantities']['evidence'][0]['sha256']='changed'
        with self.assertRaises(ValueError):S.score_review('Q01',review)


if __name__=='__main__':unittest.main()
