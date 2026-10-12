"""Documented-state semantics, complete membership and review-aware reuse."""
import copy
import unittest
import json
import zlib
import test_interpretation_state as interpretation_tests
from clinical_core.agere.clinical import source_state as ss, interpretation_state as interp
from clinical_core.agere.clinical.graph_workbench import source_state_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from test_assertion_reconciliation import source, receipt, annotation, ASOF
from test_interpretation_state import fixture
from test_semantic_reader import LABELS

P={'streams':['notes','financial'],'concept':'financial hardship','relation':'reported_barrier',
   'clock':'source_statement_date','filter':'affirmed'}


def interpreted(records,events=None):
    base=receipt(records,events);base['id']='fixture'
    row=interp.patient_receipt(base,[],[]);row['id']='interpretation'
    return row


class SourceStateTests(unittest.TestCase):
    def test_positive_negative_and_missing_are_different(self):
        for records,expected in [([source()],True),([source(value=False)],False),([],None)]:
            row=ss.project(interpreted(records),P)
            self.assertIs(ss.membership(row,'affirmed'),expected)
            self.assertFalse(row['current_clinical_state_established'])

    def test_same_date_contradiction_is_unknown_without_voting(self):
        row=ss.project(interpreted([source('a'),source('b'),source('c',False)]),P)
        self.assertEqual(row['status'],'conflicting');self.assertIsNone(ss.membership(row,'affirmed'))

    def test_explicit_clock_cannot_silently_be_replaced(self):
        row=ss.project(interpreted([source(assessment_date='2026-06-01')]),P)
        self.assertEqual(row['selected_date'],'2026-07-04')
        row=ss.project(interpreted([source(assessment_date='2026-06-01')]),{**P,'clock':'explicit_clinical_date'})
        self.assertEqual(row['selected_date'],'2026-06-01');self.assertIs(ss.membership(row,'affirmed'),True)
        row=ss.project(interpreted([source()]),{**P,'clock':'explicit_clinical_date'})
        self.assertIsNone(ss.membership(row,'affirmed'))

    def test_latest_documented_account_keeps_prior_not_current_truth(self):
        row=ss.project(interpreted([source('old',False,date='2026-06-01'),source('new',True)]),P)
        self.assertEqual(row['value'],'affirmed');self.assertEqual(len(row['prior_assertion_ids']),1)

    def test_machine_interpretation_cannot_satisfy_or_disprove_binding(self):
        r,e=annotation(polarity='negated');row=ss.project(interpreted([source(),r],[e]),P)
        self.assertEqual(row['status'],'unresolved_interpretation');self.assertIsNone(ss.membership(row,'affirmed'))

    def test_consistent_review_can_supply_documented_report(self):
        base,r=fixture(review_status='accepted');row=interp.patient_receipt(base,[r],[]);row['id']='fixture'
        result=ss.project(row,P)
        self.assertEqual(result['status'],'documented');self.assertEqual(result['value'],'affirmed')

    def test_review_conflict_cannot_fall_back_to_machine(self):
        base,a=fixture(review_status='accepted');_,b=fixture({**LABELS,'statement':'patient_negated'},'corrected','d1')
        row=interp.patient_receipt(base,[a,b],[]);row['id']='fixture'
        self.assertEqual(ss.project(row,P)['status'],'conflicting')

    def test_copied_history_cannot_become_new_current_report(self):
        base,r=fixture(review_status='accepted');r['context']['copied_history']=True
        row=interp.patient_receipt(base,[r],[]);row['id']='fixture'
        self.assertIsNone(ss.membership(ss.project(row,P),'affirmed'))

    def test_other_person_and_retracted_source_do_not_match(self):
        for label in ('other_person','hypothetical','retracted'):
            base,r=fixture({**LABELS,'statement':label},'corrected');row=interp.patient_receipt(base,[r],[]);row['id']='fixture'
            result=ss.project(row,P)
            self.assertIsNone(ss.membership(result,'affirmed'));self.assertFalse(result['selected_assertions'])

    def test_orders_are_not_use_and_referrals_are_not_completed_care(self):
        for concept,relation,requested in [('medication bb','prescription_order','reported_use'),('care nephrology','care_order_plan','care_completion')]:
            row=interpreted([source()]);a=row['effective_assertions'][0];a.update(concept=concept,relation=relation)
            result=ss.project(row,{**P,'concept':concept,'relation':requested})
            self.assertEqual(result['status'],'unobserved');self.assertEqual(result['related_evidence'][0]['relation'],relation)

    def test_dose_change_is_not_hold_or_maximum_tolerated_dose(self):
        row=interpreted([source()]);row['effective_assertions'][0].update(concept='medication bb',relation='dose_change')
        self.assertIsNone(ss.membership(ss.project(row,{**P,'concept':'medication bb','relation':'treatment_hold_stop'}),'affirmed'))
        with self.assertRaises(ValueError):ss.validate({**P,'concept':'medication bb','relation':'maximum_tolerated_dose'})

    def test_unassessed_units_do_not_disappear_when_report_documented(self):
        row=interpreted([source()]);row['coverage']['units_without_in_scope_assertions']=5
        result=ss.project(row,P)
        self.assertEqual(result['source_coverage']['units_without_in_scope_assertions'],5)
        self.assertTrue(any('unmapped' in x for x in result['next_evidence_steps']))

    def test_cross_patient_evidence_rejected(self):
        row=interpreted([source()]);row['effective_assertions'][0]['patient_id']='other'
        with self.assertRaises(ValueError):ss.project(row,P)

    def test_examples_compile_locally(self):
        for e in source_state_examples():
            plan,route=local_plan(e['question'],ASOF)
            self.assertEqual(route,'registered_example');self.assertEqual(plan['nodes'][1]['operation'],'source_state')


