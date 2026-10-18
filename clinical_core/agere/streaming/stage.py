"""Incremental canonical staging (the streaming counterpart of ingest/canonical.stage).

    change = restage(stack_dir, landing_path, changed_tables={...}, new_tables=False)

What is recomputed, and why it equals a full stage of the same landing database:

1. Identity. The union-find over every source key is re-run (identity.build, ~4 s at pilot scale: the graph collection is
   the cost, so a component-scoped re-resolution would read the same tables). The old and new stg_person_map /
   stg_person are diffed: only keys whose person changed and persons whose key set or payload changed are carried
   forward as touched (a new key that links two charts shows up as an identity merge: two old persons -> one).
2. Adapters. Each adapter module (adapt_claims, adapt_clinical) is re-run only when a landing table it read on its last
   run changed, a landing table was created (modules list tables), or the identity map changed (adapters resolve people
   through it). The read set is recorded with an sqlite authorizer while the module runs. Adapters choose one
   authoritative feed per entity and collapse duplicates across files and vendors (resend/v2 files, migration
   duplicates, HL7 presence checks against the EHR, CCLF vs 837 matching), so they are not re-run per patient: a
   patient-filtered run would not see the competing rows. A module that is not re-run reuses its previous output
   (record ids, patient, stream, payload hash, order) from the streaming state.
3. Records. The combined output is ordered exactly as canonical.stage inserts it (INSERT OR REPLACE: the last occurrence
   of a record id wins and takes the last position), superseded versions are removed from the visible store, and a
   per-patient digest of (record id, payload hash) in that order is compared with the previous one. A patient is touched
   when its visible digest, its all-versions digest or its person payload changed. Only touched patients' rows are
   deleted and re-inserted in stack.sqlite3 (records, patients) and warehouse.sqlite3 (records, patients); stg_superseded,
   stg_unresolved and (when the entity tables were read by the delta) source_entities are rewritten (small).
4. run.json gets the new as_of (same max-event-date rule as canonical) and a new source_digest (as every stage does).

The returned change names touched patients, per-record additions / removals / payload changes (with old and new payloads
for the change log), identity merges, as_of before and after and whether the entity reference tables changed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from ..ingest import canonical, identity

STATE_FILE = 'streaming-state.sqlite3'
STATE_DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS adapter_out(module TEXT, seq INTEGER, record_id TEXT, patient_id TEXT, stream TEXT, sha TEXT,
  supersedes TEXT, event_date TEXT, available_date TEXT, PRIMARY KEY(module, seq));
CREATE TABLE IF NOT EXISTS adapter_unresolved(module TEXT, seq INTEGER, stream TEXT, source_patient_id TEXT, lineage TEXT);
CREATE TABLE IF NOT EXISTS module_reads(module TEXT, tbl TEXT, PRIMARY KEY(module, tbl));
CREATE TABLE IF NOT EXISTS patient_digest(patient_id TEXT PRIMARY KEY, visible TEXT, versions TEXT, person TEXT);
CREATE TABLE IF NOT EXISTS processed_files(relpath TEXT PRIMARY KEY, bytes INTEGER, mtime REAL);
"""
ENTITY_MODULE = 'adapt_entities'


def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()[:24]


def _tracer(reads):
    def cb(action, a1, a2, dbname, source):
        if action == sqlite3.SQLITE_READ and a1:
            reads.add(a1)
        elif action == sqlite3.SQLITE_PRAGMA and a2:
            reads.add(a2.strip('"'))
        return sqlite3.SQLITE_OK
    return cb


def state_path(stack_dir):
    return Path(stack_dir) / STATE_FILE


def _state(stack_dir):
    db = sqlite3.connect(state_path(stack_dir))
    db.executescript(STATE_DDL)
    return db


def has_state(stack_dir):
    p = state_path(stack_dir)
    if not p.is_file():
        return False
    db = sqlite3.connect(p)
    try:
        return db.execute("SELECT value FROM meta WHERE key = 'bootstrapped'").fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        db.close()


