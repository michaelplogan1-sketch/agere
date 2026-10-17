"""NCPDP Telecommunication Standard D.0 parser (pharmacy claim / reversal / rebill feeds).

Layout handled (confirmed against the native sample feed):
    00<FS>batch_id<FS>file_date<FS>version<FS>sender          file header
    G1<FS>claim_ref<GS><txn header 46 chars><RS><FS>AM04<FS>C2..<FS>C1..<RS><FS>AM01...<GS><RS><FS>AM21...<RS>...
    99<FS>record_count                                        file trailer
FS=0x1C field separator, RS=0x1E segment separator, GS=0x1D group separator; one transaction per line. The
transaction header is BIN(6) version(2) txn code(2) PCN(10) txn count(1) provider-id qualifier(2) provider id(15)
date of service(8) [vendor cert id(10)]. Each segment is "<FS>AMnn<FS>XXvalue<FS>YYvalue..." where XX is the 2-char
NCPDP field id suffix (e.g. D2 = 402-D2). A line break inside a record (line folding) is repaired and reported.

Landing tables: ncpdp_claim (one per transaction, request + response) and ncpdp_parse_issue.
The _extra column holds every raw field as JSON {"AM07.D2": "...", ...}; nothing sent is dropped.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

FS, RS, GS = "\x1c", "\x1e", "\x1d"
RECORD_TYPES = {"00", "99", "G1"}
_HEADER_RE = re.compile(r"^\d{6}D\d[A-Z]\d")

TXN_TYPES = {"B1": "claim", "B2": "reversal", "B3": "rebill", "E1": "eligibility", "N1": "information_reporting",
             "N2": "information_reporting_reversal", "N3": "information_reporting_rebill", "P1": "prior_auth_request",
             "P2": "prior_auth_reversal", "P3": "prior_auth_inquiry", "P4": "prior_auth_request_inquiry",
             "C1": "controlled_substance_report", "C2": "controlled_substance_reversal",
             "C3": "controlled_substance_rebill", "D1": "predetermination_of_benefits"}
# 112-AN transaction response status
RESPONSE_STATUS = {"P": "paid", "R": "rejected", "A": "approved", "C": "captured", "D": "duplicate",
                   "Q": "duplicate", "S": "duplicate", "F": "deferred", "N": "captured"}
PROVIDER_QUAL = {"01": "NPI", "02": "BCBS", "03": "Medicare", "04": "Medicaid", "05": "UPIN", "06": "NCPDP",
                 "07": "NCPDP", "08": "state_license", "09": "champus", "10": "HIBCC", "11": "tax_id",
                 "12": "DEA", "13": "state_issued", "14": "plan_specific", "99": "other"}
PRESCRIBER_QUAL = {"01": "NPI", "02": "BCBS", "03": "Medicare", "04": "Medicaid", "05": "UPIN", "06": "NCPDP",
                   "07": "state_license", "08": "champus", "09": "HIBCC", "10": "tax_id", "11": "DEA",
                   "12": "state_issued", "13": "plan_specific", "99": "other"}
PRODUCT_QUAL = {"00": "not_specified", "01": "UPC", "02": "HRI", "03": "NDC", "04": "HIBC", "06": "DUR",
                "07": "CPT4", "08": "CPT5", "09": "HCPCS", "11": "NAPPI", "12": "GTIN", "15": "First_DataBank",
                "28": "SNOMED", "99": "other"}

# (segment, field-suffix) -> (column, transform, NCPDP field id)
def _s(v):
    return v or None


def _date(v):
    if v and re.fullmatch(r"\d{8}", v):
        return v
    return v or None


_OVERPUNCH = {**{c: (d, 1) for c, d in zip("{ABCDEFGHI", "0123456789")},
              **{c: (d, -1) for c, d in zip("}JKLMNOPQR", "0123456789")}}


def _amount(v):
    """Plain decimal ('16.18') or NCPDP signed-overpunch implied-2-decimal ('1618{')."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        pass
    if v[-1] in _OVERPUNCH and v[:-1].isdigit():
        d, sign = _OVERPUNCH[v[-1]]
        return sign * int(v[:-1] + d) / 100.0
    return None


