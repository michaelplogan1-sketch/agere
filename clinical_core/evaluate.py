"""Offline scoring only. This module is never passed into a reader."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

from .inputs import load_sample


def evaluate_sample(result: dict, sample_path: Path) -> dict:
    if result["request"]["as_of"] != "2026-09-30":
        raise ValueError("The supplied demo truth is only valid at its 2026-09-30 cutoff")
    batch = load_sample(sample_path, result["request"]["as_of"])
    if batch.snapshot_id != result["request"]["snapshot_id"]:
        raise ValueError("Scoring source snapshot does not match the inference run")
    source = json.loads(sample_path.read_text())
    truth = {r["id"]: dict(r["truth"], id=r["id"]) for r in source}
    ids = {r["patient_id"] for r in result["patient_results"]}
    if ids != set(truth):
        raise ValueError("Scoring population does not match the inference run")
    score = importlib.import_module("ie.bench.score")
    return {"run_id": result["run_id"], "synthetic": True,
            "scope": "selected demo sample; not a new full-world or clinical validation result",
            "reader": score.score_patients(result["patient_results"], truth),
            "structured_comparator": score.score_patients(result["structured_comparator_results"], truth),
            "population_scoring": "not performed; full-world materialization truth is not present",
            "comparator": "structured-data architecture control, not the TextQL vendor product"}