class SourceStateCacheTests(unittest.TestCase):
    review=interpretation_tests.PatientCacheTests.review
    tearDown=interpretation_tests.PatientCacheTests.tearDown

    def setUp(self):
        interpretation_tests.PatientCacheTests.setUp(self)
        from clinical_core.agere.clinical import assertion_reconciliation as ar
        db=ar.storage(self.root)
        for row in self.base.values():
            row['identity']='base-identity'
            db.execute('UPDATE receipts SET payload=? WHERE id=?',(zlib.compress(json.dumps(row).encode()),row['id']))
        db.commit();db.close()
    def test_source_states_reuse_unaffected_patient_and_expire_old_review(self):
        yes,unknown,first=ss.execute(self.root,['p','q'],P)
        self.assertEqual(yes,set());self.assertEqual(unknown,{'p','q'})
        self.review(self.a)
        yes,unknown,second=ss.execute(self.root,['p','q'],P)
        self.assertEqual(yes,{'p'});self.assertEqual(unknown,{'q'})
        self.assertEqual(second['processing_counts']['patient_cache_hits'],1)
        self.assertEqual(second['processing_counts']['patient_states_computed'],1)
        old=next(p for p in first['patients'] if p['patient_id']=='p')
        with self.assertRaises(ValueError):ss.inspect(self.root,old['id'])
        row=ss.inspect(self.root,next(p for p in second['patients'] if p['patient_id']=='p')['id'])
        self.assertEqual(row['value'],'affirmed')
        self.review(self.a,status='unresolved',revision=1)
        yes,unknown,third=ss.execute(self.root,['p'],P)
        self.assertEqual(yes,set());self.assertEqual(unknown,{'p'})

    def test_filter_reuses_state_and_empty_stays_empty(self):
        self.review(self.a);ss.execute(self.root,['p','q'],P)
        yes,unknown,row=ss.execute(self.root,['p','q'],{**P,'filter':'unresolved'})
        self.assertEqual(yes,{'q'});self.assertEqual(unknown,set())
        self.assertEqual(row['processing_counts']['patient_cache_hits'],2)
        yes,unknown,row=ss.execute(self.root,[],P)
        self.assertEqual(row['patients_evaluated'],0);self.assertFalse(yes|unknown)


if __name__=='__main__':unittest.main()
