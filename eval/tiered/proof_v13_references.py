"""Reference answers for proof_v13.

Written by an independent evaluator from the raw warehouse tables. Standard
library only. Every function takes an open sqlite3 connection to the warehouse
and string arguments from the question entry, and returns a list of rows.

Conventions used throughout (each function's comment says where it departs):
- "Our hospitals" means the four system hospitals WH-HOSPITAL-0..3, whose own EHR
  encounter feed (encounter.source_table = 'encounters') is the system of
  record. Each EHR encounter arrives as an in-progress and a completed version;
  one row per encounter_id is kept, preferring the version that carries an end
  date (the completed one).
- "Across the population" or "any hospital" means every source in the
  warehouse: our EHR, the network ADT feed (adt_visit), payer claims and our own
  facility claims, merged per patient where dates overlap.
- Payer medical claims (payer_claim) are cleaned before any count or dollar
  figure: void originals (claim frequency code 8) are dropped because their
  replacement (frequency 7) carries the claim; duplicate submissions (a CO-18
  "duplicate claim" denial whose patient, service date, billing NPI and charge
  match another claim) are dropped. Adjudications are ordered by adjudication
  date (sequence numbers repeat in one feed). Net paid per claim is the paid
  amount of the last adjudication when that is a payment (or a positive
  corrected amount), and
  zero when the last adjudication is a void, a denial, a recoupment or a
  negative reversal.
- Membership comes from enrollment_span. Open-ended spans (null end or the
  9999-12-31 sentinel) run to the warehouse as-of date (meta.as_of), and every
  span is cut at the member's death date. A member month is a calendar month in
  which the member was enrolled on at least one day; a member counts once per
  month however many spans overlap.
- Rates are shares rounded to 4 decimals, money in dollars rounded to 2,
  durations rounded to 1 decimal.
"""

import collections
import json
import re
import statistics
from datetime import date, datetime, timedelta

_CACHE = {}

OWN_HOSPITALS = ("WH-HOSPITAL-0", "WH-HOSPITAL-1", "WH-HOSPITAL-2", "WH-HOSPITAL-3")


def _d(s):
    if not s:
        return None
    return date.fromisoformat(str(s)[:10])


def _r4(x):
    return round(x, 4)


def _cached(db, key, fn):
    k = (id(db), key)
    if k not in _CACHE:
        _CACHE[k] = fn(db)
    return _CACHE[k]


def _as_of(db):
    return _d(db.execute("select value from meta where key='as_of'").fetchone()[0])


def _year_bounds(y):
    y = int(y)
    return date(y, 1, 1), date(y, 12, 31)


def _in_year(d, y):
    return d is not None and d.year == int(y)


# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------

def _org_names(db):
    return _cached(db, "orgnames", lambda db: dict(
        db.execute("select organization_id, display_name from organization")))


def _patients(db):
    def build(db):
        out = {}
        for pid, bd, sex, seg, county in db.execute(
                "select patient_id, birth_date, sex_reported, data_segment, county from patient"):
            out[pid] = {"birth": _d(bd), "sex": sex, "segment": seg, "county": county}
        return out
    return _cached(db, "patients", build)


def _age_at(db, pid, d):
    p = _patients(db).get(pid)
    if not p or not p["birth"]:
        return None
    b = p["birth"]
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


def _deaths(db):
    """Death date per patient: the earliest date reported by any source column
    of the death table (payer, CCLF, EHR systems, demographic update,
    enrollment termination). Earliest is used because a later report is
    usually a delayed feed of the same event."""
    def build(db):
        cols = ["death_date", "cclf8_death_date", "payer_death_date", "epic_death_date",
                "athena_death_date", "ecw_death_date", "fhir_death_date",
                "demographic_update_death_date", "enrollment_death_term_date"]
        out = {}
        for row in db.execute("select patient_id, " + ",".join(cols) + " from death"):
            ds = [_d(x) for x in row[1:] if x]
            if ds:
                out[row[0]] = min(ds)
        return out
    return _cached(db, "deaths", build)


def _ehr_encounters(db, concepts, own_only=True):
    """One row per EHR encounter_id of the given type_concepts, preferring the
    completed version (the one with an end date) for discharge date,
    disposition, timestamps and the ranked diagnoses in its payload. The admit
    date is the earliest event date across the encounter's versions: in one
    feed the completed version is dated at discharge, so its own event date is
    not the admission date."""
    key = ("ehr", tuple(sorted(concepts)), own_only)

    def build(db):
        q = ("select e.encounter_id, e.patient_id, e.event_date, e.end_date, e.organization_id, "
             "e.disposition, e.status, r.payload from encounter e join record r on r.record_id = e.record_id "
             "where e.source_table = 'encounters' and e.type_concept in (%s)" % ",".join("?" * len(concepts)))
        best = {}
        for enc, pid, ev, end, org, disp, status, payload in db.execute(q, tuple(concepts)):
            if own_only and org not in OWN_HOSPITALS:
                continue
            p = json.loads(payload)
            row = {"enc": enc, "pid": pid, "admit": _d(ev), "disch": _d(end), "org": org,
                   "disp": disp, "start_time": p.get("start_time"), "end_time": p.get("end_time"),
                   "dx": [d.get("icd10") for d in sorted(p.get("diagnoses") or [], key=lambda x: x.get("rank", 99))]}
            cur = best.get(enc)
            first = row["admit"]
            if cur is not None and cur["admit"] and (first is None or cur["admit"] < first):
                first = cur["admit"]
            if cur is None or (row["disch"] and not cur["disch"]):
                best[enc] = row
            best[enc]["admit"] = first
        return list(best.values())
    return _cached(db, key, build)


def _own_ip(db):
    return _ehr_encounters(db, ("inpatient_admission",))


def _own_obs(db):
    return _ehr_encounters(db, ("observation_stay",))


def _own_ed(db):
    return _ehr_encounters(db, ("emergency_visit",))


def _los(a, b):
    """Length of stay in days (midnights); a same-day stay counts as 1 day
    (inpatient census-day convention)."""
    return max((b - a).days, 1)


def _payer_claims(db):
    """Cleaned payer medical claims (see module docstring)."""
    def build(db):
        rows = db.execute(
            "select payer_claim_id, patient_id, payer_id, product, line_of_business, claim_type, type_of_bill, "
            "place_of_service, service_from, service_to, admission_date, discharge_date, discharge_status, "
            "principal_diagnosis, billing_npi, charge_cents, allowed_cents, carc, claim_frequency_code, "
            "first_status, final_status from payer_claim").fetchall()
        groups = collections.defaultdict(list)
        for r in rows:
            groups[(r[1], r[8], r[15], r[14])].append(r)
        dup = set()
        for g in groups.values():
            if len(g) > 1:
                for r in g:
                    if r[17] == "CO-18" and any(o[17] != "CO-18" for o in g if o[0] != r[0]):
                        dup.add(r[0])
        # Adjudications are ordered by adjudication date, then sequence: the
        # payer-copy feed repeats sequence numbers, so sequence alone does not
        # give the order.
        last = {}
        first = {}
        for cid, seq, status, paid, carc, adj_date in db.execute(
                "select payer_claim_id, sequence, claim_status, paid_cents, carc, adjudication_date from payer_adjudication"):
            k = (adj_date or "", seq)
            if cid not in last or k >= last[cid][0]:
                last[cid] = (k, status, paid or 0)
            if cid not in first or k < first[cid][0]:
                first[cid] = (k, status, carc, adj_date)
        out = []
        for r in rows:
            if r[18] == "8" or r[0] in dup:
                continue
            ls = last.get(r[0])
            if ls is None:
                net = 0
            elif ls[1] in ("paid", "reversed") and ls[2] > 0:
                net = ls[2]
            else:
                net = 0
            f = first.get(r[0])
            out.append({"id": r[0], "pid": r[1], "payer": r[2], "product": r[3], "lob": r[4],
                        "ctype": r[5], "tob": r[6] or "", "pos": r[7], "from": _d(r[8]), "to": _d(r[9]),
                        "adm": _d(r[10]), "dis": _d(r[11]), "dstat": r[12], "pdx": r[13],
                        "npi": r[14], "charge": r[15] or 0, "net": net,
                        "first_status": f[1] if f else r[19], "first_carc": f[2] if f else r[17]})
        return out
    return _cached(db, "payer_claims", build)


def _enrollment(db):
    """Enrollment spans with open ends set to as-of and cut at death."""
    def build(db):
        as_of = _as_of(db)
        deaths = _deaths(db)
        out = []
        for pid, product, lob, payer, start, end, prac in db.execute(
                "select patient_id, product, line_of_business, payer_id, coverage_start, coverage_end, "
                "attribution_practice_id from enrollment_span"):
            s = _d(start)
            e = _d(end) if end and not str(end).startswith("9999") else as_of
            e = min(e, as_of)
            if pid in deaths:
                e = min(e, deaths[pid])
            if s is None or e < s:
                continue
            out.append({"pid": pid, "product": product, "lob": lob, "payer": payer, "start": s, "end": e,
                        "practice": prac})
        return out
    return _cached(db, "enrollment", build)


_SECONDARY = ("cob_secondary", "medigap")


def _member_months(db):
    """{(pid, 'YYYY-MM'): span} with one span per member month; a primary
    product is preferred over a secondary/supplemental one (COB secondary,
    Medigap) when both cover the month; between two primary spans in the same
    month (a mid-month switch) the one still in force latest in the month
    wins (coverage at month end)."""
    def build(db):
        mm = {}
        for sp in _enrollment(db):
            y, m = sp["start"].year, sp["start"].month
            while (y, m) <= (sp["end"].year, sp["end"].month):
                k = (sp["pid"], "%04d-%02d" % (y, m))
                cur = mm.get(k)
                if cur is None:
                    mm[k] = sp
                else:
                    rank_new = (sp["product"] not in _SECONDARY, sp["end"], sp["start"])
                    rank_cur = (cur["product"] not in _SECONDARY, cur["end"], cur["start"])
                    if rank_new > rank_cur:
                        mm[k] = sp
                m += 1
                if m == 13:
                    y, m = y + 1, 1
        return mm
    return _cached(db, "mm", build)


