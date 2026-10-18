"""Incremental semantic layer: rebuild only the touched patients and splice them into the existing build.

    report = update(stack_dir, change, targets=[<ws>/semantic.sqlite3])

The semantic layer is built by clinical/semantic_layer.build. Its cost at pilot scale (409 s measured) is: per-patient
emit loop ~40 s, SQL post-steps ~75 s, note_facts ~144 s, therapy_barriers ~139 s, record_store copy ~14 s. Every step
except a handful of global ones computes a patient's rows from that patient's records alone, so a tick runs the very
same build over a mini store holding only the touched patients and splices the result in:

Table classes (see TABLE_CLASSES / describe()):
- patient-partitioned (spliced: DELETE rows of touched patients, INSERT the mini build's rows): every table with a
  patient_id column except the ones below, e.g. record, condition, measurement, encounter, visit, stay, claim,
  payer_claim*, medication, prescription, note_fact, therapy_barrier, note_therapy_change, problem/medication lists.
- record-keyed (spliced by the touched patients' old and new record ids): link, prescription_fill.
- global, re-derived cheaply after the splice from the spliced tables (run in an in-memory database attached to the
  build, with the same functions the full build uses):
    organization, clinician      semantic_layer._entities + population_feeds._directory (POS mix across all claims)
    county_svi, patient_svi      population_feeds._sdoh (county SVI lookup across all vendor rows)
    fill                         fill_events.build on the whole build (fill_id is a dense global rank and
                                 days_of_data_before uses the global first fill date); 0.6 s at pilot scale
    drug_class_vocabulary        static vocabulary (unchanged by data)
    meta                         as_of, source_digest, standards and the recomputed summary parts patched in
- as_of-dependent (the processing watermark moves with almost every tick): member_month (months capped at as_of) and the
  list tables (problem_list_entry, medication_list_entry, allergy_entry, smoking_status: days-since, stale and
  filled-not-listed rows) are re-derived for every patient with population_feeds._enrollment and client_data.lists
  (their inputs are final when they run, so a re-run equals the in-build result). Patients whose as_of-sensitive values
  feed other patient tables are rebuilt in a second mini build: latest member month payer/LOB changed (patient row),
  an enrollment span starts or ends between the old and new as_of (coverage_status), or the list rows that
  note_therapy_change / therapy_barriers read changed.
- concept bindings come from the source profile (which label values a field carries). The profile is updated per
  touched stream with the same queries as source_profile.build; when a binding gains or loses a value, every patient
  holding a record with that value joins the touched set.

Exactness is checked by equivalence.compare (full rebuild of N files vs N-k files + a delta of k).
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import time

from ..clinical import concepts, source_profile

MINI_DIR = '.streaming-mini'
GLOBAL_TABLES = ('meta', 'organization', 'clinician', 'county_svi', 'patient_svi', 'drug_class_vocabulary', 'fill', 'record_correction',
                 'therapy_barrier', 'therapy_barrier_candidate')
RECORD_KEYED = {'link': 'record_id', 'prescription_fill': 'order_record_id'}
ASOF_TABLES = ('member_month', 'problem_list_entry', 'medication_list_entry', 'allergy_entry', 'smoking_status')
# columns of the as_of tables that later build steps read (client_data.therapy_changes, therapy_barriers): a change in these
# for a patient means the patient's note_therapy_change / therapy_barrier rows must be rebuilt
DOWNSTREAM_READ = {'medication_list_entry': ('patient_id', 'drug_key', 'name', 'list_status', 'last_listed', 'record_id'),
                   'allergy_entry': ('patient_id', 'substance', 'reaction', 'status', 'first_recorded', 'record_id', 'no_known_allergies')}
SKIP_PREFIXES = ('raw_', 'delivery_', 'sqlite_')


def describe():
    return {'patient_partitioned': 'every table with patient_id except the global / as_of lists below',
            'record_keyed': RECORD_KEYED, 'global': GLOBAL_TABLES, 'as_of_dependent': ASOF_TABLES,
            'downstream_read_columns': DOWNSTREAM_READ}


# ----------------------------------------------------------------------------------------------------------- profile
def _in(n):
    return '(' + ','.join('?' * n) + ')'


def profile_streams(db, streams):
    """source_profile.build restricted to the given streams (same queries, same shapes). -> (streams dict, measurements)."""
    streams = sorted(streams)
    out, meas = {}, {}
    if not streams:
        return out, meas
    flt = ' AND r.stream IN ' + _in(len(streams))
    out = {s: {'records': n, 'fields': {}} for s, n in
           db.execute('SELECT stream, count(*) FROM records r WHERE 1=1' + flt + ' GROUP BY stream', streams)}
    for stream, key, kind, n, distinct in db.execute(
            "SELECT r.stream, j.key, j.type, count(*), count(DISTINCT j.value) "
            "FROM records r, json_each(r.payload) j WHERE substr(j.key,1,1) <> '_'" + flt + " GROUP BY 1,2,3", streams):
        f = out[stream]['fields'].setdefault(key, {'populated': 0, 'types': {}, 'distinct': 0})
        f['types'][kind] = n
        if kind != 'null':
            f['populated'] += n
            f['distinct'] = max(f['distinct'], distinct)
    for stream, key, lo, hi in db.execute(
            "SELECT r.stream, j.key, min(j.value), max(j.value) FROM records r, json_each(r.payload) j "
            "WHERE j.type IN ('integer','real') AND substr(j.key,1,1) <> '_'" + flt + " GROUP BY 1,2", streams):
        out[stream]['fields'][key]['numeric_range'] = [lo, hi]
    wanted = {(s, k) for s, v in out.items() for k, f in v['fields'].items()
              if f['types'].get('text') and f['distinct'] <= source_profile.LOW_CARDINALITY
              and not k.endswith(source_profile.SKIP_VALUE_SUFFIXES)}
    for stream, key, value, n in db.execute(
            "SELECT r.stream, j.key, j.value, count(*) FROM records r, json_each(r.payload) j "
            "WHERE j.type='text' AND substr(j.key,1,1) <> '_'" + flt + " GROUP BY 1,2,3", streams):
        if (stream, key) in wanted:
            out[stream]['fields'][key].setdefault('values', {})[value] = n
    for stream, key, value, n in db.execute(
            "SELECT r.stream, j.key, e.value, count(*) FROM records r, json_each(r.payload) j, json_each(j.value) e "
            "WHERE j.type='array' AND e.type='text' AND substr(j.key,1,1) <> '_'" + flt + " GROUP BY 1,2,3", streams):
        f = out[stream]['fields'][key]
        elements = f.setdefault('element_values', {})
        if len(elements) < 5000: elements[value] = n
    for stream, (name_field, unit_field, value_field) in source_profile.MEASUREMENT_STREAMS.items():
        if stream not in out or name_field not in out[stream]['fields']: continue
        for name, unit, n, numeric, lo, hi in db.execute(
                f"SELECT json_extract(payload,'$.{name_field}'), json_extract(payload,'$.{unit_field}'), count(*), "
                f"sum(json_type(payload,'$.{value_field}') IN ('integer','real')), "
                f"min(json_extract(payload,'$.{value_field}')), max(json_extract(payload,'$.{value_field}')) "
                f"FROM records WHERE stream=? GROUP BY 1,2", (stream,)):
            meas.setdefault(stream, []).append({'name': name, 'unit': unit, 'records': n, 'numeric': numeric or 0, 'range': [lo, hi]})
    return out, meas


def _patients_profile(db):
    patients = {'records': db.execute('SELECT count(*) FROM patients').fetchone()[0], 'fields': {}}
    for key, kind, n in db.execute("SELECT j.key, j.type, count(*) FROM patients p, json_each(p.payload) j GROUP BY 1,2"):
        f = patients['fields'].setdefault(key, {'populated': 0, 'types': {}})
        f['types'][kind] = n
        if kind != 'null': f['populated'] += n
    return patients


def update_profile(stack_dir, old_profile, touched_streams, digest, change=None):
    """New source profile = old one with the touched streams (and the patients section) recomputed: from the value counts
    in profile_state when they exist (updated here with the tick's removed / added records), else with json_each."""
    from . import profile_state, stage
    db = sqlite3.connect(f'file:{Path(stack_dir) / "stack.sqlite3"}?mode=ro', uri=True)
    state = stage.state_path(stack_dir)
    try:
        present = {r[0] for r in db.execute('SELECT DISTINCT stream FROM records')} if touched_streams else set()
        wanted = set(touched_streams) & present
        if change is not None and profile_state.ready(state):
            old, new = change.get('old_records') or {}, change.get('new_records') or {}
            removed = [(v[1], v[2]) for rid, v in old.items()]
            added = [(v[1], v[2]) for rid, v in new.items()]
            profile_state.apply(state, removed, added)
            fresh, meas, recount = profile_state.profile_streams(state, Path(stack_dir) / 'stack.sqlite3', wanted)
            if recount:
                f2, m2 = profile_streams(db, recount)
                fresh.update(f2)
                for s_ in recount:
                    meas.pop(s_, None)
                meas.update(m2)
        else:
            fresh, meas = profile_streams(db, wanted)
        streams = {s: v for s, v in old_profile['streams'].items() if s not in touched_streams}
        streams.update(fresh)
        measurements = {s: v for s, v in (old_profile.get('measurements') or {}).items() if s not in touched_streams}
        measurements.update(meas)
        profile = {'schema': source_profile.VERSION, 'streams': dict(sorted(streams.items())), 'patients': _patients_profile(db),
                   'measurements': measurements, 'elapsed_seconds': 0.0, 'source_digest': digest}
    finally:
        db.close()
    return profile


def _resolve_all(profile):
    return {c: concepts.resolve(c, profile)['bindings']
            for c in (*concepts.CONDITIONS, *concepts.MEASUREMENTS, *concepts.ENCOUNTERS)}


def _atoms(bindings):
    """Binding -> atoms (stream, field(s), value-or-None) whose presence on a record changes its concept rows."""
    out = set()
    for b in bindings:
        st = b['stream']
        if 'values' in b:
            out |= {(st, (b['field'],), v) for v in b['values']}
        elif 'names' in b:
            out |= {(st, tuple(b['name_fields']), v) for v in b['names']}
            for f, codes in (b.get('codes') or {}).items():
                out |= {(st, (f,), c) for c in codes}
        else:
            out.add((st, (b['value_field'],), None))
    return out


def binding_patients(stack_dir, old_profile, new_profile):
    """Patients holding a record whose concept binding changed between the two profiles. -> (patients, changed atoms)."""
    old, new = _resolve_all(old_profile), _resolve_all(new_profile)
    diff = set()
    for c in set(old) | set(new):
        a, b = _atoms(old.get(c, [])), _atoms(new.get(c, []))
        diff |= a ^ b
    if not diff:
        return set(), []
    pts = set()
    db = sqlite3.connect(f'file:{Path(stack_dir) / "stack.sqlite3"}?mode=ro', uri=True)
    try:
        for st, fields, value in diff:
            cond = ' OR '.join(f"json_extract(payload, '$.\"{f}\"') {'IS NOT NULL' if value is None else '= ?'}" for f in fields)
            args = [st] + ([] if value is None else [value] * len(fields))
            pts |= {r[0] for r in db.execute(f'SELECT DISTINCT patient FROM records WHERE stream = ? AND ({cond})', args)}
    finally:
        db.close()
    return pts, sorted(diff, key=str)


# ----------------------------------------------------------------------------------------------------------- mini build
def mini_build(stack_dir, patients, profile, *, label='mini'):
    """The full semantic build over a store holding only `patients` (same run.json, profile, release, entities)."""
    from ..clinical import semantic_layer
    stack_dir = Path(stack_dir)
    root = stack_dir / MINI_DIR / label
    shutil.rmtree(root, ignore_errors=True)
    mini = root / 'stack'
    mini.mkdir(parents=True)
    run = json.loads((stack_dir / 'run.json').read_text())
    (mini / 'run.json').write_text(json.dumps(run, indent=1))
    (mini / 'source-profile.json').write_text(json.dumps({**profile, 'source_digest': run['source_digest']}))
    pts = sorted(patients)
    m = sqlite3.connect(mini / 'stack.sqlite3')
    try:
        m.executescript('''CREATE TABLE patients(id TEXT PRIMARY KEY, payload TEXT);
            CREATE TABLE records(id TEXT PRIMARY KEY, patient TEXT, stream TEXT, payload TEXT);
            CREATE TABLE stg_unresolved(stream TEXT, source_patient_id TEXT, lineage TEXT);
            CREATE TABLE stg_superseded(record_id TEXT, superseded_by TEXT, stream TEXT);
            CREATE TEMP TABLE d(pid TEXT PRIMARY KEY);''')
        m.executemany('INSERT INTO d VALUES (?)', ((p,) for p in pts))
        m.execute('ATTACH ? AS src', (str(stack_dir / 'stack.sqlite3'),))
        m.execute('INSERT INTO patients SELECT id, payload FROM src.patients WHERE id IN (SELECT pid FROM d)')
        m.execute('INSERT INTO records SELECT id, patient, stream, payload FROM src.records WHERE patient IN (SELECT pid FROM d) ORDER BY rowid')
        m.commit()
        m.execute('DETACH src')
        m.execute('CREATE INDEX records_patient ON records(patient)')
        m.execute('CREATE INDEX records_stream ON records(stream)')
        m.commit()
    finally:
        m.close()
    w = sqlite3.connect(root / 'warehouse.sqlite3')
    try:
        w.executescript('''CREATE TABLE releases (id TEXT PRIMARY KEY, manifest TEXT);
            CREATE TABLE patients (release TEXT, id TEXT, payload TEXT, PRIMARY KEY(release,id));
            CREATE TABLE records (release TEXT, id TEXT, patient TEXT, stream TEXT, event TEXT, arrival TEXT, identity TEXT,
                                  supersedes TEXT, hash TEXT, payload TEXT, PRIMARY KEY(release,id));
            CREATE TABLE source_entities(release TEXT, kind TEXT, id TEXT, payload TEXT, PRIMARY KEY(release,kind,id));
            CREATE TEMP TABLE d(pid TEXT PRIMARY KEY);''')
        w.executemany('INSERT INTO d VALUES (?)', ((p,) for p in pts))
        w.execute('ATTACH ? AS src', (str(stack_dir.parent / 'warehouse.sqlite3'),))
        w.execute('INSERT INTO releases SELECT * FROM src.releases')
        w.execute('INSERT INTO patients SELECT * FROM src.patients WHERE id IN (SELECT pid FROM d)')
        w.execute('INSERT INTO records SELECT * FROM src.records WHERE patient IN (SELECT pid FROM d) ORDER BY rowid')
        w.execute('INSERT INTO source_entities SELECT * FROM src.source_entities')
        w.commit()
        w.execute('DETACH src')
        w.execute('CREATE INDEX records_stream ON records(stream)')
        w.commit()
    finally:
        w.close()
    t0 = time.time()
    with capture_directory_inputs(stack_dir, patients):
        semantic_layer.build(mini)
    return mini / 'semantic.sqlite3', round(time.time() - t0, 1)


POS_DDL = """CREATE TABLE IF NOT EXISTS pos_input(patient_id TEXT, src TEXT, seq INTEGER, claim_id TEXT, billing_npi TEXT,
  place_of_service TEXT);
CREATE INDEX IF NOT EXISTS pos_input_patient ON pos_input(patient_id);"""


@contextmanager
def capture_directory_inputs(stack_dir, patients):
    """organization.place_of_service is the most common POS over all claims billed under the org NPI, counted by
    population_feeds._directory from payer_claim and claim as they stand at that point of the build (later steps
    update both). A mini build records those (billing_npi, place_of_service) rows for its patients into the streaming
    state, in table order, so the global re-derivation reads exactly what a full build's _directory reads."""
    from ..clinical import population_feeds
    from . import stage
    real = population_feeds._directory
    pts = sorted(patients)

    def wrapped(out):
        rows = []
        for i, r in enumerate(out.execute('SELECT patient_id, payer_claim_id, billing_npi, place_of_service FROM payer_claim ORDER BY rowid')):
            rows.append((r[0], 'payer_claim', i, r[1], r[2], r[3]))
        for i, r in enumerate(out.execute('SELECT patient_id, claim_id, billing_npi, place_of_service FROM claim ORDER BY rowid')):
            rows.append((r[0], 'claim', i, r[1], r[2], r[3]))
        st = sqlite3.connect(stage.state_path(stack_dir))
        try:
            with st:
                st.executescript(POS_DDL)
                st.execute('CREATE TEMP TABLE d(pid TEXT PRIMARY KEY)')
                st.executemany('INSERT INTO d VALUES (?)', ((x,) for x in pts))
                st.execute('DELETE FROM pos_input WHERE patient_id IN (SELECT pid FROM d)')
                st.executemany('INSERT INTO pos_input VALUES (?,?,?,?,?,?)', rows)
        finally:
            st.close()
        return real(out)
    population_feeds._directory = wrapped
    try:
        yield
    finally:
        population_feeds._directory = real


def bootstrap_pos_inputs(stack_dir):
    """Record _directory's inputs for every patient: one mini build over the whole store (free-text readers served from
    the memo cache), then mark the state complete. -> seconds."""
    from . import memo, stage
    t0 = time.time()
    stack_dir = Path(stack_dir)
    profile = json.loads((stack_dir / 'source-profile.json').read_text())
    with memo.text_readers(stack_dir):
        mini_build(stack_dir, _stack_patients(stack_dir) | _patients(stack_dir / 'semantic.sqlite3'), profile, label='pos-bootstrap')
    shutil.rmtree(stack_dir / MINI_DIR, ignore_errors=True)
    st = sqlite3.connect(stage.state_path(stack_dir))
    try:
        with st:
            st.execute("INSERT OR REPLACE INTO meta VALUES ('pos_input_complete', datetime('now'))")
    finally:
        st.close()
    return round(time.time() - t0, 1)


def pos_inputs_ready(stack_dir):
    from . import stage
    st = sqlite3.connect(stage.state_path(stack_dir))
    try:
        return st.execute("SELECT 1 FROM sqlite_master WHERE name = 'pos_input'").fetchone() is not None and \
            st.execute("SELECT 1 FROM meta WHERE key = 'pos_input_complete'").fetchone() is not None
    finally:
        st.close()


def _tables(db, schema='main'):
    return [r[0] for r in db.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' ORDER BY name")]


def _cols(db, table, schema='main'):
    return [r[1] for r in db.execute(f'PRAGMA {schema}.table_info("{table}")')]


def splice(target, mini_semantic, patients, *, measure=False):
    """Replace the touched patients' rows in every patient-partitioned / record-keyed table of target with the mini
    build's rows. -> {table: [deleted, inserted]}."""
    db = sqlite3.connect(target, timeout=120)
    stats = {}
    try:
        db.execute('ATTACH ? AS mini', (str(mini_semantic),))
        db.execute('CREATE TEMP TABLE d(pid TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO d VALUES (?)', ((p,) for p in sorted(patients)))
        db.execute('CREATE TEMP TABLE old_rids(rid TEXT PRIMARY KEY)')
        db.execute('INSERT OR IGNORE INTO old_rids SELECT record_id FROM main.record WHERE patient_id IN (SELECT pid FROM d)')
        db.execute('INSERT OR IGNORE INTO old_rids SELECT record_id FROM mini.record WHERE patient_id IN (SELECT pid FROM d)')
        db.execute('CREATE TEMP TABLE new_rids(rid TEXT PRIMARY KEY)')
        db.execute('INSERT OR IGNORE INTO new_rids SELECT record_id FROM mini.record WHERE patient_id IN (SELECT pid FROM d)')
        mini_tables = set(_tables(db, 'mini'))
        with db:
            for t in _tables(db):
                if t.startswith(SKIP_PREFIXES) or t in GLOBAL_TABLES:
                    continue
                if t not in mini_tables:
                    stats[t] = 'missing in mini build'
                    continue
                cols, mcols = _cols(db, t), _cols(db, t, 'mini')
                if cols != mcols:
                    raise RuntimeError(f'splice: column mismatch in {t}: {cols} vs {mcols}')
                clist = ', '.join(f'"{c}"' for c in cols)
                if 'patient_id' in cols:
                    where = 'patient_id IN (SELECT pid FROM d)'
                    src_where = where
                elif t in RECORD_KEYED:
                    where = f'"{RECORD_KEYED[t]}" IN (SELECT rid FROM old_rids)'
                    src_where = f'"{RECORD_KEYED[t]}" IN (SELECT rid FROM new_rids)'
                else:
                    stats[t] = {'global': True}
                    continue
                info = {}
                if measure:
                    diff = (f'SELECT * FROM (SELECT {clist} FROM main."{t}" WHERE {where} EXCEPT SELECT {clist} FROM mini."{t}" WHERE {src_where}) '
                            f'UNION ALL SELECT * FROM (SELECT {clist} FROM mini."{t}" WHERE {src_where} EXCEPT SELECT {clist} FROM main."{t}" WHERE {where})')
                    rows = db.execute(diff).fetchall()
                    pi = cols.index('patient_id') if 'patient_id' in cols else None
                    pats = set()
                    if pi is not None:
                        pats = {r[pi] for r in rows}
                    elif rows:
                        key = cols.index(RECORD_KEYED[t])
                        rids = list({r[key] for r in rows})
                        for i in range(0, len(rids), 500):
                            ch = rids[i:i + 500]
                            pats |= {r[0] for r in db.execute('SELECT patient_id FROM mini.record WHERE record_id IN (' + ','.join('?' * len(ch)) + ') '
                                                              'UNION SELECT patient_id FROM main.record WHERE record_id IN (' + ','.join('?' * len(ch)) + ')', ch + ch)}
                    info = {'rows_changed': len(rows), 'patients': sorted(p for p in pats if p)}
                info['deleted'] = db.execute(f'DELETE FROM main."{t}" WHERE {where}').rowcount
                info['inserted'] = db.execute(f'INSERT INTO main."{t}" ({clist}) SELECT {clist} FROM mini."{t}" WHERE {src_where}').rowcount
                stats[t] = info
    finally:
        db.close()
    return stats


# ----------------------------------------------------------------------------------------------------------- globals
def _ddl(module_ddl, table):
    for stmt in module_ddl.split(';'):
        s = stmt.strip()
        if s.upper().startswith('CREATE TABLE') and s.split('(')[0].split()[-1] == table:
            return s
    raise KeyError(table)


class _NoScript:
    """Connection proxy for sandboxed build functions: executescript would COMMIT the savepoint, so its statements run one
    by one through execute instead."""
    def __init__(self, db):
        self._db = db

    def executescript(self, sql):
        for stmt in sql.split(';'):
            if stmt.strip():
                self._db.execute(stmt)

    def __getattr__(self, name):
        return getattr(self._db, name)


def sandbox(db, owned, fn, collect):
    """Run fn(db) on the real build inside a savepoint with the tables it creates (owned: {table: DDL or None}) renamed
    aside, read the tables in collect, and roll everything back. The exact build function runs on the exact inputs
    (no copy, no schema tricks). -> (fn result, {table: (columns, rows)})."""
    have = set(_tables(db))
    db.execute('SAVEPOINT agere_streaming_sandbox')
    try:
        for t, ddl in owned.items():
            if t in have:
                db.execute(f'ALTER TABLE "{t}" RENAME TO "_sandbox_{t}"')
                for (ix,) in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name = ? AND sql IS NOT NULL",
                                        (f'_sandbox_{t}',)).fetchall():
                    db.execute(f'DROP INDEX "{ix}"')  # its name would clash with the function's own index (rolled back)
            if ddl:
                db.execute(ddl)
        result = fn(_NoScript(db))
        out = {}
        for t in collect:
            cols = _cols(db, t)
            out[t] = (cols, db.execute(f'SELECT * FROM "{t}"').fetchall())
        return result, out
    finally:
        db.execute('ROLLBACK TO agere_streaming_sandbox')
        db.execute('RELEASE agere_streaming_sandbox')
        left = [t for t in _tables(db) if t.startswith('_sandbox_')]
        if left or set(owned) - set(_tables(db)) & have:
            raise RuntimeError(f'streaming sandbox did not roll back cleanly: {left}')


def _connect(target):
    db = sqlite3.connect(target, timeout=120, isolation_level=None)
    return db


def _project(cols, rows, keep):
    idx = [cols.index(c) for c in keep]
    return {tuple(r[i] for i in idx) for r in rows}


def asof_refresh(stack_dir, primary, as_of_before, as_of):
    """Re-derive member_month and the list tables for every patient (population_feeds._enrollment, client_data.lists in a
    sandbox on the spliced build). -> (new tables {table: (cols, rows)}, patients whose as_of-sensitive values feed other
    tables, stats)."""
    from ..clinical import client_data, population_feeds
    db = _connect(primary)
    stats, dirty, tables = {}, set(), {}
    try:
        res, t1 = sandbox(db, {t: _ddl(population_feeds.DDL, t) for t in ('enrollment_span', 'member_month')},
                          lambda c: population_feeds._enrollment(c, as_of), ['member_month'])
        stats['enrollment'] = res
        tables.update(t1)
        lists = ('problem_list_entry', 'medication_list_entry', 'allergy_entry', 'smoking_status')
        res, t2 = sandbox(db, {t: _ddl(client_data.DDL, t) for t in lists}, lambda c: client_data.lists(c, as_of), list(lists))
        stats['lists'] = res
        tables.update(t2)
        # latest member month payer / line of business (patient.line_of_business, payer_id)
        def latest(cols, rows):
            i_p, i_m, i_l, i_y = (cols.index(c) for c in ('patient_id', 'month', 'line_of_business', 'payer_id'))
            best = {}
            for r in rows:
                if r[i_p] not in best or r[i_m] > best[r[i_p]][0]:
                    best[r[i_p]] = (r[i_m], r[i_l], r[i_y])
            return {p: v[1:] for p, v in best.items()}
        old_mm = (_cols(db, 'member_month'), db.execute('SELECT * FROM member_month').fetchall())
        o, n = latest(*old_mm), latest(*tables['member_month'])
        lob = {p for p in set(o) | set(n) if o.get(p) != n.get(p)}
        dirty |= lob
        # coverage_status: an enrollment span starting or ending between the two as_of dates
        cov = set()
        if as_of_before and as_of_before != as_of:
            a, b = sorted([as_of_before, as_of])
            cov = {r[0] for r in db.execute(
                'SELECT DISTINCT patient_id FROM enrollment_span WHERE (coverage_start > ? AND coverage_start <= ?) '
                'OR (coverage_end >= ? AND coverage_end < ?)', (a, b, a, b))}
        dirty |= cov
        lst = set()
        for t, keep in DOWNSTREAM_READ.items():
            old = _project(_cols(db, t), db.execute(f'SELECT * FROM "{t}"').fetchall(), keep)
            new = _project(*tables[t], keep)
            lst |= {r[0] for r in old ^ new}
        dirty |= lst
        changed = {}
        for t in ASOF_TABLES:
            cols = _cols(db, t)
            old = set(db.execute(f'SELECT * FROM "{t}"').fetchall())
            new = set(tables[t][1])
            pi = cols.index('patient_id')
            changed[t] = len({r[pi] for r in old ^ new})
        stats.update(dirty_latest_member_month=len(lob), dirty_coverage_boundary=len(cov), dirty_list_rows=len(lst),
                     patients_with_changed_rows=changed)
    finally:
        db.close()
    return tables, dirty, stats


def global_tables(stack_dir, primary):
    """organization / clinician (_entities + _directory) and county_svi / patient_svi (_sdoh), re-derived in a sandbox
    on the spliced build. -> ({table: (cols, rows)}, stats)."""
    from ..clinical import population_feeds, semantic_layer
    db = _connect(primary)
    try:
        def ents(c):
            n = semantic_layer._entities(c, stack_dir)
            population_feeds._directory(c)
            return n
        # _directory runs before population_events folds payer claims into `claim` (claim_source = 'payer_medical_claims'),
        # so it sees `claim` without those rows
        # Row order matters for ties in the most common place of service (Counter.most_common keeps first-seen order):
        # in a full build payer_claim rows are in patient order (written from a stream scan of `record`) and claim rows in
        # claim_id order (written by a window query partitioned by claim_id); spliced rows are appended, so the sandbox
        # hands _directory both tables in their full-build order.
        claim_then = ('CREATE TEMP TABLE claim AS SELECT * FROM "_sandbox_claim" '
                      "WHERE coalesce(claim_source, '') <> 'payer_medical_claims' ORDER BY claim_id, rowid")
        payer_then = 'CREATE TEMP TABLE payer_claim AS SELECT * FROM "_sandbox_payer_claim" ORDER BY patient_id, rowid'
        if pos_inputs_ready(stack_dir):  # exact: the rows _directory read in each patient's (mini) build
            from . import stage
            db.execute('ATTACH ? AS st', (str(stage.state_path(stack_dir)),))
            payer_then = ("CREATE TEMP TABLE payer_claim AS SELECT billing_npi, place_of_service FROM st.pos_input "
                          "WHERE src = 'payer_claim' AND patient_id IN (SELECT patient_id FROM main.patient) ORDER BY patient_id, seq")
            claim_then = ("CREATE TEMP TABLE claim AS SELECT billing_npi, place_of_service FROM st.pos_input "
                          "WHERE src = 'claim' AND patient_id IN (SELECT patient_id FROM main.patient) ORDER BY claim_id, seq")
        _, t1 = sandbox(db, {'organization': None, 'clinician': None, 'claim': claim_then, 'payer_claim': payer_then}, ents,
                        ['organization', 'clinician'])
        res, t2 = sandbox(db, {t: _ddl(population_feeds.DDL, t) for t in ('sdoh_vendor', 'county_svi', 'patient_svi')},
                          population_feeds._sdoh, ['county_svi', 'patient_svi'])
        # record_correction pairs a superseded version with its replacement, which can belong to another patient (an
        # identity change between versions): re-derived from the whole received store
        from ..clinical import adjudication_history
        res_rc, t3 = sandbox(db, {'record_correction': None}, lambda c: adjudication_history.build_corrections(c, stack_dir.parent / 'warehouse.sqlite3'),
                             ['record_correction'])
        res['record_correction'] = res_rc
    finally:
        db.close()
    rc = res.pop('record_correction')
    return {**t1, **t2, **t3}, {'sdoh': res, 'record_correction': rc}


def write_tables(targets, tables):
    """Replace whole tables in every target with the given rows (by column name). -> changes measured on the first target
    {table: {'rows_changed', 'patients'}}."""
    changes = {}
    for ti, target in enumerate(targets):
        db = sqlite3.connect(target, timeout=120)
        try:
            with db:
                for t, (cols, rows) in tables.items():
                    tcols = _cols(db, t)
                    if sorted(tcols) != sorted(cols):
                        raise RuntimeError(f'write_tables: column mismatch in {t}')
                    if ti == 0:
                        old = set(db.execute(f'SELECT ' + ', '.join(f'"{c}"' for c in cols) + f' FROM "{t}"').fetchall())
                        diff = old ^ set(rows)
                        pi = cols.index('patient_id') if 'patient_id' in cols else None
                        changes[t] = {'rows_changed': len(diff), 'patients': sorted({r[pi] for r in diff if r[pi]}) if pi is not None else [],
                                      'deleted': len(old), 'inserted': len(rows)}
                    db.execute(f'DELETE FROM "{t}"')
                    db.executemany(f'INSERT INTO "{t}" (' + ', '.join(f'"{c}"' for c in cols) + ') VALUES (' + ','.join('?' * len(cols)) + ')', rows)
        finally:
            db.close()
    return changes


def rerun_fill(target):
    from ..clinical import fill_events, record_store
    db = sqlite3.connect(target, timeout=120)
    try:
        with db:
            summary = fill_events.build(db)
    finally:
        db.close()
    record_store.index_semantic(Path(target).parent) if Path(target).name == 'semantic.sqlite3' else None
    return summary


def rerun_therapy(target):
    """therapy_barriers over the whole build (its tier-2 classifier is fitted on every patient's tier-1 labels and its
    case ids number candidates across the population). Run with memo.text_readers active, the per-note candidate scan is
    served from the cache. Same failure handling as semantic_layer.build: recorded in meta, never raised."""
    from ..clinical import therapy_barriers
    if not therapy_barriers.enabled():
        return None
    try:
        return therapy_barriers.build(target, llm=therapy_barriers.llm_enabled())
    except Exception as e:  # noqa: BLE001
        db = sqlite3.connect(target)
        try:
            with db:
                db.execute("DELETE FROM meta WHERE key = 'therapy_barrier_error'")
                db.execute('INSERT INTO meta VALUES (?,?)', ('therapy_barrier_error', f'{type(e).__name__}: {str(e)[:500]}'))
        finally:
            db.close()
        return {'error': f'{type(e).__name__}: {e}'}


def patch_meta(target, stack_dir, as_of, digest, patches):
    from ..clinical import semantic_layer
    db = sqlite3.connect(target, timeout=120)
    try:
        with db:
            meta = dict(db.execute('SELECT key, value FROM meta'))
            feeds = json.loads(meta.get('population_feeds') or '{}')
            for path, value in patches.items():
                cur = feeds
                for k in path[:-1]:
                    cur = cur.setdefault(k, {})
                cur[path[-1]] = value
            rows = {'source_digest': digest, 'as_of': as_of, 'standards': json.dumps(semantic_layer._standards_summary(db)),
                    'population_feeds': json.dumps(feeds),
                    'streaming': json.dumps({'maintained_by': 'agere-streaming/1', 'note': 'claim_settlement, note_fact_version, '
                                             'therapy_barrier_summary and the population_feeds parts not re-derived here '
                                             'are the counts of the last full build'})}
            for k, v in rows.items():
                db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (k, v))
    finally:
        db.close()


