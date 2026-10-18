"""Workspace.ingest_delta and Workspace.refresh (native workspaces).

    tick = ws.ingest_delta(paths)          # files (relative to the delivery folder, or absolute); None = every new file
    report = ws.refresh(stale_only=True)   # re-run stale deterministic answers, flag model answers for re-ask

One delta = one tick:
 1. landing.ingest_paths parses only the new files (manifest.json is re-recorded when it changed).
 2. stage.restage: identity re-resolution + diff, adapters re-run only when their inputs changed, per-patient record
    diff applied to stack.sqlite3 / warehouse.sqlite3, run.json (as_of, source_digest).
 3. semantic.update: touched patients rebuilt in a mini build and spliced into stack/semantic.sqlite3 and the workspace
    semantic.sqlite3; as_of-dependent and global tables refreshed.
 4. Workspace extras: raw_<landing table> copies get the new files' rows (replaced files' rows removed), stg_person*
    copies are rewritten, delivery_file / delivery_gap are rebuilt, meta as_of / workspace set, stream-records rebuilt.
 5. ticks.record_tick writes the change log; replay.mark_stale marks dependent answers; verified caches are
    invalidated for stale answers and carried forward for fresh ones; the answer pipeline folder is re-prepared.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import time

from . import require, replay, semantic, stage, ticks

RAW_BATCH = 5000


def _safe_ident(name):
    return re.sub(r'[^A-Za-z0-9_]', '_', str(name)).lower()


def new_files(ws):
    """Files under the delivery folder that the landing database has not ingested unchanged."""
    from ..ingest import landing
    raw = Path(ws.raw_dir).resolve()
    land = landing.Landing(ws.landing_path)
    try:
        out = []
        for p in sorted(raw.rglob('*')):
            if not p.is_file() or p.name.startswith('.') or 'private' in p.relative_to(raw).parts:
                continue
            st = p.stat()
            if not land.done(str(p.relative_to(raw)), st.st_size, st.st_mtime):
                out.append(str(p.relative_to(raw)))
        return out
    finally:
        land.close()


def _landing_tables(path):
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()


def update_raw(ws, files, touched_tables):
    """raw_<table> copies in the workspace semantic layer: rows of the delta's files appended (a replaced file's earlier
    rows removed first), new landing columns added, new tables copied whole, identity tables rewritten."""
    land = sqlite3.connect(f'file:{ws.landing_path}?mode=ro', uri=True)
    db = sqlite3.connect(ws.semantic_path, timeout=120)
    rels = [f['relpath'] for f in files]
    stats = {}
    try:
        existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ltables = [r[0] for r in land.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        with db:
            for t in ltables:
                if t == 'ingest_files':
                    continue
                cols = [r[1] for r in land.execute(f'PRAGMA table_info("{t}")')]
                if not cols:
                    continue
                safe = []
                for c in cols:
                    s = _safe_ident(c) or 'col'
                    while s in safe:
                        s += '_'
                    safe.append(s)
                name = 'raw_' + _safe_ident(t)
                whole = name not in existing or t.startswith('stg_')
                if not whole and t not in touched_tables:
                    continue
                if whole:
                    if name in existing:
                        db.execute(f'DROP TABLE "{name}"')
                    db.execute(f'CREATE TABLE "{name}" (' + ', '.join(f'"{c}"' for c in safe) + ')')
                    rows = land.execute(f'SELECT ' + ', '.join(f'"{c}"' for c in cols) + f' FROM "{t}"')
                else:
                    have = [r[1] for r in db.execute(f'PRAGMA table_info("{name}")')]
                    for c in safe:
                        if c not in have:
                            db.execute(f'ALTER TABLE "{name}" ADD COLUMN "{c}"')
                    fcol = safe[cols.index('_file')] if '_file' in cols else None
                    if fcol is None:
                        continue
                    q = ','.join('?' * len(rels))
                    db.execute(f'DELETE FROM "{name}" WHERE "{fcol}" IN ({q})', rels)
                    rows = land.execute(f'SELECT ' + ', '.join(f'"{c}"' for c in cols) + f' FROM "{t}" WHERE _file IN ({q}) ORDER BY rowid', rels)
                ins = f'INSERT INTO "{name}" (' + ', '.join(f'"{c}"' for c in safe) + ') VALUES (' + ','.join('?' * len(cols)) + ')'
                n, batch = 0, []
                for row in rows:
                    batch.append(tuple(v if v is None or isinstance(v, (int, float, str, bytes)) else str(v) for v in row))
                    if len(batch) >= RAW_BATCH:
                        db.executemany(ins, batch); n += len(batch); batch = []
                if batch:
                    db.executemany(ins, batch); n += len(batch)
                stats[name] = {'mode': 'whole' if whole else 'append', 'rows': n}
    finally:
        land.close()
        db.close()
    return stats


def clone_workspace_semantic(ws, as_of):
    """Workspaces without raw_ copies (raw='none'): the workspace semantic layer is the canonical one plus meta as_of /
    workspace and the delivery tables, exactly what _build_native writes. Re-made as an APFS clone (copy-on-write: no
    extra space for the shared pages) instead of splicing a second copy. -> delivery summary."""
    from ..onboarding import workspace_stack
    src = ws.dir / 'stack' / 'semantic.sqlite3'
    tmp = ws.semantic_path.with_suffix('.tmp')
    tmp.unlink(missing_ok=True)
    if not workspace_stack._clonefile(src, tmp):
        import shutil
        shutil.copyfile(src, tmp)
    db = sqlite3.connect(tmp, timeout=120)
    try:
        with db:
            db.execute("DELETE FROM meta WHERE key IN ('as_of', 'workspace')")
            db.executemany('INSERT INTO meta VALUES (?, ?)', [('as_of', as_of), ('workspace', ws.slug)])
        summary = ws._delivery_tables(db)
    finally:
        db.close()
    for extra in ('-wal', '-shm'):
        Path(str(ws.semantic_path) + extra).unlink(missing_ok=True)
    tmp.replace(ws.semantic_path)
    return summary


def ingest_delta(ws, paths=None, *, refresh=False, prepare_pipeline=True, record_store=True, processing_date=None):
    """Incremental ingest of new delivery files into a native workspace. -> tick report."""
    require()
    from ..ingest import delivery, landing
    from ..onboarding import workspace as wsmod
    if not ws.native:
        raise ValueError('ingest_delta needs a native workspace')
    started = time.time()
    started_at = time.strftime('%Y-%m-%dT%H:%M:%S')
    timings = {}
    if pending_tick(ws) is not None:
        done = resume_pending(ws, refresh=refresh, prepare_pipeline=prepare_pipeline, record_store=record_store)
        if not paths and paths is not None:
            return done
    with wsmod._lock(ws.slug):
        raw = Path(ws.raw_dir).resolve()
        if paths is None:
            paths = new_files(ws)
        paths = [str(p) for p in paths]
        if (raw / 'manifest.json').is_file() and 'manifest.json' not in paths:
            paths.append('manifest.json')
        before = _landing_tables(ws.landing_path)
        t0 = time.time()
        # flat parser drift rows depend on parse order: replay the full-build order (flat_order.py)
        from . import flat_order
        from ..ingest import flat
        presets, reparse, flat_stats = flat_order.plan(raw, ws.landing_path, paths, stage.state_path(ws.dir / 'stack'))
        timings['flat_order'] = round(time.time() - t0, 1)
        order = sorted(set(paths) | set(reparse), key=flat_order._order)
        saved = (dict(flat._BASELINE), {k: set(v) for k, v in flat._RAW_VARIANTS.items()})
        try:
            ing = landing.ingest_paths(raw, ws.landing_path, order, force=set(reparse), before_file=flat_order.preset_hook(presets))
        finally:
            flat._BASELINE.clear(); flat._BASELINE.update(saved[0])
            flat._RAW_VARIANTS.clear(); flat._RAW_VARIANTS.update(saved[1])
        ing['flat_order'] = flat_stats
        timings['ingest'] = round(time.time() - t0, 1)
        changed = ing['changed']
        seen = {c['relpath'] for c in changed}
        recovered = [c for c in stage.pending_files(ws.dir / 'stack', ws.landing_path) if c['relpath'] not in seen]
        changed = changed + recovered
        data_files = [c for c in changed if c.get('tables') or c.get('replaced_tables')]
        touched_tables = set()
        for c in changed:
            touched_tables |= set(c.get('tables') or {}) | set(c.get('replaced_tables') or [])
        new_tables = bool(_landing_tables(ws.landing_path) - before) or bool(recovered)
        if recovered:
            touched_tables = None  # an interrupted delta: re-run every adapter (exact whatever was half done)
        if not data_files:
            ws._event('delta', files=0, skipped=ing['skipped'])
            return {'tick': None, 'files': 0, 'skipped': ing['skipped'], 'seconds': round(time.time() - started, 1)}
        stack_dir = ws.dir / 'stack'
        t0 = time.time()
        change = stage.restage(stack_dir, ws.landing_path, changed_tables=touched_tables, new_tables=new_tables)
        timings['stage'] = round(time.time() - t0, 1)
        journal = {'change': change, 'changed': changed, 'data_files': data_files, 'touched_tables': touched_tables,
                   'started': started, 'started_at': started_at, 'timings': timings, 'profile_done': False,
                   'ing': {k: ing.get(k) for k in ('skipped', 'errors', 'flat_order')}}
        _write_journal(ws, journal)
        return _finish(ws, journal, refresh=refresh, prepare_pipeline=prepare_pipeline, record_store=record_store,
                       processing_date=processing_date)


def _journal_path(ws):
    p = ws.dir / 'streaming'
    p.mkdir(exist_ok=True)
    return p / 'pending-tick.pickle'


def _write_journal(ws, journal):
    """A restaged but unfinished tick (crash recovery): restage already moved the store and the per-patient digests, so
    the semantic update, change log and replay of that change must still run; the next ingest_delta finishes it first."""
    import pickle
    path = _journal_path(ws)
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(pickle.dumps(journal, protocol=4))
    tmp.replace(path)


def pending_tick(ws):
    import pickle
    path = _journal_path(ws)
    return pickle.loads(path.read_bytes()) if path.is_file() else None


def resume_pending(ws, **kw):
    """Finish an interrupted tick, if any. -> its tick report or None."""
    from ..onboarding import workspace as wsmod
    journal = pending_tick(ws)
    if journal is None:
        return None
    with wsmod._lock(ws.slug):
        return _finish(ws, journal, resumed=True, **kw)


def _finish(ws, journal, *, refresh=False, prepare_pipeline=True, record_store=True, processing_date=None, resumed=False):
    from ..ingest import delivery
    from ..onboarding import workspace as wsmod
    change, changed, data_files = journal['change'], journal['changed'], journal['data_files']
    touched_tables, started, started_at = journal['touched_tables'], journal['started'], journal['started_at']
    timings, ing = dict(journal['timings']), journal['ing']
    raw = Path(ws.raw_dir).resolve()
    stack_dir = ws.dir / 'stack'
    if True:
        t0 = time.time()
        clone = ws.state.get('raw') == 'none'

        def profile_written():
            journal['profile_done'] = True
            _write_journal(ws, journal)
        sem = semantic.update(stack_dir, change, targets=[] if clone else [ws.semantic_path],
                              profile_done=journal.get('profile_done', False), on_profile=profile_written)
        timings['semantic'] = round(time.time() - t0, 1)
        # workspace extras
        t0 = time.time()
        if clone:
            raw_stats = {}
            delivery_summary = clone_workspace_semantic(ws, change['as_of'])
        else:
            raw_stats = update_raw(ws, changed, (touched_tables or {c for f in changed for c in (f.get('tables') or {})})
                               | {'stg_person_map', 'stg_person'})
            db = sqlite3.connect(ws.semantic_path, timeout=120)
            try:
                with db:
                    db.execute("DELETE FROM meta WHERE key IN ('as_of', 'workspace')")
                    db.executemany('INSERT INTO meta VALUES (?, ?)', [('as_of', change['as_of']), ('workspace', ws.slug)])
                delivery_summary = ws._delivery_tables(db)
            finally:
                db.close()
        timings['workspace_tables'] = round(time.time() - t0, 1)
        if record_store:
            timings['record_store'] = semantic.record_store_refresh(stack_dir)
        # stage.json (read by verify / docs) and workspace state
        rep_path = ws.dir / 'stage.json'
        rep = json.loads(rep_path.read_text()) if rep_path.is_file() else {}
        rep.update(identity=change['identity'], streams=change['streams'], unresolved=change['unresolved'], records=change['records'],
                   people=change['people'], as_of=change['as_of'], superseded=change['superseded'], issues=change['issues'],
                   tables=change['streams'], streaming={'tick_at': started_at, 'touched_patients': len(change['touched_patients'])})
        rep_path.write_text(json.dumps(rep, indent=1, default=str))
        manifest = delivery.load_manifest(raw)
        tick, kinds = ticks.record_tick(ws.dir, started=started_at, files=data_files, change=change, semantic_report=sem,
                                        table_changes=sem.get('table_changes'), manifest=manifest,
                                        processing_date=processing_date or max((manifest.get(f['relpath'], {}).get('delivered') or ''
                                                                                for f in data_files), default=None),
                                        seconds=None)
        stale = replay.mark_stale(ws.dir, tick)
        old_context = _context(ws.dir / 'pipeline')
        if prepare_pipeline:
            t0 = time.time()
            ws._do_pipeline()
            timings['pipeline'] = round(time.time() - t0, 1)
        new_context = _context(ws.dir / 'pipeline')
        inval = replay.invalidate_caches(ws.dir, tick, stale, old_context=old_context, new_context=new_context)
        ws._update(as_of=change['as_of'])
        s = ws.state
        s['steps']['delta'] = {'at': wsmod._now(), 'tick': tick, 'files': len(data_files), 'touched_patients': len(change['touched_patients']),
                               'as_of': change['as_of'], 'flags': kinds}
        ws._save(s)
        refreshed = replay.refresh(ws.dir, ws.semantic_path, change['as_of'], tick_id=tick) if refresh else None
        seconds = round(time.time() - started, 1)
        tdb = ticks.connect(ws.dir)
        try:
            with tdb:
                tdb.execute('UPDATE ticks SET seconds = ? WHERE tick_id = ?', (seconds, tick))
        finally:
            tdb.close()
        _journal_path(ws).unlink(missing_ok=True)
        out = {'tick': tick, 'files': len(data_files), 'skipped': ing['skipped'], 'parse_errors': ing['errors'], 'resumed': resumed,
               'flat_reparsed': ing['flat_order'],
               'touched_patients': len(change['touched_patients']), 'patients_rebuilt': sem['patients_rebuilt'],
               'as_of_rebuilt': sem['as_of_rebuilt'], 'as_of_before': change['as_of_before'], 'as_of': change['as_of'],
               'records_added': len(change['records_added']), 'records_removed': len(change['records_removed']),
               'records_changed': len(change['records_changed']), 'identity': change['identity_diff'], 'flags': kinds,
               'tables_changed': {t: v['rows_changed'] for t, v in (sem.get('table_changes') or {}).items() if v['rows_changed']},
               'stale_answers': stale, 'cache_invalidations': len(inval), 'refresh': refreshed, 'modules': change['modules'],
               'timings': {**timings, 'stage_detail': change['timings'], 'semantic_detail': sem['timings']},
               'delivery': {k: delivery_summary.get(k) for k in ('files', 'gaps') if isinstance(delivery_summary, dict)},
               'raw_tables': raw_stats, 'seconds': seconds}
        ws._event('delta', **{k: v for k, v in out.items() if k in ('tick', 'files', 'touched_patients', 'as_of', 'flags', 'seconds')})
        return out


def bootstrap(ws, *, profile_counts=True):
    """One-time streaming setup on a batch-built native workspace: record the adapters' outputs and read sets and the
    per-patient digests (stage.restage over an unchanged landing touches no patient), and fill the free-text reader cache
    (note_facts.extract over every note, therapy_barriers over the whole build; both idempotent). -> timings."""
    require()
    from ..clinical import note_facts
    from . import memo
    t = {}
    stack_dir = ws.dir / 'stack'
    t0 = time.time()
    if not stage.has_state(stack_dir):
        change = stage.restage(stack_dir, ws.landing_path, changed_tables=None)
        t['stage_bootstrap'] = round(time.time() - t0, 1)
        t['touched_on_bootstrap'] = len(change['touched_patients'])
        if change['touched_patients']:  # the store did not match its landing: bring the semantic layer along
            semantic.update(stack_dir, change, targets=[ws.semantic_path])
    from . import flat_order, profile_state
    t0 = time.time()
    _, _, fst = flat_order.plan(Path(ws.raw_dir).resolve(), ws.landing_path, [], stage.state_path(stack_dir))  # header cache
    t['flat_headers'] = {**fst, 'seconds': round(time.time() - t0, 1)}
    t0 = time.time()
    if profile_counts and not profile_state.ready(stage.state_path(stack_dir)):
        profile_state.build(stage.state_path(stack_dir), stack_dir / 'stack.sqlite3')
        t['profile_counts'] = round(time.time() - t0, 1)
    t0 = time.time()
    with memo.text_readers(stack_dir) as m:
        src = sqlite3.connect(f'file:{stack_dir / "semantic.sqlite3"}?mode=ro', uri=True)
        try:
            admit, _ = note_facts.admitted(None)
            for _ in note_facts.rows(src, admit):
                pass
        finally:
            src.close()
        semantic.rerun_therapy(stack_dir / 'semantic.sqlite3')
        t['text_cache_entries'] = m.stats['misses']
    t['text_cache_warm'] = round(time.time() - t0, 1)
    if not semantic.pos_inputs_ready(stack_dir):
        t['pos_inputs'] = semantic.bootstrap_pos_inputs(stack_dir)
    return t


def _context(pipe):
    try:
        from ..clinical import semantic_layer
        meta = json.loads((Path(pipe) / 'run.json').read_text())
        return f"{meta['source_digest']}|{semantic_layer.VERSION}"
    except (OSError, ValueError, KeyError):
        return None


def refresh(ws, *, stale_only=True):
    require()
    as_of = ws.state.get('as_of')
    return replay.refresh(ws.dir, ws.semantic_path, as_of, stale_only=stale_only)
