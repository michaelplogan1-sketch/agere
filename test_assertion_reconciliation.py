"""Failure-boundary tests for shared documentary reconciliation, not model accuracy."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_core.contracts import digest
from clinical_core.agere.clinical import assertion_reconciliation as ar, context_builder as ctx, longitudinal as lg
from clinical_core.agere.clinical.graph_workbench import assertion_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from clinical_core.agere.clinical.analysis_graph import validate as validate_graph
from test_task_context import record, META

ASOF = '2026-09-30'
P = {'streams':['notes','financial','medication_orders','medication_history','referrals'],
     'concepts':['financial hardship','medication bb','outside care','care nephrology']}


def source(rid='f', value=True, date='2026-07-04', **extra):
    r=record('',rid=rid);r.pop('text');r.update(source_table='financial',event_date=date,
        available_date=date,difficulty_paying_medical_bills=value,**extra)
    return r


def annotation(text='Cost is a concern.',rid='n',polarity='affirmed',day='2026-07-04',**extra):
    r=record(text,rid=rid);r.update(event_date=day,available_date=day)
    event={'id':'annotation-'+rid,'patient_id':'p','kind':'text','stream':'notes','concept':'financial hardship',
           'relation':'clinical_observation','subject':'patient','assertion':polarity,'time':'current',
           'source':{**lg.source(r),'field':'text','start':0,'end':len(text),'quote':text},
           'record_hash':digest(r),'reader_model_identity':'fixture-only','clinical_date':None,
           **lg.narrative_reference(text,0,len(text),ASOF),**extra}
    return r,event


def receipt(records,events=None,p=P):
    ev=[e for r in records for e in lg.record_events(r,ASOF)]+(events or [])
    return ar.patient_receipt('p',records,ev,p,META)


class AssertionTests(unittest.TestCase):
    def test_explicit_false_is_negative_report_but_missing_is_unknown(self):
        r=receipt([source(value=False)])
        self.assertEqual(r['assertions'][0]['polarity'],'negated')
        self.assertEqual(r['assertions'][0]['authority'],'literal_structured_field')
        missing=source();missing.pop('difficulty_paying_medical_bills')
        r=receipt([missing]);self.assertFalse(r['assertions']);self.assertEqual(r['status'],'no_mapped_assertions')
        self.assertFalse(r['current_clinical_state_established'])

    def test_string_false_is_not_coerced_to_boolean(self):
        r=receipt([source(value='False')]);self.assertFalse(r['assertions'])

    def test_same_day_conflict_cannot_be_voted_away(self):
        rows=[source('yes'+str(i)) for i in range(4)]+[source('no',False)]
        r=receipt(rows);self.assertEqual(r['groups'][0]['status'],'same_date_conflict')
        self.assertEqual(r['groups'][0]['latest_by_clock']['source_statement_date']['status'],'opposing_accounts')
        self.assertFalse(r['groups'][0]['independent_corroboration_established'])
        self.assertIn('resolve_opposing_accounts',[s['kind'] for s in r['next_evidence_steps']])

    def test_changed_report_is_not_same_date_conflict(self):
        r=receipt([source('older',True,'2026-07-04'),source('newer',False,'2026-08-04')])
        g=r['groups'][0];self.assertEqual(len(g['documentary_changes']),1)
        self.assertEqual(g['latest_by_clock']['source_statement_date']['status'],'negated_account')
        self.assertEqual(r['counts']['same_date_conflict_groups'],0)

    def test_clinical_and_documentary_clock_are_separate(self):
        r=receipt([source('assessment',True,assessment_date='2026-06-01'),source('undated',False)])
        g=r['groups'][0];self.assertEqual(set(g['latest_by_clock']),{'source_statement_date','explicit_clinical_date'})
        self.assertEqual(r['counts']['same_date_conflict_groups'],0)
        self.assertFalse(r['current_clinical_state_established'])

    def test_machine_and_literal_authorities_remain_distinct(self):
        n,e=annotation(polarity='negated');r=receipt([source(),n],[e])
        self.assertEqual(r['counts']['machine_proposals'],1)
        self.assertEqual(r['groups'][0]['status'],'same_date_conflict')
        self.assertEqual(r['assertions'][-1]['semantic_validation'],'pending')

    def test_history_does_not_use_note_date(self):
        n,e=annotation('Historical cost concern.',day='2026-09-01',time='historical')
        r=receipt([n],[e]);g=r['groups'][0]
        self.assertEqual(g['latest_by_clock'],{})
        self.assertEqual(g['excluded_from_dated_accounts'][0]['reason'],'documentary_date_unresolved')

    def test_literal_date_on_copied_history_does_not_refresh(self):
        n,e=annotation('Cost concern [2026-01-03]',day='2026-09-01',time='historical',copied_history=True)
        r=receipt([n],[e]);g=r['groups'][0]
        self.assertEqual(set(g['latest_by_clock']),{'literal_reference_date'})
        self.assertEqual(g['dated_accounts'][0]['date'],'2026-01-03')

    def test_repeated_text_not_refreshed_or_counted_independent(self):
        n1,e1=annotation(rid='n1',day='2026-05-01')
        n2,e2=annotation(rid='n2',day='2026-09-01')
        r=receipt([n1,n2],[e1,e2]);g=r['groups'][0]
        self.assertEqual(g['latest_by_clock']['source_statement_date']['date'],'2026-05-01')
        self.assertEqual(g['excluded_from_dated_accounts'][0]['reason'],'repeated_text_cannot_refresh_state')
        self.assertEqual(len(g['repeated_statement_groups']),1)

    def test_other_person_and_hypothetical_do_not_enter_dated_accounts(self):
        n1,e1=annotation(rid='n1',subject='family')
        n2,e2=annotation(rid='n2',polarity='possible')
        r=receipt([n1,n2],[e1,e2]);self.assertFalse(r['groups'][0]['dated_accounts'])

    def test_future_reference_never_substitutes_note_date(self):
        n,e=annotation('Cost concern [2027-01-01]')
        r=receipt([n],[e]);self.assertFalse(r['groups'][0]['dated_accounts'])

    def test_source_span_and_hash_changes_rejected(self):
        n,e=annotation();e['source']['quote']='Invented'
        with self.assertRaises(ValueError):receipt([n],[e])
        n,e=annotation();n['text']='Changed'
        with self.assertRaises(ValueError):receipt([n],[e])

    def test_other_patient_and_duplicate_assertion_rejected(self):
        n,e=annotation();r=receipt([n],[e]);a=r['assertions'][0]
        with self.assertRaises(ValueError):ar.reconcile('someone_else',[a],ASOF)
        with self.assertRaises(ValueError):ar.reconcile('p',[a,a],ASOF)

    def test_order_fill_and_not_observed_use_remain_different(self):
        order=source('o');order.update(source_table='medication_orders',drug_class='bb',drug='metoprolol')
        fill=source('d');fill.update(source_table='medication_history',drug_class='bb',drug='metoprolol',medication_taking_observed=False)
        r=receipt([order,fill]);relations={a['relation'] for a in r['assertions']}
        self.assertEqual(relations,{'prescription_order','dispense_record','use_observation_coverage'})
        self.assertFalse(r['current_clinical_state_established'])
        coverage=next(a for a in r['assertions'] if a['relation']=='use_observation_coverage')
        self.assertEqual(coverage['polarity'],'negated')

    def test_care_order_is_not_completion(self):
        r=source();r.update(source_table='referrals',service='nephrology')
        result=receipt([r]);self.assertEqual(result['assertions'][0]['relation'],'care_order_plan')
        r.update(attendance_status='not_attended',attendance_date='2026-07-03')
        result=receipt([r]);completion=next(a for a in result['assertions'] if a['relation']=='care_completion')
        self.assertEqual(completion['polarity'],'negated')
        self.assertEqual(completion['clocks']['clinical_date'],'2026-07-03')

    def test_unannotated_narrative_remains_visible_as_unmapped(self):
        r=receipt([record('An unrecognized but potentially relevant statement.')])
        self.assertEqual(r['coverage']['units_without_in_scope_assertions'],2)
        self.assertFalse(r['coverage']['complete_semantic_interpretation'])
        self.assertIn('interpret_unmapped_evidence',[s['kind'] for s in r['next_evidence_steps']])

    def test_declared_scope_is_not_widened(self):
        p={'streams':['financial'],'concepts':['outside care']}
        r=receipt([source(),record('Outside care occurred.')],p=p)
        self.assertFalse(r['assertions']);self.assertEqual(r['coverage']['received_source_units'],1)

    def test_invalid_parameters_fail_early(self):
        for p in ({}, {'streams':[],'concepts':['outside care']}, {'streams':['notes'],'concepts':['x','x']}):
            with self.assertRaises(ValueError):ar.validate(p)

    def test_future_structured_assessment_cannot_fall_back_to_note_clock(self):
        r=receipt([source(assessment_date='2027-01-01')])
        self.assertFalse(r['groups'][0]['dated_accounts'])
        self.assertEqual(r['assertions'][0]['clinical_date_status'],'unresolved')

    def test_attendance_date_does_not_date_referral_order(self):
        r=source();r.update(source_table='referrals',service='nephrology',attendance_status='completed',attendance_date='2026-06-01')
        result=receipt([r]);order=next(a for a in result['assertions'] if a['relation']=='care_order_plan')
        self.assertIsNone(order['clocks']['clinical_date'])

    def test_duplicate_source_records_rejected(self):
        r=source()
        with self.assertRaises(ValueError):receipt([r,r])

    def test_specialist_binding_does_not_promote_machine_agreement(self):
        from test_context_passes import fixture
        bundle,_=fixture(1);bundle['id']='bundle'
        unit=bundle['passes'][0]['evidence'][0]['id']
        review={'bundle_id':'bundle','patient_id':'p','proposals':[{'pass_number':1,'source_unit_id':unit,'label':'affirmed'}]}
        reconciled={'bundle_id':'bundle','patient_id':'p','assertions':[{'id':'assertion','source_unit_id':unit}]}
        result=ar.bind_specialist_review(bundle,review,reconciled)
        self.assertEqual(result['support_bindings'][0]['related_assertion_ids'],['assertion'])
        self.assertEqual(result['support_bindings'][0]['semantic_acceptance'],'pending')
        self.assertFalse(result['automatic_acceptance'])
        with self.assertRaises(ValueError):ar.bind_specialist_review(bundle,{**review,'patient_id':'other'},reconciled)

    def test_all_new_examples_compile_without_hosted_planning(self):
        for example in assertion_examples():
            validate_graph(example['graph'],ASOF)
            plan,key=local_plan(example['question'],ASOF)
            self.assertEqual(key,'registered_example');self.assertIsNotNone(plan)


if __name__=='__main__':unittest.main()
