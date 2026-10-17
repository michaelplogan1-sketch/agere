"""HL7 v2.x parser (ADT / ORU / any pipe-delimited message) for the native feed ingestion contract.

Landing tables yielded by parse():
    hl7_message       one row per MSH (header, PID, PV1, EVN fields; unmodelled segments kept in _unmodeled_segments)
    hl7_diagnosis     one row per DG1
    hl7_observation   one row per OBX (with its parent OBR context and any NTE comments)
    hl7_order         one row per OBR
    hl7_insurance     one row per IN1
    hl7_parse_issue   one row per structural problem found (never raises on bad input)

Every child row repeats message_key = "<file>:<ordinal>" (ordinal is 1-based position of the MSH in the file) and
message_control_id (MSH-10). Separators come from MSH-1/MSH-2; escape sequences are decoded after splitting so an
escaped separator never splits a field. Batch wrappers (FHS/BHS/BTS/FTS) and MLLP framing bytes are tolerated.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

FEED_NAME = "hl7"
_SEG_RE = re.compile(r"^[A-Z][A-Z0-9]{2}(?:\||$)")
_BATCH_SEGS = {"FHS", "BHS", "BTS", "FTS"}
_MODELED = {"MSH", "PID", "PV1", "EVN", "DG1", "OBR", "OBX", "IN1", "NTE"}

RESULT_STATUS = {
    "F": "final", "C": "corrected", "P": "preliminary", "X": "cancelled", "D": "deleted", "W": "wrong_patient",
    "R": "entered_not_verified", "I": "in_process", "S": "partial", "A": "amended", "O": "order_received",
    "N": "not_asked", "U": "status_changed_to_final", "Y": "no_order",
}


# --------------------------------------------------------------------------------------------- detection
def _head(path: Path, n: int = 64) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def detect(path: Path) -> bool:
    path = Path(path)
    if any(p.lower() == "ncpdp" for p in path.parts[:-1]):
        return False
    head = _head(path).lstrip(b"\x0b\xef\xbb\xbf \t\r\n")
    return head[:3] in (b"MSH", b"FHS", b"BHS")


# --------------------------------------------------------------------------------------------- helpers
def norm_dt(raw):
    """HL7 TS/DTM/DT -> (iso, offset). iso keeps the offset when one was sent; (None, None) if unparseable."""
    if not raw:
        return None, None
    s = raw.split("^")[0].strip()
    m = re.fullmatch(r"(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:\.(\d+))?([+-]\d{2}\d{2})?", s)
    if not m:
        return None, None
    y, mo, d, hh, mi, ss, frac, off = m.groups()
    out = y
    if mo:
        out += "-" + mo
        if d:
            out += "-" + d
    if hh:
        out += f"T{hh}:{mi or '00'}:{ss or '00'}"
        if frac:
            out += "." + frac
    offset = None
    if off:
        offset = f"{off[:3]}:{off[3:]}"
        if hh:
            out += offset
    try:  # reject impossible calendar values (month 13 etc.)
        datetime(int(y), int(mo or 1), int(d or 1), int(hh or 0), int(mi or 0), int(ss or 0))
    except ValueError:
        return None, None
    return out, offset


def _utc(iso):
    if not iso or "T" not in iso or not re.search(r"[+-]\d{2}:\d{2}$", iso):
        return None
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


class _Ctx:
    def __init__(self, fs="|", enc="^~\\&"):
        self.fs = fs
        e = (enc or "^~\\&")
        self.comp = e[0] if len(e) > 0 else "^"
        self.rep = e[1] if len(e) > 1 else "~"
        self.esc = e[2] if len(e) > 2 else "\\"
        self.sub = e[3] if len(e) > 3 else "&"
        self._esc_re = re.compile(re.escape(self.esc) + r"(.*?)" + re.escape(self.esc), re.S)

    def unesc(self, s):
        if s is None or self.esc not in s:
            return s

        def sub(m):
            c = m.group(1)
            if c == "F":
                return self.fs
            if c == "S":
                return self.comp
            if c == "T":
                return self.sub
            if c == "R":
                return self.rep
            if c == "E":
                return self.esc
            if c == ".br":
                return "\n"
            if re.fullmatch(r"X(?:[0-9A-Fa-f]{2})+", c):
                b = bytes.fromhex(c[1:])
                try:
                    return b.decode("utf-8")
                except UnicodeDecodeError:
                    return b.decode("latin-1")
            return m.group(0)

        return self._esc_re.sub(sub, s)

    def reps(self, raw):
        return raw.split(self.rep) if raw else []

    def get(self, f, n, ri=0, ci=None, si=None):
        """Unescaped value of field n (HL7 numbering), repetition ri, component ci (1-based), subcomponent si."""
        if n >= len(f) or not f[n]:
            return None
        reps = f[n].split(self.rep)
        if ri >= len(reps):
            return None
        v = reps[ri]
        if ci is not None:
            comps = v.split(self.comp)
            if ci > len(comps):
                return None
            v = comps[ci - 1]
        if si is not None:
            subs = v.split(self.sub)
            if si > len(subs):
                return None
            v = subs[si - 1]
        v = self.unesc(v)
        return v if v != "" else None

    def comp_list(self, rep_raw):
        return [self.unesc(c.split(self.sub)[0]) or None for c in rep_raw.split(self.comp)]

    def cx(self, raw):
        out = []
        for r in self.reps(raw):
            c = r.split(self.comp)

            def g(i, c=c):
                if i > len(c):
                    return None
                return self.unesc(c[i - 1].split(self.sub)[0]) or None
            if not any(c):
                continue
            out.append({"id": g(1), "check_digit": g(2), "check_scheme": g(3), "assigning_authority": g(4),
                        "id_type": g(5), "assigning_facility": g(6)})
        return out

    def ce(self, raw):
        out = []
        for r in self.reps(raw):
            c = [self.unesc(x.split(self.sub)[0]) or None for x in r.split(self.comp)]
            c += [None] * (6 - len(c))
            if any(c[:6]):
                out.append({"code": c[0], "text": c[1], "system": c[2], "alt_code": c[3], "alt_text": c[4],
                            "alt_system": c[5]})
        return out


def _js(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True) if v else None


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _is_npi(v):
    return bool(v) and bool(re.fullmatch(r"\d{10}", v))


def _xcn(ctx, f, n):
    if n >= len(f) or not f[n]:
        return {}
    c = f[n].split(ctx.rep)[0].split(ctx.comp)

    def g(i):
        return ctx.unesc(c[i - 1].split(ctx.sub)[0]) or None if i <= len(c) else None
    ident, idtype = g(1), g(13)
    name = " ".join(x for x in (g(3), g(4), g(2)) if x) or None
    npi = ident if (_is_npi(ident) and (idtype in (None, "NPI"))) or (ident and idtype == "NPI") else None
    return {"id": ident, "family": g(2), "given": g(3), "name": name, "id_type": idtype, "npi": npi,
            "authority": g(9)}


# --------------------------------------------------------------------------------------------- reading
def _read(path: Path):
    raw = path.read_bytes()
    issues = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        issues.append(("encoding_fallback", "file is not valid UTF-8; decoded as latin-1"))
    return text.replace("﻿", ""), issues


def _segments(text):
    """-> list of (segment_text, line_no, continuation?) after stripping MLLP framing."""
    text = text.replace("\x0b", "\r").replace("\x1c", "\r")
    lines = re.split(r"\r\n|\r|\n", text)
    segs, issues = [], []
    for i, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        if _SEG_RE.match(ln):
            segs.append([ln.rstrip(), i])
        elif segs:
            segs[-1][0] += "\n" + ln
            issues.append(("continuation_line", f"line {i} is not a segment; appended to previous segment", i))
        else:
            issues.append(("leading_garbage", f"line {i} before first segment ignored", i))
    return segs, issues


# --------------------------------------------------------------------------------------------- parse
def parse(path: Path, context: dict) -> Iterator[tuple[str, dict]]:
    path = Path(path)
    context = context or {}
    fname = context.get("relpath") or path.name
    try:
        text, rissues = _read(path)
    except OSError as e:
        yield "hl7_parse_issue", _issue(fname, None, None, "error", "unreadable_file", str(e), None)
        return
    for code, detail in rissues:
        yield "hl7_parse_issue", _issue(fname, None, None, "warning", code, detail, None)
    segs, sissues = _segments(text)
    for it in sissues:
        yield "hl7_parse_issue", _issue(fname, None, None, "warning", it[0], it[1], None, line=it[2])

    # group segments into messages
    msgs, cur = [], None
    for raw, ln in segs:
        sid = raw[:3]
        if sid == "MSH":
            cur = []
            msgs.append(cur)
        if sid in _BATCH_SEGS:
            cur = None
            continue
        if cur is None:
            yield "hl7_parse_issue", _issue(fname, None, None, "warning", "segment_outside_message",
                                            f"{sid} at line {ln} is not inside an MSH message", raw[:200], line=ln)
            continue
        cur.append((raw, ln))
    bts = next((raw for raw, _ in segs if raw[:3] == "BTS"), None)
    declared = None
    if bts is not None:
        try:
            declared = int(bts.split("|")[1])
        except (IndexError, ValueError):
            pass
    if declared is not None and declared != len(msgs):
        yield "hl7_parse_issue", _issue(fname, None, None, "info" if not msgs else "warning",
                                        "batch_count_mismatch", f"BTS declares {declared} messages, found {len(msgs)}",
                                        bts)
    if not msgs:
        if bts is None:
            yield "hl7_parse_issue", _issue(fname, None, None, "error", "no_msh", "no MSH segment found in file", None)
        return
    for ordinal, m in enumerate(msgs, 1):
        yield from _parse_message(fname, ordinal, m)


def _issue(file, key, ordinal, severity, code, detail, seg, line=None):
    return {"message_key": key, "message_control_id": None, "file": file, "message_ordinal": ordinal, "severity": severity, "issue_code": code,
            "detail": detail, "line_no": line, "segment_raw": (seg[:500] if seg else None)}


def _parse_message(fname, ordinal, seglist):
    key = f"{fname}:{ordinal}"
    msh_raw, msh_line = seglist[0]
    issues = []

    def iss(sev, code, detail, seg=None, line=None):
        issues.append(("hl7_parse_issue", {**_issue(fname, key, ordinal, sev, code, detail, seg, line),
                                           "message_control_id": cid_holder[0]}))

    cid_holder = [None]
    fs = msh_raw[3] if len(msh_raw) > 3 else "|"
    p = msh_raw.split(fs)
    ctx = _Ctx(fs, p[1] if len(p) > 1 else "^~\\&")
    if len(p) < 2 or not p[1]:
        iss("warning", "missing_encoding_chars", "MSH-2 empty; default ^~\\& assumed", msh_raw, msh_line)
    if len(p) < 10:
        iss("error", "short_msh", f"MSH has {len(p) - 1} fields; MSH-9 message type required", msh_raw, msh_line)
    msh = ["MSH", fs] + p[1:]  # msh[n] == MSH-n

    control_id = ctx.get(msh, 10)
    cid_holder[0] = control_id
    if control_id is None:
        iss("warning", "missing_control_id", "MSH-10 empty", msh_raw, msh_line)
    mtype = ctx.get(msh, 9, ci=1)
    mevent = ctx.get(msh, 9, ci=2)
    if not mtype:
        iss("error", "missing_message_type", "MSH-9 empty", msh_raw, msh_line)
    dt_raw = ctx.get(msh, 7, ci=1)
    dt_iso, dt_off = norm_dt(dt_raw)
    if dt_raw and dt_iso is None:
        iss("warning", "bad_datetime", f"MSH-7 not a valid HL7 timestamp: {dt_raw!r}", msh_raw, msh_line)

    by = {}
    for raw, ln in seglist[1:]:
        by.setdefault(raw[:3], []).append((raw, ln))

    def first(sid):
        return (by[sid][0][0].split(fs), by[sid][0][0]) if by.get(sid) else (None, None)

    row = {
        "message_key": key, "file": fname, "message_ordinal": ordinal, "message_control_id": control_id,
        "line_no": msh_line,
        "field_separator": fs, "encoding_characters": p[1] if len(p) > 1 else None,
        "sending_application": ctx.get(msh, 3, ci=1), "sending_facility": ctx.get(msh, 4, ci=1),
        "receiving_application": ctx.get(msh, 5, ci=1), "receiving_facility": ctx.get(msh, 6, ci=1),
        "message_datetime_raw": ctx.get(msh, 7), "message_datetime": dt_iso, "message_timezone_offset": dt_off,
        "message_datetime_utc": _utc(dt_iso),
        "message_type": f"{mtype}^{mevent}" if mtype and mevent else mtype, "message_code": mtype,
        "trigger_event": mevent, "message_structure": ctx.get(msh, 9, ci=3),
        "processing_id": ctx.get(msh, 11, ci=1), "version": ctx.get(msh, 12, ci=1),
        "accept_ack_type": ctx.get(msh, 15), "app_ack_type": ctx.get(msh, 16), "country_code": ctx.get(msh, 17),
        "character_set": ctx.get(msh, 18),
        "segment_ids": " ".join(["MSH"] + [raw[:3] for raw, _ in seglist[1:]]), "_raw_msh": msh_raw,
    }

    # PID
    pid, pid_raw = first("PID")
    if len(by.get("PID", [])) > 1:
        iss("warning", "multiple_pid", f"{len(by['PID'])} PID segments; first used", None)
    if pid is None:
        if (mtype or "") in ("ADT", "ORU"):
            iss("warning", "missing_pid", f"{mtype} message has no PID segment")
        row.update({"patient_ids_json": None})
    else:
        ids = ctx.cx(pid[3] if len(pid) > 3 else "")
        alt = ctx.cx(pid[4] if len(pid) > 4 else "")
        first_id = ids[0] if ids else {}
        mrn = next((i for i in ids if i.get("id_type") == "MR"), first_id)
        addr_c = (pid[11].split(ctx.rep)[0].split(ctx.comp) if len(pid) > 11 and pid[11] else [])

        def ac(i):
            return ctx.unesc(addr_c[i - 1].split(ctx.sub)[0]) or None if i <= len(addr_c) else None
        dob_iso, _ = norm_dt(ctx.get(pid, 7))
        death_iso, _ = norm_dt(ctx.get(pid, 29))
        race = ctx.ce(pid[10] if len(pid) > 10 else "")
        row.update({
            "patient_ids_json": _js(ids), "patient_id_count": len(ids),
            "patient_id": first_id.get("id"), "patient_id_assigning_authority": first_id.get("assigning_authority"),
            "patient_id_type": first_id.get("id_type"),
            "patient_mrn": mrn.get("id"), "patient_mrn_assigning_authority": mrn.get("assigning_authority"),
            "patient_alt_ids_json": _js(alt), "patient_set_id": ctx.get(pid, 1),
            "patient_name_family": ctx.get(pid, 5, ci=1, si=1), "patient_name_given": ctx.get(pid, 5, ci=2),
            "patient_name_middle": ctx.get(pid, 5, ci=3), "patient_name_suffix": ctx.get(pid, 5, ci=4),
            "patient_name_prefix": ctx.get(pid, 5, ci=5),
            "patient_dob_raw": ctx.get(pid, 7), "patient_dob": dob_iso, "patient_sex": ctx.get(pid, 8),
            "patient_race_code": race[0]["code"] if race else None,
            "patient_race_text": race[0]["text"] if race else None,
            "patient_race_system": race[0]["system"] if race else None,
            "patient_address_street": ac(1), "patient_address_other": ac(2), "patient_address_city": ac(3),
            "patient_address_state": ac(4), "patient_address_zip": ac(5), "patient_address_country": ac(6),
            "patient_phone_home": ctx.get(pid, 13, ci=1), "patient_language": ctx.get(pid, 15, ci=1),
            "patient_marital_status": ctx.get(pid, 16, ci=1), "patient_account_number": ctx.get(pid, 18, ci=1),
            "patient_ethnic_group": ctx.get(pid, 22, ci=1), "patient_death_indicator": ctx.get(pid, 30),
            "patient_death_datetime": death_iso,
            "_raw_pid": pid_raw,
        })
        if not ids:
            iss("warning", "missing_patient_id", "PID-3 empty", pid_raw)

    # EVN
    evn, evn_raw = first("EVN")
    if evn is not None:
        evt_iso, _ = norm_dt(ctx.get(evn, 2))
        occ_iso, _ = norm_dt(ctx.get(evn, 6))
        row.update({"event_type_code": ctx.get(evn, 1), "event_recorded_datetime": evt_iso,
                    "event_recorded_datetime_raw": ctx.get(evn, 2), "event_reason_code": ctx.get(evn, 4),
                    "event_occurred_datetime": occ_iso, "_raw_evn": evn_raw})

    # PV1
    pv1, pv1_raw = first("PV1")
    if pv1 is not None:
        adm_iso, _ = norm_dt(ctx.get(pv1, 44))
        dis_iso, _ = norm_dt(ctx.get(pv1, 45))
        att = _xcn(ctx, pv1, 7)
        refr = _xcn(ctx, pv1, 8)
        admd = _xcn(ctx, pv1, 17)
        visit = ctx.cx(pv1[19] if len(pv1) > 19 else "")
        row.update({
            "pv1_set_id": ctx.get(pv1, 1), "patient_class": ctx.get(pv1, 2),
            "assigned_location": ctx.get(pv1, 3, ci=1), "assigned_location_room": ctx.get(pv1, 3, ci=2),
            "assigned_location_bed": ctx.get(pv1, 3, ci=3), "assigned_location_facility": ctx.get(pv1, 3, ci=4),
            "assigned_location_raw": ctx.get(pv1, 3),
            "admission_type": ctx.get(pv1, 4), "preadmit_number": ctx.get(pv1, 5, ci=1),
            "attending_id": att.get("id"), "attending_npi": att.get("npi"), "attending_name": att.get("name"),
            "attending_id_type": att.get("id_type"),
            "referring_id": refr.get("id"), "referring_npi": refr.get("npi"),
            "admitting_id": admd.get("id"), "admitting_npi": admd.get("npi"),
            "hospital_service": ctx.get(pv1, 10), "admit_source": ctx.get(pv1, 14),
            "patient_type": ctx.get(pv1, 18), "visit_number": visit[0]["id"] if visit else None,
            "visit_number_authority": visit[0]["assigning_authority"] if visit else None,
            "discharge_disposition": ctx.get(pv1, 36), "discharged_to_location": ctx.get(pv1, 37, ci=1),
            "servicing_facility": ctx.get(pv1, 39),
            "admit_datetime_raw": ctx.get(pv1, 44), "admit_datetime": adm_iso,
            "discharge_datetime_raw": ctx.get(pv1, 45), "discharge_datetime": dis_iso,
            "_raw_pv1": pv1_raw,
        })

    patient_id = row.get("patient_id")
    unmodeled = [raw for raw, _ in seglist[1:] if raw[:3] not in _MODELED]
    unmodeled += [raw for sid in ("PID", "PV1", "EVN") for raw, _ in by.get(sid, [])[1:]]
    row["_unmodeled_segments"] = _js(unmodeled)
    out = [("hl7_message", row)]

    ctxrow = {"message_key": key, "message_control_id": control_id, "file": fname, "message_ordinal": ordinal}

    # DG1 / IN1 / OBR / OBX / NTE in order
    dx_n = in_n = ob_n = obx_n = 0
    cur_order = None
    last_child = None
    for raw, ln in seglist[1:]:
        sid = raw[:3]
        f = raw.split(fs)
        if sid == "DG1":
            dx_n += 1
            dt, _ = norm_dt(ctx.get(f, 5))
            out.append(("hl7_diagnosis", {
                **ctxrow, "diagnosis_ordinal": dx_n, "patient_id": patient_id, "set_id": ctx.get(f, 1),
                "coding_method": ctx.get(f, 2), "diagnosis_code": ctx.get(f, 3, ci=1),
                "diagnosis_text": ctx.get(f, 3, ci=2) or ctx.get(f, 4), "coding_system": ctx.get(f, 3, ci=3),
                "diagnosis_description": ctx.get(f, 4), "diagnosis_datetime_raw": ctx.get(f, 5),
                "diagnosis_datetime": dt, "diagnosis_type": ctx.get(f, 6), "major_diagnostic_category": ctx.get(f, 7),
                "priority": ctx.get(f, 15), "_raw": raw}))
            last_child = None
        elif sid == "IN1":
            in_n += 1
            m36, m49 = ctx.get(f, 36), ctx.get(f, 49, ci=1)
            eff, _ = norm_dt(ctx.get(f, 12))
            exp, _ = norm_dt(ctx.get(f, 13))
            out.append(("hl7_insurance", {
                **ctxrow, "insurance_ordinal": in_n, "patient_id": patient_id, "set_id": ctx.get(f, 1),
                "plan_id": ctx.get(f, 2, ci=1), "plan_name": ctx.get(f, 2, ci=2),
                "company_id": ctx.get(f, 3, ci=1), "company_name": ctx.get(f, 4, ci=1),
                "group_number": ctx.get(f, 8), "group_name": ctx.get(f, 9, ci=1), "plan_effective_date": eff,
                "plan_expiration_date": exp, "plan_type": ctx.get(f, 15), "insured_name_family": ctx.get(f, 16, ci=1),
                "insured_name_given": ctx.get(f, 16, ci=2), "insured_relationship": ctx.get(f, 17, ci=1),
                "policy_number": m36, "insured_id": m49, "member_id": m49 or m36, "_raw": raw}))
            last_child = None
        elif sid == "OBR":
            ob_n += 1
            sv = ctx.ce(f[4] if len(f) > 4 else "")
            s0 = sv[0] if sv else {}
            loinc = next((s for s in sv if (s["system"] or "").upper() in ("LN", "LOINC")), None)
            odt, _ = norm_dt(ctx.get(f, 7))
            rdt, _ = norm_dt(ctx.get(f, 14))
            rep_dt, _ = norm_dt(ctx.get(f, 22))
            status = ctx.get(f, 25)
            prov = _xcn(ctx, f, 16)
            cur_order = {
                **ctxrow, "order_ordinal": ob_n, "patient_id": patient_id, "set_id": ctx.get(f, 1),
                "placer_order_number": ctx.get(f, 2, ci=1), "filler_order_number": ctx.get(f, 3, ci=1),
                "placer_order_namespace": ctx.get(f, 2, ci=2), "filler_order_namespace": ctx.get(f, 3, ci=2),
                "universal_service_code": s0.get("code"), "universal_service_text": s0.get("text"),
                "universal_service_system": s0.get("system"), "loinc_code": loinc["code"] if loinc else None,
                "universal_service_json": _js(sv), "priority": ctx.get(f, 5, ci=1),
                "requested_datetime": norm_dt(ctx.get(f, 6))[0],
                "observation_datetime_raw": ctx.get(f, 7), "observation_datetime": odt,
                "observation_end_datetime": norm_dt(ctx.get(f, 8))[0],
                "specimen_received_raw": ctx.get(f, 14), "specimen_received_datetime": rdt,
                "specimen_source": ctx.get(f, 15, ci=1), "ordering_provider_id": prov.get("id"),
                "ordering_provider_npi": prov.get("npi"), "ordering_provider_name": prov.get("name"),
                "results_report_datetime": rep_dt, "diagnostic_service_section": ctx.get(f, 24),
                "result_status": status, "result_status_label": RESULT_STATUS.get(status or "", None),
                "reason_for_study": ctx.get(f, 31, ci=1), "note": None, "_raw": raw}
            out.append(("hl7_order", cur_order))
            last_child = cur_order
        elif sid == "OBX":
            obx_n += 1
            if cur_order is None:
                iss("warning", "obx_without_obr", f"OBX at line {ln} has no preceding OBR", raw, ln)
            vt = ctx.get(f, 2)
            ids = ctx.ce(f[3] if len(f) > 3 else "")
            i0 = ids[0] if ids else {}
            loinc = next((i for i in ids if (i["system"] or "").upper() in ("LN", "LOINC")), None)
            local = next((i for i in ids if i is not loinc), None)
            valraw = f[5] if len(f) > 5 else ""
            if vt in ("CE", "CWE", "CNE", "IS", "ID", "CF") and valraw:
                v0 = ctx.ce(valraw)
                value = v0[0]["code"] if v0 else None
                value_text = v0[0]["text"] if v0 else None
            else:
                parts = [ctx.unesc(x) for x in ctx.reps(valraw)]
                value = "~".join(parts) if parts else None
                value_text = None
            if value == "":
                value = None
            status = ctx.get(f, 11)
            odt, _ = norm_dt(ctx.get(f, 14))
            adt_, _ = norm_dt(ctx.get(f, 19))
            flags = [ctx.unesc(x) for x in ctx.reps(f[8] if len(f) > 8 else "") if x]
            org = ctx.get(f, 23, ci=1) or ctx.get(f, 15, ci=2) or ctx.get(f, 15, ci=1)
            if not status:
                iss("warning", "obx_missing_status", f"OBX-11 empty at line {ln}", raw, ln)
            o = cur_order or {}
            row_o = {
                **ctxrow, "observation_ordinal": obx_n, "order_ordinal": o.get("order_ordinal"),
                "patient_id": patient_id,
                "placer_order_number": o.get("placer_order_number"), "filler_order_number": o.get("filler_order_number"),
                "obr_universal_service_code": o.get("universal_service_code"),
                "obr_universal_service_text": o.get("universal_service_text"),
                "obr_universal_service_system": o.get("universal_service_system"),
                "obr_observation_datetime": o.get("observation_datetime"),
                "obr_specimen_received_datetime": o.get("specimen_received_datetime"),
                "obr_result_status": o.get("result_status"),
                "set_id": ctx.get(f, 1), "value_type": vt,
                "observation_code": i0.get("code"), "observation_text": i0.get("text"),
                "observation_coding_system": i0.get("system"),
                "loinc_code": loinc["code"] if loinc else None,
                "local_code": local["code"] if local else None, "local_text": local["text"] if local else None,
                "local_coding_system": local["system"] if local else None,
                "observation_identifiers_json": _js(ids), "observation_sub_id": ctx.get(f, 4),
                "value": value, "value_text": value_text, "value_numeric": _num(value) if vt == "NM" else None,
                "units": ctx.get(f, 6, ci=1), "units_text": ctx.get(f, 6, ci=2),
                "reference_range": ctx.get(f, 7), "abnormal_flags": ",".join(flags) if flags else None,
                "result_status": status, "result_status_label": RESULT_STATUS.get(status or "", None),
                "is_corrected": 1 if status == "C" else 0, "is_final": 1 if status == "F" else 0,
                "is_cancelled": 1 if status in ("X", "D") else 0,
                "observation_datetime_raw": ctx.get(f, 14), "observation_datetime": odt,
                "analysis_datetime": adt_, "performing_org": org, "performing_org_address": ctx.get(f, 24, ci=1),
                "responsible_observer": ctx.get(f, 16, ci=1), "note": None, "_raw": raw}
            out.append(("hl7_observation", row_o))
            last_child = row_o
        elif sid == "NTE":
            txt = ctx.get(f, 3)
            if last_child is not None and txt:
                last_child["note"] = txt if not last_child["note"] else last_child["note"] + "\n" + txt
            elif txt:
                iss("info", "nte_without_parent", f"NTE at line {ln} has no preceding OBR/OBX", raw, ln)
    # issues first so a consumer sees them even if it stops early; rows follow
    for t, r in issues:
        yield t, r
    for t, r in out:
        yield t, r
