"""Agere's conditional-decision research layer over saved synthetic evidence.

The supplied workflow is fictional, not a clinical guideline or CMS measure.
Inherited reader outputs remain proposals. There are no automatic model calls,
source queries, care recommendations or acceptance of unreviewed clinical facts.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .contracts import digest
from .decision_kernel import DecisionProgram
from .dependency_cache import DependencyCache


def workflow_program():
    return {"id": "synthetic-opportunity-workflow", "version": "1", "synthetic": True,
        "effective_from": "2026-01-01", "effective_to": "2026-12-31",
        "authority": "Agere engineering research fixture; not a clinical or payer specification",
        "facts": {
            "eligible": {"question": "Has eligibility for this specific intervention and time window been confirmed?", "source": "reviewed_rule_and_required_patient_inputs"},
            "care_current": {"question": "Has relevant care or current treatment been reconciled and confirmed?", "source": "authorized_care_record_or_clinical_review"},
            "appropriate_exception": {"question": "Is there a current, reviewed appropriate exception?", "source": "clinical_review_of_exception_and_timing"},
            "access_barrier": {"question": "Has a current barrier to the indicated care been established?", "source": "patient_or_verified_access_record"}},
        "constraints": [],
        "branches": [
            {"when": {"not": {"fact": "eligible"}}, "outcome": "not_applicable_under_this_workflow"},
            {"when": {"fact": "care_current"}, "outcome": "care_addressed_review_monitoring"},
            {"when": {"fact": "appropriate_exception"}, "outcome": "exception_review_path"},
            {"when": {"fact": "access_barrier"}, "outcome": "barrier_support_review_path"}],
        "default": "clinical_opportunity_review_path"}


def unknown_fact():
    return {"value": None, "status": "unknown", "available_date": None,
            "valid_from": None, "valid_to": None, "support_ids": []}


def observation_proposals(assessment):
    """Suggestions preserve provenance; they never become accepted Boolean facts."""
    facts = {name: unknown_fact() for name in workflow_program()["facts"]}
    kind_map = {"care_current": {"reported_outside", "dispense_record", "result_record"},
                "appropriate_exception": {"documented_exception"}, "access_barrier": {"documented_barrier"}}
    for name, kinds in kind_map.items():
        evidence = [a for a in assessment["evidence"] if a["kind"] in kinds]
        if evidence:
            facts[name] = {**unknown_fact(), "value": True, "status": "proposal",
                           "support_ids": [a["assertion_id"] for a in evidence]}
    return facts


def analyze_case(cache, *, scope, program, observations, as_of, acquisition=None):
    """Reusable API for other synthetic generators to supply dated fact contracts."""
    policy = cache.source("shared-policy", "policy:" + program["id"], program)
    facts = cache.source(scope, "facts", observations)
    costs = cache.source(scope, "acquisition", acquisition or {})
    revision = digest({"kernel": Path(__file__).with_name("decision_kernel.py").read_text(), "adapter": Path(__file__).read_text()})
    node = cache.resolve(scope, "decision-analysis", revision,
        {"policy": policy, "facts": facts, "acquisition": costs}, {"as_of": as_of},
        lambda deps, params: DecisionProgram(deps["policy"]).analyze(deps["facts"], params["as_of"], deps["acquisition"]))
    return {"analysis": node.value, "node_id": node.node_id, "fact_node_id": facts.node_id,
            "policy_node_id": policy.node_id, "dependencies": cache.lineage(node.node_id)}


def analyze_run(run, cache, *, namespace="legacy-synthetic"):
    if run.get("synthetic") is not True or "agere" not in run:
        raise ValueError("Expected a saved synthetic Agere run")
    before_hits, before_misses = cache.hits, cache.misses
    program = workflow_program()
    revision = digest(Path(__file__).read_text())
    items = []
    for assessment in run["agere"]["items"]:
        scope = "synthetic:" + digest({"namespace": namespace, "patient": assessment["patient_id"], "subject": assessment["subject"]})
        source = cache.source(scope, "evidence-assessment", assessment)
        proposed = cache.resolve(scope, "fact-proposals", revision, {"assessment": source}, {},
                                 lambda deps, params: observation_proposals(deps["assessment"]))
        # Keep the extraction node in the graph rather than flattening provenance
        # into an unrelated source node. Unchanged outputs remain versioned by
        # their actual evidence dependencies.
        policy = cache.source("shared-policy", "policy:" + program["id"], program)
        kernel_revision = digest(Path(__file__).with_name("decision_kernel.py").read_text())
        decision = cache.resolve(scope, "decision-analysis", kernel_revision,
            {"policy": policy, "facts": proposed}, {"as_of": assessment["as_of"]},
            lambda deps, params: DecisionProgram(deps["policy"]).analyze(deps["facts"], params["as_of"]))
        items.append({"patient_id": assessment["patient_id"], "subject": assessment["subject"],
            "observed_evidence_state": assessment["state"], "analysis": decision.value,
            "evidence_node_id": source.node_id, "proposal_node_id": proposed.node_id,
            "decision_node_id": decision.node_id})
    return {"schema_version": "agere-conditional-decisions/1", "synthetic": True,
            "source_run_id": run["run_id"], "program": program, "items": items,
            "summary": dict(Counter(item["analysis"]["status"] for item in items)),
            "compute": {"cache_hits": cache.hits-before_hits, "operators_executed": cache.misses-before_misses,
                        "external_model_calls": 0},
            "scope": "Research workflow possibilities and information needs, not inferred patient truth",
            "limitations": ["No inherited lexical extraction is promoted to a reviewed clinical fact.",
                            "Unknown source arrival and current relevance remain unresolved.",
                            "Fact acquisition availability, success and cost are not estimated from this corpus.",
                            "This layer does not change benchmark outputs or existing clinical review assessments."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--run", type=Path, help="Saved Agere synthetic run")
    inputs.add_argument("--case", type=Path, help="Explicit synthetic scope/program/observations/as_of/acquisition JSON")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cache = DependencyCache(args.database)
    try:
        if args.run:
            result = analyze_run(json.loads(args.run.read_text()), cache)
        else:
            case = json.loads(args.case.read_text())
            result = analyze_case(cache, **case)
            result["compute"] = {"cache_hits": cache.hits, "operators_executed": cache.misses, "external_model_calls": 0}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2))
        print(json.dumps({"output": str(args.output), "compute": result["compute"],
                          "summary": result.get("summary", result.get("analysis", {}).get("status"))}))
    finally:
        cache.close()


if __name__ == "__main__":
    main()
