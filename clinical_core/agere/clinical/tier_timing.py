"""Expected cost of a cascade tier: a running median of its measured seconds, kept per stack (tier-timings.json next to
learning.sqlite3). The cascade starts a tier only when the answer budget still covers this estimate.

estimate(stack_path, tier) -> seconds; record(stack_path, tier, seconds) after a tier ran. With fewer than MIN_SAMPLES
measurements the conservative static default stands in for the missing ones (local-model tiers 20 s, atomic System One 30 s,
evidence tiers 10 s, the cohort-family local slot filler 20 s (key cohort_slot_filler, recorded by bottled_cohort_hook, not a cascade tier), everything else 2 s), so a new stack is cautious and a measured one is exact. A missing or unwritable
store only means the defaults apply: timing is bookkeeping and never blocks an answer.
"""
import json
import os
import statistics
import threading
import time
from pathlib import Path

VERSION = 'tier-timing/1'
FILE = 'tier-timings.json'
WINDOW = 15       # most recent measurements kept per tier
MIN_SAMPLES = 3
DEFAULTS = {'local_normalizer': 20.0, 'qwen_rewrite_compiler': 20.0, 'bottled_sql': 20.0, 'qwen_typed_interpreter': 20.0,
            'local_free_sql': 20.0, 'atomic_system_one': 20.0, 'cohort_slot_filler': 20.0, 'evidence_retrieval': 10.0, 'evidence_review': 10.0}
DEFAULT_OTHER = 2.0
_lock = threading.Lock()
_cache = {}  # path -> (mtime_ns, {tier: [seconds]})


def default(tier):
    return DEFAULTS.get(tier, DEFAULT_OTHER)


def _path(stack_path):
    return Path(stack_path) / FILE if stack_path else None


def _load(stack_path):
    path = _path(stack_path)
    if path is None: return {}
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    cached = _cache.get(str(path))
    if cached and cached[0] == mtime: return cached[1]
    try:
        data = json.loads(path.read_text())
        data = {k: [float(x) for x in v][-WINDOW:] for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        data = {}
    _cache[str(path)] = (mtime, data)
    return data


def estimate(stack_path, tier):
    """Median of the measured seconds, padded with the static default up to MIN_SAMPLES; the default when nothing is measured."""
    if os.environ.get('AGERE_TIER_TIMING', 'on') == 'off': return default(tier)
    seen = _load(stack_path).get(tier) or []
    return float(statistics.median(seen + [default(tier)] * max(0, MIN_SAMPLES - len(seen))))


def record(stack_path, timings):
    """Append {tier: [seconds, ...]} measurements (atomic write; failures are swallowed). -> True when stored."""
    path = _path(stack_path)
    if path is None or not timings: return False
    with _lock:
        try:
            data = {k: list(v) for k, v in _load(stack_path).items()}
            for tier, values in timings.items():
                data[tier] = (data.get(tier, []) + [round(float(x), 3) for x in values])[-WINDOW:]
            tmp = path.with_name(f'{FILE}.{os.getpid()}.{threading.get_ident()}.tmp')
            tmp.write_text(json.dumps(data, sort_keys=True))
            os.replace(tmp, path)
            _cache.pop(str(path), None)
            return True
        except (OSError, ValueError, TypeError):
            return False
