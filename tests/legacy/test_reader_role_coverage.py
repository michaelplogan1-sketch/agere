import unittest
from copy import deepcopy
from clinical_core.agere.clinical import reader_role_coverage as C, semantic_reader as R


class RoleCoverageTests(unittest.TestCase):
    def fixture(self):return {'heads':{a:{'classes':list(v)} for a,v in R.AXES.items()},'calibration':{'threshold':None}}
    def test_complete_classes_still_not_deployment_qualified(self):
        c=C.coverage(self.fixture());self.assertTrue(c['full_label_contract']);self.assertFalse(c['deployment_qualified']);self.assertFalse(c['automatic_acceptance'])
    def test_missing_planned_is_explicit(self):
        m=self.fixture();m['heads']['time']['classes'].remove('planned');c=C.coverage(m)
        self.assertFalse(c['full_label_contract']);self.assertEqual(c['unavailable_labels']['time'],['planned'])
    def test_no_threshold_is_not_zero_confidence_threshold(self):
        self.assertIsNone(C.coverage(self.fixture())['selective_threshold'])
    def test_development_threshold_does_not_promote(self):
        m=self.fixture();m['calibration']['threshold']=.9;self.assertFalse(C.coverage(m)['automatic_acceptance'])
    def test_unknown_and_duplicate_classes_rejected(self):
        for label in ('invented','unknown'):
            m=self.fixture();m['heads']['time']['classes'].append(label)
            with self.assertRaises(ValueError):C.coverage(m)
    def test_inputs_preserved(self):
        m=self.fixture();before=deepcopy(m);C.coverage(m);self.assertEqual(before,m)
    def test_invalid_cohort_request(self):
        for body in ({},{'cohort_id':'../models'},{'cohort_id':False},{'cohort_id':'a'*64,'labels':[]}):
            with self.assertRaises(ValueError):C.inspect('unused',body)


if __name__=='__main__':unittest.main()
