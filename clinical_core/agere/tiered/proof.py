"""The proof scorecard: a frozen, held-out question set scored the way a sceptical reviewer would score it.

Why this exists. Vendor accuracy figures come from small internal suites, single runs and execution-match grading; answer keys
are often wrong; and a system that guesses is scored like one that knows. This scorecard follows the stricter practice:
  * each question has a reference computed here in Python, independently of the stack's SQL and recipes;
  * each question is asked in three phrasings, and each phrasing is run three times (all runs reported, not the best);
  * some questions cannot be answered from this warehouse; declining them is the right answer;
  * scoring penalises a wrong answer ten times a refusal (+1 right, 0 declined, -10 wrong), and an unanswerable question that
    is answered counts as wrong;
  * the stack runs on a copy of the saved recipes with learning switched off, so the set never teaches the stack;
  * the same questions go to a frontier model writing its own SQL, scored the same way.
The question file is eval/tiered/proof_v1.json. Do not use these questions in the curriculum or as recipe examples.

    python -m clinical_core.agere.tiered.proof run [runs] [workers]
"""
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from . import bottles as bottle_store, router, store

QUESTIONS = Path(os.environ.get('AGERE_PROOF_SET', 'eval/tiered/proof_v2.json'))
OUT = Path('artifacts/agere-tiered-core/proof')
PENALTY = 10


# ---- references, written here without the stack's SQL --------------------------------------------------------------

def _day(x):
    return date.fromisoformat(str(x)[:10])


def _readmissions(db, year):
    from .evaluate import _episodes
    eps = _episodes(db.execute("SELECT patient_id, admit_date, discharge_date FROM stay WHERE type = 'inpatient'").fetchall())
    by = defaultdict(list)
    for p, a, d in eps: by[p].append((a, d))
    out = []
    for p, stays in by.items():
        for a, d in stays:
            if not a.startswith(year): continue
            prior = [(dd, aa) for aa, dd in stays if dd and aa < a and 1 <= (_day(a) - _day(dd)).days <= 30]
            if prior: out.append((p, a, max(prior)[0]))
    return out, eps


def ref_readmissions(db, year):
    return [[len(_readmissions(db, year)[0])]]


def ref_readmission_rate(db, year):
    _, eps = _readmissions(db, year)
    idx = [(p, a, d) for p, a, d in eps if d and d.startswith(year)]
    back = sum(1 for p, a, d in idx if any(q == p and b > a and 1 <= (_day(b) - _day(d)).days <= 30 for q, b, _ in eps))
    return [[len(idx), back, round(back / len(idx), 4)]]


def ref_readmissions_no_visit(db, year):
    rows, _ = _readmissions(db, year)
    visits = defaultdict(list)
    for p, d in db.execute("SELECT patient_id, event_date FROM visit WHERE type NOT IN ('inpatient', 'emergency', 'observation', 'telephone', 'orders_only')"):
        visits[p].append(d)
    seen = sum(1 for p, a, dis in rows if any(0 < (_day(v) - _day(dis)).days <= 7 and _day(v) < _day(a) for v in visits[p]))
    return [[len(rows) - seen]]


def _member_months(db, year):
    mm, members = Counter(), defaultdict(set)
    for p, m, lob in db.execute('SELECT patient_id, month, line_of_business FROM member_month'):
        if str(m).startswith(year): mm[lob] += 1; members[lob].add(p)
    return mm, members


def ref_pmpm(db, year):
    mm, members = _member_months(db, year)
    paid = Counter()
    for lob, cents, counts, served in db.execute('SELECT line_of_business, paid_cents, counts_in_metrics, service_from FROM payer_claim'):
        if counts == 1 and served and served.startswith(year) and cents: paid[lob] += cents
    return [[lob, round(paid[lob] / 100 / mm[lob], 2)] for lob in mm]


def ref_members_by_lob(db, year):
    _, members = _member_months(db, year)
    return [[lob, len(p)] for lob, p in members.items()]


def ref_care_gaps(db, year):
    enrolled = {}
    for p, m, lob in db.execute('SELECT patient_id, month, line_of_business FROM member_month ORDER BY month'):
        if str(m).startswith(year): enrolled[p] = enrolled.get(p, False) or lob in ('medicare', 'dual')
    person = {p: (s, b) for p, s, b in db.execute('SELECT patient_id, sex_reported, birth_date FROM patient')}
    age = lambda p: int((date(int(year), 12, 31) - _day(person[p][1])).days / 365.25) if person.get(p) and person[p][1] else -1
    cond = defaultdict(set)
    for p, c, d in db.execute("SELECT patient_id, concept, COALESCE(event_date, '') FROM condition WHERE concept IS NOT NULL"):
        if d <= year + '-12-31': cond[p].add(c)
    a1c = {}
    for p, d, v in db.execute("SELECT patient_id, event_date, value_canonical FROM measurement WHERE concept = 'hba1c' ORDER BY event_date"):
        if str(d).startswith(year): a1c[p] = v
    statin = {p for p, d in db.execute("SELECT patient_id, fill_date FROM fill WHERE drug_class = 'statin'") if d and d.startswith(year)}
    mammo = {p for p, d in db.execute("SELECT patient_id, event_date FROM cancer_screening WHERE test = 'bilateral_screening_mammography'") if d and f'{int(year) - 2}-10-01' <= d <= year + '-12-31'}
    awv = {p for p, d in db.execute('SELECT patient_id, event_date FROM wellness_visit WHERE awv = 1') if d and d.startswith(year)}
    groups = {'annual_wellness_visit': ([p for p in enrolled if enrolled[p]], lambda p: p not in awv),
              'breast_cancer_screening': ([p for p in enrolled if person.get(p, ('', ''))[0] == 'F' and 52 <= age(p) <= 74], lambda p: p not in mammo),
              'diabetes_a1c': ([p for p in enrolled if 18 <= age(p) <= 75 and cond[p] & {'diabetes', 'type_2_diabetes', 'type_1_diabetes'}], lambda p: a1c.get(p) is None or a1c[p] > 9),
              'statin_cardiovascular': ([p for p in enrolled if 21 <= age(p) <= 75 and cond[p] & {'coronary_artery_disease', 'myocardial_infarction'}], lambda p: p not in statin)}
    return [[m, len(e), sum(1 for p in e if gap(p))] for m, (e, gap) in groups.items()]


