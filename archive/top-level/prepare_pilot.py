"""Freeze a label-independent synthetic development cohort before inference."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    project = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=project.parent / "caresignal-mvp/public/demo/patients_sample.json")
    parser.add_argument("--output", type=Path, default=project / "artifacts/pilot")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", default="local-reader-pilot-v1")
    parser.add_argument("--exclude-selection", type=Path, help="Exclude every patient ID in a previous selection")
    args = parser.parse_args()
    records = json.loads(args.sample.read_text())
    excluded = set(json.loads(args.exclude_selection.read_text())["ids"]) if args.exclude_selection else set()
    records = [p for p in records if p["id"] not in excluded]
    if not 0 < args.count <= len(records):
        parser.error("count must be positive and at most the available sample size")
    if (args.output / "selection.json").exists():
        parser.error("Pilot already frozen; use another output directory for a new cohort")
    selected = sorted(records, key=lambda p: hashlib.sha256((args.seed + ":" + p["id"]).encode()).hexdigest())[:args.count]
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "sample.json").write_text(json.dumps(selected, indent=2))
    (args.output / "selection.json").write_text(json.dumps({
        "synthetic": True, "source": str(args.sample.resolve()),
        "source_sha256": hashlib.sha256(args.sample.read_bytes()).hexdigest(),
        "method": f"First {args.count} SHA256 ranks of {args.seed}:patient_id, without labels",
        "excluded_ids": sorted(excluded),
        "ids": [p["id"] for p in selected]}, indent=2))
    print(args.output / "selection.json")


if __name__ == "__main__":
    main()
