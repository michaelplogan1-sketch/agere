"""Versioned, synthetic source intake. No fuzzy patient matching or OCR execution.

Event time and arrival time are separate. Corrections never erase earlier
versions. Same-time conflicting versions are quarantined, not arbitrarily ranked.
Scope labels isolate local datasets; they are not production access control.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from .contracts import SourceBatch, digest
from .inputs import DATE_FIELDS, FIELDS, project_record

SCHEMA = "agere-source/1"
ENVELOPE = {"schema", "synthetic", "namespace", "source_system", "source_kind",
            "record_id", "version", "patient_id", "event_date", "available_date", "payload"}
DOCUMENT_FIELDS = {"content_sha256", "processing_status", "extractor_revision", "pages"}


def iso(value, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must use YYYY-MM-DD")


def validate(record):
    if not isinstance(record, dict) or set(record) != ENVELOPE:
        raise ValueError("Source requires the exact versioned envelope")
    if record["schema"] != SCHEMA or record["synthetic"] is not True:
        raise ValueError("This intake accepts synthetic observations only")
    for key in ("namespace", "source_system", "source_kind", "record_id", "version", "patient_id"):
        if not isinstance(record[key], str) or not record[key].strip():
            raise ValueError("Explicit source identity and version required")
    iso(record["event_date"], nullable=True)
    iso(record["available_date"], nullable=True)
    payload, kind = record["payload"], record["source_kind"]
    if kind == "document":
        if not isinstance(payload, dict) or set(payload) != DOCUMENT_FIELDS:
            raise ValueError("Document requires hash, processing status, extractor revision and pages")
        sha = payload["content_sha256"]
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Document requires an explicit SHA-256 content identifier")
        if payload["processing_status"] not in {"pending", "failed", "ready"}:
            raise ValueError("Unknown document processing status")
        if not isinstance(payload["pages"], list):
            raise ValueError("Document pages must be a list")
        if payload["processing_status"] != "ready" and payload["pages"]:
            raise ValueError("Unprocessed document cannot expose extracted text")
        if payload["processing_status"] == "ready":
            if not payload["pages"] or not isinstance(payload["extractor_revision"], str) or not payload["extractor_revision"].strip():
                raise ValueError("Ready documents require pages and a versioned extractor")
        elif payload["extractor_revision"] is not None:
            raise ValueError("Unprocessed document has no accepted extraction revision")
        seen = set()
        for page in payload["pages"]:
            if not isinstance(page, dict) or set(page) != {"page", "text"}:
                raise ValueError("Page requires number and literal extracted text")
            if type(page["page"]) is not int or page["page"] < 1 or page["page"] in seen:
                raise ValueError("Invalid or repeated document page")
            if not isinstance(page["text"], str):
                raise ValueError("Page text must be literal text")
            seen.add(page["page"])
    elif kind in FIELDS:
        projected = project_record(payload, kind, record["patient_id"])
        if projected != payload:
            raise ValueError("Payload contains unsupported fields; project observed feeds first")
        event = payload.get(DATE_FIELDS.get(kind))
        if event != record["event_date"] or payload.get("available_date") != record["available_date"]:
            raise ValueError("Envelope and source dates disagree")
    else:
        raise ValueError("Unsupported source kind")
    digest(record)  # Reject non-JSON/nonfinite content before any persistence.


def batch_records(batch, namespace):
    """Project only observed batch fields; no benchmark answers or hidden truth."""
    if digest({"patients": batch.patients, "as_of": batch.as_of}) != batch.snapshot_id:
        raise ValueError("Mutated source batch")
    for pid, feeds in sorted(batch.patients.items()):
        for kind in FIELDS:
            for raw in feeds[kind]:
                row = project_record(raw, kind, pid)
                identifier = next((str(row[k]) for k in ("note_id", "claim_id", "rx_id") if row.get(k)), None)
                if kind == "roster":
                    identifier = pid
                yield {"schema": SCHEMA, "synthetic": True, "namespace": namespace,
                       "source_system": "synthetic-feed:" + kind, "source_kind": kind,
                       "record_id": identifier or digest(row), "version": digest(row),
                       "patient_id": pid, "event_date": row.get(DATE_FIELDS.get(kind)),
                       "available_date": row.get("available_date"), "payload": row}


class SourceLedger:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS source_versions (
            namespace TEXT NOT NULL, identity TEXT NOT NULL, version TEXT NOT NULL,
            patient_id TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(namespace, identity, version))""")
        self.db.commit()

    def close(self):
        self.db.close()

    def ingest(self, records):
        inserted = reused = 0
        with self.db:
            for record in records:
                validate(record)
                identity = digest({k: record[k] for k in ("source_system", "source_kind", "record_id")})
                existing_patient = self.db.execute(
                    "SELECT patient_id FROM source_versions WHERE namespace=? AND identity=? LIMIT 1",
                    (record["namespace"], identity)).fetchone()
                if existing_patient and existing_patient[0] != record["patient_id"]:
                    raise ValueError("A source identity cannot silently move between patients")
                payload = json.dumps(record, sort_keys=True, allow_nan=False)
                key = (record["namespace"], identity, record["version"])
                existing = self.db.execute("SELECT payload FROM source_versions WHERE namespace=? AND identity=? AND version=?", key).fetchone()
                if existing:
                    if existing[0] != payload:
                        raise ValueError("Immutable source revision changed; supply a new version")
                    reused += 1
                else:
                    self.db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?)", (*key, record["patient_id"], payload))
                    inserted += 1
        return {"inserted": inserted, "reused": reused}

    def snapshot(self, namespace, as_of, *, strict_arrival=False):
        iso(as_of)
        groups, excluded = defaultdict(list), Counter()
        rows = self.db.execute("SELECT identity,payload FROM source_versions WHERE namespace=?", (namespace,))
        for identity, payload in rows:
            record = json.loads(payload)
            if record["available_date"] and record["available_date"] > as_of:
                excluded["future_arrival"] += 1
                continue
            # Select the known correction before checking its event date: a
            # correction moving an event into the future must remove the old one.
            if strict_arrival and record["available_date"] is None and record["source_kind"] != "roster":
                excluded["unknown_arrival"] += 1
                continue
            groups[identity].append(record)
        selected, issues = [], []
        for identity, records in sorted(groups.items()):
            known = [r for r in records if r["available_date"] is not None]
            unknown = [r for r in records if r["available_date"] is None]
            newest = max((r["available_date"] for r in known), default=None)
            candidates = [r for r in known if r["available_date"] == newest] + unknown
            if len(candidates) != 1:
                issues.append({"kind": "ambiguous_source_versions", "patient_id": records[0]["patient_id"],
                               "source_identity": identity, "versions": sorted(r["version"] for r in candidates)})
                continue
            record = candidates[0]
            if record["event_date"] and record["event_date"] > as_of:
                excluded["future_event"] += 1
                continue
            selected.append(record)
        patients, provenance, documents = {}, {}, []
        # Multiple roster systems are not automatically reconciled.
        roster = defaultdict(list)
        for r in selected:
            if r["source_kind"] == "roster":
                roster[r["patient_id"]].append(r)
        for pid, records in roster.items():
            if len(records) == 1:
                patients[pid] = {kind: [] for kind in FIELDS}
            else:
                issues.append({"kind": "ambiguous_patient_roster", "patient_id": pid})
        missing_arrival = 0
        for r in selected:
            pid, kind = r["patient_id"], r["source_kind"]
            ref = {k: r[k] for k in ("source_system", "source_kind", "record_id", "version", "event_date", "available_date")}
            ref.update(source_revision_id=digest(r), namespace=namespace, patient_id=pid)
            if pid not in patients:
                issues.append({"kind": "unlinked_patient_record", **ref})
                continue
            if kind != "roster" and r["available_date"] is None:
                missing_arrival += 1
            if kind == "document":
                doc = dict(ref, **{k: r["payload"][k] for k in ("content_sha256", "processing_status", "extractor_revision")},
                           pages=len(r["payload"]["pages"]), note_ids=[])
                documents.append(doc)
                if r["payload"]["processing_status"] != "ready" or r["event_date"] is None:
                    issues.append({"kind": "document_unread" if r["payload"]["processing_status"] != "ready" else "document_date_unknown", **ref})
                    continue
                for page in sorted(r["payload"]["pages"], key=lambda p: p["page"]):
                    note = {"patient_id": pid, "note_id": "doc-" + digest([ref, page["page"]]),
                            "date": r["event_date"], "available_date": r["available_date"],
                            "text": page["text"], "note_type": "imported_document_extraction"}
                    patients[pid]["notes"].append(note)
                    doc["note_ids"].append(note["note_id"])
                    provenance[digest(note)] = {**ref, "page": page["page"], "extractor_revision": doc["extractor_revision"],
                                               "content_sha256": doc["content_sha256"], "acceptance": "unverified_extraction"}
            else:
                row = dict(r["payload"])
                if kind == "notes":
                    row["note_id"] = "note-" + digest([r["source_system"], r["record_id"], r["version"]])
                patients[pid][kind].append(row)
                provenance[digest(row)] = ref
        for feeds in patients.values():
            for kind in FIELDS:
                # The inherited reader deduplicates in input order. Earliest
                # documentation must win so copy-forward cannot refresh a hold.
                key = DATE_FIELDS.get(kind)
                feeds[kind].sort(key=lambda row: (row[key] if key else "", digest(row)))
        snapshot_id = digest({"patients": patients, "as_of": as_of})
        batch = SourceBatch(patients, as_of, snapshot_id, excluded["future_event"],
                            "synthetic_world", excluded["future_arrival"], missing_arrival)
        metadata = {"namespace": namespace, "strict_arrival": strict_arrival, "documents": documents,
                    "issues": issues, "excluded": dict(excluded), "provenance": provenance,
                    "visible_source_versions": len(selected),
                    "unknown_arrival_records": missing_arrival,
                    "identity_policy": "Exact supplied patient IDs only; no cross-system identity inference",
                    "historical_limit": "Undated roster is retained as cohort context; strict arrival applies to clinical evidence."}
        return batch, metadata
