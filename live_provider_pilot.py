"""Small, explicitly budgeted synthetic API comparison. Never reads hidden labels before inference."""
import argparse
import json
import os
from pathlib import Path

from clinical_core.bridge import InsightBridge
from clinical_core.contracts import digest
from clinical_core.corpus import load_world, jsonl
from clinical_core.providers import EvidenceTask, HostedGateway, JevProvider, ClaudeProvider


def load_backend_keys(path):
    # Only the two explicitly required keys from the known engine configuration.
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in ("TYPESAFE_API_KEY", "ANTHROPIC_API_KEY"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--budget-usd", type=float, default=0)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    output = root / "artifacts/live-provider-pilot"
    output.mkdir(exist_ok=True)
    if (output / "attempts.jsonl").exists():
        parser.error("Pilot already attempted; inspect its ledger before authorizing another run")
    if args.env_file:
        load_backend_keys(args.env_file)
    engine = root.parent / "insight-engine"
    bridge = InsightBridge(engine)
    batch = load_world(engine / "data/worlds/dev", "2026-09-30")
    rows, _ = bridge.analyze(batch.patients, batch.as_of)
    import ie.llm.jev as old
    choices = [(r["patient_id"], subject) for r in rows for subject, result in r["answers"].items()
               if result["answer"] not in ("on", "done")]
    choices.sort(key=lambda item: digest({"seed": "provider-development-v1", "item": item}))
    tasks = []
    for pid, subject in choices:
        chart = bridge.reader.read_chart(batch.patients[pid]["notes"])
        matcher = bridge.reader.A1C_RE if subject == "a1c" else bridge.reader.DRUG_RE[subject]
        candidates = [s for s in chart["sentences"] if s["section"] not in ("boilerplate", "med_list", "problem_list")
                      and (matcher.search(s["text"]) or matcher.search(s.get("prev", "")))]
        if not candidates or len(candidates) > 15:
            continue
        source_options = old.question(subject)["reason"]["criteria"]
        options = {old.MAP[k] or "unknown": v for k, v in source_options.items()}
        task = EvidenceTask(subject, batch.as_of, options,
            [{"id": f"s{i}", "text": (s.get("prev", "") + "\n" + s["text"]).strip(), "date": s["date"], "note_id": s["note_id"]}
             for i, s in enumerate(candidates)])
        if len(json.dumps(task.state())) > 6500:
            continue
        tasks.append((pid, task))
        if len(tasks) == 8:
            break
    gateway = HostedGateway(allow_live=args.allow_live, max_calls=16, max_usd=args.budget_usd)
    providers = [JevProvider(gateway), ClaudeProvider(gateway)]
    requests = [{"patient_id": pid, "subject": task.subject, "providers": {p.name: p.request(task) for p in providers}} for pid, task in tasks]
    (output / "requests.json").write_text(json.dumps(requests, indent=2))
    if not args.allow_live:
        print("Dry run: requests saved; no hosted calls")
        return
    records = []
    for pid, task in tasks:
        for provider in providers:
            record = {"patient_id": pid, "subject": task.subject, "provider": provider.name}
            try:
                record["result"] = provider.classify(task)
            except Exception as exc:
                # Do not print HTTP response bodies, headers, or credential values.
                record["error"] = type(exc).__name__
                if hasattr(exc, "code"):
                    record["http_status"] = exc.code
            records.append(record)
            with (output / "attempts.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            (output / "usage.json").write_text(json.dumps(gateway.events, indent=2))
            print(json.dumps({"provider": provider.name, "completed": len(records), "status": "error" if "error" in record else "returned"}), flush=True)
            if record.get("http_status") in (401, 403):
                break
        if records[-1].get("http_status") in (401, 403):
            break
    # Only evaluation below this point reads oracle labels.
    truth = {p["id"]: p for p in jsonl(engine / "data/worlds/dev/truth/patients.jsonl")}
    for record in records:
        expected = truth[record["patient_id"]]["status"].get(record["subject"])
        expected_reason = "unknown" if expected == "gap:never_offered" else expected.removeprefix("gap:") if expected else None
        record["expected_synthetic_state"] = expected
        record["label_match"] = record.get("result", {}).get("label") == expected_reason
    summary = {"synthetic": True, "scope": "Small development-world reader comparison, not clinical validation or full patient-list evaluation",
               "selection": "ID/subject hash order among baseline apparent gaps with bounded retrieved evidence; not a representative sample",
               "authorized_cap_usd": args.budget_usd, "reserved_usd": gateway.reserved_usd,
               "estimated_api_usd": sum(e.get("estimated_api_usd", 0) for e in gateway.events),
               "unpriced_attempts": sum("estimated_api_usd" not in e for e in gateway.events),
               "rates": {"jev_input_per_million": 0.042, "claude_opus_5_5_input_per_million": 4, "claude_opus_5_5_output_per_million": 20},
               "rate_sources": ["https://docs.typesafe.ai/models", "https://platform.claude.com/docs/en/about-claude/pricing"],
               "records": records, "provider_metrics": {p.name: {"attempted": sum(r["provider"] == p.name for r in records),
                 "returned": sum(r["provider"] == p.name and "result" in r for r in records),
                 "label_matches": sum(r["provider"] == p.name and r["label_match"] for r in records)} for p in providers}}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "records"}, indent=2))


if __name__ == "__main__":
    main()
