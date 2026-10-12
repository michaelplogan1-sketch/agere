"""Allowlisted adapter for the existing synthetic demo export only.

The demo's engine answers, truth, scores and benchmark labels are deliberately
outside this interface. This is not a general clinical ingestion connector.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .contracts import SourceBatch, digest


FIELDS = {
    "roster": ("patient_id", "age", "sex", "practice", "pcp", "plan", "region"),
    "notes": ("note_id", "patient_id", "date", "author", "note_type", "text"),
    "medical_claims": ("claim_id", "patient_id", "service_date", "paid_date", "place", "provider", "facility", "dx"),
    "pharmacy_claims": ("patient_id", "rx_id", "fill_date", "drug_class", "drug", "status", "reject_code", "days_supply", "copay"),
    "labs": ("patient_id", "result_date", "test", "value", "lab", "unit", "standard_error", "assay"),
    "adt": ("patient_id", "date", "event", "facility"),
    "sdoh_purchased": ("patient_id", "vendor", "financial_risk", "transport_risk", "isolation_risk"),
}
DATE_FIELDS = {"notes": "date", "medical_claims": "service_date", "pharmacy_claims": "fill_date", "labs": "result_date", "adt": "date"}
REQUIRED = {
    "roster": FIELDS["roster"], "notes": ("note_id", "patient_id", "date", "note_type", "text"),
    "medical_claims": ("patient_id", "service_date", "place", "dx"),
    "pharmacy_claims": ("patient_id", "fill_date", "drug_class", "status"),
    "labs": ("patient_id", "result_date", "test", "value"),
    "adt": ("patient_id", "date", "event", "facility"), "sdoh_purchased": ("patient_id",),
}


def project_record(raw: dict, feed: str, patient_id: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{feed}: expected a record")
    missing = set(REQUIRED[feed]) - raw.keys()
    if missing:
        raise ValueError(f"{feed}: missing required fields {sorted(missing)}")
    if raw["patient_id"] != patient_id:
        raise ValueError(f"{feed}: patient identity mismatch")
    result = {k: raw[k] for k in (*FIELDS[feed], "available_date") if k in raw}
    if result.get("available_date") is not None:
        date.fromisoformat(result["available_date"])
    # Fields in this export are scalars except the diagnosis string list.
    for key, value in result.items():
        if key == "dx":
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError("medical_claims: dx must be a string list")
        elif not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError(f"{feed}: invalid field shape for {key}")
    return result


def project_sample(records: list, as_of: str) -> SourceBatch:
    date.fromisoformat(as_of)
    if not isinstance(records, list) or not records:
        raise ValueError("Expected a nonempty synthetic patient sample")
    patients, excluded, late, unknown_arrival = {}, 0, 0, 0
    for record in records:
        pid = record.get("id")
        if not isinstance(pid, str) or not pid or pid in patients:
            raise ValueError("Missing or duplicate patient ID")
        feeds = record.get("feeds")
        if not isinstance(feeds, dict):
            raise ValueError("Expected source feeds")
        pf = {"roster": [project_record(record["roster"], "roster", pid)]}
        if (pf["roster"][0].get("available_date") or as_of) > as_of:
            raise ValueError("Roster not available at cutoff")
        for feed in FIELDS:
            if feed == "roster":
                continue
            # Missing feed and a supplied empty feed are not interchangeable.
            if feed not in feeds or not isinstance(feeds[feed], list):
                raise ValueError(f"Missing source feed: {feed}")
            pf[feed] = []
            seen_notes = set()
            for raw in feeds[feed]:
                row = project_record(raw, feed, pid)
                date_key = DATE_FIELDS.get(feed)
                if date_key:
                    date.fromisoformat(row[date_key])
                    if row[date_key] > as_of:
                        excluded += 1
                        continue
                if row.get("available_date") is not None and row["available_date"] > as_of:
                    late += 1
                    continue
                if row.get("available_date") is None:
                    unknown_arrival += 1
                if feed == "notes":
                    if row["note_id"] in seen_notes:
                        raise ValueError("Duplicate source note ID")
                    seen_notes.add(row["note_id"])
                pf[feed].append(row)
            if feed in DATE_FIELDS:
                pf[feed].sort(key=lambda row: (row[DATE_FIELDS[feed]], digest(row)))
        patients[pid] = pf
    snapshot_id = digest({"patients": patients, "as_of": as_of})
    return SourceBatch(patients, as_of, snapshot_id, excluded,
                       excluded_late_records=late, arrival_time_missing_records=unknown_arrival)


def load_sample(path: Path, as_of: str) -> SourceBatch:
    return project_sample(json.loads(path.read_text()), as_of)
