"""Fit a finite patient/source model to development simulator mechanisms only."""
import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path

from clinical_core.bridge import InsightBridge
from clinical_core.contracts import digest
from clinical_core.corpus import family_mapping, family_split, load_world
from clinical_core.joint_observation import AXES, SUBJECTS, fit_joint, infer_joint, observed_features
from clinical_core.runtime import Runtime

ROOT = Path(__file__).resolve().parent


def collect(engine, database):
    families = family_mapping(json.loads((engine / "data/source/synthetic_patients.json").read_text()))
    runtime = Runtime(database, InsightBridge(engine))
    rows = {"train": [], "calibration": []}
    exclusions = Counter()
    try:
        for world in ("dev", "hard_dev"):
            batch = load_world(engine / "data/worlds" / world, "2026-09-30")
            run = runtime.run(batch, scope="joint-training-" + world)
            # Supervision is read only here, after observed inference. No truth
            # object is passed to Runtime or to observed_features.
            truth = {r["id"]: r for r in map(json.loads, (engine / "data/worlds" / world / "truth/patients.jsonl").read_text().splitlines())}
            for assessment in run["agere"]["items"]:
                if assessment["subject"] not in SUBJECTS:
                    continue
                pid, subject = assessment["patient_id"], assessment["subject"]
                family = families[pid]
                split = family_split(family)
                if split == "test":
                    exclusions["reserved_test_family"] += 1
                    continue
                status = truth[pid]["status"].get(subject)
                if status is None:
                    exclusions["simulator_target_not_defined"] += 1
                    continue
                if status not in {"on", "on_outside"} and not status.startswith("gap:"):
                    raise ValueError("Unknown simulator medication status")
                target = {"care": "therapy_present" if status in {"on", "on_outside"} else "therapy_absent",
                          "channel": "outside" if status == "on_outside" else "no_outside_marker",
                          "documentation": "sparse" if truth[pid]["flags"]["low_doc"] else "usual"}
                rows[split].append({"id": digest([world, pid, subject]), "family": family, "world": world,
                                    "subject": subject, "features": observed_features(assessment), "target": target})
    finally:
        runtime.close()
    assert not ({r["family"] for r in rows["train"]} & {r["family"] for r in rows["calibration"]})
    return rows, dict(exclusions)


def evaluate(model, rows):
    correct = {axis: 0 for axis in AXES}
    joint_correct, brier, logloss, unseen = 0, 0.0, 0.0, 0
    for row in rows:
        result = infer_joint(model, row["features"])
        states = result["joint_distribution"]
        joint_correct += max(states, key=lambda s: s["probability"])["state"] == row["target"]
        for axis in AXES:
            correct[axis] += max(result["marginals"][axis], key=result["marginals"][axis].get) == row["target"][axis]
        target = next((s for s in states if s["state"] == row["target"]), None)
        if target is None:
            unseen += 1
        probability = target["probability"] if target else 0.0
        logloss -= math.log(max(probability, 1e-15))
        brier += sum((s["probability"] - int(s["state"] == row["target"])) ** 2 for s in states) + int(target is None)
    n = len(rows)
    return {"rows": n, "joint_accuracy": joint_correct / n, "axis_accuracy": {k: v / n for k, v in correct.items()},
            "multiclass_brier": brier / n, "log_loss_clipped_at_1e_15": logloss / n, "unseen_target_states": unseen,
            "scope": "Calibration-family agreement with simulator mechanisms, not reviewed clinical accuracy"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=ROOT.parent / "insight-engine")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/agere-joint-v1")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new output directory")
    args.output.mkdir(parents=True)
    started = time.perf_counter()
    rows, exclusions = collect(args.engine, args.output / "observed-runs.sqlite3")
    # Multiple world variants of a source family stay in the same partition.
    model = fit_joint(rows["train"])
    report = {"synthetic": True, "model_weights_trained": True,
              "architecture": "Joint-state prior plus conditionally independent Bernoulli observations",
              "supervision": "Development simulator medication/channel/documentation mechanisms; not human clinical labels",
              "counts": {s: len(v) for s, v in rows.items()},
              "families": {s: len({r['family'] for r in v}) for s, v in rows.items()},
              "exclusions": exclusions, "calibration": evaluate(model, rows["calibration"]),
              "limitations": model["assumptions"] + [
                  "Target availability restricts the training cohort; unmodeled eligibility is not inferred.",
                  "No outside marker is a simulator label, not proof of complete local capture.",
                  "Therapy absent includes appropriate holds; it is not a care gap.",
                  "Features still use the inherited, imperfect clinical reader.",
                  "Family-held-out template language can overlap; no real-world generalization established."],
              "external_model_calls": 0, "paid_api_usd": 0, "total_operating_cost_usd": None}
    # A simpler discriminative comparator uses exactly the same observed features.
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score
    labels = ["|".join(r["target"][a] for a in AXES) for r in rows["train"]]
    control = LogisticRegression(max_iter=1000, random_state=173).fit([r["features"] for r in rows["train"]], labels)
    predicted = control.predict([r["features"] for r in rows["calibration"]])
    target = ["|".join(r["target"][a] for a in AXES) for r in rows["calibration"]]
    report["logistic_control_joint_accuracy"] = float(accuracy_score(target, predicted))
    report["elapsed_seconds"] = time.perf_counter() - started
    for split, values in rows.items():
        (args.output / (split + "-supervision.jsonl")).write_text("".join(json.dumps(r) + "\n" for r in values))
    (args.output / "joint-model.json").write_text(json.dumps(model, indent=2) + "\n")
    (args.output / "training-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
