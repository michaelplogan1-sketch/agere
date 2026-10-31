"""Patient-facing reports consume reconciled source accounts without voting."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from clinical_core.agere.clinical import document_review as D
from clinical_core.agere.deliverables import patient_evidence as P


class DocumentaryCards(unittest.TestCase):
    def context(self):
        q = {'question': 'What reason does this note document for not using an MRA?',
             'entity': {'kind': 'medication_class', 'id': 'mra', 'aliases': ['MRA']},
             'relation': 'documented_reason_not_on_therapy', 'subject': 'patient'}
        docs = [{'record_id': rid, 'date': '2030-01-01', 'available_date': '2030-01-02', 'text': text}
                for rid, text in [('R1', 'MRA held for hyperkalemia.'), ('R2', 'No MRA intolerance or potassium barrier documented.')]]
        findings = [D.relation_finding('A', 'reason', q, doc,
            {'start': 0, 'end': len(doc['text']), 'text': doc['text']},
            {'polarity': polarity, 'temporality': 'current', 'subject': 'patient'}, 'fixture')
            for doc, polarity in zip(docs, ['affirmed', 'negated'])]
        source_row = D.with_correspondence([{'patient_id': 'A', 'review_json': json.dumps({'reason': findings})}],
            {'A': [docs]}, {'reason': q}, '2030-05-27')[0]
        row = {'patient_id': 'A', 'on_mra': 0, 'on_raas': 0, 'on_beta_blocker': 0, 'on_sglt2i': 0,
               'documentary_findings': source_row['source_findings_json']}
        return {'question': 'Review medication optimization', 'source': {'as_of': '2030-05-27'},
                'answer': {'status': 'partial', 'table': [row]},
                'investigation': {'document_reviews': [{'questions': {'reason': q}, 'rows': [source_row]}]}}

    def test_opposing_dated_state_reaches_patient_card_and_keeps_both_exact_readings(self):
        c = self.context(); before = copy.deepcopy(c)
        projected = P.projection(c)
        self.assertEqual(projected['notes']['A'][0]['documentary_state'],
                         json.loads(c['investigation']['document_reviews'][0]['rows'][0]['documentary_state_json']))
        card = P.review_cards(c, projected)['A']
        self.assertEqual(card['documentary_reconciliation']['groups'][0]['status'], 'same_date_conflict')
        self.assertIn('1 set of chart statements', card['documentary_reconciliation']['summary'])
        reasons = card['therapy'][2]['documented_reasons']
        self.assertEqual(len(reasons), 2)
        self.assertTrue(all('reconciliation' in g['review_status'] for g in reasons))
        self.assertTrue(all(g['documentary_accounts'][0]['dated_accounts'][0]['date'] == '2030-01-01' for g in reasons))
        self.assertFalse(card['documentary_reconciliation']['current_clinical_state_established'])
        self.assertEqual(c, before)

    def test_changed_finding_cannot_borrow_another_assertions_reconciliation(self):
        c = self.context()
        findings = json.loads(c['answer']['table'][0]['documentary_findings'])
        findings[0]['assertion']['value'] = 'Changed interpretation under the old ID'
        c['answer']['table'][0]['documentary_findings'] = json.dumps(findings)
        with self.assertRaisesRegex(ValueError, 'differs from its reconciled source assertion'):
            P.review_cards(c)

    def test_other_patient_state_does_not_qualify_this_patients_source_reading(self):
        c = self.context(); native = c['investigation']['document_reviews'][0]['rows'][0]
        state = json.loads(native['documentary_state_json'])
        for a in state['assertions']:a['patient_id'] = a['source']['patient_id'] = 'B'
        native['documentary_state_json'] = json.dumps(state)
        card = P.review_cards(c)['A']
        self.assertEqual(card['documentary_reconciliation']['groups'], [])
        self.assertFalse(any(g.get('documentary_accounts') for g in card['therapy'][2]['documented_reasons']))

    def test_pdf_shows_the_reconciliation_problem_without_printing_assertion_hashes(self):
        from pypdf import PdfReader
        from clinical_core.agere.deliverables.patient_report_pdf import render
        c = self.context()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'review.pdf'; render(c, target)
            text = ' '.join(' '.join(page.extract_text() or '' for page in PdfReader(target).pages).split())
        self.assertIn('1 set of chart statements differs', text)
        self.assertIn('MRA held for hyperkalemia.', text)
        assertion_id = json.loads(c['investigation']['document_reviews'][0]['rows'][0]['documentary_state_json'])['assertions'][0]['id']
        self.assertNotIn(assertion_id, text)


if __name__ == '__main__':
    unittest.main()
