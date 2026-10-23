"""Cross-check chart_reader against independent SQL (the dev-set definitions) for many random patients.

    .venv/bin/python -m tools.chart_reader_oracle --n 40 --seed 7

For each question template it picks N random patients that have the data (any segment, living or deceased), asks chart_reader the question in
words, and compares the answer's number / date / list with a direct SQL oracle written from tools/dev_set/definitions.py. This is a
generalization check beyond the 20 dev questions: the dev-set definitions are Mike's decisions, so agreement here is agreement with them.
Disagreements are printed with the patient id and both values. Exit code 1 when any template has a disagreement.
"""
from __future__ import annotations

import argparse
import random
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clinical_core.agere.clinical import chart_reader  # noqa: E402
from tools.chart_reader_eval import STACK, LightStack  # noqa: E402
from tools.dev_set import definitions as D  # noqa: E402


def close(a, b, tol=0.005):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


class Oracle:
    def __init__(self, db, rng, n):
        self.db, self.rng, self.n = db, rng, n

    def pick(self, sql, k=None):
        rows = [r[0] for r in self.db.execute(sql)]
        self.rng.shuffle(rows)
        return rows[: (k or self.n)]

    def one(self, sql, *a):
        r = self.db.execute(sql, a).fetchone()
        return r[0] if r else None

    def col(self, sql, *a):
        return [r[0] for r in self.db.execute(sql, a)]


def templates(o):
    db = o.db
    last = D.in_last_year
    T = []

    def add(name, picks, question, oracle, check):
        T.append((name, picks, question, oracle, check))

    for concept, label in (('hba1c', 'HbA1c'), ('egfr', 'eGFR'), ('ldl', 'LDL'), ('weight', 'weight'), ('creatinine', 'creatinine'), ('potassium', 'potassium')):
        add(f'latest_{concept}', f'SELECT DISTINCT patient_id FROM measurement_latest WHERE concept = \'{concept}\'', 'What is the latest {l} for {p}?'.replace('{l}', label),
            lambda p, c=concept: o.one(f'SELECT value FROM ({D.latest_lab(c)}) WHERE patient_id = ?', p), 'value')
        add(f'previous_{concept}', f'SELECT patient_id FROM ({D.any_lab(concept)}) GROUP BY 1 HAVING COUNT(*) >= 2',
            'What was the {l} result before the most recent one on {p}?'.replace('{l}', label),
            lambda p, c=concept: o.one(f'SELECT value FROM (SELECT value, ROW_NUMBER() OVER (ORDER BY event_date DESC, record_id DESC) rn FROM ({D.any_lab(c)}) WHERE patient_id = ?) WHERE rn = 2', p),
            'value')
        add(f'highest_{concept}', f'SELECT patient_id FROM ({D.any_lab(concept)}) GROUP BY 1 HAVING COUNT(*) >= 2', 'What is the highest {l} ever recorded for {p}?'.replace('{l}', label),
            lambda p, c=concept: o.one(f'SELECT MAX(value) FROM ({D.any_lab(c)}) WHERE patient_id = ?', p), 'value')
    add('active_meds', "SELECT DISTINCT patient_id FROM medication_list_entry WHERE list_status = 'active'", 'List the active medications for {p}.',
        lambda p: o.col("SELECT lower(drug_key) FROM medication_list_entry WHERE patient_id = ? AND list_status = 'active'", p), 'list')
    for cls, word in (('statin', 'a statin'), ('ace_inhibitor', 'an ACE inhibitor'), ('metformin', 'metformin')):
        add(f'last_fill_{cls}', f"SELECT DISTINCT patient_id FROM fill WHERE drug_class = '{cls}'", 'When did {p} last pick up ' + word + '?',
            lambda p, c=cls: o.one('SELECT MAX(fill_date) FROM fill WHERE patient_id = ? AND drug_class = ?', p, c), 'date')
    add('ed_year', f"SELECT DISTINCT patient_id FROM utilization_event WHERE kind = 'ed_visit' AND {last('event_date')}", 'How many ED visits has {p} had in the past year?',
        lambda p: o.one(f"SELECT COUNT(*) FROM ({D.ED_VISITS}) WHERE patient_id = ? AND {last('event_date')}", p), 'count')
    add('adm_2029', "SELECT DISTINCT patient_id FROM utilization_event WHERE kind = 'inpatient' AND substr(event_date, 1, 4) = '2029'", 'How many hospital admissions did {p} have in 2029?',
        lambda p: o.one(f"SELECT COUNT(*) FROM ({D.ADMISSIONS}) WHERE patient_id = ? AND {D.in_year('admit_date')}", p), 'count')
    add('adm_all', "SELECT DISTINCT patient_id FROM utilization_event WHERE kind = 'inpatient'", 'How many hospital admissions has {p} had?',
        lambda p: o.one(f"SELECT COUNT(*) FROM ({D.ADMISSIONS}) WHERE patient_id = ?", p), 'count')
    add('readmit', "SELECT DISTINCT patient_id FROM stay", 'Was {p} ever readmitted within 30 days of a discharge?',
        lambda p: bool(o.one('SELECT MAX(readmitted_30d) FROM stay WHERE patient_id = ?', p)), 'bool')
    add('practice', "SELECT patient_id FROM patient WHERE pcp_practice_id IS NOT NULL", 'Which practice is {p} attributed to?',
        lambda p: o.one('SELECT pcp_practice_id FROM patient WHERE patient_id = ?', p), 'text')
    add('mammogram', "SELECT DISTINCT patient_id FROM cancer_screening WHERE test = 'bilateral_screening_mammography' AND result_status = 'final'", "When was {p}'s last screening mammogram?",
        lambda p: o.one("SELECT MAX(event_date) FROM cancer_screening WHERE patient_id = ? AND test = 'bilateral_screening_mammography' AND result_status = 'final'", p), 'date')
    add('flu', "SELECT DISTINCT patient_id FROM immunization WHERE vaccine LIKE 'influenza%' AND status = 'completed'", 'When did {p} last get a flu shot?',
        lambda p: o.one("SELECT MAX(event_date) FROM immunization WHERE patient_id = ? AND vaccine LIKE 'influenza%' AND status = 'completed'", p), 'date')
    add('allergies', "SELECT DISTINCT patient_id FROM allergy_entry WHERE no_known_allergies = 0", 'What allergies are recorded for {p}?',
        lambda p: o.col('SELECT lower(substance) FROM allergy_entry WHERE patient_id = ? AND no_known_allergies = 0', p), 'list')
    add('spend', f"SELECT DISTINCT patient_id FROM payer_claim WHERE counts_in_metrics = 1 AND {last('service_from')}", 'How much did the plan pay in claims for {p} over the last 12 months?',
        lambda p: o.one(f"SELECT SUM(paid_cents) / 100.0 FROM payer_claim WHERE patient_id = ? AND counts_in_metrics = 1 AND {last('service_from')}", p), 'value')
    add('uacr_year', "SELECT DISTINCT patient_id FROM measurement_latest WHERE concept = 'egfr'", 'Has {p} had a urine albumin/creatinine test in the last 12 months?',
        lambda p: bool(o.one(f"SELECT COUNT(*) FROM ({D.any_lab('uacr')}) WHERE patient_id = ? AND {last('event_date')}", p)), 'bool')
    add('on_statin', "SELECT DISTINCT patient_id FROM medication_list_entry WHERE list_status = 'active'", 'Is {p} on a statin?',
        lambda p: bool(o.one("SELECT COUNT(*) FROM medication_list_entry WHERE patient_id = ? AND list_status = 'active' AND lower(name) LIKE '%statin%'", p)
                       or o.one(f"SELECT COUNT(*) FROM fill WHERE patient_id = ? AND drug_class = 'statin' AND {last('fill_date')}", p)), 'bool')
    add('not_filled', "SELECT DISTINCT patient_id FROM prescription WHERE fill_status = 'not_filled'", 'Which prescriptions written for {p} were never filled?',
        lambda p: [x.split(' ')[0] for x in o.col("SELECT lower(drug_name) FROM prescription WHERE patient_id = ? AND fill_status = 'not_filled'", p)], 'list')
    add('noshow_year', "SELECT DISTINCT patient_id FROM appointment WHERE final_status = 'no_show'", 'How many no-shows has {p} had in the past year?',
        lambda p: o.one(f"SELECT COUNT(*) FROM appointment WHERE patient_id = ? AND final_status = 'no_show' AND {last('scheduled_date')}", p), 'count')
    add('age', "SELECT patient_id FROM patient WHERE age_latest IS NOT NULL", 'How old is {p}?', lambda p: o.one('SELECT age_latest FROM patient WHERE patient_id = ?', p), 'value')
    return T


