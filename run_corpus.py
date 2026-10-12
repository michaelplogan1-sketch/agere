"""Run every available synthetic world through the local baseline and question layer."""
import argparse
import json
from collections import Counter
from pathlib import Path

from clinical_core.bridge import InsightBridge
from clinical_core.corpus import inventory, load_world, partition_ids
from clinical_core.questions import answer, catalog
from clinical_core.runtime import Runtime
from clinical_core.world_evaluation import evaluate_world


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path, default=root.parent / "insight-engine")
    parser.add_argument("--output", type=Path, default=root / "artifacts/corpus")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = inventory(args.engine_root)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    partitions = {w["name"]: {purpose: partition_ids(manifest, w["name"], purpose)
                              for purpose in (("train", "calibration", "test") if w["role"] == "development" else ("test",))}
                  for w in manifest["worlds"]}
    (args.output / "partitions.json").write_text(json.dumps(partitions, indent=2))
    bridge = InsightBridge(args.engine_root)
    runtime = Runtime(args.output / "runs.sqlite3", bridge)
    summaries = []
    questions = [q for g in catalog()["groups"] for q in g["questions"]]
    try:
        for world in manifest["worlds"]:
            target = args.output / world["name"]
            target.mkdir(exist_ok=True)
            batch = load_world(Path(world["path"]), "2026-09-30")
            result = runtime.run(batch)
            evaluation = evaluate_world(result["patient_results"], result["insight_cards"], Path(world["path"]), batch.as_of)
            responses = {q["id"]: answer(q["id"], result, batch.patients) for q in questions}
            (target / "run.json").write_text(json.dumps(result))
            (target / "evaluation.json").write_text(json.dumps(evaluation, indent=2))
            (target / "answers.json").write_text(json.dumps(responses))
            item = {"world": world["name"], "patients": len(batch.patients), "run_id": result["run_id"],
                    "notes": world["counts"]["notes"], "patient_scoring": evaluation["patients"],
                    "population_scoring": {k: v for k, v in evaluation["population"].items() if k != "detail"},
                    "runtime_seconds": result["telemetry"]["original_compute_seconds"]}
            summaries.append(item)
            print(json.dumps({"completed": world["name"], "patients": item["patients"], "notes": item["notes"]}), flush=True)
            # Release the previous world's large in-memory structures before loading the next.
            del batch, result, responses
    finally:
        runtime.close()
    report = {"synthetic": True, "worlds": summaries, "question_count": len(questions),
              "question_coverage": dict(Counter(q["execution"]["status"] for q in questions)),
              "total_patient_world_instances": manifest["patient_world_instances"],
              "total_note_world_instances": manifest["note_world_instances"],
              "model_training_performed": False, "hosted_calls": 0}
    (args.output / "summary.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "worlds"}, indent=2))


if __name__ == "__main__":
    main()
