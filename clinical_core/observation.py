"""Synthetic evidence foundation: care evidence and observability stay separate.

This is a deterministic reconciliation baseline, not a trained latent-state model.
It never treats missing records as documented noncompletion or a clinical hold as
automatically appropriate. Date windows are research settings, not care guidelines.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from .contracts import digest

POLICY = {"version": "agere-observation/1", "medication_days": 120, "lab_days": 365,
          "automatic_clinical_action": False}
SUBJECTS = {"mra", "sglt2", "arni", "bb", "statin", "a1c"}
KINDS = {"outside_fill": "reported_outside", "outside_lab": "reported_outside",
         "contraindicated": "documented_exception", "intolerance": "documented_exception",
         "pa_denied": "documented_barrier", "cost_abandoned": "documented_barrier",
         "declined": "documented_barrier", "no_access": "documented_barrier"}
FEEDS = {"notes", "pharmacy_claims", "labs"}


def validate_coverage(coverage, patients):
    """Coverage is supplied metadata; record presence cannot manufacture it."""
    if not isinstance(coverage, dict) or set(coverage) - set(patients):
        raise ValueError("Coverage must reference supplied patients")
    for feeds in coverage.values():
        if not isinstance(feeds, dict) or set(feeds) - FEEDS:
            raise ValueError("Unknown coverage source")
        for item in feeds.values():
            required = {"status", "known_at", "window_start", "window_end", "concepts", "evidence_reference"}
            if not isinstance(item, dict) or set(item) != required:
                raise ValueError("Coverage requires explicit dates, scope and evidence reference")
            if item["status"] not in {"unknown", "complete_for_scope", "delayed", "unavailable"}:
                raise ValueError("Invalid coverage status")
            for key in ("known_at", "window_start", "window_end"):
                date.fromisoformat(item[key])
            if item["window_start"] > item["window_end"]:
                raise ValueError("Reversed coverage window")
            if (not isinstance(item["concepts"], list) or not item["concepts"]
                    or any(c not in SUBJECTS for c in item["concepts"])):
                raise ValueError("Coverage concepts must be explicit supported subjects")
            if not isinstance(item["evidence_reference"], str) or not item["evidence_reference"].strip():
                raise ValueError("Coverage needs provenance")


def coverage_view(feeds, subject, as_of, window_start, declared):
    required = ["notes", "labs" if subject == "a1c" else "pharmacy_claims"]
    views = []
    for source in required:
        supplied = declared.get(source)
        status, reason = "unknown", "Feed records do not establish source completeness."
        if supplied:
            if supplied["known_at"] > as_of:
                reason = "Coverage declaration was unavailable at the cutoff."
            elif subject not in supplied["concepts"]:
                reason = "Coverage declaration does not cover this concept."
            elif supplied["window_start"] > window_start or supplied["window_end"] < as_of:
                reason = "Coverage declaration does not cover the whole requested period."
            else:
                status, reason = supplied["status"], "Caller-supplied coverage declaration; not independently verified."
        # Do not disclose future coverage metadata in a historical result.
        visible = supplied if supplied and supplied["known_at"] <= as_of else None
        views.append({"source": source, "status": status, "reason": reason,
                      "supplied_records": len(feeds[source]), "declaration": visible})
    return views


def collect_assertions(pid, feeds, subjects, reader, reader_revision):
    """Keep all recognized reasons, including those masked by judge precedence."""
    chart = reader.read_chart(feeds["notes"])
    notes = {n["note_id"]: n for n in feeds["notes"]}
    assertions = {}

    def add(subject, kind, source, record, event_date, detail, quote=None, context=None):
        if record.get("patient_id") != pid:
            raise ValueError("Assertion source has a different patient identity")
        ref = {"source": source, "source_hash": digest(record),
               "record_id": record.get("note_id", record.get("rx_id", digest(record))),
               "quote": quote, "start": None, "end": None}
        if quote is not None:
            start = record["text"].find(quote)
            if start < 0:
                return
            ref.update(start=start, end=start + len(quote))
        if context and context in record.get("text", ""):
            start = record["text"].find(context)
            ref["antecedent"] = {"quote": context, "start": start, "end": start + len(context)}
        a = {"patient_id": pid, "subject": subject, "kind": kind,
             "event_date": event_date, "available_date": record.get("available_date"),
             "event_date_basis": "document_date" if source == "notes" else "source_event_date",
             "detail": detail, "reference": ref, "reader_revision": reader_revision,
             "review_status": "unreviewed_synthetic", "probability": None}
        a["assertion_id"] = digest(a)
        assertions[a["assertion_id"]] = a

    for subject in sorted(subjects):
        for e in reader.evidence(chart, subject):
            if e["type"] not in KINDS or e.get("note_id") not in notes:
                continue
            n = notes[e["note_id"]]
            add(subject, KINDS[e["type"]], "notes", n, n["date"], e["type"], e["text"], e.get("prev"))
        if subject == "a1c":
            for record in feeds["labs"]:
                if record["test"] == "hba1c" and record.get("value") is not None:
                    add(subject, "result_record", "labs", record, record["result_date"],
                        {"test": record["test"], "value": record["value"], "unit": "not supplied"})
        else:
            for record in feeds["pharmacy_claims"]:
                if record["drug_class"] != subject:
                    continue
                kind = {"paid": "dispense_record", "reversed": "reversed_transaction",
                        "rejected": "rejected_transaction"}.get(record["status"])
                if kind:
                    add(subject, kind, "pharmacy_claims", record, record["fill_date"],
                        {"status": record["status"], "reject_code": record.get("reject_code")})
    return sorted(assertions.values(), key=lambda a: (a["event_date"], a["assertion_id"]))


def assess(pid, subject, assertions, feeds, declared, as_of):
    window = POLICY["lab_days" if subject == "a1c" else "medication_days"]
    start = (date.fromisoformat(as_of) - timedelta(days=window)).isoformat()
    relevant = [a for a in assertions if a["subject"] == subject]
    current = [a for a in relevant if start <= a["event_date"] <= as_of
               and (a["available_date"] is None or a["available_date"] <= as_of)]
    kinds = {a["kind"] for a in current}
    supplied = bool(kinds & {"dispense_record", "result_record", "reported_outside"})
    unresolved_treatment = bool(kinds & {"documented_exception", "documented_barrier", "reversed_transaction", "rejected_transaction"})
    if supplied and unresolved_treatment:
        state, action = "reconcile_timeline", "Review dated supply and barrier/exception evidence together; establish the current state."
    elif "documented_exception" in kinds:
        state, action = "exception_review", "Review the documented hold or intolerance and its current relevance; do not label it a missed treatment."
    elif "documented_barrier" in kinds:
        state, action = "access_review", "Verify current eligibility, patient preferences and the documented barrier before selecting an intervention."
    elif "reported_outside" in kinds and not kinds & {"dispense_record", "result_record"}:
        state, action = "outside_record_reconciliation", "Confirm the reported outside care through an authorized source; retrieve the original record if available."
    elif kinds & {"dispense_record", "result_record"}:
        state, action = "care_record_observed", "Use the dated record; a dispense does not establish ingestion, current use or an optimized dose."
    elif kinds & {"reversed_transaction", "rejected_transaction"}:
        state, action = "transaction_review", "Reconcile the transaction outcome; a reversal alone does not establish a financial barrier or noncompletion."
    else:
        state, action = "insufficient_evidence", "Resolve source coverage and outside care, then clarify whether care occurred or an exception applies."
    coverage = coverage_view(feeds, subject, as_of, start, declared)
    needs = []
    if state != "care_record_observed":
        for c in coverage:
            if c["status"] != "complete_for_scope":
                needs.append({"source": c["source"], "reason": c["reason"],
                              "operation": "clarify_source_coverage", "automatic_fetch": False})
        needs.append({"source": "patient_or_authorized_care_record", "reason": action,
                      "operation": "reconcile_evidence" if supplied else "clarify_current_state", "automatic_fetch": False})
    unknowns = ["Clinical eligibility, current dose/tolerance and appropriateness have not been independently validated."]
    if any(a["available_date"] is None for a in current):
        unknowns.append("Arrival times are absent for some supporting evidence; this is not a strict historical reconstruction.")
    if any(c["status"] != "complete_for_scope" for c in coverage):
        unknowns.append("The available sources do not establish complete capture for this question.")
    return {"patient_id": pid, "subject": subject, "state": state,
            "window_start": start, "as_of": as_of, "evidence": current,
            "older_assertion_ids": [a["assertion_id"] for a in relevant if a["event_date"] < start],
            "observability": coverage, "next_action": action, "information_needs": needs,
            "unknowns": unknowns, "confirmed_care_gap": False,
            "care_gap_probability": None, "automatic_outreach": False,
            "eligibility_basis": "inherited synthetic benchmark heuristic; review required",
            "policy": POLICY}


class ObservationStore:
    """Append-only assertions and dependency-keyed per-patient assessments."""

    def __init__(self, db):
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS agere_assertions (id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS agere_assessments (id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, payload TEXT NOT NULL)")
        db.commit()

    def build(self, patients, rows, as_of, reader, reader_revision, coverage=None):
        coverage = {} if coverage is None else coverage
        validate_coverage(coverage, patients)
        items, hits, misses = [], 0, 0
        implementation = digest(Path(__file__).read_text())
        with self.db:
            for row in rows:
                pid = row["patient_id"]
                subjects = sorted(set(row["answers"]) & SUBJECTS)
                if not subjects:
                    continue
                feeds = {k: patients[pid][k] for k in sorted(FEEDS)}
                # Readers receive only visible records, even for a direct store call.
                for source, event_key in (("notes", "date"), ("labs", "result_date"), ("pharmacy_claims", "fill_date")):
                    feeds[source] = [r for r in feeds[source] if r[event_key] <= as_of
                                     and (r.get("available_date") is None or r["available_date"] <= as_of)]
                declared = {k: v for k, v in coverage.get(pid, {}).items() if v["known_at"] <= as_of}
                key = digest({"patient": pid, "feeds": feeds, "subjects": subjects, "as_of": as_of,
                              "coverage": declared, "reader": reader_revision, "implementation": implementation,
                              "policy": POLICY})
                cached = self.db.execute("SELECT payload FROM agere_assessments WHERE id=?", (key,)).fetchone()
                if cached:
                    assessments = json.loads(cached[0])
                    hits += 1
                else:
                    assertions = collect_assertions(pid, feeds, subjects, reader, reader_revision)
                    assessments = [assess(pid, subject, assertions, feeds, declared, as_of) for subject in subjects]
                    for a in assertions:
                        self.db.execute("INSERT OR IGNORE INTO agere_assertions VALUES (?,?,?)",
                                        (a["assertion_id"], pid, json.dumps(a, sort_keys=True)))
                    self.db.execute("INSERT INTO agere_assessments VALUES (?,?,?)", (key, pid, json.dumps(assessments)))
                    misses += 1
                for item in assessments:
                    item["assessment_snapshot"] = key
                    item["practice"] = row["practice"]
                items.extend(assessments)
        by_site = {}
        for practice in sorted({a["practice"] for a in items}):
            subset = [a for a in items if a["practice"] == practice]
            by_site[practice] = {"patient_subject_pairs": len(subset), "states": dict(Counter(a["state"] for a in subset))}
        return {"schema_version": POLICY["version"], "synthetic": True, "items": items,
                "states": dict(Counter(a["state"] for a in items)), "by_practice": by_site,
                "patients_assessed": len({a["patient_id"] for a in items}),
                "patient_subject_pairs": len(items),
                "reuse": {"patient_hits": hits, "patient_misses": misses},
                "reader": "inherited lexicon; unreviewed synthetic extraction",
                "limitations": ["No calibrated clinical probability or automatic outreach.",
                                "Research date windows do not encode clinical due dates.",
                                "Temporal coexistence flags a reconciliation need, not a proven contradiction.",
                                "Assessment reuse is per patient, not a general dependency graph.",
                                "Assertion versions are preserved; automatic correction/supersession resolution remains to build."]}
