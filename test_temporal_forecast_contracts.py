"""Authored historical append-invariance and endpoint/path-event contract checks."""
from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from clinical_core.agere.clinical import trajectory as T, calibration as C, analysis_graph as G, outreach as O


def series():
    days = ['2030-01-01','2030-02-01','2030-03-01','2030-05-10','2030-06-10','2030-06-20','2030-06-25']
    return {str(i): {d:[50.] for d in days} for i in range(20)}


class Historical(unittest.TestCase):
    def test_future_feedback_cannot_change_historical_calibration(self):
        s = series(); snap = date(2030,6,30).toordinal()
        with patch.object(T, '_predict', return_value=(50.,1.)):
            before = T.public(T.calibrate(s,'egfr',snap,30,40,True))
            for days in s.values(): days.update({f'2030-07-{i:02}':[1000.] for i in range(1,11)})
            after = T.public(T.calibrate(s,'egfr',snap,30,40,True))
        self.assertEqual(before, after); self.assertGreater(before['aci']['steps'],0)
        self.assertEqual(before['observation_cutoff'], '2030-06-30')

    def test_sequential_cutoff_and_population_are_enforced_before_feedback(self):
        s = series(); s['0']['2030-07-01'] = [1000.]
        snap = date(2030,6,30).toordinal()
        with patch.object(T, '_predict', return_value=(50.,1.)):
            steps = T._sequential(s,date(2030,5,1).toordinal(),.08,3,3,keep={'0'},end=snap)
        self.assertEqual(len(steps),4); self.assertTrue(all(v[0]<=snap for v in steps))
        self.assertEqual(T._sequential(s,0,.08,3,3,keep=set(),end=snap),[])

    def test_shared_reader_excludes_future_event_and_known_future_arrival(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE measurement(patient_id,concept,event_date,value_canonical,available_date)')
            db.executemany('INSERT INTO measurement VALUES(?,?,?,?,?)', [('A','egfr','2030-06-10',50,'2030-06-10'),
                ('A','egfr','2030-06-15',20,'2030-07-01'),('A','egfr','2030-07-01',10,None),('OUT','egfr','2030-06-10',1,'2030-06-10')])
            self.assertEqual(dict(T.load_series(db,['egfr'],'2030-06-30',{'A'})['egfr']['A']),{'2030-06-10':[50]})

    def test_public_calibration_operation_is_historically_append_invariant(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp); semantic=path/'semantic.sqlite3'
            with sqlite3.connect(semantic) as db:
                db.executescript("CREATE TABLE meta(key,value);INSERT INTO meta VALUES('as_of','2030-06-30'); CREATE TABLE measurement(patient_id,concept,event_date,value_canonical,available_date);")
                db.executemany('INSERT INTO measurement VALUES(?,?,?,?,?)',[(pid,'egfr',day,v[0],day) for pid,days in series().items() for day,v in days.items()])
            with patch.object(C.semantic_layer,'ensure',return_value=semantic),patch.object(T,'_predict',return_value=(50.,1.)):
                before=C.execute(path,None,{'concept':'egfr','threshold':40,'horizon_days':30},'2030-06-30')
                with sqlite3.connect(semantic) as db:
                    db.executemany('INSERT INTO measurement VALUES(?,?,?,?,?)',[(str(pid),'egfr',f'2030-07-{i:02}',1000.,f'2030-07-{i:02}') for pid in range(20) for i in range(1,11)])
                after=C.execute(path,None,{'concept':'egfr','threshold':40,'horizon_days':30},'2030-06-30')
            self.assertEqual(before,after)


class Events(unittest.TestCase):
    def test_recovered_series_has_an_observed_crossing_but_safe_last_value(self):
        origin=date(2030,1,1).toordinal()
        s={'A':{'2029-10-01':[50.],'2029-11-01':[50.],'2030-01-01':[50.], '2030-01-11':[20.],'2030-01-31':[50.]}}
        with patch.object(T,'_predict',return_value=(50.,1.)):
            row,=T.backtest_pairs(s,origin,30,.08,3,3,30,True)
        self.assertFalse(row['crossed']);self.assertEqual(row['observation_definition'],'last_observed_value_in_window')
        self.assertEqual(row['target_event'],'endpoint_exceedance')
        self.assertTrue(any(v[0]<30 for d,v in s['A'].items() if '2030-01-01'<d<='2030-01-31'))

    def test_forecast_probability_declares_latent_endpoint_not_first_passage(self):
        r=T.forecast([(i*30,50.) for i in range(12)],.08,3,365,30,True)
        self.assertEqual(r['probability_target']['event'],'endpoint_exceedance')
        self.assertFalse(r['probability_target']['path_event_probability_available'])
        self.assertEqual(r['p_endpoint'],r['p_cross'])

    def test_original_path_event_request_cannot_be_answered_by_endpoint_result(self):
        report={'mode':'forecast','method':'endpoint-only','patients':[],'flagged':0,'patients_with_series':20,
                'concept':'egfr','direction':'below','threshold':30,'unit':'mL/min','horizon_days':365}
        plan={'nodes':[{'id':'t','operation':'trajectory'}],'coverage':'supported_observation','unmet_requirements':[]}
        r=G.build_answer_core(plan,[{'result':report}],{},'Who will cross below 30 within one year?')
        self.assertEqual(r['status'],'partial');self.assertIsNone(r['count']);self.assertTrue(r['missing'])
        self.assertFalse(r['overall']['path_event_probability_available'])

    def test_event_definitions_and_outreach_labels_remain_distinct(self):
        for question,event in [('Who crosses within a year?','any_observed_crossing'),('First-passage probability?','first_passage'),
                               ('Sustained crossing?','sustained_crossing'),('Value below 30 at the one-year endpoint?','endpoint_exceedance'),
                               ('Among patients tested within the last year, value below 30 at the endpoint?','endpoint_exceedance')]:
            self.assertEqual(T.requested_event(question),event)
        for code in ['RISING_EGFR','RISING_A1C','RISING_SBP']:
            self.assertIn('endpoint',O.REASONS[code][1]);self.assertNotIn('within',O.REASONS[code][1])

    def test_strict_schema_null_forecast_fields_do_not_invent_a_forecast(self):
        p=T.validate({'concept':'egfr','direction':'down','min_points':3,'top':None,
                      'threshold':None,'horizon_days':None,'target_event':None})
        self.assertNotIn('threshold',p)

    def test_execute_retains_requested_path_event_without_a_false_zero_patient_count(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);semantic=path/'semantic.sqlite3'
            (path/'run.json').write_text(json.dumps({'snapshot':{'as_of':'2030-06-30'}}))
            with sqlite3.connect(semantic) as db:
                db.execute('CREATE TABLE measurement(patient_id,concept,event_date,value_canonical)')
                db.executemany('INSERT INTO measurement VALUES(?,?,?,?)',[('A','egfr',d,v[0]) for d,v in series()['0'].items()])
            with patch.object(T.semantic_layer,'ensure',return_value=semantic):
                r=T.execute(path,['A'],{'concept':'egfr','threshold':30,'horizon_days':365,'target_event':'first_passage'})
            self.assertEqual(r['status'],'unknown');self.assertIsNone(r['flagged'])
            self.assertEqual(r['target_event'],'first_passage');self.assertTrue(r['missing'])


if __name__=='__main__':unittest.main()
