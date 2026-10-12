"""Small shared interfaces; source observations and evaluation labels stay separate."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol


def digest(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class ChartReader(Protocol):
    """A reader interprets source notes; it never receives benchmark truth."""

    def read_chart(self, notes: list) -> dict: ...
    def evidence(self, chart: dict, subject: str) -> list: ...
    def ef_values(self, chart: dict) -> list: ...
    def problem_text(self, notes: list) -> str: ...


@dataclass(frozen=True)
class ReaderSpec:
    name: str
    revision: str
    inference_kind: str
    network_required: bool


@dataclass(frozen=True)
class SourceBatch:
    patients: dict
    as_of: str
    snapshot_id: str
    excluded_future_records: int
    source_kind: str = "demo_sample"
    excluded_late_records: int = 0
    arrival_time_missing_records: int = 0