def _mm_year(db, y):
    y = str(y)
    return {k: v for k, v in _member_months(db).items() if k[1].startswith(y)}


def _lob_label(sp):
    return sp["lob"]


def _coverage_on(db, pid, d):
    """Primary line of business on a date, or 'uninsured / no enrollment'."""
    k = (pid, "%04d-%02d" % (d.year, d.month))
    sp = _member_months(db).get(k)
    return sp["lob"] if sp else "no enrollment on file"


def _enrolled_months(db, y):
    c = collections.Counter()
    for (pid, mon) in _mm_year(db, y):
        c[pid] += 1
    return c


def _all_stays(db):
    """All-source inpatient stays. Sources: our EHR inpatient encounters, the
    network ADT feed (adt_visit rows whose final patient class is inpatient),
    cleaned payer institutional inpatient claims (type of bill 011x) and our own
    facility inpatient claims (claim_source medical_claims, type of bill 0111).
    Records for the same patient are merged into one stay when their date spans
    overlap or touch (next admit on or before the current discharge), which
    joins ED-to-inpatient ADT conversions, claim copies and transfers. A merged
    stay's discharge date is the latest known discharge; it is unknown only if
    no source has one. Principal diagnosis comes from our EHR, then our facility
    claim, then the payer claim. Expired = EHR disposition 'expired' or a death
    date on or before discharge."""
    def build(db):
        recs = collections.defaultdict(list)
        for e in _own_ip(db):
            recs[e["pid"]].append({"a": e["admit"], "d": e["disch"], "org": e["org"], "src": "ehr",
                                   "pdx": e["dx"][0] if e["dx"] else None, "disp": e["disp"], "pri": 0})
        for pid, a, d, org, disp in db.execute(
                "select patient_id, admit_date, discharge_date, organization_id, discharge_disposition "
                "from adt_visit where final_patient_class = 'I'"):
            recs[pid].append({"a": _d(a), "d": _d(d), "org": org, "src": "adt", "pdx": None, "disp": disp, "pri": 3})
        for pid, a, d, s, pdx in db.execute(
                "select patient_id, admission_date, discharge_date, service_date, principal_diagnosis from claim "
                "where claim_source = 'medical_claims' and type_of_bill like '011%'"):
            recs[pid].append({"a": _d(a or s), "d": _d(d), "org": None, "src": "mc", "pdx": pdx, "disp": None, "pri": 1})
        for c in _payer_claims(db):
            if c["tob"].startswith("011"):
                recs[c["pid"]].append({"a": c["adm"] or c["from"], "d": c["dis"] or c["to"], "org": None,
                                       "src": "pc", "pdx": c["pdx"], "disp": c["dstat"], "pri": 2})
        deaths = _deaths(db)
        stays = []
        for pid, rs in recs.items():
            rs = [r for r in rs if r["a"]]
            rs.sort(key=lambda r: r["a"])
            cur = None
            for r in rs:
                end = r["d"] or r["a"]
                if cur and r["a"] <= cur["end"]:
                    cur["members"].append(r)
                    cur["end"] = max(cur["end"], end)
                else:
                    cur = {"pid": pid, "a": r["a"], "end": end, "members": [r]}
                    stays.append(cur)
        out = []
        for s in stays:
            ds = [m["d"] for m in s["members"] if m["d"]]
            disch = max(ds) if ds else None
            mem = sorted(s["members"], key=lambda m: m["pri"])
            pdx = next((m["pdx"] for m in mem if m["pdx"]), None)
            org = next((m["org"] for m in mem if m["org"]), None)
            expired = any(m["disp"] == "expired" for m in mem)
            dd = deaths.get(s["pid"])
            if dd and disch and s["a"] <= dd <= disch:
                expired = True
            out.append({"pid": s["pid"], "admit": s["a"], "disch": disch, "pdx": pdx, "org": org,
                        "expired": expired, "srcs": set(m["src"] for m in s["members"])})
        return out
    return _cached(db, "all_stays", build)


def _readmit_rate(index, later_admits, days=30):
    """index: list of (pid, discharge date); later_admits: {pid: [admit dates]}.
    A readmission is an admission dated 1..days days after the discharge."""
    n = len(index)
    if not n:
        return 0.0
    hits = 0
    for pid, dd in index:
        if any(dd < a <= dd + timedelta(days=days) for a in later_admits.get(pid, ())):
            hits += 1
    return _r4(hits / n)


def _diagnosis_patients(db, prefixes, y_from, y_to):
    """Patients with an ICD-10 code starting with any prefix dated between the
    two dates, from cleaned payer claims (principal + secondary diagnoses), our
    EHR encounter diagnoses and EHR condition records."""
    key = ("dxp", tuple(prefixes), y_from, y_to)

    def build(db):
        pats = set()
        ok_claims = {c["id"]: c for c in _payer_claims(db)}

        def norm(code):
            if not code:
                return ""
            code = code.replace(".", "").upper().strip()
            return code

        pref = tuple(p.replace(".", "") for p in prefixes)
        for cid, pid, sf, icd, icdr in db.execute(
                "select payer_claim_id, patient_id, service_from, icd10, icd10_received from payer_claim_diagnosis"):
            if cid not in ok_claims:
                continue
            d = _d(sf)
            if d and y_from <= d <= y_to and norm(icdr or icd).startswith(pref):
                pats.add(pid)
        for pid, icd, ev in db.execute("select patient_id, icd10, event_date from encounter_diagnosis"):
            d = _d(ev)
            if d and y_from <= d <= y_to and norm(icd).startswith(pref):
                pats.add(pid)
        for pid, icd, ev, rec in db.execute(
                "select patient_id, icd10, event_date, recorded_date from condition where icd10 is not null"):
            d = _d(ev) or _d(rec)
            if d and y_from <= d <= y_to and norm(icd).startswith(pref):
                pats.add(pid)
        return pats
    return _cached(db, key, build)


def _rx_paid(db):
    """Paid payer pharmacy claims (claim_status 'paid'); rejected claims,
    B2 reversals and recoupments are not fills."""
    def build(db):
        out = []
        for pid, fd, name, ds, tier, ptype, paid, status in db.execute(
                "select patient_id, fill_date, drug_name, days_supply, formulary_tier, pharmacy_type, "
                "plan_paid_cents, claim_status from payer_pharmacy_claim where claim_status = 'paid'"):
            out.append({"pid": pid, "date": _d(fd), "name": name or "", "ing": _ingredient(name),
                        "days": ds or 0, "tier": tier, "ptype": ptype, "paid": paid})
        return out
    return _cached(db, "rx", build)


_SALTS = {"hydrochloride", "hcl", "potassium", "sodium", "calcium", "succinate", "tartrate", "besylate",
          "maleate", "mesylate", "magnesium", "fumarate", "bromide", "citrate", "acetate", "bitartrate",
          "propionate", "furoate", "xinafoate", "monohydrate", "dihydrate", "hyclate", "phosphate"}


def _ingredient(name):
    """Plain ingredient from a free-text drug name: lowercase, drop NDA/package
    prefixes and leading package/release quantities ('24 HR', '0.5 ML',
    '120 ACTUAT'), keep the words before the next strength number, drop salt
    words. 'Lisinopril', 'lisinopril 10 mg tablet' and 'lisinopril 20 MG Oral
    Tablet' all become 'lisinopril'. A combination product is grouped under
    its first-listed ingredient."""
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"\[.*?\]", " ", s)
    s = re.sub(r"^nda\d+\s+\d+\s+actuat\s+", "", s)
    s = re.sub(r"^\s*(\d+(\.\d+)?\s*(hr|hour|ml|actuat|day)\s+)+", "", s)
    s = re.split(r"\d", s, maxsplit=1)[0]
    words = [w for w in re.split(r"[\s/,]+", s) if w and w not in _SALTS]
    return " ".join(words).strip()


def _provider_claims(db):
    """Our own billed claims (claim.claim_source = 'medical_claims') with the
    adjudication history from claim_adjudication: first status = sequence 1;
    final status = status of the last adjudication that is not a reversal (a
    reversal only backs out the prior payment ahead of its replacement); net
    paid = sum of payer-paid deltas; first paid date = first 'paid'
    adjudication date."""
    def build(db):
        adj = collections.defaultdict(list)
        for cid, seq, status, ev, paid in db.execute(
                "select claim_id, sequence, status, event_date, payer_paid_delta_cents from claim_adjudication "
                "where claim_source = 'medical_claims'"):
            adj[cid].append((seq, status, _d(ev), paid or 0))
        rw = {}
        for (p,) in db.execute("select payload from record where stream = 'medical_claims' "
                               "and json_extract(payload, '$.type_of_bill') like '011%'"):
            j = json.loads(p)
            if j.get("relative_weight") is not None:
                rw[j["claim_id"]] = float(j["relative_weight"])
        out = []
        for row in db.execute(
                "select claim_id, patient_id, payer, service_date, billed_cents, first_submitted_date, "
                "type_of_bill, drg, service_organization_id from claim where claim_source = 'medical_claims'"):
            cid = row[0]
            a = sorted(adj.get(cid, []))
            if not a:
                continue
            first = a[0][1]
            nonrev = [x for x in a if x[1] != "reversed"]
            final = nonrev[-1][1] if nonrev else a[-1][1]
            paid_dates = [x[2] for x in a if x[1] == "paid"]
            out.append({"id": cid, "pid": row[1], "payer": row[2], "svc": _d(row[3]), "billed": row[4] or 0,
                        "sub": _d(row[5]), "tob": row[6] or "", "drg": row[7], "org": row[8],
                        "first": first, "final": final, "net": sum(x[3] for x in a),
                        "first_adj": a[0][2], "first_paid": min(paid_dates) if paid_dates else None,
                        "reversed": any(x[1] == "reversed" for x in a), "rw": rw.get(cid)})
        return out
    return _cached(db, "provider_claims", build)


