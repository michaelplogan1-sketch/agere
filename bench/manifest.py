"""Run manifest and fail-closed cost accounting for every scored run.

A manifest pins what a score depends on, written before anything runs: the data and build files (by content sha256),
the documentation the contestant reads, the question file, the scorer modules (and the version they declare, if any),
the model settings, the cache policy, the pricing table and the Agere code (git HEAD plus a digest of uncommitted
changes). Two runs are comparable only when their `pinned_sha256` match; `diff` names what changed.

Costs fail closed. A row's cost is one of:
  paid       a known estimate from reported usage (summed)
  replayed   a cached replay or an answer that made no new paid call; its cost is not "free work", so it is counted
             and reported apart, never folded into the paid total
  not_run    skipped before any call (budget reached)
  unknown    anything else: a missing or invalid estimate. Any unknown row makes the run total unknown (None).

    from bench import manifest
    m = manifest.build(questions_file=..., scorer_files=[...], data=[...], docs=[...], model_settings={...},
                       cache_policy='off', system='frontier_semantic')
    manifest.write(out_dir, m)
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

SCHEMA = 'agere-run-manifest/1'
REPO = Path(__file__).resolve().parents[1]
PRICING = REPO / 'config/agere/pricing.json'
CACHE_POLICIES = ('off', 'prompt_cache_only', 'replay_allowed')


def sha256_file(path, cache=None):
    """sha256 of a file; large warehouse files are cached by (path, size, mtime_ns) so a round does not rehash GBs."""
    path = Path(path).resolve()
    st = path.stat()
    key = f'{path}|{st.st_size}|{st.st_mtime_ns}'
    if cache is not None and key in cache:
        return cache[key]
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    digest = h.hexdigest()
    if cache is not None:
        cache[key] = digest
    return digest


def sha256_json(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def warehouse_stamp(path, cache=None):
    """Hash a warehouse by its manifest when one sits beside it (<file>.manifest.json or manifest.json in a release
    directory), else by its bytes."""
    path = Path(path)
    for manifest in (path.with_name(path.name + '.manifest.json'), path / 'manifest.json' if path.is_dir() else None):
        if manifest is not None and manifest.is_file():
            return {'path': str(path), 'hashed': 'manifest', 'manifest': str(manifest), 'sha256': sha256_file(manifest, cache)}
    if not path.is_file():
        raise FileNotFoundError(f'Warehouse not found: {path}')
    return {'path': str(path), 'hashed': 'file', 'bytes': path.stat().st_size, 'sha256': sha256_file(path, cache)}


def file_stamp(path, cache=None):
    """A file by its bytes, or a directory by every file under it (sorted relative path -> sha256)."""
    path = Path(path)
    if path.is_file():
        return {'path': str(path), 'sha256': sha256_file(path, cache)}
    if path.is_dir():
        files = {str(p.relative_to(path)): sha256_file(p, cache) for p in sorted(path.rglob('*'))
                 if p.is_file() and '__pycache__' not in p.parts and p.name != '.DS_Store'}
        return {'path': str(path), 'files': len(files), 'sha256': sha256_json(files)}
    raise FileNotFoundError(f'Not found: {path}')


def code_stamp(repo=REPO):
    """git HEAD plus a digest of uncommitted tracked changes (untracked files are not part of the code)."""
    def git(*a):
        return subprocess.run(['git', '-C', str(repo), *a], capture_output=True, text=True).stdout
    head = git('rev-parse', 'HEAD').strip() or None
    diff = git('diff', 'HEAD')
    return {'repo': str(repo), 'head': head, 'dirty': bool(diff), 'diff_sha256': hashlib.sha256(diff.encode()).hexdigest() if diff else None}


def scorer_version(paths):
    """The version string the scorer modules declare (SCORER_VERSION / VERSION = '...'), if any."""
    found = {}
    for p in paths:
        m = re.search(r"^(?:SCORER_VERSION|VERSION)\s*=\s*['\"]([^'\"]+)['\"]", Path(p).read_text(), re.M)
        if m:
            found[Path(p).name] = m.group(1)
    return found or None


def build(*, questions_file, scorer_files, data=(), docs=(), model_settings, cache_policy, system, extra=None, cache=None):
    if cache_policy not in CACHE_POLICIES:
        raise ValueError(f'cache_policy must be one of {CACHE_POLICIES}, not {cache_policy!r}')
    pinned = {
        'questions_file': file_stamp(questions_file, cache),
        'scorer': {'files': {str(p): sha256_file(p, cache) for p in scorer_files}, 'declared_version': scorer_version(scorer_files)},
        'data': [warehouse_stamp(p, cache) for p in data],
        'docs': [file_stamp(p, cache) for p in docs],
        'model_settings': model_settings,
        'cache_policy': cache_policy,
        'pricing': file_stamp(PRICING, cache) if PRICING.is_file() else None,
        'code': code_stamp(),
        'system': system,
        **({'extra': extra} if extra else {}),
    }
    return {'schema': SCHEMA, 'pinned': pinned, 'pinned_sha256': sha256_json(pinned),
            'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}


def write(out_dir, manifest, name='manifest.json'):
    path = Path(out_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=1, default=str))
    return path


def hash_cache(path):
    """A persistent (path, size, mtime) -> sha256 cache so multi-GB data files are hashed once per change."""
    path = Path(path)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_hash_cache(path, cache):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(cache))


def diff(a, b):
    """Top-level pinned fields that differ between two manifests (empty = comparable runs)."""
    pa, pb = a.get('pinned') or {}, b.get('pinned') or {}
    return sorted(k for k in set(pa) | set(pb) if sha256_json(pa.get(k)) != sha256_json(pb.get(k)))


# --- costs ------------------------------------------------------------------------------------------------------------

def _valid_cost(c):
    return isinstance(c, (int, float)) and not isinstance(c, bool) and c == c and c >= 0


def cost_class(row):
    """paid | replayed | not_run | unknown for one result row (see module docstring)."""
    if str(row.get('error') or '').startswith('skipped:'):
        return 'not_run'
    if row.get('cached') or row.get('replayed') or row.get('cost_basis') == 'replayed':
        return 'replayed'
    if row.get('new_paid_api_calls') == 0 and row.get('cost_basis') != 'paid':
        return 'replayed'
    if row.get('cost_scope') == 'incomplete_product_usage':
        return 'unknown'
    return 'paid' if _valid_cost(row.get('estimated_cost_usd')) else 'unknown'


def cost_summary(rows):
    """Paid total only when every row that ran has a known cost; replays are counted apart, never as $0 work."""
    rows = list(rows)
    classes = [cost_class(r) for r in rows]
    paid = [r['estimated_cost_usd'] for r, c in zip(rows, classes) if c == 'paid']
    unknown = [r.get('id') for r, c in zip(rows, classes) if c == 'unknown']
    out = {'rows': len(rows), 'paid_rows': len(paid), 'replayed_rows': classes.count('replayed'),
           'not_run_rows': classes.count('not_run'), 'unknown_cost_rows': len(unknown),
           'known_paid_usd': round(sum(paid), 6),
           'total_usd': None if unknown else round(sum(paid), 6),
           'complete': not unknown}
    if unknown:
        out['unknown_cost_ids'] = unknown[:50]
    return out


def format_cost(summary):
    total = 'UNKNOWN' if summary['total_usd'] is None else f"${summary['total_usd']:.2f}"
    text = f"cost {total} (paid rows {summary['paid_rows']}, known ${summary['known_paid_usd']:.2f})"
    if summary['unknown_cost_rows']:
        text += f"; {summary['unknown_cost_rows']} rows with unknown cost"
    if summary['replayed_rows']:
        text += f"; {summary['replayed_rows']} replayed/no-new-call rows reported apart"
    if summary['not_run_rows']:
        text += f"; {summary['not_run_rows']} not run"
    return text
