"""General intent slots and independent synthetic edge cases; no closed bank."""
import sqlite3
import unittest
from collections import Counter
import test_population_forms as F
from clinical_core.agere.clinical import population_engines as E, request_plan as R

class InsightGeneralization(unittest.TestCase):
    def setUp(self):
        self.f=F.EngineForms(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.catalog=R.Catalog.load(self.f.path)

    def test_frequent_use_shapes_include_all_event_owners(self):
        counts=Counter(p for p,d in F.ED if F.living(p) and F.in_win(d))
        expected=sorted(p for p,n in counts.items() if n>=3)
        # Mike 2026-10-25: patient lists stay living, but both event-share readings include deceased patients.
        events=Counter(p for p,d in F.ED if F.in_win(d))
        for q in ('Identify frequent ED users and the share of ED volume they account for.',
                  'Which patients use the ED frequently, and what portion of all ED visits do they generate?',
                  'Frequent ED utilizers — who are they, and how much of ED volume comes from them?',
                  'Who are our frequent emergency department users and what share of emergency department volume comes from them?'):
            with self.subTest(q=q):
                a=self.f.answer(q);facts={v['id']:v for v in a['insight']['facts']}
                self.assertEqual(a['patient_ids'],expected)
                self.assertEqual(facts['visits']['denominator'],sum(events.values()))
                self.assertEqual(facts['visits']['numerator'],sum(n for n in events.values() if n>=3))

    def test_priority_wording_uses_same_existing_tiers(self):
        base=self.f.answer('Who should we prioritize for diabetes outreach?')
        for q in ('Which diabetes patients should we reach out to first?',
                  'Who are the priority patients for diabetes outreach?',
                  'Whom should we target first for diabetes outreach?'):
            with self.subTest(q=q):
                a=self.f.answer(q)
                self.assertEqual(a['patient_ids'],base['patient_ids'])
                self.assertEqual(a['insight']['facts'],base['insight']['facts'])

    def test_care_gap_wording_keeps_existing_measure_definition(self):
        base=self.f.answer('Where are the biggest gaps in CKD care?')
        for q in ('What is our biggest shortfall in chronic kidney disease care?', 'Which CKD care measure has the biggest gap?'):
            with self.subTest(q=q):
                self.assertEqual(self.f.answer(q)['insight']['facts'],base['insight']['facts'])

    def test_extra_constraints_are_not_swallowed_by_intent_words(self):
        for q in ('Identify frequent ED users and their ED volume in 2027',
                  'Identify frequent ED users under age 21 and their ED volume',
                  'Which diabetes patients should we target first excluding CKD?',
                  'Who are frequent ED users with at least 8 visits?'):
            with self.subTest(q=q): self.assertIsNone(E.interpret(self.catalog,q))

    def test_zero_ed_denominator_is_unknown_not_error_or_zero_percent(self):
        with sqlite3.connect(self.f.path/'semantic.sqlite3') as db:db.execute('DELETE FROM utilization_event')
        a=self.f.answer('Identify frequent ED users and the share of ED volume they account for.')
        facts={v['id']:v for v in a['insight']['facts']}
        self.assertEqual(a['patient_ids'],[])
        self.assertEqual(facts['visits']['denominator'],0)
        self.assertIsNone(facts['visits']['value'])
        self.assertIn('unknown share',a['headline'])
