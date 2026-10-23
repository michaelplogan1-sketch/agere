"""Multi-entity and temporal composition in the fully consuming grammar (request_language.Parser.sequence ->
request_plan.Compiler.sequence): windows in days/weeks/months, unbounded before/after, first/last anchors, anchor periods,
fill leads and negated sequences, each chaining two received relations on the patient. Counts on the active sample are
checked against references computed independently in Python from the raw rows.

    AGERE_LOCAL_TYPED=off AGERE_LOCAL_NORMALIZER=off .venv/bin/python -m unittest test_temporal_composition
"""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import unittest

from clinical_core.agere.clinical import request_language as L, request_plan as R, sql_query

ROOT = Path(__file__).resolve().parent


def build():
    try: return Path(json.loads((ROOT/'config/agere/active_warehouse.json').read_text())['build'])
    except (OSError, ValueError, KeyError): return None


def days(a, b): return (dt.date.fromisoformat(a[:10]) - dt.date.fromisoformat(b[:10])).days


@unittest.skipUnless(build() and (build()/'stack/semantic.sqlite3').exists(), 'active sample warehouse not present')
class TemporalComposition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = R.Catalog.load(build()/'stack')
        cls.db = sqlite3.connect(f'file:{cls.cat.target}?mode=ro', uri=True)
        cls.stays = list(cls.db.execute("SELECT patient_id, admit_date, discharge_date, discharge_known FROM stay WHERE type = 'inpatient'"))
        cls.ed = list(cls.db.execute("SELECT patient_id, event_date FROM utilization_event WHERE kind = 'ed_visit' AND event_date IS NOT NULL"))
        cls.dead = {r[0] for r in cls.db.execute('SELECT patient_id FROM death')}  # patients are living patients (decision context, 2026-10-23)

    @classmethod
    def tearDownClass(cls): cls.db.close()

    def count(self, q):
        req = L.parse(self.cat, q)
        contract = R.compile_request(self.cat, q, req)
        db = sql_query.register(sqlite3.connect(f'file:{self.cat.target}?mode=ro', uri=True))
        try: rows = db.execute(contract['query']).fetchall()
        finally: db.close()
        return rows[0][0], req, contract

    def test_window_in_weeks_after_an_anchor_with_a_period(self):
        got, req, contract = self.count('How many patients had an ED visit within 2 weeks after a hospital discharge in 2029?')
        seq = req['where']
        self.assertEqual((seq['op'], seq['days'], seq['window']), ('sequence', [1, 14], {'n': 2, 'unit': 'week'}))
        ref = {p for p, _, d, known in self.stays if known == 1 and d and d[:4] == '2029'
               for q, e in self.ed if q == p and 1 <= days(e, d) <= 14}
        self.assertEqual(got, len(ref - self.dead))
        self.assertIn('2 week(s) compiled as 14 days (7 days per week).', contract['notes'])  # disclosed (SEQ-WINDOW)

    def test_unbounded_before_the_first_admission(self):
        got, req, _ = self.count('How many patients had an ED visit before their first hospital admission?')
        self.assertEqual(req['where']['anchor']['rank'], 'first')
        first = {}
        for p, a, _, _ in self.stays:
            if a and (p not in first or a < first[p]): first[p] = a
        ref = {p for p, e in self.ed if p in first and -3650 <= days(e, first[p]) <= 0}
        self.assertEqual(got, len(ref - self.dead))

    def test_fill_lead_months_window_and_first_anchor(self):
        got, req, _ = self.count('How many patients filled a statin within 3 months after their first inpatient admission?')
        self.assertEqual((req['where']['days'], req['where']['then']['table']), ([1, 90], 'fill'))
        first = {}
        for p, a, _, _ in self.stays:
            if a and (p not in first or a < first[p]): first[p] = a
        fills = self.db.execute("SELECT patient_id, fill_date FROM fill WHERE drug_class = 'statin'").fetchall()
        ref = {p for p, f in fills if p in first and f and 1 <= days(f, first[p]) <= 90}
        self.assertEqual(got, len(ref - self.dead))

    def test_negated_sequence_and_chained_conjunction(self):
        q = 'How many patients had an ED visit within 30 days after discharge and no ED visit within 7 days after discharge?'
        got, req, contract = self.count(q)
        args = req['where']['args']
        self.assertEqual([a['op'] for a in args], ['sequence', 'not'])
        self.assertIn('NOT EXISTS', contract['query'].replace('(NOT EXISTS', 'NOT EXISTS'))
        known = [(p, d) for p, _, d, k in self.stays if k == 1 and d]
        within = lambda lo, hi: {p for p, d in known for q2, e in self.ed if q2 == p and lo <= days(e, d) <= hi}
        self.assertEqual(got, len(within(1, 30) - within(1, 7) - self.dead))

    def test_mismatched_window_is_refused(self):
        req = L.parse(self.cat, 'How many patients had an ED visit within 2 weeks after a hospital discharge in 2029?')
        req['where']['window'] = {'n': 3, 'unit': 'week'}
        with self.assertRaises(R.PlanError):
            R.compile_request(self.cat, 'How many patients had an ED visit within 2 weeks after a hospital discharge in 2029?', req)


if __name__ == '__main__':
    unittest.main()