def _identity_snapshot(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'stg_person_map' not in tables:
        return {}, {}
    keys = dict(db.execute('SELECT source_key, person_id FROM stg_person_map'))
    persons = dict(db.execute('SELECT person_id, payload FROM stg_person'))
    return keys, persons


def identity_diff(old_keys, old_persons, new_keys, new_persons):
    """-> dict: keys whose person changed (incl. added/removed), persons added/removed/payload-changed, merges, splits."""
    keys_changed = {k: (old_keys.get(k), new_keys.get(k)) for k in set(old_keys) | set(new_keys)
                    if old_keys.get(k) != new_keys.get(k)}
    added = sorted(set(new_persons) - set(old_persons))
    removed = sorted(set(old_persons) - set(new_persons))
    payload = sorted(p for p in set(new_persons) & set(old_persons) if new_persons[p] != old_persons[p])
    by_new = defaultdict(set)
    by_old = defaultdict(set)
    for k, (o, n) in keys_changed.items():
        pass
    for k, n in new_keys.items():
        o = old_keys.get(k)
        if n and o:
            by_new[n].add(o)
            by_old[o].add(n)
    merges = {n: sorted(os_) for n, os_ in by_new.items() if len(os_) > 1}
    splits = {o: sorted(ns) for o, ns in by_old.items() if len(ns) > 1}
    return {'keys_changed': keys_changed, 'persons_added': added, 'persons_removed': removed, 'persons_payload_changed': payload,
            'merges': merges, 'splits': splits}


def _pathkey(v):
    """Sort key for a landing _file equal to Python's Path ordering (parts compared in turn): '/' sorts below every
    other character."""
    return None if v is None else str(v).replace('/', '\x01')


def full_build_order(db):
    """Shadow every landing table with a TEMP VIEW of the same name ordered as a full ingest writes it (files in path
    order, rows in file order). A full build parses every file in path order, so its landing tables are in that order;
    a streamed landing appends each delta's files at the end. Adapters break ties by the order they read rows (for
    example which CCLF Part B line pairs with an MSSP 837 line), so they must read the full-build order. Unqualified
    table names resolve to the temp schema first; nothing is written."""
    db.create_function('agere_pathkey', 1, _pathkey, deterministic=True)
    n = 0
    for (t,) in db.execute("SELECT name FROM main.sqlite_master WHERE type='table'").fetchall():
        cols = [r[1] for r in db.execute(f'PRAGMA main.table_info("{t}")')]
        if '_file' in cols and '_row' in cols:
            db.execute(f'DROP VIEW IF EXISTS temp."{t}"')
            db.execute(f'CREATE TEMP VIEW "{t}" AS SELECT * FROM main."{t}" ORDER BY agere_pathkey(_file), _row')
            n += 1
    return n


def drop_full_build_order(db):
    for (t,) in db.execute("SELECT name FROM temp.sqlite_master WHERE type='view'").fetchall():
        db.execute(f'DROP VIEW temp."{t}"')


def _run_module(mod, db, resolve, reads):
    """Run one adapter module like canonical.stage does. -> (entries, payloads, unresolved)."""
    db.set_authorizer(_tracer(reads))
    entries, payloads, unresolved = [], {}, []
    try:
        for rec in mod.records(db, resolve):
            rec['record_id'] = canonical._record_id(rec)
            if not rec.get('patient_id'):
                unresolved.append((rec.get('source_table'), rec.get('source_patient_id'), json.dumps(rec.get('_lineage'), default=str)))
                continue
            rec.setdefault('synthetic', True)
            rec.setdefault('identity_status', 'matched')
            payload = json.dumps(rec, default=str)
            sha = _sha(payload)
            entries.append((rec['record_id'], rec['patient_id'], rec['source_table'], sha, rec.get('supersedes_record_id'),
                            (rec.get('event_date') or None) if isinstance(rec.get('event_date'), str) else None,
                            (rec.get('available_date') or None) if isinstance(rec.get('available_date'), str) else None))
            payloads[(rec['record_id'], sha)] = payload
    finally:
        db.set_authorizer(None)
    return entries, payloads, unresolved


def _entities(landing_db, reads):
    from ..ingest import adapt_entities
    landing_db.set_authorizer(_tracer(reads))
    try:
        return [(k, i, json.dumps(p, default=str)) for k, i, p in adapt_entities.entities(landing_db)]
    finally:
        landing_db.set_authorizer(None)


def _digests(final, superseded, persons):
    """Per patient: digest of visible (record id, sha) in stack order, of all versions, and of the person payload."""
    vis, ver = defaultdict(hashlib.sha256), defaultdict(hashlib.sha256)
    for rid, pid, stream, sha, sup, ev, av in final:
        token = f'{rid}\x1f{sha}\x1e'.encode()
        ver[pid].update(token)
        if rid not in superseded:
            vis[pid].update(token)
    out = {}
    for pid in set(vis) | set(ver) | set(persons):
        out[pid] = (vis[pid].hexdigest()[:24] if pid in vis else None, ver[pid].hexdigest()[:24] if pid in ver else None,
                    _sha(persons[pid]) if pid in persons else None)
    return out


def _current_digests(stack_dir):
    """Digests of the store as it stands (bootstrap from a batch-built workspace)."""
    stack = sqlite3.connect(f'file:{Path(stack_dir) / "stack.sqlite3"}?mode=ro', uri=True)
    wh_path = Path(stack_dir).parent / 'warehouse.sqlite3'
    vis, ver = defaultdict(hashlib.sha256), defaultdict(hashlib.sha256)
    try:
        persons = dict(stack.execute('SELECT id, payload FROM patients'))
        for rid, pid, payload in stack.execute('SELECT id, patient, payload FROM records ORDER BY rowid'):
            vis[pid].update(f'{rid}\x1f{_sha(payload)}\x1e'.encode())
    finally:
        stack.close()
    wh = sqlite3.connect(f'file:{wh_path}?mode=ro', uri=True)
    try:
        for rid, pid, payload in wh.execute('SELECT id, patient, payload FROM records ORDER BY rowid'):
            ver[pid].update(f'{rid}\x1f{_sha(payload)}\x1e'.encode())
    finally:
        wh.close()
    return {pid: (vis[pid].hexdigest()[:24] if pid in vis else None, ver[pid].hexdigest()[:24] if pid in ver else None,
                  _sha(persons[pid]) if pid in persons else None) for pid in set(vis) | set(ver) | set(persons)}


def _chunks(seq, n=500):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def restage(stack_dir, landing_path, *, changed_tables=None, new_tables=False, force_all=False):
    """Bring stack.sqlite3, warehouse.sqlite3 and run.json up to date with the landing database. changed_tables: landing
    tables written or cleared by the delta (None = unknown: every module re-runs). -> change dict (see module doc)."""
    started = time.time()
    stack_dir, landing_path = Path(stack_dir), Path(landing_path)
    stack_path = stack_dir / 'stack.sqlite3'
    wh_path = stack_dir.parent / 'warehouse.sqlite3'
    timings = {}
    bootstrapped = has_state(stack_dir)
    st = _state(stack_dir)
    land = sqlite3.connect(landing_path)
    land.execute('PRAGMA journal_mode=WAL')
    try:
        # 1. identity
        t0 = time.time()
        old_keys, old_persons = _identity_snapshot(land)
        full_build_order(land)
        ident_report = identity.build(land)
        land.commit()
        new_keys, new_persons = _identity_snapshot(land)
        idiff = identity_diff(old_keys, old_persons, new_keys, new_persons)
        timings['identity'] = round(time.time() - t0, 2)
        resolve = canonical.resolver(land)
        # 2. adapters
        mods = canonical.adapters()
        prev_reads = defaultdict(set)
        for m, t in st.execute('SELECT module, tbl FROM module_reads'):
            prev_reads[m].add(t)
        changed_tables = None if changed_tables is None else set(changed_tables)
        identity_moved = bool(idiff['keys_changed'])

        def must_run(name):
            if force_all or not bootstrapped or changed_tables is None or name not in prev_reads:
                return True
            reads = prev_reads[name]
            if new_tables and reads & {'sqlite_master', 'sqlite_schema'}:
                return True
            if reads & changed_tables:
                return True
            return identity_moved and name != ENTITY_MODULE
        order, payloads, unresolved_all, ran = [], {}, [], {}
        if bootstrapped:
            old_sha = dict(st.execute('SELECT record_id, sha FROM adapter_out'))
        else:  # bootstrap from a batch-built store: every stored version, so unchanged payloads are not held in memory
            whr = sqlite3.connect(f'file:{wh_path}?mode=ro', uri=True)
            try:
                old_sha = {rid: _sha(p) for rid, p in whr.execute('SELECT id, payload FROM records')}
            finally:
                whr.close()
        for mi, mod in enumerate(mods):
            name = mod.__name__.rsplit('.', 1)[-1]
            t0 = time.time()
            if must_run(name):
                reads = set()
                entries, pl, unres = _run_module(mod, land, resolve, reads)
                ran[name] = {'records': len(entries), 'seconds': round(time.time() - t0, 1), 'reads': sorted(reads)}
                # keep payloads only where the (id, sha) is new: unchanged payloads are read back from the store
                for key, p in pl.items():
                    if old_sha.get(key[0]) != key[1]:
                        payloads[key] = p
                st.execute('DELETE FROM adapter_out WHERE module = ?', (name,))
                st.executemany('INSERT INTO adapter_out VALUES (?,?,?,?,?,?,?,?,?)',
                               ((name, i, *e) for i, e in enumerate(entries)))
                st.execute('DELETE FROM adapter_unresolved WHERE module = ?', (name,))
                st.executemany('INSERT INTO adapter_unresolved VALUES (?,?,?,?,?)', ((name, i, *u) for i, u in enumerate(unres)))
                st.execute('DELETE FROM module_reads WHERE module = ?', (name,))
                st.executemany('INSERT INTO module_reads VALUES (?,?)', ((name, t) for t in sorted(reads)))
            else:
                entries = [tuple(r) for r in st.execute(
                    'SELECT record_id, patient_id, stream, sha, supersedes, event_date, available_date FROM adapter_out '
                    'WHERE module = ? ORDER BY seq', (name,))]
                unres = [tuple(r) for r in st.execute('SELECT stream, source_patient_id, lineage FROM adapter_unresolved '
                                                      'WHERE module = ? ORDER BY seq', (name,))]
                ran[name] = {'reused': len(entries)}
            order.extend(entries)
            unresolved_all.extend(unres)
            timings[name] = round(time.time() - t0, 1)
        del old_sha
        # entity reference tables (clinicians, organizations) for the received store
        t0 = time.time()
        entities, entities_changed = None, False
        if must_run(ENTITY_MODULE):
            reads = set()
            entities = _entities(land, reads)
            st.execute('DELETE FROM module_reads WHERE module = ?', (ENTITY_MODULE,))
            st.executemany('INSERT INTO module_reads VALUES (?,?)', ((ENTITY_MODULE, t) for t in sorted(reads)))
        timings['entities'] = round(time.time() - t0, 1)
        drop_full_build_order(land)
        # 3. final record order (INSERT OR REPLACE semantics), versions, digests
        t0 = time.time()
        last = {}
        for pos, e in enumerate(order):
            last[e[0]] = pos
        final = [order[p] for p in sorted(last.values())]
        del order, last
        superseded_rows = [(e[4], e[0], e[2]) for e in final if e[4] is not None]
        superseded = {r[0] for r in superseded_rows}
        new_dig = _digests(final, superseded, new_persons)
        if bootstrapped:
            old_dig = {r[0]: tuple(r[1:]) for r in st.execute('SELECT patient_id, visible, versions, person FROM patient_digest')}
        else:
            old_dig = _current_digests(stack_dir)
        touched = sorted(p for p in set(new_dig) | set(old_dig) if new_dig.get(p) != old_dig.get(p))
        tset = set(touched)
        timings['diff'] = round(time.time() - t0, 1)
        # 4. apply to stack and warehouse
        t0 = time.time()
        stack = sqlite3.connect(stack_path)
        wh = sqlite3.connect(wh_path)
        release = wh.execute('SELECT id FROM releases').fetchone()[0]
        old_records = {}  # touched patients' previous visible records: id -> (patient, stream, sha, payload)
        old_versions = {}
        for chunk in _chunks(touched):
            q = ','.join('?' * len(chunk))
            for rid, pid, stream, payload in stack.execute(f'SELECT id, patient, stream, payload FROM records WHERE patient IN ({q})', chunk):
                old_records[rid] = (pid, stream, _sha(payload), payload)
            for rid, pid, stream, payload in wh.execute(f'SELECT id, patient, stream, payload FROM records WHERE release = ? AND patient IN ({q})',
                                                        [release, *chunk]):
                old_versions[rid] = (pid, stream, _sha(payload), payload)
        new_t = [e for e in final if e[1] in tset]

        def payload_of(e):
            p = payloads.get((e[0], e[3]))
            if p is not None:
                return p
            for src in (old_versions, old_records):
                o = src.get(e[0])
                if o is not None and o[2] == e[3]:
                    return o[3]
            # an unchanged record that moved here from an untouched patient's store is impossible (both are touched);
            # fall back to the warehouse by id
            row = wh.execute('SELECT payload FROM records WHERE release = ? AND id = ?', (release, e[0])).fetchone()
            if row is None or _sha(row[0]) != e[3]:
                raise RuntimeError(f'streaming stage: no payload for record {e[0]}')
            return row[0]
        new_payload = {e[0]: payload_of(e) for e in new_t}
        with stack:
            stack.execute('CREATE TEMP TABLE IF NOT EXISTS touched(pid TEXT PRIMARY KEY)')
            stack.execute('DELETE FROM touched')
            stack.executemany('INSERT INTO touched VALUES (?)', ((p,) for p in touched))
            stack.execute('DELETE FROM records WHERE patient IN (SELECT pid FROM touched)')
            stack.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',
                              ((e[0], e[1], e[2], new_payload[e[0]]) for e in new_t if e[0] not in superseded))
            stack.execute('DELETE FROM patients WHERE id IN (SELECT pid FROM touched)')
            stack.executemany('INSERT OR REPLACE INTO patients VALUES (?,?)', ((p, new_persons[p]) for p in touched if p in new_persons))
            stack.execute('DELETE FROM stg_superseded')
            stack.executemany('INSERT INTO stg_superseded VALUES (?,?,?)', superseded_rows)
            stack.execute('DELETE FROM stg_unresolved')
            stack.executemany('INSERT INTO stg_unresolved VALUES (?,?,?)', unresolved_all)
        with wh:
            wh.execute('CREATE TEMP TABLE IF NOT EXISTS touched(pid TEXT PRIMARY KEY)')
            wh.execute('DELETE FROM touched')
            wh.executemany('INSERT INTO touched VALUES (?)', ((p,) for p in touched))
            wh.execute('DELETE FROM records WHERE release = ? AND patient IN (SELECT pid FROM touched)', (release,))
            wh.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?,?,?,?)', (
                (release, e[0], e[1], e[2], e[5], e[6], 'matched', e[4], hashlib.sha256(new_payload[e[0]].encode()).hexdigest()[:16],
                 new_payload[e[0]]) for e in new_t))
            wh.execute('DELETE FROM patients WHERE release = ? AND id IN (SELECT pid FROM touched)', (release,))
            wh.executemany('INSERT OR REPLACE INTO patients VALUES (?,?,?)', ((release, p, new_persons[p]) for p in touched if p in new_persons))
            if entities is not None:
                cur = sorted(wh.execute('SELECT kind, id, payload FROM source_entities WHERE release = ?', (release,)))
                fresh = {}
                for k, i, p in entities:  # INSERT OR REPLACE semantics: the last row per (kind, id) wins
                    fresh[(k, i)] = p
                if cur != sorted((k, i, p) for (k, i), p in fresh.items()):
                    entities_changed = True
                    wh.execute('DELETE FROM source_entities WHERE release = ?', (release,))
                    wh.executemany('INSERT OR REPLACE INTO source_entities VALUES (?,?,?,?)', ((release, k, i, p) for k, i, p in entities))
        stack.close()
        wh.close()
        timings['apply'] = round(time.time() - t0, 1)
        # 5. record-level change for touched patients
        added, removed, changed = [], [], []
        new_visible = {e[0]: e for e in new_t if e[0] not in superseded}
        for rid, e in new_visible.items():
            o = old_records.get(rid)
            if o is None:
                added.append(rid)
            elif o[2] != e[3] or o[0] != e[1]:
                changed.append(rid)
        removed = [rid for rid in old_records if rid not in new_visible]
        new_versions = [e[0] for e in new_t if e[0] not in old_versions]
        # 6. run.json
        run_path = stack_dir / 'run.json'
        old_run = json.loads(run_path.read_text())
        as_of = canonical._max_date(stack_path)
        s2 = sqlite3.connect(f'file:{stack_path}?mode=ro', uri=True)
        try:
            total = s2.execute('SELECT count(*) FROM records').fetchone()[0]
            people = s2.execute('SELECT count(*) FROM patients').fetchone()[0]
        finally:
            s2.close()
        if not touched and as_of == (old_run.get('snapshot') or {}).get('as_of'):
            digest = old_run['source_digest']  # nothing changed: the release keeps its identity (no cache invalidation)
        else:
            digest = hashlib.sha256(f'{stack_path.stat().st_size}:{total}:{people}:{time.time()}'.encode()).hexdigest()
        run = {**old_run, 'source_digest': digest, 'patients': people, 'visible_records': total,
               'snapshot': {**old_run['snapshot'], 'as_of': as_of}}
        run_path.write_text(json.dumps(run, indent=1))
        # 7. state
        st.execute('DELETE FROM patient_digest')
        st.executemany('INSERT INTO patient_digest VALUES (?,?,?,?)', ((p, *d) for p, d in new_dig.items()))
        # landing files reflected in the store (crash recovery: a delta whose ingest landed but whose restage did not finish
        # is picked up by the next ingest_delta through pending_files)
        st.execute('DELETE FROM processed_files')
        st.executemany('INSERT INTO processed_files VALUES (?,?,?)', land.execute('SELECT relpath, bytes, mtime FROM ingest_files'))
        st.execute("INSERT OR REPLACE INTO meta VALUES ('bootstrapped', ?)", (time.strftime('%Y-%m-%dT%H:%M:%S'),))
        st.execute("INSERT OR REPLACE INTO meta VALUES ('source_digest', ?)", (digest,))
        st.commit()
        streams = Counter(e[2] for e in final)
        report = {'identity': ident_report, 'identity_diff': {k: (len(v) if k == 'keys_changed' else v) for k, v in idiff.items()},
                  'modules': ran, 'touched_patients': touched, 'records_added': added, 'records_removed': removed,
                  'records_changed': changed, 'versions_added': new_versions,
                  'old_records': {rid: (o[0], o[1], o[3]) for rid, o in old_records.items() if rid in set(removed) | set(changed)},
                  'old_versions': {rid: (o[0], o[1], o[3]) for rid, o in old_versions.items()
                                   if rid not in new_payload or _sha(new_payload[rid]) != o[2]},
                  'new_records': {rid: (new_visible[rid][1], new_visible[rid][2], new_payload[rid]) for rid in added + changed},
                  'new_versions': {e[0]: (e[1], e[2], new_payload[e[0]]) for e in new_t
                                   if e[0] not in old_versions or old_versions[e[0]][2] != e[3]},
                  'as_of_before': (old_run.get('snapshot') or {}).get('as_of'), 'as_of': as_of, 'source_digest': digest,
                  'records': total, 'people': people, 'superseded': len(superseded_rows), 'entities_changed': entities_changed,
                  'streams': dict(streams), 'unresolved': dict(Counter(u[0] for u in unresolved_all)),
                  'issues': len(unresolved_all), 'bootstrapped_now': not bootstrapped, 'timings': timings,
                  'seconds': round(time.time() - started, 1)}
        return report
    finally:
        st.close()
        land.close()


def pending_files(stack_dir, landing_path):
    """Landing files (relpath, parser, rows, tables) not yet reflected in the store: ingested by a delta that stopped
    before its restage finished. Empty for a workspace never restaged by streaming (bootstrap records them all)."""
    if not has_state(stack_dir):
        return []
    st = _state(stack_dir)
    try:
        if not st.execute('SELECT 1 FROM processed_files LIMIT 1').fetchone():
            return []
        done = {r[0]: (r[1], r[2]) for r in st.execute('SELECT relpath, bytes, mtime FROM processed_files')}
    finally:
        st.close()
    land = sqlite3.connect(f'file:{landing_path}?mode=ro', uri=True)
    try:
        out = []
        for rel, size, mtime, rows, tables, err in land.execute('SELECT relpath, bytes, mtime, rows, tables, error FROM ingest_files'):
            if done.get(rel) != (size, mtime):
                out.append({'relpath': rel, 'tables': json.loads(tables or '{}'), 'replaced': rel in done, 'replaced_tables': [],
                            'rows': rows, 'error': err})
        return out
    finally:
        land.close()