def _pc_year(db, y):
    return [c for c in _provider_claims(db) if _in_year(c["svc"], y)]


def _payer_label(c):
    # 86 facility claims carry the hospital's own ID in the payer field (the
    # payer was not recorded on the source row); they are reported as such.
    return "payer not recorded" if (c["payer"] or "").startswith("WH-HOSPITAL") else c["payer"]


# ---------------------------------------------------------------------------
# Inpatient volume and length of stay: our four hospitals
# ---------------------------------------------------------------------------

def ref_own_ip_admissions(db, y):
    # Plain reading: inpatient admissions at our four hospitals with an admit
    # date in the year; one per EHR encounter (in-progress/completed versions
    # collapsed). Observation stays excluded.
    return [[sum(1 for e in _own_ip(db) if _in_year(e["admit"], y))]]


def _own_disch(db, y):
    return [e for e in _own_ip(db) if _in_year(e["disch"], y)]


def ref_own_ip_alos(db, y):
    # ALOS (Kaufman Hall / HFMA volume convention): patient days / discharges for
    # inpatient discharges in the year at our four hospitals; LOS = discharge
    # date minus admit date, same-day stays count 1 day.
    xs = [_los(e["admit"], e["disch"]) for e in _own_disch(db, y)]
    return [[round(sum(xs) / len(xs), 1)]]


def ref_own_ip_median_los(db, y):
    # Median LOS in days for inpatient discharges in the year at our hospitals
    # (same LOS rule as ALOS).
    xs = [_los(e["admit"], e["disch"]) for e in _own_disch(db, y)]
    return [[round(float(statistics.median(xs)), 1)]]


def ref_own_ip_patient_days(db, y):
    # Patient days attributed to discharges in the year (sum of LOS, same-day = 1)
    # at our four hospitals. Discharge-based patient days, not midnight census.
    return [[sum(_los(e["admit"], e["disch"]) for e in _own_disch(db, y))]]


def ref_own_ip_admissions_by_hospital(db, y):
    # Inpatient admissions (admit date in year) by hospital, our four hospitals,
    # labelled with the organization display name.
    names = _org_names(db)
    c = collections.Counter(e["org"] for e in _own_ip(db) if _in_year(e["admit"], y))
    return [[names.get(o, o), c[o]] for o in sorted(c)]


def ref_own_ip_alos_by_hospital(db, y):
    # ALOS by hospital for discharges in the year (patient days / discharges).
    names = _org_names(db)
    g = collections.defaultdict(list)
    for e in _own_disch(db, y):
        g[e["org"]].append(_los(e["admit"], e["disch"]))
    return [[names.get(o, o), round(sum(v) / len(v), 1)] for o, v in sorted(g.items())]


def ref_own_ip_snf_share(db, y):
    # Share of inpatient discharges in the year at our hospitals with discharge
    # disposition skilled nursing facility (denominator: all discharges,
    # including deaths).
    xs = _own_disch(db, y)
    return [[_r4(sum(1 for e in xs if e["disp"] == "skilled_nursing_facility") / len(xs))]]


def ref_own_ip_disposition_mix(db, y):
    # Discharges in the year at our hospitals by discharge disposition, as
    # shares of all discharges.
    xs = _own_disch(db, y)
    c = collections.Counter(e["disp"] or "not recorded" for e in xs)
    return [[k, _r4(v / len(xs))] for k, v in sorted(c.items())]


def ref_own_ip_mortality(db, y):
    # Crude in-hospital mortality: inpatient discharges in the year at our
    # hospitals with disposition 'expired' / all inpatient discharges.
    xs = _own_disch(db, y)
    return [[_r4(sum(1 for e in xs if e["disp"] == "expired") / len(xs))]]


def _own_index(db, y):
    return [(e["pid"], e["disch"]) for e in _own_disch(db, y) if e["disp"] != "expired"]


def ref_own_readmit_any(db, y):
    # 30-day all-cause readmission rate (CMS hospital-wide readmission timing:
    # an inpatient admission 1-30 days after discharge). Index = live inpatient
    # discharges in the year from our four hospitals. A readmission counts at any
    # hospital visible in the warehouse (all-source merged stays: our EHR, network
    # ADT, payer and facility claims). Planned-readmission exclusions and
    # risk adjustment are not applied.
    later = collections.defaultdict(list)
    for s in _all_stays(db):
        later[s["pid"]].append(s["admit"])
    return [[_readmit_rate(_own_index(db, y), later)]]


def ref_own_ip_top_dx(db, y):
    # Top 5 principal diagnoses (ICD-10 3-character category of the rank-1 EHR
    # encounter diagnosis) for inpatient admissions in the year at our
    # hospitals, by admission count; ties broken by code.
    c = collections.Counter((e["dx"][0] or "")[:3] for e in _own_ip(db) if _in_year(e["admit"], y) and e["dx"])
    return [[k, v] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]


def _is_hf(code):
    return bool(code) and code.replace(".", "").upper().startswith("I50")


def _is_sepsis(code):
    if not code:
        return False
    c = code.replace(".", "").upper()
    return c.startswith("A40") or c.startswith("A41") or c.startswith("R652")


def ref_own_hf_admissions(db, y):
    # Heart failure admissions: inpatient admissions in the year at our hospitals
    # whose principal (rank-1) diagnosis is ICD-10 I50.* (CMS HF cohort codes).
    return [[sum(1 for e in _own_ip(db) if _in_year(e["admit"], y) and e["dx"] and _is_hf(e["dx"][0]))]]


def ref_own_hf_readmit(db, y):
    # HF 30-day all-cause readmission (CMS HRRP HF timing, unadjusted, all
    # payers and ages): index = live discharges in the year from our hospitals
    # with principal diagnosis I50.*; readmission = any inpatient admission at
    # any hospital (all-source merged stays) 1-30 days later.
    later = collections.defaultdict(list)
    for s in _all_stays(db):
        later[s["pid"]].append(s["admit"])
    idx = [(e["pid"], e["disch"]) for e in _own_disch(db, y)
           if e["disp"] != "expired" and e["dx"] and _is_hf(e["dx"][0])]
    return [[_readmit_rate(idx, later)]]


def ref_own_obs_stays(db, y):
    # Observation stays at our hospitals (EHR observation encounters) starting in
    # the year; one per encounter.
    return [[sum(1 for e in _own_obs(db) if _in_year(e["admit"], y))]]


def ref_own_obs_share(db, y):
    # Observation share of hospital stays: observation stays / (observation
    # stays + inpatient admissions) starting in the year at our hospitals
    # (Kaufman Hall volume metric).
    o = sum(1 for e in _own_obs(db) if _in_year(e["admit"], y))
    i = sum(1 for e in _own_ip(db) if _in_year(e["admit"], y))
    return [[_r4(o / (o + i))]]


def ref_own_ip_admissions_trend(db, y1, y2, y3):
    # Inpatient admissions at our hospitals for each of three years (one row per
    # year).
    return [[y, sum(1 for e in _own_ip(db) if _in_year(e["admit"], y))] for y in (y1, y2, y3)]


def ref_own_ip_by_coverage(db, y):
    # Payer mix of inpatient admissions at our hospitals: admissions in the year
    # by the patient's primary line of business on the admit date (from
    # enrollment spans), as shares of admissions. Patients with no enrollment
    # covering the date are 'no enrollment on file'.
    xs = [e for e in _own_ip(db) if _in_year(e["admit"], y)]
    c = collections.Counter(_coverage_on(db, e["pid"], e["admit"]) for e in xs)
    return [[k, _r4(v / len(xs))] for k, v in sorted(c.items())]


def ref_own_ip_peak_month(db, y):
    # Month with the most inpatient admissions at our hospitals in the year;
    # returns [month 'YYYY-MM', admissions]; ties go to the earlier month.
    c = collections.Counter(e["admit"].strftime("%Y-%m") for e in _own_ip(db) if _in_year(e["admit"], y))
    k = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return [[k[0], k[1]]]


def ref_own_followup_7d(db, y):
    # Transitions of care (HEDIS-style 7-day follow-up timing): share of live
    # inpatient discharges home (dispositions home / home_with_guardian) in the
    # year from our hospitals with an ambulatory visit 1-7 days after discharge.
    # Ambulatory visit = a completed or undated-status EHR encounter of an
    # office, primary care, specialty follow-up, telehealth, medication
    # management, home health or wellness type, or a cleaned payer professional
    # claim line with E/M 99202-99215, TCM 99495-99496 or AWV G0438/G0439.
    amb_types = ("office", "primary_care", "specialty_followup", "telehealth", "medication_management",
                 "medication_management_followup", "specialist_office", "pediatric_office", "home_health",
                 "wellness", "ambulatory", "virtual", "telephone")
    visits = collections.defaultdict(set)
    for pid, t, ev, st in db.execute(
            "select patient_id, type, event_date, status from encounter where source_table = 'encounters'"):
        if t in amb_types and st in ("completed", None):
            d = _d(ev)
            if d:
                visits[pid].add(d)
    ok = {c["id"] for c in _payer_claims(db) if c["ctype"] == "professional"}
    codes = {str(x) for x in list(range(99202, 99206)) + list(range(99211, 99216))} | {"99495", "99496", "G0438", "G0439"}
    for cid, pid, sd, cpt, hcpcs in db.execute(
            "select payer_claim_id, patient_id, service_date, cpt, hcpcs from payer_claim_line"):
        if cid in ok and (cpt in codes or hcpcs in codes):
            d = _d(sd)
            if d:
                visits[pid].add(d)
    idx = [e for e in _own_disch(db, y) if e["disp"] in ("home", "home_with_guardian")]
    hit = sum(1 for e in idx if any(e["disch"] < v <= e["disch"] + timedelta(days=7) for v in visits.get(e["pid"], ())))
    return [[_r4(hit / len(idx))]]


