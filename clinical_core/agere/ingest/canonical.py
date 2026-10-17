"""Canonical staging: landing tables -> one person per human -> Agere canonical records -> the full semantic layer.

    stage(landing_path) -> report dict

1. Identity (ingest/identity.py) writes stg_person_map(source_key, person_id, tier, evidence) and
   stg_person(person_id, payload JSON with the canonical patient fields).
   source_key conventions (every adapter resolves people through these, never through raw ids):
     epic:<instance>:<pat_id>      athena:<contextid>:<patientid>   ecw:<practice>:<pid>
     fhir:<practice folder>:<Patient.id>                            mrn:<practice>:<mrn>
     mbi:<MBI>                     member:<payer>:<member_id>       hl7:<assigning authority>:<PID-3.1>
   resolve(db, source_key) -> person_id or None. A record whose person cannot be resolved is kept in stg_unresolved.
2. Adapters (ingest/adapt_claims.py, ingest/adapt_clinical.py) each expose
     records(db, resolve) -> iterator of canonical record dicts
   A canonical record has the envelope fields (record_id, patient_id, source_table = canonical stream name,
   source_system, organization_id, event_date, recorded_date, available_date, encounter_id, source_patient_id,
   supersedes_record_id, event_date_precision) plus the stream's fields named exactly as in
   docs/agere/canonical_streams.json (the record model the semantic layer reads). Adapters choose ONE authoritative
   feed per entity and collapse duplicates (docs/agere/NATIVE_FEED_INVENTORY.md sections 2-3), and keep lineage in
   _lineage (landing table + _file + _row).
3. stage() writes <landing dir>/stack/stack.sqlite3 (patients, records) and run.json, then builds the semantic layer
   with clinical/semantic_layer.build (the same code as every other Agere build), so engines, governed metrics and
   the analysts' docs work unchanged. Superseded versions are removed from records and listed in stg_superseded.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import time

ENVELOPE = ('record_id', 'patient_id', 'source_table', 'source_system', 'organization_id', 'event_date', 'recorded_date',
            'available_date', 'encounter_id', 'source_patient_id', 'supersedes_record_id', 'event_date_precision')


def resolver(db):
    cache = {}

    def resolve(source_key):
        if source_key is None:
            return None
        if source_key not in cache:
            row = db.execute('SELECT person_id FROM stg_person_map WHERE source_key = ?', (source_key,)).fetchone()
            cache[source_key] = row[0] if row else None
        return cache[source_key]
    return resolve


def adapters():
    import importlib
    out = []
    for name in ('adapt_claims', 'adapt_clinical'):
        try:
            out.append(importlib.import_module(f'{__package__}.{name}'))
        except ImportError:
            continue
    return out


def _record_id(rec):
    if rec.get('record_id'):
        return str(rec['record_id'])
    basis = json.dumps(rec.get('_lineage') or rec, sort_keys=True, default=str)
    return 'AGR-' + hashlib.sha256(basis.encode()).hexdigest()[:24]


def stage(landing_path, *, as_of=None):
    from . import identity
    landing_path = Path(landing_path)
    started = time.time()
    db = sqlite3.connect(landing_path)
    db.execute('PRAGMA journal_mode=WAL')
    report = {'identity': identity.build(db), 'streams': {}, 'unresolved': {}, 'adapters': {}}
    db.commit()
    resolve = resolver(db)
    out_dir = landing_path.parent / 'stack'
    out_dir.mkdir(exist_ok=True)
    stack_path = out_dir / 'stack.sqlite3'
    tmp = out_dir / 'stack.sqlite3.tmp'
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    out.executescript('''CREATE TABLE patients(id TEXT PRIMARY KEY, payload TEXT);
        CREATE TABLE records(id TEXT PRIMARY KEY, patient TEXT, stream TEXT, payload TEXT);
        CREATE TABLE stg_unresolved(stream TEXT, source_patient_id TEXT, lineage TEXT);
        CREATE TABLE stg_superseded(record_id TEXT, superseded_by TEXT, stream TEXT);''')
    for pid, payload in db.execute('SELECT person_id, payload FROM stg_person'):
        out.execute('INSERT OR REPLACE INTO patients VALUES (?, ?)', (pid, payload))
    streams, unresolved, batch = Counter(), Counter(), []
    for mod in adapters():
        name = mod.__name__.rsplit('.', 1)[-1]
        t0, n = time.time(), 0
        for rec in mod.records(db, resolve):
            rec['record_id'] = _record_id(rec)
            if not rec.get('patient_id'):
                unresolved[rec.get('source_table')] += 1
                out.execute('INSERT INTO stg_unresolved VALUES (?,?,?)', (rec.get('source_table'), rec.get('source_patient_id'),
                                                                          json.dumps(rec.get('_lineage'), default=str)))
                continue
            rec.setdefault('synthetic', True)
            rec.setdefault('identity_status', 'matched')
            batch.append((rec['record_id'], rec['patient_id'], rec['source_table'], json.dumps(rec, default=str)))
            streams[rec['source_table']] += 1
            n += 1
            if len(batch) >= 5000:
                out.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?,?)', batch); batch = []
        report['adapters'][name] = {'records': n, 'seconds': round(time.time() - t0, 1)}
    if batch:
        out.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?,?)', batch)
    # Versions: a record named by a later record's supersedes_record_id is not current.
    sup = out.execute("""SELECT json_extract(payload, '$.supersedes_record_id'), id, stream FROM records
                         WHERE json_extract(payload, '$.supersedes_record_id') IS NOT NULL""").fetchall()
    out.executemany('INSERT INTO stg_superseded VALUES (?,?,?)', sup)
    out.execute('DELETE FROM records WHERE id IN (SELECT record_id FROM stg_superseded)')
    out.execute('CREATE INDEX records_patient ON records(patient)')
    out.execute('CREATE INDEX records_stream ON records(stream)')
    out.commit()
    total = out.execute('SELECT count(*) FROM records').fetchone()[0]
    people = out.execute('SELECT count(*) FROM patients').fetchone()[0]
    out.close()
    tmp.replace(stack_path)
    digest = hashlib.sha256(f'{stack_path.stat().st_size}:{total}:{people}:{time.time()}'.encode()).hexdigest()
    as_of = as_of or _max_date(stack_path)
    run = {'schema': 'agere-native-stack/1', 'synthetic': True, 'source_digest': digest, 'patients': people,
           'visible_records': total, 'snapshot': {'release': 'native:' + digest[:16], 'as_of': as_of,
                                                   'source_dataset': {'adapter': 'agere-native-ingest/1', 'source': str(landing_path)}}}
    (out_dir / 'run.json').write_text(json.dumps(run, indent=1))
    from ..clinical import semantic_layer
    semantic = semantic_layer.build(out_dir)
    report.update(streams=dict(streams), unresolved=dict(unresolved), records=total, people=people, as_of=as_of,
                  superseded=len(sup), semantic=str(semantic), seconds=round(time.time() - started, 1),
                  tables=dict(streams), issues=sum(unresolved.values()))
    db.close()
    return report


def _max_date(stack_path):
    db = sqlite3.connect(stack_path)
    try:
        v = db.execute("SELECT max(json_extract(payload, '$.event_date')) FROM records "
                       "WHERE json_extract(payload, '$.event_date') <= date('now', '+20 years')").fetchone()[0]
        return (v or '')[:10] or None
    finally:
        db.close()
