"""Cold caches per arm-pass, so arms never share a cached answer.

Where Agere keeps state that can answer a question again for free, and how each arm-pass gets its own copy:

- Provider response cache = the budget ledger (calls table keyed by request-body hash; a validated row replays at $0).
  Each arm-pass gets a fresh ledger file via AGERE_LEDGER_PATH, which every product caller reads at call time
  (gateway.default_ledger_path).
- Learned verified reuse (learning.sqlite3), the exact-question plan cache (question-plans.sqlite3), the graph/cohort
  result cache (result_cache / analyses tables in stack.sqlite3), the evidence query cache and provider receipts all live
  in the workspace's pipeline/ folder. Each arm-pass answers from its own workspace clone (clonefile(2), copy-on-write)
  whose pipeline/ folder is rebuilt empty by workspace_stack.prepare(); the source workspace is never written.
- The local MiniLM embedding memo (pipeline/model-cache.sqlite3) is a deterministic function cache, not an answer cache;
  it is cloned so latency does not include re-embedding the catalog (keep_encoder_memo=False drops it).
- AGERE_LEARNING_LOOP_DIR is pointed at the arm-pass folder (read-only summary, isolated for completeness).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
from pathlib import Path
import shutil
import sqlite3

# Not cloned into an arm's workspace: per-run outputs and every answer cache.
SKIP_TOP = {'receipts', 'pipeline', 'questions.jsonl', 'events.jsonl'}
SKIP_SUFFIX = ('-wal', '-shm', '.tmp', '.part', '-journal')


def _clonefile(src, dst):
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
        fn = libc.clonefile
    except (OSError, AttributeError, TypeError):
        return False
    fn.argtypes, fn.restype = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_int
    return fn(os.fsencode(str(src)), os.fsencode(str(dst)), 0) == 0


def _place(src, dst, allow_copy):
    if _clonefile(src, dst):
        return 'clone'
    if not allow_copy:
        raise OSError(f'cannot clone {src} (APFS clonefile unavailable); refusing to copy a multi-GB workspace')
    shutil.copy2(src, dst)
    return 'copy'


def clone_workspace(src_dir, dst_root, *, allow_copy=False, keep_encoder_memo=True):
    """Clone a workspace folder to dst_root/<slug> without any answer cache. Returns {'dir', 'methods'}.
    Large files are cloned (copy-on-write); directories other than SKIP_TOP are copied file by file the same way."""
    src_dir, dst_root = Path(src_dir), Path(dst_root)
    dst = dst_root / src_dir.name
    if dst.exists():
        raise FileExistsError(f'{dst} already exists (a clone is never reused across arm-passes)')
    dst.mkdir(parents=True)
    methods = {}
    for p in sorted(src_dir.iterdir()):
        if p.name in SKIP_TOP or p.name.endswith(SKIP_SUFFIX):
            continue
        if p.is_dir():
            for f in sorted(p.rglob('*')):
                rel = f.relative_to(src_dir)
                if f.is_dir():
                    (dst / rel).mkdir(parents=True, exist_ok=True)
                elif not f.name.endswith(SKIP_SUFFIX):
                    (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                    methods[str(rel)] = _place(f, dst / rel, True if f.stat().st_size < 64 << 20 else allow_copy)
        else:
            methods[p.name] = _place(p, dst / p.name, True if p.stat().st_size < 64 << 20 else allow_copy)
    (dst / 'receipts').mkdir()
    memo = src_dir / 'pipeline' / 'model-cache.sqlite3'
    if keep_encoder_memo and memo.is_file():
        (dst / 'pipeline').mkdir()
        methods['pipeline/model-cache.sqlite3'] = _place(memo, dst / 'pipeline' / 'model-cache.sqlite3', True)
    return {'dir': str(dst), 'methods': methods}


def preflight(src_dir, need_full=True):
    """Refuse to clone a workspace that is mid-build: status must be 'ready', no staging temp files, and (for full-pipeline
    arms) the semantic layer and record store must come from the same delivery. -> fingerprint dict for run.json."""
    import json
    src_dir = Path(src_dir)
    state = json.loads((src_dir / 'workspace.json').read_text())
    if state.get('status') != 'ready':
        raise RuntimeError(f"workspace {src_dir.name} is {state.get('status')!r}, not ready (a build or restage is running)")
    busy = [str(p.relative_to(src_dir)) for d in (src_dir, src_dir / 'stack') if d.is_dir() for p in d.iterdir()
            if p.name.endswith(('.tmp', '-journal', '.part'))]
    if busy:
        raise RuntimeError(f'workspace {src_dir.name} has files being written: {busy[:5]}')
    out = {'status': state.get('status'), 'as_of': state.get('as_of')}
    run = src_dir / 'stack' / 'run.json'
    if run.is_file():
        out['record_store_source_digest'] = json.loads(run.read_text()).get('source_digest')
    sem = src_dir / 'semantic.sqlite3'
    if sem.is_file():
        db = sqlite3.connect(f'file:{sem}?mode=ro', uri=True)
        try:
            meta = dict(db.execute('SELECT key, value FROM meta').fetchall())
            out['semantic_source_digest'] = meta.get('source_digest')
            out['semantic_version'] = meta.get('version')
        except sqlite3.Error:
            pass
        finally:
            db.close()
    if need_full and out.get('record_store_source_digest') != out.get('semantic_source_digest'):
        raise RuntimeError('semantic layer and record store come from different deliveries (source_digest differs): '
                           'the full pipeline cannot run; wait for the workspace build to finish')
    return out


def seed_ledger(path, cap=None):
    """A fresh ledger with the shared ledger's schema and authorization. The product ledger is uncapped (cap NULL); a new
    BudgetLedger would otherwise default to a 10-unit cap (20 calls at the 0.5 reservation), which no arm could finish in."""
    path = Path(path)
    if path.exists():
        return str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.executescript('''CREATE TABLE IF NOT EXISTS authorization (id INTEGER PRIMARY KEY, cap REAL);
        CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY,provider TEXT,model TEXT,reserved REAL,status TEXT,response TEXT,error TEXT,seconds REAL);
        CREATE TABLE IF NOT EXISTS imports (id TEXT PRIMARY KEY,reserved REAL);''')
        db.execute('INSERT OR IGNORE INTO authorization VALUES (1, ?)', (cap,))
        db.commit()
    finally:
        db.close()
    return str(path)


def ledger_rows(path, after_rowid=0):
    """[(rowid, provider, model, status, response_json, error, seconds)] for calls after after_rowid."""
    if not Path(path).is_file():
        return []
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return db.execute('SELECT rowid, provider, model, status, response, error, seconds FROM calls WHERE rowid > ? '
                          'ORDER BY rowid', (after_rowid,)).fetchall()
    finally:
        db.close()


def ledger_max_rowid(path):
    if not Path(path).is_file():
        return 0
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return db.execute('SELECT COALESCE(MAX(rowid), 0) FROM calls').fetchone()[0]
    finally:
        db.close()


def priced(rows):
    """Ledger rows -> excerpt dicts with usage and pricing-file cost (economics.usage_cost)."""
    import json
    from clinical_core.agere.economics import usage_cost
    out = []
    for rowid, provider, model, status, response, error, seconds in rows:
        usage = None
        cost = None
        if response:
            try:
                raw = json.loads(response)
                usage = raw.get('usage')
                est = usage_cost(model, usage)
                cost = est['estimated_usd'] if est else None
            except (ValueError, TypeError, AttributeError):
                pass
        out.append({'rowid': rowid, 'provider': provider, 'model': model, 'status': status, 'seconds': seconds,
                    'usage': usage, 'cost_usd': cost, 'error': (error or None) and str(error)[:300]})
    return out
