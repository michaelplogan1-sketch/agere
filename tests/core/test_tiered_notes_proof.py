"""The note-reading test's scoring, with no model and no warehouse: how an answer table becomes a figure, the reference from
the key's labels, exact grading, and the readers' agreement."""
import unittest

from clinical_core.agere.tiered import notes_proof as n


class Scoring(unittest.TestCase):
    def test_reference_by_aggregate(self):
        labels = {'d1': ('p1', 'yes'), 'd2': ('p1', 'yes'), 'd3': ('p2', 'no'), 'd4': ('p3', 'yes')}
        self.assertEqual(n.reference('count_documents', labels), 3)
        self.assertEqual(n.reference('count_patients', labels), 2)
        self.assertEqual(n.reference('list_patients', labels), ['p1', 'p3'])

    def test_answer_tables_become_the_figure_the_question_asks_for(self):
        counted = (['documents_read', 'documents_stating', 'documents_not_stating', 'documents_undecided'], [[10, 4, 6, 0]])
        self.assertEqual(n._value_from_rows('count_documents', *counted), 4)
        listed = (['record_id', 'patient_id'], [['d1', 'p1'], ['d2', 'p1'], ['d3', 'p2']])
        self.assertEqual(n._value_from_rows('count_documents', *listed), 3)
        self.assertEqual(n._value_from_rows('count_patients', *listed), 2)
        self.assertEqual(n._value_from_rows('list_patients', *listed), ['p1', 'p2'])
        self.assertEqual(n._value_from_rows('count_patients', ['patients'], [[7]]), 7)
        self.assertIsNone(n._value_from_rows('list_patients', ['patients'], [[7]]))
        self.assertIsNone(n._value_from_rows('count_documents', ['a', 'b'], [[1, 2]]))   # two figures: no single answer

    def test_grading_is_exact(self):
        self.assertEqual(n._kind(4, 4), 'right'); self.assertEqual(n._kind(5, 4), 'wrong'); self.assertEqual(n._kind(4.0, 4), 'right')
        self.assertEqual(n._kind(None, 4), 'declined')
        self.assertEqual(n._kind(['p3', 'p1'], ['p1', 'p3']), 'right'); self.assertEqual(n._kind(['p1'], ['p1', 'p3']), 'wrong')

    def test_kappa(self):
        self.assertEqual(n._kappa(['yes', 'no', 'yes', 'no'], ['yes', 'no', 'yes', 'no']), 1.0)
        self.assertEqual(n._kappa(['yes', 'no'], ['no', 'yes']), -1.0)


if __name__ == '__main__':
    unittest.main()
