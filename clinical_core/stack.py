"""One local execution path from synthetic sources to evidence and review actions.

The integrated runner never issues network calls. Model candidates remain
replaceable; installed passage selection runs on a bounded review subset.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from .capabilities import stack_capabilities
from .contracts import digest
from .decision_kernel import DecisionProgram
from .dependency_cache import DependencyCache
from .information_value import expected_information_value
from .providers import ClaudeProvider, EvidenceTask, JevProvider
from .questions import answer, catalog
from .reasoning import observation_proposals, workflow_program
from .runtime import Runtime
from .source_ledger import SourceLedger, batch_records
from .trajectories import analyze_measurements

ROOT = Path(__file__).resolve().parents[1]
VERSION = "agere-integrated-stack/1"
OPTIONS = {
    "care_record_observed": "A relevant dated care record is present, without establishing adherence or optimal care.",
    "outside_record_reconciliation": "Outside care is reported and needs original-record reconciliation.",
    "exception_review": "An exception or hold is documented; appropriateness remains unverified.",
    "access_review": "An access barrier is documented; its current relevance needs review.",
    "reconcile_timeline": "Dated evidence needs reconciliation before selecting a current state.",
    "unknown": "The supplied evidence is insufficient or contradictory."}


def population_cards(items):
    groups, denominators = defaultdict(list), Counter()
    for item in items:
        denominators[(item["practice"], item["subject"])] += 1
        if item["state"] != "care_record_observed":
            groups[(item["practice"], item["subject"], item["state"])].append(item)
    cards = []
    for (practice, subject, state), cases in sorted(groups.items()):
        cards.append({"id": digest([practice, subject, state]), "practice": practice,
                      "subject": subject, "finding": state, "patient_subject_pairs": len(cases),
                      "unique_patients": len({c["patient_id"] for c in cases}),
                      "denominator": denominators[(practice, subject)],
                      "denominator_definition": "Assessed synthetic patient/subject pairs at this practice",
                      "case_ids": [c["case_id"] for c in cases], "next_action": cases[0]["next_action"],
                      "interpretation": "Descriptive evidence-review workload, not a confirmed care gap or causal discovery",
                      "synthetic": True, "automatic_outreach": False})
    return sorted(cards, key=lambda c: (-c["patient_subject_pairs"], c["id"]))


class IntegratedStack:
    def __init__(self, database, bridge, *, selector=None):
        self.ledger = SourceLedger(database)
        self.runtime = Runtime(database, bridge)
        self.cache = DependencyCache(database)
        self.bridge, self.selector = bridge, selector

    def close(self):
        self.cache.close()
        self.runtime.close()
        self.ledger.close()

    def run(self, batch, *, namespace, documents=(), source_coverage=None,
            strict_arrival=False, max_packets=12, token_budget=1600, question_id=None,
            program=None, information_assumptions=None):
        if type(max_packets) is not int or max_packets < 0 or max_packets > 10000:
            raise ValueError("Packet count must be a bounded nonnegative integer")
        if type(token_budget) is not int or token_budget < 1:
            raise ValueError("Positive packet token budget required")
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("Explicit dataset namespace required")
        if any(doc.get("namespace") != namespace or doc.get("source_kind") != "document" for doc in documents):
            raise ValueError("Document imports must match this namespace and source kind")
        start = time.perf_counter()
        imported = self.ledger.ingest([*batch_records(batch, namespace), *documents])
        snapshot, intake = self.ledger.snapshot(namespace, batch.as_of, strict_arrival=strict_arrival)
        if not snapshot.patients:
            raise ValueError("No unambiguous linked patient roster is visible")
        baseline = self.runtime.run(snapshot, source_coverage=source_coverage, scope=namespace)
        policy = workflow_program() if program is None else program
        # This binding is intentionally restricted: new policies can change the
        # workflow, but cannot silently reinterpret these clinical fact names.
        compiled = DecisionProgram(policy)
        if set(compiled.names) != set(workflow_program()["facts"]):
            raise ValueError("This adapter supports only the documented workflow fact contract")
        policy_node = self.cache.source("shared-policy", "policy:" + policy["id"], policy)
        code_revision = digest({p.name: p.read_text() for p in Path(__file__).parent.glob("*.py")})
        before = (self.cache.hits, self.cache.misses)
        items, packets, requests = [], [], []
        packet_computes = 0
        assessments = sorted(baseline["agere"]["items"], key=lambda a: (
            a["state"] == "care_record_observed", a["state"] == "insufficient_evidence", a["patient_id"], a["subject"]))
        doc_by_patient = defaultdict(list)
        issues_by_patient = defaultdict(list)
        for doc in intake["documents"]:
            doc_by_patient[doc["patient_id"]].append(doc)
        for issue in intake["issues"]:
            if issue["kind"] in {"ambiguous_source_versions", "unlinked_patient_record", "ambiguous_patient_roster"}:
                issues_by_patient[issue["patient_id"]].append(issue)
        for assessment in assessments:
            pid, subject = assessment["patient_id"], assessment["subject"]
            scope = "agere:" + digest([namespace, pid, subject])
            case_id = digest([namespace, pid, subject, snapshot.as_of])
            source = self.cache.source(scope, "assessment", assessment)
            proposals = self.cache.resolve(scope, "fact-proposals", code_revision,
                {"assessment": source}, {}, lambda deps, params: observation_proposals(deps["assessment"]))
            decision = self.cache.resolve(scope, "decision-analysis", code_revision,
                {"facts": proposals, "policy": policy_node}, {"as_of": snapshot.as_of},
                lambda deps, params: DecisionProgram(deps["policy"]).analyze(deps["facts"], params["as_of"]))
            evidence = []
            for assertion in assessment["evidence"]:
                origin = intake["provenance"].get(assertion["reference"]["source_hash"])
                if origin is None:
                    raise ValueError("Assertion lost its source-ledger provenance")
                evidence.append({**assertion, "source_origin": origin})
            patient_docs = doc_by_patient[pid]
            unread = [doc for doc in patient_docs if doc["processing_status"] != "ready" or doc["event_date"] is None]
            source_issues = issues_by_patient[pid]
            needs = list(assessment["information_needs"])
            if unread:
                needs.insert(0, {"operation": "resolve_document_processing", "source": "document_ledger",
                                 "reason": "A linked document exists but its dated text is not usable; relevance remains unknown.",
                                 "record_ids": [doc["record_id"] for doc in unread], "automatic_fetch": False})
            if source_issues:
                needs.insert(0, {"operation": "resolve_source_conflict", "source": "source_ledger",
                                 "reason": "Ambiguous source revisions were quarantined; establish the authoritative record.",
                                 "automatic_fetch": False})
            route = {"operation": "source_or_clinical_review", "reason": "No calibrated automatic decision route is configured",
                     "model_execution": "not_scheduled", "automatic_action": False}
            if source_issues:
                route.update(operation="resolve_source_conflict", reason="Resolve source identity/version ambiguity before interpreting an incomplete record")
            elif unread:
                route.update(operation="resolve_document_processing", reason="Reading the missing document can add evidence; rereading current text cannot recover it")
            elif assessment["state"] == "care_record_observed":
                route.update(operation="reuse_observed_record", reason="A dated care record is already present; no generative summary is needed for this record-presence finding")
            elif self.selector is not None and len(packets) < max_packets:
                notes_node = self.cache.source(scope, "notes", snapshot.patients[pid]["notes"])
                prior_misses = self.cache.misses
                packet_node = self.cache.resolve(scope, "evidence-packet", code_revision,
                    {"notes": notes_node}, {"as_of": snapshot.as_of, "subject": subject, "budget": token_budget,
                        "selector": self.selector.card["model_sha256"],
                        "threshold": self.selector.threshold,
                        "tokenizer": getattr(self.selector.counter, "sha256", None)},
                    lambda deps, params: self.selector.packet(deps["notes"], self.bridge.reader,
                        as_of=params["as_of"], subject=params["subject"], token_budget=params["budget"]))
                packet_computes += self.cache.misses - prior_misses
                packet = {**packet_node.value, "case_id": case_id, "patient_id": pid, "node_id": packet_node.node_id}
                packets.append(packet)
                if packet["status"] == "retrieval_proposal" and packet["evidence"]:
                    task = EvidenceTask(subject, snapshot.as_of, OPTIONS, packet["evidence"])
                    prepared = {p.name: p.request(task) for p in (JevProvider(None), ClaudeProvider(None))}
                    sizes = {name: len(json.dumps(body, allow_nan=False).encode()) for name, body in prepared.items()}
                    requests.append({"case_id": case_id, "status": "prepared_not_sent", "requests": prepared,
                                     "request_bytes": sizes, "within_existing_gateway_byte_limit": {k: v <= 20000 for k, v in sizes.items()}})
                    route.update(operation="bounded_model_comparison_available", model_execution="prepared_not_sent",
                                 reason="Source-grounded packet prepared; model proposals would still require validation")
                else:
                    route.update(operation="evidence_review", reason=packet.get("reason", "No usable passage selected; do not infer absent care"))
            elif self.selector is not None:
                route["reason"] = "Bounded packet work budget reached; case remains in the review queue"
            items.append({**assessment, "case_id": case_id, "evidence": evidence,
                          "documents": patient_docs, "source_issues": source_issues, "information_needs": needs,
                          "route": route, "conditional_analysis": decision.value,
                          "lineage": {"assessment": source.node_id, "facts": proposals.node_id, "decision": decision.node_id}})
        trajectories = analyze_measurements(snapshot.patients, snapshot.as_of)
        supported = [q for group in catalog()["groups"] for q in group["questions"]]
        public_intake = {k: v for k, v in intake.items() if k != "provenance"}
        result = {"schema_version": VERSION, "synthetic": True, "namespace": namespace,
                  "as_of": snapshot.as_of, "snapshot_id": snapshot.snapshot_id,
                  "baseline_run_id": baseline["run_id"], "intake": public_intake,
                  "source_provenance": intake["provenance"], "capabilities": stack_capabilities(),
                  "policy": policy, "cases": items, "insight_cards": population_cards(items),
                  "trajectories": trajectories, "evidence_packets": packets, "provider_requests": requests,
                  "question_inventory": dict(Counter(q["execution"]["status"] for q in supported)),
                  "question_answer": answer(question_id, baseline, snapshot.patients) if question_id else None,
                  "information_value": expected_information_value(**information_assumptions) if information_assumptions else None,
                  "summary": {"patients": len(snapshot.patients), "patient_subject_pairs": len(items),
                              "evidence_states": dict(Counter(i["state"] for i in items)),
                              "routes": dict(Counter(i["route"]["operation"] for i in items)),
                              "conditional_states": dict(Counter(i["conditional_analysis"]["status"] for i in items)),
                              "document_states": dict(Counter(d["processing_status"] for d in intake["documents"])),
                              "trajectory_states": dict(Counter(t["status"] for t in trajectories))},
                  "compute": {"ingest": imported, "baseline_cache_hit": baseline["telemetry"]["cache_hit"],
                              "dependency_cache_hits": self.cache.hits - before[0],
                              "operators_executed": self.cache.misses - before[1],
                              "selector_packet_computations": packet_computes, "external_model_calls": 0,
                              "paid_api_usd": 0, "total_operating_cost_usd": None,
                              "cost_basis": "No paid inference executed; local compute/storage/review cost is not measured",
                              "elapsed_seconds": time.perf_counter() - start},
                  "limitations": ["Synthetic local research stack; no real-world clinical superiority established.",
                                   "Every extracted clinical assertion remains a proposal; no automatic outreach.",
                                   "Unread documents do not establish which clinical facts they contain.",
                                   "Source confidence, clinical correctness and decision uncertainty are distinct.",
                                   "SQLite batch execution is not a distributed ingestion platform or production identity service.",
                                   "No new encoder weights or joint observation model were trained by this run."]}
        result["run_id"] = digest({k: v for k, v in result.items() if k != "compute"})
        # Expose these to the explicit development-only learning bridge, not to
        # model prompts or public claim-generation interfaces.
        self.last_batch, self.last_baseline = snapshot, baseline
        return result


def main():
    from .bridge import InsightBridge
    from .corpus import load_world
    from .evidence_selection import EvidenceSelector, LocalTokenCounter
    from .inputs import load_sample
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--world", type=Path)
    sources.add_argument("--sample", type=Path, default=ROOT.parent / "caresignal-mvp/public/demo/patients_sample.json")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--as-of", default="2026-09-30")
    parser.add_argument("--engine", type=Path, default=ROOT.parent / "insight-engine")
    parser.add_argument("--database", type=Path, default=ROOT / "artifacts/agere-stack/stack.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/agere-stack/run.json")
    parser.add_argument("--documents", type=Path, help="Synthetic document envelopes JSON; no upload or automatic OCR")
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--policy", type=Path, help="Versioned synthetic workflow; same fact contract")
    parser.add_argument("--information-assumptions", type=Path)
    parser.add_argument("--strict-arrival", action="store_true")
    parser.add_argument("--max-packets", type=int, default=12)
    parser.add_argument("--token-budget", type=int, default=1600)
    parser.add_argument("--selector", type=Path, default=ROOT / "artifacts/learned-evidence")
    parser.add_argument("--tokenizer", type=Path, default=ROOT / "models/qwen3-4b/tokenizer.json")
    parser.add_argument("--question-id")
    parser.add_argument("--question", help="Optional trained research-domain proposal; does not execute arbitrary questions")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--prepare-learning", type=Path, help="Development worlds only: register review tasks in this database")
    args = parser.parse_args()
    stack = None
    try:
        batch = load_world(args.world, args.as_of) if args.world else load_sample(args.sample, args.as_of)
        selector = EvidenceSelector(args.selector, LocalTokenCounter(args.tokenizer)) if args.max_packets else None
        stack = IntegratedStack(args.database, InsightBridge(args.engine), selector=selector)
        read = lambda path: json.loads(path.read_text()) if path else None
        result = stack.run(batch, namespace=args.namespace, documents=read(args.documents) or [],
            source_coverage=read(args.coverage), strict_arrival=args.strict_arrival,
            max_packets=args.max_packets, token_budget=args.token_budget, question_id=args.question_id,
            program=read(args.policy), information_assumptions=read(args.information_assumptions))
        if args.question:
            from .question_planner import QuestionPlanner
            result["question_plan_proposal"] = QuestionPlanner(ROOT / "artifacts/research-training").propose(args.question)
        if args.prepare_learning:
            from .learning import LearningStore, prepare_tasks
            manifest = json.loads((ROOT / "artifacts/corpus/manifest.json").read_text())
            world = args.world.name if args.world else "demo"
            tasks = prepare_tasks(stack.last_batch, stack.last_baseline, manifest["source_family_by_patient"], world)
            learning = LearningStore(args.prepare_learning)
            try:
                learning.register(tasks)
                result["learning"] = {"registered_tasks": len(tasks), "database": str(args.prepare_learning),
                                      "status": "unreviewed_proposals", "new_reviewed_labels": 0}
            finally:
                learning.close()
        result["run_id"] = digest({k: v for k, v in result.items() if k not in {"run_id", "compute"}})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False))
        if args.report:
            from .stack_view import write_report
            write_report(result, args.report)
        print(json.dumps({"output": str(args.output), "report": str(args.report) if args.report else None,
                          "summary": result["summary"], "compute": result["compute"]}, indent=2))
    except (ValueError, KeyError, FileNotFoundError) as exc:
        parser.exit(2, f"Integrated stack failed: {exc}\n")
    finally:
        if stack:
            stack.close()


if __name__ == "__main__":
    main()
