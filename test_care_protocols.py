"""Clinical input meaning, review provenance and complete-population integration."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import clinical_inputs as ci, care_protocols as cp
from clinical_core.agere.clinical import selection_context as sc, program_work as work
from clinical_core.agere.clinical.graph_workbench import protocol_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from clinical_core.agere.clinical.analysis_graph import validate, execute
from test_clinical_state import record, ASOF
from test_source_state import interpreted, SourceStateCacheTests
from test_assertion_reconciliation import source
from test_analysis_graph import GraphExecution, graph, node, empty, WINDOW


def lab(rid='r',value=25,**kw):
    return record(rid=rid,table='labs',test='egfr',value=value,unit='ml/min/1.73m2',status='final',**kw)


def binding(**kw):
    return {'kind':'measurement','label':'test','measure':'egfr','op':'lt','value':30,
            'clock':'source_statement_date','selection':'latest',**kw}


def reduce(records,**kw):
    return ci.measurement_binding(ci.measurements('p',records,[],ASOF).get('egfr',[]),binding(**kw),ASOF)


def narrative(text='EF 35%',**kw):
    r=record(table='notes',text=text);quote='EF 35%';start=text.index(quote)
    f={'fact_id':'f','patient_id':'p','concept':'ef','value':{'lower':35,'upper':35,'unit':'%'},
       'assertion':'affirmed','subject':'patient','temporality':'current','method':'context_rules',
       'evidence':[{'record_id':'r','field':'text','quote':quote,'start':start,'end':start+len(quote)}],**kw}
    return r,{'source_bound_facts':[f]}


class InputMeaning(unittest.TestCase):
    def test_absence_is_unknown_and_literal_negative_is_not_missing(self):
        self.assertIsNone(reduce([])['value'])
        self.assertIs(reduce([lab(value=50)])['value'],False)
        self.assertIs(reduce([lab()])['value'],True)

    def test_interval_is_not_midpoint_and_strict_boundary_is_preserved(self):
        self.assertIsNone(ci.interval_test(25,35,'lt',30))
        self.assertIs(ci.interval_test(30,30,'lt',30),False)
        self.assertIs(ci.interval_test(40,40,'le',40),True)

    def test_units_normalize_with_exact_raw_field(self):
        r=record(table='labs',test='creatinine',value=176.8,unit='µmol/L',status='final')
        v=ci.measurements('p',[r],[],ASOF)['creatinine'][0]
        self.assertAlmostEqual(v['lower'],2);self.assertEqual(v['evidence'][0]['raw_unit'],'µmol/L')
        self.assertEqual(v['evidence'][0]['value'],176.8)

    def test_later_unusable_result_cannot_silently_restore_old_result(self):
        for overrides in ({'value':'unknown'},{'unit':'unknown'},{'status':'preliminary'},
                          {'collection_date':'2026-09-01','specimen_date':'2026-09-02'}):
            bad={**lab('bad'),'event_date':'2026-09-20',**overrides}
            r=reduce([lab(),bad]);self.assertIsNone(r['value']);self.assertTrue(r['pending'])

    def test_source_clock_never_substitutes_for_missing_clinical_clock(self):
        r=reduce([lab()],clock='explicit_clinical_date');self.assertIsNone(r['value'])
        self.assertIs(reduce([lab(collection_date='2026-08-30')],clock='explicit_clinical_date')['value'],True)
        self.assertIsNone(reduce([lab(collection_date='2026-08-30'),lab('undated')],clock='explicit_clinical_date')['value'])

    def test_same_date_disagreement_is_not_averaged_or_outvoted(self):
        r=reduce([lab('a'),lab('b'),lab('c',50)])
        self.assertEqual(r['status'],'conflicting');self.assertIsNone(r['value'])

    def test_historical_narrative_nominates_source_review_not_clinical_fact(self):
        r,a=narrative('Copied history:\nEF 35%',temporality='historical')
        observations=ci.measurements('p',[r],[a],ASOF)['lvef']
        history=ci.measurement_binding(observations,binding(measure='lvef',selection='any_history',op='le',value=40),ASOF)
        self.assertIs(history['literal_signal'],True);self.assertIsNone(history['value'])
        self.assertEqual(history['status'],'interpretation_required')
        current=ci.measurement_binding(observations,binding(),ASOF)
        self.assertFalse(current['selected_observations']);self.assertEqual(current['status'],'historical_or_excluded')

    def test_later_improvement_does_not_erase_historical_review_signal(self):
        old=record(rid='old',table='echo',ejection_fraction=35,unit='%',status='final')
        new={**old,'record_id':'new','event_date':'2026-09-20','ejection_fraction':55,'observation_date':'2026-09-19'}
        obs=ci.measurements('p',[old,new],[],ASOF)['lvef']
        self.assertIs(ci.measurement_binding(obs,binding(selection='any_history',value=40),ASOF)['value'],True)
        self.assertIs(ci.measurement_binding(obs,binding(value=40),ASOF)['value'],False)
        # Readiness asks whether the dated value is interpretable, not whether
        # it is still low; improved EF must not be treated as absent evidence.
        self.assertIs(ci.measurement_binding(ci.measurements('p',[new],[],ASOF)['lvef'],binding(value=40,expected='known',clock='explicit_clinical_date'),ASOF)['value'],True)

    def test_other_subject_negation_and_signature_are_not_review_signals(self):
        for text,extra in [('Signed by: EF 35%',{}),('EF 35%',{'subject':'family'}),('EF 35%',{'assertion':'negated'})]:
            r,a=narrative(text,**extra);obs=ci.measurements('p',[r],[a],ASOF)['lvef']
            v=ci.measurement_binding(obs,binding(selection='any_history'),ASOF)
            self.assertIsNone(v['literal_signal']);self.assertTrue(v['excluded'])

    def test_bad_patient_future_arrival_and_tampered_quote_rejected(self):
        for r in ({**lab(),'patient_id':'other'},{**lab(),'available_date':'2026-10-01'}):
            with self.assertRaises(ValueError):ci.measurements('p',[r],[],ASOF)
        r,a=narrative();r['text']='EF 55%'
        with self.assertRaises(ValueError):ci.measurements('p',[r],[a],ASOF)

    def test_known_history_witness_survives_other_unresolved_observation(self):
        self.assertIs(reduce([lab(),{**lab('bad'),'value':'unknown'}],selection='any_history')['value'],True)


class ReviewLogic(unittest.TestCase):
    def test_low_egfr_is_review_not_ckd_or_missing_attendance(self):
        spec=cp.definition('low_egfr_specialist_review')
        r=cp.evaluate({'patient_id':'p','birth_date':'1980-01-01'},[lab()],[],interpreted([]),spec,ASOF)
        self.assertEqual(r['status'],'review_criterion_supported');self.assertIsNone(r['documentary_readiness'])
        self.assertFalse(r['clinical_eligibility_established']);self.assertFalse(r['clinical_execution_authorized'])
        self.assertIsNone(r['bindings']['attendance']['value']);self.assertTrue(r['missing_inputs'])

    def test_adult_condition_and_unknown_age_are_distinct(self):
        spec=cp.definition('low_egfr_specialist_review')
        for birthday,status in [('2020-01-01','criterion_not_supported'),(None,'insufficient_evidence')]:
            r=cp.evaluate({'patient_id':'p','birth_date':birthday},[lab()],[],interpreted([]),spec,ASOF)
            self.assertEqual(r['status'],status)

    def test_historical_ef_creates_conditional_review_only(self):
        note,a=narrative(temporality='historical')
        r=cp.evaluate({'patient_id':'p'},[note],[a],interpreted([]),cp.definition('reduced_ef_therapy_review'),ASOF)
        self.assertEqual(r['status'],'source_interpretation_required')
        self.assertEqual(r['proposed_actions'][1]['status'],'conditional_on_source_review')
        self.assertIsNone(r['trigger'])

    def test_financial_report_denial_missing_and_conflict(self):
        for records,status in [([source()],'review_criterion_supported'),([source(value=False)],'criterion_not_supported'),
                              ([], 'insufficient_evidence'),([source('a'),source('b',False)],'conflicting_evidence')]:
            r=cp.evaluate({'patient_id':'p'},records,[],interpreted(records),cp.definition('affordability_action_review@1'),ASOF)
            self.assertEqual(r['status'],status)

    def test_no_invented_protocols_or_thresholds_in_graph(self):
        for p in ({'protocol':'invented','filter':'all'}, {'protocol':'low_egfr_specialist_review','filter':'eligible'},
                  {'protocol':'low_egfr_specialist_review','filter':'all','threshold':50}):
            with self.assertRaises(ValueError):cp.validate(p)

    def test_registered_examples_compile_without_model(self):
        for ex in protocol_examples():
            plan,route=local_plan(ex['question'],ASOF);self.assertEqual(route,'registered_example')
            self.assertEqual(validate(plan,ASOF)['nodes'][1]['operation'],'protocol_review')


class ProtocolCache(unittest.TestCase):
    tearDown=SourceStateCacheTests.tearDown
    review=SourceStateCacheTests.review
    P={'protocol':'affordability_action_review@1','filter':'supported'}

    def setUp(self):
        SourceStateCacheTests.setUp(self)
        meta=json.loads((self.root/'run.json').read_text());meta['source_run']='fixture'
        (self.root/'run.json').write_text(json.dumps(meta))
        db=sqlite3.connect(self.root/'stack.sqlite3')
        db.executescript('CREATE TABLE patients(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE analyses(patient TEXT,operator TEXT,payload TEXT);')
        for pid in ('p','q'):
            db.execute('INSERT INTO patients VALUES (?,?)',(pid,json.dumps({'patient_id':pid,'birth_date':'1980-01-01'})))
            a={'patient_id':pid,'synthetic':True,'source_run':'fixture','source_bound_facts':[],
               'workflow':{'as_of':ASOF,'review_candidate':True,'status':'review_required','missing_information':[]}}
            db.execute('INSERT INTO analyses VALUES (?,?,?)',(pid,'affordability_review',json.dumps(a)))
        db.commit();db.close()

    def test_review_change_expires_affected_patient_and_preserves_other_cache(self):
        yes,unknown,first=cp.execute(self.root,['p','q'],self.P)
        self.assertFalse(yes);self.assertEqual(unknown,{'p','q'})
        self.review(self.a);yes,unknown,r=cp.execute(self.root,['p','q'],self.P)
        self.assertEqual(yes,{'p'});self.assertEqual(unknown,{'q'})
        self.assertEqual(r['processing_counts']['patient_cache_hits'],1)
        old=next(p for p in first['patients'] if p['patient_id']=='p')
        with self.assertRaises(ValueError):cp.inspect(self.root,old['id'])
        yes,unknown,replay=cp.execute(self.root,['p','q'],{**self.P,'filter':'all'})
        self.assertEqual(replay['processing_counts']['patient_cache_hits'],2)
        self.assertEqual(yes,{'p','q'});self.assertFalse(unknown)
        self.assertEqual(cp.execute(self.root,[],self.P)[2]['patients_evaluated'],0)

    def test_program_provenance_survives_and_revoked_review_blocks_action(self):
        self.review(self.a);cp.execute(self.root,['p'],self.P)
        nodes=[{'id':'rule','parameters':self.P}];preps={'rule':cp.prepare(self.root,self.P)}
        contexts=sc.contexts(self.root,['p','q'],nodes,preps,evaluated_patients={'rule':{'p'}})
        self.assertEqual(contexts['q']['protocols'][0]['status'],'not_assessed')
        sc.verify(self.root,list(contexts.values()))
        proposal=work.prepare(self.root,'affordability',['p'],decision_contexts={'p':contexts['p']})
        work.materialize(self.root,proposal['proposal_id'],'ISOLATED TEST FIXTURE')
        item=work.list_items(self.root)['items'][0]
        a=work.inspect_item(self.root,item['id'])['assessment']
        self.assertEqual(a['decision_context']['protocols'][0]['status'],'review_criterion_supported')
        self.review(self.a,status='unresolved',revision=1)
        with self.assertRaises(ValueError):work.materialize(self.root,proposal['proposal_id'],'ISOLATED TEST FIXTURE')
        with self.assertRaises(work.WorkConflict):work.change(self.root,{
            'item_id':item['id'],'expected_revision':item['revision'],'action':'assign','actor':'ISOLATED TEST FIXTURE',
            'request_id':'stale','data':{'owner':'Fixture owner','due_date':None}})


class ProtocolGraph(unittest.TestCase):
    setUp=GraphExecution.setUp
    tearDown=GraphExecution.tearDown

    def test_full_membership_upstream_unknowns_and_preparation_changes(self):
        west=node('west','cohort',parameters={'specification':{**empty(),'all':[{'kind':'patient','field':'region','op':'eq','value':'west'}]}})
        p={'protocol':'low_egfr_specialist_review','filter':'supported'}
        revision={'identity':'one'}
        with patch.object(cp,'prepare',side_effect=lambda *a:revision.copy()), \
             patch.object(cp,'execute',return_value=({'p0'},{'p1','p2'},{})) as run, \
             patch('clinical_core.agere.clinical.graph_criteria.member_index',return_value=[]), \
             patch('clinical_core.agere.clinical.graph_criteria.dependencies',return_value={}), \
             patch('clinical_core.agere.clinical.graph_decision.source_fingerprint',return_value={}):
            plan=graph([west,node('rule','protocol_review',['west'],p)])
            r=execute(self.stack,plan)
            self.assertEqual(r['results'][-1]['result']['counts'],{'evaluated':6,'matched':1,'not_matched':2,'unknown':3})
            self.assertEqual(run.call_args.args[1],['p0','p1','p2'])
            self.assertTrue(execute(self.stack,plan)['cache_hit'])
            revision['identity']='two';self.assertFalse(execute(self.stack,plan)['cache_hit'])


if __name__=='__main__':unittest.main()
