"""Counterexamples for the four note-correctness defects of the 2026-10-24 review, section 3 (fake provider, fake warehouse; no network).

    .venv/bin/python -m unittest tools.note_labels.test_p1_note_correctness
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_core.agere.clinical import chart_notes as CN
from clinical_core.agere.clinical import note_evidence as ne
from clinical_core.agere.clinical import note_frames as nf
from clinical_core.agere.clinical import note_questions as nq
from tools.note_labels import test_note_frames as TF
from tools.note_labels import test_note_open as TO

AS_OF = '2030-05-27'


class FakeNotes:
    """Stand-in for note_frames.answer_patient: canned readings per (frame, term); records every predicate it was asked."""
    FRAMES = {}

    def __init__(self, readings):
        self.calls, self.readings = [], readings

    def validate_predicate(self, p):
        return p

    def answer_patient(self, stack, predicate, patient_id, *, as_of, ledger=None, allow_live=True):
        self.calls.append(predicate)
        return self.readings.get((predicate['frame'], predicate['concept']['term']),
                                 {'status': 'not_mentioned', 'holds': False, 'detail': {}, 'evidence': [], 'p': 0.9})


EV = [{'record_id': 'N-1', 'date': '2029-08-05', 'note_type': 'progress_note', 'quote': 'ACS ruled out by troponins.', 'supports': True}]


class NegativeAssertion(unittest.TestCase):
    def pred(self):
        return {'op': 'note', 'frame': 'condition', 'concept': {'term': 'acute coronary syndrome', 'codes': [], 'quote': 'acute coronary syndrome'},
                'statuses': ['denied'], 'combine': 'any'}

    def test_ruled_out_is_yes_when_the_notes_deny_it(self):
        nfk = FakeNotes({('condition', 'acute coronary syndrome'): {'status': 'denied', 'holds': True, 'detail': {}, 'evidence': EV, 'p': 0.95}})
        a = CN.render(None, [self.pred()], 'P1', AS_OF, nf=nfk, yes_no=True)
        self.assertEqual(a['facts']['lead'], 'Yes')

    def test_ruled_out_is_no_when_the_notes_document_it(self):
        nfk = FakeNotes({('condition', 'acute coronary syndrome'): {'status': 'documented', 'holds': False, 'detail': {}, 'evidence': EV, 'p': 0.95}})
        self.assertEqual(CN.render(None, [self.pred()], 'P1', AS_OF, nf=nfk, yes_no=True)['facts']['lead'], 'No')

    def test_a_denied_reading_is_still_no_for_a_positive_question(self):
        p = {**self.pred(), 'statuses': ['documented', 'denied']}  # "is smoking documented/does he smoke": denied is the answer No
        nfk = FakeNotes({('condition', 'acute coronary syndrome'): {'status': 'denied', 'holds': True, 'detail': {}, 'evidence': EV, 'p': 0.95}})
        self.assertEqual(CN.render(None, [p], 'P1', AS_OF, nf=nfk, yes_no=True)['facts']['lead'], 'No')


class Completeness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = TF.FakeDB(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_retrieval_reports_truncation(self):
        capped = ne.retrieve(self.db, 'p1', {'term': 'metformin'}, as_of=AS_OF, max_passages=5)
        self.assertTrue(capped.truncated)
        self.assertGreater(capped.candidates, 5)
        wide = ne.retrieve(self.db, 'p1', {'term': 'metformin'}, as_of=AS_OF, max_passages=500, per_record=50)
        self.assertFalse(wide.truncated)
        self.assertEqual(wide.candidates, len(wide))

    def r(self, status, day):
        return TF.CombineTests.reading(None, status, day)

    def test_absence_from_a_capped_set_is_unknown_not_no(self):
        p = nf.validate_predicate(TF.pred(combine='any'))
        rs = [self.r('other_person', '2029-01-01'), self.r('not_mentioned', '2029-02-01')]
        self.assertFalse(nq.combine(p, rs)['holds'])  # complete evidence: a real no
        c = nq.combine(p, rs, True, 2, 20)
        self.assertEqual((c['status'], c['holds']), ('unknown', None))
        self.assertIn('2 of 20', c['partial'])
        self.assertEqual(nq.combine(p, [], True, 0, 3)['status'], 'unknown')

    def test_earliest_latest_and_no_contradiction_need_the_whole_set(self):
        rs = [self.r('documented', '2029-01-01'), self.r('documented', '2029-02-01')]
        for rule in ('first', 'latest'):
            p = nf.validate_predicate(TF.pred(combine=rule))
            self.assertTrue(nq.combine(p, rs)['holds'])
            self.assertIsNone(nq.combine(p, rs, True, 2, 20)['holds'])
        p = nf.validate_predicate(TF.pred(statuses=['documented', 'denied'], combine='conflict'))
        self.assertIsNone(nq.combine(p, rs, True, 2, 20)['holds'])

    def test_a_hit_survives_truncation(self):
        p = nf.validate_predicate(TF.pred(combine='any'))
        c = nq.combine(p, [self.r('documented', '2029-01-01')], True, 1, 20)
        self.assertEqual((c['status'], c['holds']), ('documented', True))
        self.assertTrue(c['truncated'])

    def test_evaluate_marks_a_capped_search_partial(self):
        p = nf.validate_predicate(TF.pred(statuses=['denied'], combine='any'))
        TF.fake_decide.conf = {}
        out = nq.evaluate(self.db, p, ['p1'], as_of=AS_OF, workers=1, decide=TF.fake_decide, max_passages=2)['p1']
        self.assertEqual((out['holds'], out['status']), (None, 'unknown'))  # the denial sits outside the 2 passages read

    def test_render_says_the_answer_is_partial(self):
        nfk = FakeNotes({('condition', 'x'): {'status': 'unknown', 'holds': None, 'detail': {}, 'evidence': [], 'truncated': True,
                                              'partial': 'only 12 of 40 matching passages were read'}})
        pred = {'op': 'note', 'frame': 'condition', 'concept': {'term': 'x', 'codes': []}, 'statuses': ['denied'], 'combine': 'any'}
        a = CN.render(None, [pred], 'P1', AS_OF, nf=nfk, yes_no=True)
        self.assertEqual(a['facts']['lead'], 'Undecided')
        self.assertIn('only 12 of 40', a['headline'])


class DigitsDedup(unittest.TestCase):
    def test_passages_that_differ_in_numbers_are_not_merged(self):
        self.assertNotEqual(nq._norm_text('Reports 2 falls.'), nq._norm_text('Reports 8 falls.'))
        self.assertEqual(nq._norm_text('Reports  2 FALLS.'), nq._norm_text('reports 2 falls.'))

    def test_each_numbered_reading_is_read_on_its_own(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TO.DB(tmp.name)
        c = sqlite3.connect(db.path)
        for rid, day, n in (('Z1', '2027-01-01', 2), ('Z2', '2027-02-01', 8)):
            c.execute('insert into record values(?,?,?,?,?,?,?,?)', (rid, 'p9', 'notes', day, day, day, 'o', json.dumps(
                {'text': f'Allergies: sulfa (rash, {n} reactions).', 'note_type': 'office_followup'})))
        c.commit()
        c.close()
        TO.fake_decide.calls.clear()
        TO.fake_decide.conf = {}
        pred = TO.open_pred('adverse_event', 'adverse', 'drug intolerance')
        nq.evaluate(db, pred, ['p9'], as_of=AS_OF, workers=1, decide=TO.fake_decide)
        asked = [q for q in TO.fake_decide.calls if 'sulfa' in q and 'what does this' in q.lower()]
        self.assertEqual(len(asked), 2)


class UnresolvedAnchor(unittest.TestCase):
    def test_an_anchor_with_no_date_does_not_read_every_note(self):
        anchor = {'op': 'note', 'frame': 'preference_refusal', 'concept': {'term': 'goals of care', 'codes': []}, 'statuses': ['documented'], 'combine': 'any'}
        main = {'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'lisinopril', 'codes': []}, 'statuses': ['documented'], 'combine': 'any',
                '_anchor': anchor}
        nfk = FakeNotes({('medication_event', 'lisinopril'): {'status': 'documented', 'holds': True, 'detail': {}, 'evidence': EV, 'p': 0.9}})
        a = CN.render(None, [main], 'P1', AS_OF, nf=nfk, yes_no=True)
        self.assertEqual([c['concept']['term'] for c in nfk.calls], ['goals of care'])  # the later notes were never read
        self.assertEqual(a['facts']['lead'], 'Undecided')
        self.assertIn('no dated documentation', a['headline'])
        self.assertIn('not read', a['headline'])


class CappedOpenAndAnchors(unittest.TestCase):
    """Positive findings from a capped read stand; their dates, record counts and the anchor date are qualified as partial."""

    def reading(self, rid, day):
        return {'finding': {'concept': 'sulfa rash', 'source': 'quote', 'quote': 'sulfa rash'}, 'detail': {}, 'duplicates': [], 'status': 'documented', 'p': 0.9, 'passage_id': rid + ':0',
                'record_id': rid, 'date': day, 'note_type': 'office_followup', 'source': 'notes', 'quote': 'sulfa rash', 'start': 0, 'end': 9,
                'undecided': False, 'cost_usd': 0.0}

    def pred(self):
        return {'op': 'note', 'frame': 'adverse_event', 'concept': {'term': 'drug intolerance', 'codes': [], 'open': True, 'class': 'adverse'},
                'statuses': ['documented'], 'combine': 'any'}

    def test_capped_findings_carry_the_truncation_and_bound_the_dates(self):
        out = nq.combine_open(self.pred(), [self.reading('R1', '2027-01-01'), self.reading('R2', '2027-02-01')], True, 2, 9)
        self.assertTrue(out['holds'])  # the finding stands
        self.assertTrue(out['truncated'])
        self.assertIn('2 of 9', out['partial'])
        self.assertTrue(all(g['bounds_partial'] for g in out['findings']))

    def test_uncapped_findings_are_not_qualified(self):
        out = nq.combine_open(self.pred(), [self.reading('R1', '2027-01-01')], False, 1, 1)
        self.assertNotIn('truncated', out)
        self.assertFalse(out['findings'][0].get('bounds_partial'))

    def test_render_says_the_dates_and_count_are_partial(self):
        out = nq.combine_open(self.pred(), [self.reading('R1', '2027-01-01'), self.reading('R2', '2027-02-01')], True, 2, 9)
        nfk = FakeNotes({('adverse_event', 'drug intolerance'): out})
        a = CN.render(None, [self.pred()], 'P1', AS_OF, nf=nfk, yes_no=True)
        self.assertIn('at least 2 record(s)', a['headline'])
        self.assertIn('not first/last overall', a['headline'])
        self.assertIn('partial', a['headline'])

    def test_capped_count_is_at_least(self):
        out = nq.combine_open(self.pred(), [self.reading('R1', '2027-01-01')], True, 1, 9)
        self.assertIn('at least', CN.count_events(out, True)['basis'])

    def test_capped_anchor_is_qualified(self):
        anchor = {'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'metformin', 'codes': []}, 'statuses': ['documented'], 'combine': 'any'}
        main = {'op': 'note', 'frame': 'condition', 'concept': {'term': 'nausea', 'codes': []}, 'statuses': ['documented'], 'combine': 'any', '_anchor': anchor}
        ev = [{'record_id': 'A-1', 'date': '2029-03-01', 'note_type': 'progress_note', 'quote': 'metformin started', 'supports': True}]
        for capped in (True, False):
            nfk = FakeNotes({('medication_event', 'metformin'): {'status': 'documented', 'holds': True, 'detail': {}, 'evidence': ev, 'p': 0.9,
                                                                 **({'truncated': True, 'partial': 'only 30 of 80 matching passages were read'} if capped else {})},
                             ('condition', 'nausea'): {'status': 'documented', 'holds': True, 'detail': {}, 'evidence': EV, 'p': 0.9}})
            a = CN.render(None, [main], 'P1', AS_OF, nf=nfk, yes_no=True)
            self.assertEqual('partial anchor' in a['headline'], capped)

    def test_citation_is_the_supporting_passage_not_every_record_read(self):
        ev = [{'record_id': 'OLD', 'date': '2028-01-01', 'note_type': 'progress_note', 'quote': 'old unrelated mention', 'supports': False},
              {'record_id': 'NEW', 'date': '2029-01-01', 'note_type': 'progress_note', 'quote': 'metformin stopped', 'supports': True}]
        pred = {'op': 'note', 'frame': 'medication_event', 'concept': {'term': 'metformin', 'codes': []}, 'statuses': ['documented'], 'combine': 'latest'}
        nfk = FakeNotes({('medication_event', 'metformin'): {'status': 'documented', 'holds': True, 'detail': {}, 'evidence': ev, 'p': 0.9}})
        a = CN.render(None, [pred], 'P1', AS_OF, nf=nfk, yes_no=True)
        self.assertEqual([s['record_id'] if 'record_id' in s else s.get('id') for s in a['sources']].count('OLD'), 0)
        self.assertNotIn('OLD', a['headline'])
        self.assertIn('NEW', a['headline'])


if __name__ == '__main__':
    unittest.main()
