"""Temporal leakage, uncertainty and composition tests; no clinical validation."""
import copy
import unittest
from clinical_core.agere.clinical import pre_event_state as pre, source_state, interpretation_state, assertion_reconciliation, analysis_graph, outcomes
from test_native_assertions import record
from test_task_context import META

P={'streams':['financial_screens'],'concept':'financial hardship','relation':'reported_barrier','clock':'source_statement_date','filter':'affirmed'}
AP={'contract':'admission_discharge_source_30d','index_from':'2026-08-01','index_through':'2026-08-31','available_by':'2026-09-30','filter':'all'}
SPEC=outcomes.definitions()[AP['contract']]


def fixture(records=None):
    base=assertion_reconciliation.patient_receipt('p',records or [record()],[],{'streams':P['streams'],'concepts':[P['concept']]},META);base['id']='base'
    receipt=interpretation_state.patient_receipt(base,[],[]);receipt['id']='interpreted'
    event={'id':'index','patient_id':'p','source':{'patient_id':'p'},'arrival_date':'2026-08-02','clinical_date':'2026-08-01','clinical_date_evidence':[]}
    anchor={'id':'outcome','patient_id':'p','indexed':True,'status':'incomplete_followup','index':{'event_id':'index','date':'2026-08-01','evidence_ids':['index']},'evidence':{'index':event}}
    return receipt,anchor


def project(records=None, **kw):
    receipt,anchor=fixture(records)
    return pre.project(receipt,anchor,SPEC,AP,{**P,**kw})


