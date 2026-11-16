"""Focused operator integrity and executable-plan boundary checks."""
import unittest
from clinical_core.agere.clinical.analytic_registry import renal_series,kidney
from clinical_core.agere.clinical.stack_planner import validate
from clinical_core.agere.clinical.warehouse_retrieval import representations

def lab(identifier,day,value,unit='mL/min/1.73m2',test='egfr',**kw):
    return {'record_id':identifier,'patient_id':'p','source_table':'labs','event_date':day,
            'available_date':day,'test':test,'value':value,'unit':unit,'status':'final',**kw}

class OperatorIntegrity(unittest.TestCase):
    def test_same_day_disagreement_is_not_smoothed_into_trend(self):
        r=renal_series([lab('a','2026-01-01',70),lab('b','2026-01-01',30),lab('c','2026-02-01',50)],'egfr','2026-09-30')
        self.assertEqual(len(r['same_date_conflicts']),1);self.assertEqual(len(r['series']),1)
        self.assertEqual(r['trajectory']['status'],'insufficient_distinct_dates')
    def test_duplicate_values_keep_both_sources(self):
        r=renal_series([lab('a','2026-01-01',50),lab('b','2026-01-01',50)],'egfr','2026-09-30')
        self.assertEqual(r['duplicate_observations_retaining_provenance'],1)
        self.assertEqual(len(r['series'][0]['all_source_evidence']),2)
    def test_endpoint_arithmetic_and_source_date_slope(self):
        r=renal_series([lab('a','2026-01-01',90),lab('b','2026-01-11',80),lab('c','2026-01-21',70)],'egfr','2026-09-30')
        self.assertAlmostEqual(r['trajectory']['slope_per_year'],-365.25)
        self.assertAlmostEqual(r['endpoint_comparison']['relative_change_percent'],-100*20/90)
        self.assertFalse(r['trajectory']['measurement_time_verified'])
    def test_bad_unit_and_unfinal_result_exposed(self):
        r=renal_series([lab('a','2026-01-01',4,unit='nonsense'),lab('b','2026-01-01',50,status='preliminary')],'egfr','2026-09-30')
        self.assertEqual({x['reason'] for x in r['excluded']},{'unsupported_or_missing_unit','result_status_not_final'})
    def test_creatinine_unit_conversion_keeps_raw(self):
        r=renal_series([lab('a','2026-01-01',88.4,unit='umol/L',test='creatinine')],'creatinine','2026-09-30')
        self.assertEqual(r['series'][0]['value'],1)
        self.assertEqual(r['series'][0]['evidence']['raw_value'],88.4)
    def test_missing_creatinine_and_acr_not_invented(self):
        r=kidney({'patient_id':'p','birth_date':'1970-01-01','sex':'female'},[lab('a','2026-01-01',30)],'2026-09-30')
        self.assertIn('usable_creatinine',r['missing_inputs']);self.assertIn('usable_uacr',r['missing_inputs'])
        self.assertIsNone(r['kidney_failure_probability']);self.assertFalse(r['care_gap_established'])
    def test_measurement_date_is_explicit_and_future_blocked(self):
        r=renal_series([lab('a','2026-01-01',40,collection_date='2026-10-01')],'egfr','2026-09-30')
        self.assertEqual(r['excluded'][0]['reason'],'future_measurement_date')
    def test_document_original_is_ineligible(self):
        r={'source_table':'documents','original_text':'secret simulator answer','extracted_text':'received OCR'}
        self.assertEqual([x['text'] for x in representations(r)],['received OCR'])

class PlannerBoundaries(unittest.TestCase):
    def plan(self,**changes):
        p={'interpretation':'Inspect renal source patterns.','coverage':'partial','unmet_requirements':['No validated renal risk calculation.'],
           'steps':[{'operation':'kidney_trajectory','query':'','patient_id':'','drug_class':'','view':'declining','purpose':'Observed renal trend.'}]}
        p.update(changes);return p
    def test_registered_plan_is_executable(self):self.assertEqual(validate(self.plan())['steps'][0]['operation'],'kidney_trajectory')
    def test_generated_sql_is_not_a_plan(self):
        p=self.plan();p['steps'][0]['sql']='DROP TABLE records'
        with self.assertRaises(ValueError):validate(p)
    def test_unregistered_operator_rejected(self):
        p=self.plan();p['steps'][0]['operation']='diagnose_aki'
        with self.assertRaises(ValueError):validate(p)
    def test_unsupported_plan_cannot_execute(self):
        with self.assertRaises(ValueError):validate(self.plan(coverage='unsupported'))
    def test_claimed_complete_with_missing_inputs_rejected(self):
        with self.assertRaises(ValueError):validate(self.plan(coverage='supported_observation'))

if __name__=='__main__':unittest.main()
