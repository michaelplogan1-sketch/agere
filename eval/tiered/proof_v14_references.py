"""proof_v14 reference answers, computed from the raw warehouse tables.

Written by the independent evaluator acting as the customer's analytics team.
Standard library only. Each ref_<name>(db, *args) takes an open sqlite3
connection to the warehouse (read-only) and returns a list of rows:
  [[figure]]                     for counts and rates
  [[label, figure], ...]         for breakdowns (at most 12 groups)
Rates are shares rounded to 4 decimals, money in dollars to 2, durations to
1 decimal in the unit the question states, per-1,000 rates to 1 decimal.

The code reads the raw tables directly (encounter, encounter_diagnosis,
record, member_month, payer_claim, payer_claim_line, payer_claim_diagnosis,
payer_pharmacy_claim, claim_adjudication, claim, appointment, social_need,
measurement) and does not select from the cust_ views in
proof_v14_customer_definitions.json; it encodes the same certified rules.
"""

import datetime
import json
import statistics
from collections import defaultdict

HOSPITALS = ("WH-HOSPITAL-0", "WH-HOSPITAL-1", "WH-HOSPITAL-2", "WH-HOSPITAL-3")
HOME = {"home", "home_with_guardian", "home_with_followup", "home_with_arranged_assistance"}
ED_ADMIT = {"admitted", "inpatient_admission"}
ED_OBS = {"observation", "hospital_observation", "observation_for_mobility"}


# ---------------------------------------------------------------- helpers

def _d(s):
    return datetime.date.fromisoformat(s[:10])


def _t(s):
    return datetime.datetime.fromisoformat(s).replace(tzinfo=None)


def _yr(s):
    return s[:4] if s else None


def _in(s, start, end=None):
    """Date string s falls in year(s) start..end (inclusive)."""
    if not s:
        return False
    end = end or start
    return str(start) <= s[:4] <= str(end)


def _rate(n, d):
    return round(n / d, 4) if d else None


def _cat(code):
    return code.replace(".", "").upper()[:3] if code else None


def _hosp_encounters(db, etype):
    """One merged record per hospital encounter of a type at our four hospitals."""
    q = ("SELECT encounter_id, patient_id, organization_id, status, event_date, end_date, disposition, record_id "
         "FROM encounter WHERE source_table = 'encounters' AND type = ? AND organization_id IN (?,?,?,?)")
    out = {}
    for eid, pid, org, status, ev, end, disp, rid in db.execute(q, (etype,) + HOSPITALS):
        e = out.setdefault(eid, {"id": eid, "patient": pid, "org": org, "start": ev, "end": None,
                                 "disp": None, "rid": None})
        if ev < e["start"]:
            e["start"] = ev
        if status == "completed":
            e["end"], e["disp"], e["rid"] = end, disp, rid
    dx = {}
    for eid, code in db.execute("SELECT encounter_id, icd10 FROM encounter_diagnosis WHERE source = 'ehr' AND rank = 1"):
        if eid in out:
            dx[eid] = code if eid not in dx else min(dx[eid], code)
    for e in out.values():
        e["dx"] = dx.get(e["id"])
        e["cat"] = _cat(e["dx"])
        if e["end"]:
            days = (_d(e["end"]) - _d(e["start"])).days
            e["los"] = max(1, days)
    return list(out.values())


def _with_times(db, encs):
    for e in encs:
        p = json.loads(db.execute("SELECT payload FROM record WHERE record_id = ?", (e["rid"],)).fetchone()[0])
        e["hours"] = (_t(p["end_time"]) - _t(p["start_time"])).total_seconds() / 3600.0
    return encs


def _mm(db):
    return {(p, m): (lob, payer, prod) for p, m, lob, payer, prod in
            db.execute("SELECT patient_id, month, line_of_business, payer_id, product FROM member_month")}


def _lob(mm, pid, date):
    v = mm.get((pid, date[:7]))
    return v[0] if v else "uninsured"