def ref_a1c_gaps(db, year):
    return [[r[2]] for r in ref_care_gaps(db, year) if r[0] == 'diabetes_a1c']


def ref_top_cost(db, year, n=5):
    paid = Counter()
    for p, cents, counts, served in db.execute('SELECT patient_id, paid_cents, counts_in_metrics, service_from FROM payer_claim'):
        if counts == 1 and served and served.startswith(year) and cents: paid[p] += cents
    return [[p, round(c / 100, 2)] for p, c in sorted(paid.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def ref_ed_visits(db, year):
    return [[sum(1 for (d,) in db.execute("SELECT event_date FROM utilization_event WHERE kind = 'ed_visit'") if str(d).startswith(year))]]


def ref_statin_patients(db, year):
    return [[len({p for p, d in db.execute("SELECT patient_id, fill_date FROM fill WHERE drug_class = 'statin'") if d and d.startswith(year)})]]


def ref_heart_failure_patients(db, year):
    return [[len({p for (p,) in db.execute("SELECT patient_id FROM condition WHERE concept = 'heart_failure'")})]]


def ref_ed_returns(db, year):
    _, eps = _readmissions(db, year)
    dead = {p: d for p, d in db.execute('SELECT patient_id, death_date FROM death') if d}
    arrivals = set()
    for p, d in db.execute("SELECT patient_id, event_date FROM utilization_event WHERE kind = 'ed_visit'"):
        if not str(d).startswith(year): continue
        recent = [dd for q, aa, dd in eps if q == p and dd and 0 < (_day(d) - _day(dd)).days <= 30]
        inside = any(q == p and aa < d and (not dd or dd >= d) for q, aa, dd in eps)
        if recent and not inside and not (p in dead and dead[p] < d): arrivals.add((p, d))
    return [[len(arrivals)]]


def ref_heart_failure_any_source(db, year):
    return [[len({p for (p,) in db.execute("SELECT patient_id FROM concept_patient WHERE kind = 'condition' AND concept = 'heart_failure'")})]]


def _ed_returns(db, year, inside=True, distinct=True):
    _, eps = _readmissions(db, year)
    dead = {p: d for p, d in db.execute('SELECT patient_id, death_date FROM death') if d}
    out = []
    for p, d in db.execute("SELECT patient_id, event_date FROM utilization_event WHERE kind = 'ed_visit'"):
        if not str(d).startswith(year): continue
        recent = [dd for q, aa, dd in eps if q == p and dd and 0 < (_day(d) - _day(dd)).days <= 30]
        in_hospital = any(q == p and aa < d and (not dd or dd >= d) for q, aa, dd in eps)
        if recent and not (inside and in_hospital) and not (p in dead and dead[p] < d): out.append((p, d))
    return [[len(set(out)) if distinct else len(out)]]


def ref_ed_returns_visit_rows(db, year):
    return _ed_returns(db, year, distinct=False)


def ref_ed_returns_no_inpatient_rule(db, year):
    return _ed_returns(db, year, inside=False)


def ref_ed_returns_no_inpatient_rule_visit_rows(db, year):
    return _ed_returns(db, year, inside=False, distinct=False)


def ref_inpatient_patients(db, year):
    return [[len({p for p, k, d in db.execute('SELECT patient_id, kind, event_date FROM utilization_event') if k == 'inpatient' and str(d).startswith(year)})]]


def ref_avg_paid_by_claim_type(db, year):
    tot, n = Counter(), Counter()
    for t, c, f, d in db.execute('SELECT claim_type, paid_cents, counts_in_metrics, service_from FROM payer_claim'):
        if f == 1 and d and d.startswith(year) and c is not None: tot[t] += c; n[t] += 1
    return [[t, round(tot[t] / n[t] / 100, 2)] for t in n]


def ref_no_shows(db, year):
    return [[sum(1 for st, d in db.execute('SELECT final_status, scheduled_date FROM appointment') if st == 'no_show' and str(d).startswith(year))]]


def ref_food_positive_patients(db, year):
    return [[len({p for p, need, pos in db.execute('SELECT patient_id, need, positive FROM social_need') if need == 'food' and pos == 1})]]


def ref_deaths(db, year):
    return [[sum(1 for (d,) in db.execute('SELECT death_date FROM death') if str(d).startswith(year))]]


def ref_glp1_patients(db, year):
    return [[len({p for p, c, d in db.execute('SELECT patient_id, drug_class, fill_date FROM fill') if c == 'glp1_agonist' and d and d.startswith(year)})]]


# ---- references added for proof_v3 (written before proof_v3's first run) --------------------------------------------

def _episodes_out(db, year):
    _, eps = _readmissions(db, year)
    return [(p, a, d) for p, a, d in eps if d and d.startswith(year)]


def ref_stays_discharged(db, year):
    return [[len(_episodes_out(db, year))]]


def ref_avg_length_of_stay(db, year):
    eps = _episodes_out(db, year)
    return [[round(sum((_day(d) - _day(a)).days for _, a, d in eps) / len(eps), 2)]]


def _ed(db, year):
    return [(p, d) for p, d in db.execute("SELECT patient_id, event_date FROM utilization_event WHERE kind = 'ed_visit'") if str(d).startswith(year)]


def ref_frequent_ed_patients(db, year):
    n = Counter(p for p, _ in _ed(db, year))
    return [[sum(1 for v in n.values() if v >= 2)]]


def ref_ed_by_month(db, year):
    n = Counter(str(d)[:7] for _, d in _ed(db, year))
    return [[m, n[m]] for m in sorted(n)]


def ref_patients_by_sex(db, year):
    n = Counter(s for (s,) in db.execute('SELECT sex_reported FROM patient'))
    return [[s, c] for s, c in n.items()]


def ref_patients_65_plus(db, year):
    end = date(int(year), 12, 31)
    return [[sum(1 for (b,) in db.execute('SELECT birth_date FROM patient') if b and (end - _day(b)).days / 365.25 >= 65)]]


def ref_transport_positive_patients(db, year):
    return [[len({p for p, need, pos in db.execute('SELECT patient_id, need, positive FROM social_need') if need == 'transport' and pos == 1})]]


def ref_top_drug_classes(db, year, n=5):
    c = Counter(k for k, d in db.execute('SELECT drug_class, fill_date FROM fill') if k and d and d.startswith(year))
    return [[k, v] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def ref_total_paid(db, year):
    return [[round(sum(c for c, f, d in db.execute('SELECT paid_cents, counts_in_metrics, service_from FROM payer_claim') if f == 1 and d and d.startswith(year) and c) / 100, 2)]]


def ref_paid_by_claim_type(db, year):
    t = Counter()
    for k, c, f, d in db.execute('SELECT claim_type, paid_cents, counts_in_metrics, service_from FROM payer_claim'):
        if f == 1 and d and d.startswith(year) and c: t[k] += c
    return [[k, round(v / 100, 2)] for k, v in t.items()]


def ref_denied_claims(db, year):
    return [[sum(1 for d, x in db.execute('SELECT service_date, ever_denied FROM claim') if str(d).startswith(year) and x == 1)]]


def ref_appointments_by_status(db, year):
    n = Counter(st for st, d in db.execute('SELECT final_status, scheduled_date FROM appointment') if str(d).startswith(year))
    return [[k, v] for k, v in n.items()]


def ref_no_show_rate(db, year):
    rows = [st for st, d in db.execute('SELECT final_status, scheduled_date FROM appointment') if str(d).startswith(year)]
    return [[round(sum(1 for st in rows if st == 'no_show') / len(rows), 4)]]


def ref_a1c_tested_patients(db, year):
    return [[len({p for p, d in db.execute("SELECT patient_id, event_date FROM measurement WHERE concept = 'hba1c'") if str(d).startswith(year)})]]


def ref_avg_a1c(db, year):
    v = [x for x, d in db.execute("SELECT value_canonical, event_date FROM measurement WHERE concept = 'hba1c'") if str(d).startswith(year) and x is not None]
    return [[round(sum(v) / len(v), 2)]]


def ref_awv_patients(db, year):
    return [[len({p for p, d in db.execute('SELECT patient_id, event_date FROM wellness_visit WHERE awv = 1') if str(d).startswith(year)})]]


def ref_mammograms(db, year):
    return [[sum(1 for (d,) in db.execute("SELECT event_date FROM cancer_screening WHERE test = 'bilateral_screening_mammography'") if str(d).startswith(year))]]


def ref_diabetes_condition_patients(db, year):
    return [[len({p for p, c in db.execute('SELECT patient_id, concept FROM condition') if c in ('diabetes', 'type_2_diabetes', 'type_1_diabetes')})]]


def ref_attributed_practices(db, year):
    return [[len({x for x, m in db.execute('SELECT attribution_practice_id, month FROM member_month') if x and str(m).startswith(year)})]]


def ref_member_months(db, year):
    return [[sum(1 for (m,) in db.execute('SELECT month FROM member_month') if str(m).startswith(year))]]


def ref_statin_gaps(db, year):
    return [[r[2]] for r in ref_care_gaps(db, year) if r[0] == 'statin_cardiovascular']


def ref_wellness_gaps(db, year):
    return [[r[2]] for r in ref_care_gaps(db, year) if r[0] == 'annual_wellness_visit']


# ---- references added for proof_v4 (written before proof_v4's first run) --------------------------------------------

def _events(db, year, kind):
    return [(p, d) for p, k, d in db.execute('SELECT patient_id, kind, event_date FROM utilization_event') if k == kind and str(d).startswith(year)]


def ref_inpatient_events(db, year):
    return [[len(_events(db, year, 'inpatient'))]]


def ref_observation_events(db, year):
    return [[len(_events(db, year, 'observation'))]]


def ref_ed_patients(db, year):
    return [[len({p for p, _ in _events(db, year, 'ed_visit')})]]


def ref_patients_with_any_fill(db, year):
    return [[len({p for p, d in db.execute('SELECT patient_id, fill_date FROM fill') if d and d.startswith(year)})]]


def ref_claims_count(db, year):
    return [[sum(1 for f, d in db.execute('SELECT counts_in_metrics, service_from FROM payer_claim') if f == 1 and d and d.startswith(year))]]


def ref_cancelled_appointments(db, year):
    return [[sum(1 for st, d in db.execute('SELECT final_status, scheduled_date FROM appointment') if st == 'cancelled' and str(d).startswith(year))]]


def ref_immunizations(db, year):
    return [[sum(1 for (d,) in db.execute('SELECT event_date FROM immunization') if str(d).startswith(year))]]


# ---- references added for proof_v5 (written before proof_v5's first run) --------------------------------------------

def _results(db, concept, year):
    return [(p, v) for p, v, d in db.execute('SELECT patient_id, value_canonical, event_date FROM measurement WHERE concept = ?', (concept,)) if str(d).startswith(year)]


def ref_ldl_tested_patients(db, year):
    return [[len({p for p, _ in _results(db, 'ldl', year)})]]


def ref_avg_ldl(db, year):
    v = [x for _, x in _results(db, 'ldl', year) if x is not None]
    return [[round(sum(v) / len(v), 2)]]


def ref_egfr_tested_patients(db, year):
    return [[len({p for p, _ in _results(db, 'egfr', year)})]]


def ref_stays_admitted(db, year):
    _, eps = _readmissions(db, year)
    return [[sum(1 for _, a, _ in eps if a.startswith(year))]]


def ref_ckd_any_source(db, year):
    return [[len({p for (p,) in db.execute("SELECT patient_id FROM concept_patient WHERE kind = 'condition' AND concept = 'chronic_kidney_disease'")})]]


def ref_sglt2_patients(db, year):
    return [[len({p for p, c, d in db.execute('SELECT patient_id, drug_class, fill_date FROM fill') if c == 'sglt2_inhibitor' and d and d.startswith(year)})]]


def ref_flu_vaccinated_patients(db, year):
    return [[len({p for p, v, d in db.execute('SELECT patient_id, vaccine, event_date FROM immunization') if v in ('influenza', 'influenza high-dose') and str(d).startswith(year)})]]


def ref_housing_positive_patients(db, year):
    return [[len({p for p, need, pos in db.execute('SELECT patient_id, need, positive FROM social_need') if need == 'housing' and pos == 1})]]


# ---- references added for proof_v8, the ACO job (written before proof_v8's first run) ---------------------------------
# The ACO population is payer WH-PAYER-MSSP's members; terms are read from the contract file itself and risk scores from
# the CMS-HCC engine run directly, so none of these goes through the recipes' SQL or the cached risk table.

_MSSP = 'WH-PAYER-MSSP'


def _aco_mm(db, year):
    return [(p, m) for p, m, payer in db.execute('SELECT patient_id, month, payer_id FROM member_month') if payer == _MSSP and str(m).startswith(year)]


def _aco_members(db, year):
    return {p for p, _ in _aco_mm(db, year)}


def _aco_paid_cents(db, year, claim_type=None):
    return sum(c or 0 for c, f, d, payer, k in db.execute('SELECT paid_cents, counts_in_metrics, service_from, payer_id, claim_type FROM payer_claim')
               if payer == _MSSP and f == 1 and d and d.startswith(year) and (claim_type is None or k == claim_type))


def ref_aco_members(db, year):
    return [[len(_aco_members(db, year))]]


def ref_aco_member_months(db, year):
    return [[len(_aco_mm(db, year))]]


def ref_aco_pmpm(db, year):
    return [[round(_aco_paid_cents(db, year) / 100 / len(_aco_mm(db, year)), 2)]]


def ref_aco_institutional_paid(db, year):
    return [[round(_aco_paid_cents(db, year, 'institutional') / 100, 2)]]


def _terms(year):
    c = json.loads(Path('config/agere/contracts/demo-mssp-aco.json').read_text())
    return c['years'].get(year)


def _savings(db, year):
    t = _terms(year)
    mm = len(_aco_mm(db, year)); paid = _aco_paid_cents(db, year) / 100
    savings = t['benchmark_pmpm_dollars'] * mm - paid
    rate = savings / (t['benchmark_pmpm_dollars'] * mm)
    shared = savings * t['sharing_rate'] if rate >= t['minimum_savings_rate'] and t['quality_standard_met'] else 0.0
    return savings, rate, shared


def ref_aco_shared_savings(db, year):
    return [[round(_savings(db, year)[2], 2)]]


def ref_aco_total_savings(db, year):
    return [[round(_savings(db, year)[0], 2)]]


def ref_aco_savings_rate(db, year):
    return [[round(_savings(db, year)[1], 4)]]


def _aco_scores(db, year):
    from ..clinical import risk_adjustment as ra
    members = _aco_members(db, year)
    dual = {p for p, m, lob in db.execute('SELECT patient_id, month, line_of_business FROM member_month') if lob == 'dual' and str(m).startswith(year)}
    out = {}
    for segment, ids in (('community_non_dual', sorted(members - dual)), ('community_full_dual', sorted(members & dual))):
        if ids:
            for pt in ra.execute(store.path(), ids, {'year': int(year), 'coverage': 'all', 'segment': segment, 'payment_year': 2026, 'top': 500})['patients']:
                out[pt['patient_id']] = pt
    return out


def ref_aco_average_risk(db, year):
    s = _aco_scores(db, year)
    return [[round(sum(p['payment_raf'] for p in s.values()) / len(s), 3)]]


def ref_aco_members_with_hcc(db, year):
    return [[sum(1 for p in _aco_scores(db, year).values() if p['hccs'].get('V28'))]]


def ref_aco_heart_failure_hcc(db, year):
    return [[sum(1 for p in _aco_scores(db, year).values() if any(h['hcc'] == 226 for h in p['hccs'].get('V28', [])))]]


def ref_aco_ed_visits(db, year):
    members = _aco_members(db, year)
    return [[sum(1 for p, k, d in db.execute('SELECT patient_id, kind, event_date FROM utilization_event') if k == 'ed_visit' and p in members and str(d).startswith(year))]]


def ref_aco_stays(db, year):
    members = _aco_members(db, year)
    _, eps = _readmissions(db, year)
    return [[sum(1 for p, a, _ in eps if p in members and a.startswith(year))]]


def ref_aco_readmissions(db, year):
    members = _aco_members(db, year)
    return [[sum(1 for p, _, _ in _readmissions(db, year)[0] if p in members)]]


def ref_aco_top_spender_paid(db, year):
    members, paid = _aco_members(db, year), Counter()
    for p, c, f, d, payer in db.execute('SELECT patient_id, paid_cents, counts_in_metrics, service_from, payer_id FROM payer_claim'):
        if payer == _MSSP and f == 1 and d and d.startswith(year) and p in members: paid[p] += c or 0
    return [[round(max(paid.values()) / 100, 2)]]


def ref_aco_practices(db, year):
    return [[len({x for (p, m, payer, x) in db.execute('SELECT patient_id, month, payer_id, attribution_practice_id FROM member_month') if payer == _MSSP and str(m).startswith(year) and x})]]


# ---- references added for proof_v9, the hospital operations core (written before proof_v9's first run) ---------------
# The governed timing rules are applied here in Python to the raw visit_group rows, without hospital_visit's SQL.

from datetime import datetime as _dt


def _ts(x):
    return _dt.fromisoformat(str(x)[:16]) if x else None


def _visits(db):
    cols = ['visit_group_id', 'patient_id', 'has_ed', 'has_inpatient', 'has_observation', 'start_date', 'end_date', 'ed_arrival', 'ed_departure',
            'admit_decision', 'inpatient_admit', 'discharge_time', 'time_precision', 'disposition', 'principal_icd10']
    out = []
    for r in db.execute('SELECT ' + ', '.join(cols) + ' FROM visit_group'):
        v = dict(zip(cols, r))
        dep = v['inpatient_admit'] if v['has_inpatient'] == 1 and v['inpatient_admit'] and (not v['ed_departure'] or v['ed_departure'] > v['inpatient_admit']) else v['ed_departure']
        minute = v['time_precision'] == 'minute'
        ed = (_ts(dep) - _ts(v['ed_arrival'])).total_seconds() / 60 if minute and v['has_ed'] == 1 and dep and v['ed_arrival'] else None
        board = (_ts(dep) - _ts(v['admit_decision'])).total_seconds() / 60 if minute and dep and v['admit_decision'] else None
        v['ed_minutes'] = ed if ed is not None and 0 <= ed <= 4320 else None
        v['boarding_minutes'] = board if board is not None and 0 <= board <= 4320 else None
        v['outcome'] = 'admitted' if v['has_inpatient'] == 1 else 'observation' if v['has_observation'] == 1 else 'discharged' if v['has_ed'] == 1 else None
        v['year'] = str(v['ed_arrival'] or v['start_date'])[:4]
        dx = v['principal_icd10'] or ''
        v['sepsis'] = dx.startswith(('A40', 'A41', 'R652', 'R65.2'))
        v['hf'] = dx.startswith('I50')
        out.append(v)
    return out


def _median(xs):
    xs = sorted(xs)
    if not xs: return None
    n = len(xs)
    return round((xs[(n - 1) // 2] + xs[n // 2]) / 2, 1)


def _ed(db, year):
    return [v for v in _visits(db) if v['has_ed'] == 1 and v['year'] == year]


def ref_hv_ed_visits(db, year):
    return [[len(_ed(db, year))]]


def ref_hv_ed_median_discharged(db, year):
    return [[_median([v['ed_minutes'] for v in _ed(db, year) if v['outcome'] == 'discharged' and v['ed_minutes'] is not None])]]


def ref_hv_ed_median_admitted(db, year):
    return [[_median([v['ed_minutes'] for v in _ed(db, year) if v['outcome'] == 'admitted' and v['ed_minutes'] is not None])]]


def ref_hv_boarding_mean(db, year):
    xs = [v['boarding_minutes'] for v in _ed(db, year) if v['outcome'] == 'admitted' and v['boarding_minutes'] is not None]
    return [[round(sum(xs) / len(xs), 1)]]


def ref_hv_boarded_over_4h(db, year):
    return [[sum(1 for v in _ed(db, year) if v['outcome'] == 'admitted' and v['boarding_minutes'] is not None and v['boarding_minutes'] > 240)]]


def ref_hv_discharged_over_8h(db, year):
    return [[sum(1 for v in _ed(db, year) if v['outcome'] == 'discharged' and v['ed_minutes'] is not None and v['ed_minutes'] > 480)]]


def ref_hv_ed_admission_rate(db, year):
    e = _ed(db, year)
    return [[round(sum(1 for v in e if v['outcome'] == 'admitted') / len(e), 4)]]


def ref_hv_ed_admitted(db, year):
    return [[sum(1 for v in _ed(db, year) if v['outcome'] == 'admitted')]]


def ref_hv_before_noon_share(db, year):
    d = [v for v in _visits(db) if v['has_inpatient'] == 1 and v['discharge_time'] and str(v['discharge_time']).startswith(year)]
    return [[round(sum(1 for v in d if int(str(v['discharge_time'])[11:13]) < 12) / len(d), 4)]]


def _inpatient(db, year, flag=None):
    return [v for v in _visits(db) if v['has_inpatient'] == 1 and str(v['end_date']).startswith(year) and (flag is None or v[flag])]


def ref_hv_mortality_rate(db, year):
    v = _inpatient(db, year)
    return [[round(sum(1 for x in v if x['disposition'] == 'expired') / len(v), 4)]]


def ref_hv_deaths_in_hospital(db, year):
    return [[sum(1 for x in _inpatient(db, year) if x['disposition'] == 'expired')]]


def _episode_of(eps, patient, start):
    for p, a, d in eps:
        if p == patient and a <= start and (d or '9999') >= start: return a, d
    return None


def _cohort(db, year, flag):
    _, eps = _readmissions(db, year)
    by = defaultdict(list)
    for p, a, d in eps: by[p].append((a, d))
    rows = []
    for v in _inpatient(db, year, flag):
        ep = _episode_of(eps, v['patient_id'], v['start_date'])
        readmit = bool(ep and ep[1] and any(a > ep[0] and 1 <= (_day(a) - _day(ep[1])).days <= 30 for a, _ in by[v['patient_id']]))
        days = (_day(ep[1]) - _day(ep[0])).days if ep and ep[1] else None
        rows.append((v, readmit, days))
    return rows


def ref_hv_sepsis_hospitalizations(db, year):
    return [[len(_cohort(db, year, 'sepsis'))]]


def ref_hv_sepsis_readmission_rate(db, year):
    c = _cohort(db, year, 'sepsis')
    return [[round(sum(1 for _, r, _ in c if r) / len(c), 4)]]


def ref_hv_hf_hospitalizations(db, year):
    return [[len(_cohort(db, year, 'hf'))]]


def ref_hv_hf_readmissions(db, year):
    return [[sum(1 for _, r, _ in _cohort(db, year, 'hf') if r)]]


def ref_hv_hf_mortality_rate(db, year):
    c = _cohort(db, year, 'hf')
    return [[round(sum(1 for v, _, _ in c if v['disposition'] == 'expired') / len(c), 4)]]


def ref_hv_hf_los(db, year):
    xs = [d for _, _, d in _cohort(db, year, 'hf') if d is not None]
    return [[round(sum(xs) / len(xs), 2)]]


# ---- references added for proof_v10, hospital harm on an EMR's FHIR export (written before proof_v10's first run) -------
# Computed from the Bulk FHIR files themselves (artifacts/agere-tiered-core/fhir/export), not from the imported tables or the
# recipes' SQL: drug classes by RxNorm code, the patient's location at each moment from the encounter's location history.

FHIR_EXPORT = Path('artifacts/agere-tiered-core/fhir/export')
_FHIR = {}
_INSULIN, _OPIOID, _ANTAGONIST = {'86009'}, {'7052', '7804', '4337'}, {'7242'}
_POA = 'http://hl7.org/fhir/us/qicore/StructureDefinition/qicore-encounter-diagnosisPresentOnAdmission'


def _fhir():
    if _FHIR: return _FHIR
    read = lambda k: [json.loads(l) for l in open(FHIR_EXPORT / f'{k}.ndjson') if l.strip()]
    loc_type = {r['id']: r['physicalType']['text'] for r in read('Location')}
    birth = {r['id']: r.get('birthDate') for r in read('Patient')}
    sex = {r['id']: r.get('gender') for r in read('Patient')}
    cond = {r['id']: r['code']['coding'][0]['code'] for r in read('Condition')}
    encs = {}
    for r in read('Encounter'):
        if r['class']['code'] != 'IMP': continue
        locs = [(loc_type[l['location']['reference'].split('/')[1]], l['period'].get('start', '')[:16], l['period'].get('end', '')[:16]) for l in r.get('location', [])]
        unit = [l for l in locs if l[0] in ('MED', 'ICU')]
        if not unit or not unit[0][2]: continue
        ed = [l for l in locs if l[0] == 'ED']
        pid = r['subject']['reference'].split('/')[1]
        dx = [(cond.get(d['condition']['reference'].split('/')[1]), next((e['valueCodeableConcept']['coding'][0]['code'] for e in d.get('extension', []) if e['url'] == _POA), None))
              for d in r.get('diagnosis', [])]
        encs[r['id']] = {'pid': pid, 'sex': sex.get(pid), 't0': (ed[0][1] if ed else unit[0][1]), 't1': unit[0][2], 'admit': unit[0][1], 'locs': locs, 'dx': dx,
                         'adult': bool(birth.get(pid)) and (_ts(unit[0][1]) - _ts(birth[pid] + 'T00:00')).days / 365.25 >= 18}
    admins = defaultdict(list)
    for r in read('MedicationAdministration'):
        enc = (r.get('context') or {}).get('reference', '').split('/')[-1]
        if enc not in encs: continue
        code = r['medicationCodeableConcept']['coding'][0]['code']
        when = r['effectiveDateTime'][:16]
        inside = [(s, typ) for typ, s, f in encs[enc]['locs'] if s and when >= s and (not f or when <= f)]
        admins[enc].append({'code': code, 't': when, 'route': r['dosage']['route']['text'], 'loc': max(inside)[1] if inside else None})
    obs = defaultdict(list)
    for r in read('Observation'):
        code = r['code']['coding'][0]['code']
        if code not in ('2345-7', '41653-7', '2160-0') or len(r.get('effectiveDateTime', '')) < 16: continue
        obs[r['subject']['reference'].split('/')[1]].append(('glucose' if code != '2160-0' else 'creatinine', r['effectiveDateTime'][:16], r['valueQuantity']['value']))
    diabetic = {r['subject']['reference'].split('/')[1] for r in read('Condition') if str(r['code']['coding'][0]['code']).startswith(('E10', 'E11', 'E13'))}
    _FHIR.update(encs=encs, admins=admins, obs=obs, diabetic=diabetic)
    return _FHIR


def _harm_encs(year):
    f = _fhir()
    return {k: e for k, e in f['encs'].items() if e['adult'] and e['t1'].startswith(year)}


def _hours(a, b):
    return (_ts(b) - _ts(a)).total_seconds() / 3600


def _hypo(year):
    f, den, harm = _fhir(), 0, 0
    for k, e in _harm_encs(year).items():
        ins = [a['t'] for a in f['admins'][k] if a['code'] in _INSULIN]
        if not ins: continue
        den += 1
        g = [(t, v) for c, t, v in f['obs'][e['pid']] if c == 'glucose']
        for t, v in g:
            if v < 40 and e['t0'] <= t <= e['t1'] and any(x <= t and _hours(x, t) <= 24 for x in ins) \
                    and not any(t2 > t and _hours(t, t2) * 60 <= 5 and v2 > 80 for t2, v2 in g):
                harm += 1; break
    return den, harm


def ref_fh_hypo_events(db, year):
    return [[_hypo(year)[1]]]


def ref_fh_hypo_denominator(db, year):
    return [[_hypo(year)[0]]]


def _orae(year):
    f, den, harm = _fhir(), 0, 0
    for k, e in _harm_encs(year).items():
        op = [a['t'] for a in f['admins'][k] if a['code'] in _OPIOID and a['loc'] != 'OR']
        if not op: continue
        den += 1
        if any(a['code'] in _ANTAGONIST and a['loc'] != 'OR' and a['route'] not in ('oral', 'enteral') and any(o <= a['t'] and _hours(o, a['t']) <= 12 for o in op)
               for a in f['admins'][k]):
            harm += 1
    return den, harm


def ref_fh_orae_events(db, year):
    return [[_orae(year)[1]]]


def ref_fh_orae_rate(db, year):
    d, h = _orae(year)
    return [[round(h / d, 4)]]


def _aki(year):
    f, den, harm = _fhir(), 0, 0
    for k, e in _harm_encs(year).items():
        if _hours(e['t0'], e['t1']) < 48: continue
        cr = sorted((t, v, _hours(e['t0'], t)) for c, t, v in f['obs'][e['pid']] if c == 'creatinine' and e['t0'] <= t <= e['t1'])
        early, late = [x for x in cr if x[2] <= 48], [x for x in cr if x[2] > 48]
        if len(early) < 2 or not late: continue
        first24 = [v for _, v, h in cr if h <= 24]
        idx = min(first24) if first24 else early[0][1]
        if max(v for _, v, _ in early) - idx >= 0.3: continue
        den += 1
        normal = 1.02 if e['sex'] == 'female' else 1.18
        if any(v > normal and v >= 2 * min(v2 for t2, v2, _ in cr if t2 < t) for t, v, h in late):
            harm += 1
    return den, harm


def ref_fh_aki_events(db, year):
    return [[_aki(year)[1]]]


def ref_fh_aki_rate(db, year):
    d, h = _aki(year)
    return [[round(h / d, 4)]]


def ref_fh_pressure_injury_events(db, year):
    return [[sum(1 for e in _harm_encs(year).values() if any(c and c.startswith('L89') and c[-1] in '234' and p == 'N' for c, p in e['dx']))]]


def ref_fh_falls_injury_events(db, year):
    def fall(c): return bool(c) and c[0] == 'W' and c[1:3].isdigit() and 0 <= int(c[1:3]) <= 19
    def injury(c): return bool(c) and (c.startswith('S') or c.startswith('T0') or c.startswith('T1'))
    return [[sum(1 for e in _harm_encs(year).values() if any(fall(c) and p == 'N' for c, p in e['dx']) and any(injury(c) and p == 'N' for c, p in e['dx']))]]


def ref_fh_adult_inpatient_encounters(db, year):
    return [[len(_harm_encs(year))]]


def ref_fh_hyper_days(db, year):
    f, days, events = _fhir(), 0, 0
    for k, e in _harm_encs(year).items():
        g = [(t, v) for c, t, v in f['obs'][e['pid']] if c == 'glucose']
        eligible = e['pid'] in f['diabetic'] or any(a['code'] in _INSULIN for a in f['admins'][k]) or any(v >= 200 and e['t0'] <= t <= e['t1'] for t, v in g)
        if not eligible: continue
        span = _hours(e['t0'], e['t1']) / 24
        for d in range(2, 11):
            if span < d: break
            s = (_ts(e['t0']) + timedelta(hours=(d - 1) * 24)).strftime('%Y-%m-%dT%H:%M'); f2 = (_ts(e['t0']) + timedelta(hours=d * 24)).strftime('%Y-%m-%dT%H:%M')
            days += 1
            events += any(v > 300 and s <= t < f2 for t, v in g)
    return [[events]]


REFERENCES = {name[4:]: fn for name, fn in globals().items() if name.startswith('ref_') and callable(fn)}


# ---- scoring -------------------------------------------------------------------------------------------------------

def _right(reference, rows):
    from . import evaluate, upkeep
    if rows is None: return False
    if len(reference) == 1 and len(reference[0]) == 1 and isinstance(reference[0][0], int) and len(rows) == reference[0][0] > 1:
        return True   # "how many" answered by listing them, one row each
    return evaluate.correct(reference, rows) or upkeep.same_numbers(reference, rows) or _contains(reference, rows)


def _contains(reference, rows):
    """The reference figures appear in the answer when it adds columns (a count beside a rate, a name beside a number)."""
    from . import evaluate
    return len(rows) == len(reference) and evaluate.correct(reference, rows)


def outcome_kind(item, o):
    answered = o.get('status') == 'answered' and o.get('rows') and not (len(o['rows']) == 1 and isinstance(o['rows'][0][0], str) and o['rows'][0][0].startswith('No '))
    if item.get('unanswerable'): return 'declined' if not answered else 'wrong'
    if not answered: return 'declined'
    return 'right' if any(_right(ref, o['rows']) for ref in item.get('references') or [item['reference']]) else 'wrong'


def run(runs=3, workers=4):
    from .. import model_policy
    os.environ.pop('ANTHROPIC_API_KEY', None)
    from run_integrated_stack import keys
    keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
    os.environ.update({k: v for k, v in model_policy.ECONOMICAL_RUNTIME.items() if isinstance(v, str)})
    os.environ['AGERE_TIERED_AUTO_LEARN'] = 'off'
    scratch = Path(tempfile.mkdtemp(prefix='proof-'))
    shutil.copy(os.environ.get('AGERE_TIERED_BOTTLES', bottle_store.DEFAULT), scratch / 'bottles.sqlite3')
    data = json.loads(QUESTIONS.read_text())
    # References are computed on the semantic layer itself. On a portability run the arms answer over another warehouse
    # (AGERE_TIERED_DB, with or without a mapping) holding the same facts; AGERE_PROOF_REFERENCE_DB names the original.
    reference_db = os.environ.get('AGERE_PROOF_REFERENCE_DB')
    import sqlite3
    db = sqlite3.connect(Path(reference_db).resolve().as_uri() + '?mode=ro', uri=True) if reference_db else store.connect()
    items = []
    for q in data['questions']:
        for k, phrasing in enumerate(q['phrasings']):
            # A phrasing may accept more than one reading (recorded in the question file's audit notes); each is computed here.
            names = (q.get('accept_by_phrasing') or {}).get(str(k)) or [q.get('reference')]
            refs = [] if q.get('unanswerable') else [[list(r) for r in REFERENCES[n](db, *q.get('args', []))] for n in names]
            if q.get('numbers_only'):   # labels may rightly be the warehouse's own codes; only the numbers are compared
                refs = [[[v for v in row if isinstance(v, (int, float))] for row in ref] for ref in refs]
            items.append({'id': q['id'], 'phrasing': k, 'question': phrasing, 'domain': q['domain'], 'reference': refs[0] if refs else None, 'references': refs,
                          'unanswerable': q.get('unanswerable', False)})
    db.close()
    import threading
    local = threading.local()

    def bottles():
        if not hasattr(local, 'b'): local.b = bottle_store.Bottles(scratch / 'bottles.sqlite3')
        return local.b

    from . import catalogue, governed

    def one(job):
        item, arm, r = job
        catalogue.ENABLED.set(arm in ('stack', 'stack_unchecked'))   # the governed definitions are the stack's; the other arms run without them
        governed.ENABLED.set(arm in ('stack', 'stack_plain'))   # so are the definition checks; stack_unchecked measures them by their absence
        ladder = arm in ('stack', 'stack_plain', 'stack_unchecked')
        try:
            o = router.answer(item['question'], tiers=router.ALL_TIERS if ladder else ('llm',), bottles=bottles() if ladder else None,
                              s1_ledger=str(scratch / 's1.sqlite3'))
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            o = {'status': 'error', 'final_tier': None, 'rows': None, 'steps': [], 'seconds': 0.0}
        return {'id': item['id'], 'phrasing': item['phrasing'], 'domain': item['domain'], 'arm': arm, 'run': r, 'unanswerable': item['unanswerable'],
                'kind': outcome_kind(item, o), 'tier': o.get('final_tier'), 'usd': sum(s.get('usd', 0.0) for s in o['steps']), 'seconds': o.get('seconds', 0.0),
                # frontier calls and the tokens sent: cost that does not depend on the transport's caching
                'frontier_calls': sum(1 for s in o['steps'] if s.get('tier') == 'llm' and s.get('model')),
                'frontier_tokens': sum(s.get('tokens', 0) for s in o['steps'] if s.get('tier') == 'llm' and s.get('model')),
                'checks_rejected': sum(1 for s in o['steps'] if 'Governed definition' in str(s.get('reason', ''))),
                'rows': (o.get('rows') or [])[:3], 'question': item['question']}

    arms = tuple(os.environ.get('AGERE_PROOF_ARMS', 'stack,stack_plain,frontier').split(','))
    jobs = [(it, arm, r) for r in range(int(runs)) for it in items for arm in arms]
    started = time.monotonic()
    with ThreadPoolExecutor(int(workers)) as pool:
        results = list(pool.map(one, jobs))
    return report(results, items, runs, round(time.monotonic() - started, 1), data)


def report(results, items, runs, wall, data):
    lines = [f"PROOF SCORECARD {data.get('version')}  {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
             f"{len({i['id'] for i in items})} questions ({sum(1 for q in data['questions'] if q.get('unanswerable'))} cannot be answered from this warehouse), "
             f"{len(items)} phrasings, {runs} runs each; +1 right, 0 declined, -{PENALTY} wrong", '']
    summary = {}
    names = {'stack': 'Agere stack (with the governed definitions and their code checks)', 'stack_unchecked': 'Agere stack with the governed definitions but without their code checks',
             'stack_plain': 'Agere stack without the governed definitions',
             'frontier': "frontier model writing its own SQL from the schema (run through the stack's checks, no governed definitions)"}
    for arm, name in names.items():
        rs = [r for r in results if r['arm'] == arm]
        if not rs: continue
        c = Counter(r['kind'] for r in rs)
        score = c['right'] - PENALTY * c['wrong']
        groups = defaultdict(list)
        for r in rs: groups[(r['id'], r['phrasing'])].append(r['kind'])
        consistent = sum(1 for v in groups.values() if len(set(v)) == 1)
        all_right = sum(1 for (qid, _), v in groups.items() if all(k in ('right',) or (k == 'declined' and next(x for x in rs if x['id'] == qid)['unanswerable']) for k in v))
        tiers = Counter(str(r['tier']) for r in rs if r['kind'] == 'right')
        summary[arm] = {'right': c['right'], 'declined': c['declined'], 'wrong': c['wrong'], 'score': score, 'answers': len(rs), 'usd': round(sum(r['usd'] for r in rs), 4),
                        'seconds_each': round(sum(r['seconds'] for r in rs) / max(1, len(rs)), 2), 'same_outcome_every_run': consistent, 'phrasings': len(groups),
                        'right_every_run': all_right, 'right_by_rung': dict(tiers)}
        s = summary[arm]
        lines += [f"{name}", f"  right {s['right']}  declined {s['declined']}  wrong {s['wrong']}  of {s['answers']}   score {s['score']} (best possible {s['answers']})",
                  f"  every run right (or correctly declined): {s['right_every_run']} of {s['phrasings']} phrasings; same outcome on every run: {s['same_outcome_every_run']} of {s['phrasings']}",
                  f"  cost ${s['usd']:.2f} for {s['answers']} answers (${s['usd'] / max(1, s['answers']):.4f} each), {s['seconds_each']} s each   right answers by rung: {s['right_by_rung']}"]
        by = defaultdict(Counter)
        for r in rs: by[r['domain']][r['kind']] += 1
        lines += ['  by area: ' + '; '.join(f"{d} {v['right']} right, {v['declined']} declined, {v['wrong']} wrong" for d, v in sorted(by.items())), '']
    lines.append('wrong answers, for reading:')
    lines += [f"  {r['arm']:8} {r['id']}/{r['phrasing']} run {r['run']} {r['tier']} | {r['question'][:90]} | got {str(r['rows'])[:120]}" for r in results if r['kind'] == 'wrong'] or ['  none']
    lines += ['', f'{wall} s on the clock']
    text = '\n'.join(lines)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    (OUT / f'proof-{stamp}.txt').write_text(text + '\n')
    (OUT / f'proof-{stamp}.json').write_text(json.dumps({'summary': summary, 'results': results}, indent=1, default=str))
    return text


if __name__ == '__main__':
    if sys.argv[1:2] == ['run']:
        print(run(int(sys.argv[2]) if len(sys.argv) > 2 else 3, int(sys.argv[3]) if len(sys.argv) > 3 else 4))
    else:
        print(__doc__)
