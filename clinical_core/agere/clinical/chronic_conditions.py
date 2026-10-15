"""Documented conditions per patient from every received diagnosis source, with a claims-confirmation rule.

Sources (semantic layer): payer claim diagnoses (paid, current version: counts_in_metrics = 1), the client's own billing
claims (paid), EHR visit diagnoses (completed visits), and condition records (problem list, assessments, encounter and
lab-basis diagnoses; refuted / entered-in-error entries dropped).

Every row carries a kind:
  claim             a paid professional / outpatient claim line diagnosis (one service date)
  claim_inpatient   a paid inpatient facility claim (TOB 011x)
  visit             an EHR visit diagnosis (completed outpatient / ED visit). An encounter diagnosis is the same coded fact a
                    claim line carries, so it is counted like one (a dated encounter), not as problem-list confirmation.
  visit_inpatient   an EHR inpatient-admission diagnosis (same weight as an inpatient facility claim)
  ehr               a condition record: problem list, clinician assessment, encounter-basis or lab-basis diagnosis

Confirmation rules (CMS Chronic Conditions Warehouse convention, https://www2.ccwdata.org/web/guest/condition-categories):
  - a category documented only by dated encounters (claims and EHR visit diagnoses together) needs >= min_claim_dates
    distinct service dates, or one inpatient stay (claim TOB 011x or EHR admission). A single outpatient code is often a
    rule-out, an order diagnosis or a one-off visit (one dental visit coded K05 is not periodontal disease).
  - osteoarthritis (M15-M19): the CCW 'Rheumatoid Arthritis / Osteoarthritis' algorithm needs two claims at least one
    day apart of any type; one inpatient stay does not qualify on its own (an elective joint-replacement admission
    is coded with the joint's OA on one date).
  - acute or episodic categories never become a chronic condition from encounters: injuries and poisonings (S00-T88,
    ICD-10-CM chapter 19; initial-encounter 7th character 'A' = active treatment of an acute injury), sepsis (A40-A41),
    shock (R57) and limb pain (M79.6, a sign/symptom code: ICD-10-CM Official Guidelines I.B.4-5). The AHRQ HCUP Chronic
    Condition Indicator Refined classifies these as not chronic. A clinician's problem-list entry still documents them.
  - condition records (problem list etc.) document a category on their own; refuted / entered-in-error entries are dropped.
    Resolved entries (clinical_status resolved, abatement date) are kept: they show the condition was present during the
    observation window, which is what the condition list reports.
  - claims of every type count toward the two dates, laboratory and imaging claims included (CCW counts any claim type;
    a lone order diagnosis is a single date and does not confirm on its own).
min_claim_dates = 1 reproduces 'any claim code'. Categories are ICD-10-CM 3-character categories (the dot removed).
"""
from collections import defaultdict
import json

from .risk_adjustment import icd_key

VERSION = 'chronic-conditions/2'
DEFAULTS = {'min_claim_dates': 2}
EXCLUDED_VERIFICATION = ('refuted', 'entered-in-error', 'entered_in_error')
ENCOUNTER_KINDS = ('claim', 'visit')                      # dated encounters, counted by distinct service date
INPATIENT_KINDS = ('claim_inpatient', 'visit_inpatient')  # one inpatient stay confirms (except TWO_DATE_ONLY)
# Acute / episodic codes that encounters never turn into a chronic condition (prefixes of the dot-free ICD-10-CM code).
ACUTE_PREFIXES = ('S', 'T', 'A40', 'A41', 'R57', 'M796')
# CCW RA/OA: two claims at least one day apart, no single-inpatient path.
TWO_DATE_ONLY = ('M15', 'M16', 'M17', 'M18', 'M19')


