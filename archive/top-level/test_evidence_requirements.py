"""Decision composition, source-role boundaries and complete population reuse."""
import copy
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import evidence_requirements as er, source_structure as structure
from clinical_core.agere.clinical import source_state as state, interpretation_state as interpretation
from clinical_core.agere.clinical.graph_workbench import requirement_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from clinical_core.agere.clinical.analysis_graph import validate
from test_assertion_reconciliation import source, annotation, receipt, ASOF
from test_source_state import interpreted
import test_source_state as source_state_tests


class DocumentStructureTests(unittest.TestCase):
    def locate(self,text,quote):
        start=text.index(quote)
        return structure.locate(text,start,start+len(quote))

    def test_signature_and_body_occurrences_are_distinct(self):
        text='Assessment:\nPalliative care consultation completed.\nSigned by: Dr Example\nPalliative Medicine\n'
        body=self.locate(text,'Palliative care');signature=self.locate(text,'Palliative Medicine')
        self.assertEqual(body['role'],'assessment');self.assertFalse(body['clinical_account_excluded'])
        self.assertEqual(signature['role'],'signature');self.assertTrue(signature['clinical_account_excluded'])
        m=signature['markers'][0];self.assertEqual(text[m['start']:m['end']],m['quote'])

    def test_metadata_field_does_not_consume_unsectioned_body(self):
        text='Author: Dr Example\nPatient reports medication use.\n'
        self.assertTrue(self.locate(text,'Dr Example')['clinical_account_excluded'])
        self.assertFalse(self.locate(text,'Patient')['clinical_account_excluded'])

    def test_attested_specialty_line_is_signature_but_unattested_name_is_not(self):
        text='Goals discussed.\n\nDev J. Example, MD | Palliative Medicine\nSigned: 2026-08-30'
        self.assertEqual(self.locate(text,'Palliative')['role'],'signature')
        self.assertFalse(self.locate(text.split('\nSigned:')[0],'Palliative')['clinical_account_excluded'])

    def test_unknown_heading_ends_signature_scope(self):
        text='Signed by: Dr Example\nSpecialty text\nNew clinical section:\nPatient text'
        self.assertEqual(self.locate(text,'Patient')['role'],'unknown_section')
        self.assertFalse(self.locate(text,'Patient')['clinical_account_excluded'])

    def test_role_is_not_a_clinical_classifier(self):
        for header,role in [('Family history','family_history'),('Medications','medication_list'),('Copied history','copied_history')]:
            r=self.locate(header+':\nMetoprolol','Metoprolol')
            self.assertEqual(r['role'],role);self.assertTrue(r['interpretation_required'])

    def test_unicode_offsets_and_cross_section(self):
        text='😀\nAssessment:\nUses drug.\nSignature:\nDrug Example'
        r=self.locate(text,'Uses drug.');self.assertEqual(text[r['target_start']:r['target_end']],r['target_quote'])
        mixed=structure.locate(text,text.index('Uses'),len(text))
        self.assertEqual(mixed['role'],'cross_section');self.assertFalse(mixed['clinical_account_excluded'])

    def test_signature_proposal_does_not_override_structured_report(self):
        text='Specialty: financial hardship';record,event=annotation(text)
        row=interpreted([source(),record],[event])
        p={'streams':['notes','financial'],'concept':'financial hardship','relation':'reported_barrier','clock':'source_statement_date','filter':'affirmed'}
        projected=state.project(row,p)
        self.assertEqual(projected['status'],'documented')
        self.assertEqual(projected['excluded_assertions'][0]['reason'],'nonclinical_source_role')
        self.assertTrue(any(a.get('source_structure') for a in row['effective_assertions']))


