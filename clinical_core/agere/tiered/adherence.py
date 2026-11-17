"""Medication adherence (PDC) for a year, computed by the existing therapy-course code (clinical/therapy_courses.py), not plain SQL.

Trial (2026-11-17, eval/tiered/trial_pdc.py on claude/zen-goldberg-4e958c): across 7 cardiometabolic classes a plain SQL PDC
(days supplied over days from first fill to year end) overstates the share of adherent patients by +6.2 points [5.0, 7.3]
against the course code, which handles overlapping fills, stockpiling and documented stops. The method follows the PQA
proportion-of-days-covered approach (adherent: PDC of 80% or more; patients with at least two fills of the class in the year)
but has not been independently adjudicated.

    compute(sql, params, db)   the built-in recipes adherence_pdc (every class) and adherence_pdc_class (one class)
"""
import sqlite3
from collections import defaultdict

from . import store

THRESHOLD = 0.8
MIN_FILLS = 2


def classes():
    from ..clinical import therapy_courses as tc
    return [c for comps in tc.AREAS.values() for c in comps]


def compute(sql, params, db):
    from ..clinical import therapy_courses as tc
    raw = sqlite3.connect(store.path().resolve().as_uri() + '?mode=ro', uri=True)   # the course code reads the raw fill tables
    try:
        first = raw.execute(sql).fetchone()[0]
        rows = tc.courses(raw, [int(params['year'])], store.as_of(db), first)
    finally:
        raw.close()
    want = params.get('drug_class')
    by = defaultdict(list)
    for r in rows:
        if r['pdc'] is not None and r['fills'] >= MIN_FILLS and (want is None or r['drug_class'] == want): by[r['drug_class']].append(r['pdc'])
    out = [[c, len(v), sum(x >= THRESHOLD for x in v), round(sum(x >= THRESHOLD for x in v) / len(v), 4)] for c, v in sorted(by.items())]
    return ['drug_class', 'patients_with_2_fills', 'adherent_pdc_80', 'adherent_share'], out
