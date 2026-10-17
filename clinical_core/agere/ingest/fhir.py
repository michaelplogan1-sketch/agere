"""FHIR R4 bulk (ndjson) loader.

One landing table per resource type: ``fhir_<resourcetype lowercase>``. Every row carries the common columns
(id, meta_last_updated, patient_id, encounter_id, status, ...), resource-specific flattened essentials, and the full
resource as JSON in ``_resource`` so nothing a sender included is lost. Date-times keep the sender's text (offset and
all) in ``<col>`` and a normalized ISO form in ``<col>_iso`` (offset-bearing values converted to UTC ``...Z``; naive
values left naive; partial dates left partial).

Lines that cannot be parsed (truncated deliveries) are not dropped: they land in ``fhir_parse_error`` with the raw
text prefix and the JSON error so the orchestrator can report them.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

PARSE_ERROR_TABLE = "fhir_parse_error"

_SYS = {
    "loinc": "http://loinc.org",
    "snomed": "http://snomed.info/sct",
    "rxnorm": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "ndc": "http://hl7.org/fhir/sid/ndc",
    "cvx": "http://hl7.org/fhir/sid/cvx",
    "cpt": "http://www.ama-assn.org/go/cpt",
    "icd10cm": "http://hl7.org/fhir/sid/icd-10-cm",
    "npi": "http://hl7.org/fhir/sid/us-npi",
}


# ---------------------------------------------------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------------------------------------------------
def detect(path: Path) -> bool:
    p = Path(path)
    if "private" in p.parts:
        return False
    return p.suffix.lower() in (".ndjson", ".jsonl")


# ---------------------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------------------
def _js(v: Any) -> str | None:
    if v is None or v == [] or v == {}:
        return None
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def _first(v: Any) -> Any:
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _dict(v: Any) -> dict:
    v = _first(v)
    return v if isinstance(v, dict) else {}


def _ref_id(ref: Any) -> str | None:
    """Reference ('Patient/123', urn:uuid:..., absolute URL, or {reference:...}) -> the id part."""
    ref = _first(ref)
    if isinstance(ref, dict):
        r = ref.get("reference")
        if not r:
            ident = ref.get("identifier")
            return str(ident.get("value")) if isinstance(ident, dict) and ident.get("value") is not None else None
        ref = r
    if not isinstance(ref, str) or not ref:
        return None
    ref = ref.split("|", 1)[0].split("#", 1)[0]
    if ref.startswith("urn:"):
        return ref.rsplit(":", 1)[-1]
    return ref.rstrip("/").rsplit("/", 1)[-1] or None


def _ref_type(ref: Any) -> str | None:
    ref = _first(ref)
    r = ref.get("reference") if isinstance(ref, dict) else ref
    if isinstance(r, str) and "/" in r and not r.startswith("urn:"):
        parts = r.rstrip("/").split("/")
        return parts[-2] if len(parts) >= 2 else None
    return None


_DT_RE = re.compile(
    r"^(\d{4})(?:-(\d{2})(?:-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?)?)?)?$"
)


def norm_dt(value: Any) -> str | None:
    """Normalized ISO for a FHIR date/dateTime/instant string. See module docstring."""
    if not isinstance(value, str) or not value.strip():
        return None
    m = _DT_RE.match(value.strip())
    if not m:
        return None
    y, mo, d, hh, mi, ss, frac, tz = m.groups()
    if hh is None:  # partial date or date
        return "-".join(x for x in (y, mo, d) if x)
    try:
        dt = datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0))
    except ValueError:
        return None
    if tz is None:
        return dt.isoformat()
    if tz == "Z":
        off = timedelta(0)
    else:
        sign = -1 if tz[0] == "-" else 1
        digits = tz[1:].replace(":", "")
        off = sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    utc = (dt - off).replace(tzinfo=timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _put_dt(row: dict, col: str, value: Any) -> None:
    if value is None or isinstance(value, (dict, list)):
        row[col] = None
        row[col + "_iso"] = None
        return
    row[col] = value
    row[col + "_iso"] = norm_dt(value)


def _codings(cc: Any) -> list[dict]:
    """All codings of a CodeableConcept (or list of CodeableConcepts)."""
    out: list[dict] = []
    for c in _as_list(cc):
        if isinstance(c, dict):
            for k in c.get("coding") or []:
                if isinstance(k, dict):
                    out.append(k)
    return out


def _text(cc: Any) -> str | None:
    c = _first(cc)
    if isinstance(c, dict):
        if c.get("text"):
            return c["text"]
        for k in c.get("coding") or []:
            if isinstance(k, dict) and k.get("display"):
                return k["display"]
    return None


def _pick(codings: list[dict], prefer: tuple[str, ...]) -> dict:
    """Pick the coding whose system matches the earliest preference; otherwise the first coding that has a code."""
    for want in prefer:
        for k in codings:
            if _sys_match(k.get("system"), want) and k.get("code") is not None:
                return k
    for k in codings:
        if k.get("code") is not None:
            return k
    return codings[0] if codings else {}


def _sys_match(system: Any, want: str) -> bool:
    if not isinstance(system, str):
        return False
    s = system.lower()
    if want == "icd10cm":
        return "icd-10" in s or "icd10" in s
    if want == "rxnorm":
        return "rxnorm" in s
    if want == "ndc":
        return "/ndc" in s or s.endswith("ndc")
    if want == "loinc":
        return "loinc.org" in s
    if want == "snomed":
        return "snomed" in s
    if want == "cvx":
        return "/cvx" in s or s.endswith("cvx")
    if want == "cpt":
        return "cpt" in s
    return s == _SYS.get(want, want).lower()


def _code_cols(row: dict, prefix: str, cc: Any, prefer: tuple[str, ...]) -> list[dict]:
    """<prefix>_system/_code/_display/_text/_codings (all codings JSON). Returns the coding list."""
    cs = _codings(cc)
    k = _pick(cs, prefer)
    row[f"{prefix}_system"] = k.get("system")
    row[f"{prefix}_code"] = k.get("code")
    row[f"{prefix}_display"] = k.get("display") or _text(cc)
    row[f"{prefix}_text"] = _text(cc)
    row[f"{prefix}_codings"] = _js(cs)
    return cs


def _code_of(codings: list[dict], want: str) -> str | None:
    for k in codings:
        if _sys_match(k.get("system"), want) and k.get("code") is not None:
            return k["code"]
    return None


def _qty(row: dict, prefix: str, q: Any) -> None:
    q = _dict(q)
    row[f"{prefix}_value"] = q.get("value")
    row[f"{prefix}_unit"] = q.get("unit")
    row[f"{prefix}_code"] = q.get("code")
    row[f"{prefix}_system"] = q.get("system")


def _first_code(cc: Any) -> str | None:
    k = _pick(_codings(cc), ())
    return k.get("code")


def _join_codes(cc: Any) -> str | None:
    codes = [str(k["code"]) for k in _codings(cc) if k.get("code") is not None]
    return ",".join(codes) if codes else None


def _patient_id(res: dict) -> str | None:
    for key in ("subject", "patient", "beneficiary", "individual"):
        v = res.get(key)
        if v:
            i = _ref_id(v)
            if i and _ref_type(v) in (None, "Patient"):
                return i
    for part in _as_list(res.get("participant")):  # Appointment
        a = part.get("actor") if isinstance(part, dict) else None
        if a and _ref_type(a) == "Patient":
            return _ref_id(a)
    return None


def _encounter_id(res: dict) -> str | None:
    enc = res.get("encounter")
    if enc:
        return _ref_id(enc)
    ctx = res.get("context")
    if isinstance(ctx, dict) and ctx.get("encounter"):
        return _ref_id(ctx["encounter"])
    return None


def _identifier_value(res: dict) -> str | None:
    ids = res.get("identifier")
    first = _dict(ids)
    return first.get("value")


# ---------------------------------------------------------------------------------------------------------------------
# resource-specific flatteners (each returns extra columns; common ones are added by flatten())
# ---------------------------------------------------------------------------------------------------------------------
def _patient(res: dict) -> dict:
    row: dict[str, Any] = {}
    idents = [i for i in _as_list(res.get("identifier")) if isinstance(i, dict)]
    row["identifiers"] = _js(idents)
    by_sys: dict[str, Any] = {}
    mrn = mbi = member = None
    for i in idents:
        val = i.get("value")
        sysv = i.get("system") or ""
        tcodes = [str(k.get("code")).upper() for k in _codings(i.get("type")) if k.get("code")]
        key = sysv or (tcodes[0] if tcodes else "")
        if key and key not in by_sys:
            by_sys[key] = val
        low = sysv.lower()
        if mbi is None and ("mbi" in low or "MB" in tcodes and "medicare" in low):
            mbi = val
        elif member is None and ("member" in low or "SN" in tcodes or "MB" in tcodes or "memberid" in low):
            member = val
        elif mrn is None and ("MR" in tcodes or "mrn" in low or "medicalrecord" in low):
            mrn = val
    if mrn is None:  # fall back: first identifier with type MR anywhere
        for i in idents:
            if "MR" in [str(k.get("code")).upper() for k in _codings(i.get("type"))]:
                mrn = i.get("value")
                break
    row.update(mrn=mrn, mbi=mbi, member_id=member, identifiers_by_system=_js(by_sys))
    names = [n for n in _as_list(res.get("name")) if isinstance(n, dict)]
    n0 = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    row["name_family"] = n0.get("family")
    row["name_given"] = " ".join(str(g) for g in _as_list(n0.get("given"))) or None
    row["name_text"] = n0.get("text") or " ".join(
        x for x in (row["name_given"], row["name_family"]) if x
    ) or None
    row["names"] = _js(names)
    row["birth_date"] = res.get("birthDate")
    row["gender"] = res.get("gender")
    addrs = [a for a in _as_list(res.get("address")) if isinstance(a, dict)]
    a0 = addrs[0] if addrs else {}
    row["address_line"] = " ".join(str(x) for x in _as_list(a0.get("line"))) or None
    row["address_city"] = a0.get("city")
    row["address_district"] = a0.get("district")
    row["address_state"] = a0.get("state")
    row["address_zip"] = a0.get("postalCode")
    row["addresses"] = _js(addrs)
    race = ethnicity = None
    for ext in _as_list(res.get("extension")):
        if not isinstance(ext, dict):
            continue
        url = str(ext.get("url", "")).lower()
        if "us-core-race" in url or url.endswith("/race"):
            race = _ext_category(ext)
        elif "us-core-ethnicity" in url or url.endswith("/ethnicity"):
            ethnicity = _ext_category(ext)
    row["race"] = race
    row["ethnicity"] = ethnicity
    row["deceased_boolean"] = res.get("deceasedBoolean")
    _put_dt(row, "deceased_datetime", res.get("deceasedDateTime"))
    row["deceased"] = (
        True
        if res.get("deceasedBoolean") is True or res.get("deceasedDateTime")
        else (False if res.get("deceasedBoolean") is False else None)
    )
    row["telecom"] = _js(res.get("telecom"))
    row["marital_status"] = _first_code(res.get("maritalStatus"))
    comm = _dict(res.get("communication"))
    row["language"] = _first_code(comm.get("language")) or _text(comm.get("language"))
    row["managing_organization_id"] = _ref_id(res.get("managingOrganization"))
    row["general_practitioner_ids"] = _js([_ref_id(g) for g in _as_list(res.get("generalPractitioner"))])
    row["active"] = res.get("active")
    return row


def _ext_category(ext: dict) -> str | None:
    texts, cats = None, []
    for sub in ext.get("extension") or []:
        if not isinstance(sub, dict):
            continue
        if sub.get("url") == "text":
            texts = sub.get("valueString")
        elif sub.get("url") in ("ombCategory", "detailed"):
            vc = sub.get("valueCoding") or {}
            cats.append(vc.get("display") or vc.get("code"))
    if cats:
        return "; ".join(str(c) for c in cats if c)
    if texts:
        return texts
    vc = ext.get("valueCoding") or {}
    return vc.get("display") or vc.get("code") or ext.get("valueString")


def _encounter(res: dict) -> dict:
    row: dict[str, Any] = {}
    cls = _dict(res.get("class"))
    row["class_system"] = cls.get("system")
    row["class_code"] = cls.get("code")
    row["class_display"] = cls.get("display")
    tcs = _codings(res.get("type"))
    row["type_code"] = _pick(tcs, ("cpt", "snomed")).get("code")
    row["type_codes"] = _js(tcs)
    row["type_text"] = _text(res.get("type"))
    row["service_type"] = _first_code(res.get("serviceType"))
    row["priority"] = _first_code(res.get("priority"))
    period = _dict(res.get("period"))
    _put_dt(row, "period_start", period.get("start"))
    _put_dt(row, "period_end", period.get("end"))
    row["length_minutes"] = _dict(res.get("length")).get("value")
    row["service_provider_id"] = _ref_id(res.get("serviceProvider"))
    parts = [p for p in _as_list(res.get("participant")) if isinstance(p, dict)]
    npi = None
    ids = []
    for p in parts:
        ind = _dict(p.get("individual"))
        ident = _dict(ind.get("identifier"))
        if ident.get("value") is not None and (
            npi is None and ("npi" in str(ident.get("system", "")).lower() or not ident.get("system"))
        ):
            npi = ident["value"]
        rid = _ref_id(ind)
        if rid:
            ids.append(rid)
    row["participant_npi"] = npi
    row["participant_ids"] = _js(ids)
    row["participants"] = _js(parts)
    hosp = _dict(res.get("hospitalization"))
    row["admit_source"] = _first_code(hosp.get("admitSource"))
    row["discharge_disposition"] = _first_code(hosp.get("dischargeDisposition"))
    row["discharge_disposition_text"] = _text(hosp.get("dischargeDisposition"))
    rcs = _codings(res.get("reasonCode"))
    row["reason_code"] = _pick(rcs, ("icd10cm", "snomed")).get("code")
    row["reason_system"] = _pick(rcs, ("icd10cm", "snomed")).get("system")
    row["reason_codes"] = _js([k.get("code") for k in rcs])
    row["reason_codings"] = _js(rcs)
    row["diagnosis"] = _js(res.get("diagnosis"))
    locs = [_ref_id(l.get("location")) for l in _as_list(res.get("location")) if isinstance(l, dict)]
    row["location_ids"] = _js(locs)
    row["part_of_id"] = _ref_id(res.get("partOf"))
    return row


def _observation(res: dict) -> dict:
    row: dict[str, Any] = {}
    cats = _as_list(res.get("category"))
    row["category_code"] = _first_code(cats[0] if cats else None)
    row["category_codes"] = _join_codes(cats)
    codings = _code_cols(row, "code", res.get("code"), ("loinc", "snomed"))
    row["loinc_code"] = _code_of(codings, "loinc")
    _qty(row, "value_quantity", res.get("valueQuantity"))
    row["value_string"] = res.get("valueString")
    vcc = res.get("valueCodeableConcept")
    vcs = _codings(vcc)
    row["value_codeableconcept_code"] = _pick(vcs, ()).get("code")
    row["value_codeableconcept_system"] = _pick(vcs, ()).get("system")
    row["value_codeableconcept_display"] = _pick(vcs, ()).get("display") or _text(vcc)
    row["value_codeableconcept"] = _js(vcc)
    row["value_integer"] = res.get("valueInteger")
    row["value_boolean"] = res.get("valueBoolean")
    row["value_datetime"] = res.get("valueDateTime")
    for k in ("valueQuantity", "valueString", "valueCodeableConcept", "valueInteger", "valueBoolean", "valueDateTime",
              "valueRange", "valueRatio", "valuePeriod", "valueTime"):
        if res.get(k) is not None:
            row["value_type"] = k[5:]
            break
    else:
        row["value_type"] = None
    row["value_other"] = _js({k: v for k, v in res.items() if k in ("valueRange", "valueRatio", "valuePeriod", "valueTime")})
    _put_dt(row, "effective_datetime", res.get("effectiveDateTime") or res.get("effectiveInstant"))
    period = _dict(res.get("effectivePeriod"))
    _put_dt(row, "effective_period_start", period.get("start"))
    _put_dt(row, "effective_period_end", period.get("end"))
    _put_dt(row, "issued", res.get("issued"))
    row["interpretation_code"] = _first_code(res.get("interpretation"))
    row["interpretation_display"] = _text(res.get("interpretation"))
    rr = [r for r in _as_list(res.get("referenceRange")) if isinstance(r, dict)]
    r0 = rr[0] if rr else {}
    row["reference_range"] = _js(rr)
    row["reference_range_low"] = _dict(r0.get("low")).get("value")
    row["reference_range_high"] = _dict(r0.get("high")).get("value")
    row["reference_range_text"] = r0.get("text")
    row["data_absent_reason"] = _first_code(res.get("dataAbsentReason"))
    comps = [c for c in _as_list(res.get("component")) if isinstance(c, dict)]
    row["component_count"] = len(comps) or None
    row["components"] = _js(comps)
    row["performer_ids"] = _js([_ref_id(p) for p in _as_list(res.get("performer"))])
    row["specimen_id"] = _ref_id(res.get("specimen"))
    row["method_code"] = _first_code(res.get("method"))
    row["note"] = _annotation_text(res.get("note"))
    row["based_on_ids"] = _js([_ref_id(b) for b in _as_list(res.get("basedOn"))])
    row["is_corrected"] = res.get("status") in ("amended", "corrected", "appended")
    return row


def _annotation_text(notes: Any) -> str | None:
    texts = [n.get("text") for n in _as_list(notes) if isinstance(n, dict) and n.get("text")]
    return "\n".join(texts) if texts else None


def _condition(res: dict) -> dict:
    row: dict[str, Any] = {}
    codings = _code_cols(row, "code", res.get("code"), ("icd10cm", "snomed"))
    row["icd10_code"] = _code_of(codings, "icd10cm")
    row["snomed_code"] = _code_of(codings, "snomed")
    row["clinical_status"] = _first_code(res.get("clinicalStatus"))
    row["verification_status"] = _first_code(res.get("verificationStatus"))
    cats = _as_list(res.get("category"))
    row["category_code"] = _first_code(cats[0] if cats else None)
    row["category_codes"] = _join_codes(cats)
    row["is_problem_list_item"] = "problem-list-item" in (row["category_codes"] or "") or None
    row["is_encounter_diagnosis"] = "encounter-diagnosis" in (row["category_codes"] or "") or None
    row["severity_code"] = _first_code(res.get("severity"))
    onset = res.get("onsetDateTime") or _dict(res.get("onsetPeriod")).get("start") or None
    _put_dt(row, "onset_datetime", onset)
    row["onset_string"] = res.get("onsetString")
    _put_dt(row, "recorded_date", res.get("recordedDate"))
    abate = res.get("abatementDateTime") or _dict(res.get("abatementPeriod")).get("end") or None
    _put_dt(row, "abatement_datetime", abate)
    row["abatement_string"] = res.get("abatementString")
    row["recorder_id"] = _ref_id(res.get("recorder"))
    row["asserter_id"] = _ref_id(res.get("asserter"))
    row["note"] = _annotation_text(res.get("note"))
    return row


def _medicationrequest(res: dict) -> dict:
    row: dict[str, Any] = {}
    mcc = res.get("medicationCodeableConcept")
    codings = _code_cols(row, "medication", mcc, ("rxnorm", "ndc"))
    row["rxnorm_code"] = _code_of(codings, "rxnorm")
    row["ndc_code"] = _code_of(codings, "ndc")
    row["medication_reference_id"] = _ref_id(res.get("medicationReference"))
    row["medication_reference_display"] = _dict(res.get("medicationReference")).get("display")
    row["intent"] = res.get("intent")
    row["priority"] = res.get("priority")
    _put_dt(row, "authored_on", res.get("authoredOn"))
    dis = [d for d in _as_list(res.get("dosageInstruction")) if isinstance(d, dict)]
    d0 = dis[0] if dis else {}
    row["dosage_text"] = " | ".join(str(d["text"]) for d in dis if d.get("text")) or None
    dr = _dict(d0.get("doseAndRate"))
    _qty(row, "dose_quantity", dr.get("doseQuantity"))
    rep = _dict(_dict(d0.get("timing")).get("repeat"))
    row["frequency"] = rep.get("frequency")
    row["frequency_period"] = rep.get("period")
    row["frequency_period_unit"] = rep.get("periodUnit")
    row["timing_code"] = _first_code(_dict(d0.get("timing")).get("code"))
    row["route"] = _first_code(d0.get("route")) or _text(d0.get("route"))
    row["as_needed"] = d0.get("asNeededBoolean")
    row["dosage_instructions"] = _js(dis)
    dsp = _dict(res.get("dispenseRequest"))
    _qty(row, "dispense_quantity", dsp.get("quantity"))
    esd = _dict(dsp.get("expectedSupplyDuration"))
    row["supply_duration_value"] = esd.get("value")
    row["supply_duration_unit"] = esd.get("unit")
    row["refills_allowed"] = dsp.get("numberOfRepeatsAllowed")
    vp = _dict(dsp.get("validityPeriod"))
    _put_dt(row, "dispense_validity_start", vp.get("start"))
    _put_dt(row, "dispense_validity_end", vp.get("end"))
    row["requester_id"] = _ref_id(res.get("requester"))
    req_ident = _dict(_dict(res.get("requester")).get("identifier"))
    row["requester_npi"] = req_ident.get("value") if "npi" in str(req_ident.get("system", "npi")).lower() else None
    row["reported_boolean"] = res.get("reportedBoolean")
    rcs = _codings(res.get("reasonCode"))
    row["reason_code"] = _pick(rcs, ("icd10cm", "snomed")).get("code")
    row["reason_codes"] = _js([k.get("code") for k in rcs])
    row["substitution_allowed"] = _dict(res.get("substitution")).get("allowedBoolean")
    row["note"] = _annotation_text(res.get("note"))
    return row


def _decode_attachment(att: dict) -> tuple[str | None, str | None]:
    """(decoded text, error). Only inline base64 data is decoded."""
    data = att.get("data")
    if not isinstance(data, str) or not data:
        return None, None
    try:
        raw = base64.b64decode(data + "=" * (-len(data) % 4), validate=False)
    except (binascii.Error, ValueError) as exc:
        return None, f"base64: {exc}"
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "decoded as cp1252 (not utf-8)"


def _documentreference(res: dict) -> dict:
    row: dict[str, Any] = {}
    _code_cols(row, "type", res.get("type"), ("loinc",))
    cats = _as_list(res.get("category"))
    row["category_code"] = _first_code(cats[0] if cats else None)
    row["doc_status"] = res.get("docStatus")
    _put_dt(row, "date", res.get("date"))
    row["author_ids"] = _js([_ref_id(a) for a in _as_list(res.get("author"))])
    row["author_id"] = _ref_id(_first(res.get("author")))
    row["custodian_id"] = _ref_id(res.get("custodian"))
    ctx = _dict(res.get("context"))
    encs = [_ref_id(e) for e in _as_list(ctx.get("encounter"))]
    row["encounter_ids"] = _js(encs) if len(encs) > 1 else None
    contents = [c for c in _as_list(res.get("content")) if isinstance(c, dict)]
    row["attachment_count"] = len(contents) or None
    atts = [c.get("attachment") for c in contents if isinstance(c.get("attachment"), dict)]
    a0 = atts[0] if atts else {}
    row["attachment_content_type"] = a0.get("contentType")
    row["attachment_url"] = a0.get("url")
    row["attachment_title"] = a0.get("title")
    row["attachment_size"] = a0.get("size")
    row["attachment_hash"] = a0.get("hash")
    row["attachment_format"] = _dict(contents[0].get("format")).get("code") if contents else None
    texts: list[str] = []
    errors: list[str] = []
    urls: list[str] = []
    for a in atts:
        txt, err = _decode_attachment(a)
        if txt is not None:
            texts.append(txt)
        if err:
            errors.append(err)
        if a.get("url") and not a.get("data"):
            urls.append(a["url"])
    row["text"] = "\n\n".join(texts) if texts else None
    row["text_decode_error"] = "; ".join(errors) or None
    row["attachment_urls"] = _js(urls) if len(urls) > 1 else None
    return row


def _appointment(res: dict) -> dict:
    row: dict[str, Any] = {}
    _put_dt(row, "start", res.get("start"))
    _put_dt(row, "end", res.get("end"))
    row["minutes_duration"] = res.get("minutesDuration")
    _put_dt(row, "created", res.get("created"))
    st = _first(res.get("serviceType"))
    row["service_type_code"] = _first_code(st)
    row["service_type_display"] = _text(st)
    row["appointment_type_code"] = _first_code(res.get("appointmentType"))
    row["specialty_code"] = _first_code(res.get("specialty"))
    row["description"] = res.get("description")
    row["cancelation_reason"] = _first_code(res.get("cancelationReason")) or _text(res.get("cancelationReason"))
    parts = [p for p in _as_list(res.get("participant")) if isinstance(p, dict)]
    row["participants"] = _js(parts)
    row["practitioner_ids"] = _js([_ref_id(p.get("actor")) for p in parts if _ref_type(p.get("actor")) == "Practitioner"])
    row["location_id"] = next((_ref_id(p.get("actor")) for p in parts if _ref_type(p.get("actor")) == "Location"), None)
    row["participant_statuses"] = _js([p.get("status") for p in parts])
    rcs = _codings(res.get("reasonCode"))
    row["reason_code"] = _pick(rcs, ("icd10cm", "snomed")).get("code")
    return row


def _allergyintolerance(res: dict) -> dict:
    row: dict[str, Any] = {}
    row["clinical_status"] = _first_code(res.get("clinicalStatus"))
    row["verification_status"] = _first_code(res.get("verificationStatus"))
    row["type"] = res.get("type")
    row["category"] = _js(res.get("category"))
    row["criticality"] = res.get("criticality")
    _code_cols(row, "code", res.get("code"), ("rxnorm", "snomed"))
    _put_dt(row, "onset_datetime", res.get("onsetDateTime"))
    _put_dt(row, "recorded_date", res.get("recordedDate"))
    reacts = [r for r in _as_list(res.get("reaction")) if isinstance(r, dict)]
    row["reactions"] = _js(reacts)
    row["reaction_manifestation"] = "; ".join(
        filter(None, (_text(m) for r in reacts for m in _as_list(r.get("manifestation"))))
    ) or None
    row["reaction_severity"] = next((r.get("severity") for r in reacts if r.get("severity")), None)
    row["recorder_id"] = _ref_id(res.get("recorder"))
    row["note"] = _annotation_text(res.get("note"))
    return row


def _immunization(res: dict) -> dict:
    row: dict[str, Any] = {}
    codings = _code_cols(row, "vaccine", res.get("vaccineCode"), ("cvx",))
    row["cvx_code"] = _code_of(codings, "cvx")
    _put_dt(row, "occurrence_datetime", res.get("occurrenceDateTime"))
    row["occurrence_string"] = res.get("occurrenceString")
    _put_dt(row, "recorded", res.get("recorded"))
    row["primary_source"] = res.get("primarySource")
    row["lot_number"] = res.get("lotNumber")
    _put_dt(row, "expiration_date", res.get("expirationDate"))
    row["site"] = _first_code(res.get("site"))
    row["route"] = _first_code(res.get("route"))
    _qty(row, "dose_quantity", res.get("doseQuantity"))
    row["performer_ids"] = _js([_ref_id(p.get("actor")) for p in _as_list(res.get("performer")) if isinstance(p, dict)])
    row["location_id"] = _ref_id(res.get("location"))
    row["status_reason"] = _first_code(res.get("statusReason"))
    row["note"] = _annotation_text(res.get("note"))
    return row


def _procedure(res: dict) -> dict:
    row: dict[str, Any] = {}
    codings = _code_cols(row, "code", res.get("code"), ("cpt", "snomed"))
    row["cpt_code"] = _code_of(codings, "cpt")
    row["snomed_code"] = _code_of(codings, "snomed")
    row["category_code"] = _first_code(res.get("category"))
    _put_dt(row, "performed_datetime", res.get("performedDateTime"))
    period = _dict(res.get("performedPeriod"))
    _put_dt(row, "performed_period_start", period.get("start"))
    _put_dt(row, "performed_period_end", period.get("end"))
    row["performer_ids"] = _js([_ref_id(p.get("actor")) for p in _as_list(res.get("performer")) if isinstance(p, dict)])
    row["location_id"] = _ref_id(res.get("location"))
    rcs = _codings(res.get("reasonCode"))
    row["reason_code"] = _pick(rcs, ("icd10cm", "snomed")).get("code")
    row["body_site_code"] = _first_code(res.get("bodySite"))
    row["outcome_code"] = _first_code(res.get("outcome"))
    row["note"] = _annotation_text(res.get("note"))
    return row


_SPECIFIC = {
    "Patient": _patient,
    "Encounter": _encounter,
    "Observation": _observation,
    "Condition": _condition,
    "MedicationRequest": _medicationrequest,
    "DocumentReference": _documentreference,
    "Appointment": _appointment,
    "AllergyIntolerance": _allergyintolerance,
    "Immunization": _immunization,
    "Procedure": _procedure,
}

_SKIP_GENERIC = {"resourceType", "id", "meta", "text", "contained", "extension", "modifierExtension", "implicitRules",
                 "language", "identifier"}
_DT_FIELD = re.compile(r"(date|time|instant|^start$|^end$|issued|created|recorded|authoredOn|period)", re.I)


def _snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _generic(res: dict) -> dict:
    """Flatten any resource: scalars as-is, Reference -> <f>_id, CodeableConcept -> <f>_system/_code/_display/_text,
    Period -> <f>_start/_end, Quantity -> <f>_value/_unit, date-times get an _iso twin, anything else JSON."""
    row: dict[str, Any] = {}
    for key, val in res.items():
        if key in _SKIP_GENERIC:
            continue
        col = _snake(key)
        v1 = _first(val)
        if isinstance(val, dict) and "reference" in val or (isinstance(v1, dict) and "reference" in v1):
            row[f"{col}_id"] = _ref_id(val)
            row[col] = _js(val)
        elif _codings(val):
            _code_cols(row, col, val, ())
        elif isinstance(val, dict) and ("start" in val or "end" in val) and set(val) <= {"start", "end"}:
            _put_dt(row, f"{col}_start", val.get("start"))
            _put_dt(row, f"{col}_end", val.get("end"))
        elif isinstance(val, dict) and "value" in val and set(val) <= {"value", "unit", "system", "code", "comparator"}:
            _qty(row, col, val)
        elif isinstance(val, (dict, list)):
            row[col] = _js(val)
        elif isinstance(val, str) and _DT_FIELD.search(key):
            _put_dt(row, col, val)
        else:
            row[col] = val
    ids = [i for i in _as_list(res.get("identifier")) if isinstance(i, dict)]
    row["identifiers"] = _js(ids)
    row["identifier_value"] = _identifier_value(res)
    return row


# ---------------------------------------------------------------------------------------------------------------------
# public flatten / parse
# ---------------------------------------------------------------------------------------------------------------------
def table_name(resource_type: str) -> str:
    return "fhir_" + (re.sub(r"[^a-z0-9]+", "_", resource_type.lower()).strip("_") or "unknown")


def flatten(res: dict) -> tuple[str, dict]:
    """Flatten one FHIR resource dict into (landing_table, row)."""
    rt = str(res.get("resourceType") or "Unknown")
    meta = res.get("meta") if isinstance(res.get("meta"), dict) else {}
    row: dict[str, Any] = {"id": res.get("id")}
    if rt != "Patient":
        row["patient_id"] = _patient_id(res)
    else:
        row["patient_id"] = res.get("id")
    row["encounter_id"] = _encounter_id(res) if rt != "Encounter" else res.get("id")
    row["resource_type"] = rt
    row["status"] = res.get("status")
    _put_dt(row, "meta_last_updated", meta.get("lastUpdated"))
    row["meta_version_id"] = meta.get("versionId")
    row["meta_source"] = meta.get("source")
    row["meta_profile"] = _js(meta.get("profile"))
    fn = _SPECIFIC.get(rt)
    extra = fn(res) if fn else _generic(res)
    for k, v in extra.items():
        row.setdefault(k, v)
    row["_resource"] = json.dumps(res, ensure_ascii=False, separators=(",", ":"))
    return table_name(rt), row


def _iter_resources(obj: Any) -> Iterator[dict]:
    if isinstance(obj, dict) and obj.get("resourceType") == "Bundle":
        for e in obj.get("entry") or []:
            r = e.get("resource") if isinstance(e, dict) else None
            if isinstance(r, dict):
                yield from _iter_resources(r)
    elif isinstance(obj, dict):
        yield obj


def parse(path: Path, context: dict | None = None) -> Iterator[tuple[str, dict]]:
    with open(path, "rb") as fh:
        for line_no, raw in enumerate(fh, start=1):
            if not raw.strip():
                continue
            if line_no == 1 and raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("cp1252", errors="replace")
            text = text.strip()
            try:
                obj = json.loads(text)
            except (json.JSONDecodeError, RecursionError) as exc:
                yield PARSE_ERROR_TABLE, {
                    "line_no": line_no,
                    "error": str(exc),
                    "raw_length": len(text),
                    "raw_prefix": text[:500],
                    "raw_tail": text[-200:],
                }
                continue
            if not isinstance(obj, dict) or not obj.get("resourceType"):
                yield PARSE_ERROR_TABLE, {
                    "line_no": line_no,
                    "error": "not a FHIR resource object (no resourceType)",
                    "raw_length": len(text),
                    "raw_prefix": text[:500],
                    "raw_tail": text[-200:],
                }
                continue
            for res in _iter_resources(obj):
                yield flatten(res)
