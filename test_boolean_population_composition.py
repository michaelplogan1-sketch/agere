"""Polarity, condition conjunctions and vocabulary parity on an independent tiny population."""
import copy
import sqlite3
import unittest
from spec_test_support import Warehouse
from clinical_core.agere.clinical import request_language as L, request_plan as R, decision_context as D, sql_query

class BooleanPopulation(unittest.TestCase):
    def setUp(self):
        self.w=Warehouse(); self.addCleanup(self.w.close)
        with sqlite3.connect(self.w.catalog.target) as db:
            db.executemany('INSERT INTO condition VALUES(?,?,1)', [('SYN-A','hypertension'),('SYN-B','hypertension'),('SYN-D','hypertension')])
    def members(self,q):
        plan=L.parse(self.w.catalog,q)
        contract=R.compile_request(self.w.catalog,q,plan)
        with D.scope(q,'2030-05-27'):
            report=sql_query.execute(self.w.path,{'query':contract['membership_query'],'returns':'patient_set'},target=self.w.catalog.target)
        return set(report['patient_ids'])
    def test_no_condition_is_a_real_complement(self):
        self.assertEqual(self.members('Number of hypertensive patients who have no diabetes?'),{'SYN-D'})
        self.assertEqual(self.members('Number of hypertensive patients who have diabetes?'),{'SYN-A','SYN-B'})
    def test_neither_conditions_excludes_either_evidence(self):
        self.assertEqual(self.members('Patients who have neither diabetes nor heart failure'),set())
        self.assertEqual(self.members('Patients who have neither diabetes nor obesity on record'),{'SYN-D'})
    def test_neither_medication_uses_the_current_fill_window(self):
        self.assertEqual(self.members('Patients on neither a statin nor an SGLT2 inhibitor'),{'SYN-B'})
    def test_lists_are_conjunctions_and_either_is_union(self):
        self.assertEqual(self.members('How many patients carry both diabetes and a heart failure diagnosis?'),{'SYN-A','SYN-C'})
        self.assertEqual(self.members('Patients have diabetes, hypertension and heart failure all at once'),{'SYN-A'})
        self.assertEqual(self.members('Patients have diabetes or heart failure (either one)'),{'SYN-A','SYN-B','SYN-C','SYN-D'})
    def test_comma_linked_population_question_is_now_grammar_supported(self):
        self.assertEqual(self.members('Patients, heart failure, how many?'),{'SYN-A','SYN-C','SYN-D'})

    def test_adjectival_concepts_reparse_in_model_specs(self):
        q='Number of hypertensive patients who have no diabetes?'
        plan=L.parse(self.w.catalog,q)
        plan['where']['args'][0]['quote']='hypertensive'
        R.compile_request(self.w.catalog,q,plan)
    def test_who_is_a_subject_not_a_relative_filter(self):
        self.assertEqual(self.members('Who has hypertension but is not flagged with diabetes?'),{'SYN-D'})
    def test_fronted_and_inline_scopes_keep_the_same_ratio_denominator(self):
        for q in ['Of the patients with hypertension, what share also have diabetes?',
                  'What share of patients with hypertension also have diabetes?',
                  'What share of hypertensive patients have diabetes?']:
            with self.subTest(question=q):
                plan=L.parse(self.w.catalog,q);contract=R.compile_request(self.w.catalog,q,plan)
                with D.scope(q,'2030-05-27'):
                    report=sql_query.execute(self.w.path,{'query':contract['query'],'returns':contract['returns']},target=self.w.catalog.target)
                self.assertAlmostEqual(report['value'],200/3)

    def test_parent_quote_cannot_hide_an_omitted_predicate(self):
        q='Patients have hypertension and no diabetes and obesity'
        plan=L.parse(self.w.catalog,q)
        plan['where']['args'].pop()
        with self.assertRaises(R.PlanError): R.compile_request(self.w.catalog,q,plan)