def _inpatient(db):
    return _hosp_encounters(db, "inpatient")


def _readmit_index(db):
    stays = _inpatient(db)
    by_pt = defaultdict(list)
    for s in stays:
        by_pt[s["patient"]].append(s)
    idx = []
    for s in stays:
        if s["disp"] == "expired":
            continue
        gaps = [(_d(o["start"]) - _d(s["end"])).days for o in by_pt[s["patient"]] if o["id"] != s["id"]]
        gaps = [g for g in gaps if 1 <= g <= 30]
        s = dict(s)
        s["readmit"] = 1 if gaps else 0
        s["days_to"] = min(gaps) if gaps else None
        idx.append(s)
    return idx


def _counted_claims(db, cols):
    q = ("SELECT " + cols + " FROM payer_claim WHERE claim_frequency_code <> '8' AND duplicate_submission = 0 "
         "AND superseded_by_payer_claim_id IS NULL")
    return db.execute(q).fetchall()


def _counted_ids(db):
    return {r[0] for r in _counted_claims(db, "payer_claim_id")}


def _rx(db):
    best = {}
    for rid, cid, pid, fd, drug, st, paid, tier, ptype, avail in db.execute(
            "SELECT record_id, rx_claim_id, patient_id, fill_date, drug_name, claim_status, plan_paid_cents, "
            "formulary_tier, pharmacy_type, available_date FROM payer_pharmacy_claim"):
        key = (avail or "", rid)
        if cid not in best or key > best[cid][0]:
            best[cid] = (key, dict(pid=pid, fill=fd, drug=drug, status=st, paid=paid or 0, tier=tier, ptype=ptype))
    return [v[1] for v in best.values() if v[1]["status"] in ("paid", "recouped")]


def _billed(db):
    rows = defaultdict(list)
    for cid, pid, seq, st, ev, sd, payer, billed, delta in db.execute(
            "SELECT claim_id, patient_id, sequence, status, event_date, service_date, payer, billed_cents, "
            "payer_paid_delta_cents FROM claim_adjudication WHERE claim_source = 'medical_claims'"):
        rows[cid].append((seq, st, ev, sd, payer, billed, delta, pid))
    org = dict(db.execute("SELECT claim_id, service_organization_id FROM claim WHERE claim_source = 'medical_claims'"))
    out = []
    for cid, rs in rows.items():
        rs.sort()
        first = rs[0]
        nonrev = [r for r in rs if r[1] != "reversed"]
        last = nonrev[-1]
        out.append(dict(id=cid, service=first[3], payer=first[4], billed=first[5] or 0,
                        initial=first[1], final=last[1], paid=sum(r[6] or 0 for r in rs),
                        org=org.get(cid), hosp=org.get(cid) in HOSPITALS))
    return out


def _member_events(db):
    claims = _counted_claims(db, "payer_claim_id, patient_id, type_of_bill, place_of_service, service_from, admission_date")
    ids = {c[0] for c in claims}
    ed_lines = set()
    for cid, rev, cpt in db.execute("SELECT payer_claim_id, revenue_code, cpt FROM payer_claim_line"):
        if cid in ids and ((rev or "").startswith("045") or cpt in ("99281", "99282", "99283", "99284", "99285")):
            ed_lines.add(cid)
    adm, ed = set(), set()
    for cid, pid, tob, pos, sf, ad in claims:
        if tob and tob.startswith("011"):
            adm.add((pid, ad or sf))
        if pos == "23" or cid in ed_lines:
            ed.add((pid, sf))
    return adm, ed


def _per_1000(events, mm, year, lob=None):
    n = sum(1 for p, dt in events if _in(dt, year) and (p, dt[:7]) in mm
            and (lob is None or mm[(p, dt[:7])][0] == lob))
    months = sum(1 for (p, m), v in mm.items() if m[:4] == str(year) and (lob is None or v[0] == lob))
    return round(n / months * 12000, 1)


