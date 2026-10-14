"""Local compiler answer shapes: periods bind every timed atom, requested groupings are kept, unread words decline,
plain 'how many' counts stay cohort counts, and fills count every picked-up fill source. Own phrasings, fixture profile."""
import unittest

from clinical_core.agere.clinical import local_compiler as lc

AS_OF = '2030-05-27'


def _f(values=None, populated=100):
    spec = {'populated': populated, 'types': {'text': populated}, 'distinct': len(values or {}) or 50}
    if values: spec['values'] = values
    return spec


PROFILE = {'source_digest': 'fixture', 'patients': {'records': 500, 'fields': {}}, 'measurements': {}, 'streams': {
    'appointments': {'records': 400, 'fields': {
        'appointment_id': _f(), 'event_date': _f(), 'patient_id': _f(), 'scheduled_date': _f(),
        'status': _f({'cancelled': 50, 'completed': 300, 'no_show': 50}),
        'cancellation_channel': _f({'patient_portal': 20, 'patient_call': 20, 'clinic_initiated': 10}),
        'visit_type': _f({'new_patient': 100, 'follow_up': 300}), 'modality': _f({'in_person': 300, 'video': 100})}},
    'labs': {'records': 900, 'fields': {'test': _f(), 'value': _f(), 'unit': _f(), 'event_date': _f(), 'patient_id': _f(),
                                        'result_time': _f(), 'specimen_date': _f()}},
    'social_screens': {'records': 300, 'fields': {'transport_difficulty_reported': {'populated': 300, 'types': {'true': 40, 'false': 260}, 'distinct': 2},
                                                  'transport': _f({'reliable': 250, 'unreliable': 50}), 'event_date': _f(), 'patient_id': _f()}},
    'adt': {'records': 100, 'fields': {'event': _f({'admission': 50, 'discharge': 50}), 'event_date': _f(), 'patient_id': _f()}},
    'encounters': {'records': 900, 'fields': {'type': _f({'office': 600, 'ED': 200, 'inpatient': 100}), 'event_date': _f(), 'patient_id': _f()}},
    'conditions': {'records': 900, 'fields': {'description': _f(), 'icd10': _f(), 'event_date': _f(), 'patient_id': _f()}},
}}


def compile_(q):
    return lc.compile(q, PROFILE, AS_OF)


def spec(graph):
    return graph['nodes'][0]['parameters']['specification']


class PeriodWindows(unittest.TestCase):
    def test_either_or_social_atoms_take_the_period(self):
        g, info = compile_('Count the patients with a transport problem recorded during 2028')
        self.assertIsNotNone(g, info)
        atoms = spec(g)['any'] + spec(g)['all']
        self.assertTrue(atoms)
        for a in atoms:
            self.assertEqual(a['window'], {'basis': 'source_event', 'start': '2028-01-01', 'end': '2028-12-31'})

    def test_excluded_lab_test_takes_the_period(self):
        g, info = compile_('List the patients with hypertension who had no LDL drawn in 2028')
        self.assertIsNotNone(g, info)
        ldl = [a for a in spec(g)['exclude'] if a.get('concept') == 'ldl']
        self.assertEqual(ldl[0]['window']['start'], '2028-01-01')
        htn = [a for a in spec(g)['all'] if a.get('concept') == 'hypertension']
        self.assertEqual(htn[0]['window']['basis'], 'all_observed')  # chronic conditions keep full history


class Grouping(unittest.TestCase):
    def test_which_field_is_a_grouping(self):
        g, info = compile_('How many appointments were cancelled during 2028 and via which channel?')
        self.assertIsNotNone(g, info)
        sql = g['nodes'][-1]['parameters']['query']
        self.assertIn("GROUP BY 1", sql)
        self.assertIn('cancellation_channel', sql)
        self.assertEqual(g['nodes'][-1]['parameters']['returns'], 'table')

    def test_what_modality_groups(self):
        g, info = compile_('What modality did the no-show appointments in 2029 use?')
        self.assertIsNotNone(g, info)
        self.assertIn("json_extract(payload,'$.modality') AS modality", g['nodes'][-1]['parameters']['query'])


