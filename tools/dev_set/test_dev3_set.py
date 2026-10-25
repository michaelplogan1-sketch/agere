"""Tests for the independent development set (split dev3): every key computes, the file matches a rebuild, no question echoes a dev / dev2 /
paraphrase question, and 15 keys re-derive by an alternative method (Python over raw rows instead of the key's SQL).

    .venv/bin/python -m unittest tools.dev_set.test_dev3_set
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import unittest
from collections import Counter, defaultdict
from pathlib import Path

from tools.dev_set import definitions as D
from tools.dev_set import dev3_keys as K
from tools.dev_set.keys import connect

HERE = Path(__file__).resolve().parent
ROWS = K.read()
BY_ID = {r['id']: r for r in ROWS}
BY_KEY = {r['key_name']: r for r in ROWS}


def _non_heldout(path):
    """Questions of a dev file; held-out lines are dropped BEFORE they are parsed."""
    out = []
    for line in path.read_text().splitlines():
        if not line.strip() or '"split": "heldout"' in line:
            continue
        out.append(json.loads(line))
    return out


def _norm(text):
    return re.sub(r'[^a-z0-9 ]+', ' ', text.lower()).split()


def _sim(a, b):
    return difflib.SequenceMatcher(None, ' '.join(_norm(a)), ' '.join(_norm(b))).ratio()


class Shape(unittest.TestCase):
    def test_counts(self):
        c = Counter(r['bucket'] for r in ROWS)
        self.assertEqual(set(c), set(K.BUCKETS))
        self.assertTrue(all(v == 30 for v in c.values()), c)
        self.assertEqual(len(ROWS), 150)
        self.assertTrue(all(r['split'] == 'dev3' for r in ROWS))
        self.assertGreaterEqual(sum(1 for r in ROWS if r['answer_type'] == 'absent'), 15 - 1)

    def test_follow_ups_resolve(self):
        for r in ROWS:
            if r['follow_up_of']:
                parent = BY_ID[r['follow_up_of']]
                self.assertEqual(parent['bucket'], r['bucket'])
                self.assertLess(parent['id'], r['id'])
                self.assertTrue(r['key'].get('standalone'), r['id'])

    def test_sources_are_listed(self):
        for r in ROWS:
            self.assertTrue(r['source'], r['id'])
            for s in r['source']:
                self.assertIn(s, K.SOURCES)

    def test_every_key_computes(self):
        for r in ROWS:
            k, t = r['key'], r['answer_type']
            if t == 'absent':
                self.assertTrue(k.get('reason'), r['id'])
            elif t == 'rubric':
                facts = k['answer']['required_facts']
                self.assertTrue(facts, r['id'])
                for f in facts:
                    self.assertTrue(f.get('value') is not None or f.get('patterns') or f.get('items'), (r['id'], f['id']))
            elif t == 'note':
                self.assertIsNotNone(k['answer'], r['id'])
                self.assertTrue(k.get('scope_records'), r['id'])
                if k['kind'] != 'not_documented':
                    self.assertTrue(r['evidence'], r['id'])
            elif t == 'patient_set' or t == 'table' or t == 'list':
                self.assertTrue(k['answer'], r['id'])
            else:
                self.assertIsNotNone(k['answer'], r['id'])
                if t == 'share':
                    self.assertTrue(k.get('denominator'), r['id'])
        self.assertEqual(sum(1 for r in ROWS if r['answer_type'] == 'count' and r['key']['answer'] == 0), 0)

    def test_file_matches_rebuild(self):
        rows = K.build_rows()
        self.assertEqual(len(rows), len(ROWS))
        for r in rows:
            self.assertEqual(json.dumps(BY_ID[r['id']]['key'], sort_keys=True), json.dumps(r['key'], sort_keys=True), r['id'])

    def test_note_quotes_are_exact(self):
        conn = connect()
        for r in ROWS:
            for ev in r['evidence']:
                if 'quote' not in ev:
                    continue
                row = conn.execute('SELECT text FROM scanned_document_text WHERE record_id = ?', (ev['record_id'],)).fetchone()
                if row:
                    text = row[0]
                else:
                    text = json.loads(conn.execute('SELECT payload FROM record WHERE record_id = ?', (ev['record_id'],)).fetchone()[0]).get('text')
                self.assertIn(ev['quote'], text, r['id'])


class NoOverlap(unittest.TestCase):
    def test_no_question_within_085_of_dev_dev2_or_paraphrase(self):
        old = []
        for name in ('questions.jsonl', 'compose_questions.jsonl', 'paraphrases.jsonl'):
            for q in _non_heldout(HERE / name):
                old.append(q['question'])
                if q['key'].get('standalone'):
                    old.append(q['key']['standalone'])
        worst = []
        for r in ROWS:
            mine = [r['question']] + ([r['key']['standalone']] if r['key'].get('standalone') else [])
            for m in mine:
                best = max((_sim(m, o), o) for o in old)
                worst.append((best[0], r['id'], m, best[1]))
        worst.sort(reverse=True)
        self.assertLess(worst[0][0], 0.85, worst[:3])


# --- alternative derivations (Python over raw rows) -------------------------------------------------------------------------------
class SpotChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = connect()
        cls.dead = {r[0] for r in cls.c.execute('SELECT patient_id FROM death')}
        cls.dup = {r[0] for r in cls.c.execute("SELECT patient_id FROM patient WHERE data_segment = 'duplicate_chart'")}
        cls.living = {r[0] for r in cls.c.execute('SELECT patient_id FROM patient')} - cls.dead - cls.dup
        cls.birth = dict(cls.c.execute('SELECT patient_id, birth_date FROM patient').fetchall())
        cls.sex = dict(cls.c.execute('SELECT patient_id, sex_reported FROM patient').fetchall())

    def age(self, pid):
        b = dt.date.fromisoformat(self.birth[pid])
        a = dt.date.fromisoformat(D.AS_OF)
        return a.year - b.year - ((a.month, a.day) < (b.month, b.day))

    def q(self, key):
        return BY_KEY[key]['key']

    def pid(self, key):
        return re.search(r'\b(?:SYN-[0-9A-F]{8}-[0-9A-F]{3}|WHP-[0-9A-F]{12}|P-[A-Z0-9]+)\b', BY_KEY[key]['question']).group(0)

    def has_condition(self, name):
        """patients with condition evidence, derived from the raw tables by ICD-10 prefix in Python"""
        prefixes = tuple(p.rstrip('%') for p in D.CONDITIONS[name])
        s = set()
        for pid, icd, dx, basis in self.c.execute('SELECT patient_id, icd10, is_diagnosis, diagnosis_basis FROM condition WHERE icd10 IS NOT NULL'):
            if icd.startswith(prefixes) and (dx == 1 or (dx is None and basis == 'ehr_problem_list')):
                s.add(pid)
        for pid, icd in self.c.execute('SELECT patient_id, icd10 FROM problem_list_entry WHERE icd10 IS NOT NULL'):
            if icd.startswith(prefixes):
                s.add(pid)
        for pid, icd in self.c.execute('SELECT patient_id, icd10 FROM payer_claim_diagnosis WHERE counts_in_metrics = 1 AND icd10 IS NOT NULL'):
            if icd.startswith(prefixes):
                s.add(pid)
        if name == 'diabetes':
            s |= {pid for pid, v in self.c.execute("SELECT patient_id, value_canonical FROM measurement WHERE concept = 'hba1c' AND value_canonical IS NOT NULL AND result_current = 1 "
                                                   "AND excluded_reason IS NULL AND source_table NOT IN ('claim_line', 'payer_claim_line')") if v >= 6.5}
        return s & self.living

    def results(self, concept):
        out = defaultdict(list)
        for pid, d, v in self.c.execute("SELECT patient_id, event_date, value_canonical FROM measurement WHERE concept = ? AND value_canonical IS NOT NULL AND result_current = 1 "
                                        "AND excluded_reason IS NULL AND source_table NOT IN ('claim_line', 'payer_claim_line')", (concept,)):
            out[pid].append((d, v))
        return out

    # 1
    def test_age(self):
        self.assertEqual(self.age(self.pid('age')), self.q('age')['answer'])

    # 2
    def test_no_shows(self):
        pid = self.pid('no_shows')
        n = sum(1 for (st, d) in self.c.execute('SELECT final_status, scheduled_date FROM appointment WHERE patient_id = ?', (pid,))
                if st == 'no_show' and D.LAST_YEAR_START <= d <= D.AS_OF)
        self.assertEqual(n, self.q('no_shows')['answer'])

    # 3
    def test_ldl_peak(self):
        pid = self.pid('ldl_peak')
        self.assertAlmostEqual(max(v for _, v in self.results('ldl')[pid]), self.q('ldl_peak')['answer'], places=3)

    # 4 and 13: PDC by day-by-day simulation (the key uses a set of covered days built per fill; this re-derives with interval merging)
    @staticmethod
    def _pdc_intervals(fills, start, end):
        fills = sorted((d, s) for d, s in fills if start <= d <= end)
        if len(fills) < 2:
            return None
        d0 = dt.date.fromisoformat(start)
        last = dt.date.fromisoformat(end)
        first = dt.date.fromisoformat(fills[0][0])
        free, covered = first, 0
        for d, s in fills:
            begin = max(dt.date.fromisoformat(d), free)
            finish = begin + dt.timedelta(days=s)
            covered += max(0, (min(finish, last + dt.timedelta(days=1)) - begin).days)
            free = finish
        return covered / ((last - first).days + 1)

    def test_statin_pdc(self):
        pid = self.pid('statin_pdc')
        fills = [(d, s) for d, s in self.c.execute("SELECT fill_date, days_supply FROM fill WHERE patient_id = ? AND drug_class = 'statin'", (pid,))]
        self.assertAlmostEqual(self._pdc_intervals(fills, '2029-01-01', '2029-12-31'), self.q('statin_pdc')['answer'], delta=0.0005)

    def test_statin_adherence(self):
        by = defaultdict(list)
        for pid, d, s in self.c.execute("SELECT patient_id, fill_date, days_supply FROM fill WHERE drug_class = 'statin'"):
            if pid in self.living:
                by[pid].append((d, s))
        vals = [self._pdc_intervals(v, '2029-01-01', '2029-12-31') for v in by.values()]
        vals = [x for x in vals if x is not None]
        k = self.q('statin_adherence')
        self.assertEqual((sum(1 for x in vals if x >= 0.8), len(vals)), (k['numerator'], k['denominator']))

    # 5
    def test_hba1c_testing(self):
        den = {p for p in self.has_condition('diabetes') if 18 <= self.age(p) <= 75}
        r = self.results('hba1c')
        num = {p for p in den if any(d.startswith('2029') for d, _ in r.get(p, []))}
        k = self.q('hba1c_testing')
        self.assertEqual((len(num), len(den)), (k['numerator'], k['denominator']))

    # 6
    def test_spd_statin(self):
        den = {p for p in self.has_condition('diabetes') if 40 <= self.age(p) <= 75}
        on = {pid for (pid,) in self.c.execute("SELECT DISTINCT patient_id FROM fill WHERE drug_class = 'statin' AND substr(fill_date, 1, 4) = '2029'")}
        k = self.q('spd_statin')
        self.assertEqual((len(den & on), len(den)), (k['numerator'], k['denominator']))

    # 7
    def test_plan_readmit(self):
        rows = [(p, rd) for p, rd, d in self.c.execute("SELECT patient_id, readmitted_30d, event_date FROM utilization_event WHERE kind = 'inpatient'") if d.startswith('2029') and p in self.living]
        k = self.q('plan_readmit')
        self.assertEqual((sum(rd for _, rd in rows), len(rows)), (k['numerator'], k['denominator']))

    # 8
    def test_deaths_2029(self):
        n = sum(1 for p, d in self.c.execute('SELECT patient_id, death_date FROM death') if d.startswith('2029') and p not in self.dup)
        self.assertEqual(n, self.q('deaths_2029')['answer'])

    # 9
    def test_avg_los(self):
        xs = [(dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days for p, s, e in self.c.execute("SELECT patient_id, event_date, end_date FROM utilization_event WHERE kind = 'inpatient' AND end_date IS NOT NULL")
              if s.startswith('2029') and p in self.living]
        self.assertAlmostEqual(sum(xs) / len(xs), self.q('avg_los_2029')['answer'], delta=0.002)

    # 10
    def test_mammo_gap(self):
        lo = (dt.date.fromisoformat(D.AS_OF) - dt.timedelta(days=0)).replace(year=int(D.AS_OF[:4]) - 2).isoformat()   # 24 months back
        seen = {p for p, d in self.c.execute("SELECT patient_id, event_date FROM cancer_screening WHERE test = 'bilateral_screening_mammography'") if lo < d <= D.AS_OF}
        seen |= {p for p, d in self.c.execute("SELECT patient_id, service_date FROM procedure_line WHERE cpt = '77067' AND counts_in_metrics = 1") if lo < d <= D.AS_OF}
        n = sum(1 for p in self.living if self.sex[p] == 'F' and 50 <= self.age(p) <= 74 and p not in seen)
        self.assertEqual(n, self.q('mammo_gap')['answer'])

    # 11
    def test_top10_members(self):
        tot = defaultdict(int)
        for p, c in self.c.execute("SELECT patient_id, paid_cents FROM payer_claim WHERE counts_in_metrics = 1 AND substr(service_from, 1, 4) = '2029'"):
            tot[p] += c or 0
        top = sum(sorted(tot.values(), reverse=True)[:10]) / 100.0
        self.assertAlmostEqual(top, self.q('top10_members')['answer'], delta=0.01)

    # 12
    def test_ninety_day_share(self):
        xs = [s for (s,) in self.c.execute("SELECT days_supply FROM payer_pharmacy_claim WHERE counts_as_fill = 1 AND substr(fill_date, 1, 4) = '2029'")]
        k = self.q('ninety_day_share')
        self.assertEqual((sum(1 for s in xs if s is not None and s >= 84), len(xs)), (k['numerator'], k['denominator']))

    # 14
    def test_pmpm_2029(self):
        paid = sum(c or 0 for (c,) in self.c.execute("SELECT paid_cents FROM payer_claim WHERE counts_in_metrics = 1 AND substr(service_from, 1, 4) = '2029'")) / 100.0
        mm = sum(1 for (m,) in self.c.execute('SELECT month FROM member_month') if m.startswith('2029'))
        row = [r for r in self.q('pmpm_trend')['answer'] if r['year'] == 2029][0]
        self.assertAlmostEqual(paid / mm, row['pmpm_usd'], delta=0.01)

    # 15
    def test_ed_half_and_rising_risk(self):
        ed = Counter(p for p, d in self.c.execute("SELECT patient_id, event_date FROM utilization_event WHERE kind = 'ed_visit'") if D.LAST_YEAR_START <= d <= D.AS_OF and p in self.living)
        tot, run, k = sum(ed.values()), 0, 0
        for _, n in sorted(ed.items(), key=lambda kv: (-kv[1], kv[0])):
            run += n
            k += 1
            if run >= tot / 2:
                break
        facts = {f['id']: f for f in BY_KEY['ed_concentration']['key']['answer']['required_facts']}
        self.assertEqual(facts['half']['value'], k)
        self.assertEqual(facts['users']['value'], len(ed))
        ip = {p for p, d in self.c.execute("SELECT patient_id, event_date FROM utilization_event WHERE kind = 'inpatient'") if D.LAST_YEAR_START <= d <= D.AS_OF}
        rr = sum(1 for p, n in ed.items() if n >= 2 and p not in ip)
        self.assertEqual(rr, {f['id']: f for f in BY_KEY['rising_risk']['key']['answer']['required_facts']}['n']['value'])


if __name__ == '__main__':
    unittest.main()