# ---------------------------------------------------------------------------
# Inpatient: all sources / population
# ---------------------------------------------------------------------------

def ref_all_ip_admissions(db, y):
    # All-source inpatient stays (see _all_stays: EHR + network ADT + payer and
    # facility inpatient claims, overlapping records merged) with admit date in
    # the year. Observation excluded.
    return [[sum(1 for s in _all_stays(db) if _in_year(s["admit"], y))]]


def ref_all_ip_alos(db, y):
    # ALOS of all-source merged stays discharged in the year (discharge known);
    # LOS from earliest admit to latest discharge of the merged stay, same-day = 1.
    xs = [_los(s["admit"], s["disch"]) for s in _all_stays(db) if _in_year(s["disch"], y)]
    return [[round(sum(xs) / len(xs), 1)]]


def ref_all_readmit(db, y):
    # Population 30-day all-cause readmission rate (CMS hospital-wide timing,
    # unadjusted, no planned-readmission exclusion): index = all-source merged
    # stays discharged alive in the year; readmission = another merged stay
    # admitted 1-30 days after discharge.
    stays = _all_stays(db)
    later = collections.defaultdict(list)
    for s in stays:
        later[s["pid"]].append(s["admit"])
    idx = [(s["pid"], s["disch"]) for s in stays if _in_year(s["disch"], y) and not s["expired"]]
    return [[_readmit_rate(idx, later)]]


def ref_ip_per_1000(db, y):
    # Inpatient admissions per 1,000 members per year (utilization per 1,000):
    # all-source merged stays admitted in the year for patients enrolled in the
    # month of admission, divided by member months / 12, times 1,000. Rounded
    # to 1 decimal.
    mm = _mm_year(db, y)
    n = sum(1 for s in _all_stays(db) if _in_year(s["admit"], y)
            and (s["pid"], s["admit"].strftime("%Y-%m")) in mm)
    return [[round(n / (len(mm) / 12.0) * 1000, 1)]]


def ref_all_ip_by_lob(db, y):
    # All-source inpatient admissions in the year by the patient's primary line
    # of business on the admit date (counts).
    c = collections.Counter(_coverage_on(db, s["pid"], s["admit"]) for s in _all_stays(db) if _in_year(s["admit"], y))
    return [[k, v] for k, v in sorted(c.items())]


def ref_all_mortality_30d(db, y):
    # 30-day post-discharge mortality (CMS MORT-30 timing from discharge rather
    # than admission, unadjusted): share of all-source stays discharged alive in
    # the year whose patient has a death date 1-30 days after discharge.
    deaths = _deaths(db)
    idx = [s for s in _all_stays(db) if _in_year(s["disch"], y) and not s["expired"]]
    hit = sum(1 for s in idx if s["pid"] in deaths and s["disch"] < deaths[s["pid"]] <= s["disch"] + timedelta(days=30))
    return [[_r4(hit / len(idx))]]


# ---------------------------------------------------------------------------
# Claims-based (payer) acute cohorts
# ---------------------------------------------------------------------------

def _claim_stays(db):
    """Inpatient stays from cleaned payer claims only (type of bill 011x),
    merged per patient when dates overlap or touch. Principal diagnosis from
    the earliest claim of the stay; line of business from the claim."""
    def build(db):
        by = collections.defaultdict(list)
        for c in _payer_claims(db):
            if c["tob"].startswith("011"):
                a = c["adm"] or c["from"]
                d = c["dis"] or c["to"] or a
                by[c["pid"]].append((a, d, c))
        out = []
        for pid, rs in by.items():
            rs.sort(key=lambda r: r[0])
            cur = None
            for a, d, c in rs:
                if cur and a <= cur["disch"]:
                    cur["disch"] = max(cur["disch"], d)
                    cur["paid"] += c["net"]
                else:
                    cur = {"pid": pid, "admit": a, "disch": d, "pdx": c["pdx"], "lob": c["lob"],
                           "product": c["product"], "paid": c["net"]}
                    out.append(cur)
        return out
    return _cached(db, "claim_stays", build)


def ref_claims_hf_readmit_medicare(db, y):
    # CMS HRRP heart-failure readmission, unadjusted approximation from payer
    # claims: index = claim-based inpatient stays (011x, merged) discharged in
    # the year with principal diagnosis I50.*, for Medicare members (line of
    # business medicare: FFS or Medicare Advantage; dual excluded) aged 65+ at
    # admission; readmission = any claim-based inpatient stay admitted 1-30 days
    # after discharge. Planned-readmission exclusions and risk adjustment not
    # applied.
    cs = _claim_stays(db)
    later = collections.defaultdict(list)
    for s in cs:
        later[s["pid"]].append(s["admit"])
    idx = [(s["pid"], s["disch"]) for s in cs if _in_year(s["disch"], y) and _is_hf(s["pdx"])
           and s["lob"] == "medicare" and (_age_at(db, s["pid"], s["admit"]) or 0) >= 65]
    return [[_readmit_rate(idx, later)]]


def ref_claims_sepsis_admissions(db, y):
    # Sepsis admissions from payer claims: claim-based inpatient stays (011x,
    # merged) admitted in the year whose principal diagnosis is A40.*, A41.* or
    # R65.2* (CMS sepsis cohort principal codes).
    return [[sum(1 for s in _claim_stays(db) if _in_year(s["admit"], y) and _is_sepsis(s["pdx"]))]]


def ref_all_sepsis_admissions(db, y):
    # Sepsis admissions across all sources: all-source merged stays admitted in
    # the year whose principal diagnosis (EHR, then facility claim, then payer
    # claim) is A40.*, A41.* or R65.2*.
    return [[sum(1 for s in _all_stays(db) if _in_year(s["admit"], y) and _is_sepsis(s["pdx"]))]]


def ref_claims_ip_paid_per_stay(db, y):
    # Average payer net paid per claim-based inpatient stay (011x claims merged
    # per stay) admitted in the year, in dollars.
    xs = [s["paid"] for s in _claim_stays(db) if _in_year(s["admit"], y)]
    return [[round(sum(xs) / len(xs) / 100.0, 2)]]


def ref_claims_top_ip_dx(db, y):
    # Top 5 principal diagnosis categories (ICD-10 first 3 characters) of
    # claim-based inpatient stays admitted in the year, by stay count; ties by
    # code.
    c = collections.Counter((s["pdx"] or "").replace(".", "")[:3] for s in _claim_stays(db)
                            if _in_year(s["admit"], y) and s["pdx"])
    return [[k, v] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]


# ---------------------------------------------------------------------------
# Emergency department
# ---------------------------------------------------------------------------

def _ts(s):
    """Parse an ISO timestamp; a timestamp without an offset is taken as UTC so
    that all comparisons are on one clock."""
    if not s:
        return None
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        from datetime import timezone
        t = t.replace(tzinfo=timezone.utc)
    return t


def _ed_hours(e):
    a, b = _ts(e["start_time"]), _ts(e["end_time"])
    if a and b and b >= a:
        return (b - a).total_seconds() / 3600.0
    return None


_ED_ADMIT = ("admitted", "inpatient_admission")
_ED_OBS = ("observation", "hospital_observation", "observation_for_mobility")


def ref_own_ed_visits(db, y):
    # ED visits at our four hospitals with arrival date in the year (one per EHR
    # ED encounter).
    return [[sum(1 for e in _own_ed(db) if _in_year(e["admit"], y))]]


def ref_own_ed_visits_by_hospital(db, y):
    # ED visits in the year by hospital (our four hospitals).
    names = _org_names(db)
    c = collections.Counter(e["org"] for e in _own_ed(db) if _in_year(e["admit"], y))
    return [[names.get(o, o), c[o]] for o in sorted(c)]


def ref_own_ed_median_los(db, y):
    # Median ED length of stay in hours (EDBA: arrival to departure, here the EHR
    # ED encounter start to end timestamps) for all ED visits in the year at our
    # hospitals.
    xs = [h for e in _own_ed(db) if _in_year(e["admit"], y) for h in [_ed_hours(e)] if h is not None]
    return [[round(statistics.median(xs), 1)]]


def ref_own_ed_median_los_discharged(db, y):
    # Median ED LOS in hours for patients discharged from the ED (not admitted
    # and not placed in observation): the former CMS OP-18 concept, arrival to
    # departure.
    xs = [h for e in _own_ed(db) if _in_year(e["admit"], y) and e["disp"] not in _ED_ADMIT + _ED_OBS
          for h in [_ed_hours(e)] if h is not None]
    return [[round(statistics.median(xs), 1)]]


def ref_own_ed_median_los_admitted(db, y):
    # Median ED LOS in hours for ED patients admitted as inpatients (ED arrival
    # to ED departure; the former ED-1 concept).
    xs = [h for e in _own_ed(db) if _in_year(e["admit"], y) and e["disp"] in _ED_ADMIT
          for h in [_ed_hours(e)] if h is not None]
    return [[round(statistics.median(xs), 1)]]


