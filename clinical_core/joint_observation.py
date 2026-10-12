"""Finite joint patient/recording-state research model with explicit assumptions.

Parameters may be fitted to separated simulator labels. Inference accepts only
observed feature vectors. Outputs are simulator-conditioned hypotheses, never
validated clinical probabilities or an authorization to act.
"""
from __future__ import annotations

import math
from collections import Counter

from .contracts import digest

FEATURES = ("dispense_record", "reported_outside", "documented_exception", "documented_barrier",
            "rejected_transaction", "reversed_transaction", "older_evidence", "no_current_evidence",
            "multiple_current_kinds", "notes_present", "pharmacy_present", "labs_present",
            "unknown_arrivals", "coverage_unknown", "subject_mra", "subject_sglt2", "subject_arni", "subject_bb")
SUBJECTS = {"mra", "sglt2", "arni", "bb"}
AXES = ("care", "channel", "documentation")


def observed_features(assessment):
    """Boolean source-supported features reduce copied-note vote multiplication."""
    if assessment["subject"] not in SUBJECTS:
        raise ValueError("Joint prototype supports the four HF medication subjects only")
    kinds = {e["kind"] for e in assessment["evidence"]}
    coverage = {v["source"]: v for v in assessment["observability"]}
    values = {name: name in kinds for name in FEATURES[:6]}
    values.update(older_evidence=bool(assessment["older_assertion_ids"]),
                  no_current_evidence=not kinds, multiple_current_kinds=len(kinds) > 1,
                  unknown_arrivals=any(e["available_date"] is None for e in assessment["evidence"]),
                  coverage_unknown=any(v["status"] != "complete_for_scope" for v in coverage.values()))
    for name, source in (("notes_present", "notes"), ("pharmacy_present", "pharmacy_claims"), ("labs_present", "labs")):
        values[name] = bool(coverage.get(source, {}).get("supplied_records", 0))
    for subject in SUBJECTS:
        values["subject_" + subject] = assessment["subject"] == subject
    return [int(values[name]) for name in FEATURES]


def checked_features(x):
    if len(x) != len(FEATURES) or any(type(v) not in (int, bool) or v not in (0, 1) for v in x):
        raise ValueError("Expected the versioned observed binary feature vector")
    return x


def fit_joint(rows, *, alpha=1.0):
    """Smoothed joint-state prior + conditional Bernoulli likelihoods.

    Conditional independence of features is a simplifying, imperfect assumption.
    This is an established generative model, not a newly invented theorem.
    """
    if not rows or not math.isfinite(alpha) or alpha <= 0:
        raise ValueError("Nonempty training rows and positive smoothing required")
    states = sorted({tuple(r["target"][axis] for axis in AXES) for r in rows})
    counts = Counter(tuple(r["target"][axis] for axis in AXES) for r in rows)
    sums = {s: [0] * len(FEATURES) for s in states}
    collisions = {}
    for r in rows:
        x = checked_features(r["features"])
        s = tuple(r["target"][axis] for axis in AXES)
        sums[s] = [a + b for a, b in zip(sums[s], x)]
        key = digest(x)
        collisions.setdefault(key, Counter())[states.index(s)] += 1
    model = {"schema_version": "agere-joint-observation/1", "features": list(FEATURES), "axes": list(AXES),
             "alpha": alpha, "training_rows": len(rows),
             "states": [{"state": dict(zip(AXES, s)), "support": counts[s],
                         "prior": (counts[s] + alpha) / (len(rows) + alpha * len(states)),
                         "likelihood": [(v + alpha) / (counts[s] + 2 * alpha) for v in sums[s]]} for s in states],
             "feature_equivalence_counts": {k: {str(s): n for s, n in v.items()} for k, v in collisions.items()},
             "assumptions": ["Conditional Bernoulli feature independence given joint state.",
                             "Training simulator relationships may not transfer to another source or time.",
                             "Only joint states represented in training receive probability mass.",
                             "Correlated evidence can make this model overconfident; no probability calibration claimed."]}
    model["model_id"] = digest(model)
    return model


def infer_joint(model, features):
    if (model.get("schema_version") != "agere-joint-observation/1" or model["features"] != list(FEATURES)
            or digest({k: v for k, v in model.items() if k != "model_id"}) != model["model_id"]):
        raise ValueError("Joint model version or content mismatch")
    x = checked_features(features)
    logits = [math.log(s["prior"]) + sum(math.log(p if v else 1 - p)
               for v, p in zip(x, s["likelihood"])) for s in model["states"]]
    pivot = max(logits)
    weights = [math.exp(v - pivot) for v in logits]
    total = sum(weights)
    probabilities = [w / total for w in weights]
    marginals = {axis: {} for axis in AXES}
    for entry, probability in zip(model["states"], probabilities):
        for axis in AXES:
            value = entry["state"][axis]
            marginals[axis][value] = marginals[axis].get(value, 0.0) + probability
    equivalence = model["feature_equivalence_counts"].get(digest(x), {})
    return {"model_id": model["model_id"], "status": "simulator_conditioned_hypothesis",
            "joint_distribution": [{"state": s["state"], "probability": p, "training_support": s["support"]}
                                   for s, p in zip(model["states"], probabilities)],
            "marginals": marginals,
            "entropy_nats": -sum(p * math.log(p) for p in probabilities if p),
            "matching_training_rows": sum(equivalence.values()),
            "distinct_states_with_same_observed_features": len(equivalence),
            "clinical_probability_calibrated": False, "automatic_action": False,
            "care_gap_established": False,
            "interpretation": "Finite simulator-trained hypotheses; multiple hidden states can produce the same observations."}
