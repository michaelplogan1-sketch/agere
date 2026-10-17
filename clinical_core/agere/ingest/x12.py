"""X12 5010 parser (837P/837I claims, 835 remittance, 834 eligibility) for Agere native feed ingestion.

Contract (see ingest/__init__.py): detect(path) and parse(path, context) -> Iterator[(landing_table, row)].

Landing tables emitted
    x12_interchange, x12_837_claim, x12_837_dx, x12_837_line,
    x12_835_payment, x12_835_claim, x12_835_adjustment, x12_835_line,
    x12_834_member, x12_parse_issue

Conventions: codes stay as sent (whitespace trimmed, empty -> None); dates YYYYMMDD -> YYYY-MM-DD;
amounts -> float. Every row carries _segment_index (0-based ordinal, within the file, of the row's first
segment) and isa_control_number / gs_control_number / st_control_number. Claims (837/835) get
claim_key = f"{isa13}:{st02}:{ordinal}" (ordinal is 1-based within the transaction) and dx/line/adjustment
rows carry the same claim_key; 834 members get member_key built the same way. Segments the parser does not
map into a column are kept in the row's `_extra` JSON column so nothing the sender wrote is lost.

The file is streamed in chunks (separators are read from every ISA) and malformed input never raises:
problems become x12_parse_issue rows.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator, Optional

_WS = " \t\r\n\x00\x1a"
_CHUNK = 1 << 20

TRANSACTION_837 = {"X222": "837P", "X223": "837I", "X224": "837D"}
MAINT_LABELS = {"001": "change", "021": "add", "024": "cancel_term", "025": "reinstatement", "026": "correction",
                "030": "audit", "032": "employee_info_change"}
DX_QUALIFIERS = {"ABK", "ABF", "ABJ", "APR", "BK", "BF", "ABN", "BJ", "PR", "BN"}
PRINCIPAL_QUALIFIERS = {"ABK", "BK"}


# --------------------------------------------------------------------------------------------- detection
def detect(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return False
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    return head.lstrip().startswith(b"ISA")


def parse(path: Path, context: dict) -> Iterator[tuple[str, dict]]:
    path = Path(path)
    with open(path, "r", encoding="latin-1", newline="") as fh:
        yield from parse_stream(_reader(fh), path.name)


def parse_text(text: str, name: str = "") -> Iterator[tuple[str, dict]]:
    """Parse an in-memory X12 string (used by tests)."""
    return parse_stream(iter([text]), name)


def _reader(fh):
    while True:
        chunk = fh.read(_CHUNK)
        if not chunk:
            return
        yield chunk


# --------------------------------------------------------------------------------------------- tokenizer
def iter_segments(chunks) -> Iterator[tuple[str, str, str]]:
    """Yield (segment_text, element_sep, component_sep). Separators are re-read at every ISA."""
    chunks = iter(chunks)
    buf, pos, eof = "", 0, False
    el, comp, term = "*", ":", "~"
    first = True

    def refill() -> bool:
        nonlocal buf, pos, eof
        if eof:
            return False
        try:
            nxt = next(chunks)
        except StopIteration:
            eof = True
            return False
        buf = buf[pos:] + nxt
        pos = 0
        return True

    while True:
        # skip inter-segment whitespace (newlines after the terminator)
        while True:
            while pos < len(buf) and buf[pos] in _WS:
                pos += 1
            if pos < len(buf) or not refill():
                break
        if pos >= len(buf):
            return
        if first:
            first = False
            if buf[pos] == "﻿":
                pos += 1
                continue
        if buf.startswith("ISA", pos) or (len(buf) - pos < 3 and not eof):
            while len(buf) - pos < 140 and refill():
                pass
            if buf.startswith("ISA", pos) and len(buf) - pos > 4:
                # Read the separators from the ISA itself without trusting the fixed 106-byte width
                # (some senders pad ids wrongly): ISA16 is the character after the 16th element separator.
                sep = buf[pos + 3]
                j, n = pos + 3, 0
                while n < 16 and j < len(buf) and j - pos < 140:
                    if buf[j] == sep:
                        n += 1
                    j += 1
                if n == 16 and j + 1 < len(buf):
                    el, comp, term = sep, buf[j], buf[j + 1]
                    seg = buf[pos:j + 1]
                    pos = j + 2
                    yield seg, el, comp
                    continue
        idx = buf.find(term, pos)
        while idx < 0 and refill():
            idx = buf.find(term, pos)
        if idx < 0:
            seg, pos = buf[pos:], len(buf)
        else:
            seg, pos = buf[pos:idx], idx + 1
        seg = seg.strip(_WS)
        if seg:
            yield seg, el, comp


# --------------------------------------------------------------------------------------------- helpers
def _s(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _g(e: list, i: int) -> Optional[str]:
    return _s(e[i]) if i < len(e) else None


def _date(v: Optional[str]) -> Optional[str]:
    v = _s(v)
    if not v:
        return None
    if re.fullmatch(r"\d{8}", v):
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    if re.fullmatch(r"\d{12}|\d{14}", v):
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}"
    if re.fullmatch(r"\d{6}", v):
        yy = int(v[:2])
        return f"{2000 + yy if yy < 70 else 1900 + yy}-{v[2:4]}-{v[4:]}"
    return v


def _date_range(v: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    v = _s(v)
    if not v:
        return None, None
    if "-" in v:
        a, _, b = v.partition("-")
        return _date(a), _date(b)
    return _date(v), None


def _time(v: Optional[str]) -> Optional[str]:
    v = _s(v)
    if v and re.fullmatch(r"\d{4}(\d{2,4})?", v):
        return f"{v[:2]}:{v[2:4]}" + (f":{v[4:6]}" if len(v) >= 6 else "")
    return v


def _jd(obj) -> Optional[str]:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True) if obj else None


class _Fatal(Exception):
    pass


# --------------------------------------------------------------------------------------------- reader
class _Reader:
    def __init__(self, source_name: str = ""):
        self.name = source_name or ""
        self.idx = -1
        self.out: list[tuple[str, dict]] = []
        self.isa: Optional[dict] = None
        self.gs: Optional[dict] = None
        self.tx: Optional[dict] = None
        self.seg_text = ""
        self.unsupported_logged = False
        self.el, self.comp = "*", ":"

    # ---- plumbing
    def emit(self, table: str, row: dict):
        self.out.append((table, row))

    def issue(self, reason: str, seg: Optional[str] = None, idx: Optional[int] = None):
        seg = self.seg_text if seg is None else seg
        warn = reason.startswith(("se_segment_count_mismatch", "iea_control_number_mismatch",
                                  "se_control_number_mismatch"))
        row = {
            "severity": "warning" if warn else "error",
            "_segment_index": self.idx if idx is None else idx,
            "reason": reason,
            "segment_id": seg.split(self.el, 1)[0][:8] if seg else None,
            "segment": seg[:300] if seg else None,
        }
        row.update(self.ids())
        self.emit("x12_parse_issue", row)

    def ids(self) -> dict:
        return {
            "isa_control_number": self.isa["control_number"] if self.isa else None,
            "gs_control_number": self.gs["control_number"] if self.gs else None,
            "st_control_number": self.tx["st02"] if self.tx else None,
        }

    def base(self, idx: int) -> dict:
        d = {"_segment_index": idx}
        d.update(self.ids())
        d["transaction_type"] = self.tx["type"] if self.tx else None
        return d

    def amt(self, v: Optional[str]) -> Optional[float]:
        v = _s(v)
        if v is None:
            return None
        try:
            return float(v)
        except ValueError:
            self.issue(f"bad_amount:{v[:20]}")
            return None

    def claim_key(self, ordinal: int) -> str:
        return f"{self.isa['control_number']}:{self.tx['st02']}:{ordinal}"

    # ---- main entry
    def feed(self, seg: str, el: str, comp: str):
        self.idx += 1
        self.seg_text = seg
        self.el, self.comp = el, comp
        e = seg.split(el)
        sid = e[0].strip()
        try:
            if sid == "ISA":
                self.on_isa(e)
            elif sid == "IEA":
                self.close_isa(e)
            elif sid == "GS":
                self.on_gs(e)
            elif sid == "GE":
                self.close_gs()
            elif sid == "ST":
                self.on_st(e)
            elif sid == "SE":
                self.on_se(e)
            elif self.tx is None:
                self.issue("segment_outside_transaction")
            else:
                self.tx["seg_count"] += 1
                h = self.tx.get("handler")
                if h:
                    h(sid, e)
        except Exception as ex:  # never crash on malformed input
            self.issue(f"handler_error:{type(ex).__name__}:{str(ex)[:80]}")

    def finish(self):
        if self.tx is not None:
            self.issue("missing_se_at_eof", idx=self.idx)
            self.close_tx()
        if self.gs is not None:
            self.close_gs()
        if self.isa is not None:
            self.issue("missing_iea_at_eof", idx=self.idx)
            self.close_isa(None)

    # ---- envelopes
    def on_isa(self, e):
        if self.tx is not None:
            self.issue("missing_se")
            self.close_tx()
        if self.gs is not None:
            self.issue("missing_ge")
            self.close_gs()
        if self.isa is not None:
            self.issue("missing_iea")
            self.close_isa(None)
        if len(e) < 17:
            self.issue("short_isa")
            self.isa = None
            return
        self.isa = {
            "idx": self.idx, "auth_qual": _g(e, 1), "auth_info": _g(e, 2), "sec_qual": _g(e, 3), "sec_info": _g(e, 4),
            "sender_qualifier": _g(e, 5), "sender_id": _g(e, 6), "receiver_qualifier": _g(e, 7),
            "receiver_id": _g(e, 8), "date": _date(_g(e, 9)), "time": _time(_g(e, 10)),
            "repetition_separator": _g(e, 11), "version": _g(e, 12), "control_number": _g(e, 13),
            "ack_requested": _g(e, 14), "usage_indicator": _g(e, 15), "component_separator": _g(e, 16),
            "group_count": 0, "tx_count": 0, "gs_ids": [], "gs_versions": [], "tx_types": [],
        }

    def close_isa(self, e):
        if self.tx is not None:
            self.issue("missing_se")
            self.close_tx()
        if self.gs is not None:
            self.issue("missing_ge")
            self.close_gs()
        i = self.isa
        if i is None:
            if e is not None:
                self.issue("iea_without_isa")
            return
        if e is not None and _g(e, 2) != i["control_number"]:
            self.issue("iea_control_number_mismatch")
        row = {
            "_segment_index": i["idx"], "isa_control_number": i["control_number"],
            "sender_qualifier": i["sender_qualifier"], "sender_id": i["sender_id"],
            "receiver_qualifier": i["receiver_qualifier"], "receiver_id": i["receiver_id"],
            "interchange_date": i["date"], "interchange_time": i["time"],
            "interchange_version": i["version"], "ack_requested": i["ack_requested"],
            "usage_indicator": i["usage_indicator"], "authorization_qualifier": i["auth_qual"],
            "authorization_info": i["auth_info"], "security_qualifier": i["sec_qual"],
            "security_info": i["sec_info"], "repetition_separator": i["repetition_separator"],
            "component_separator": i["component_separator"],
            "gs_functional_id": ",".join(dict.fromkeys(x for x in i["gs_ids"] if x)) or None,
            "gs_version": ",".join(dict.fromkeys(x for x in i["gs_versions"] if x)) or None,
            "group_count": i["group_count"], "transaction_count": i["tx_count"],
            "transaction_types": ",".join(dict.fromkeys(i["tx_types"])) or None,
            "iea_group_count": _g(e, 1) if e else None,
        }
        self.emit("x12_interchange", row)
        self.isa = None

    def on_gs(self, e):
        if self.tx is not None:
            self.issue("missing_se")
            self.close_tx()
        if self.gs is not None:
            self.issue("missing_ge")
            self.close_gs()
        self.gs = {"idx": self.idx, "functional_id": _g(e, 1), "sender": _g(e, 2), "receiver": _g(e, 3),
                   "date": _date(_g(e, 4)), "time": _time(_g(e, 5)), "control_number": _g(e, 6),
                   "agency": _g(e, 7), "version": _g(e, 8)}
        if self.isa is not None:
            self.isa["group_count"] += 1
            self.isa["gs_ids"].append(self.gs["functional_id"])
            self.isa["gs_versions"].append(self.gs["version"])
        else:
            self.issue("gs_outside_interchange")

    def close_gs(self):
        if self.tx is not None:
            self.issue("missing_se")
            self.close_tx()
        self.gs = None

    def on_st(self, e):
        if self.tx is not None:
            self.issue("missing_se")
            self.close_tx()
        if self.isa is None:
            self.issue("st_outside_interchange")
            self.isa = {"idx": self.idx, "auth_qual": None, "auth_info": None, "sec_qual": None, "sec_info": None,
                        "sender_qualifier": None, "sender_id": None, "receiver_qualifier": None,
                        "receiver_id": None, "date": None, "time": None, "repetition_separator": None,
                        "version": None, "control_number": None, "ack_requested": None, "usage_indicator": None,
                        "component_separator": None, "group_count": 0, "tx_count": 0, "gs_ids": [],
                        "gs_versions": [], "tx_types": []}
        impl = _g(e, 3) or (self.gs["version"] if self.gs else None)
        self.tx = {"idx": self.idx, "type": _g(e, 1), "st02": _g(e, 2), "impl": impl, "seg_count": 1,
                   "ordinal": 0, "handler": None, "issues_before": 0}
        t = self.tx["type"]
        self.isa["tx_count"] += 1
        if t == "837":
            code = next((v for k, v in TRANSACTION_837.items() if impl and k in impl), None)
            self.tx["type"] = code or "837"
            self.tx["tx_type"] = code
            self.tx["handler"] = self.h837
            self.init_837()
        elif t == "835":
            self.tx["handler"] = self.h835
            self.init_835()
        elif t == "834":
            self.tx["handler"] = self.h834
            self.init_834()
        else:
            self.issue(f"unsupported_transaction_set:{t}")
        self.isa["tx_types"].append(self.tx["type"] or "")

    def on_se(self, e):
        if self.tx is None:
            self.issue("se_without_st")
            return
        self.tx["seg_count"] += 1
        n = _g(e, 1)
        if n is not None and n.isdigit() and int(n) != self.tx["seg_count"]:
            self.issue(f"se_segment_count_mismatch:declared={n}:actual={self.tx['seg_count']}")
        if _g(e, 2) != self.tx["st02"]:
            self.issue("se_control_number_mismatch")
        self.close_tx()

    def close_tx(self):
        t = self.tx
        if t is None:
            return
        try:
            if t["type"] in ("837P", "837I", "837D", "837"):
                self.close_837_claim()
            elif t["type"] == "835":
                self.close_835_claim()
                self.close_835_payment()
            elif t["type"] == "834":
                self.close_834_member()
        except Exception as ex:
            self.issue(f"close_error:{type(ex).__name__}:{str(ex)[:80]}")
        self.tx = None

    # =========================================================================================== 837
    def init_837(self):
        t = self.tx
        t.update(bill={}, sub={}, pat={}, claim=None, line=None, level=None, other_sub=False,
                 header_extra=[])

    def h837(self, sid, e):
        t = self.tx
        if sid == "HL":
            self.close_837_claim()
            level = _g(e, 3)
            t["level"] = level
            if level == "20":
                t["bill"], t["sub"], t["pat"] = {"hl_id": _g(e, 1), "extra": []}, {}, {}
            elif level == "22":
                t["sub"], t["pat"] = {"hl_id": _g(e, 1), "extra": []}, {}
            elif level == "23":
                t["pat"] = {"hl_id": _g(e, 1), "extra": []}
            return
        c = t["claim"]
        if sid == "CLM":
            self.close_837_claim()
            self.open_837_claim(e)
            return
        if c is None:
            self.pre_claim_837(sid, e)
            return
        if sid == "LX":
            self.new_837_line(e)
            return
        ln = t["line"]
        if sid in ("SV1", "SV2", "SV3"):
            if ln is None:
                self.new_837_line(None)
                ln = t["line"]
            self.on_sv(sid, e, ln)
        elif sid == "SBR" and ln is None:
            t["other_sub"] = True
            c["other_subscriber_count"] += 1
        elif ln is not None and sid in ("DTP", "REF", "NM1", "LIN", "CTP", "AMT", "NTE", "PWK", "SVD", "CAS", "LQ"):
            self.line_seg_837(sid, e, ln)
        elif sid == "HI":
            self.on_hi(e, c)
        elif sid == "DTP":
            q = _g(e, 1)
            f, to = _date_range(_g(e, 3))
            c["dtp"].setdefault(q, f if to is None else f"{f}/{to}")
            if q == "434":
                c["statement_from"], c["statement_to"] = f, to or f
            elif q == "435":
                c["admission_date"] = f
            elif q == "096":
                c["discharge_date"] = f
        elif sid == "REF":
            q = _g(e, 1)
            v = _g(e, 2)
            if q == "F8":
                c["original_reference"] = v
            elif q == "EA":
                c["medical_record_number"] = v
            elif q == "D9":
                c["claim_id"] = v
            elif q == "G1":
                c["prior_authorization"] = v
            else:
                c["refs"].setdefault(q, v)
        elif sid == "CL1":
            c["admission_type"], c["admission_source"], c["patient_status"] = _g(e, 1), _g(e, 2), _g(e, 3)
            c["cl1_nursing_home_residential_status"] = _g(e, 4)
        elif sid == "NM1" and not t["other_sub"]:
            q = _g(e, 1)
            col = {"82": "rendering", "71": "attending", "72": "operating", "73": "other_operating",
                   "DN": "referring", "77": "service_facility", "DQ": "supervising"}.get(q)
            if col:
                c[f"{col}_npi"] = _g(e, 9) if _g(e, 8) in ("XX", None) else None
                c[f"{col}_name"] = self.nm_name(e)
                if _g(e, 8) not in ("XX", None):
                    c[f"{col}_id"] = _g(e, 9)
            else:
                c["extra"].append(self.seg_text)
        elif sid == "PAT":
            c["extra"].append(self.seg_text)
        else:
            c["extra"].append(self.seg_text)

    @staticmethod
    def nm_name(e) -> Optional[str]:
        last, first = _g(e, 3), _g(e, 4)
        if last and first:
            return f"{last}, {first}"
        return last or first

    def pre_claim_837(self, sid, e):
        t = self.tx
        lvl = t["level"]
        if lvl is None:
            t["header_extra"].append(self.seg_text)
            return
        bill, sub, pat = t["bill"], t["sub"], t["pat"]
        if lvl == "20":
            if sid == "NM1":
                q = _g(e, 1)
                if q == "85":
                    bill.update(npi=_g(e, 9) if _g(e, 8) in ("XX", None) else None, name=_g(e, 3),
                                first=_g(e, 4), entity_type=_g(e, 2), id_qual=_g(e, 8), id=_g(e, 9))
                elif q == "87":
                    bill.update(pay_to_name=_g(e, 3), pay_to_npi=_g(e, 9), pay_to_id=_g(e, 9))
                else:
                    bill["extra"].append(self.seg_text)
            elif sid == "REF":
                q = _g(e, 1)
                if q == "EI":
                    bill["tin"] = _g(e, 2)
                elif q == "SY" and not bill.get("tin"):
                    bill["tin"] = _g(e, 2)
                else:
                    bill["extra"].append(self.seg_text)
            else:
                bill.setdefault("extra", []).append(self.seg_text)
            return
        if lvl == "22":
            if sid == "SBR":
                sub.update(payer_responsibility=_g(e, 1), relationship=_g(e, 2), group_number=_g(e, 3),
                           group_name=_g(e, 4), insurance_type=_g(e, 5), filing_indicator=_g(e, 9))
            elif sid == "NM1":
                q = _g(e, 1)
                if q == "IL":
                    sub.update(last=_g(e, 3), first=_g(e, 4), middle=_g(e, 5), id_qual=_g(e, 8),
                               member_id=_g(e, 9))
                elif q == "PR":
                    sub.update(payer_name=_g(e, 3), payer_id_qual=_g(e, 8), payer_id=_g(e, 9))
                else:
                    sub["extra"].append(self.seg_text)
            elif sid == "DMG":
                sub.update(dob=_date(_g(e, 2)), gender=_g(e, 3))
            else:
                sub["extra"].append(self.seg_text)
            return
        if lvl == "23":
            if sid == "PAT":
                pat["relationship"] = _g(e, 1)
            elif sid == "NM1" and _g(e, 1) == "QC":
                pat.update(last=_g(e, 3), first=_g(e, 4), middle=_g(e, 5), id_qual=_g(e, 8), id=_g(e, 9))
            elif sid == "DMG":
                pat.update(dob=_date(_g(e, 2)), gender=_g(e, 3))
            else:
                pat["extra"].append(self.seg_text)

    def open_837_claim(self, e):
        t = self.tx
        t["ordinal"] += 1
        bill, sub, pat = t["bill"], t["sub"], t["pat"]
        if len(e) < 3 or not _g(e, 2):
            self.issue("clm_missing_total_charge")
        f5 = (_g(e, 5) or "").split(self.comp)
        f5 += [None] * (3 - len(f5))
        c = self.base(self.idx)
        c.update({
            "claim_key": self.claim_key(t["ordinal"]), "claim_ordinal": t["ordinal"],
            "hl_billing": bill.get("hl_id"), "hl_subscriber": sub.get("hl_id"), "hl_patient": pat.get("hl_id"),
            "billing_provider_npi": bill.get("npi"), "billing_provider_tin": bill.get("tin"),
            "billing_provider_name": bill.get("name"), "billing_provider_id_qualifier": bill.get("id_qual"),
            "pay_to_name": bill.get("pay_to_name"), "pay_to_npi": bill.get("pay_to_npi"),
            "subscriber_member_id": sub.get("member_id"), "subscriber_id_qualifier": sub.get("id_qual"),
            "subscriber_last_name": sub.get("last"), "subscriber_first_name": sub.get("first"),
            "subscriber_middle_name": sub.get("middle"), "subscriber_dob": sub.get("dob"),
            "subscriber_gender": sub.get("gender"), "subscriber_relationship": sub.get("relationship"),
            "payer_responsibility": sub.get("payer_responsibility"), "group_number": sub.get("group_number"),
            "insurance_type": sub.get("insurance_type"), "claim_filing_indicator": sub.get("filing_indicator"),
            "patient_present": 1 if pat else 0, "patient_member_id": pat.get("id"),
            "patient_last_name": pat.get("last"), "patient_first_name": pat.get("first"),
            "patient_dob": pat.get("dob"), "patient_gender": pat.get("gender"),
            "patient_relationship": pat.get("relationship"),
            "payer_id": sub.get("payer_id"), "payer_name": sub.get("payer_name"),
            "payer_id_qualifier": sub.get("payer_id_qual"),
            "patient_control_number": _g(e, 1), "total_charge": self.amt(_g(e, 2)),
            "facility_code": f5[0] or None, "place_of_service": None, "facility_code_qualifier": f5[1] or None,
            "frequency_code": f5[2] or None, "claim_status_label": None,
            "provider_signature_indicator": _g(e, 6), "provider_accept_assignment": _g(e, 7),
            "benefits_assignment": _g(e, 8), "release_of_information": _g(e, 9),
            "original_reference": None, "medical_record_number": None, "claim_id": None,
            "prior_authorization": None, "statement_from": None, "statement_to": None, "admission_date": None,
            "discharge_date": None, "admission_type": None, "admission_source": None, "patient_status": None,
            "diagnosis_count": 0, "principal_diagnosis": None, "other_diagnosis_count": 0, "drg": None,
            "rendering_npi": None, "rendering_name": None, "attending_npi": None, "attending_name": None,
            "operating_npi": None, "referring_npi": None, "referring_name": None, "service_facility_npi": None,
            "service_facility_name": None, "line_count": 0, "other_subscriber_count": 0,
            "interchange_sender_id": self.isa["sender_id"], "interchange_receiver_id": self.isa["receiver_id"],
            "interchange_date": self.isa["date"], "implementation_version": t["impl"],
        })
        c["_dx"], c["_lines"], c["dtp"], c["refs"], c["extra"] = [], [], {}, {}, []
        t["claim"], t["line"], t["other_sub"] = c, None, False

    def on_hi(self, e, c):
        for i in range(1, len(e)):
            comps = e[i].split(self.comp)
            q = _s(comps[0])
            code = _s(comps[1]) if len(comps) > 1 else None
            if not q:
                continue
            if q == "DR":
                c["drg"] = code
                continue
            if q in DX_QUALIFIERS and code:
                seq = len(c["_dx"]) + 1
                poa = _s(comps[8]) if len(comps) > 8 else None
                c["_dx"].append({"dx_sequence": seq, "dx_qualifier": q, "dx_code": code, "poa": poa,
                                 "is_principal": 1 if q in PRINCIPAL_QUALIFIERS else 0,
                                 "hi_segment_index": self.idx, "hi_position": i, "_segment_index": self.idx})
            else:
                c["extra"].append(f"HI{self.el}{e[i]}")

    def new_837_line(self, e):
        t = self.tx
        c = t["claim"]
        ln = {"_segment_index": self.idx, "line_number": _g(e, 1) if e else None,
              "line_ordinal": len(c["_lines"]) + 1, "extra": []}
        if e is None:
            ln["_segment_index"] = self.idx
        c["_lines"].append(ln)
        t["line"] = ln

    def on_sv(self, sid, e, ln):
        def proc(raw):
            p = (raw or "").split(self.comp)
            p += [None] * (7 - len(p))
            return [_s(x) for x in p]
        ln["segment_type"] = sid
        if sid == "SV1":
            p = proc(e[1] if len(e) > 1 else None)
            ln.update(procedure_qualifier=p[0], procedure_code=p[1], modifier_1=p[2], modifier_2=p[3],
                      modifier_3=p[4], modifier_4=p[5], procedure_description=p[6], revenue_code=None,
                      charge=self.amt(_g(e, 2)), unit_type=_g(e, 3), units=self.amt(_g(e, 4)),
                      place_of_service=_g(e, 5), service_type_code=_g(e, 6), dx_pointers=_g(e, 7),
                      emergency_indicator=_g(e, 9), epsdt_indicator=_g(e, 11))
        elif sid == "SV2":
            p = proc(e[2] if len(e) > 2 else None)
            ln.update(revenue_code=_g(e, 1), procedure_qualifier=p[0], procedure_code=p[1], modifier_1=p[2],
                      modifier_2=p[3], modifier_3=p[4], modifier_4=p[5], procedure_description=p[6],
                      charge=self.amt(_g(e, 3)), unit_type=_g(e, 4), units=self.amt(_g(e, 5)),
                      place_of_service=None, service_type_code=None, dx_pointers=None,
                      non_covered_charge=self.amt(_g(e, 7)))
        else:
            ln["extra"].append(self.seg_text)
            ln.setdefault("charge", self.amt(_g(e, 2)))

    def line_seg_837(self, sid, e, ln):
        if sid == "DTP":
            q = _g(e, 1)
            f, to = _date_range(_g(e, 3))
            if q == "472":
                ln["service_date"], ln["service_date_to"] = f, to
            else:
                ln["extra"].append(self.seg_text)
        elif sid == "LIN":
            if _g(e, 2) == "N4":
                ln["ndc"] = _g(e, 3)
            else:
                ln["extra"].append(self.seg_text)
        elif sid == "CTP":
            ln["ndc_quantity"] = self.amt(_g(e, 4))
            ln["ndc_unit"] = (_g(e, 5) or "").split(self.comp)[0] or None
        elif sid == "NM1" and _g(e, 1) == "82":
            ln["rendering_npi"] = _g(e, 9) if _g(e, 8) in ("XX", None) else None
        elif sid == "REF" and _g(e, 1) == "6R":
            ln["line_control_number"] = _g(e, 2)
        else:
            ln["extra"].append(self.seg_text)

    def close_837_claim(self):
        t = self.tx
        c = t.get("claim") if t else None
        if c is None:
            return
        t["claim"], t["line"], t["other_sub"] = None, None, False
        dxs, lines = c.pop("_dx"), c.pop("_lines")
        c["diagnosis_count"] = len(dxs)
        princ = next((d for d in dxs if d["is_principal"]), dxs[0] if dxs else None)
        c["principal_diagnosis"] = princ["dx_code"] if princ else None
        c["other_diagnosis_count"] = max(len(dxs) - (1 if princ else 0), 0)
        c["line_count"] = len(lines)
        if not t["tx_type"]:
            c["transaction_type"] = "837I" if (c["admission_type"] or any(l.get("segment_type") == "SV2" for l in lines)) \
                else "837P" if lines else t["type"]
        else:
            c["transaction_type"] = t["tx_type"]
        # for institutional claims CLM05-1 is the facility type code; for professional it is place of service
        if c["transaction_type"] == "837P":
            c["place_of_service"] = c["facility_code"]
        c["claim_status_label"] = {"1": "original", "7": "replacement", "8": "void"}.get(c["frequency_code"])
        dtp, refs, extra = c.pop("dtp"), c.pop("refs"), c.pop("extra")
        c["dates_json"] = _jd(dtp)
        c["refs_json"] = _jd(refs)
        c["_extra"] = json.dumps(extra, separators=(",", ":")) if extra else None
        self.emit("x12_837_claim", c)
        for d in dxs:
            r = self.base(d["_segment_index"])
            r["transaction_type"] = c["transaction_type"]
            r["claim_key"] = c["claim_key"]
            r.update({k: v for k, v in d.items() if k != "_segment_index"})
            self.emit("x12_837_dx", r)
        for ln in lines:
            ex = ln.pop("extra", [])
            r = self.base(ln["_segment_index"])
            r["transaction_type"] = c["transaction_type"]
            r["claim_key"] = c["claim_key"]
            r["claim_patient_control_number"] = c["patient_control_number"]
            for k in ("line_number", "line_ordinal", "segment_type", "procedure_qualifier", "procedure_code",
                      "modifier_1", "modifier_2", "modifier_3", "modifier_4", "procedure_description",
                      "revenue_code", "charge", "units", "unit_type", "place_of_service", "service_type_code",
                      "dx_pointers", "service_date", "service_date_to", "rendering_npi", "ndc", "ndc_quantity",
                      "ndc_unit", "line_control_number", "emergency_indicator", "epsdt_indicator",
                      "non_covered_charge"):
                r[k] = ln.get(k)
            r["_extra"] = json.dumps(ex, separators=(",", ":")) if ex else None
            self.emit("x12_837_line", r)

    # =========================================================================================== 835
    def init_835(self):
        self.tx.update(pay={"bpr": None, "trn": None, "payer": {}, "payee": {}, "extra": [], "plb": [],
                            "dtm": {}, "refs": {}}, cur_n1=None, cl=None, svc=None, claims_paid=0.0,
                       claims_n=0)

    def h835(self, sid, e):
        t = self.tx
        pay = t["pay"]
        if sid == "CLP":
            self.close_835_claim()
            self.open_835_claim(e)
            return
        if sid == "PLB":
            self.close_835_claim()
            pay["plb"].append(self.seg_text)
            return
        cl = t["cl"]
        if cl is None:
            self.pre_claim_835(sid, e, pay)
            return
        sv = t["svc"]
        if sid == "SVC":
            self.open_835_svc(e, cl)
        elif sid == "CAS":
            self.on_cas(e, cl, sv)
        elif sid == "LQ":
            tgt = sv if sv is not None else cl
            tgt["remarks"].append(f"{_g(e, 1)}:{_g(e, 2)}" if _g(e, 1) else (_g(e, 2) or ""))
        elif sid == "DTM":
            q = _g(e, 1)
            d = _date(_g(e, 2))
            if sv is not None:
                if q == "472":
                    sv["service_date"] = d
                elif q in ("150", "151"):
                    sv[f"service_{'start' if q == '150' else 'end'}_date"] = d
                else:
                    sv["extra"].append(self.seg_text)
            elif q == "232":
                cl["statement_from"] = d
            elif q == "233":
                cl["statement_to"] = d
            elif q == "050":
                cl["received_date"] = d
            else:
                cl["dtm"][q] = d
        elif sid == "REF":
            q, v = _g(e, 1), _g(e, 2)
            if sv is not None:
                if q == "6R":
                    sv["line_ref"] = v
                else:
                    sv["extra"].append(self.seg_text)
            elif q == "1L":
                cl["group_policy_number"] = v
            elif q == "EA":
                cl["medical_record_number"] = v
            elif q == "F8":
                cl["original_reference"] = v
            else:
                cl["refs"].setdefault(q, v)
        elif sid == "AMT":
            q = _g(e, 1)
            if sv is not None and q == "B6":
                sv["allowed_amount"] = self.amt(_g(e, 2))
            elif sv is not None:
                sv["extra"].append(self.seg_text)
            else:
                cl["amts"][q] = self.amt(_g(e, 2))
        elif sid == "NM1" and sv is None:
            q = _g(e, 1)
            pre = {"QC": "patient", "IL": "insured", "74": "corrected", "82": "rendering",
                   "TT": "crossover_carrier", "PR": "corrected_payer"}.get(q)
            if pre in ("patient", "insured"):
                cl.update({f"{pre}_last": _g(e, 3), f"{pre}_first": _g(e, 4), f"{pre}_middle": _g(e, 5),
                           f"{pre}_id_qualifier": _g(e, 8), f"{pre}_id": _g(e, 9)})
            elif pre == "rendering":
                cl["rendering_npi"] = _g(e, 9)
            else:
                cl["extra"].append(self.seg_text)
        elif sid == "MIA":
            cl["mia"] = self.el.join(x.strip() for x in e[1:])
            cl["mia_covered_days"] = self.amt(_g(e, 1))
        elif sid == "MOA":
            cl["moa"] = self.el.join(x.strip() for x in e[1:])
            cl["moa_reimbursement_rate"] = self.amt(_g(e, 1))
            cl["moa_remark_codes"] = ",".join(x for x in (_g(e, i) for i in range(3, 8)) if x) or None
        else:
            (sv["extra"] if sv is not None else cl["extra"]).append(self.seg_text)

    def pre_claim_835(self, sid, e, pay):
        t = self.tx
        if sid == "BPR":
            pay["bpr"] = [_g(e, i) for i in range(len(e))]
            t["bpr_idx"] = self.idx
        elif sid == "TRN":
            pay["trn"] = [_g(e, i) for i in range(len(e))]
        elif sid == "N1":
            q = _g(e, 1)
            t["cur_n1"] = q
            if q == "PR":
                pay["payer"].update(name=_g(e, 2), id_qualifier=_g(e, 3), id=_g(e, 4))
            elif q == "PE":
                pay["payee"].update(name=_g(e, 2), id_qualifier=_g(e, 3), id=_g(e, 4),
                                    npi=_g(e, 4) if _g(e, 3) == "XX" else None)
            else:
                pay["extra"].append(self.seg_text)
        elif sid == "DTM":
            q = _g(e, 1)
            pay["dtm"][q] = _date(_g(e, 2))
        elif sid == "REF":
            q, v = _g(e, 1), _g(e, 2)
            cur = t["cur_n1"]
            if cur == "PE" and q in ("TJ", "EI"):
                pay["payee"]["tin"] = v
            elif cur == "PR" and q in ("2U", "EO", "HI", "NF"):
                pay["payer"]["ref_id"] = v
            else:
                pay["refs"].setdefault(q, v)
        else:
            pay["extra"].append(self.seg_text)

    def open_835_claim(self, e):
        t = self.tx
        t["ordinal"] += 1
        if len(e) < 5:
            self.issue("clp_short")
        status = _g(e, 2)
        cl = self.base(self.idx)
        cl.update({
            "claim_key": self.claim_key(t["ordinal"]), "claim_ordinal": t["ordinal"],
            "patient_control_number": _g(e, 1), "claim_status_code": status,
            "is_reversal": 1 if status == "22" else 0,
            "total_charge": self.amt(_g(e, 3)), "paid_amount": self.amt(_g(e, 4)),
            "patient_responsibility": self.amt(_g(e, 5)), "claim_filing_indicator": _g(e, 6),
            "payer_claim_control_number": _g(e, 7), "facility_code": _g(e, 8), "frequency_code": _g(e, 9),
            "patient_status_code": _g(e, 10), "drg_code": _g(e, 11), "drg_weight": self.amt(_g(e, 12)),
            "discharge_fraction": self.amt(_g(e, 13)),
            "patient_last": None, "patient_first": None, "patient_middle": None, "patient_id_qualifier": None,
            "patient_id": None, "insured_last": None, "insured_first": None, "insured_middle": None,
            "insured_id_qualifier": None, "insured_id": None, "rendering_npi": None,
            "group_policy_number": None, "medical_record_number": None, "original_reference": None,
            "statement_from": None, "statement_to": None, "received_date": None,
            "mia": None, "mia_covered_days": None, "moa": None, "moa_reimbursement_rate": None,
            "moa_remark_codes": None,
            "payer_name": t["pay"]["payer"].get("name"), "payee_npi": t["pay"]["payee"].get("npi"),
            "payee_name": t["pay"]["payee"].get("name"),
            "cas_count": 0, "cas_total": 0.0, "cas_pr_total": 0.0, "cas_co_total": 0.0, "cas_oa_total": 0.0,
            "cas_pi_total": 0.0, "cas_cr_total": 0.0, "line_count": 0, "line_cas_count": 0,
            "payment_trace_number": (t["pay"]["trn"] or [None] * 3)[2] if t["pay"]["trn"] and len(t["pay"]["trn"]) > 2 else None,
        })
        cl["remarks"], cl["dtm"], cl["refs"], cl["amts"], cl["extra"] = [], {}, {}, {}, []
        cl["_adj"], cl["_lines"] = [], []
        t["cl"], t["svc"] = cl, None
        t["claims_n"] += 1
        t["claims_paid"] += cl["paid_amount"] or 0.0

    def open_835_svc(self, e, cl):
        t = self.tx
        p = (e[1] if len(e) > 1 else "").split(self.comp)
        p += [None] * (7 - len(p))
        p = [_s(x) for x in p]
        o = (e[6] if len(e) > 6 else "").split(self.comp)
        o += [None] * 2
        sv = {"_segment_index": self.idx, "line_ordinal": len(cl["_lines"]) + 1,
              "procedure_qualifier": p[0], "procedure_code": p[1], "modifier_1": p[2], "modifier_2": p[3],
              "modifier_3": p[4], "modifier_4": p[5], "procedure_description": p[6],
              "charge": self.amt(_g(e, 2)), "paid_amount": self.amt(_g(e, 3)), "revenue_code": _g(e, 4),
              "units": self.amt(_g(e, 5)), "original_procedure": _s(e[6]) if len(e) > 6 else None,
              "original_units": self.amt(_g(e, 7)), "service_date": None, "service_start_date": None,
              "service_end_date": None, "line_ref": None, "allowed_amount": None,
              "remarks": [], "extra": []}
        cl["_lines"].append(sv)
        t["svc"] = sv

    def on_cas(self, e, cl, sv):
        grp = _g(e, 1)
        n = 0
        i = 2
        while i < len(e):
            reason, amt, qty = _g(e, i), _g(e, i + 1), _g(e, i + 2)
            if reason is None and amt is None and qty is None:
                i += 3
                continue
            n += 1
            a = self.amt(amt)
            row = self.base(self.idx)
            row.update({"claim_key": cl["claim_key"], "level": "line" if sv is not None else "claim",
                        "line_ordinal": sv["line_ordinal"] if sv is not None else None,
                        "cas_sequence": len(cl["_adj"]) + 1, "group_code": grp, "reason_code": reason,
                        "amount": a, "quantity": self.amt(qty), "patient_control_number": cl["patient_control_number"]})
            cl["_adj"].append(row)
            if sv is None and a is not None:
                cl["cas_count"] += 1
                cl["cas_total"] += a
                key = {"PR": "cas_pr_total", "CO": "cas_co_total", "OA": "cas_oa_total", "PI": "cas_pi_total",
                       "CR": "cas_cr_total"}.get(grp)
                if key:
                    cl[key] += a
            elif sv is None:
                cl["cas_count"] += 1
            else:
                cl["line_cas_count"] += 1
            i += 3
        if n == 0:
            self.issue("cas_without_adjustments")

    def close_835_claim(self):
        t = self.tx
        cl = t.get("cl") if t else None
        if cl is None:
            return
        t["cl"], t["svc"] = None, None
        adjs, lines = cl.pop("_adj"), cl.pop("_lines")
        cl["line_count"] = len(lines)
        for k in ("cas_total", "cas_pr_total", "cas_co_total", "cas_oa_total", "cas_pi_total", "cas_cr_total"):
            cl[k] = round(cl[k], 2)
        cl["remark_codes"] = ",".join(cl.pop("remarks")) or None
        cl["dates_json"] = _jd(cl.pop("dtm"))
        cl["refs_json"] = _jd(cl.pop("refs"))
        cl["amounts_json"] = _jd(cl.pop("amts"))
        ex = cl.pop("extra")
        cl["_extra"] = json.dumps(ex, separators=(",", ":")) if ex else None
        self.emit("x12_835_claim", cl)
        for a in adjs:
            self.emit("x12_835_adjustment", a)
        for sv in lines:
            r = self.base(sv["_segment_index"])
            r["claim_key"] = cl["claim_key"]
            r["claim_patient_control_number"] = cl["patient_control_number"]
            for k, v in sv.items():
                if k not in ("_segment_index", "remarks", "extra"):
                    r[k] = v
            r["remark_codes"] = ",".join(sv["remarks"]) or None
            r["_extra"] = json.dumps(sv["extra"], separators=(",", ":")) if sv["extra"] else None
            self.emit("x12_835_line", r)

    def close_835_payment(self):
        t = self.tx
        pay = t["pay"]
        bpr = pay["bpr"] or []
        trn = pay["trn"] or []
        if not bpr and not trn and not t["claims_n"]:
            self.issue("835_without_bpr_trn", idx=t["idx"])
            return
        if not bpr:
            self.issue("835_missing_bpr", idx=t["idx"])
        r = self.base(t.get("bpr_idx", t["idx"]))
        r["payment_key"] = f"{self.isa['control_number']}:{t['st02']}"

        def b(i):
            return bpr[i] if i < len(bpr) else None
        r.update({
            "handling_code": b(1), "payment_amount": self.amt(b(2)), "credit_debit_flag": b(3),
            "payment_method": b(4), "payment_format": b(5),
            "sender_dfi_qualifier": b(6), "sender_dfi_id": b(7), "sender_account_qualifier": b(8),
            "sender_account_number": b(9), "originating_company_id": b(10), "originating_company_supplemental": b(11),
            "receiver_dfi_qualifier": b(12), "receiver_dfi_id": b(13), "receiver_account_qualifier": b(14),
            "receiver_account_number": b(15), "effective_date": _date(b(16)),
            "trace_type": trn[1] if len(trn) > 1 else None, "trace_number": trn[2] if len(trn) > 2 else None,
            "trace_originator_id": trn[3] if len(trn) > 3 else None,
            "payer_name": pay["payer"].get("name"), "payer_id": pay["payer"].get("id") or pay["payer"].get("ref_id"),
            "payer_id_qualifier": pay["payer"].get("id_qualifier"),
            "payee_name": pay["payee"].get("name"), "payee_npi": pay["payee"].get("npi"),
            "payee_id": pay["payee"].get("id"), "payee_id_qualifier": pay["payee"].get("id_qualifier"),
            "payee_tin": pay["payee"].get("tin"),
            "production_date": pay["dtm"].get("405"), "claim_count": t["claims_n"],
            "claims_paid_total": round(t["claims_paid"], 2),
            "plb_count": len(pay["plb"]),
            "dates_json": _jd(pay["dtm"]), "refs_json": _jd(pay["refs"]),
            "_extra": json.dumps(pay["extra"] + pay["plb"], separators=(",", ":")) if (pay["extra"] or pay["plb"]) else None,
            "interchange_sender_id": self.isa["sender_id"], "interchange_receiver_id": self.isa["receiver_id"],
            "interchange_date": self.isa["date"],
        })
        self.emit("x12_835_payment", r)

    # =========================================================================================== 834
    def init_834(self):
        self.tx.update(hdr={"extra": [], "dtp": {}, "refs": {}}, mem=None, nm_ctx=None, cov=None, file_type=None,
                       file_type_source=None)

    def h834(self, sid, e):
        t = self.tx
        if sid == "INS":
            self.close_834_member()
            self.open_834_member(e)
            return
        m = t["mem"]
        hdr = t["hdr"]
        if m is None:
            if sid == "BGN":
                hdr.update(bgn01=_g(e, 1), reference_id=_g(e, 2), file_date=_date(_g(e, 3)), file_time=_time(_g(e, 4)),
                           bgn08=_g(e, 8))
                a = hdr["bgn08"]
                if a == "RX":
                    t["file_type"], t["file_type_source"] = "full", "BGN08"
                elif a == "2":
                    t["file_type"], t["file_type_source"] = "change", "BGN08"
                elif a == "4":
                    t["file_type"], t["file_type_source"] = "verify", "BGN08"
                if t["file_type"] is None:
                    n = self.name.upper()
                    if "FULL" in n:
                        t["file_type"], t["file_type_source"] = "full", "filename"
                    elif "CHG" in n or "CHANGE" in n:
                        t["file_type"], t["file_type_source"] = "change", "filename"
            elif sid == "N1":
                q = _g(e, 1)
                if q == "P5":
                    hdr.update(sponsor_name=_g(e, 2), sponsor_id_qualifier=_g(e, 3), sponsor_id=_g(e, 4))
                elif q == "IN":
                    hdr.update(insurer_name=_g(e, 2), insurer_id_qualifier=_g(e, 3), insurer_id=_g(e, 4))
                elif q == "TV":
                    hdr.update(tpa_name=_g(e, 2), tpa_id=_g(e, 4))
                else:
                    hdr["extra"].append(self.seg_text)
            elif sid == "DTP":
                hdr["dtp"][_g(e, 1)] = _date(_g(e, 3))
            elif sid == "REF":
                hdr["refs"][_g(e, 1)] = _g(e, 2)
            else:
                hdr["extra"].append(self.seg_text)
            return
        if sid == "REF":
            q = _g(e, 1)
            if t["cov"] is not None:
                t["cov"]["extra"].append(self.seg_text)
                return
            v = _g(e, 2)
            if q == "0F":
                m["subscriber_id"] = v
            elif q == "1L":
                m["group_policy_number"] = v
            elif q == "17":
                m["ref_17"] = v
            else:
                m["refs"].setdefault(q, v)
        elif sid == "NM1":
            q = _g(e, 1)
            t["nm_ctx"] = q
            if q == "IL":
                m.update(last_name=_g(e, 3), first_name=_g(e, 4), middle_name=_g(e, 5), name_suffix=_g(e, 7),
                         member_id_qualifier=_g(e, 8), member_id=_g(e, 9), entity_type=_g(e, 2))
            elif q == "P3":
                m["pcp_npi"], m["pcp_name"] = _g(e, 9), self.nm_name(e)
            else:
                m["extra"].append(self.seg_text)
        elif sid in ("N3", "N4"):
            if t["nm_ctx"] == "IL" and t["cov"] is None:
                if sid == "N3":
                    m["address_1"], m["address_2"] = _g(e, 1), _g(e, 2)
                else:
                    m["city"], m["state"], m["postal_code"] = _g(e, 1), _g(e, 2), _g(e, 3)
                    m["country_code"] = _g(e, 4)
            else:
                m["extra"].append(self.seg_text)
        elif sid == "DMG":
            m["dob"], m["gender"], m["marital_status"] = _date(_g(e, 2)), _g(e, 3), _g(e, 4)
            r = _g(e, 5)
            m["race"] = r.split(self.comp)[1] if r and self.comp in r and len(r.split(self.comp)) > 1 else r
        elif sid == "HD":
            cov = {"maintenance_type": _g(e, 1), "maintenance_reason": _g(e, 2), "insurance_line_code": _g(e, 3),
                   "plan_coverage_description": _g(e, 4), "coverage_level_code": _g(e, 5), "dtp": {}, "extra": []}
            m["coverages"].append(cov)
            t["cov"] = cov
        elif sid == "DTP":
            q = _g(e, 1)
            d = _date_range(_g(e, 3))[0]
            cov = t["cov"]
            if cov is not None:
                cov["dtp"][q] = d
            else:
                m["dtp"][q] = d
        else:
            (t["cov"]["extra"] if t["cov"] is not None else m["extra"]).append(self.seg_text)

    def open_834_member(self, e):
        t = self.tx
        t["ordinal"] += 1
        t["nm_ctx"], t["cov"] = None, None
        t["mem"] = {"_segment_index": self.idx, "ordinal": t["ordinal"], "ins": [_g(e, i) for i in range(len(e))],
                    "coverages": [], "dtp": {}, "refs": {}, "extra": []}
        if len(e) < 4:
            self.issue("ins_short")

    def close_834_member(self):
        t = self.tx
        m = t.get("mem") if t else None
        if m is None:
            return
        t["mem"] = None
        hdr, ins = t["hdr"], m["ins"]

        def i(n):
            return ins[n] if n < len(ins) else None
        r = self.base(m["_segment_index"])
        d = m["dtp"]
        c0 = m["coverages"][0] if m["coverages"] else {}
        r.update({
            "member_key": f"{self.isa['control_number']}:{t['st02']}:{m['ordinal']}", "member_ordinal": m["ordinal"],
            "file_type": t["file_type"], "file_type_source": t["file_type_source"],
            "file_action_code": hdr.get("bgn08"), "file_reference_id": hdr.get("reference_id"),
            "file_date": hdr.get("file_date"),
            "sponsor_name": hdr.get("sponsor_name"), "sponsor_id": hdr.get("sponsor_id"),
            "insurer_name": hdr.get("insurer_name"), "insurer_id": hdr.get("insurer_id"),
            "subscriber_indicator": i(1), "relationship_code": i(2), "maintenance_type_code": i(3),
            "maintenance_type_label": MAINT_LABELS.get(i(3) or ""), "maintenance_reason_code": i(4),
            "benefit_status_code": i(5), "medicare_status_code": i(6), "cobra_qualifier": i(7),
            "employment_status_code": i(8), "student_status_code": i(9), "handicap_indicator": i(10),
            "ins_other": self.el.join(x or "" for x in ins[11:]) or None,
            "subscriber_id": m.get("subscriber_id"), "group_policy_number": m.get("group_policy_number"),
            "ref_17": m.get("ref_17"), "refs_json": _jd(m["refs"]),
            "last_name": m.get("last_name"), "first_name": m.get("first_name"), "middle_name": m.get("middle_name"),
            "name_suffix": m.get("name_suffix"), "member_id_qualifier": m.get("member_id_qualifier"),
            "member_id": m.get("member_id"),
            "address_1": m.get("address_1"), "address_2": m.get("address_2"), "city": m.get("city"),
            "state": m.get("state"), "postal_code": m.get("postal_code"), "country_code": m.get("country_code"),
            "dob": m.get("dob"), "gender": m.get("gender"), "marital_status": m.get("marital_status"),
            "race": m.get("race"), "pcp_npi": m.get("pcp_npi"), "pcp_name": m.get("pcp_name"),
            "eligibility_begin_date": d.get("356"), "eligibility_end_date": d.get("357"),
            "maintenance_effective_date": d.get("303"),
            "benefit_begin_date": next((c["dtp"].get("348") for c in m["coverages"] if c["dtp"].get("348")), None),
            "benefit_end_date": next((c["dtp"].get("349") for c in m["coverages"] if c["dtp"].get("349")), None),
            "dates_json": _jd(d),
            "hd_maintenance_type": c0.get("maintenance_type"), "hd_maintenance_reason": c0.get("maintenance_reason"),
            "insurance_line_code": c0.get("insurance_line_code"),
            "plan_coverage_description": c0.get("plan_coverage_description"),
            "coverage_level_code": c0.get("coverage_level_code"),
            "coverage_count": len(m["coverages"]),
            "coverages_json": json.dumps(m["coverages"], separators=(",", ":"), sort_keys=True) if m["coverages"] else None,
            "interchange_sender_id": self.isa["sender_id"], "interchange_receiver_id": self.isa["receiver_id"],
            "interchange_date": self.isa["date"],
            "_extra": json.dumps(m["extra"], separators=(",", ":")) if m["extra"] else None,
        })
        self.emit("x12_834_member", r)


# --------------------------------------------------------------------------------------------- driver
def parse_stream(chunks, source_name: str = "") -> Iterator[tuple[str, dict]]:
    rd = _Reader(source_name)
    saw_any = False
    for seg, el, comp in iter_segments(chunks):
        saw_any = True
        rd.feed(seg, el, comp)
        if rd.out:
            yield from rd.out
            rd.out.clear()
    if not saw_any:
        rd.issue("empty_file", seg="", idx=0)
    rd.finish()
    yield from rd.out
    rd.out.clear()