def ref_own_ed_admit_rate(db, y):
    # ED admission rate: share of ED visits in the year at our hospitals with
    # disposition admitted to inpatient (observation not counted as admitted).
    xs = [e for e in _own_ed(db) if _in_year(e["admit"], y)]
    return [[_r4(sum(1 for e in xs if e["disp"] in _ED_ADMIT) / len(xs))]]


def ref_own_ed_admit_or_obs_rate(db, y):
    # Alternative reading of "admitted from the ED": share of ED visits in the
    # year at our hospitals that went on to an inpatient admission or an
    # observation stay.
    xs = [e for e in _own_ed(db) if _in_year(e["admit"], y)]
    return [[_r4(sum(1 for e in xs if e["disp"] in _ED_ADMIT + _ED_OBS) / len(xs))]]


def ref_own_ed_over_4h(db, y):
    # Share of ED visits in the year at our hospitals whose ED LOS exceeded
    # 4 hours (arrival to departure), among visits with both timestamps.
    xs = [h for e in _own_ed(db) if _in_year(e["admit"], y) for h in [_ed_hours(e)] if h is not None]
    return [[_r4(sum(1 for h in xs if h > 4) / len(xs))]]


def _pop_ed_events(db):
    """ED visits across all sources as distinct (patient, date): our EHR ED
    encounters, network ADT visits that started in the ED (patient class E),
    and cleaned payer claims with an ED revenue code 045x or ED E/M
    99281-99285 or place of service 23. Same patient and date from several
    sources is one visit."""
    def build(db):
        ev = set()
        for e in _ehr_encounters(db, ("emergency_visit",), own_only=True):
            if e["admit"]:
                ev.add((e["pid"], e["admit"]))
        for pid, a in db.execute("select patient_id, admit_date from adt_visit where patient_class = 'E'"):
            if a:
                ev.add((pid, _d(a)))
        ok = {c["id"]: c for c in _payer_claims(db)}
        edc = set()
        for cid, rev, cpt in db.execute("select payer_claim_id, revenue_code, cpt from payer_claim_line"):
            if (rev or "").startswith("045") or (cpt or "") in ("99281", "99282", "99283", "99284", "99285"):
                edc.add(cid)
        for cid, c in ok.items():
            if cid in edc or c["pos"] == "23":
                if c["from"]:
                    ev.add((c["pid"], c["from"]))
        return ev
    return _cached(db, "pop_ed", build)


def ref_pop_ed_visits(db, y):
    # ED visits across all sources in the year (distinct patient-date; see
    # _pop_ed_events).
    return [[sum(1 for p, d in _pop_ed_events(db) if d.year == int(y))]]


def ref_pop_ed_per_1000(db, y):
    # ED visits per 1,000 members per year: all-source ED visits in the year for
    # patients enrolled in that month / (member months / 12) x 1,000, 1 decimal.
    mm = _mm_year(db, y)
    n = sum(1 for p, d in _pop_ed_events(db) if d.year == int(y) and (p, d.strftime("%Y-%m")) in mm)
    return [[round(n / (len(mm) / 12.0) * 1000, 1)]]


def ref_pop_ed_frequent(db, y):
    # Frequent ED users: distinct patients with 4 or more all-source ED visits
    # (distinct dates) in the year (common frequent-user threshold).
    c = collections.Counter(p for p, d in _pop_ed_events(db) if d.year == int(y))
    return [[sum(1 for v in c.values() if v >= 4)]]


def ref_network_ed_conversion(db, y):
    # Network ADT feed: share of ED-registered visits in the year whose final
    # patient class became inpatient (ED-to-inpatient conversion rate).
    rows = db.execute("select final_patient_class from adt_visit where patient_class = 'E' "
                      "and substr(admit_date,1,4) = ?", (str(y),)).fetchall()
    return [[_r4(sum(1 for r in rows if r[0] == "I") / len(rows))]]


# ---------------------------------------------------------------------------
# Mortality
# ---------------------------------------------------------------------------

def ref_deaths(db, y):
    # Deaths in the year among our patients and members: distinct patients whose
    # death date (earliest date reported by any source) falls in the year.
    return [[sum(1 for d in _deaths(db).values() if d.year == int(y))]]


def ref_member_mortality_rate(db, y):
    # Crude annual mortality among members: deaths in the year of patients
    # enrolled at some point in the year / distinct members enrolled in the year.
    mem = set(_enrolled_months(db, y))
    n = sum(1 for p, d in _deaths(db).items() if d.year == int(y) and p in mem)
    return [[_r4(n / len(mem))]]


# ---------------------------------------------------------------------------
# Membership, payer mix and claims spend
# ---------------------------------------------------------------------------

def ref_members(db, y):
    # Distinct members with at least one member month in the year.
    return [[len(_enrolled_months(db, y))]]


def ref_member_months(db, y):
    # Total member months in the year (any-day-enrolled convention, one per
    # member per month).
    return [[len(_mm_year(db, y))]]


def ref_member_months_by_lob(db, y):
    # Member months in the year by primary line of business (primary product
    # preferred over COB secondary/Medigap when both cover a month).
    c = collections.Counter(_lob_label(v) for v in _mm_year(db, y).values())
    return [[k, v] for k, v in sorted(c.items())]


def ref_lob_mix_share(db, y):
    # Payer mix of membership: share of member months in the year by primary
    # line of business.
    mm = _mm_year(db, y)
    c = collections.Counter(_lob_label(v) for v in mm.values())
    return [[k, _r4(v / len(mm))] for k, v in sorted(c.items())]


def ref_ma_share_of_medicare(db, y):
    # Medicare Advantage penetration: Medicare Advantage member months / all
    # Medicare member months (products medicare_ffs + medicare_advantage; dual
    # products excluded) in the year.
    mm = _mm_year(db, y).values()
    ma = sum(1 for v in mm if v["product"] == "medicare_advantage")
    ffs = sum(1 for v in mm if v["product"] == "medicare_ffs")
    return [[_r4(ma / (ma + ffs))]]


def ref_avg_monthly_members(db, y):
    # Average monthly membership in the year: member months / 12, 1 decimal.
    return [[round(len(_mm_year(db, y)) / 12.0, 1)]]


def _claims_year(db, y):
    return [c for c in _payer_claims(db) if _in_year(c["from"], y)]


def ref_total_medical_paid(db, y):
    # Total medical claims spend: net payer paid on cleaned payer medical claims
    # with service-from date in the year, dollars (pharmacy excluded).
    return [[round(sum(c["net"] for c in _claims_year(db, y)) / 100.0, 2)]]


def ref_medical_pmpm(db, y):
    # Medical PMPM: total net paid on cleaned payer medical claims with service
    # in the year / member months in the year, dollars.
    return [[round(sum(c["net"] for c in _claims_year(db, y)) / 100.0 / len(_mm_year(db, y)), 2)]]


def ref_medical_pmpm_by_lob(db, y):
    # Medical PMPM by line of business: net paid on cleaned claims with service
    # in the year grouped by the claim's line of business / member months
    # enrolled in that same line of business (a member-month counts once per
    # line of business it is enrolled in, so secondary lines such as Medigap and
    # COB secondary get their own denominators). Lines with no member months are
    # omitted.
    paid = collections.Counter()
    for c in _claims_year(db, y):
        paid[c["lob"]] += c["net"]
    seen = set()
    for sp in _enrollment(db):
        yy, m = sp["start"].year, sp["start"].month
        while (yy, m) <= (sp["end"].year, sp["end"].month):
            if yy == int(y):
                seen.add((sp["pid"], m, sp["lob"]))
            m += 1
            if m == 13:
                yy, m = yy + 1, 1
    mm = collections.Counter(k[2] for k in seen)
    return [[k, round(paid[k] / 100.0 / mm[k], 2)] for k in sorted(mm) if mm[k]]


def ref_medical_pmpm_change(db, y1, y2):
    # Change in medical PMPM from y1 to y2 as a share ((pmpm2 - pmpm1) / pmpm1).
    a = ref_medical_pmpm(db, y1)[0][0]
    b = ref_medical_pmpm(db, y2)[0][0]
    return [[_r4((b - a) / a)]]


def ref_paid_by_payer_top5(db, y):
    # Top 5 payers by net medical paid on cleaned claims with service in the
    # year, dollars.
    c = collections.Counter()
    for x in _claims_year(db, y):
        c[x["payer"]] += x["net"]
    return [[k, round(v / 100.0, 2)] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]


def _setting(c):
    if c["tob"].startswith("011"):
        return "inpatient facility"
    if c["tob"].startswith("013"):
        return "outpatient facility"
    if c["ctype"] == "professional":
        return "professional"
    return "other institutional"


def ref_paid_share_by_setting(db, y):
    # Share of net medical paid (cleaned claims, service in year) by setting:
    # inpatient facility (bill type 011x), outpatient facility (013x),
    # professional claims, other institutional (SNF, home health, hospice etc.).
    c = collections.Counter()
    for x in _claims_year(db, y):
        c[_setting(x)] += x["net"]
    t = sum(c.values())
    return [[k, _r4(v / t)] for k, v in sorted(c.items())]


