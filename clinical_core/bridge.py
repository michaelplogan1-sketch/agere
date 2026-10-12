"""Reuse the existing engine through an explicit chart-reader interface."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

from .contracts import ReaderSpec, digest


class InsightBridge:
    def __init__(self, engine_root: Path):
        self.root = engine_root.resolve()
        if not (self.root / "ie/engine/judge.py").is_file():
            raise ValueError("Insight engine source directory not found")
        sys.path.insert(0, str(self.root))
        self.reader = importlib.import_module("ie.engine.reader")
        self.judge = importlib.import_module("ie.engine.judge")
        self.discovery = importlib.import_module("ie.engine.discover")
        if Path(self.judge.__file__).resolve() != self.root / "ie/engine/judge.py":
            raise ValueError("A different insight engine is already imported")
        paths = ("ie/engine/reader.py", "ie/engine/judge.py", "ie/engine/discover.py", "ie/ontology/decisions.json")
        self.version = digest({p: (self.root / p).read_text() for p in paths})
        self.spec = ReaderSpec("lexicon", self.version, "deterministic_lexicon", False)

    def analyze(self, patients: dict, as_of: str, reader=None) -> tuple:
        if reader is not None and hasattr(reader, "finalize"):
            rows = [reader.finalize(self.judge.judge_patient(p, as_of, reader=reader))
                    for _, p in sorted(patients.items()) if p.get("roster")]
        else:
            rows = self.judge.judge_world(patients, as_of, reader=reader if reader is not None else self.reader)
        cards = self.discovery.discover(rows, mode="engine")
        return rows, cards

    def structured(self, patients: dict, as_of: str) -> tuple:
        rows = self.judge.judge_world(patients, as_of, use_notes=False)
        return rows, self.discovery.discover(rows, mode="structured")
