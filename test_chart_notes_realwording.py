"""Real-world wording for the chart reader's notes path: document kinds ("annual physical", "consult letter", "d/c summary", "H&P"), tests and
measurements named by their own words, relative follow-ups ("that visit"), dates after "on", and scanned (OCR) passages. Pure functions only:
no stack, no model."""
import unittest

from clinical_core.agere.clinical import chart_intents as CI
from clinical_core.agere.clinical import chart_notes as CN
from clinical_core.agere.clinical import note_evidence as NE

AS_OF = '2030-05-27'


def read(q):
    it = CI.parse(q, AS_OF, quotes=[], lexicon=None)
    note = CN.is_note_question(q, it)
    return note, (CN.build(q, AS_OF) if note else None)


def first(q):
    note, preds = read(q)
    return note, (preds or [None])[0]


class DocumentKinds(unittest.TestCase):
    def test_document_kind_makes_a_note_question(self):
        for q in ["What was her LDL on the March 2029 annual physical? P-1001",
                  "What did the cardiology consult letter recommend for P-1001?",
                  "Per the d/c summary, where did P-1001 go after discharge?",
                  "What did the H&P say about her smoking history? P-1001",
                  "What did the colonoscopy show for P-1001?",
                  "Do the outside records mention a prior stroke for P-1001?",
                  "What did the nurse record about her pain at the 2029-05-01 follow-up call? P-1001"]:
            self.assertTrue(read(q)[0], q)

    def test_structured_questions_stay_structured(self):
        for q in ["What was the last A1c for P-1001?", "When was P-1001's last visit?", "What medications is P-1001 on?",
                  "Is P-1001 due for an annual physical?", "Pull up the last PHQ-9 score for P-1001", "Show me the claims for P-1001 in 2029",
                  "How many ED visits did P-1001 have last year?"]:
            self.assertFalse(read(q)[0], q)

    def test_visit_needs_a_content_ask_and_a_date(self):
        self.assertTrue(read("Who brought P-1001 to the ED visit in May 2029?")[0])
        self.assertFalse(read("How many office visits did P-1001 have in May 2029?")[0])

    def test_discharge_summary_spelling_binds_the_note_type(self):
        _, p = first("Per the d/c summary, where did P-1001 go after discharge?")
        self.assertEqual(p['frame'], 'plan_followup')
        self.assertIn('discharge_summary', p.get('note_types') or [])


class FramesAndConcepts(unittest.TestCase):
    def test_named_measurement_is_a_value_question(self):
        for q, term in [("What was the HbA1c at the June 2028 physical for P-1001?", 'HbA1c'), ("What PHQ-2 did P-1001 score at the May 2029 preventive review?", 'PHQ-2'),
                        ("What's the BMI documented at the annual wellness exam for P-1001?", 'BMI')]:
            note, p = first(q)
            self.assertTrue(note, q)
            self.assertEqual((p['frame'], p['concept']['term']), ('finding_value', term), q)

    def test_test_named_by_its_own_word(self):
        _, p = first("What was the impression on the November 2028 MRI report scanned in for P-1001?")
        self.assertEqual((p['frame'], p['concept']['term']), ('procedure_test', 'MRI'))
        _, p = first("What did surgical pathology show for P-1001 after the colectomy in June 2028?")
        self.assertEqual((p['frame'], p['concept']['term']), ('procedure_test', 'surgical pathology'))
        self.assertEqual(p['window']['start'], '2028-06-01')
        self.assertEqual(p['window']['end'], AS_OF)  # "after <event> in June 2028": from that month on

    def test_size_of_a_finding(self):
        _, p = first("how big was the lung nodule on the July 2029 imaging report for P-1001, in mm?")
        self.assertEqual((p['frame'], p['concept']['term']), ('finding_value', 'lung nodule'))
        self.assertIn('radiology_report', p['note_types'])

    def test_which_drug_is_a_medication_question_without_a_drug_name(self):
        _, p = first("What did the specialist letter from March 2028 recommend starting for P-1001?")
        self.assertEqual((p['frame'], p['concept']['term']), ('medication_event', 'medication'))
        self.assertIn('planned_not_done', p['statuses'])  # a recommendation is not shown as done

    def test_class_named_by_kind(self):
        _, p = first("Which anti-inflammatory did the orthopedics letter from October 2027 suggest for P-1001?")
        self.assertEqual(p['concept']['term'], 'anti-inflammatory medication')

    def test_condition_named_in_the_concept_library(self):
        _, p = first("Do the outside records mention any prior stroke for P-1001?")
        self.assertEqual((p['frame'], p['concept']['term']), ('condition', 'stroke'))

    def test_who_prompted_the_visit(self):
        _, p = first("Who pushed P-1001 to get seen at the July 2029 acute visit?")
        self.assertEqual((p['frame'], p['concept']['term']), ('preference_refusal', 'urged'))

    def test_refusal_object(self):
        _, p = first("Is there a note saying P-1001 refused the flu shot?")
        self.assertEqual((p['frame'], p['concept']['term']), ('preference_refusal', 'flu shot'))

    def test_social_need_by_name(self):
        _, p = first("Did the social worker document any housing problems for P-1001?")
        self.assertEqual((p['frame'], p['concept']['term']), ('social_need', 'housing'))

    def test_document_words_are_not_the_asked_frame(self):
        _, preds = read("What did the nurse note about her pain at the follow-up call on 2029-05-01? P-1001")
        self.assertEqual([p['frame'] for p in preds], ['symptom'])  # "follow-up call" is where to read, not a follow-up plan


class Windows(unittest.TestCase):
    def test_date_after_on_is_the_encounter_day_for_a_lookup(self):
        _, p = first("What pain score did the nurse record on 2029-08-21 for P-1001?")
        self.assertEqual((p['window']['start'], p['window']['end']), ('2029-08-21', '2029-08-21'))

    def test_a_date_that_only_dates_a_medication_event_is_not_a_note_day(self):
        _, p = first("Why was lisinopril stopped on 2029-08-05 for P-1001?")
        self.assertIsNone(p.get('window'))

    def test_relative_span_inside_a_dated_note_is_not_the_window(self):
        _, p = first("How many of the last 14 days did P-1001 say they used the albuterol inhaler, per the May 2027 callback?")
        self.assertEqual(p['window']['start'], '2027-05-01')
        self.assertNotIn('_count', p)  # "0 of 14 days" is stated by the passage; there is no event count to make

    def test_back_reference_inherits_the_encounter(self):
        _, prior = first("What was the HbA1c at the June 2028 annual physical for P-1001?")
        self.assertTrue(CN.back_references('and her PHQ-9 that day?', [prior]))
        preds = CN.build('and her PHQ-9 that day?', AS_OF, prior_predicates=[prior])
        self.assertEqual(preds[0]['concept']['term'], 'PHQ-9')
        self.assertEqual(preds[0]['window']['start'], '2028-06-01')
        self.assertEqual(preds[0].get('note_types'), prior.get('note_types'))
        self.assertFalse(CN.back_references('and her PHQ-9 that day?', None))


class ScannedText(unittest.TestCase):
    def test_ocr_fold_keeps_length_and_matches_a_scanned_word(self):
        text = 'PLAN: I recommendedwe me1oxicam 15 mg daily as needed; PT referral.'
        self.assertEqual(len(NE.ocr_fold(text)), len(text))
        ps = NE.passages_in(text, NE.concept_patterns({'term': 'anti-inflammatory medication'}))
        self.assertTrue(ps)
        self.assertIn('me1oxicam', ps[0]['text'])  # quoted as written


if __name__ == '__main__':
    unittest.main()