def _qty(v):
    """442-E7 quantity dispensed carries an implied 3 decimals ('30000' = 30.000)."""
    if v is None or v == "":
        return None
    if "." in v:
        try:
            return float(v)
        except ValueError:
            return None
    try:
        n = int(v) / 1000.0
    except ValueError:
        return None
    return int(n) if n == int(n) else n


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


FIELDS = {
    # insurance AM04
    ("AM04", "C1"): ("group_id", _s, "301-C1"), ("AM04", "C2"): ("cardholder_id", _s, "302-C2"),
    ("AM04", "C3"): ("person_code", _s, "303-C3"), ("AM04", "C6"): ("patient_relationship_code", _s, "306-C6"),
    ("AM04", "C9"): ("eligibility_clarification_code", _s, "309-C9"),
    # patient AM01
    ("AM01", "C4"): ("patient_dob_raw", _s, "304-C4"), ("AM01", "C5"): ("patient_gender_code", _s, "305-C5"),
    ("AM01", "CA"): ("patient_first_name", _s, "310-CA"), ("AM01", "CB"): ("patient_last_name", _s, "311-CB"),
    ("AM01", "CP"): ("patient_zip", _s, "325-CP"), ("AM01", "C3"): ("person_code", _s, "303-C3"),
    # claim AM07
    ("AM07", "EM"): ("rx_service_ref_qualifier", _s, "455-EM"), ("AM07", "D2"): ("rx_service_ref_number", _s, "402-D2"),
    ("AM07", "E1"): ("product_service_id_qualifier", _s, "436-E1"), ("AM07", "D7"): ("product_service_id", _s, "407-D7"),
    ("AM07", "E7"): ("quantity_dispensed", _qty, "442-E7"), ("AM07", "D3"): ("fill_number", _int, "403-D3"),
    ("AM07", "D5"): ("days_supply", _int, "405-D5"), ("AM07", "D8"): ("daw_code", _s, "408-D8"),
    ("AM07", "D6"): ("compound_code", _s, "406-D6"), ("AM07", "C8"): ("other_coverage_code", _s, "308-C8"),
    ("AM07", "DE"): ("date_prescription_written", _date, "414-DE"),
    ("AM07", "DF"): ("refills_authorized", _int, "415-DF"), ("AM07", "ET"): ("quantity_prescribed", _qty, "460-ET"),
    ("AM07", "DK"): ("submission_clarification_code", _s, "420-DK"),
    # prescriber AM03
    ("AM03", "EZ"): ("prescriber_id_qualifier", _s, "466-EZ"), ("AM03", "DB"): ("prescriber_id", _s, "411-DB"),
    ("AM03", "DR"): ("prescriber_last_name", _s, "427-DR"),
    # pricing AM11
    ("AM11", "D9"): ("ingredient_cost_submitted", _amount, "409-D9"),
    ("AM11", "DC"): ("dispensing_fee_submitted", _amount, "412-DC"),
    ("AM11", "DU"): ("gross_amount_due", _amount, "430-DU"),
    ("AM11", "DQ"): ("usual_customary_charge", _amount, "426-DQ"),
    ("AM11", "DX"): ("patient_paid_amount_submitted", _amount, "433-DX"),
    # response status AM21
    ("AM21", "AN"): ("response_status_code", _s, "112-AN"), ("AM21", "FA"): ("reject_count_raw", _s, "510-FA"),
    ("AM21", "F3"): ("authorization_number", _s, "503-F3"),
    # response pricing AM23
    ("AM23", "F5"): ("patient_pay_amount", _amount, "505-F5"), ("AM23", "F9"): ("total_amount_paid", _amount, "509-F9"),
    ("AM23", "F6"): ("ingredient_cost_paid", _amount, "506-F6"), ("AM23", "F7"): ("dispensing_fee_paid", _amount, "507-F7"),
}
# repeated fields collected as lists (reject codes live in a loop)
LIST_FIELDS = {("AM21", "FB"): ("reject_codes", "511-FB")}
FIELD_IDS = {f"{seg}.{fid}": nfo[-1] for (seg, fid), nfo in {**FIELDS, **LIST_FIELDS}.items()}


def detect(path: Path) -> bool:
    path = Path(path)
    if any(p.lower() == "ncpdp" for p in path.parts[:-1]):
        return True
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    if head[:3] == b"00" + FS.encode():
        return True
    return bool(re.search(rb"\x1e\x1cAM0[1-9]\x1c", head)) or bool(re.match(rb"G1\x1c", head))


