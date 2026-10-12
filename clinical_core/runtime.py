"""Content-addressed local runs with persistent outputs and honest telemetry."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from .contracts import SourceBatch, digest
from .observation import ObservationStore, validate_coverage


def runtime_revision() -> str:
    return digest({p.name: p.read_text() for p in sorted(Path(__file__).parent.glob("*.py"))})


def link_note_evidence(rows: list, patients: dict) -> list:
    """Resolve exact quotes only. Matching text is provenance, not entailment."""
    result = []
    for row in rows:
        for subject, answer in row["answers"].items():
            for quote in answer.get("evidence", []):
                matches = []
                for note in patients[row["patient_id"]]["notes"]:
                    start = note["text"].find(quote) if quote else -1
                    if start >= 0:
                        matches.append({"note_id": note["note_id"], "date": note["date"], "start": start,
                                        "end": start + len(quote), "document_hash": digest(note)})
                result.append({"patient_id": row["patient_id"], "subject": subject, "quote": quote,
                               "status": "exact_quote_found" if matches else "unresolved_quote",
                               "occurrences": matches})
    return result


class Runtime:
    def __init__(self, database: Path, bridge):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, result TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS snapshots (snapshot_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.db.commit()
        self.bridge = bridge
        self.observations = ObservationStore(self.db)

    def close(self):
        self.db.close()

    def run(self, batch: SourceBatch, *, reader=None, spec=None, source_coverage=None, scope="local-synthetic-demo") -> dict:
        if digest({"patients": batch.patients, "as_of": batch.as_of}) != batch.snapshot_id:
            raise ValueError("Source batch changed after snapshot creation")
        if reader is not None and spec is None:
            raise ValueError("Custom readers require an explicit versioned ReaderSpec")
        if spec is not None and reader is None:
            raise ValueError("ReaderSpec requires its matching reader implementation")
        selected = spec or self.bridge.spec
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("Explicit runtime scope required")
        source_coverage = {} if source_coverage is None else source_coverage
        validate_coverage(source_coverage, batch.patients)
        if selected.network_required:
            raise ValueError("This local baseline does not allow network-dependent readers")
        request = {"schema_version": "clinical-core/0.2", "snapshot_id": batch.snapshot_id,
                   "source_coverage_revision": digest(source_coverage),
                   "source_kind": batch.source_kind,
                   "as_of": batch.as_of, "reader": asdict(selected), "engine_revision": self.bridge.version,
                   "runtime_revision": runtime_revision(), "scope": scope}
        run_id = digest(request)
        cached = self.db.execute("SELECT result FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if cached:
            result = json.loads(cached[0])
            result["telemetry"] = dict(result["telemetry"], cache_hit=True, current_compute_seconds=0.0)
            result["telemetry"]["current_model_calls"] = 0
            result["coverage"]["excluded_future_records"] = batch.excluded_future_records
            result["coverage"]["excluded_late_records"] = batch.excluded_late_records
            result["coverage"]["arrival_time_missing_records"] = batch.arrival_time_missing_records
            result["agere"]["reuse"] = {"patient_hits": 0, "patient_misses": 0, "whole_run_hit": True}
            return result
        start = time.perf_counter()
        rows, cards = self.bridge.analyze(batch.patients, batch.as_of, reader)
        interpreted = time.perf_counter()
        refs = link_note_evidence(rows, batch.patients)
        baseline_rows, _ = self.bridge.structured(batch.patients, batch.as_of)
        agere = self.observations.build(batch.patients, rows, batch.as_of, self.bridge.reader,
                                       self.bridge.version, source_coverage)
        elapsed = time.perf_counter() - start
        is_lexicon = reader is None
        result = {"run_id": run_id, "request": request, "synthetic": True,
                  "coverage": {"patients": len(batch.patients), "evaluated": len(rows),
                               "population_scope": "full supplied synthetic world" if batch.source_kind == "synthetic_world" else "provided demo sample, not the full benchmark world",
                               "excluded_future_records": batch.excluded_future_records,
                               "excluded_late_records": batch.excluded_late_records,
                               "arrival_time_missing_records": batch.arrival_time_missing_records,
                               "source_availability": "Feed arrays supplied; real-world completeness is unverified"},
                  "patient_results": rows, "insight_cards": cards, "note_evidence": refs,
                  "agere": agere,
                  "structured_comparator_results": baseline_rows,
                  "telemetry": {"cache_hit": False, "current_compute_seconds": elapsed,
                                "original_compute_seconds": elapsed,
                                "interpretation_and_discovery_seconds": interpreted-start,
                                "model_calls": 0 if is_lexicon else None,
                                "external_model_calls": 0 if is_lexicon else None,
                                "cost_usd": None, "cost_status": "not measured; local execution is not free"},
                  "limitations": ["Synthetic research integration; not a validated clinical system.",
                                   "Lexicon and synthetic clinical heuristics are inherited from the benchmark.",
                                   "Inherited confidence values are heuristic, not calibrated probabilities.",
                                   "No documented offer does not establish that treatment was never offered.",
                                   "Synthetic population patterns do not establish real-world effects.",
                                   "Quote matching establishes location, not clinical correctness.",
                                   "Known future arrival dates are excluded; missing arrival times remain unknown.",
                                   "Benchmark patient_results and insight_cards retain legacy heuristics; Agere assessments are separate.",
                                   "Agere evidence reconciliation currently uses the inherited lexicon, including when a custom benchmark reader is selected.",
                                   "SQLite caching is local/synthetic only; no production tenant authorization."]}
        if reader is not None and hasattr(reader, "telemetry"):
            result["telemetry"].update(reader.telemetry())
            result["model_traces"] = reader.traces
        result["telemetry"]["current_model_calls"] = result["telemetry"]["model_calls"]
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?)",
                            (batch.snapshot_id, json.dumps(batch.patients, sort_keys=True)))
            self.db.execute("INSERT INTO runs VALUES (?,?)", (run_id, json.dumps(result, sort_keys=True)))
        return result
