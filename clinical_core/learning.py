"""Local, model-independent review-to-training workflow for synthetic evidence.

Proposals are not labels. Only explicit human-attributed review revisions can
enter an export. This local research tool records attribution, not authentication
or proof of a reviewer's credentials. It never invokes a model or trains weights.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .contracts import digest
from .corpus import family_split
from .inputs import DATE_FIELDS, FIELDS

VERSION = "agere-evidence-review/1"
LABELS = {
    "insufficient_evidence": "Available evidence cannot establish the requested care state.",
    "care_record_observed": "A relevant result or dispense record is observed; use/adherence is not established.",
    "outside_record_reconciliation": "Outside care is reported; the original record requires reconciliation.",
    "exception_review": "A hold or intolerance is documented; current appropriateness requires review.",
    "access_review": "A barrier or declined care is documented; verify its current relevance.",
    "transaction_review": "A rejection or reversal needs reconciliation; its cause is not established.",
    "reconcile_timeline": "Potentially discordant dated evidence requires current-state reconciliation.",
}


def make_input(feeds, subject, as_of, window_start):
    """Raw observed facts only: no model proposal, hidden truth or patient ID."""
    evidence, source_map = [], {}
    for feed in ("notes", "pharmacy_claims", "labs"):
        for record in feeds[feed]:
            if feed == "pharmacy_claims" and record["drug_class"] != subject:
                continue
            # Retain labs: a reviewer may need tolerability context, not just HbA1c.
            event = record[DATE_FIELDS[feed]]
            if event > as_of or (record.get("available_date") or as_of) > as_of:
                continue
            identifier = f"e{len(evidence)}"
            values = {k: v for k, v in record.items()
                      if k in (*FIELDS[feed], "available_date")
                      and k not in {"patient_id", "note_id", "rx_id", "author", "claim_id"}}
            evidence.append({"id": identifier, "source": feed, "record": values})
            source_map[identifier] = {"source": feed, "source_hash": digest(record),
                                      "source_record_id": record.get("note_id", record.get("rx_id"))}
    return {"task": "reconcile_observed_care_evidence", "subject": subject, "as_of": as_of,
            "window_start": window_start, "window_interpretation": "Synthetic research window; not a clinical due-date rule. Historical evidence is retained for context.",
            "question": "What do the supplied records establish for this subject, and what remains unresolved? Do not infer absent care from absent records.",
            "source_coverage": "unknown; supplied arrays do not establish completeness",
            "evidence": evidence}, source_map


def prepare_tasks(batch, run, family_map, world, limit=60):
    if world not in {"dev", "hard_dev"}:
        raise ValueError("Only development worlds may seed a training review queue")
    if run["request"]["snapshot_id"] != batch.snapshot_id:
        raise ValueError("Run and source snapshot differ")
    if digest({"patients": batch.patients, "as_of": batch.as_of}) != batch.snapshot_id:
        raise ValueError("Source batch changed after snapshot creation")
    if type(limit) is not int or limit <= 0:
        raise ValueError("Review limit must be a positive integer")
    buckets = defaultdict(list)
    for assessment in run["agere"]["items"]:
        pid = assessment["patient_id"]
        family = family_map[pid]
        split = family_split(family)
        if split == "test":
            continue
        buckets[(split, assessment["state"])].append(assessment)
    for bucket in buckets.values():
        bucket.sort(key=lambda a: digest({"patient": a["patient_id"], "subject": a["subject"]}))
    chosen, seen = [], set()
    while len(chosen) < limit and any(buckets.values()):
        for key in sorted(buckets):
            while buckets[key]:
                a = buckets[key].pop()
                family = family_map[a["patient_id"]]
                pair = (family, a["subject"])
                if pair in seen:
                    continue
                seen.add(pair)
                model_input, source_map = make_input(batch.patients[a["patient_id"]], a["subject"], batch.as_of, a["window_start"])
                task = {"schema_version": VERSION, "synthetic": True,
                        "origin": {"world": world, "family": family, "patient_id": a["patient_id"],
                                   "split": key[0], "snapshot_id": batch.snapshot_id, "run_id": run["run_id"]},
                        "input": model_input, "source_map": source_map,
                        "label_options": LABELS, "proposal": {"state": a["state"],
                        "provenance": "Unreviewed deterministic baseline; not ground truth"}}
                task["task_id"] = digest(task)
                chosen.append(task)
                break
            if len(chosen) == limit:
                break
    return chosen


class LearningStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS learning_tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS learning_reviews (revision TEXT PRIMARY KEY, task_id TEXT NOT NULL, previous TEXT, payload TEXT NOT NULL)")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS review_successor ON learning_reviews(task_id, COALESCE(previous, ''))")
        self.db.commit()

    def close(self):
        self.db.close()

    def register(self, tasks):
        with self.db:
            for task in tasks:
                if digest({k: v for k, v in task.items() if k != "task_id"}) != task["task_id"]:
                    raise ValueError("Task content hash mismatch")
                if task["schema_version"] != VERSION or task["synthetic"] is not True:
                    raise ValueError("Only this synthetic review schema is supported")
                origin = task["origin"]
                if (origin["world"] not in {"dev", "hard_dev"} or origin["split"] not in {"train", "calibration"}
                        or family_split(origin["family"]) != origin["split"]):
                    raise ValueError("Evaluation-world or family partition violation")
                if task["label_options"] != LABELS:
                    raise ValueError("Unknown review label schema")
                self.db.execute("INSERT OR IGNORE INTO learning_tasks VALUES (?,?)", (task["task_id"], json.dumps(task, sort_keys=True)))

    def latest(self, task_id):
        row = self.db.execute("SELECT r.revision,r.payload FROM learning_reviews r WHERE r.task_id=? AND NOT EXISTS (SELECT 1 FROM learning_reviews s WHERE s.previous=r.revision)", (task_id,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (None, None)

    def import_reviews(self, reviews):
        """All-or-nothing import, immutable revisions, explicit supersession."""
        required = {"task_id", "previous_revision", "decision", "label", "support_ids", "reviewer",
                    "reviewer_kind", "reviewed_at", "rationale"}
        inserted = []
        with self.db:
            for review in reviews:
                if set(review) != required:
                    raise ValueError("Incomplete or unknown review fields")
                if review["reviewer_kind"] != "human":
                    raise ValueError("Model proposals cannot be promoted to human-reviewed labels")
                for field in ("reviewer", "rationale"):
                    if not isinstance(review[field], str) or not review[field].strip():
                        raise ValueError("Named reviewer and rationale required")
                stamp = datetime.fromisoformat(review["reviewed_at"])
                if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
                    raise ValueError("Review time must be timezone-aware and not in the future")
                row = self.db.execute("SELECT payload FROM learning_tasks WHERE id=?", (review["task_id"],)).fetchone()
                if not row:
                    raise ValueError("Unknown review task")
                task = json.loads(row[0])
                if not isinstance(review["decision"], str) or review["decision"] not in {"accept", "correct", "exclude"}:
                    raise ValueError("Invalid review decision")
                if review["decision"] == "exclude":
                    if review["label"] is not None or review["support_ids"] != []:
                        raise ValueError("Excluded examples cannot carry training labels")
                elif not isinstance(review["label"], str) or review["label"] not in LABELS:
                    raise ValueError("Invalid reviewed label")
                if review["decision"] == "accept" and review["label"] != task["proposal"]["state"]:
                    raise ValueError("An accepted label must match the proposal; use correct to change it")
                refs = review["support_ids"]
                available = {e["id"] for e in task["input"]["evidence"]}
                if (not isinstance(refs, list) or any(not isinstance(r, str) for r in refs)
                        or len(refs) != len(set(refs)) or set(refs) - available):
                    raise ValueError("Invalid or invented support reference")
                if review["label"] not in {None, "insufficient_evidence"} and not refs:
                    raise ValueError("An affirmative evidence label requires supporting records")
                revision = digest(review)
                if self.db.execute("SELECT 1 FROM learning_reviews WHERE revision=?", (revision,)).fetchone():
                    inserted.append(revision)
                    continue
                previous, _ = self.latest(review["task_id"])
                if review["previous_revision"] != previous:
                    raise ValueError("Stale review revision; explicitly supersede the current revision")
                self.db.execute("INSERT INTO learning_reviews VALUES (?,?,?,?)",
                                (revision, review["task_id"], previous, json.dumps(review, sort_keys=True)))
                inserted.append(revision)
        return inserted

    def export(self, target):
        """Separate provenance from features; no unreviewed targets or test rows."""
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)
        if any(target.iterdir()):
            raise ValueError("Export directory must be empty to preserve previous dataset versions")
        candidates, pending, excluded = [], 0, 0
        for task_id, payload in self.db.execute("SELECT id,payload FROM learning_tasks ORDER BY id"):
            task = json.loads(payload)
            revision, review = self.latest(task_id)
            if review is None:
                pending += 1
                continue
            if review["decision"] == "exclude":
                excluded += 1
                continue
            candidates.append({"task_id": task_id, "split": task["origin"]["split"],
                               "input": task["input"], "target": {"state": review["label"], "support_ids": review["support_ids"]},
                               "provenance": {"origin": task["origin"], "source_map": task["source_map"],
                                              "review_revision": revision, "reviewer": review["reviewer"],
                                              "reviewed_at": review["reviewed_at"], "rationale": review["rationale"],
                                              "reviewer_identity": "caller-attributed; not authenticated"}})
        groups = defaultdict(list)
        for item in candidates:
            groups[digest(item["input"])].append(item)
        output, blocked, duplicate = [], 0, 0
        for group in groups.values():
            if len({r["split"] for r in group}) > 1 or len({digest(r["target"]) for r in group}) > 1:
                blocked += len(group)
                continue
            output.append(group[0])
            duplicate += len(group) - 1
        counts = {}
        files = {}
        for split in ("train", "calibration"):
            rows = [r for r in output if r["split"] == split]
            payload = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
            name = split + ".jsonl"
            (target / name).write_text(payload)
            counts[split] = len(rows)
            files[name] = {"content_digest": digest(payload), "rows": len(rows)}
        manifest = {"schema_version": VERSION, "synthetic": True, "counts": counts,
                    "pending_review": pending, "excluded_by_reviewer": excluded,
                    "blocked_cross_split_or_conflicting_input": blocked, "deduplicated": duplicate,
                    "files": files, "model_weights_trained": False,
                    "status": "reviewed_training_export" if counts["train"] else "awaiting_training_labels",
                    "limitations": ["Reference validation does not establish entailment or clinical accuracy.",
                                    "Reviewer identity and qualifications are not independently verified.",
                                    "Near-duplicate language requires further leakage review; only exact input duplicates are handled.",
                                    "Full evidence is retained; downstream trainers must not silently truncate it."]}
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return manifest


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=root / "artifacts/agere-learning/reviews.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--world", choices=("dev", "hard_dev"), default="hard_dev")
    prepare.add_argument("--engine-root", type=Path, default=root.parent / "insight-engine")
    prepare.add_argument("--limit", type=int, default=60)
    prepare.add_argument("--output", type=Path, default=root / "artifacts/agere-learning/queue.jsonl")
    ingest = commands.add_parser("import-reviews")
    ingest.add_argument("file", type=Path)
    export = commands.add_parser("export")
    export.add_argument("directory", type=Path)
    args = parser.parse_args()
    store = LearningStore(args.database)
    try:
        if args.command == "prepare":
            from .bridge import InsightBridge
            from .corpus import family_mapping, load_world
            from .runtime import Runtime
            if args.output.exists():
                raise ValueError("Review queue already exists; choose a new output path")
            world_path = args.engine_root / "data/worlds" / args.world
            batch = load_world(world_path, "2026-09-30")
            families = family_mapping(json.loads((args.engine_root / "data/source/synthetic_patients.json").read_text()))
            runtime = Runtime(args.database.with_name("development-runs.sqlite3"), InsightBridge(args.engine_root))
            try:
                run = runtime.run(batch)
            finally:
                runtime.close()
            tasks = prepare_tasks(batch, run, families, args.world, args.limit)
            store.register(tasks)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text("".join(json.dumps(t, sort_keys=True) + "\n" for t in tasks))
            print(json.dumps({"queue": str(args.output), "tasks": len(tasks),
                              "splits": dict(Counter(t["origin"]["split"] for t in tasks)),
                              "proposals": dict(Counter(t["proposal"]["state"] for t in tasks)),
                              "review_status": "pending", "model_weights_trained": False}))
        elif args.command == "import-reviews":
            rows = [json.loads(line) for line in args.file.read_text().splitlines() if line.strip()]
            print(json.dumps({"revisions": store.import_reviews(rows)}))
        else:
            print(json.dumps(store.export(args.directory), indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()