class UnreadWordsDecline(unittest.TestCase):
    def test_negation_without_an_exclusion_declines(self):
        g, info = compile_('How many specimen rows have no collection timestamp?')
        self.assertIsNone(g)

    def test_row_counts_never_become_the_roster(self):
        g, info = compile_('How many rows came in this year?')
        self.assertIsNone(g)

    def test_absent_named_field_is_a_null_check(self):
        g, info = compile_('How many lab result rows have no result_time?')
        self.assertIsNotNone(g, info)
        self.assertIn("json_extract(payload,'$.result_time') IS NULL", g['nodes'][-1]['parameters']['query'])
        self.assertIn("stream = 'labs'", g['nodes'][-1]['parameters']['query'])


class PlainCounts(unittest.TestCase):
    def test_how_many_with_a_defined_label_is_a_cohort_count(self):
        g, info = compile_('How many hypertensive patients had out-of-range lipids (latest 2028 LDL above 190)?')
        self.assertIsNotNone(g, info)
        self.assertEqual(g['nodes'][-1]['operation'], 'cohort')
        ldl = [a for a in spec(g)['all'] if a.get('concept') == 'ldl'][0]
        self.assertEqual((ldl['op'], ldl['value'], ldl['aggregate']), ('gt', 190.0, 'latest'))
        self.assertEqual(ldl['window']['start'], '2028-01-01')

    def test_plain_count_is_not_a_measure(self):
        self.assertTrue(lc._plain_count_question('how many diabetics had an eye exam in 2028?'))
        self.assertFalse(lc._plain_count_question('what is our diabetic eye exam rate for 2028?'))
        self.assertFalse(lc._plain_count_question('how many diabetics are non-compliant on the eye exam measure?'))
        self.assertFalse(lc._plain_count_question('how many open care gaps for eye exams do we have?'))

    def test_ratio_test_is_uacr_not_creatinine(self):
        g, info = compile_('How many hypertensive patients got an albumin to creatinine ratio checked in 2028?')
        self.assertIsNotNone(g, info)
        concepts_ = {a.get('concept') for a in spec(g)['all']}
        self.assertIn('uacr', concepts_)
        self.assertNotIn('creatinine', concepts_)


class Fills(unittest.TestCase):
    def test_picked_up_counts_every_fill_source(self):
        g, info = compile_('How many hypertensive patients picked up an ACE inhibitor in 2028?')
        self.assertIsNotNone(g, info)
        sql = g['nodes'][-1]['parameters']['query']
        for evidence in lc.FILL_EVIDENCE:
            self.assertIn(repr(evidence), sql)
        self.assertIn('payer_claim', lc.FILL_EVIDENCE)


class PmpmHeadline(unittest.TestCase):
    ROWS = [{'group': 'all', 'members': 10, 'member_months': 120, 'allowed_pmpm': 410.5, 'paid_pmpm': 300.0,
             'allowed_pmpm_completed': 455.25, 'ibnr_estimate': 5463.0}]

    def test_observed_allowed_pmpm_leads(self):
        from clinical_core.agere.clinical.analysis_graph import headline
        text = headline({'status': 'answered', 'answer_type': 'table', 'table': self.ROWS, 'interpretation': 'allowed pmpm for 2028'})
        self.assertIn('allowed pmpm: all 410.5', text)

    def test_completed_pmpm_when_asked(self):
        from clinical_core.agere.clinical.analysis_graph import headline
        text = headline({'status': 'answered', 'answer_type': 'table', 'table': self.ROWS,
                         'interpretation': 'ibnr-completed allowed pmpm for 2028'})
        self.assertIn('allowed pmpm completed: all 455.25', text)


if __name__ == '__main__':
    unittest.main()
