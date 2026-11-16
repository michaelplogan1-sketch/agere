"""Authored comparator and ascertainment checks; no clinical/survival-model qualification."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import survival as S, analysis_graph as G


class BoundedThreshold(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name);self.semantic=self.path/'semantic.sqlite3'
        self.db=sqlite3.connect(self.semantic);self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE patient(patient_id,age_reported,sex_reported,coverage_type,pcp_clinician_id,pcp_practice_id);
          INSERT INTO patient VALUES('A',50,'F',NULL,NULL,NULL),('B',60,'M',NULL,NULL,NULL);
          CREATE TABLE measurement(record_id,patient_id,concept,event_date,value_canonical REAL,value_censor,value,
                                   result_current INTEGER,excluded_reason);''')
        (self.path/'run.json').write_text(json.dumps({'snapshot':{'as_of':'2030-02-01'}}))

    def add(self,rid,pid,day,value,censor=None):
        self.db.execute('INSERT INTO measurement VALUES(?,?,?,?,?,?,?,?,?)',
                        (rid,pid,'egfr',day,value,censor,str(censor or '')+str(value),1,None))

    def params(self,op='gt'):
        return {'index':{'type':'date','date':'2030-01-01'},
                'event':{'type':'measurement','concept':'egfr','op':op,'value':10},
                'horizon_days':30,'time_points':[10,30],'group_by':'none','cox':False,'lower_bound':False}

    def run_report(self,op='gt'):
        self.db.commit()
        with patch.object(S.semantic_layer,'ensure',return_value=self.semantic):
            return S.execute(self.path,['A','B'],self.params(op))

    def test_strict_reported_bounds_prove_crossing_while_definite_negative_control_remains(self):
        for op,baseline,bound in [('gt',5,'>'),('lt',15,'<')]:
            with self.subTest(op=op):
                self.db.execute('DELETE FROM measurement')
                self.add('baseline','A','2030-01-01',baseline)
                self.add('bounded','A','2030-01-11',10,bound)
                r=self.run_report(op)
                self.assertEqual((r['units'],r['events']),(1,1))
                self.assertEqual(r['table'][0]['events_by_day'],1)

    def test_unresolved_baseline_cannot_establish_an_at_risk_patient(self):
        self.add('baseline-ambiguous','A','2030-01-01',5,'>')
        self.add('later-positive','A','2030-01-11',15)
        r=self.run_report()
        self.assertEqual(r['units'],0)
        self.assertEqual(r.get('status'),'partial')
        self.assertEqual(r['excluded'].get('unresolved_threshold_at_time_zero'),1)

    def test_earlier_ambiguous_result_does_not_become_a_precise_first_event_or_censor(self):
        for pid in ('A','B'):self.add('baseline-'+pid,pid,'2030-01-01',5)
        self.add('ambiguous','A','2030-01-11',20,'<')
        self.add('later-positive','A','2030-01-21',15)
        self.add('known-positive','B','2030-01-11',15)
        r=self.run_report()
        self.assertEqual((r['units'],r['events']),(1,1))
        self.assertEqual(r.get('status'),'partial')
        unresolved,=r.get('unresolved_units') or []
        self.assertEqual((unresolved['patient_id'],unresolved['measurement_evidence'][0]['record_id']),('A','ambiguous'))
        plan={'nodes':[{'id':'s','operation':'survival','parameters':{}}],
              'coverage':'supported_observation','unmet_requirements':[]}
        a=G.build_answer_core(plan,[{'result':r}],{},'Show time to the measurement threshold')
        self.assertEqual(a['status'],'partial');self.assertTrue(a['missing'])
        self.assertEqual(a['overall']['unresolved_units'],r['unresolved_units'])

    def test_exact_negative_known_bound_and_post_event_ambiguity_controls(self):
        self.add('baseline-a','A','2030-01-01',5)
        self.add('event-a','A','2030-01-11',15)
        self.add('irrelevant-later','A','2030-01-21',20,'<')
        self.add('baseline-b','B','2030-01-01',5)
        self.add('negative-b','B','2030-01-21',10,'<')
        r=self.run_report()
        self.assertEqual((r['units'],r['events']),(2,1))
        self.assertFalse(r.get('unresolved_units'))
        self.assertEqual(r['groups'][0]['censored'],1)

    def test_unrecognized_comparator_is_not_an_exact_value(self):
        self.add('baseline','A','2030-01-01',5)
        self.add('approximate','A','2030-01-11',15,'~')
        r=self.run_report()
        self.assertEqual(r['units'],0)
        self.assertEqual(r.get('status'),'partial')


if __name__=='__main__':unittest.main()
