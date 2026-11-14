"""Unit normalizers contain constants; patient quantities still need grounding."""
import unittest
from clinical_core.agere.deliverables import verify, runtime
from clinical_core.agere.deliverables.receipts import Bundle

class UnitSyntax(unittest.TestCase):
    def setUp(self):
        self.b=Bundle({'frozen_evidence':True})
        self.rid=self.b.add('saved_answer',{}, {'observed':45})

    def test_indexed_rate_unit_label_is_not_a_patient_measurement(self):
        for unit in ('mL/min/1.73m²','mL/min/1.73 m2','mL/min per 1.73 m^2','ml / min / 1.73 m²'):
            with self.subTest(unit=unit):
                body=f'<p><span data-bind="{self.rid}:observed"></span> {unit}</p>'
                report=verify.verify(runtime.wrap(body,self.b,'Observed measurement'),self.b)
                self.assertTrue(report['passed'],report['violations'])
                self.assertEqual(verify.text_numbers(unit),[])

    def test_literal_markup_and_script_unit_labels_share_the_same_parser(self):
        for body in ('<p>Unit mL/min/1.73 m<sup>2</sup></p>',
                     "<script>var unit=' mL/min/1.73m²';</script>"):
            report=verify.verify(runtime.wrap(body,self.b,'Units'),self.b)
            self.assertTrue(report['passed'],report['violations'])

    def test_measurements_and_bare_constants_are_never_exempted_by_a_unit(self):
        for text in ('45 mL/min/1.73m²','1.73','1.73%','1.73 dollars','mL/min/1.74m²','mL/min/1.73m² + 27'):
            with self.subTest(text=text):
                self.assertTrue(verify.text_numbers(text))
                report=verify.verify(runtime.wrap('<p>'+text+'</p>',self.b,'Observed'),self.b)
                self.assertFalse(report['passed'])

    def test_numeric_calculation_factor_remains_checked(self):
        report=verify.verify(runtime.wrap('<script>var factor=1.73;</script>',self.b,'Computation'),self.b)
        self.assertIn('script_literal_not_in_data',[v['kind']for v in report['violations']])

if __name__=='__main__':unittest.main()
