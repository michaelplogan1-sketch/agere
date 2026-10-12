"""Source-bound documentary semantics and answer coverage, not clinical validation."""
import copy
import unittest
from clinical_core.contracts import digest
from clinical_core.agere.clinical import assertion_reconciliation as ar, interpretation_state as interp, source_state as ss, native_assertions as native, analysis_graph as graph
from test_assertion_reconciliation import source, ASOF
from test_task_context import META


def record(stream='financial_screens', field='difficulty_paying_medical_bills', value=True, **extra):
    r=source();r.pop('difficulty_paying_medical_bills');r.update(source_table=stream,respondent='patient',_warehouse_source={'adapter':native.warehouse.ADAPTER})
    r[field]=value;r.update(extra);return r


def project(records, concept='financial hardship', clock='source_statement_date'):
    p={'streams':sorted({r['source_table'] for r in records}) or ['financial_screens'],'concepts':[concept]}
    base=ar.patient_receipt('p',records,[],p,META);base['id']='base'
    interpreted=interp.patient_receipt(base,[],[]);interpreted['id']='interpretation'
    return base,ss.project(interpreted,{'streams':p['streams'],'concept':concept,'relation':'reported_barrier','clock':clock,'filter':'affirmed'})


class NativeAssertionsTests(unittest.TestCase):
    def test_all_bindings_keep_literal_field_hash_and_reporter(self):
        for stream,field,concept,_ in native.BINDINGS:
            for value in (True,False):
                raw=record(stream,field,value);base,state=project([raw],concept)
                a=base['assertions'][0]
                self.assertEqual(a['source']['record_hash'],digest(raw));self.assertEqual(a['evidence'],{'field':field,'value':value})
                self.assertEqual(a['respondent'],'patient');self.assertEqual(a['clocks']['clinical_date'],None)
                self.assertIs(ss.membership(state,'affirmed'),value);self.assertFalse(state['current_clinical_state_established'])

    def test_proxy_is_reporter_not_new_subject(self):
        base,state=project([record(respondent='parent_or_guardian')])
        self.assertEqual(base['assertions'][0]['subject'],'patient');self.assertEqual(base['assertions'][0]['respondent'],'parent_or_guardian')
        self.assertEqual(state['value'],'affirmed')

    def test_unknown_reporter_other_subject_and_invalid_type_stay_unknown(self):
        for extra,status in [({'respondent':'unknown'},'respondent_unresolved'),({'subject':'relative'},'subject_not_patient'),({'difficulty_paying_medical_bills':'false'},'invalid_literal_type')]:
            raw=record(**extra);base,state=project([raw]);self.assertFalse(base['assertions'])
            self.assertEqual(base['coverage']['native_fields']['counts'],{status:1});self.assertIsNone(ss.membership(state,'affirmed'))

    def test_absent_null_and_false_are_distinct(self):
        for raw in [record(value=None),record()]:
            if raw.get('difficulty_paying_medical_bills') is True:raw.pop('difficulty_paying_medical_bills')
            base,state=project([raw]);self.assertFalse(base['assertions']);self.assertEqual(base['coverage']['native_fields']['counts'],{'field_not_observed':1})
        _,state=project([record(value=False)]);self.assertEqual(state['value'],'negated')

    def test_unqualified_source_cannot_borrow_adapter(self):
        base,state=project([record(_warehouse_source={})]);self.assertFalse(base['assertions']);self.assertIsNone(ss.membership(state,'affirmed'))

    def test_incomplete_received_field_prevents_false_certainty(self):
        bad=record(record_id='bad',respondent='unknown')
        _,state=project([record(),bad]);self.assertEqual(state['status'],'unresolved_interpretation');self.assertIsNone(ss.membership(state,'affirmed'))

    def test_no_date_fallback_from_arrival_or_assessment_alias(self):
        raw=record(event_date=None,assessment_date='2026-08-01')
        for clock in ('source_statement_date','explicit_clinical_date'):
            base,state=project([raw],clock=clock);self.assertIsNone(ss.membership(state,'affirmed'));self.assertIsNone(base['assertions'][0]['clocks']['clinical_date'])

    def test_late_receipt_and_cross_patient_rejected(self):
        for raw in (record(available_date='2099-01-01'),record(patient_id='other')):
            with self.assertRaises(ValueError):project([raw])

    def test_latest_report_and_same_day_conflict_are_documentary(self):
        old=record(record_id='old',value=False,event_date='2026-05-01')
        _,state=project([old,record()]);self.assertEqual(state['value'],'affirmed');self.assertEqual(len(state['prior_assertion_ids']),1)
        _,state=project([record(record_id='no',value=False),record()]);self.assertEqual(state['status'],'conflicting')

    def test_affordability_questions_do_not_cancel_each_other(self):
        rows=[record(),record('social_screens','difficulty_paying_for_care_reported',False,record_id='social')]
        self.assertEqual(project(rows)[1]['value'],'affirmed');self.assertEqual(project(rows,'affordability of care')[1]['value'],'negated')

    def test_transport_mode_is_not_reported_difficulty(self):
        raw=record('social_screens','transport','unreliable');base,state=project([raw],'transport difficulty')
        self.assertFalse(base['assertions']);self.assertIsNone(ss.membership(state,'affirmed'))

    def test_legacy_financial_remains_supported(self):
        self.assertEqual(project([source()])[1]['value'],'affirmed')

    def test_context_accepts_declared_native_concepts_but_rejects_inventions(self):
        import tempfile, json, sqlite3
        from pathlib import Path
        from unittest.mock import patch
        from clinical_core.agere.clinical import context_builder as ctx
        from test_task_context import PARAMS
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'run.json').write_text(json.dumps({'synthetic':True,'snapshot':META['snapshot'],'source_digest':'fixture'}))
            def connect(_):
                db=sqlite3.connect(':memory:');db.execute('CREATE TABLE events(stream TEXT,concept TEXT)');return db,{}
            with patch.object(ctx.longitudinal,'connect',side_effect=connect), patch('clinical_core.agere.clinical.program_registry.packages',return_value=[]):
                params={**PARAMS,'streams':['financial_screens'],'concepts':sorted(native.CONCEPTS)}
                identity=ctx.dependency_identity(root,params)[0]
                self.assertTrue(identity)
                with self.assertRaisesRegex(ValueError,'Unmapped context concept'):
                    ctx.dependency_identity(root,{**params,'concepts':['invented health state']})

    def test_all_state_retention_cannot_masquerade_as_overlap(self):
        base={'id':'p','operation':'cohort','inputs':[],'parameters':{'specification':{'all':[],'any':[],'exclude':[]}},'purpose':'Entire denominator'}
        screen={'id':'s','operation':'source_state','inputs':['p'],'parameters':{'streams':['social_screens'],'concept':'food difficulty','relation':'reported_barrier','clock':'source_statement_date','filter':'all'},'purpose':'All documentary states'}
        appointment={'id':'a','operation':'appointment_followthrough','inputs':['p'],'parameters':{'available_by':ASOF,'filter':'all'},'purpose':'All appointment dispositions'}
        for op,inputs in [('intersect',['s','a']),('compare',['p','a','s'])]:
            plan={'interpretation':'Inspect overlap','coverage':'supported_observation','unmet_requirements':[],'nodes':[base,screen,appointment,{'id':'x','operation':op,'inputs':inputs,'parameters':{},'purpose':'Actual predicate overlap'}]}
            with self.assertRaisesRegex(ValueError,'All-state retention'):
                graph.validate(plan,ASOF)
            plan['nodes'][1]=copy.deepcopy(screen);plan['nodes'][1]['parameters']['filter']='affirmed'
            plan['nodes'][2]=copy.deepcopy(appointment);plan['nodes'][2]['parameters']['filter']='explicit_no_show'
            self.assertTrue(graph.validate(plan,ASOF))

    def test_answer_coverage_qualifies_plan_support(self):
        plan={'coverage':'supported_observation','unmet_requirements':[]}
        row={'node':{'operation':'source_state','id':'social','parameters':{'concept':'food difficulty'}},'result':{'status_counts':{'unobserved':2},'evidence_coverage':{'patients_without_scoped_records':1,'patients_with_unmapped_received_units':1}}}
        out=graph.answer_coverage(plan,[row]);self.assertEqual(out['status'],'partial_evidence');self.assertFalse(out['whole_question_answer_established']);self.assertEqual(plan['coverage'],'supported_observation')

if __name__=='__main__':unittest.main()
