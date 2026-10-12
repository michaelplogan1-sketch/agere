"""Grounding, concept resolution and concept/measurement cohort evaluation.

Uses an in-memory profile shaped like the received warehouse, so these run
without artifacts: ICD-10 on a minority of conditions, SNOMED/labels on the rest,
lab results with no code field and mixed unit spellings.
"""
import unittest

from clinical_core.agere.clinical import cohorts, concepts, grounding

W = {'basis': 'all_observed', 'start': '', 'end': ''}
AS_OF = '2029-08-15'


def profile():
    return {
        'streams': {
            'conditions': {'records': 100, 'fields': {
                'icd10': {'populated': 5, 'types': {'text': 5}, 'distinct': 3, 'values': {'E11.9': 2, 'E10.9': 1, 'I50.22': 2}},
                'description': {'populated': 100, 'types': {'text': 100}, 'distinct': 6, 'values': {
                    'Diabetes mellitus type 2': 30, 'T2DM': 2, 'Prediabetes': 40, 'Family history of diabetes mellitus': 3,
                    'Chronic congestive heart failure': 20, 'HTN': 5}},
                'snomed': {'populated': 95, 'types': {'text': 95}, 'distinct': 4, 'values': {'44054006': 30}}}},
            'labs': {'records': 50, 'fields': {
                'test': {'populated': 50, 'types': {'text': 50}, 'distinct': 3, 'values': {'HbA1c': 20, 'Potassium': 25, 'Glomerular filtration rate': 5}},
                'value': {'populated': 48, 'types': {'real': 48, 'null': 2}, 'distinct': 40, 'numeric_range': [2, 12]},
                'unit': {'populated': 50, 'types': {'text': 50}, 'distinct': 4, 'values': {'%': 20, 'mmol/L': 20, 'mEq/L': 5, 'mL/min': 5}}}},
        },
        'patients': {'records': 3, 'fields': {'age_reported': {'populated': 3, 'types': {'integer': 3}},
                                              'patient_id': {'populated': 3, 'types': {'text': 3}}}},
        'measurements': {'labs': [
            {'name': 'HbA1c', 'unit': '%', 'records': 20, 'numeric': 20, 'range': [5, 12]},
            {'name': 'Potassium', 'unit': 'mmol/L', 'records': 20, 'numeric': 18, 'range': [3, 6]},
            {'name': 'Potassium', 'unit': 'mEq/L', 'records': 5, 'numeric': 5, 'range': [3, 6]},
            {'name': 'Glomerular filtration rate', 'unit': 'mL/min', 'records': 5, 'numeric': 5, 'range': [20, 90]}]},
    }


def spec(*atoms, group='all'):
    s = {'all': [], 'any': [], 'exclude': []}
    s[group] = list(atoms)
    return s


def rec(stream, **kw):
    return {'source_table': stream, 'record_id': kw.pop('record_id', f'{stream}-{len(kw)}'), **kw}


class ConceptResolution(unittest.TestCase):
    def test_condition_covers_every_coding_system_and_excludes_lookalikes(self):
        r = concepts.resolve('diabetes', profile())
        self.assertEqual(set(r['included_labels']), {'Diabetes mellitus type 2', 'T2DM'})
        self.assertEqual(set(r['excluded_labels']), {'Prediabetes', 'Family history of diabetes mellitus'})
        codes = {v for b in r['bindings'] if b['field'] == 'icd10' for v in b['values']}
        self.assertEqual(codes, {'E11.9', 'E10.9'})

    def test_measurement_keeps_only_convertible_units(self):
        r = concepts.resolve('egfr', profile())
        self.assertEqual(r['included_series'], {})
        self.assertIn('Glomerular filtration rate [mL/min]', r['unconvertible_series'])
        k = concepts.resolve('potassium', profile())
        self.assertEqual(set(k['included_series']), {'Potassium [mmol/L]', 'Potassium [mEq/L]'})

    def test_code_mapping_prefers_specific_concepts(self):
        self.assertEqual(concepts.concept_for_code('E11'), 'type_2_diabetes')
        self.assertEqual(concepts.concept_for_code('E11.22'), 'chronic_kidney_disease')
        self.assertEqual(concepts.concept_for_code('E08'), 'diabetes')
        self.assertEqual(concepts.concept_for_code('4548-4'), 'hba1c')
        self.assertIsNone(concepts.concept_for_code('E1'))