# ----------------------------------------------------------------------------------------------------------- update
SWAP_SHARE = 0.6  # rebuilding more than this share of patients: splice the untouched ones into the mini build instead


def update(stack_dir, change, *, targets=(), keep_mini=False, profile_done=False, on_profile=None):
    """Bring stack/semantic.sqlite3 (and each extra target) up to date with a staged change; see _update.
    profile_done: the profile for this change was already written (a resumed, interrupted tick); on_profile() is called
    once it is, so a journal can record it (the value-count update is not idempotent)."""
    from . import memo
    with memo.text_readers(stack_dir) as m:
        report = _update(stack_dir, change, targets=targets, keep_mini=keep_mini, profile_done=profile_done, on_profile=on_profile)
        report['text_reader_cache'] = dict(m.stats)
    return report


def _update(stack_dir, change, *, targets=(), keep_mini=False, profile_done=False, on_profile=None):
    """Bring stack/semantic.sqlite3 (and each extra target, e.g. the workspace semantic.sqlite3 that adds raw_ and
    delivery tables) up to date with a staged change (stage.restage). -> report."""
    started = time.time()
    stack_dir = Path(stack_dir)
    primary = stack_dir / 'semantic.sqlite3'
    all_targets = [primary, *[Path(t) for t in targets if Path(t).resolve() != primary.resolve()]]
    run = json.loads((stack_dir / 'run.json').read_text())
    report = {'timings': {}}
    t0 = time.time()
    old_profile = json.loads((stack_dir / 'source-profile.json').read_text())
    touched = set(change['touched_patients'])
    streams = set()
    for d in ('new_records', 'old_records', 'new_versions', 'old_versions'):
        streams |= {v[1] for v in (change.get(d) or {}).values()}
    if profile_done:
        profile = {**old_profile, 'source_digest': run['source_digest']}
        old_profile = json.loads((stack_dir / 'source-profile.prev.json').read_text()) if (stack_dir / 'source-profile.prev.json').is_file() else old_profile
    else:
        profile = update_profile(stack_dir, old_profile, streams, run['source_digest'], change)
        (stack_dir / 'source-profile.prev.json').write_text(json.dumps(old_profile))
    (stack_dir / 'source-profile.json').write_text(json.dumps(profile))
    if on_profile:
        on_profile()
    source_profile._CACHE.pop((str(stack_dir), run['source_digest']), None)
    bpts, atoms = binding_patients(stack_dir, old_profile, profile)
    report['binding_changes'] = [list(map(str, a)) for a in atoms[:50]]
    report['binding_patients'] = len(bpts - touched)
    d0 = touched | bpts
    report['timings']['profile'] = round(time.time() - t0, 1)
    splices, table_changes = {}, {}
    # global tables before this tick (measured before a swap can replace the build file)
    before_fill = _table_rows(primary, 'fill')
    before_tb = _table_rows(primary, 'therapy_barrier') if _has_table(primary, 'therapy_barrier') else set()
    before_tbc = _table_rows(primary, 'therapy_barrier_candidate') if _has_table(primary, 'therapy_barrier_candidate') else set()
    if d0:
        mini, secs = mini_build(stack_dir, d0, profile, label='touched')
        report['timings']['mini_build'] = secs
        t0 = time.time()
        everyone = _patients(primary) | _stack_patients(stack_dir)
        if len(d0) > SWAP_SHARE * max(len(everyone), 1):
            # most patients rebuilt: copy the few untouched patients' rows into the mini build and make it the build
            rest = everyone - d0
            splice(mini, primary, rest)
            for extra in ('-wal', '-shm'):
                Path(str(primary) + extra).unlink(missing_ok=True)
            Path(mini).replace(primary)
            splices[str(primary)] = {'swap': True, 'untouched_copied': len(rest)}
            db = sqlite3.connect(f'file:{primary}?mode=ro', uri=True)
            try:
                for t in _tables(db):
                    if not t.startswith(SKIP_PREFIXES) and t not in GLOBAL_TABLES and ('patient_id' in _cols(db, t) or t in RECORD_KEYED):
                        table_changes[t] = {'rows_changed': 1, 'patients': sorted(d0), 'conservative': True}
            finally:
                db.close()
            for t in all_targets[1:]:
                splices[str(t)] = splice(t, primary, d0)
            report['swap'] = True
        else:
            for i, t in enumerate(all_targets):
                splices[str(t)] = splice(t, mini, d0, measure=(i == 0))
            _merge_changes(table_changes, splices[str(primary)])
        report['timings']['splice'] = round(time.time() - t0, 1)
    # as_of-dependent tables
    t0 = time.time()
    atables, dasof, astats = asof_refresh(stack_dir, primary, change.get('as_of_before'), change['as_of'])
    _merge_changes(table_changes, write_tables(all_targets, atables), replace=True)
    report['as_of'] = {'before': change.get('as_of_before'), 'after': change['as_of'], **astats}
    extra = dasof - d0
    report['timings']['as_of_refresh'] = round(time.time() - t0, 1)
    if extra:
        mini2, secs = mini_build(stack_dir, extra, profile, label='as_of')
        report['timings']['mini_build_as_of'] = secs
        t0 = time.time()
        for i, t in enumerate(all_targets):
            sp = splice(t, mini2, extra, measure=(i == 0))
            if i == 0:
                _merge_changes(table_changes, sp)
        report['timings']['splice_as_of'] = round(time.time() - t0, 1)
    # global tables
    t0 = time.time()
    gtables, gstats = global_tables(stack_dir, primary)
    _merge_changes(table_changes, write_tables(all_targets, gtables), replace=True)
    fill = None
    for t in all_targets:
        fill = rerun_fill(t)
    after_fill = _table_rows(primary, 'fill')
    diff = {r[1:] for r in before_fill} ^ {r[1:] for r in after_fill}  # fill_id is a dense rank: renumbering is not a change
    table_changes['fill'] = {'rows_changed': len(diff), 'patients': sorted({r[0] for r in diff if r[0]})}
    t1 = time.time()
    # therapy_barriers runs on the canonical build only (the workspace copy carries raw_ tables, which its text-unit scan
    # would also read); its tables, note_therapy_change reasons and summary are then copied to the other targets
    tb_summary = rerun_therapy(primary)
    if len(all_targets) > 1 and _has_table(primary, 'therapy_barrier'):
        db = _connect(primary)
        try:
            copy = {t: (_cols(db, t), db.execute(f'SELECT * FROM "{t}"').fetchall())
                    for t in ('therapy_barrier', 'therapy_barrier_candidate', 'note_therapy_change')}
            tb_meta = db.execute("SELECT value FROM meta WHERE key = 'therapy_barrier_summary'").fetchone()
        finally:
            db.close()
        for t in all_targets[1:]:
            tdb = sqlite3.connect(t, timeout=120)
            try:
                with tdb:
                    for name, (cols, _) in copy.items():
                        have = _cols(tdb, name)
                        for c in cols:
                            if c not in have:
                                tdb.execute(f'ALTER TABLE "{name}" ADD COLUMN "{c}"')
                    if tb_meta:
                        tdb.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('therapy_barrier_summary', tb_meta[0]))
            finally:
                tdb.close()
        write_tables(all_targets[1:], copy)
    report['timings']['therapy_barriers'] = round(time.time() - t1, 1)
    if _has_table(primary, 'therapy_barrier'):
        diff = before_tb ^ _table_rows(primary, 'therapy_barrier')
        table_changes['therapy_barrier'] = {'rows_changed': len(diff), 'patients': sorted({r[0] for r in diff if r[0]})}
        diff = {r[1:] for r in before_tbc} ^ {r[1:] for r in _table_rows(primary, 'therapy_barrier_candidate')}  # case_id is a rank
        table_changes['therapy_barrier_candidate'] = {'rows_changed': len(diff), 'patients': sorted({r[0] for r in diff if r[0]})}
    for t in all_targets:
        patch_meta(t, stack_dir, change['as_of'], run['source_digest'],
                   {('enrollment',): astats['enrollment'], ('sdoh',): gstats['sdoh'], ('fill_events',): fill,
                    ('record_correction',): gstats['record_correction'],
                    ('client_data', 'lists'): astats['lists']})
    report['timings']['globals'] = round(time.time() - t0, 1)
    if not keep_mini:
        shutil.rmtree(stack_dir / MINI_DIR, ignore_errors=True)
    report['table_changes'] = table_changes
    report.update(patients_rebuilt=len(d0 | extra), touched=len(touched), as_of_rebuilt=len(extra),
                  splice=splices.get(str(primary)), seconds=round(time.time() - started, 1))
    return report