class RequirementLogicTests(unittest.TestCase):
    def test_truth_tables_do_not_treat_unknown_as_false(self):
        for a in (True,False,None):
            for b in (True,False,None):
                values={'a':a,'b':b}
                self.assertIs(er.logic({'all':['a','b']},values),False if False in (a,b) else None if None in (a,b) else True)
                self.assertIs(er.logic({'any':['a','b']},values),True if True in (a,b) else None if None in (a,b) else False)
        self.assertIsNone(er.logic({'not':'a'},{'a':None}))

    def test_positive_negative_missing_and_conflict_produce_distinct_results(self):
        spec=er.definition('affordability_report')
        for records,expected in [([source()],'supported'),([source(value=False)],'contradicted'),([], 'unresolved'),([source('a'),source('b',False)],'unresolved')]:
            row=er.evaluate(interpreted(records),spec)
            self.assertEqual(row['status'],expected)
            self.assertFalse(row['clinical_eligibility_established'])

    def test_undeclared_stream_cannot_satisfy_binding(self):
        spec=er.definition('affordability_report');spec['bindings']['barrier']['source']['streams']=['notes']
        result=er.evaluate(interpreted([source()]),spec)
        self.assertEqual(result['status'],'unresolved')
        self.assertFalse(result['bindings']['barrier']['state']['selected_assertions'])

    def test_missing_input_impact_is_conditional_not_estimated_probability(self):
        spec=er.definition('affordability_report');row=er.evaluate(interpreted([]),spec)
        self.assertTrue(row['acquisition'][0]['can_resolve_alone'])
        self.assertEqual(row['acquisition'][0]['if_binding_resolved'],{'false':False,'true':True})
        spec['bindings']['second']=copy.deepcopy(spec['bindings']['barrier'])
        spec['expression']={'all':['barrier','second']}
        row=er.evaluate(interpreted([]),spec)
        self.assertEqual(row['acquisition'][0]['if_binding_resolved'],{'false':False,'true':None})

    def test_orders_and_requests_do_not_satisfy_completion(self):
        r=source();r.update(source_table='referrals',service='nephrology',referral_date='2026-07-04')
        row=er.evaluate(interpreted([r]),er.definition('nephrology_followthrough'))
        self.assertIs(row['bindings']['plan']['result'],True)
        self.assertIsNone(row['bindings']['completion']['result'])
        self.assertEqual(row['status'],'unresolved')

    def test_fully_dated_accounts_can_support_documentary_contract(self):
        r=source();r.update(source_table='referrals',service='nephrology',referral_date='2026-06-01',attendance_date='2026-07-04',attendance_status='completed')
        row=er.evaluate(interpreted([r]),er.definition('nephrology_followthrough'))
        self.assertEqual(row['status'],'supported')
        self.assertIn('identity and ordering',row['next_step'])

    def test_invalid_definitions_and_model_invented_contracts_rejected(self):
        for expr in ({'sql':'SELECT *'},'missing',{'all':[]},{'all':['barrier'],'any':['barrier']}):
            with self.assertRaises(ValueError):er.validate_expression(expr,{'barrier'})
        with self.assertRaises(ValueError):er.validate({'contract':'invented_eligibility','filter':'supported'})

    def test_every_ui_example_compiles_locally_and_exposes_full_membership(self):
        for example in requirement_examples():
            p=validate(example['graph'],ASOF);plan,route=local_plan(example['question'],ASOF)
            self.assertEqual(route,'registered_example');self.assertEqual(p,plan)
            self.assertEqual(plan['nodes'][1]['operation'],'requirements')
        self.assertEqual(requirement_examples()[-1]['graph']['nodes'][-1]['inputs'],['requirements'])


