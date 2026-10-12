"""Registered, read-only analyses over source evidence and computed patient facts."""
from collections import Counter, defaultdict
from pathlib import Path
import json


def catalog():
    return json.loads((Path(__file__).resolve().parents[1] / "config/questions.json").read_text())


def get_question(question_id):
    for group in catalog()["groups"]:
        for question in group["questions"]:
            if question["id"] == question_id:
                return question
    raise ValueError("Unknown question ID")


def answer(question_id, run, patients):
    q = get_question(question_id)
    execution = q["execution"]
    response = {"question_id": question_id, "question": q["question"], "synthetic": True,
                "status": execution["status"], "scope": execution["scope"],
                "run_id": run["run_id"], "as_of": run["request"]["as_of"],
                "population": run["coverage"]["evaluated"], "result": None}
    handler = execution["handler"]
    if handler is None:
        response["required_data"] = execution["required_data"]
        response["next_step"] = "Map available fields, implement versioned analysis, and validate before answering."
        return response
    rows = run["patient_results"]
    if "agere" in run and handler in ("hf_gaps", "barriers", "outside"):
        hf = {r["patient_id"] for r in rows if r["conditions"]["hfref"]}
        allowed = {"hf_gaps": {"access_review", "exception_review", "reconcile_timeline", "transaction_review", "insufficient_evidence"},
                   "barriers": {"access_review", "transaction_review", "reconcile_timeline"},
                   "outside": {"outside_record_reconciliation"}}[handler]
        matches = [a for a in run["agere"]["items"] if a["state"] in allowed
                   and (handler != "hf_gaps" or (a["patient_id"] in hf and a["subject"] in ("mra", "sglt2", "arni", "bb")))]
        response["result"] = {"patient_subject_pairs": len(matches),
                              "unique_patients": len({a["patient_id"] for a in matches}),
                              "states": dict(Counter(a["state"] for a in matches)), "items": matches}
        response["interpretation"] = "Evidence-review candidates, not confirmed care gaps or automatic outreach. Clinical eligibility remains heuristic."
        return response
    if handler in ("hf_gaps", "barriers", "outside"):
        matches = []
        for row in rows:
            if handler == "hf_gaps" and not row["conditions"]["hfref"]:
                continue
            for subject, result in row["answers"].items():
                state = result["answer"]
                if handler == "hf_gaps" and (subject not in ("mra", "sglt2", "arni", "bb") or not state.startswith(("gap:", "review:"))):
                    continue
                if handler == "barriers" and state not in ("gap:pa_denied", "gap:cost_abandoned", "gap:no_access", "gap:declined", "gap:intolerance", "gap:contraindicated"):
                    continue
                if handler == "outside" and state != "on_outside":
                    continue
                matches.append({"patient_id": row["patient_id"], "subject": subject, **result})
        response["result"] = {"patient_subject_pairs": len(matches), "unique_patients": len({m["patient_id"] for m in matches}),
                              "states": dict(Counter(m["answer"] for m in matches)), "items": matches}
    elif handler == "feed_counts":
        response["result"] = {feed: {"records": sum(len(p[feed]) for p in patients.values()),
                                        "patients_with_records": sum(bool(p[feed]) for p in patients.values())} for feed in next(iter(patients.values()))}
    elif handler == "documentation":
        response["result"] = {"sentences": sum(r["bloat"]["sentences"] for r in rows),
                              "repeated_sentences": sum(r["bloat"]["echoes"] for r in rows),
                              "visits": sum(r["doc"]["visits"] for r in rows),
                              "heuristic_decision_mentions": sum(r["doc"]["decisions"] for r in rows)}
    elif handler == "cutoff":
        response["result"] = {"excluded_future_records": run["coverage"]["excluded_future_records"], "arrival_time_known": False}
    elif handler == "lab_coverage":
        by_test, counts = defaultdict(set), Counter()
        for pid, feeds in patients.items():
            for lab in feeds["labs"]:
                by_test[lab["test"]].add(pid)
                counts[lab["test"]] += 1
        response["result"] = {t: {"patients": len(ids), "observations": counts[t]} for t, ids in sorted(by_test.items())}
    elif handler == "source_references":
        refs = run["note_evidence"]
        response["result"] = {"checked": len(refs), "unresolved": [r for r in refs if r["status"] != "exact_quote_found"]}
    elif handler == "reader_review":
        response["result"] = {"items": [{"patient_id": r["patient_id"], "subjects": r["reader_review_subjects"]} for r in rows if r.get("reader_review_subjects")],
                              "invalid_extractions": run["telemetry"].get("invalid_extractions"),
                              "reader": run["request"]["reader"]["name"]}
    elif handler == "insights":
        response["result"] = run["insight_cards"]
    else:
        raise ValueError("Handler registered but not implemented")
    return response