def _patients(target):
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        return {r[0] for r in db.execute('SELECT patient_id FROM patient')}
    finally:
        db.close()


def _stack_patients(stack_dir):
    db = sqlite3.connect(f'file:{Path(stack_dir) / "stack.sqlite3"}?mode=ro', uri=True)
    try:
        return {r[0] for r in db.execute('SELECT id FROM patients')} | {r[0] for r in db.execute('SELECT DISTINCT patient FROM records')}
    finally:
        db.close()


def _has_table(target, table):
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone() is not None
    finally:
        db.close()


def _table_rows(target, table):
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        return set(db.execute(f'SELECT * FROM "{table}"').fetchall())
    finally:
        db.close()


def _merge_changes(acc, new, replace=False):
    for t, info in (new or {}).items():
        if not isinstance(info, dict) or info.get('global') or 'rows_changed' not in info:
            continue
        if replace or t not in acc:
            acc[t] = {'rows_changed': info['rows_changed'], 'patients': list(info.get('patients') or []),
                      'deleted': info.get('deleted'), 'inserted': info.get('inserted')}
        else:
            a = acc[t]
            a['rows_changed'] += info['rows_changed']
            a['patients'] = sorted(set(a['patients']) | set(info.get('patients') or []))
            a['deleted'] = (a.get('deleted') or 0) + (info.get('deleted') or 0)
            a['inserted'] = (a.get('inserted') or 0) + (info.get('inserted') or 0)


def record_store_refresh(stack_dir):
    from ..clinical import record_store
    t0 = time.time()
    record_store.build(stack_dir)
    return round(time.time() - t0, 1)
