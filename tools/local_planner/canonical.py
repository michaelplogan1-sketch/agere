"""Canonical questions: precise English the free parser reads completely, across the warehouse's vocabulary.

Each emitted question is parsed and compiled by the current free parser (request_language + request_plan); a question
that does not compile is dropped, so every canonical question has a deterministic plan. These are the targets the local
normalizer learns to rewrite client wording into. Synthetic warehouse only.

Usage: python tools/local_planner/canonical.py OUT.jsonl [--per-family N] [--seed S]
"""
import argparse
import itertools
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import request_plan as R, request_language as L, request_vocabulary as V  # noqa: E402

STACK = ROOT / 'artifacts' / 'agere-healthcare-v28-sample-0.30' / 'stack'

CONDITIONS = ['diabetes', 'heart failure', 'CKD', 'chronic kidney disease', 'COPD', 'hypertension', 'asthma', 'depression',
              'atrial fibrillation', 'coronary disease', 'obesity', 'dementia', 'stroke', 'cancer', 'hyperlipidemia',
              'anxiety', 'osteoporosis', 'sleep apnea', 'hypothyroidism', 'anemia', 'prediabetes']
SUBJECTS = ['patients', 'living patients', 'women', 'men', 'Medicaid patients', 'Medicare patients', 'living Medicaid patients']
AGES = ['', ' over 65', ' 65 and older', ' 65+', ' aged 18 to 75', ' aged 50 to 74', ' under 18', ' aged 40 to 64']
GROUPS = ['', ' by sex', ' by payer', ' by coverage', ' by language']
YEARS = ['2027', '2028', '2029']
DRUGS = ['a statin', 'metformin', 'insulin', 'an SGLT2 inhibitor', 'a beta-blocker', 'an ACE inhibitor or ARB']
GAPS = [  # (wording, allows a calendar year)
    ('have had no HbA1c test', True), ('had no HbA1c test', True), ('had no eGFR', True), ('had a uACR', True),
    ('had neither an eGFR nor a uACR', True), ('had a PHQ-9 score recorded', True), ('had no LDL result', True),
    ('had no statin fill', True), ('had no beta-blocker fill', True), ('had no metformin fill', True),
    ('did not get a flu shot', False), ('have not had a mammogram', True), ('did not have an annual wellness visit', True),
    ('have ever received a pneumococcal vaccine on record', None), ('have never had a flu shot on record', None),
    ('have screened positive for food insecurity', None), ('have screened positive for transportation needs', None),
    ('have screened for housing', None),
]
WINDOWS = [' in the last 12 months', ' in the last 24 months', ' in the last 6 months', ' in the past year']
METRIC_EXCLUDE = ('_', 'measure.', '(', '/')
SOURCES = ['ED visits', 'emergency department visits', 'inpatient admissions', 'hospital admissions', 'claims',
           'medical claims', 'appointments', 'kept appointments', 'pharmacy claims', 'hospital stays', 'prescriptions']
STATUS = [('appointments', 'cancelled'), ('appointments', 'kept'), ('pharmacy claims', 'rejected'), ('pharmacy claims', 'reversed')]
SPECIALISTS = ['cardiologists', 'nephrologists', 'endocrinologists', 'oncologists', 'neurologists', 'psychiatrists',
               'pediatricians', 'family medicine providers', 'cardiology providers', 'pulmonology physicians']
MONEY = ['allowed dollars', 'paid dollars', 'patient responsibility', 'charge dollars']