def _iso(d):
    if d and re.fullmatch(r"\d{8}", d):
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return None


def _issue(file, key, line, sev, code, detail):
    return {"claim_key": key, "file": file, "line_no": line, "severity": sev, "issue_code": code, "detail": detail}


def _logical_lines(text):
    """Join physical lines broken inside a record (line folding). -> list of (line_no, text, folded?)"""
    out = []
    for i, ln in enumerate(re.split(r"\r\n|\n|\r", text), 1):
        if ln == "":
            continue
        head = ln[:3]
        is_start = (len(ln) > 2 and ln[2] == FS and ln[:2] in RECORD_TYPES) or bool(_HEADER_RE.match(ln))
        if is_start or not out:
            out.append([i, ln, 0])
        else:
            out[-1][1] += ln
            out[-1][2] += 1
    return out


def parse(path: Path, context: dict) -> Iterator[tuple[str, dict]]:
    path = Path(path)
    context = context or {}
    fname = context.get("relpath") or path.name
    try:
        raw = path.read_bytes()
    except OSError as e:
        yield "ncpdp_parse_issue", _issue(fname, None, None, "error", "unreadable_file", str(e))
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        yield "ncpdp_parse_issue", _issue(fname, None, None, "warning", "encoding_fallback", "decoded as latin-1")

    hdr = {"file_batch_id": None, "file_date": None, "file_version": None, "file_sender": None}
    trailer, n_claims, ordinal = None, 0, 0
    claims = []  # buffered so every row can carry the trailer record count
    for line_no, ln, folded in _logical_lines(text):
        rt = ln[:2]
        if rt == "00" and len(ln) > 2 and ln[2] == FS:
            p = ln.split(FS)
            hdr = {"file_batch_id": p[1] if len(p) > 1 else None, "file_date": p[2] if len(p) > 2 else None,
                   "file_version": p[3] if len(p) > 3 else None, "file_sender": p[4] if len(p) > 4 else None}
            continue
        if rt == "99" and len(ln) > 2 and ln[2] == FS:
            trailer = _int(ln.split(FS)[1]) if len(ln.split(FS)) > 1 else None
            continue
        ordinal += 1
        key = f"{fname}:{ordinal}"
        if folded:
            yield "ncpdp_parse_issue", _issue(fname, key, line_no, "warning", "line_folding_repaired",
                                              f"{folded} stray line break(s) inside record joined")
        row, issues = _parse_record(fname, key, ordinal, line_no, ln, hdr)
        for code, detail, sev in issues:
            yield "ncpdp_parse_issue", _issue(fname, key, line_no, sev, code, detail)
        if row is not None:
            n_claims += 1
            claims.append(row)
    for row in claims:
        row["file_trailer_count"] = trailer
        yield "ncpdp_claim", row
    if trailer is None and n_claims:
        yield "ncpdp_parse_issue", _issue(fname, None, None, "warning", "missing_trailer", "no 99 trailer record")
    elif trailer is not None and trailer != n_claims:
        yield "ncpdp_parse_issue", _issue(fname, None, None, "warning", "trailer_count_mismatch",
                                          f"trailer says {trailer}, parsed {n_claims}")
    if n_claims == 0:
        yield "ncpdp_parse_issue", _issue(fname, None, None, "error", "no_transactions", "no transaction records")