def ref_spend_top5pct_share(db, y):
    # Spend concentration: share of total net medical paid (cleaned claims,
    # service in year) incurred by the costliest 5% of members, where members
    # are those enrolled in the year (n = members; top ceil(5% of n) by paid;
    # members with no claims count as zero).
    mem = list(_enrolled_months(db, y))
    paid = collections.Counter()
    for c in _claims_year(db, y):
        paid[c["pid"]] += c["net"]
    vals = sorted((paid.get(p, 0) for p in mem), reverse=True)
    k = -(-len(vals) * 5 // 100)
    t = sum(paid.values())
    return [[_r4(sum(vals[:k]) / t)]]


def ref_payer_denial_rate(db, y):
    # Payer-side initial denial rate (HFMA Claim Integrity Task Force initial
    # denial rate, by count): cleaned payer medical claims with service in the
    # year whose first adjudication was a denial (denied or denied_upheld) / all
    # cleaned claims with service in the year.
    xs = _claims_year(db, y)
    return [[_r4(sum(1 for c in xs if c["first_status"] in ("denied", "denied_upheld")) / len(xs))]]


def ref_payer_denial_top_reasons(db, y):
    # Top 5 denial reasons (CARC on the first adjudication) among cleaned payer
    # medical claims with service in the year that were denied on first
    # adjudication; counts; ties by code.
    c = collections.Counter(x["first_carc"] for x in _claims_year(db, y)
                            if x["first_status"] in ("denied", "denied_upheld"))
    return [[k, v] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0] or ""))[:5]]


def ref_duplicate_submissions(db, y):
    # Number of duplicate claim submissions in the payer feed with service in
    # the year: CO-18 denials whose patient, service date, billing NPI and charge
    # match another, non-duplicate claim.
    rows = db.execute("select payer_claim_id, patient_id, service_from, charge_cents, billing_npi, carc "
                      "from payer_claim").fetchall()
    g = collections.defaultdict(list)
    for r in rows:
        g[(r[1], r[2], r[3], r[4])].append(r)
    n = 0
    for v in g.values():
        for r in v:
            if r[5] == "CO-18" and any(o[5] != "CO-18" for o in v if o[0] != r[0]) and (r[2] or "")[:4] == str(y):
                n += 1
    return [[n]]


# ---------------------------------------------------------------------------
# Revenue cycle: our own billed claims (claim_source = 'medical_claims')
# ---------------------------------------------------------------------------

def ref_rc_claims(db, y):
    # Number of claims we billed with date of service in the year (own billed
    # claims, one per claim ID).
    return [[len(_pc_year(db, y))]]


def ref_rc_initial_denial_rate(db, y):
    # Initial denial rate by count (HFMA Claim Integrity Task Force): our claims
    # with service in the year denied on first adjudication / all our claims
    # with service in the year that were adjudicated.
    xs = _pc_year(db, y)
    return [[_r4(sum(1 for c in xs if c["first"] == "denied") / len(xs))]]


def ref_rc_initial_denial_rate_dollars(db, y):
    # Initial denial rate by dollars: billed charges of our claims (service in
    # year) denied on first adjudication / billed charges of all our claims
    # with service in the year.
    xs = _pc_year(db, y)
    return [[_r4(sum(c["billed"] for c in xs if c["first"] == "denied") / sum(c["billed"] for c in xs))]]


def ref_rc_final_denial_rate(db, y):
    # Final denial rate by count: our claims with service in the year whose final
    # adjudication status is denied / all our claims with service in the year.
    xs = _pc_year(db, y)
    return [[_r4(sum(1 for c in xs if c["final"] == "denied") / len(xs))]]


def ref_rc_overturn_rate(db, y):
    # Denial overturn rate (HFMA Claim Integrity Task Force): of our claims with
    # service in the year that were denied on first adjudication, the share whose
    # final status is paid.
    xs = [c for c in _pc_year(db, y) if c["first"] == "denied"]
    return [[_r4(sum(1 for c in xs if c["final"] == "paid") / len(xs))]]


def ref_rc_denial_rate_by_payer(db, y):
    # Initial denial rate by payer for our claims with service in the year.
    # Claims whose payer field holds a hospital ID instead of a payer are shown
    # as 'payer not recorded'.
    g = collections.defaultdict(lambda: [0, 0])
    for c in _pc_year(db, y):
        k = _payer_label(c)
        g[k][1] += 1
        if c["first"] == "denied":
            g[k][0] += 1
    return [[k, _r4(v[0] / v[1])] for k, v in sorted(g.items())]


def ref_rc_denied_dollars_final(db, y):
    # Billed dollars on our claims (service in year) that ended finally denied.
    return [[round(sum(c["billed"] for c in _pc_year(db, y) if c["final"] == "denied") / 100.0, 2)]]


def ref_rc_median_days_to_submit(db, y):
    # Median days from date of service to first claim submission for our claims
    # with service in the year; claims with no submission record (laboratory
    # claims without an 837 submission row) are excluded.
    xs = [(c["sub"] - c["svc"]).days for c in _pc_year(db, y) if c["sub"]]
    return [[round(float(statistics.median(xs)), 1)]]


def ref_rc_median_days_submit_to_paid(db, y):
    # Median days from first submission to first paid adjudication (remittance)
    # for our claims with service in the year that were submitted and paid.
    xs = [(c["first_paid"] - c["sub"]).days for c in _pc_year(db, y) if c["sub"] and c["first_paid"]]
    return [[round(float(statistics.median(xs)), 1)]]


def ref_rc_total_billed(db, y):
    # Gross charges: total billed dollars on our claims with service in the year.
    return [[round(sum(c["billed"] for c in _pc_year(db, y)) / 100.0, 2)]]


def ref_rc_total_paid(db, y):
    # Total payer payments on our claims with service in the year: sum of net
    # payer-paid deltas across the adjudication history, dollars.
    return [[round(sum(c["net"] for c in _pc_year(db, y)) / 100.0, 2)]]


def ref_rc_paid_to_billed(db, y):
    # Payer payments as a share of gross charges for our claims with service in
    # the year (net payer paid / billed).
    xs = _pc_year(db, y)
    return [[_r4(sum(c["net"] for c in xs) / sum(c["billed"] for c in xs))]]


def ref_rc_paid_by_payer(db, y):
    # Net payer payments on our claims with service in the year by payer,
    # dollars ('payer not recorded' where the payer field holds a hospital ID).
    g = collections.Counter()
    for c in _pc_year(db, y):
        g[_payer_label(c)] += c["net"]
    return [[k, round(v / 100.0, 2)] for k, v in sorted(g.items())]


def ref_rc_cmi(db, y):
    # Case mix index (HFMA MAP Key convention: MS-DRG relative weights applied to
    # all payers): mean MS-DRG relative weight on our inpatient facility claims
    # (type of bill 0111) with service in the year, 4 decimals.
    xs = [c["rw"] for c in _pc_year(db, y) if c["tob"].startswith("011") and c["rw"] is not None]
    return [[_r4(sum(xs) / len(xs))]]


def ref_rc_top_drg(db, y):
    # Top 5 MS-DRGs by count on our inpatient facility claims (0111) with
    # service in the year; ties by DRG code.
    c = collections.Counter(x["drg"] for x in _pc_year(db, y) if x["tob"].startswith("011") and x["drg"])
    return [[k, v] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]


def ref_rc_reversal_share(db, y):
    # Share of our claims with service in the year that had a payment reversal
    # (a 'reversed' adjudication) at any point.
    xs = _pc_year(db, y)
    return [[_r4(sum(1 for c in xs if c["reversed"]) / len(xs))]]


def ref_rc_denial_rate_trend(db, y1, y2, y3):
    # Initial denial rate (count basis, as above) for each of three service years.
    return [[y, ref_rc_initial_denial_rate(db, y)[0][0]] for y in (y1, y2, y3)]


# ---------------------------------------------------------------------------
# Population health and care gaps
# ---------------------------------------------------------------------------

def _ce_members(db, y, min_months=11, products=None):
    """Members enrolled at least min_months of the 12 months of the year
    (HEDIS continuous enrollment allows one gap of up to 45 days; 11 of 12
    months approximates it). products: optional set of products that the month
    must carry."""
    c = collections.Counter()
    for (p, m), sp in _mm_year(db, y).items():
        if products is None or sp["product"] in products:
            c[p] += 1
    return {p for p, n in c.items() if n >= min_months}


def _payer_lines(db):
    def build(db):
        ok = {c["id"] for c in _payer_claims(db)}
        out = []
        for cid, pid, sd, cpt, hcpcs in db.execute(
                "select payer_claim_id, patient_id, service_date, cpt, hcpcs from payer_claim_line"):
            if cid in ok:
                out.append((pid, _d(sd), cpt or "", hcpcs or ""))
        return out
    return _cached(db, "payer_lines", build)


def ref_awv_rate(db, y):
    # Medicare Annual Wellness Visit rate: Medicare members (products
    # medicare_ffs, medicare_advantage, dual_ffs, dual_ma) enrolled 11+ months of
    # the year with an AWV (HCPCS G0438 or G0439 on a cleaned payer claim line, or
    # an EHR wellness visit recorded as a Medicare AWV) dated in the year.
    den = _ce_members(db, y, products={"medicare_ffs", "medicare_advantage", "dual_ffs", "dual_ma"})
    num = set()
    for pid, d, cpt, h in _payer_lines(db):
        if (h in ("G0438", "G0439") or cpt in ("G0438", "G0439")) and _in_year(d, y):
            num.add(pid)
    for pid, ev in db.execute("select patient_id, event_date from wellness_visit "
                              "where visit_type = 'medicare_annual_wellness' and source = 'ehr' and status = 'completed'"):
        if _in_year(_d(ev), y):
            num.add(pid)
    return [[_r4(len(den & num) / len(den))]]


_MAMMO = {"77065", "77066", "77067", "G0202", "G0204", "G0206"}


