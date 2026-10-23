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
import os
from pathlib import Path
import re
import sqlite3
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


def pin_answer_key(out_dir, score_output, name='manifest.json'):
    """After scoring: pin the sealed key the scorer used (its answers_sha256 and scorer_version, from score() output;
    Agere never reads the key). A score output without the digest pins answer_key as unknown, which never compares equal
    to a pinned one."""
    try:
        result = score_output if isinstance(score_output, dict) else json.loads(score_output)
    except (TypeError, ValueError):
        result = {}
    path = Path(out_dir) / name
    m = json.loads(path.read_text())
    m['pinned']['answer_key'] = {'answers_sha256': result.get('answers_sha256'), 'scorer_version': result.get('scorer_version')}
    m['pinned_sha256'] = sha256_json(m['pinned'])
    path.write_text(json.dumps(m, indent=1, default=str))
    return m


# -- run identity (resume safety) -------------------------------------------------------------------------------------
_FLAG_SECRET = re.compile(r'(?:key|token|secret|password|credential)', re.I)
CERTIFIED_STORES = ('learning.sqlite3', 'question-plans.sqlite3', 'vcache.sqlite3')  # what the contestant knows beyond its code


def agere_flags(environ=None):
    """Every AGERE_* environment setting of this process (secrets by name excluded), sorted."""
    environ = os.environ if environ is None else environ
    return {k: environ[k] for k in sorted(environ) if k.startswith('AGERE_') and not _FLAG_SECRET.search(k)}


def _table_digest(db, table):
    h = hashlib.sha256()
    for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
        h.update(repr(row).encode())
    return h.hexdigest()


def stack_digest(stack_dir):
    """Content digest of what defines the contestant at run time: run.json (the data release) and the rows of the certified
    stores (learning, plans, verified-cache), table by table, read-only (so a WAL checkpoint does not change it). None when
    no stack directory was given."""
    if not stack_dir:
        return None
    stack = Path(stack_dir)
    parts = {}
    run = stack / 'run.json'
    parts['run.json'] = sha256_file(run) if run.is_file() else None
    for name in CERTIFIED_STORES:
        path = stack / name
        if not path.is_file():
            parts[name] = None
            continue
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            parts[name] = {t: _table_digest(db, t) for t in tables}
        except sqlite3.Error as exc:
            parts[name] = f'unreadable: {type(exc).__name__}'
        finally:
            db.close()
    return sha256_json(parts)


def run_identity(stack_dir, system):
    """Manifest `extra`: the AGERE_* flags and the stack digest a resume must still match (agere runs only)."""
    if system != 'agere':
        return None
    return {'agere_flags': agere_flags(), 'stack_digest': stack_digest(stack_dir), 'evaluation_mode': True}


RESUME_SETTINGS_IGNORED = ('jobs', 'budget_usd', 'url')  # how a run is driven, not what it measures


def resume_conflicts(old, new):
    """Human-readable differences that forbid continuing a saved run with the current one (empty = may resume)."""
    po, pn = old.get('pinned') or {}, new.get('pinned') or {}
    out = []
    co, cn = po.get('code') or {}, pn.get('code') or {}
    if co.get('head') != cn.get('head'):
        out.append(f"code commit changed: saved {str(co.get('head'))[:12]} vs now {str(cn.get('head'))[:12]}")
    elif co.get('diff_sha256') != cn.get('diff_sha256'):
        out.append('uncommitted code changed since the run started (same commit, different working-tree diff)')
    eo, en = po.get('extra') or {}, pn.get('extra') or {}
    if 'stack_digest' not in eo:
        out.append('the saved manifest predates run identity (no stack digest / AGERE_* flags recorded)')
    elif eo.get('stack_digest') != en.get('stack_digest'):
        out.append('stack digest changed (the certified stores or run.json differ from when the run started)')
    if (eo.get('agere_flags') or {}) != (en.get('agere_flags') or {}):
        names = sorted({k for k in set(eo.get('agere_flags') or {}) | set(en.get('agere_flags') or {})
                        if (eo.get('agere_flags') or {}).get(k) != (en.get('agere_flags') or {}).get(k)})
        out.append('AGERE_* flags differ: ' + ', '.join(names[:12]))
    if po.get('system') != pn.get('system'):
        out.append(f"system differs: {po.get('system')} vs {pn.get('system')}")
    ms_o = {k: v for k, v in (po.get('model_settings') or {}).items() if k not in RESUME_SETTINGS_IGNORED}
    ms_n = {k: v for k, v in (pn.get('model_settings') or {}).items() if k not in RESUME_SETTINGS_IGNORED}
    if ms_o != ms_n:
        out.append('settings differ (mode / model / as-of / ...): ' + ', '.join(sorted(k for k in set(ms_o) | set(ms_n) if ms_o.get(k) != ms_n.get(k))))
    for field in ('questions_file', 'scorer', 'data', 'docs', 'cache_policy', 'pricing'):
        if sha256_json(po.get(field)) != sha256_json(pn.get(field)):
            out.append(f'{field} differs')
    return out


def stamp_finished(out_dir, name='manifest.json'):
    """Record the run's end time beside its start (created_at): the wall-clock window of everything it wrote."""
    path = Path(out_dir) / name
    m = json.loads(path.read_text())
    m['finished_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    path.write_text(json.dumps(m, indent=1, default=str))


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


# The ruler: fields that must match for two scores to be comparable. The rest describe the contestant being measured.
MEASUREMENT_FIELDS = ('questions_file', 'scorer', 'answer_key', 'data', 'cache_policy', 'pricing')


def compare(baseline, current):
    """{'comparable': bool, 'measurement_changed': [...], 'contestant_changed': [...]} against a frozen baseline."""
    changed = diff(baseline, current)
    ruler = [k for k in changed if k in MEASUREMENT_FIELDS]
    return {'comparable': not ruler, 'measurement_changed': ruler, 'contestant_changed': [k for k in changed if k not in ruler]}


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