def _parse_record(fname, key, ordinal, line_no, ln, hdr):
    issues = []
    record_type = claim_ref = None
    body = ln
    if len(ln) > 2 and ln[2] == FS and ln[:2] in RECORD_TYPES:
        record_type = ln[:2]
        first, sep, rest = ln.partition(GS)
        claim_ref = first.split(FS)[1] if len(first.split(FS)) > 1 else None
        if not sep:
            issues.append(("missing_group_separator", "no group separator after record prefix", "error"))
            return None, issues
        body = rest
    pieces = [p for p in re.split(f"[{RS}{GS}]", body)]
    header, segs = pieces[0], [p for p in pieces[1:] if p]
    m = re.match(r"^(\d{6})(D\d)([A-Z]\d)(.{10})(\d)(\d{2})(.{15})(\d{8})(.*)$", header, re.S)
    row = {"claim_key": key, "file": fname, "file_record_ordinal": ordinal, "line_no": line_no,
           "record_type": record_type, "claim_ref": claim_ref, **hdr}
    if not m:
        issues.append(("bad_transaction_header", f"unparseable header {header[:60]!r}", "error"))
        row["_extra"] = json.dumps({"header": header}, ensure_ascii=False)
        return None, issues
    (bin_, ver, txn, pcn, cnt, pq, pid, dos, cert) = m.groups()
    row.update({
        "bin_number": bin_, "version_release": ver, "transaction_code": txn,
        "transaction_type": TXN_TYPES.get(txn, "other"), "pcn": pcn.strip() or None, "transaction_count": int(cnt),
        "service_provider_id_qualifier": pq, "service_provider_id_type": PROVIDER_QUAL.get(pq),
        "service_provider_id": pid.strip() or None, "date_of_service": _iso(dos), "date_of_service_raw": dos,
        "software_vendor_cert_id": cert.strip() or None,
    })
    extra = {"header": header}
    lists = {}
    seen = set()
    for seg in segs:
        parts = seg.split(FS)
        if len(parts) < 2 or not parts[1]:
            issues.append(("bad_segment", f"segment without id: {seg[:40]!r}", "warning"))
            continue
        sid = parts[1]
        if not re.fullmatch(r"AM\d{2}", sid):
            issues.append(("unknown_segment_id", f"{sid!r}", "warning"))
        seen.add(sid)
        for fld in parts[2:]:
            if len(fld) < 2:
                if fld:
                    issues.append(("short_field", f"{sid}: {fld!r}", "warning"))
                continue
            fid, val = fld[:2], fld[2:]
            ek = f"{sid}.{fid}"
            if ek in extra:  # repeated field (loops)
                prev = extra[ek]
                extra[ek] = (prev if isinstance(prev, list) else [prev]) + [val]
            else:
                extra[ek] = val
            if (sid, fid) in LIST_FIELDS:
                lists.setdefault(LIST_FIELDS[(sid, fid)][0], []).append(val)
            elif (sid, fid) in FIELDS:
                col, fn, _ = FIELDS[(sid, fid)]
                if col not in row or row[col] is None:
                    row[col] = fn(val)
                    if fn in (_amount, _qty) and val and row[col] is None:
                        issues.append(("bad_number", f"{ek}={val!r}", "warning"))
    for col in {c for c, _, _ in FIELDS.values()}:
        row.setdefault(col, None)
    # derived
    dob = row.get("patient_dob_raw")
    row["patient_dob"] = _iso(dob)
    g = row.get("patient_gender_code")
    row["patient_gender"] = {"1": "M", "2": "F", "0": None}.get(g, None) if g else None
    row["date_prescription_written"] = _iso(row.get("date_prescription_written")) if row.get(
        "date_prescription_written") else None
    row["product_service_id_type"] = PRODUCT_QUAL.get(row.get("product_service_id_qualifier") or "")
    row["prescriber_id_type"] = PRESCRIBER_QUAL.get(row.get("prescriber_id_qualifier") or "")
    codes = lists.get("reject_codes", [])
    row["reject_codes"] = ",".join(codes) if codes else None
    row["reject_count"] = len(codes) if codes else (_int(row.get("reject_count_raw")) or 0)
    status = row.get("response_status_code")
    row["response_status"] = RESPONSE_STATUS.get(status) if status else None
    row["plan_paid_amount"] = row.get("total_amount_paid")
    row["is_compound"] = 1 if (row.get("compound_code") == "2" or "AM10" in seen) else 0
    row["has_response"] = 1 if status else 0
    row.pop("reject_count_raw", None)
    row["_extra"] = json.dumps(extra, ensure_ascii=False, sort_keys=True)
    if not status and txn in ("B1", "B2", "B3"):
        issues.append(("missing_response", "no AM21 response status segment (truncated record?)", "warning"))
    if status == "R" and not codes:
        issues.append(("rejected_without_codes", "response status R but no 511-FB reject code", "warning"))
    if txn in ("B1", "B3") and not row.get("product_service_id"):
        issues.append(("missing_product_id", "no 407-D7 product/service id", "warning"))
    return row, issues
