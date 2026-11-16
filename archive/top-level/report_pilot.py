"""Compare saved synthetic runs after inference; this script alone reads labels."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent / "artifacts/pilot")
    args = parser.parse_args()
    root = args.directory.resolve()
    truth = {p["id"]: p["truth"]["status"] for p in json.loads((root / "sample.json").read_text())}
    systems = {}
    for label in ("lexicon", "mlx"):
        run = json.loads((root / f"{label}-run.json").read_text())
        evaluation = json.loads((root / f"{label}-run-evaluation.json").read_text())
        mismatches, correct, total, reviews = [], 0, 0, 0
        for row in run["patient_results"]:
            expected = truth[row["patient_id"]]
            for subject in sorted(set(expected) | set(row["answers"])):
                answer = row["answers"].get(subject, {}).get("answer")
                target = expected.get(subject)
                total += 1
                correct += answer == target
                reviews += bool(answer and answer.startswith("review:"))
                if answer != target:
                    mismatches.append({"patient_id": row["patient_id"], "subject": subject,
                                       "expected": target, "actual": answer,
                                       "evidence": row["answers"].get(subject, {}).get("evidence", [])})
        systems[label] = {"run_id": run["run_id"], "worklist_scoring": evaluation["reader"],
                          "all_statuses_correct": correct, "all_statuses_total": total,
                          "review_pairs": reviews, "mismatches": mismatches,
                          "telemetry": run["telemetry"]}
    (root / "comparison.json").write_text(json.dumps({"synthetic": True, "systems": systems}, indent=2))
    a, b = systems["lexicon"], systems["mlx"]
    def metric(s, name):
        return s["worklist_scoring"][name]
    lines = ["# Local model pilot", "", "All records and answer labels are synthetic. This is a small pilot, not clinical validation.", "",
             "The fixed cohort was selected by patient-ID hash before inference; `selection.json` records the method and source hash. "
             "No training or healthcare fine-tuning was performed. The model sees only allowlisted source feeds; scoring loads labels afterward. "
             "The `pilot` cohort was used for prompt development; `holdout` excludes all development patient IDs and is a separate, one-pass synthetic check with the frozen prompt. "
             "Both come from the same synthetic world and may share underlying library templates; this is not a held-out world, site, time period, or source-patient-family evaluation.", "",
             "| Metric | Lexicon + rules | Local Qwen + rules |", "|---|---:|---:|"]
    for name in ("worklist", "true_actionable", "tp", "precision", "recall", "worklist_on_treatment_or_done", "worklist_appropriate_holds", "reason_accuracy"):
        lines.append(f"| {name} | {metric(a, name)} | {metric(b, name)} |")
    lines += [f"| Exact status matches over union of expected and predicted patient/subject pairs | {a['all_statuses_correct']}/{a['all_statuses_total']} | {b['all_statuses_correct']}/{b['all_statuses_total']} |",
              f"| Patient/subject pairs sent to review | {a['review_pairs']} | {b['review_pairs']} |", "",
              "Worklist counts are patient/subject pairs, not unique patients. Reason accuracy in the inherited scorer covers pairs where both outputs and truth are gaps; it can hide abstentions. Exact status matches and review counts expose that limitation.", "",
              "## Measured execution", "", "```json", json.dumps(b["telemetry"], indent=2), "```", "",
              "MLX peak memory is memory reported by the model runtime, not total process or machine RAM. End-to-end runtime excludes package installation, weight download and file-integrity verification. No electricity, hardware amortization, review labor, or dollar cost was measured. Local inference is not free.", "",
              "## Interpretation", "", "Keep the lexicon reader as the default until a broader independent evaluation justifies a change. "
              "Schema-valid output and real source references do not establish correct clinical interpretation. Invalid model extractions route to review; they never fall back silently to a guessed care gap. "
              "Review cases require follow-up and should not be treated as negative clinical findings. This pilot is too small and selected to estimate real-world performance or population-card validity.", "",
              "This hybrid replaces note-evidence interpretation only. Deduplication, condition/EF extraction, structured evidence rules and discovery still use the existing engine. "
              "It is not an LLM-alone comparator, a full ensemble, a trained healthcare model, or a vendor-product comparison.", "",
              "## Audit files", "", "- `mlx-run.json`: outputs, exact note references, raw model responses, validation failures and timing.",
              "- `lexicon-run.json`: matched baseline.", "- `*-evaluation.json`: original synthetic scorer results.",
              "- `comparison.json`: all status mismatches, review counts and metrics.",
              "- `../../models/qwen3-4b/manifest.json`: pinned model revision and file hashes.", ""]
    (root / "REPORT.md").write_text("\n".join(lines))
    print(root / "REPORT.md")


if __name__ == "__main__":
    main()
