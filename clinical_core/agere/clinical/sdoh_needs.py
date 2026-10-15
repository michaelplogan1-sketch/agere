"""Social needs per patient (transport, food, housing, work schedule constraint) that require corroboration.

A single positive answer or a purchased vendor flag is a weak signal: area-level vendor scores describe a neighbourhood,
and one screen answer can be a transient or misread response. A need is asserted when its evidence score reaches
min_score (default 2) from these independent sources (points are parameters, POINTS):

  z_code      ICD-10-CM Z55-Z65 social-determinant code documented by a clinician or on a paid claim (CMS/AHA Coding
              Clinic guidance allows SDOH Z codes from any clinician or screen documentation): 2 points.
                housing  Z59.0x homelessness, Z59.1 inadequate housing, Z59.81x housing instability
                food     Z59.4x lack of adequate food / food insecurity
                transport Z59.82 transportation insecurity
                work_schedule_constraint Z56.3 stressful work schedule (Z56.9 unspecified employment problem: 1 point per
                distinct encounter date, at most 2: the same clinician-coded problem on two separate dates is corroborated)
  screen      positive answer on a validated screen (CMS AHC HRSN, PRAPARE-style brief needs interview, social_need
              table) or a care-support interview: 1 point per distinct date, at most 2.
  note        affirmed (non-negated, patient-experienced) social need stated in clinical note text (note_fact): 1 point.
  vendor      purchased SDOH attribute: food_access_low, transportation_risk_score >= 4, housing_instability_score >= 4:
              1 point (never sufficient alone at the default threshold).

Work-schedule constraints come from the brief-needs interview answer naming the constraint itself (daytime commitments,
variable hours, school and parent availability; 2 points, work_answer: a direct self-report of the constraint rather
than a yes/no screen item) and Z56.3.
"""
from collections import defaultdict
import json

from .risk_adjustment import icd_key

VERSION = 'sdoh-needs/2'
NEEDS = ('transport', 'food', 'housing', 'work_schedule_constraint')
DEFAULTS = {'min_score': 2, 'vendor_score': 4}
POINTS = {'z_code': 2, 'z_code_weak': 1, 'screen': 1, 'screen_cap': 2, 'note': 1, 'vendor': 1, 'work_answer': 2}
Z_CODES = {'housing': ('Z590', 'Z591', 'Z5981'), 'food': ('Z594',), 'transport': ('Z5982',), 'work_schedule_constraint': ('Z563',)}
Z_WEAK = {'work_schedule_constraint': ('Z569',)}
WORK_CONSTRAINT = {'daytime commitments', 'variable hours', 'school_and_parent_availability'}
SCREEN_NEED = {'transport': 'transport', 'food': 'food', 'housing': 'housing'}
INTERVIEW = {'transport_to_medical_visits_difficult_reported': 'transport', 'food_access_concern_reported': 'food',
             'housing_safety_concern_reported': 'housing'}


def _truthy(v):
    return v in (1, True, '1', 'true', 'True', 'yes', 'Yes', 'positive')


def evidence(db, diagnosis_rows=(), vendor_score=4):
    """{pid: {need: {source: set(dates or markers)}}}."""
    ev = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    for pid, code, day, kind in diagnosis_rows:
        c = icd_key(code)
        if not c.startswith('Z5'): continue
        for need, prefixes in Z_CODES.items():
            if any(c.startswith(p) for p in prefixes): ev[pid][need]['z_code'].add(day or 'undated')
        for need, prefixes in Z_WEAK.items():
            if any(c.startswith(p) for p in prefixes): ev[pid][need]['z_code_weak'].add(day or 'undated')
    if 'social_need' in tables:
        for pid, need, day in db.execute("SELECT patient_id, need, substr(event_date,1,10) FROM social_need WHERE positive = 1"):
            if need in SCREEN_NEED: ev[pid][SCREEN_NEED[need]]['screen'].add(day or 'undated')
    for pid, day, value in db.execute("SELECT patient_id, substr(event_date,1,10), json_extract(payload,'$.work_or_school_scheduling_constraint') "
                                      "FROM record WHERE stream = 'social_screens'"):
        if value in WORK_CONSTRAINT: ev[pid]['work_schedule_constraint']['work_answer'].add(day or 'undated')
    for pid, day, payload in db.execute("SELECT patient_id, substr(event_date,1,10), payload FROM record WHERE stream = 'care_support_interviews'"):
        r = json.loads(payload or '{}')
        for field, need in INTERVIEW.items():
            if _truthy(r.get(field)): ev[pid][need]['screen'].add(day or 'undated')
    if 'note_fact' in tables:
        for pid, value, day in db.execute("SELECT patient_id, value, substr(event_date,1,10) FROM note_fact WHERE fact_type = 'social_need' "
                                          "AND coalesce(negated,0) = 0 AND coalesce(experiencer,'patient') = 'patient'"):
            if value in NEEDS: ev[pid][value]['note'].add(day or 'undated')
    if 'sdoh_vendor' in tables:
        for pid, food, trans, housing, day in db.execute("SELECT patient_id, food_access_low, transportation_risk_score, housing_instability_score, "
                                                         "substr(delivery_date,1,10) FROM sdoh_vendor"):
            if _truthy(food): ev[pid]['food']['vendor'].add(day or 'undated')
            if trans is not None and float(trans) >= vendor_score: ev[pid]['transport']['vendor'].add(day or 'undated')
            if housing is not None and float(housing) >= vendor_score: ev[pid]['housing']['vendor'].add(day or 'undated')
    return ev


def score(sources):
    s = 0
    if sources.get('z_code'): s += POINTS['z_code']
    elif sources.get('z_code_weak'): s += min(len(sources['z_code_weak']), 2) * POINTS['z_code_weak']
    s += min(len(sources.get('screen', ())), POINTS['screen_cap']) * POINTS['screen']
    if sources.get('note'): s += POINTS['note']
    if sources.get('vendor'): s += POINTS['vendor']
    if sources.get('work_answer'): s += POINTS['work_answer']
    return s


def needs(db, diagnosis_rows=(), params=None):
    """{pid: {need: {'score', 'sources'}}} for needs reaching min_score."""
    p = {**DEFAULTS, **(params or {})}
    out = {}
    for pid, by_need in evidence(db, diagnosis_rows, p['vendor_score']).items():
        keep = {}
        for need, sources in by_need.items():
            sc = score(sources)
            if sc >= p['min_score']: keep[need] = {'score': sc, 'sources': sorted(k for k, v in sources.items() if v)}
        if keep: out[pid] = keep
    return out