class Grounding(unittest.TestCase):
    def test_absent_lab_code_field_is_repaired_to_measurement(self):
        atom = {'kind': 'record', 'stream': 'labs', 'window': W, 'conditions': [
            {'field': 'code', 'op': 'eq', 'value': '4548-4'}, {'field': 'value', 'op': 'gt', 'value': 9}]}
        grounded, report = grounding.prepare(spec(atom), profile())
        new = grounded['all'][0]
        self.assertEqual((new['kind'], new['concept'], new['op'], new['value']), ('measurement', 'hba1c', 'gt', 9))
        self.assertEqual(report[0]['issue'], 'field_unavailable')
        self.assertEqual(report[0]['severity'], 'repaired')

    def test_minority_icd_field_is_repaired_to_concept(self):
        atom = {'kind': 'record', 'stream': 'conditions', 'window': W,
                'conditions': [{'field': 'icd10', 'op': 'prefix', 'value': 'E11'}]}
        grounded, report = grounding.prepare(spec(atom), profile())
        self.assertEqual((grounded['all'][0]['kind'], grounded['all'][0]['concept']), ('concept', 'type_2_diabetes'))
        self.assertIn('partial_code_coverage', {i['issue'] for i in report})

    def test_unrepairable_missing_field_fails_loudly(self):
        atom = {'kind': 'record', 'stream': 'labs', 'window': W,
                'conditions': [{'field': 'specimen', 'op': 'eq', 'value': 'serum'}]}
        with self.assertRaises(grounding.GroundingError) as ctx:
            grounding.prepare(spec(atom), profile())
        self.assertEqual(ctx.exception.issues[0]['issue'], 'field_unavailable')

    def test_missing_stream_fails_loudly(self):
        atom = {'kind': 'record', 'stream': 'sdoh', 'window': W,
                'conditions': [{'field': 'transport', 'op': 'eq', 'value': 'none'}]}
        with self.assertRaises(grounding.GroundingError):
            grounding.prepare(spec(atom), profile())

    def test_age_without_birth_date_uses_reported_age(self):
        grounded, report = grounding.prepare(spec({'kind': 'patient', 'field': 'age', 'op': 'ge', 'value': 65}), profile())
        self.assertEqual(grounded['all'][0]['field'], 'age_reported')
        self.assertEqual(report[0]['severity'], 'repaired')

    def test_value_never_observed_is_reported(self):
        atom = {'kind': 'record', 'stream': 'labs', 'window': W, 'conditions': [{'field': 'unit', 'op': 'eq', 'value': 'g/L'}]}
        with self.assertRaises(grounding.GroundingError) as ctx:
            grounding.prepare(spec(atom), profile())
        self.assertEqual(ctx.exception.issues[0]['issue'], 'value_not_observed')


class ConceptEvaluation(unittest.TestCase):
    def evaluate(self, atom, records):
        grounded, _ = grounding.prepare(spec(cohorts.validate(spec(atom), AS_OF)['all'][0]), profile())
        state, traces = cohorts.evaluate_patient(grounded, {'patient_id': 'P'}, records, AS_OF)
        return state, traces[0]

    def test_label_only_diagnosis_matches(self):
        state, _ = self.evaluate({'kind': 'concept', 'concept': 'diabetes', 'window': W},
                                 [rec('conditions', description='T2DM')])
        self.assertIs(state, True)

    def test_lookalike_label_does_not_match_and_is_not_unknown(self):
        state, _ = self.evaluate({'kind': 'concept', 'concept': 'diabetes', 'window': W},
                                 [rec('conditions', description='Prediabetes')])
        self.assertIs(state, False)

    def test_measurement_converts_equivalent_units(self):
        atom = {'kind': 'measurement', 'concept': 'potassium', 'op': 'gt', 'value': 5.5, 'aggregate': 'any', 'window': W}
        state, _ = self.evaluate(atom, [rec('labs', test='Potassium', value=5.8, unit='mEq/L')])
        self.assertIs(state, True)

    def test_cancelled_result_is_not_a_measurement(self):
        atom = {'kind': 'measurement', 'concept': 'potassium', 'op': 'gt', 'value': 5.5, 'aggregate': 'any', 'window': W}
        state, trace = self.evaluate(atom, [rec('labs', test='Potassium', value=4.1, unit='mmol/L', record_id='a'),
                                            rec('labs', test='Potassium', value=None, unit='mmol/L', record_id='b',
                                                result_status='cancelled')])
        self.assertIs(state, False)
        self.assertEqual(trace['records_without_reported_value'], 1)

    def test_latest_uses_most_recent_result(self):
        atom = {'kind': 'measurement', 'concept': 'hba1c', 'op': 'gt', 'value': 9, 'aggregate': 'latest', 'window': W}
        records = [rec('labs', test='HbA1c', value=10.2, unit='%', event_date='2028-01-01', record_id='old'),
                   rec('labs', test='HbA1c', value=7.1, unit='%', event_date='2029-03-01', record_id='new')]
        self.assertIs(self.evaluate(atom, records)[0], False)
        atom['aggregate'] = 'any'
        self.assertIs(self.evaluate(atom, records)[0], True)

    def test_unconvertible_unit_is_unknown(self):
        atom = {'kind': 'measurement', 'concept': 'potassium', 'op': 'gt', 'value': 5.5, 'aggregate': 'any', 'window': W}
        state, _ = self.evaluate(atom, [rec('labs', test='Potassium', value=6.0, unit='mg/dL')])
        self.assertIsNone(state)

    def test_validation_rejects_unknown_concepts(self):
        with self.assertRaises(ValueError):
            cohorts.validate(spec({'kind': 'concept', 'concept': 'not_a_concept', 'window': W}), AS_OF)
        with self.assertRaises(ValueError):
            cohorts.validate(spec({'kind': 'measurement', 'concept': 'diabetes', 'op': 'gt', 'value': 1,
                                   'aggregate': 'any', 'window': W}), AS_OF)


if __name__ == '__main__':
    unittest.main()
