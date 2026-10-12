"""Full synthetic-world adapter and family-aware development partitions."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

from .contracts import digest
from .inputs import FIELDS, project_sample


def jsonl(path):
    with path.open() as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def load_world(path: Path, as_of: str):
    """Read only named feeds; never open the truth directory."""
    feeds = path / "feeds"
    records = {}
    for roster in jsonl(feeds / "roster.jsonl"):
        pid = roster["patient_id"]
        if pid in records:
            raise ValueError("Duplicate roster patient")
        records[pid] = {"id": pid, "roster": roster,
                        "feeds": {name: [] for name in FIELDS if name != "roster"}}
    for name in FIELDS:
        if name == "roster":
            continue
        for row in jsonl(feeds / (name + ".jsonl")):
            if row["patient_id"] not in records:
                raise ValueError(f"{name}: source record has no roster patient")
            records[row["patient_id"]]["feeds"][name].append(row)
    return replace(project_sample(list(records.values()), as_of), source_kind="synthetic_world")


def family_mapping(library: list):
    """Match actual source IDs and generator's three draws; never strip IDs blindly."""
    mapping = {}
    for patient in library:
        if not patient.get("runnable"):
            continue
        family = patient["id"]
        for pid in (family, family + "-2", family + "-3"):
            if pid in mapping and mapping[pid] != family:
                raise ValueError("Ambiguous source-family identity")
            mapping[pid] = family
    return mapping


def family_split(family):
    bucket = int(digest({"seed": "clinical-core-family-v1", "family": family})[:8], 16) % 100
    return "train" if bucket < 70 else "calibration" if bucket < 85 else "test"


def inventory(engine_root: Path):
    library_path = engine_root / "data/source/synthetic_patients.json"
    library = json.loads(library_path.read_text())
    families = family_mapping(library)
    worlds, observed_ids = [], set()
    for world in sorted((engine_root / "data/worlds").iterdir()):
        if not (world / "feeds/roster.jsonl").exists():
            continue
        counts, files, tests = {}, {}, set()
        ids = []
        for name in FIELDS:
            p = world / "feeds" / (name + ".jsonl")
            h = hashlib.sha256()
            count = 0
            with p.open("rb") as stream:
                for line in stream:
                    h.update(line)
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    count += 1
                    if name == "roster":
                        ids.append(row["patient_id"])
                    if name == "labs":
                        tests.add(row["test"])
            counts[name] = count
            files[name] = {"path": str(p), "sha256": h.hexdigest(), "bytes": p.stat().st_size}
        if len(ids) != len(set(ids)) or any(pid not in families for pid in ids):
            raise ValueError("Roster duplicate or unrecognized source family")
        observed_ids.update(ids)
        worlds.append({"name": world.name, "path": str(world), "counts": counts,
                       "files": files, "lab_tests": sorted(tests),
                       "role": "development" if world.name in ("dev", "hard_dev") else "evaluation",
                       "family_partitions": dict(Counter(family_split(families[pid]) for pid in ids)),
                       "has_separate_truth": (world / "truth/patients.jsonl").is_file()})
    return {"synthetic": True, "library_rows": len(library),
            "runnable_source_families": len(set(families.values())),
            "worlds": worlds, "world_count": len(worlds),
            "patient_world_instances": sum(w["counts"]["roster"] for w in worlds),
            "distinct_generated_patient_ids": len(observed_ids),
            "note_world_instances": sum(w["counts"]["notes"] for w in worlds),
            "source_family_by_patient": {p: families[p] for p in sorted(observed_ids)},
            "partition_by_family": {f: family_split(f) for f in sorted(set(families.values()))},
            "training_policy": "Only train families in development worlds; calibration families for thresholds; test families and evaluation worlds excluded from training.",
            "limitations": ["Prospective partitions; no claim that historical engine development was blind to these families.",
                            "Repeated worlds and jittered clones are not independent real patients.",
                            "No model training is performed by inventory or benchmark execution."]}


def partition_ids(manifest, world, purpose):
    if purpose not in ("train", "calibration", "test"):
        raise ValueError("Unknown partition purpose")
    entry = next(w for w in manifest["worlds"] if w["name"] == world)
    if purpose in ("train", "calibration") and entry["role"] != "development":
        raise ValueError("Evaluation worlds cannot be used for training or threshold calibration")
    # Read the roster, so this also works if future worlds contain different IDs.
    return [r["patient_id"] for r in jsonl(Path(entry["path"]) / "feeds/roster.jsonl")
            if manifest["partition_by_family"][manifest["source_family_by_patient"][r["patient_id"]]] == purpose]
