"""Evidence-set completeness: conclusion types, components carried by streams/cues, targeted fetch, re-selection inside the limit."""
import unittest

from clinical_core.agere.clinical import evidence_completeness as EC


def row(i, stream, text='', protected=False):
    return {'id': str(i), 'text': text, 'source': {'source_table': stream, 'record_id': f'r{i}'}, 'protected': protected}


class Assess(unittest.TestCase):
    def test_types_from_the_question(self):
        self.assertEqual(EC.types_for('Is the patient currently taking metformin?'), ['current_treatment'])
        self.assertEqual(EC.types_for('What did we pay the specialist provider per member?'), ['provider_cost'])
        self.assertIn('lab_result', EC.types_for('What was the latest A1c result?'))
        self.assertEqual(EC.types_for('Does she have transportation problems?'), ['social_need'])
        self.assertEqual(EC.types_for('Does the patient have a history of heart failure?'), ['condition_status'])
        self.assertEqual(EC.types_for('hello there'), [])

    def test_streams_and_cues_carry_components(self):
        rows = [row(1, 'medication_orders'), row(2, 'notes', 'Metformin was stopped for GI intolerance.'), row(3, 'notes', 'Routine follow up.')]
        a = EC.assess('Is the patient currently taking metformin?', rows)
        st = {c: v['status'] for c, v in a['components']['current_treatment'].items()}
        self.assertEqual(st, {'prescribing': 'present', 'dispensing': 'missing', 'administration': 'missing', 'discontinuation': 'present', 'current_status': 'missing'})
        self.assertFalse(a['complete']); self.assertIn(('current_treatment', 'dispensing'), a['missing'])
        self.assertEqual(a['components']['current_treatment']['discontinuation']['rows'], ['2'])

    def test_no_type_means_nothing_to_complete(self):
        rows, rep = EC.complete(lambda q: [], 'hello there', [row(1, 'notes')], 8)
        self.assertTrue(rep['complete']); self.assertEqual(rep['extra_searches'], 0)


class Complete(unittest.TestCase):
    def setUp(self):
        self.pool = {'dispense': row(10, 'pharmacy_dispenses', 'dispensed metformin 500 mg'), 'admin': row(11, 'medication_administrations', 'given in clinic'),
                     'list': row(12, 'medication_lists', 'metformin on list'), 'order': row(13, 'medication_orders', 'ordered metformin')}
        self.queries = []

    def search(self, q):
        self.queries.append(q)
        for key, cue in (('dispense', 'dispensed'), ('admin', 'administered'), ('list', 'medication list'), ('order', 'prescribed')):
            if cue in q: return [self.pool[key], row(99, 'notes', 'unrelated')]
        return []

    def test_missing_components_are_fetched_and_filtered_to_carriers(self):
        rows = [row(1, 'notes', 'continue metformin'), row(2, 'notes', 'continue metformin daily'), row(3, 'notes', 'continue metformin again')]
        out, rep = EC.complete(self.search, 'Is the patient currently taking metformin?', rows, 8)
        ids = {r['id'] for r in out}
        self.assertTrue({'10', '11', '12', '13'} <= ids); self.assertNotIn('99', ids)           # only rows that really carry the component join
        self.assertEqual({a['component'] for a in rep['added']}, {'current_treatment.prescribing', 'current_treatment.dispensing', 'current_treatment.administration', 'current_treatment.current_status'})
        self.assertEqual(rep['before']['current_treatment']['dispensing'], 'missing')
        self.assertEqual(rep['components']['current_treatment']['dispensing']['status'], 'present')
        self.assertIn('current_treatment.discontinuation', rep['missing'])                      # nothing carried it: reported, not guessed
        self.assertIn('partial answer', rep['guidance'])
        self.assertLessEqual(rep['extra_searches'], EC.EXTRA_SEARCHES)
        self.assertTrue(any('metformin' in q for q in self.queries))                           # subject terms kept in every targeted query
        self.assertTrue(all(r.get('completes') for r in out if r['id'] in {'10', '11', '12', '13'}))

    def test_reselection_stays_inside_limit_and_protects_sole_carriers_and_protected_rows(self):
        rows = [row(1, 'notes', 'continue metformin', protected=True)] + [row(i, 'notes', f'continue metformin {i}') for i in range(2, 9)]
        out, rep = EC.complete(self.search, 'Is the patient currently taking metformin?', rows, 8)
        self.assertLessEqual(len(out), 8)
        self.assertIn('1', {r['id'] for r in out})                                              # protected stays
        self.assertTrue({'10', '12'} <= {r['id'] for r in out})                                 # completers stay
        self.assertTrue(rep['dropped'])                                                         # redundant same-stream rows made room

    def test_complete_set_triggers_no_search(self):
        rows = [row(1, 'medication_orders'), row(2, 'pharmacy_dispenses'), row(3, 'medication_administrations'), row(4, 'medication_intent_changes'), row(5, 'medication_lists')]
        out, rep = EC.complete(self.search, 'Is the patient currently taking metformin?', rows, 8)
        self.assertEqual((rep['extra_searches'], rep['added'], rep['complete']), (0, [], True)); self.assertEqual(out, rows)

    def test_search_failures_degrade_to_a_reported_gap(self):
        def boom(q): raise ValueError('bounded search refused')
        out, rep = EC.complete(boom, 'Is the patient currently taking metformin?', [row(1, 'notes', 'continue')], 8)
        self.assertEqual(len(out), 1); self.assertFalse(rep['complete'])

    def test_extra_search_budget(self):
        _, rep = EC.complete(lambda q: [], 'What did we pay the provider for the specialist cost?', [row(1, 'notes')], 8)
        self.assertEqual(rep['extra_searches'], EC.EXTRA_SEARCHES); self.assertTrue(rep['unfetched'] or len(rep['missing']) <= EC.EXTRA_SEARCHES)


if __name__ == '__main__':
    unittest.main()