class RequirementCacheTests(unittest.TestCase):
    setUp=source_state_tests.SourceStateCacheTests.setUp
    tearDown=source_state_tests.SourceStateCacheTests.tearDown
    review=source_state_tests.SourceStateCacheTests.review
    P={'contract':'affordability_report','filter':'supported'}

    def test_review_changes_only_affected_patient_and_revocation_removes_match(self):
        yes,unknown,first=er.execute(self.root,['p','q'],self.P)
        self.assertEqual(unknown,{'p','q'});self.assertFalse(yes)
        self.review(self.a)
        yes,unknown,second=er.execute(self.root,['p','q'],self.P)
        self.assertEqual(yes,{'p'});self.assertEqual(unknown,{'q'})
        self.assertEqual(second['processing_counts']['patient_cache_hits'],1)
        self.assertEqual(second['processing_counts']['patient_contracts_computed'],1)
        with self.assertRaises(ValueError):er.inspect(self.root,first['patients'][0]['id'])
        current=next(x for x in second['patients'] if x['patient_id']=='p')
        self.assertEqual(er.inspect(self.root,current['id'])['status'],'supported')
        self.review(self.a,status='unresolved',revision=1)
        yes,unknown,_=er.execute(self.root,['p'],self.P)
        self.assertFalse(yes);self.assertEqual(unknown,{'p'})

    def test_filter_reuses_patient_results_and_empty_is_empty(self):
        er.execute(self.root,['p','q'],self.P)
        yes,unknown,r=er.execute(self.root,['p','q'],{**self.P,'filter':'unresolved'})
        self.assertEqual(yes,{'p','q'});self.assertFalse(unknown)
        self.assertEqual(r['processing_counts']['patient_cache_hits'],2)
        yes,unknown,r=er.execute(self.root,[],self.P)
        self.assertFalse(yes|unknown);self.assertEqual(r['patients_evaluated'],0)

    def test_program_assessment_carries_requirements_and_refuses_revoked_evidence(self):
        import json,sqlite3
        from clinical_core.agere.clinical import requirement_work as rw,program_work as work
        meta=json.loads((self.root/'run.json').read_text());meta['source_run']='fixture'
        (self.root/'run.json').write_text(json.dumps(meta))
        db=sqlite3.connect(self.root/'stack.sqlite3')
        db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT);')
        db.execute('INSERT INTO patients VALUES (?,?)',('p','{}'))
        analysis={'synthetic':True,'patient_id':'p','source_run':'fixture',
            'workflow':{'as_of':ASOF,'review_candidate':True,'status':'review_required','missing_information':[]}}
        db.execute('INSERT INTO analyses VALUES (?,?,?)',('p','affordability_review',json.dumps(analysis)));db.commit();db.close()
        self.review(self.a);er.execute(self.root,['p'],self.P)
        nodes=[{'id':'rule','parameters':self.P}]
        snap={'rule':er.snapshot(self.root,self.P)}
        outside=rw.contexts(self.root,['p'],nodes,snap,evaluated_patients={'rule':set()})
        self.assertEqual(outside['p']['requirements'][0]['status'],'not_assessed')
        contexts=rw.contexts(self.root,['p'],nodes,snap,evaluated_patients={'rule':{'p'}})
        proposal=work.prepare(self.root,'affordability',['p'],decision_contexts=contexts)
        work.materialize(self.root,proposal['proposal_id'],'ISOLATED TEST FIXTURE')
        item=work.list_items(self.root)['items'][0]
        assessment=work.inspect_item(self.root,item['id'])['assessment']
        self.assertEqual(assessment['decision_context']['requirements'][0]['status'],'supported')
        self.review(self.a,status='unresolved',revision=1)
        with self.assertRaises(ValueError):work.materialize(self.root,proposal['proposal_id'],'ISOLATED TEST FIXTURE')
        with self.assertRaises(work.WorkConflict):work.change(self.root,{
            'item_id':item['id'],'expected_revision':item['revision'],'action':'assign','actor':'ISOLATED TEST FIXTURE',
            'request_id':'stale','data':{'owner':'Fixture owner','due_date':None}})
        er.execute(self.root,['p'],self.P)
        contexts=rw.contexts(self.root,['p'],nodes,{'rule':er.snapshot(self.root,self.P)},evaluated_patients={'rule':{'p'}})
        refreshed=work.prepare(self.root,'affordability',['p'],decision_contexts=contexts)
        result=work.materialize(self.root,refreshed['proposal_id'],'ISOLATED TEST FIXTURE')
        self.assertEqual(result['changes']['evidence_changed'],1)
        self.assertTrue(work.inspect_item(self.root,item['id'])['item']['reassessment_required'])


if __name__=='__main__':unittest.main()