def ref_bcs_rate(db, y):
    # HEDIS Breast Cancer Screening (BCS-E): women aged 52-74 on Dec 31 of the
    # year, enrolled 11+ months of the year, with a mammogram (CPT 77065-77067,
    # HCPCS G0202/G0204/G0206 on cleaned payer claim lines, or an EHR screening
    # mammography result) from Oct 1 two years prior through Dec 31 of the year.
    # Mastectomy exclusions are not applied (not identifiable reliably here).
    y = int(y)
    lo, hi = date(y - 2, 10, 1), date(y, 12, 31)
    pats = _patients(db)
    den = {p for p in _ce_members(db, y) if pats.get(p, {}).get("sex") == "F"
           and 52 <= (_age_at(db, p, hi) or 0) <= 74}
    num = set()
    for pid, d, cpt, h in _payer_lines(db):
        if (cpt in _MAMMO or h in _MAMMO) and d and lo <= d <= hi:
            num.add(pid)
    for pid, ev in db.execute("select patient_id, event_date from cancer_screening "
                              "where test like '%mammograph%' and source = 'ehr'"):
        d = _d(ev)
        if d and lo <= d <= hi:
            num.add(pid)
    return [[_r4(len(den & num) / len(den))]]


def ref_flu_vax_65(db, y):
    # Influenza vaccination for adults 65+ for the season starting July 1 of the
    # year through June 30 of the next year: members aged 65+ on July 1 enrolled
    # at least one month in the season with any completed influenza vaccine
    # record (EHR or claim) dated in the season / those members.
    y = int(y)
    lo, hi = date(y, 7, 1), date(y + 1, 6, 30)
    mm = _member_months(db)
    months = set()
    d = lo
    while d <= hi:
        months.add(d.strftime("%Y-%m"))
        d = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
    den = {p for (p, m) in mm if m in months and (_age_at(db, p, lo) or 0) >= 65}
    num = set()
    for pid, ev in db.execute("select patient_id, event_date from immunization "
                              "where lower(vaccine) like '%influenza%' and status = 'completed'"):
        dd = _d(ev)
        if dd and lo <= dd <= hi:
            num.add(pid)
    return [[_r4(len(den & num) / len(den))]]


def _diabetics(db, y):
    y = int(y)
    return _diagnosis_patients(db, ("E10", "E11", "E13"), date(y - 1, 1, 1), date(y, 12, 31))


def ref_diabetes_patients(db, y):
    # Patients with diabetes: distinct patients with an ICD-10 E10/E11/E13 code
    # in the year or the prior year (HEDIS two-year diagnosis lookback) from
    # cleaned payer claims, EHR encounter diagnoses or EHR conditions (HEDIS
    # diabetes denominator identified by diagnosis only; pharmacy-only
    # identification not used).
    return [[len(_diabetics(db, y))]]


def _a1c_tests(db):
    """HbA1c tests: lab results (measurement concept hba1c, final/corrected,
    not 'test_not_performed') with value, plus claim-billed HbA1c tests (CPT
    83036/83037 on cleaned payer lines, as recorded in measurement with
    result_status claim_billed)."""
    def build(db):
        res = collections.defaultdict(list)
        billed = collections.defaultdict(set)
        for pid, ev, val, st, excl, cur in db.execute(
                "select patient_id, event_date, value_canonical, result_status, excluded_reason, result_current "
                "from measurement where concept = 'hba1c'"):
            d = _d(ev)
            if not d:
                continue
            if st == "claim_billed":
                billed[pid].add(d)
            elif excl is None and val is not None and cur == 1:
                res[pid].append((d, float(val)))
        for pid, d, cpt, h in _payer_lines(db):
            if cpt in ("83036", "83037"):
                billed[pid].add(d)
        return res, billed
    return _cached(db, "a1c", build)


def ref_a1c_testing_rate(db, y):
    # HbA1c testing among patients with diabetes (diabetes as above): share with
    # at least one HbA1c test in the year (lab result or claim-billed CPT
    # 83036/83037).
    dm = _diabetics(db, y)
    res, billed = _a1c_tests(db)
    hit = sum(1 for p in dm if any(_in_year(d, y) for d, v in res.get(p, ())) or any(_in_year(d, y) for d in billed.get(p, ())))
    return [[_r4(hit / len(dm))]]


def ref_a1c_poor_control(db, y):
    # HbA1c poor control (HEDIS GSD >9% logic applied to patients with a result):
    # among patients with diabetes who have at least one HbA1c lab value in the
    # year, share whose most recent value in the year is above 9.0%. (Patients
    # with no result are excluded rather than counted as poorly controlled,
    # because claims-only members never carry lab values here.) If several
    # values share the latest date, the lowest is used.
    dm = _diabetics(db, y)
    res, _ = _a1c_tests(db)
    n = hit = 0
    for p in dm:
        vs = [(d, v) for d, v in res.get(p, ()) if _in_year(d, y)]
        if not vs:
            continue
        last = max(d for d, v in vs)
        val = min(v for d, v in vs if d == last)
        n += 1
        if val > 9.0:
            hit += 1
    return [[_r4(hit / n)]]


def ref_bp_control(db, y):
    # Controlling High Blood Pressure (HEDIS CBP logic): among patients with a
    # hypertension diagnosis (ICD-10 I10) in the year or prior year who have at
    # least one blood pressure reading in the year, share whose most recent
    # reading date shows systolic <140 and diastolic <90, using the lowest
    # systolic and lowest diastolic recorded on that date. Patients without a
    # reading are excluded (claims-only members carry no vitals).
    yy = int(y)
    htn = _diagnosis_patients(db, ("I10",), date(yy - 1, 1, 1), date(yy, 12, 31))
    sbp = collections.defaultdict(dict)
    dbp = collections.defaultdict(dict)
    for pid, concept, ev, val in db.execute(
            "select patient_id, concept, event_date, value_canonical from measurement "
            "where concept in ('systolic_bp','diastolic_bp') and value_canonical is not null "
            "and substr(event_date,1,4) = ?", (str(y),)):
        d = _d(ev)
        tgt = sbp if concept == "systolic_bp" else dbp
        tgt[pid][d] = min(tgt[pid].get(d, 1e9), float(val))
    n = hit = 0
    for p in htn:
        dates = set(sbp.get(p, {})) & set(dbp.get(p, {}))
        if not dates:
            continue
        last = max(dates)
        n += 1
        if sbp[p][last] < 140 and dbp[p][last] < 90:
            hit += 1
    return [[_r4(hit / n)]]


def ref_hf_patients(db, y):
    # Heart failure population: distinct patients with an ICD-10 I50.* code dated
    # in the year (cleaned payer claims, EHR encounter diagnoses, EHR conditions).
    yy = int(y)
    return [[len(_diagnosis_patients(db, ("I50",), date(yy, 1, 1), date(yy, 12, 31)))]]


_SGLT2 = ("empagliflozin", "dapagliflozin", "canagliflozin", "ertugliflozin", "sotagliflozin")


def ref_hf_sglt2_share(db, y):
    # HF guideline therapy (AHA GWTG-HF SGLT2i concept, population version):
    # among patients with an I50.* diagnosis in the year, share with at least one
    # paid pharmacy claim for an SGLT2 inhibitor (empagliflozin, dapagliflozin,
    # canagliflozin, ertugliflozin, sotagliflozin) filled in the year.
    yy = int(y)
    hf = _diagnosis_patients(db, ("I50",), date(yy, 1, 1), date(yy, 12, 31))
    on = {r["pid"] for r in _rx_paid(db) if _in_year(r["date"], y) and any(s in r["ing"] for s in _SGLT2)}
    return [[_r4(len(hf & on) / len(hf))]]


def ref_top_practices_by_attribution(db, y):
    # Top 5 attributed practices by attributed member months in the year
    # (enrollment span attribution_practice_id; months with no attribution
    # skipped); ties by practice ID.
    c = collections.Counter(v["practice"] for v in _mm_year(db, y).values() if v["practice"])
    return [[k, v] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]


def ref_members_by_age_band(db, y):
    # Members enrolled in the year by age band on Dec 31 of the year:
    # 0-17, 18-44, 45-64, 65-74, 75+.
    hi = date(int(y), 12, 31)
    c = collections.Counter()
    for p in _enrolled_months(db, y):
        a = _age_at(db, p, hi)
        if a is None:
            band = "unknown"
        elif a < 18:
            band = "0-17"
        elif a < 45:
            band = "18-44"
        elif a < 65:
            band = "45-64"
        elif a < 75:
            band = "65-74"
        else:
            band = "75+"
        c[band] += 1
    order = ["0-17", "18-44", "45-64", "65-74", "75+", "unknown"]
    return [[b, c[b]] for b in order if c[b]]


def ref_high_svi_share(db, y):
    # Social vulnerability: share of members enrolled in the year whose county of
    # residence (patient.county FIPS) is in the top quartile of social
    # vulnerability: CDC/ATSDR SVI overall percentile >= 75 (county_svi stores the
    # percentile on a 0-100 scale). Members whose county has no SVI value are
    # excluded from the denominator.
    svi = dict(db.execute("select county_fips, svi_overall from county_svi"))
    pats = _patients(db)
    n = hit = 0
    for p in _enrolled_months(db, y):
        v = svi.get(pats.get(p, {}).get("county"))
        if v is None:
            continue
        n += 1
        if float(v) >= 75:
            hit += 1
    return [[_r4(hit / n)]]


# ---------------------------------------------------------------------------
# Pharmacy
# ---------------------------------------------------------------------------

def _rx_year(db, y):
    return [r for r in _rx_paid(db) if _in_year(r["date"], y)]


def ref_rx_fills(db, y):
    # Paid prescription fills in the year (payer pharmacy claims with status
    # paid; rejected claims, B2 reversals and recoupments excluded).
    return [[len(_rx_year(db, y))]]