def compare(kind, got, exp):
    if kind in ('value', 'count'):
        return close(got, exp)
    if kind == 'bool':
        return bool(got) is bool(exp)
    return got == exp


def extract(kind, out):
    if kind in ('value', 'count'):
        return out.get('value') if out.get('value') is not None else out.get('count')
    if kind == 'bool':
        return out.get('value')
    if kind == 'date':
        return out.get('value')
    if kind == 'text':
        return out.get('value')
    if kind == 'list':
        return None
    return out.get('value')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=25)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--only', nargs='*')
    args = ap.parse_args(argv)
    db = sqlite3.connect(f'file:{STACK / "semantic.sqlite3"}?mode=ro', uri=True)
    o = Oracle(db, random.Random(args.seed), args.n)
    stack = LightStack(STACK)
    bad = total = 0
    for name, picks, question, oracle, kind in templates(o):
        if args.only and name not in args.only:
            continue
        ids = o.pick(picks)
        wrong = []
        for p in ids:
            out = chart_reader.ask(stack, question.replace('{p}', p))
            exp = oracle(p)
            if kind == 'list':
                text = (out.get('headline') or '').lower() + ' ' + str(out.get('table')).lower()
                ok = all(str(x).lower() in text for x in exp) and (out.get('count') in (None, len(exp)) or name == 'not_filled')
                got = out.get('count')
            else:
                got = extract(kind, out)
                ok = compare(kind, got, exp) if kind != 'text' else str(exp) in str(got)
                if kind == 'count' and got is None:
                    got = out.get('count')
                    ok = compare(kind, got, exp)
            total += 1
            if not ok:
                wrong.append((p, got, exp, (out.get('headline') or '')[:120]))
        bad += len(wrong)
        print(f"{name:22} n={len(ids):3} disagreements={len(wrong)}")
        for w in wrong[:4]:
            print('     ', w)
    print(f'TOTAL checks={total} disagreements={bad}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