class MedicationEventReason(unittest.TestCase):
    def test_reason_is_bound_to_the_event_despite_a_later_continuation(self):
        from clinical_core.agere.clinical import chart_notes as C, note_frames as F, note_questions as N
        from types import SimpleNamespace
        pred=C.build('Why was aspirin stopped?', '2030-05-27')[0]
        self.assertEqual(pred['detail'],{'event':'stopped'})
        reads=[]
        for rid,day,event,reason in [('stop','2029-01-01','stopped',['side_effect']),('continue','2030-01-01','continued',[])]:
            reads.append({'record_id':rid,'date':day,'event_date':day,'note_type':'progress','source':'clinical_note',
                          'quote':f'aspirin {event}', 'start':0,'end':20,'status':'documented','p':0.99,
                          'detail':{'event':event,'reason':reason},'detail_undecided':[], 'undecided':False})
        nf=SimpleNamespace(validate_predicate=F.validate_predicate,
                           answer_patient=lambda stack,predicate,pid,**kw: N.combine(predicate,reads))
        answer=C.render(SimpleNamespace(),[pred],'SYN-A','2030-05-27',nf=nf)
        self.assertIn('side_effect',str(answer))
        self.assertIn('stop',str(answer['sources']))
        self.assertNotIn('continue',str(answer['sources']))

class ClaimsWindowComposition(unittest.TestCase):
    def test_calendar_and_rolling_windows_keep_the_service_clock_and_boundaries(self):
        w=Warehouse();self.addCleanup(w.close)
        with sqlite3.connect(w.catalog.target) as db:
            db.executescript('CREATE TABLE payer_claim(patient_id,counts_in_metrics,service_from,paid_cents);')
            db.executemany('INSERT INTO payer_claim VALUES(?,?,?,?)',[
                ('SYN-A',1,'2029-05-27',4000),('SYN-A',1,'2029-06-01',3000),
                ('SYN-A',1,'2030-06-01',9000),('SYN-A',0,'2029-06-01',8000)])
        catalog=R.Catalog.load(w.path)
        for wording,expected in [('over the last 12 months',3000),('in 2029',7000)]:
            q='How much have we paid on medical claims '+wording+'?'
            plan=L.parse(catalog,q);contract=R.compile_request(catalog,q,plan)
            with sqlite3.connect(catalog.target) as db:
                self.assertEqual(db.execute(contract['query']).fetchone()[0],expected)
        q='How much have we paid on medical claims in 2029 over the last year?'
        with self.assertRaises(R.AmbiguousRequest):L.parse(catalog,q)

class IncrementalCategoryDiscovery(unittest.TestCase):
    def test_a_failed_column_is_retried_without_rescanning_complete_columns(self):
        from unittest.mock import patch
        from clinical_core.agere.clinical import request_discovery as D
        w=Warehouse();self.addCleanup(w.close)
        D._VALUE_CACHE.clear();D._PARTIAL_VALUE_CACHE.clear()
        real_connect=sqlite3.connect;seen=[];failed=[False]
        class Connection:
            def __init__(self,*a,**kw):self.db=real_connect(*a,**kw)
            def __getattr__(self,k):return getattr(self.db,k)
            def execute(self,query,*a,**kw):
                if query.startswith('SELECT DISTINCT'):
                    seen.append(query)
                    if '"condition"' in query and '"concept"' in query and not failed[0]:
                        failed[0]=True;raise sqlite3.OperationalError('interrupted')
                return self.db.execute(query,*a,**kw)
        with patch.object(D.sqlite3,'connect',Connection):
            first=D.relation_values(w.catalog);n=len(seen)
            second=D.relation_values(w.catalog)
        self.assertIn('condition.concept',first['incomplete'])
        self.assertEqual(second['incomplete'],[])
        self.assertEqual(len(seen)-n,1)
        self.assertIn('diabetes',second['fields']['condition.concept'])
        D._VALUE_CACHE.clear();D._PARTIAL_VALUE_CACHE.clear()
