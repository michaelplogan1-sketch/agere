"""Reproducible research baseline from attributed synthetic evidence reviews.

No provider calls, downloads, automatic label generation or runtime promotion.
Review attribution is checked against the local ledger; it is not authentication.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sqlite3
import time
from collections import Counter
from datetime import date
from pathlib import Path

from .contracts import digest
from .corpus import family_split
from .inputs import DATE_FIELDS, FIELDS
from .learning import LABELS, VERSION

FEATURE_VERSION = "agere-reviewed-observation-features/1"


def feature_text(model_input):
    """Only observed allowlisted fields; never reviewer, proposal or target."""
    cutoff = date.fromisoformat(model_input["as_of"])
    records = []
    for evidence in model_input["evidence"]:
        source, record = evidence["source"], evidence["record"]
        if source not in {"notes", "labs", "pharmacy_claims"}:
            raise ValueError("Unsupported evidence source")
        allowed = set(FIELDS[source]) | {"available_date"}
        allowed -= {"patient_id", "note_id", "rx_id", "author", "claim_id"}
        if set(record) - allowed:
            raise ValueError("Unexpected evidence fields; refusing possible label or identity leakage")
        event = date.fromisoformat(record[DATE_FIELDS[source]])
        arrival = record.get("available_date")
        if event > cutoff or (arrival and date.fromisoformat(arrival) > cutoff):
            raise ValueError("Future or late evidence in training input")
        records.append({"source": source, "record": record})
    return json.dumps({"subject": model_input["subject"], "as_of": model_input["as_of"],
                       "window_start": model_input["window_start"],
                       "source_coverage": model_input["source_coverage"],
                       "records": records}, sort_keys=True, allow_nan=False)


def inspect_export(dataset: Path, database: Path):
    """Verify an export against a read-only, transaction-consistent review ledger."""
    dataset, database = Path(dataset), Path(database)
    manifest = json.loads((dataset / "manifest.json").read_text())
    if manifest.get("schema_version") != VERSION or manifest.get("synthetic") is not True:
        raise ValueError("Only synthetic reviewed exports are supported")
    rows = {"train": [], "calibration": []}
    features, tasks = set(), set()
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        for split in rows:
            name = split + ".jsonl"
            content = (dataset / name).read_text()
            declaration = manifest["files"][name]
            if digest(content) != declaration["content_digest"]:
                raise ValueError("Export content digest mismatch")
            items = [json.loads(line) for line in content.splitlines() if line.strip()]
            if len(items) != declaration["rows"] or len(items) != manifest["counts"][split]:
                raise ValueError("Export count mismatch")
            for item in items:
                task_id = item["task_id"]
                task_row = db.execute("SELECT payload FROM learning_tasks WHERE id=?", (task_id,)).fetchone()
                if not task_row or task_id in tasks:
                    raise ValueError("Unknown or repeated review task")
                tasks.add(task_id)
                task = json.loads(task_row[0])
                if digest({k: v for k, v in task.items() if k != "task_id"}) != task_id:
                    raise ValueError("Task content hash mismatch")
                if task.get("synthetic") is not True or task.get("schema_version") != VERSION:
                    raise ValueError("Unexpected task schema")
                origin = task["origin"]
                if (origin["world"] not in {"dev", "hard_dev"} or item["split"] != split
                        or origin["split"] != split or family_split(origin["family"]) != split):
                    raise ValueError("Development-world or family partition violation")
                latest = db.execute(
                    "SELECT r.revision,r.payload FROM learning_reviews r WHERE r.task_id=? "
                    "AND NOT EXISTS (SELECT 1 FROM learning_reviews s WHERE s.previous=r.revision)",
                    (task_id,)).fetchall()
                if len(latest) != 1:
                    raise ValueError("Missing or ambiguous review")
                revision, payload = latest[0]
                review = json.loads(payload)
                if (digest(review) != revision or review["reviewer_kind"] != "human"
                        or review["decision"] not in {"accept", "correct"}):
                    raise ValueError("Excluded or unreviewed target")
                expected_target = {"state": review["label"], "support_ids": review["support_ids"]}
                expected_provenance = {
                    "origin": origin, "source_map": task["source_map"], "review_revision": revision,
                    "reviewer": review["reviewer"], "reviewed_at": review["reviewed_at"],
                    "rationale": review["rationale"], "reviewer_identity": "caller-attributed; not authenticated"}
                if (item["input"] != task["input"] or item["target"] != expected_target
                        or item["provenance"] != expected_provenance):
                    raise ValueError("Export does not match the latest attributed review")
                refs = {e["id"] for e in item["input"]["evidence"]}
                label, support = review["label"], review["support_ids"]
                if (label not in LABELS or not isinstance(support, list)
                        or len(support) != len(set(support)) or set(support) - refs
                        or (label != "insufficient_evidence" and not support)):
                    raise ValueError("Invalid reviewed label or support")
                text = feature_text(item["input"])
                fingerprint = digest(text)
                if fingerprint in features:
                    raise ValueError("Repeated model features within or across splits")
                features.add(fingerprint)
                rows[split].append({**item, "feature_text": text})
    counts = {s: dict(Counter(r["target"]["state"] for r in values)) for s, values in rows.items()}
    missing = sorted(set(counts["calibration"]) - set(counts["train"]))
    blockers = []
    if len(counts["train"]) < 2:
        blockers.append("Need reviewed training examples from at least two evidence states.")
    if not rows["calibration"]:
        blockers.append("Need reviewed calibration-family examples; do not borrow training families.")
    if missing:
        blockers.append("Calibration states absent from training: " + ", ".join(missing))
    report = {
        "schema_version": "agere-reviewed-training/1", "synthetic": True,
        "status": "blocked" if blockers else "ready_for_research_fit", "blockers": blockers,
        "counts": {s: len(v) for s, v in rows.items()}, "states": counts,
        "families": {s: len({r["provenance"]["origin"]["family"] for r in v}) for s, v in rows.items()},
        "training_states_absent_from_calibration": sorted(set(counts["train"]) - set(counts["calibration"])),
        "manifest_digest": digest(manifest), "feature_version": FEATURE_VERSION,
        "model_weights_trained": False, "runtime_promoted": False,
        "limitations": [
            "Readiness is a structural check, not enough data for clinical validity.",
            "Reviewers are caller-attributed; credentials and label correctness are not authenticated.",
            "Only exact feature duplicates are rejected; templates and near-duplicates may still overlap.",
            "The model predicts evidence-review states, not treatment eligibility or benefit.",
            "Support references are audited targets; this classifier does not learn evidence-span attribution.",
            "Calibration-family metrics are development feedback, not a final held-out benchmark."]}
    return report, rows


def train(dataset: Path, database: Path, output: Path):
    report, rows = inspect_export(dataset, database)
    if report["blockers"]:
        return report
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a new output directory; model revisions are immutable")
    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, classification_report
    from sklearn.pipeline import Pipeline
    from threadpoolctl import threadpool_limits

    started = time.perf_counter()
    model = Pipeline([
        ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                 max_features=40000, sublinear_tf=True)),
        ("classifier", LogisticRegression(class_weight="balanced", max_iter=1000, random_state=173))])
    with threadpool_limits(limits=4):
        model.fit([r["feature_text"] for r in rows["train"]], [r["target"]["state"] for r in rows["train"]])
        predictions = model.predict([r["feature_text"] for r in rows["calibration"]])
    truth = [r["target"]["state"] for r in rows["calibration"]]
    report.update({
        "status": "research_model_fitted", "model_weights_trained": True,
        "architecture": "Character TF-IDF + balanced logistic regression",
        "calibration_agreement": {"accuracy": float(accuracy_score(truth, predictions)),
                                  "per_state": classification_report(truth, predictions, labels=sorted(LABELS),
                                                                    output_dict=True, zero_division=0)},
        "fit_and_prediction_seconds": time.perf_counter() - started,
        "features": len(model["tfidf"].vocabulary_), "external_model_calls": 0,
        "paid_api_usd": 0, "total_operating_cost_usd": None,
        "cost_basis": "Local wall time measured; hardware, power, storage and review cost not priced.",
        "training_task_ids": [r["task_id"] for r in rows["train"]],
        "calibration_task_ids": [r["task_id"] for r in rows["calibration"]],
        "dependencies": {m: importlib.metadata.version(m) for m in ("scikit-learn", "numpy", "scipy", "joblib")}})
    output.mkdir(parents=True, exist_ok=False)
    joblib.dump(model, output / "model.joblib", compress=3)
    report["model_sha256"] = hashlib.sha256((output / "model.joblib").read_bytes()).hexdigest()
    (output / "model_card.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "calibration_predictions.jsonl").write_text("".join(json.dumps({
        "task_id": row["task_id"], "reviewed_state": row["target"]["state"], "predicted_state": prediction,
        "requires_review": True}) + "\n" for row, prediction in zip(rows["calibration"], predictions)))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Audit only; never fit weights")
    parser.add_argument("--output", type=Path, help="New model directory (required unless --check)")
    args = parser.parse_args()
    if not args.check and args.output is None:
        parser.error("--output is required for fitting")
    try:
        result = inspect_export(args.dataset, args.database)[0] if args.check else train(args.dataset, args.database, args.output)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "invalid_input", "error": str(exc), "model_weights_trained": False}))
        return 2
    print(json.dumps(result, indent=2))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