def _top(counter, n):
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:int(n)]
    return items


# ---------------------------------------------------------------- inpatient volume and LOS

def ref_ip_admissions(db, year):
    return [[sum(1 for s in _inpatient(db) if _in(s["start"], year))]]


def ref_ip_admissions_range(db, start, end):
    return [[sum(1 for s in _inpatient(db) if _in(s["start"], start, end))]]


def ref_ip_admissions_by_hospital(db, year):
    c = defaultdict(int)
    for s in _inpatient(db):
        if _in(s["start"], year):
            c[s["org"]] += 1
    return [[k, c[k]] for k in sorted(c)]


def ref_ip_admissions_hospital(db, year, org):
    return [[sum(1 for s in _inpatient(db) if _in(s["start"], year) and s["org"] == org)]]


def ref_ip_admissions_by_quarter(db, year):
    c = defaultdict(int)
    for s in _inpatient(db):
        if _in(s["start"], year):
            c["Q%d" % ((int(s["start"][5:7]) - 1) // 3 + 1)] += 1
    return [[k, c[k]] for k in sorted(c)]


def ref_ip_patients(db, year):
    return [[len({s["patient"] for s in _inpatient(db) if _in(s["start"], year)})]]


def ref_ip_admissions_payer(db, year, lob):
    mm = _mm(db)
    return [[sum(1 for s in _inpatient(db) if _in(s["start"], year) and _lob(mm, s["patient"], s["start"]) == lob)]]


def ref_ip_payer_mix(db, year):
    mm = _mm(db)
    c = defaultdict(int)
    for s in _inpatient(db):
        if _in(s["start"], year):
            c[_lob(mm, s["patient"], s["start"])] += 1
    tot = sum(c.values())
    return [[k, _rate(c[k], tot)] for k in sorted(c)]


def ref_encounter_uninsured_share(db, year):
    mm = _mm(db)
    n = d = 0
    for t in ("inpatient", "emergency", "observation"):
        for e in _hosp_encounters(db, t):
            if _in(e["start"], year):
                d += 1
                n += _lob(mm, e["patient"], e["start"]) == "uninsured"
    return [[_rate(n, d)]]


def ref_top_admit_dx(db, year, n):
    c = defaultdict(int)
    for s in _inpatient(db):
        if _in(s["start"], year) and s["cat"]:
            c[s["cat"]] += 1
    return [[k, v] for k, v in _top(c, n)]


def ref_alos(db, year):
    v = [s["los"] for s in _inpatient(db) if _in(s["end"], year)]
    return [[round(statistics.mean(v), 1)]]


def ref_alos_by_hospital(db, year):
    g = defaultdict(list)
    for s in _inpatient(db):
        if _in(s["end"], year):
            g[s["org"]].append(s["los"])
    return [[k, round(statistics.mean(g[k]), 1)] for k in sorted(g)]


def ref_median_los(db, year):
    v = [s["los"] for s in _inpatient(db) if _in(s["end"], year)]
    return [[round(float(statistics.median(v)), 1)]]


def ref_patient_days(db, year):
    return [[sum(s["los"] for s in _inpatient(db) if _in(s["end"], year))]]


def ref_long_stay_share(db, year, days):
    v = [s["los"] for s in _inpatient(db) if _in(s["end"], year)]
    return [[_rate(sum(1 for x in v if x >= int(days)), len(v))]]


# ---------------------------------------------------------------- discharges and disposition

def _dgroup(disp):
    if disp in HOME:
        return "home"
    if disp in ("skilled_nursing_facility", "expired"):
        return disp
    return "other"


def ref_discharges(db, year):
    return [[sum(1 for s in _inpatient(db) if _in(s["end"], year))]]


def ref_discharge_group_share(db, year, group):
    v = [s for s in _inpatient(db) if _in(s["end"], year)]
    return [[_rate(sum(1 for s in v if _dgroup(s["disp"]) == group), len(v))]]


def ref_discharge_disposition_mix(db, year):
    c = defaultdict(int)
    for s in _inpatient(db):
        if _in(s["end"], year):
            c[_dgroup(s["disp"])] += 1
    return [[k, c[k]] for k in sorted(c)]


# ---------------------------------------------------------------- readmissions

def ref_readmit_rate(db, year):
    v = [s for s in _readmit_index(db) if _in(s["end"], year)]
    return [[_rate(sum(s["readmit"] for s in v), len(v))]]


def ref_readmit_count(db, year):
    return [[sum(s["readmit"] for s in _readmit_index(db) if _in(s["end"], year))]]


def ref_readmit_rate_by_hospital(db, year):
    g = defaultdict(list)
    for s in _readmit_index(db):
        if _in(s["end"], year):
            g[s["org"]].append(s["readmit"])
    return [[k, _rate(sum(g[k]), len(g[k]))] for k in sorted(g)]


def ref_readmit_rate_by_payer(db, year):
    mm = _mm(db)
    g = defaultdict(list)
    for s in _readmit_index(db):
        if _in(s["end"], year):
            g[_lob(mm, s["patient"], s["start"])].append(s["readmit"])
    return [[k, _rate(sum(g[k]), len(g[k]))] for k in sorted(g)]


def ref_hf_readmit_rate(db, start, end):
    v = [s for s in _readmit_index(db) if _in(s["end"], start, end) and s["cat"] == "I50"]
    return [[_rate(sum(s["readmit"] for s in v), len(v))]]


def ref_median_days_to_readmit(db, start, end):
    v = [s["days_to"] for s in _readmit_index(db) if _in(s["end"], start, end) and s["readmit"]]
    return [[round(float(statistics.median(v)), 1)]]


# ---------------------------------------------------------------- ED

def _ed(db):
    return _with_times(db, _hosp_encounters(db, "emergency"))


def ref_ed_visits(db, year):
    return [[sum(1 for e in _hosp_encounters(db, "emergency") if _in(e["start"], year))]]


def ref_ed_visits_by_hospital(db, year):
    c = defaultdict(int)
    for e in _hosp_encounters(db, "emergency"):
        if _in(e["start"], year):
            c[e["org"]] += 1
    return [[k, c[k]] for k in sorted(c)]


def ref_ed_median_hours(db, year, subset="all"):
    v = []
    for e in _ed(db):
        if not _in(e["start"], year):
            continue
        if subset == "admitted" and e["disp"] not in ED_ADMIT:
            continue
        if subset == "discharged" and (e["disp"] in ED_ADMIT or e["disp"] in ED_OBS):
            continue
        v.append(e["hours"])
    return [[round(float(statistics.median(v)), 1)]]


def ref_ed_mean_hours(db, year):
    v = [e["hours"] for e in _ed(db) if _in(e["start"], year)]
    return [[round(statistics.mean(v), 1)]]


def ref_ed_median_hours_by_hospital(db, year):
    g = defaultdict(list)
    for e in _ed(db):
        if _in(e["start"], year):
            g[e["org"]].append(e["hours"])
    return [[k, round(float(statistics.median(g[k])), 1)] for k in sorted(g)]


def ref_ed_share_over_hours(db, year, hours):
    v = [e["hours"] for e in _ed(db) if _in(e["start"], year)]
    return [[_rate(sum(1 for h in v if h >= float(hours)), len(v))]]


def ref_ed_admit_rate(db, year):
    v = [e for e in _hosp_encounters(db, "emergency") if _in(e["start"], year)]
    return [[_rate(sum(1 for e in v if e["disp"] in ED_ADMIT), len(v))]]


def ref_ed_obs_rate(db, year):
    v = [e for e in _hosp_encounters(db, "emergency") if _in(e["start"], year)]
    return [[_rate(sum(1 for e in v if e["disp"] in ED_OBS), len(v))]]


def ref_ed_top_dx(db, year, n):
    c = defaultdict(int)
    for e in _hosp_encounters(db, "emergency"):
        if _in(e["start"], year) and e["cat"]:
            c[e["cat"]] += 1
    return [[k, v] for k, v in _top(c, n)]


def ref_ed_visits_dx(db, year, cat):
    return [[sum(1 for e in _hosp_encounters(db, "emergency") if _in(e["start"], year) and e["cat"] == cat)]]


def ref_ed_payer_mix(db, year):
    mm = _mm(db)
    c = defaultdict(int)
    for e in _hosp_encounters(db, "emergency"):
        if _in(e["start"], year):
            c[_lob(mm, e["patient"], e["start"])] += 1
    tot = sum(c.values())
    return [[k, _rate(c[k], tot)] for k in sorted(c)]


# ---------------------------------------------------------------- observation

def ref_obs_stays(db, year):
    return [[sum(1 for e in _hosp_encounters(db, "observation") if _in(e["start"], year))]]


def ref_obs_mean_hours(db, year):
    v = [e["hours"] for e in _with_times(db, _hosp_encounters(db, "observation")) if _in(e["start"], year)]
    return [[round(statistics.mean(v), 1)]]


def ref_obs_by_hospital(db, year):
    c = defaultdict(int)
    for e in _hosp_encounters(db, "observation"):
        if _in(e["start"], year):
            c[e["org"]] += 1
    return [[k, c[k]] for k in sorted(c)]


# ---------------------------------------------------------------- mortality, sepsis, heart failure

def ref_mortality_rate(db, year):
    v = [s for s in _inpatient(db) if _in(s["end"], year)]
    return [[_rate(sum(1 for s in v if s["disp"] == "expired"), len(v))]]


def ref_inpatient_deaths(db, year):
    return [[sum(1 for s in _inpatient(db) if _in(s["end"], year) and s["disp"] == "expired")]]


def ref_mortality_by_hospital(db, year):
    g = defaultdict(list)
    for s in _inpatient(db):
        if _in(s["end"], year):
            g[s["org"]].append(s["disp"] == "expired")
    return [[k, _rate(sum(g[k]), len(g[k]))] for k in sorted(g)]


def _sepsis(db):
    ids = set()
    for eid, code in db.execute("SELECT encounter_id, icd10 FROM encounter_diagnosis WHERE source = 'ehr'"):
        c = (code or "").replace(".", "").upper()
        if c.startswith("A40") or c.startswith("A41") or c.startswith("R652"):
            ids.add(eid)
    return [s for s in _inpatient(db) if s["id"] in ids]


def ref_sepsis_stays(db, start, end):
    return [[sum(1 for s in _sepsis(db) if _in(s["start"], start, end))]]


def ref_sepsis_mortality(db, start, end):
    v = [s for s in _sepsis(db) if _in(s["end"], start, end)]
    return [[_rate(sum(1 for s in v if s["disp"] == "expired"), len(v))]]


def ref_sepsis_alos(db, start, end):
    v = [s["los"] for s in _sepsis(db) if _in(s["end"], start, end)]
    return [[round(statistics.mean(v), 1)]]


def _hf(db):
    return [s for s in _inpatient(db) if s["cat"] == "I50"]


def ref_hf_admissions(db, year):
    return [[sum(1 for s in _hf(db) if _in(s["start"], year))]]


def ref_hf_share_of_admissions(db, year):
    v = [s for s in _inpatient(db) if _in(s["start"], year)]
    return [[_rate(sum(1 for s in v if s["cat"] == "I50"), len(v))]]


def ref_hf_alos(db, year):
    v = [s["los"] for s in _hf(db) if _in(s["end"], year)]
    return [[round(statistics.mean(v), 1)]]


def ref_hf_mortality(db, start, end):
    v = [s for s in _hf(db) if _in(s["end"], start, end)]
    return [[_rate(sum(1 for s in v if s["disp"] == "expired"), len(v))]]


# ---------------------------------------------------------------- members and spend

def ref_members(db, year):
    return [[len({p for (p, m) in _mm(db) if m[:4] == str(year)})]]


def ref_member_months(db, year):
    return [[sum(1 for (p, m) in _mm(db) if m[:4] == str(year))]]


def ref_member_months_by_lob(db, year):
    c = defaultdict(int)
    for (p, m), v in _mm(db).items():
        if m[:4] == str(year):
            c[v[0]] += 1
    return [[k, c[k]] for k in sorted(c)]


def ref_member_months_product(db, year, product):
    return [[sum(1 for (p, m), v in _mm(db).items() if m[:4] == str(year) and v[2] == product)]]


def ref_members_lob(db, year, lob):
    return [[len({p for (p, m), v in _mm(db).items() if m[:4] == str(year) and v[0] == lob})]]


def ref_medical_paid(db, year):
    rows = _counted_claims(db, "service_from, paid_cents")
    return [[round(sum(c or 0 for sf, c in rows if _in(sf, year)) / 100.0, 2)]]


def ref_medical_paid_by_lob(db, year):
    g = defaultdict(int)
    for sf, c, lob in _counted_claims(db, "service_from, paid_cents, line_of_business"):
        if _in(sf, year):
            g[lob] += c or 0
    return [[k, round(g[k] / 100.0, 2)] for k in sorted(g)]


def ref_counted_claims(db, year):
    return [[sum(1 for (sf,) in _counted_claims(db, "service_from") if _in(sf, year))]]


def ref_payer_claim_denied_share(db, year):
    v = [st for sf, st in _counted_claims(db, "service_from, final_status") if _in(sf, year)]
    return [[_rate(sum(1 for s in v if s in ("denied", "denied_upheld")), len(v))]]


def ref_oon_paid_share(db, year):
    tot = oon = 0
    for sf, c, net in _counted_claims(db, "service_from, paid_cents, network_status"):
        if _in(sf, year):
            tot += c or 0
            if net == "out_of_network":
                oon += c or 0
    return [[_rate(oon, tot)]]


def ref_inpatient_facility_paid(db, year):
    rows = _counted_claims(db, "service_from, paid_cents, type_of_bill")
    return [[round(sum(c or 0 for sf, c, tob in rows if _in(sf, year) and tob and tob.startswith("011")) / 100.0, 2)]]


def ref_top_dx_by_paid(db, year, n):
    g = defaultdict(int)
    for sf, c, dx in _counted_claims(db, "service_from, paid_cents, principal_diagnosis"):
        if _in(sf, year) and dx:
            g[_cat(dx)] += c or 0
    return [[k, round(v / 100.0, 2)] for k, v in _top(g, n)]


def _month_spend(db):
    med = defaultdict(int)
    for pid, sf, c in _counted_claims(db, "patient_id, service_from, paid_cents"):
        med[(pid, sf[:7])] += c or 0
    rx = defaultdict(int)
    for r in _rx(db):
        rx[(r["pid"], r["fill"][:7])] += r["paid"]
    return med, rx


def ref_pmpm(db, year, kind="medical", lob=None):
    mm = _mm(db)
    med, rx = _month_spend(db)
    months = [k for k, v in mm.items() if k[1][:4] == str(year) and (lob is None or v[0] == lob)]
    tot = 0
    for k in months:
        if kind in ("medical", "total"):
            tot += med.get(k, 0)
        if kind in ("pharmacy", "total"):
            tot += rx.get(k, 0)
    return [[round(tot / 100.0 / len(months), 2)]]


def ref_pmpm_by_lob(db, year):
    mm = _mm(db)
    med, _ = _month_spend(db)
    g = defaultdict(lambda: [0, 0])
    for k, v in mm.items():
        if k[1][:4] == str(year):
            g[v[0]][0] += med.get(k, 0)
            g[v[0]][1] += 1
    return [[k, round(g[k][0] / 100.0 / g[k][1], 2)] for k in sorted(g)]


# ---------------------------------------------------------------- pharmacy

def ref_rx_fills(db, year):
    return [[sum(1 for r in _rx(db) if r["status"] == "paid" and _in(r["fill"], year))]]


def ref_rx_plan_paid(db, year):
    return [[round(sum(r["paid"] for r in _rx(db) if _in(r["fill"], year)) / 100.0, 2)]]


def ref_generic_rate(db, year):
    v = [r["tier"] for r in _rx(db) if r["status"] == "paid" and _in(r["fill"], year)
         and r["tier"] in ("generic", "brand", "specialty")]
    return [[_rate(sum(1 for t in v if t == "generic"), len(v))]]


def ref_top_drugs_by_paid(db, year, n):
    g = defaultdict(int)
    for r in _rx(db):
        if _in(r["fill"], year):
            g[r["drug"]] += r["paid"]
    return [[k, round(v / 100.0, 2)] for k, v in _top(g, n)]


def ref_mail_order_share(db, year):
    v = [r for r in _rx(db) if r["status"] == "paid" and _in(r["fill"], year)]
    return [[_rate(sum(1 for r in v if r["ptype"] == "mail_order_pharmacy"), len(v))]]


# ---------------------------------------------------------------- revenue cycle

def _billed_year(db, year, hosp_only=False):
    return [c for c in _billed(db) if _in(c["service"], year) and (c["hosp"] or not hosp_only)]


def ref_billed_claims(db, year):
    return [[len(_billed_year(db, year))]]


def ref_initial_denial_rate(db, year, scope="all"):
    v = _billed_year(db, year, scope == "hospitals")
    return [[_rate(sum(1 for c in v if c["initial"] == "denied"), len(v))]]


def ref_initial_denials(db, year):
    return [[sum(1 for c in _billed_year(db, year) if c["initial"] == "denied")]]


def ref_final_denial_rate(db, year):
    v = _billed_year(db, year)
    return [[_rate(sum(1 for c in v if c["final"] == "denied"), len(v))]]


def ref_overturn_rate(db, year):
    v = [c for c in _billed_year(db, year) if c["initial"] == "denied"]
    return [[_rate(sum(1 for c in v if c["final"] == "paid"), len(v))]]


def ref_initial_denial_rate_by_payer(db, year):
    g = defaultdict(list)
    for c in _billed_year(db, year):
        g[c["payer"]].append(c["initial"] == "denied")
    return [[k, _rate(sum(g[k]), len(g[k]))] for k in sorted(g)]


def ref_billed_net_paid(db, year, scope="all"):
    v = _billed_year(db, year, scope == "hospitals")
    return [[round(sum(c["paid"] for c in v) / 100.0, 2)]]


def ref_final_denied_billed_dollars(db, year):
    return [[round(sum(c["billed"] for c in _billed_year(db, year) if c["final"] == "denied") / 100.0, 2)]]


# ---------------------------------------------------------------- population health / VBC

def ref_admits_per_1000(db, year, lob=None):
    adm, _ = _member_events(db)
    return [[_per_1000(adm, _mm(db), year, lob)]]


def ref_ed_per_1000(db, year, lob=None):
    _, ed = _member_events(db)
    return [[_per_1000(ed, _mm(db), year, lob)]]


def _diabetes(db, year):
    claims = {c[0]: c for c in _counted_claims(db, "payer_claim_id, patient_id, service_from")}
    members = {p for (p, m) in _mm(db) if m[:4] == str(year)}
    dm = set()
    for cid, code in db.execute("SELECT payer_claim_id, icd10 FROM payer_claim_diagnosis"):
        c = claims.get(cid)
        if c and _in(c[2], year) and _cat(code) in ("E10", "E11", "E13") and c[1] in members:
            dm.add(c[1])
    return dm, claims


def ref_diabetic_members(db, year):
    return [[len(_diabetes(db, year)[0])]]


def ref_a1c_testing_rate(db, year):
    dm, claims = _diabetes(db, year)
    tested = set()
    for cid, sd, cpt in db.execute("SELECT payer_claim_id, service_date, cpt FROM payer_claim_line "
                                   "WHERE cpt IN ('83036','83037')"):
        c = claims.get(cid)
        if c and _in(sd, year) and c[1] in dm:
            tested.add(c[1])
    return [[_rate(len(tested), len(dm))]]


def ref_a1c_poor_control_rate(db, year):
    dm, _ = _diabetes(db, year)
    latest = {}
    for pid, ev, val in db.execute("SELECT patient_id, event_date, value_canonical FROM measurement WHERE concept = 'hba1c' "
                                   "AND source_table = 'labs' AND result_current = 1 AND value_canonical IS NOT NULL"):
        if pid in dm and _in(ev, year):
            key = (ev, -val)
            if pid not in latest or key > latest[pid][0]:
                latest[pid] = (key, val)
    poor = sum(1 for p in dm if p not in latest or latest[p][1] > 9.0)
    return [[_rate(poor, len(dm))]]


def ref_members_with_dx_category(db, year, cat):
    claims = {c[0]: c for c in _counted_claims(db, "payer_claim_id, patient_id, service_from")}
    members = {p for (p, m) in _mm(db) if m[:4] == str(year)}
    out = set()
    for cid, code in db.execute("SELECT payer_claim_id, icd10 FROM payer_claim_diagnosis"):
        c = claims.get(cid)
        if c and _in(c[2], year) and _cat(code) == cat and c[1] in members:
            out.add(c[1])
    return [[len(out)]]


# ---------------------------------------------------------------- appointments

def _appts(db, year):
    return [st for sd, st in db.execute("SELECT scheduled_date, final_status FROM appointment") if _in(sd, year)]


def ref_no_show_rate(db, year):
    v = _appts(db, year)
    ns = sum(1 for s in v if s == "no_show")
    kept = sum(1 for s in v if s in ("fulfilled", "arrived", "attended"))
    return [[_rate(ns, ns + kept)]]


def ref_appointments_outcome(db, year, outcome):
    m = {"kept": ("fulfilled", "arrived", "attended"), "no_show": ("no_show",), "cancelled": ("cancelled",)}[outcome]
    return [[sum(1 for s in _appts(db, year) if s in m)]]


# ---------------------------------------------------------------- social needs

def _sdoh(db, year):
    return [(p, n, pos) for p, n, pos, ev in db.execute("SELECT patient_id, need, positive, event_date FROM social_need")
            if _in(ev, year)]


def ref_sdoh_screened(db, year):
    return [[len({p for p, n, pos in _sdoh(db, year)})]]


def ref_sdoh_any_positive_rate(db, year):
    v = _sdoh(db, year)
    scr = {p for p, n, pos in v}
    posi = {p for p, n, pos in v if pos == 1}
    return [[_rate(len(posi), len(scr))]]


def ref_sdoh_need_rate(db, year, need):
    v = [(p, pos) for p, n, pos in _sdoh(db, year) if n == need]
    return [[_rate(len({p for p, pos in v if pos == 1}), len({p for p, pos in v}))]]


def ref_sdoh_need_positive_patients(db, year, need):
    return [[len({p for p, n, pos in _sdoh(db, year) if n == need and pos == 1})]]


def ref_sdoh_rate_by_need(db, year):
    g = defaultdict(lambda: [set(), set()])
    for p, n, pos in _sdoh(db, year):
        g[n][0].add(p)
        if pos == 1:
            g[n][1].add(p)
    return [[k, _rate(len(g[k][1]), len(g[k][0]))] for k in sorted(g)]
