"""Documented conditions per patient from every received diagnosis source, with a claims-confirmation rule.

Sources (semantic layer): payer claim diagnoses (paid, current version: counts_in_metrics = 1), the client's own billing
claims (paid), EHR visit diagnoses (completed visits), and condition records (problem list, assessments, encounter and
lab-basis diagnoses; refuted / entered-in-error entries dropped).

Claims-confirmation rule (CMS Chronic Conditions Warehouse convention, https://www2.ccwdata.org/web/guest/condition-categories):
a category documented only on claims needs >= min_claim_dates distinct service dates, or one inpatient facility claim
(TOB 011x). A single outpatient claim code is often a rule-out or an order diagnosis. Any EHR source (problem list,
visit diagnosis, condition record) documents the category on its own. min_claim_dates = 1 reproduces 'any claim code'.
Categories are ICD-10-CM 3-character categories (the dot removed).
"""
from collections import defaultdict
import json

from .risk_adjustment import icd_key

VERSION = 'chronic-conditions/1'
DEFAULTS = {'min_claim_dates': 2}
EXCLUDED_VERIFICATION = ('refuted', 'entered-in-error', 'entered_in_error')


def _codes(principal, codes):
    out = []
    try: out = json.loads(codes) if codes else []
    except ValueError: out = [c for c in str(codes).replace(';', ',').replace('|', ',').split(',') if c.strip()]
    return list(dict.fromkeys(icd_key(c) for c in [principal, *out] if c and icd_key(c)))


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def diagnosis_rows(db, start='0000-01-01', end='9999-12-31'):
    """Iterator of (patient_id, icd10 key, date or None, kind) with kind in claim | claim_inpatient | ehr."""
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
        for pid, icd, day in db.execute("SELECT d.patient_id, d.icd10, substr(v.event_date,1,10) FROM encounter_diagnosis d JOIN visit v "
                                        "ON v.encounter_id = d.encounter_id WHERE coalesce(v.status,'completed') NOT IN ('cancelled','canceled','entered-in-error','no_show') "
                                        "AND v.event_date BETWEEN ? AND ?", (start, end + 'T99')):
            if icd: yield pid, icd_key(icd), day, 'ehr'
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


def documented(rows, min_claim_dates=2):
    """{pid: {category: {'first': date, 'kinds': set, 'codes': set}}} for categories passing the confirmation rule."""
    acc = defaultdict(lambda: defaultdict(lambda: {'dates': set(), 'kinds': set(), 'codes': set(), 'first': None}))
    for pid, code, day, kind in rows:
        if len(code) < 3: continue
        a = acc[pid][code[:3]]
        a['kinds'].add(kind); a['codes'].add(code)
        if kind == 'claim' and day: a['dates'].add(day)
        if day and (a['first'] is None or day < a['first']): a['first'] = day
    out = {}
    for pid, cats in acc.items():
        keep = {}
        for cat, a in cats.items():
            if 'ehr' in a['kinds'] or 'claim_inpatient' in a['kinds'] or len(a['dates']) >= max(1, min_claim_dates):
                keep[cat] = {'first': a['first'], 'kinds': a['kinds'], 'codes': a['codes']}
        if keep: out[pid] = keep
    return out
