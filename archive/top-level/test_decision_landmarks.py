"""Decision timing, prior-source isolation, receipt identity and graph integration."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import decision_landmarks as D, baseline_evidence as B, outcomes as O
from clinical_core.agere.clinical.analysis_graph import validate, execute
from clinical_core.agere.clinical.graph_workbench import landmark_examples
from clinical_core.agere.clinical.economical_planner import local_plan
from test_longitudinal import event, ASOF
import test_baseline_evidence as baselines
import test_analysis_graph as graphs


def ev(rid='a',clinical='2026-08-01',arrival='2026-08-20',**kw):
    r=event(rid=rid,day=clinical or '2026-08-01',available_date=arrival,**kw)
    r['clinical_date']=clinical
    return r


def params(**kw):
    return {'contract':'received_admission_future_discharge_30d','receipt_from':'2026-01-01','receipt_through':ASOF,'available_by':ASOF,**kw}


def assess(indices=None,ends=None,p=None):
    p=p or params()
    return D.evaluate('p',[ev()] if indices is None else indices,ends or [],D.definitions()[p['contract']],p)


class Timing(unittest.TestCase):
    def test_late_historical_endpoint_not_future_target(self):
        r=assess(ends=[ev('d','2026-08-10','2026-08-25',event='A03')])
        self.assertEqual(r['recorded_label'],0)
        self.assertEqual(r['excluded_endpoints'][0]['reason'],'clinical_event_before_decision_window')
        self.assertEqual(r['decision']['date'],'2026-08-21')
        self.assertEqual(r['decision']['receipt_lag_calendar_days'],19)

    def test_window_inclusive_and_source_time_not_used(self):
        for day,label in [('2026-08-20',0),('2026-08-21',1),('2026-09-19',1),('2026-09-20',0)]:
            e=ev('d',day,'2026-09-25',event='A03');e['source_date']='2026-01-01'
            self.assertEqual(assess(ends=[e])['recorded_label'],label)

    def test_already_received_future_dated_endpoint_is_not_future_observation(self):
        r=assess(ends=[ev('d','2026-08-23','2026-08-20',event='A03')])
        self.assertEqual(r['recorded_label'],0)
        self.assertEqual(r['excluded_endpoints'][0]['reason'],'endpoint_record_already_available_at_decision')

    def test_clock_requires_received_dependencies_and_patient_identity(self):
        e=ev();e['clinical_date_evidence']=[{'patient_id':'p','arrival_date':'2026-08-22'}]
        self.assertEqual(assess([e])['status'],'index_clinical_clock_unavailable_at_receipt')
        e['clinical_date_evidence'][0]['patient_id']='other'
        with self.assertRaises(ValueError):assess([e])

    def test_unknown_clinical_clock_never_falls_back(self):
        self.assertIsNone(assess([ev(clinical=None)])['recorded_label'])
        r=assess(ends=[ev('d',None,'2026-09-01',event='A03')])
        self.assertIsNone(r['recorded_label']);self.assertEqual(r['status'],'unresolved_future_endpoint')

    def test_earliest_receipt_not_selected_for_favorable_endpoint(self):
        a=ev('a','2026-08-01','2026-08-02');b=ev('b','2026-09-01','2026-09-02')
        r=assess([b,a],[ev('d','2026-09-03','2026-09-04',event='A03')])
        self.assertEqual(r['index']['event_id'],a['id']);self.assertEqual(r['recorded_label'],0)
        self.assertEqual(D.baseline_anchor(r),D.baseline_anchor(assess([a,b])))

    def test_ambiguous_first_receipt_and_unknown_arrival_excluded(self):
        self.assertEqual(assess([ev(),ev('b','2026-08-03')])['status'],'ambiguous_first_received_index')
        self.assertEqual(assess([ev(arrival=None)])['status'],'unresolved_index_arrival')

    def test_incomplete_positive_not_eligible_and_no_index_not_zero(self):
        r=assess([ev(arrival='2026-09-20')],[ev('d','2026-09-25','2026-09-26',event='A03')])
        self.assertEqual(r['recorded_label'],1);self.assertFalse(r['mature_recorded_label_usable'])
        self.assertIsNone(assess([])['recorded_label'])

    def test_cutoff_baseline_excludes_next_day_and_index_record(self):
        idx=baselines.index_record();e=next(D.L.record_events(idx,ASOF));e['clinical_date']='2026-08-10'
        r=assess([e]);old=baselines.rec();late=baselines.rec('future',arrival='2026-08-21')
        b=B.evaluate('p',[idx,old,late],[],[],D.baseline_anchor(r),B.definition('received_prior_180d'))
        self.assertEqual(b['cutoff'],'2026-08-20');self.assertEqual([x['record_id'] for x in b['included_sources']],['lab'])
        self.assertEqual(b['anchor']['basis'],'receipt_decision')


class Persistence(unittest.TestCase):
    setUp=baselines.Persistence.setUp
    tearDown=baselines.Persistence.tearDown
    ledger=baselines.Persistence.ledger

    def prep(self):
        self.op['index']['p'][0]['clinical_date']='2026-08-10'
        with patch.object(O,'prepare',return_value=self.op):return D.prepare(self.root,params())

    def test_cache_and_patient_specific_refresh(self):
        prep=self.prep();r=D.execute(self.root,['p','q'],params(),prepared=prep)
        self.assertEqual(r['status_counts'],{'no_future_endpoint_observed_mature':1,'no_received_index':1})
        self.assertEqual(r['candidate_label_counts'],{'0':1})
        self.assertEqual(D.execute(self.root,['p','q'],params(),prepared=prep)['processing_counts']['patient_cache_hits'],2)
        db=sqlite3.connect(self.root/'stack.sqlite3')
        db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(baselines.rec(value=60)),'lab'));db.commit();db.close()
        fresh=self.prep();self.assertNotEqual(D.receipt_id(prep,'p'),D.receipt_id(fresh,'p'))
        self.assertEqual(D.receipt_id(prep,'q'),D.receipt_id(fresh,'q'))
        with patch.object(D,'prepare',return_value=fresh):
            with self.assertRaisesRegex(ValueError,'changed'):D.inspect(self.root,D.receipt_id(prep,'p'))

    def test_mutation_during_uncached_assembly_rejected(self):
        prep=self.prep();db=sqlite3.connect(self.root/'stack.sqlite3')
        db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(baselines.rec(value=70)),'lab'));db.commit();db.close()
        with self.assertRaisesRegex(ValueError,'changed during'):D.execute(self.root,['p'],params(),prepared=prep)

    def test_empty_input_never_expands(self):
        r=D.execute(self.root,[],params(),prepared=self.prep());self.assertEqual(r['patients_evaluated'],0)


class Graph(unittest.TestCase):
    setUp=graphs.GraphExecution.setUp
    tearDown=graphs.GraphExecution.tearDown

    def test_local_questions_and_complete_population(self):
        for ex in landmark_examples():
            graph,route=local_plan(ex['question'],ASOF);self.assertEqual(route,'registered_example')
            self.assertEqual(validate(graph,ASOF)['nodes'][-1]['operation'],'decision_landmark')
        with patch.object(D,'prepare',return_value={'identity':'landmark'}),patch.object(D,'execute',return_value={'patients_evaluated':6}) as run:
            r=execute(self.stack,landmark_examples()[0]['graph'])
            self.assertEqual(run.call_args.args[1],['p0','p1','p2','p3','p4','p5'])
            self.assertEqual(r['results'][-1]['result']['input_membership']['counts']['matched'],6)
            self.assertTrue(execute(self.stack,landmark_examples()[0]['graph'])['cache_hit'])

    def test_report_not_coerced_into_membership(self):
        g=copy.deepcopy(landmark_examples()[0]['graph'])
        g['nodes'].append(graphs.node('work','worklist',['decision'],{'program':'hf_review'}))
        with self.assertRaisesRegex(ValueError,'membership'):validate(g,ASOF)


if __name__=='__main__':unittest.main()
