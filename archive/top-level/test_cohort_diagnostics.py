import json
import unittest
from unittest.mock import patch
from test_learning_cohorts import CohortTests
from clinical_core.agere.clinical import learning_cohorts as c, cohort_diagnostics as d


class DiagnosticsTests(unittest.TestCase):
    setUp=CohortTests.setUp
    fake_model=CohortTests.fake_model
    acquire=CohortTests.acquire

    def fit(self):
        self.acquire();c.freeze(self.stack,self.key);real=c.student.fit
        def fit(rows,path,**kw):
            if kw['kind']=='minilm':raise ValueError('Fixture variant unavailable')
            return real(rows,path,**kw)
        with patch.object(c.student,'fit',side_effect=fit):c.fit(self.stack,self.key)

    def test_diagnostics_are_frozen_validation_only_and_create_no_readings_or_reviews(self):
        self.fit();before=c.reader.learning_export(self.stack)
        result=d.inspect(self.stack,self.key,'lexical',outcome='all',limit=30)
        self.assertEqual(result['validation_rows'],self.plan['counts']['validation'])
        self.assertTrue(result['rows']);self.assertTrue(all(r['partition']=='validation' for r in result['rows']))
        self.assertEqual(result['agreement_rows']+result['disagreement_rows'],result['validation_rows'])
        disagreed=d.inspect(self.stack,self.key,'lexical')
        self.assertTrue(all(r['disagreement_axes'] for r in disagreed['rows']))
        self.assertEqual(result['new_model_calls'],0);self.assertEqual(before,c.reader.learning_export(self.stack))

    def test_tampered_prediction_evidence_is_rejected(self):
        self.fit();file=self.output/'models/lexical/validation-predictions.jsonl'
        file.write_text(file.read_text()+'{}\n')
        with self.assertRaisesRegex(ValueError,'checksum mismatch'):d.inspect(self.stack,self.key,'lexical')

    def test_filters_are_bounded_before_dataset_access(self):
        for kw in [{'outcome':'truth'},{'offset':-1},{'limit':31},{'limit':True}]:
            with self.assertRaises(ValueError):d.inspect(self.stack,self.key,'lexical',**kw)

    def test_acquisition_breadth_is_reported_separately_from_fitted_targets(self):
        result=d.coverage(self.stack,self.key)
        self.assertEqual(result['sampled_fields'],280)
        self.assertEqual(sum(result['category_counts'].values()),self.plan['target_count'])
        self.assertEqual(sum(result['concept_counts'].values()),self.plan['target_count'])
        self.assertLessEqual(result['fitted_target_categories'],result['sampled_categories'])


if __name__=='__main__':unittest.main()