def _codes(principal, codes):
    out = []
    try: out = json.loads(codes) if codes else []
    except ValueError: out = [c for c in str(codes).replace(';', ',').replace('|', ',').split(',') if c.strip()]
    return list(dict.fromkeys(icd_key(c) for c in [principal, *out] if c and icd_key(c)))


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def diagnosis_rows(db, start='0000-01-01', end='9999-12-31'):
    """Iterator of (patient_id, icd10 key, date or None, kind); kinds in the module docstring."""
    t = _tables(db)
    if 'payer_claim_diagnosis' in t:
        inpatient = {cid for (cid,) in db.execute("SELECT payer_claim_id FROM payer_claim WHERE substr(ltrim(type_of_bill,'0'),1,2) = '11'")}
        for cid, pid, icd, day in db.execute(
                "SELECT d.payer_claim_id, d.patient_id, d.icd10, substr(d.service_from,1,10) FROM payer_claim_diagnosis d "
                "JOIN payer_claim c ON c.payer_claim_id = d.payer_claim_id WHERE c.final_status = 'paid' AND coalesce(d.counts_in_metrics,1) = 1 "
                "AND d.service_from BETWEEN ? AND ?", (start, end + 'T99')):
            if icd: yield pid, icd_key(icd), day, 'claim_inpatient' if cid in inpatient else 'claim'
    if 'claim' in t:
        for pid, day, pdx, dxs, tob in db.execute("SELECT patient_id, substr(service_date,1,10), principal_diagnosis, diagnosis_codes, type_of_bill "
                                                 "FROM claim WHERE final_status = 'paid' AND service_date BETWEEN ? AND ?", (start, end + 'T99')):
            kind = 'claim_inpatient' if str(tob or '').lstrip('0').startswith('11') else 'claim'
            for c in _codes(pdx, dxs): yield pid, c, day, kind
    if 'encounter_diagnosis' in t and 'visit' in t:
        for pid, icd, day, concept in db.execute(
                "SELECT d.patient_id, d.icd10, substr(v.event_date,1,10), v.type_concept FROM encounter_diagnosis d JOIN visit v "
                "ON v.encounter_id = d.encounter_id WHERE coalesce(v.status,'completed') NOT IN ('cancelled','canceled','entered-in-error','no_show') "
                "AND v.event_date BETWEEN ? AND ?", (start, end + 'T99')):
            if icd: yield pid, icd_key(icd), day, 'visit_inpatient' if concept == 'inpatient_admission' else 'visit'
    if 'condition' in t:
        bad = set()
        for (rid,) in db.execute("SELECT record_id FROM record WHERE stream = 'conditions' AND json_extract(payload,'$.verification_status') IN (?,?,?)",
                                 EXCLUDED_VERIFICATION):
            bad.add(rid)
        for rid, pid, icd, day in db.execute("SELECT record_id, patient_id, icd10, substr(coalesce(event_date, onset, recorded_date),1,10) FROM condition "
                                             "WHERE icd10 IS NOT NULL AND icd10 != ''"):
            if rid in bad: continue
            if day and not (start <= day <= end): continue
            yield pid, icd_key(icd), day, 'ehr'


def confirmed(code_kinds, dates, category, min_claim_dates=2):
    """Whether one category is documented: code_kinds = {(code, kind)}, dates = distinct encounter service dates."""
    kinds = {k for _, k in code_kinds}
    if 'ehr' in kinds: return True
    usable = {(c, k) for c, k in code_kinds if not c.startswith(ACUTE_PREFIXES)}
    if not usable: return False
    if category not in TWO_DATE_ONLY and any(k in INPATIENT_KINDS for _, k in usable): return True
    return len(dates) >= max(1, min_claim_dates)


def documented(rows, min_claim_dates=2):
    """{pid: {category: {'first': date, 'kinds': set, 'codes': set}}} for categories passing the confirmation rules."""
    acc = defaultdict(lambda: defaultdict(lambda: {'dates': set(), 'code_kinds': set(), 'first': None}))
    for pid, code, day, kind in rows:
        if len(code) < 3: continue
        a = acc[pid][code[:3]]
        a['code_kinds'].add((code, kind))
        if day and kind in ENCOUNTER_KINDS + INPATIENT_KINDS and not code.startswith(ACUTE_PREFIXES): a['dates'].add(day)
        if day and (a['first'] is None or day < a['first']): a['first'] = day
    out = {}
    for pid, cats in acc.items():
        keep = {}
        for cat, a in cats.items():
            if confirmed(a['code_kinds'], a['dates'], cat, min_claim_dates):
                keep[cat] = {'first': a['first'], 'kinds': {k for _, k in a['code_kinds']}, 'codes': {c for c, _ in a['code_kinds']}}
        if keep: out[pid] = keep
    return out