def families(rng):
    fam = {}
    fam['condition_count'] = [f'How many {s}{a} have {c}{g}?' for s, a, c, g in itertools.product(SUBJECTS, AGES, CONDITIONS, GROUPS)]
    pairs = [(a, b) for a, b in itertools.permutations(CONDITIONS[:12], 2)]
    fam['condition_combo'] = ([f'How many patients have both {a} and {b}?' for a, b in pairs]
                              + [f'How many patients have {a} plus {b}?' for a, b in pairs]
                              + [f'How many patients with {a} do not have {b}?' for a, b in pairs]
                              + [f'How many {s} with {a} or {b} are there?' for s in ('patients', 'living patients') for a, b in pairs])
    gaps = []
    for s, a, c, (gap, yearly) in itertools.product(['patients', 'living patients', 'women', 'Medicaid patients'],
                                                    ['', ' 65 and older', ' aged 50 to 74', ' aged 18 to 75'], CONDITIONS[:10] + [None], GAPS):
        tail = [''] if yearly is None else ([' in the 2028-2029 season', ' in the 2029-2030 season'] if gap.endswith('flu shot') and yearly is False
                                           else WINDOWS + ([' in 2029', ' in 2028'] if yearly else []))
        for w in tail:
            gaps.append(f'How many {s}{a}{" with " + c if c else ""} {gap}{w}?')
    fam['care_gap'] = gaps
    metrics = sorted({a for a in V.aliases() if not any(x in a for x in METRIC_EXCLUDE)})
    fam['governed_metric'] = ([f'What was the {m} in {y}?' for m, y in itertools.product(metrics, YEARS)]
                              + [f'What was the {m} in 2029 for {p} patients?' for m, p in itertools.product(metrics, ['Medicaid', 'Medicare', 'commercial'])]
                              + [f'What was the {m} in 2029 by {g}?' for m, g in itertools.product(metrics, ['payer', 'sex', 'coverage'])]
                              + [f'What was the 2029 {m}?' for m in metrics])
    fam['comparison'] = ([f'How did {m} change from {a} to {b}?' for m, (a, b) in itertools.product(metrics, [('2027', '2028'), ('2028', '2029')])]
                         + [f'Compared with 2028, did the 2029 {m} go up or down?' for m in metrics]
                         + [f'{m} in 2029 vs 2028' for m in metrics])
    fam['source_count'] = ([f'How many {s} were there in {y}?' for s, y in itertools.product(SOURCES, YEARS)]
                           + [f'How many {s} were {w} in {y}?' for (s, w), y in itertools.product(STATUS, YEARS)]
                           + [f'How many {x} are there?' for x in SPECIALISTS]
                           + [f'Show {s} by month for {y}' for s, y in itertools.product(['ED visits', 'inpatient admissions', 'appointments'], YEARS)]
                           + [f'How many {s} were there in {y} by payer?' for s, y in itertools.product(['ED visits', 'claims'], YEARS)]
                           + ['How many prescriptions were written but never filled?', 'How many patients have an assigned PCP?'])
    fam['money_total'] = [f'What were total {m} on {y} medical claims?' for m, y in itertools.product(MONEY, YEARS)]
    fam['sequence'] = ([f'How many {c} patients started {d} within {n} days after a hospital discharge?'
                        for c, d, n in itertools.product(['heart failure', 'diabetes', 'CKD', 'COPD'], DRUGS, [30, 60, 90])]
                       + [f'Of inpatient discharges in {y}, how many had a primary care visit within {n} days after discharge?'
                          for y, n in itertools.product(YEARS, [7, 14, 30])]
                       + [f'How many {y} hospital discharges got a primary care follow-up within a week?' for y in YEARS]
                       + [f'How many patients had an ED visit within {n} days after a hospital discharge?' for n in (7, 30, 90)]
                       + [f'How many patients who died had an inpatient admission in the {n} days before death?' for n in (7, 30, 90)])
    fam['ranking'] = ([f'What are the top {n} denial reason codes across all medical claims?' for n in ('three', 'five', '10')]
                      + [f'What are the top {n} denial reason codes across all medical claims in {y}?' for n, y in itertools.product(('three', 'five'), YEARS)]
                      + [f'What are the top {n} symptoms in notes in {y}?' for n, y in itertools.product(('three', 'five'), YEARS)]
                      + ['What is the most frequently documented symptom in notes (affirmed, not negated)?',
                         'What is the most common reject code on pharmacy claims?', 'What are the top five reject codes on pharmacy claims?'])
    return fam


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out'); ap.add_argument('--per-family', type=int, default=400); ap.add_argument('--seed', type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    catalog = R.Catalog.load(STACK)
    kept, report = [], {}
    for name, questions in families(rng).items():
        questions = list(dict.fromkeys(questions)); rng.shuffle(questions)
        ok = 0
        for q in questions:
            if ok >= args.per_family: break
            try:
                program = L.parse_program(catalog, q)
                plan = program if program is not None else L.parse(catalog, q)
                if program is None: R.compile_request(catalog, q, plan)
            except Exception:  # noqa: BLE001 - only fully compiled questions are canonical
                continue
            kept.append({'family': name, 'canonical': q}); ok += 1
        report[name] = {'candidates': len(questions), 'kept': ok}
    Path(args.out).write_text(''.join(json.dumps(k) + '\n' for k in kept))
    print(json.dumps(report, indent=1)); print('total', len(kept))


if __name__ == '__main__':
    main()