def ref_rx_reject_rate(db, y):
    # Pharmacy claim rejection rate: rejected point-of-sale claims / (paid +
    # rejected) claims with fill date in the year.
    rows = db.execute("select claim_status from payer_pharmacy_claim where substr(fill_date,1,4) = ? "
                      "and claim_status in ('paid','rejected')", (str(y),)).fetchall()
    return [[_r4(sum(1 for r in rows if r[0] == "rejected") / len(rows))]]


def ref_rx_top10(db, y):
    # Top 10 drugs by paid fills in the year, grouped by plain ingredient name
    # (strengths, dose forms, salts and brand packaging collapsed); ties by name.
    c = collections.Counter(r["ing"] for r in _rx_year(db, y))
    return [[k, v] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]


def ref_rx_generic_rate(db, y):
    # Generic dispensing rate: paid fills in the year with formulary tier
    # 'generic' / paid fills with a recorded tier (generic, brand, specialty).
    xs = [r for r in _rx_year(db, y) if r["tier"]]
    return [[_r4(sum(1 for r in xs if r["tier"] == "generic") / len(xs))]]


def ref_rx_mail_share(db, y):
    # Mail-order share of paid fills in the year (pharmacy type mail order).
    xs = _rx_year(db, y)
    return [[_r4(sum(1 for r in xs if r["ptype"] == "mail_order_pharmacy") / len(xs))]]


_STATINS = ("atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin", "fluvastatin", "pitavastatin")
_RASA = ("lisinopril", "enalapril", "ramipril", "benazepril", "quinapril", "fosinopril", "perindopril", "captopril",
         "trandolapril", "moexipril", "losartan", "valsartan", "irbesartan", "olmesartan", "telmisartan",
         "candesartan", "azilsartan", "eprosartan", "sacubitril")


def _pdc_share(db, y, ingredients):
    """PQA proportion of days covered: patients with >=2 paid fills of the
    class in the year; PDC = days covered (fill date + days supply, overlapping
    supply shifted forward, capped at Dec 31) / days from first fill to Dec 31.
    Returns share with PDC >= 0.80."""
    y = int(y)
    end = date(y, 12, 31)
    by = collections.defaultdict(list)
    for r in _rx_year(db, y):
        if any(r["ing"].startswith(i) or (" " + i) in r["ing"] for i in ingredients):
            by[r["pid"]].append((r["date"], int(r["days"] or 0)))
    n = hit = 0
    for p, fills in by.items():
        if len(fills) < 2:
            continue
        fills.sort()
        start = fills[0][0]
        covered = set()
        cursor = start
        for d, ds in fills:
            cursor = max(cursor, d)
            for i in range(ds):
                day = cursor + timedelta(days=i)
                if day > end:
                    break
                covered.add(day)
            cursor = cursor + timedelta(days=ds)
        period = (end - start).days + 1
        n += 1
        if len(covered) / period >= 0.80:
            hit += 1
    return _r4(hit / n)


def ref_statin_pdc(db, y):
    # PQA Adherence to Statins (PDC >= 80%) from paid pharmacy claims; see
    # _pdc_share. Denominator: patients with 2+ statin fills in the year.
    return [[_pdc_share(db, y, _STATINS)]]


def ref_rasa_pdc(db, y):
    # PQA Adherence to RAS antagonists (ACE inhibitors, ARBs, sacubitril/
    # valsartan; PDC >= 80%) from paid pharmacy claims; see _pdc_share.
    return [[_pdc_share(db, y, _RASA)]]


_GLP1 = ("semaglutide", "liraglutide", "dulaglutide", "tirzepatide", "exenatide", "lixisenatide")


def ref_glp1_patients(db, y):
    # Distinct patients with at least one paid fill of a GLP-1 receptor agonist
    # (incl. tirzepatide) in the year.
    return [[len({r["pid"] for r in _rx_year(db, y) if any(g in r["ing"] for g in _GLP1)})]]


# ---------------------------------------------------------------------------
# Appointments and access
# ---------------------------------------------------------------------------

def _appointments(db):
    """One row per appointment from the raw appointments stream: the latest
    status record (by event date, then available date) other than the initial
    'booked' record decides the outcome; scheduled date is taken from that
    latest record (slot_start in the network practice feed); booked date =
    booked_at where the feed has it, else the event date of the first record. Records with
    status 'unassigned_requires_review' are dropped as unmatched. Outcome:
    fulfilled/arrived/attended = kept; no_show; cancelled; rescheduled
    counts with its final record; appointments never updated past 'booked' are
    unresolved."""
    def build(db):
        recs = collections.defaultdict(list)
        for p, ev, av in db.execute("select payload, event_date, available_date from record where stream = 'appointments'"):
            j = json.loads(p)
            recs[j.get("appointment_id")].append((ev or "", av or "", j))
        out = []
        for aid, rs in recs.items():
            rs.sort(key=lambda r: (r[0], r[1]))
            if any(r[2].get("status") == "unassigned_requires_review" for r in rs):
                continue
            booked = _d(rs[0][0])
            upd = [r for r in rs if r[2].get("status") != "booked"]
            last = upd[-1][2] if upd else rs[-1][2]
            st = last.get("status")
            if st in ("fulfilled", "arrived", "attended"):
                outcome = "kept"
            elif st == "no_show":
                outcome = "no_show"
            elif st == "cancelled":
                outcome = "cancelled"
            elif st == "booked":
                outcome = "unresolved"
            else:
                outcome = st
            # Two scheduling feeds: one carries scheduled_date, the network
            # practice feed carries slot_start (and booked_at).
            sched = (_d(last.get("scheduled_date")) or _d(last.get("slot_start"))
                     or _d(rs[0][2].get("scheduled_date")) or _d(rs[0][2].get("slot_start")))
            booked = _d(rs[0][2].get("booked_at")) or booked
            out.append({"id": aid, "pid": rs[0][2].get("patient_id"), "sched": sched, "booked": booked,
                        "outcome": outcome, "org": rs[0][2].get("organization_id")})
        return out
    return _cached(db, "appts", build)


def _appt_year(db, y):
    return [a for a in _appointments(db) if _in_year(a["sched"], y)]


def ref_no_show_rate(db, y):
    # No-show rate (MGMA convention): no-shows / (kept + no-show) appointments
    # scheduled in the year; cancelled, rescheduled-away and unresolved
    # appointments excluded from the denominator.
    xs = _appt_year(db, y)
    ns = sum(1 for a in xs if a["outcome"] == "no_show")
    k = sum(1 for a in xs if a["outcome"] == "kept")
    return [[_r4(ns / (ns + k))]]


def ref_no_show_rate_all(db, y):
    # Alternative reading: no-shows / all appointments scheduled in the year
    # (every outcome in the denominator).
    xs = _appt_year(db, y)
    return [[_r4(sum(1 for a in xs if a["outcome"] == "no_show") / len(xs))]]


def ref_cancel_rate(db, y):
    # Cancellation rate: cancelled / all appointments scheduled in the year.
    xs = _appt_year(db, y)
    return [[_r4(sum(1 for a in xs if a["outcome"] == "cancelled") / len(xs))]]


def ref_no_show_by_weekday(db, y):
    # No-show rate (no-shows / (kept + no-shows)) by weekday of the scheduled
    # date, appointments scheduled in the year; Monday..Sunday, weekdays with no
    # resolved appointments omitted.
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    g = collections.defaultdict(lambda: [0, 0])
    for a in _appt_year(db, y):
        if a["outcome"] in ("kept", "no_show"):
            w = a["sched"].weekday()
            g[w][1] += 1
            if a["outcome"] == "no_show":
                g[w][0] += 1
    return [[names[w], _r4(g[w][0] / g[w][1])] for w in range(7) if g[w][1]]


def ref_mean_lead_time(db, y):
    # Average appointment lead time in days: scheduled date minus the date the
    # appointment was first booked, for appointments scheduled in the year
    # (negative lead times, i.e. back-entered bookings, excluded).
    xs = [(a["sched"] - a["booked"]).days for a in _appt_year(db, y) if a["booked"] and a["sched"]]
    xs = [x for x in xs if x >= 0]
    return [[round(sum(xs) / len(xs), 1)]]


def ref_no_show_trend(db, y1, y2, y3):
    # No-show rate (MGMA convention) for each of three years.
    return [[y, ref_no_show_rate(db, y)[0][0]] for y in (y1, y2, y3)]


def ref_no_show_trend_all(db, y1, y2, y3):
    # Alternative reading of the trend with all appointments in the denominator.
    return [[y, ref_no_show_rate_all(db, y)[0][0]] for y in (y1, y2, y3)]


# ---------------------------------------------------------------------------
# Social needs
# ---------------------------------------------------------------------------

def _sn_year(db, y):
    return db.execute("select patient_id, need, positive from social_need where substr(event_date,1,4) = ?",
                      (str(y),)).fetchall()


def ref_sdoh_screened(db, y):
    # Distinct patients with at least one social-needs screening answer recorded
    # in the year (any instrument).
    return [[len({r[0] for r in _sn_year(db, y)})]]


def ref_sdoh_any_positive(db, y):
    # Share of patients screened in the year who screened positive for at least
    # one social need.
    rows = _sn_year(db, y)
    s = {r[0] for r in rows}
    pos = {r[0] for r in rows if r[2] == 1}
    return [[_r4(len(pos) / len(s))]]


def ref_sdoh_positive_by_need(db, y):
    # Positive rate by need in the year, patient level: patients positive for
    # the need at least once / patients screened for that need.
    scr = collections.defaultdict(set)
    pos = collections.defaultdict(set)
    for pid, need, p in _sn_year(db, y):
        scr[need].add(pid)
        if p == 1:
            pos[need].add(pid)
    return [[k, _r4(len(pos[k]) / len(scr[k]))] for k in sorted(scr)]


