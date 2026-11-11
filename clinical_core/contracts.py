"""Small shared interfaces; source observations and evaluation labels stay separate."""
from __future__ import annotations

import hashlib
from collections import Counter
import json
from dataclasses import dataclass
from typing import Protocol


def json_fingerprint(value, *, separators=None, allow_nan=True, default=None):
    """Hash the exact existing JSON bytes while caching shared source fragments.

    Complete values and their order are retained. Repeated evidence does not
    require repeatedly constructing a large parent JSON string in memory.
    """
    options = dict(sort_keys=True, separators=separators, allow_nan=allow_nan,
                   default=default)
    if separators not in (None, (',', ':')):
        return hashlib.sha256(json.dumps(value, **options).encode()).hexdigest()
    item_separator, key_separator = separators or (', ', ': ')
    counts = Counter()
    seen = set()
    active = set()
    expanded_nodes = {}
    # This only chooses an encoding strategy; it never truncates an input.
    # Small rows with shared metadata are cheaper in the standard C encoder
    # than in a Python walk that serializes every key and scalar separately.
    direct_nodes = 2048

    def count(v):
        if not isinstance(v, (dict, list, tuple)):
            return 1
        identity = id(v)
        counts[identity] += 1
        if identity in active:
            raise ValueError('Circular reference detected')
        if identity in seen:
            return expanded_nodes[identity]
        seen.add(identity)
        active.add(identity)
        size = 1
        for child in v.values() if isinstance(v, dict) else v:
            size = min(direct_nodes + 1, size + count(child))
        active.remove(identity)
        expanded_nodes[identity] = size
        return size

    count(value)
    repeated = {key for key, n in counts.items() if n > 1}
    if not repeated:
        return hashlib.sha256(json.dumps(value, **options).encode()).hexdigest()
    below = {}

    def shared_below(v):
        if not isinstance(v, (dict, list, tuple)):
            return False
        identity = id(v)
        if identity not in below:
            below[identity] = any(id(x) in repeated or shared_below(x)
                                  for x in (v.values() if isinstance(v, dict) else v)
                                  if isinstance(x, (dict, list, tuple)))
        return below[identity]

    sha = hashlib.sha256()
    fragments = {}
    scalar_cache = {}
    key_cache = {}

    def scalar(v):
        if isinstance(v, str):
            identity = id(v)
            if identity not in scalar_cache:
                scalar_cache[identity] = json.dumps(v, **options).encode()
            return scalar_cache[identity]
        return json.dumps(v, **options).encode()

    def emit(v):
        identity = id(v)
        if isinstance(v, (dict, list, tuple)) and (
                expanded_nodes[identity] <= direct_nodes or not shared_below(v)):
            # A small shared metadata object elsewhere must not force every
            # unrelated source row through the Python key/scalar encoder.
            # Whole unshared branches use the standard C encoder unchanged.
            if identity in repeated:
                if identity not in fragments:
                    fragments[identity] = json.dumps(v, **options).encode()
                sha.update(fragments[identity])
            else:
                sha.update(json.dumps(v, **options).encode())
        elif isinstance(v, dict):
            sha.update(b'{')
            for i, (key, child) in enumerate(sorted(v.items())):
                if i:
                    sha.update(item_separator.encode())
                # Let the standard encoder retain its exact key conversions,
                # escaping and invalid-key errors. No hand-authored coercion.
                cache_key = (type(key), key)
                if cache_key not in key_cache:
                    text = json.dumps({key: None}, **options)
                    key_cache[cache_key] = text[1:text.rfind(':')].encode()
                sha.update(key_cache[cache_key])
                sha.update(key_separator.encode())
                emit(child)
            sha.update(b'}')
        elif isinstance(v, (list, tuple)):
            sha.update(b'[')
            for i, child in enumerate(v):
                if i:
                    sha.update(item_separator.encode())
                emit(child)
            sha.update(b']')
        else:
            sha.update(scalar(v))

    emit(value)
    return sha.hexdigest()


def digest(value) -> str:
    return json_fingerprint(value, separators=(",", ":"), allow_nan=False)


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
