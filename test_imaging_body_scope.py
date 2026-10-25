import unittest
from clinical_core.agere.clinical import note_evidence as E

class ImagingBodyScope(unittest.TestCase):
    def matches(self, text):
        return E.report_concept_scope({'term': 'chest x-ray'}, text)

    def test_generic_xray_does_not_establish_chest(self):
        text = 'EXAM: XRAY\nIMPRESSION: Mild degenerative changes. No fracture.'
        self.assertFalse(self.matches(text))
        self.assertTrue(E.report_concept_scope({'term': 'x-ray'}, text))

    def test_explicit_chest_exam_keeps_a_bone_related_finding(self):
        self.assertTrue(self.matches('EXAM: CHEST XRAY\nIMPRESSION: No rib fracture.'))

    def test_chest_specific_report_wording_and_ocr_are_retained(self):
        self.assertTrue(self.matches('EXAM: XRAY\nIMPRESSION: No acute cardiopulmonary process.'))
        self.assertTrue(self.matches('EXAM: XRAY\nIMPRESSION: Possible early right 1owerlobe infiltrate.'))

    def test_other_site_or_modality_is_not_changed_by_a_prior_chest_reference(self):
        self.assertFalse(self.matches('EXAM: KNEE XRAY\nComparison: previous chest film. IMPRESSION: Degenerative changes.'))
        self.assertFalse(self.matches('EXAM: MRI HEAD\nPrior CXR was clear. IMPRESSION: Normal.'))

    def test_specific_title_can_establish_the_exam_site(self):
        self.assertTrue(E.report_concept_scope({'term': 'chest x-ray'},
                        'IMPRESSION: No rib fracture.', 'Chest X-ray Report'))
        self.assertFalse(E.report_concept_scope({'term': 'chest x-ray'},
                         'IMPRESSION: No acute chest findings.', 'CT Chest Report'))

    def test_xray_request_does_not_admit_mri_with_an_incidental_prior_xray(self):
        self.assertFalse(E.report_concept_scope({'term': 'x-ray'},
                         'EXAM: MRI HEAD\nPrior CXR clear. IMPRESSION: Normal.'))