class PreEventTests(unittest.TestCase):
    def test_later_negative_does_not_erase_prior_affirmation(self):
        row=project([record(),record(record_id='later',value=False,event_date='2026-08-15',available_date='2026-08-15')])
        self.assertEqual(row['value'],'affirmed');self.assertEqual(len(row['excluded_assertions']),1)
        self.assertEqual(row['excluded_assertions'][0]['reason'],'documented_after_index')
        self.assertFalse(row['temporal_scope']['historical_system_knowledge_established'])

    def test_backdated_but_late_arriving_report_is_excluded(self):
        row=project([record(value=False),record(record_id='late',available_date='2026-08-05')])
        self.assertEqual(row['value'],'negated');self.assertEqual(row['excluded_assertions'][0]['reason'],'received_after_index')

    def test_earlier_requested_cutoff_excludes_late_arrival(self):
        r,a=fixture([record(available_date='2026-09-10')])
        row=pre.project(r,a,SPEC,{**AP,'available_by':'2026-08-31'},P)
        self.assertEqual(row['status'],'unobserved');self.assertEqual(row['excluded_assertions'][0]['reason'],'after_receipt_cutoff')

    def test_same_day_is_unknown_for_either_clock(self):
        for extra in ({'event_date':'2026-08-01'},{'available_date':'2026-08-01'}):
            row=project([record(value=False),record(record_id='same',**extra)])
            self.assertIsNone(source_state.membership(row,'affirmed'));self.assertEqual(row['status'],'unresolved_temporal_evidence')
            self.assertEqual(row['pending_assertions'][0]['reason'],'same_day_order_unknown')

    def test_missing_clocks_do_not_fall_back_to_each_other(self):
        for field in ('source_date','arrival_date'):
            r,a=fixture();r['effective_assertions'][0]['clocks'][field]=None
            row=pre.project(r,a,SPEC,AP,P)
            self.assertIsNone(row['value']);self.assertTrue(row['pending_assertions'])

    def test_known_late_evidence_does_not_poison_with_other_missing_clock(self):
        r,a=fixture();r['effective_assertions'][0]['clocks'].update(source_date='2026-09-01',arrival_date=None)
        row=pre.project(r,a,SPEC,AP,P)
        self.assertEqual(row['status'],'unobserved');self.assertFalse(row['pending_assertions'])

    def test_unknown_anchor_retained_and_never_negative(self):
        for status,indexed in [('no_observed_index',False),('unresolved_index_timing',None),('ambiguous_index_identity',None)]:
            r,a=fixture();a.update(status=status,indexed=indexed,index=None)
            row=pre.project(r,a,SPEC,AP,P)
            self.assertEqual(row['status'],'temporal_anchor_unresolved');self.assertIsNone(source_state.membership(row,'affirmed'))
            self.assertTrue(source_state.membership(row,'all'));self.assertEqual(row['anchor']['problem'],status)

    def test_unavailable_index_receipt_is_not_used(self):
        r,a=fixture();a['evidence']['index']['arrival_date']='2026-10-01'
        row=pre.project(r,a,SPEC,AP,P);self.assertEqual(row['anchor']['problem'],'index_receipt_unavailable_at_cutoff')

    def test_clinical_anchor_dependency_must_be_received_by_cutoff(self):
        r,a=fixture();a['evidence']['index']['clinical_date_evidence']=[{'patient_id':'p','available_date':'2026-10-01'}]
        row=pre.project(r,a,{**SPEC,'basis':'clinical'},AP,P)
        self.assertEqual(row['anchor']['problem'],'clinical_index_clock_unavailable_at_cutoff')

    def test_cross_patient_anchor_assertion_and_dependency_rejected(self):
        for target in ('anchor','assertion','event','source'):
            r,a=fixture()
            if target=='anchor':a['patient_id']='other'
            elif target=='assertion':r['effective_assertions'][0]['patient_id']='other'
            elif target=='event':a['evidence']['index']['source']['patient_id']='other'
            else:next(iter(r['sources'].values()))['patient_id']='other'
            with self.assertRaisesRegex(ValueError,'Cross-patient'):pre.project(r,a,SPEC,AP,P)

    def test_current_review_does_not_create_historical_knowledge(self):
        r,a=fixture();r['effective_assertions'][0]['authority']='source_review'
        row=pre.project(r,a,SPEC,AP,P)
        self.assertIsNone(row['value']);self.assertEqual(row['pending_assertions'][0]['reason'],'review_context_not_time_qualified')

    def test_future_bad_native_mapping_cannot_poison_prior_evidence(self):
        row=project([record(),record(record_id='bad',respondent='unknown',event_date='2026-09-01',available_date='2026-09-01')])
        self.assertEqual(row['value'],'affirmed');self.assertFalse(row['native_mapping_gaps'])
        self.assertEqual(row['source_coverage']['native_fields']['counts']['respondent_unresolved'],1)

    def test_same_date_opposition_and_prior_unobserved_are_preserved(self):
        self.assertEqual(project([record(),record(record_id='no',value=False)])['status'],'conflicting')
        row=project([record(event_date='2026-09-01',available_date='2026-09-01')])
        self.assertEqual(row['status'],'unobserved');self.assertIsNone(source_state.membership(row,'negated'))

    def test_no_implicit_clinical_clock_binding(self):
        self.assertIsNone(project(clock='explicit_clinical_date')['value'])

    def test_projection_does_not_mutate_received_interpretation(self):
        r,a=fixture();before=copy.deepcopy(r);pre.project(r,a,SPEC,AP,P);self.assertEqual(r,before)

    def test_graph_requires_exact_all_state_outcome_and_explicit_predicate(self):
        base={'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':{'all':[],'any':[],'exclude':[]}},'purpose':'Keep all patients'}
        index={'id':'index','operation':'outcome','inputs':['population'],'parameters':AP,'purpose':'Select declared event'}
        node={'id':'prior','operation':'pre_event_state','inputs':['index'],'parameters':P,'purpose':'Reconstruct prior reports'}
        plan={'interpretation':'Prior evidence','coverage':'supported_observation','unmet_requirements':[],'nodes':[base,index,node]}
        self.assertTrue(analysis_graph.validate(plan,'2026-09-30'))
        for changed in ('direct','filtered','all_overlap'):
            bad=copy.deepcopy(plan)
            if changed=='direct':bad['nodes'][2]['inputs']=['population']
            elif changed=='filtered':bad['nodes'][1]['parameters']['filter']='indexed'
            else:
                bad['nodes'][2]['parameters']['filter']='all'
                bad['nodes'].append({'id':'overlap','operation':'intersect','inputs':['prior','population'],'parameters':{},'purpose':'Invalid overlap'})
            with self.assertRaises(ValueError):analysis_graph.validate(bad,'2026-09-30')


if __name__=='__main__':unittest.main()
