"""Evaluation-only access to full-world synthetic truth."""
import json
from pathlib import Path

from .corpus import jsonl


def evaluate_world(rows, cards, world: Path, as_of: str):
    from ie.bench.score import score_patients, score_population
    metadata = json.loads((world / "truth/planted.json").read_text())
    if as_of != metadata["as_of"]:
        raise ValueError("World truth cutoff does not match inference cutoff")
    truth = {r["id"]: r for r in jsonl(world / "truth/patients.jsonl")}
    if {r["patient_id"] for r in rows} != set(truth):
        raise ValueError("Population does not match full-world truth")
    # Scorer annotates cards; never mutate inference outputs with oracle fields.
    import copy
    return {"synthetic": True, "patients": score_patients(rows, truth),
            "population": score_population(copy.deepcopy(cards), truth, metadata["planted"]),
            "scope": "Full existing synthetic world; not clinical or vendor-product validation"}
