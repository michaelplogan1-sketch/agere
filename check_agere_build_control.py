"""Read-only continuity preflight; NOT a product acceptance or quality evaluator."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib


def backlog_text(queue):
    return queue.split("## Preserved full-product acceptance backlog\n", 1)[1].split(
        "## Historical evidence", 1
    )[0]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(root, control, owner, automation, intent="review", gate=None):
    errors = []

    def require(condition, message):
        if not condition:
            errors.append(message)

    def local_file(value):
        if not isinstance(value, str) or not value:
            raise ValueError("Empty or invalid evidence path")
        path = (root / value).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"Missing or outside-project file: {value}")
        return path

    require(control.get("schema") == "agere-build-control/1", "Unknown control schema")
    require(owner.get("build_status") in ("PAUSED", "ACTIVE"), "Invalid build status")
    manual = owner.get("continuation_mode") == "human_turns_with_outcome_next_step_and_master_plan_update"
    expected_heartbeat = "PAUSED" if manual else owner.get("build_status")
    require(owner.get("heartbeat_status") == expected_heartbeat,
            "Owner heartbeat status disagrees with continuation mode")
    require(automation.get("status") == expected_heartbeat,
            "Actual heartbeat status disagrees with owner")
    require(automation.get("id") == owner.get("heartbeat_id"), "Wrong heartbeat")
    # A paused Codex heartbeat may keep targeting a prior Codex thread when the
    # owner is a non-Codex agent (e.g. Claude Code); it never fires while paused.
    require(automation.get("target_thread_id") == owner.get("thread_id")
            or (automation.get("status") == "PAUSED" and not str(owner.get("thread_id")).startswith("01")),
            "Heartbeat targets a different build owner")
    require("AGERE_BUILD_CONTROL.json" in automation.get("prompt", ""),
            "Heartbeat does not read the canonical control file")
    require(owner.get("build_control") == "AGERE_BUILD_CONTROL.json",
            "Owner does not point to canonical control")
    require(owner.get("latest_architecture_review") == control.get("decision_basis"),
            "Owner and control disagree on architecture decision")
    try:
        for value in [*control["required_references"], control["decision_basis"],
                      owner["latest_receipt"], owner["protected_manifest"]]:
            local_file(value)
        queue = local_file("AUTONOMOUS_STACK_QUEUE.md").read_text()
        backlog = backlog_text(queue)
        require(hashlib.sha256(backlog.encode()).hexdigest() == control["scope"]["backlog_sha256"],
                "Full-product backlog changed: review scope impact before updating its baseline")
        require(re.findall(r"^(\d+)\. \*\*", backlog, re.M) == [str(i) for i in range(1, 11)],
                "Full ten-area acceptance backlog is not intact")
        for name in ("AUTONOMOUS_STACK_QUEUE.md", "CONTINUE_AGERE.md", "AGENTS.md"):
            require("AGERE_BUILD_CONTROL.json" in local_file(name).read_text(),
                    f"{name} does not point to canonical control")
        gates = control["gates"]
        ids = [item["id"] for item in gates]
        require(bool(ids) and len(ids) == len(set(ids)), "Missing or duplicate gates")
        require(set(n for item in gates for n in item["scope_areas"]) == set(range(1, 11)),
                "Delivery gates no longer cover all ten scope areas")
        for item in gates:
            require(item["status"] in ("pending", "active", "blocked", "accepted"),
                    f"Invalid gate status: {item['id']}")
            if item["status"] == "accepted":
                # Evidence integrity only. The receipt's checks must come from
                # actual runtime verification, never this continuity checker.
                evidence = item.get("acceptance_evidence", [])
                require(bool(evidence), f"Accepted gate lacks runtime evidence: {item['id']}")
                for ref in evidence:
                    require(digest(local_file(ref["path"])) == ref["sha256"],
                            f"Acceptance evidence changed: {ref['path']}")
            if item["status"] == "blocked":
                require(bool(item.get("blocker")), f"Blocked gate lacks reason: {item['id']}")
        remaining = [item["id"] for item in gates if item["status"] != "accepted"]
        expected = remaining[0] if remaining else None
        require(control.get("current_gate") == expected,
                "Current gate is not first unfinished gate; record/reorder a justified sequence change")
        if owner.get("full_queue_acceptance"):
            require(not remaining, "Full acceptance claimed with unfinished gates")
        checkpoint = control["checkpoint"]
        require(checkpoint["gate_id"] == control.get("current_gate"),
                "Checkpoint and current gate disagree")
        for key in ("user_behavior_delta", "evidence", "remaining_gap", "next_action"):
            require(bool(checkpoint.get(key)), f"Missing checkpoint field: {key}")
        for path in checkpoint["evidence"]:
            local_file(path)
        require(bool(control.get("decisions")), "Missing decision record")
        if intent == "build":
            require(owner.get("build_status") == "ACTIVE",
                    "Build PAUSED: explicit user resumption required before product work")
            require(bool(expected) and gate == expected,
                    "Requested work does not match the current delivery gate")
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        errors.append(f"Incomplete or invalid control data: {exc}")
    return {
        "schema": "agere-continuity-check/1",
        "status": "failed" if errors else "passed",
        "checks_only": "continuity and evidence references; not product or clinical acceptance",
        "build_status": owner.get("build_status"),
        "current_gate": control.get("current_gate"),
        "next_action": control.get("checkpoint", {}).get("next_action"),
        "last_sealed_receipt": owner.get("latest_receipt"),
        "unsealed_work": owner.get("unsealed_work"),
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", choices=("review", "build"), default="review")
    parser.add_argument("--gate")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    try:
        control = json.loads((root / "AGERE_BUILD_CONTROL.json").read_text())
        owner = json.loads((root / "BUILD_OWNER.json").read_text())
        codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        heartbeat_id = owner["heartbeat_id"]
        if Path(heartbeat_id).name != heartbeat_id:
            raise ValueError("Invalid heartbeat identifier")
        with (codex_dir / "automations" / heartbeat_id / "automation.toml").open("rb") as source:
            automation = tomllib.load(source)
        result = inspect(root, control, owner, automation, args.intent, args.gate)
    except (OSError, ValueError, KeyError) as exc:
        result = {"status": "failed", "errors": [str(exc)]}
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
