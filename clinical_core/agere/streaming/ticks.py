"""The streaming change log: one tick per delivery batch, in <ws>/streaming/streaming.sqlite3.

A tick records what the batch touched -- files, patients, streams, semantic tables -- and flags in event time vs
processing time (Dataflow vocabulary):
- watermark      the event-time completeness estimate before the tick: the build's as_of (max event date received).
- late_file      a file delivered more than 7 days past its series' usual lag after its period ended (usual lag = median
                 over the series' earlier deliveries in the manifest; 30 days for monthly series with fewer than 3).
- late_record    a new record whose event_date is more than LATE_DAYS before the watermark at arrival.
- claim_reversal a claim / pharmacy claim version that reverses, voids, recoups or replaces an earlier one (status,
                 frequency code 7/8, or supersedes_record_id on a claim stream), or that retracts a visible claim.
- retro_termination  an enrollment span version whose coverage_end lies before the date it became available, or that
                 moves an existing span's end earlier.
- corrected_lab  a lab result marked corrected / amended, replacing an earlier result, or whose payload changed.
- restatement    any other record whose payload changed under the same record id.
- retraction     a visible record removed (superseded or no longer delivered).
- identity_merge / identity_split   a new key links two charts into one person (or a link is broken).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import json
from pathlib import Path
import sqlite3
import statistics
import time

LATE_DAYS = 30
CLAIM_STREAMS = ('medical_claims', 'payer_medical_claims', 'pharmacy_claim_transactions', 'payer_pharmacy_claims',
                 'provider_remittance_advice', 'medical_claim_submissions')
REVERSAL_WORDS = ('revers', 'void', 'recoup', 'cancel', 'adjust', 'replac')
DEFAULT_LAG = {'week': 14, 'month': 30, 'quarter': 60, 'year': 120}

DDL = """
CREATE TABLE IF NOT EXISTS ticks(tick_id INTEGER PRIMARY KEY, started TEXT, finished TEXT, files INTEGER, seconds REAL,
  as_of_before TEXT, as_of TEXT, processing_date TEXT, patients_touched INTEGER, patients_rebuilt INTEGER, records_added INTEGER,
  records_removed INTEGER, records_changed INTEGER, late_files INTEGER, late_records INTEGER, corrections INTEGER,
  summary TEXT);
CREATE TABLE IF NOT EXISTS tick_file(tick_id INTEGER, relpath TEXT, series TEXT, period TEXT, cadence TEXT, delivered TEXT,
  lag_days INTEGER, usual_lag_days INTEGER, late INTEGER, replaced INTEGER, rows INTEGER, tables TEXT);
CREATE TABLE IF NOT EXISTS tick_patient(tick_id INTEGER, patient_id TEXT, added INTEGER, removed INTEGER, changed INTEGER,
  streams TEXT, reason TEXT, PRIMARY KEY(tick_id, patient_id));
CREATE TABLE IF NOT EXISTS tick_stream(tick_id INTEGER, stream TEXT, added INTEGER, removed INTEGER, changed INTEGER,
  late INTEGER, corrections INTEGER, PRIMARY KEY(tick_id, stream));
CREATE TABLE IF NOT EXISTS tick_table(tick_id INTEGER, tbl TEXT, rows_changed INTEGER, patients_changed INTEGER,
  rows_deleted INTEGER, rows_inserted INTEGER, PRIMARY KEY(tick_id, tbl));
CREATE TABLE IF NOT EXISTS tick_table_patient(tick_id INTEGER, tbl TEXT, patient_id TEXT, PRIMARY KEY(tick_id, tbl, patient_id));
CREATE TABLE IF NOT EXISTS tick_flag(tick_id INTEGER, kind TEXT, patient_id TEXT, stream TEXT, record_id TEXT, relpath TEXT,
  event_date TEXT, available_date TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS tick_flag_kind ON tick_flag(tick_id, kind);
"""


def path(ws_dir):
    p = Path(ws_dir) / 'streaming'
    p.mkdir(exist_ok=True)
    return p / 'streaming.sqlite3'


def connect(ws_dir):
    db = sqlite3.connect(path(ws_dir), timeout=60)
    db.executescript(DDL)
    return db


def _day(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _series_history(manifest):
    """{series: [(delivered, lag_days)]} over every file the manifest lists (the delivery's own record of when each earlier
    file of a series arrived relative to its period end)."""
    from ..ingest import delivery
    hist = {}
    for rel, m in manifest.items():
        d = delivery.describe_path(rel)
        day = _day(m.get('delivered'))
        if d['ordinal'] is None or day is None:
            continue
        hist.setdefault(d['series'], []).append((day, (day - delivery._period_end(d['cadence'], d['ordinal'])).days))
    return hist


def file_facts(db, relpaths_rows, manifest):
    """Late-file flags: a file is late when it arrived more than 7 days past its series' usual lag after its period ended.
    Usual lag = median lag of the series' files delivered earlier (from the delivery manifest), or DEFAULT_LAG for the
    cadence when the series has fewer than 3 earlier files. -> list of dicts."""
    from ..ingest import delivery
    history = _series_history(manifest)
    out = []
    for f in relpaths_rows:
        rel = f['relpath']
        d = delivery.describe_path(rel)
        m = manifest.get(rel) or {}
        delivered = m.get('delivered')
        lag, usual, late = None, None, 0
        if d['ordinal'] is not None and delivered and _day(delivered):
            end = delivery._period_end(d['cadence'], d['ordinal'])
            lag = (_day(delivered) - end).days
            prior = [lg for day, lg in history.get(d['series'], []) if day < _day(delivered)]
            usual = int(statistics.median(prior)) if len(prior) >= 3 else DEFAULT_LAG.get(d['cadence'], 30)
            late = int(lag > usual + 7)
        out.append({'relpath': rel, 'series': d['series'], 'period': d['period'], 'cadence': d['cadence'], 'delivered': delivered,
                    'lag_days': lag, 'usual_lag_days': usual, 'late': late, 'replaced': int(bool(f.get('replaced'))),
                    'rows': f.get('rows'), 'tables': json.dumps(f.get('tables') or {})})
    return out


def _payload(p):
    try:
        return json.loads(p) if isinstance(p, str) else (p or {})
    except ValueError:
        return {}


def _status_words(r):
    vals = [str(r.get(k) or '').lower() for k in ('status', 'claim_status', 'adjudication_status', 'transaction_type',
                                                     'claim_frequency_code', 'frequency_code', 'action')]
    return vals


def record_flags(change, lineage_files=None):
    """Correction / lateness flags for the records a stage changed. -> list of flag dicts."""
    flags = []
    wm = _day(change.get('as_of_before'))
    new = change.get('new_records') or {}
    old = change.get('old_records') or {}
    new_versions = change.get('new_versions') or {}
    old_versions = change.get('old_versions') or {}
    seen_versions = set()
    for rid, (pid, stream, payload) in list(new.items()) + [(k, v) for k, v in new_versions.items() if k not in new]:
        if rid in seen_versions:
            continue
        seen_versions.add(rid)
        r = _payload(payload)
        ev, av = r.get('event_date'), r.get('available_date')
        lin = r.get('_lineage') or {}
        rel = (lin.get('_file') if isinstance(lin, dict) else None) or (lin[0].get('_file') if isinstance(lin, list) and lin and isinstance(lin[0], dict) else None)
        base = {'patient_id': pid, 'stream': stream, 'record_id': rid, 'relpath': rel, 'event_date': (ev or '')[:10] or None,
                'available_date': (av or '')[:10] or None}
        is_new = rid in new and rid not in old
        if is_new and wm and _day(ev) and (wm - _day(ev)).days > LATE_DAYS:
            flags.append({**base, 'kind': 'late_record', 'detail': f'event {str(ev)[:10]} is {(wm - _day(ev)).days} days behind watermark {wm}'})
        words = _status_words(r)
        if stream in CLAIM_STREAMS:
            fc = str(r.get('claim_frequency_code') or r.get('frequency_code') or '')
            if r.get('supersedes_record_id') or fc in ('7', '8') or any(w in v for v in words for w in REVERSAL_WORDS):
                flags.append({**base, 'kind': 'claim_reversal',
                              'detail': json.dumps({'status': r.get('status') or r.get('claim_status'), 'frequency_code': fc or None,
                                                    'supersedes': r.get('supersedes_record_id')})})
        elif stream == 'enrollment_spans':
            end = (r.get('coverage_end') or '')[:10]
            prev = _payload((old.get(rid) or old_versions.get(r.get('supersedes_record_id') or '') or (None, None, None))[2])
            prev_end = (prev.get('coverage_end') or '')[:10] if prev else ''
            if end and ((av and end < str(av)[:10]) and (r.get('supersedes_record_id') or rid in old) or (prev_end and end < prev_end)):
                flags.append({**base, 'kind': 'retro_termination', 'detail': f'coverage_end {end} (was {prev_end or "open/none"}), available {str(av)[:10]}'})
        elif stream == 'labs':
            st = str(r.get('result_status') or '').lower()
            if st in ('corrected', 'amended', 'c', 'a') or r.get('supersedes_record_id') or r.get('replaces_result_record_id') or rid in old:
                flags.append({**base, 'kind': 'corrected_lab', 'detail': json.dumps({'result_status': r.get('result_status'),
                                                                                       'replaces': r.get('supersedes_record_id') or r.get('replaces_result_record_id'),
                                                                                       'value': r.get('value')})})
        if rid in old and stream not in ('labs',) and not any(f['record_id'] == rid for f in flags[-1:]):
            flags.append({**base, 'kind': 'restatement', 'detail': 'payload changed under the same record id'})
    for rid in change.get('records_removed') or []:
        pid, stream, payload = old.get(rid) or (None, None, None)
        r = _payload(payload)
        kind = 'claim_reversal' if stream in CLAIM_STREAMS else 'retraction'
        flags.append({'kind': kind, 'patient_id': pid, 'stream': stream, 'record_id': rid, 'relpath': None,
                      'event_date': (r.get('event_date') or '')[:10] or None, 'available_date': (r.get('available_date') or '')[:10] or None,
                      'detail': 'visible record retracted (superseded or no longer delivered)'})
    idiff = change.get('identity_diff') or {}
    for new_pid, olds in (idiff.get('merges') or {}).items():
        flags.append({'kind': 'identity_merge', 'patient_id': new_pid, 'stream': None, 'record_id': None, 'relpath': None,
                      'event_date': None, 'available_date': None, 'detail': json.dumps({'merged_from': olds})})
    for old_pid, news in (idiff.get('splits') or {}).items():
        flags.append({'kind': 'identity_split', 'patient_id': old_pid, 'stream': None, 'record_id': None, 'relpath': None,
                      'event_date': None, 'available_date': None, 'detail': json.dumps({'split_into': news})})
    return flags


CORRECTION_KINDS = ('claim_reversal', 'retro_termination', 'corrected_lab', 'restatement', 'retraction')


def record_tick(ws_dir, *, started, files, change, semantic_report, table_changes, manifest, processing_date=None, seconds=None):
    db = connect(ws_dir)
    try:
        facts = file_facts(db, files, manifest)
        flags = record_flags(change)
        late_files = [f for f in facts if f['late']]
        for f in late_files:
            flags.append({'kind': 'late_file', 'patient_id': None, 'stream': None, 'record_id': None, 'relpath': f['relpath'],
                          'event_date': f['period'], 'available_date': f['delivered'],
                          'detail': f"delivered {f['lag_days']} days after period end (series usual {f['usual_lag_days']})"})
        kinds = Counter(f['kind'] for f in flags)
        per_patient = defaultdict(lambda: {'added': 0, 'removed': 0, 'changed': 0, 'streams': set()})
        per_stream = defaultdict(Counter)
        for rid in change.get('records_added') or []:
            pid, stream, _ = change['new_records'][rid]
            per_patient[pid]['added'] += 1; per_patient[pid]['streams'].add(stream); per_stream[stream]['added'] += 1
        for rid in change.get('records_changed') or []:
            pid, stream, _ = change['new_records'][rid]
            per_patient[pid]['changed'] += 1; per_patient[pid]['streams'].add(stream); per_stream[stream]['changed'] += 1
        for rid in change.get('records_removed') or []:
            pid, stream, _ = change['old_records'].get(rid) or (None, None, None)
            per_patient[pid]['removed'] += 1; per_patient[pid]['streams'].add(stream); per_stream[stream]['removed'] += 1
        for f in flags:
            if f.get('stream'):
                if f['kind'] == 'late_record':
                    per_stream[f['stream']]['late'] += 1
                elif f['kind'] in CORRECTION_KINDS:
                    per_stream[f['stream']]['corrections'] += 1
        for pid in change.get('touched_patients') or []:
            per_patient[pid]  # touched without a visible record change (versions, person payload, identity)
        corrections = sum(kinds[k] for k in CORRECTION_KINDS)
        summary = {'flags': dict(kinds), 'identity': change.get('identity_diff'), 'stage_timings': change.get('timings'),
                   'semantic': {k: v for k, v in (semantic_report or {}).items() if k not in ('splice',)}}
        with db:
            cur = db.execute('INSERT INTO ticks(started, finished, files, seconds, as_of_before, as_of, processing_date, patients_touched, '
                             'patients_rebuilt, records_added, records_removed, records_changed, late_files, late_records, corrections, summary) '
                             'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (started, time.strftime('%Y-%m-%dT%H:%M:%S'), len(files), seconds, change.get('as_of_before'), change.get('as_of'),
                              processing_date, len(change.get('touched_patients') or []), (semantic_report or {}).get('patients_rebuilt'),
                              len(change.get('records_added') or []), len(change.get('records_removed') or []),
                              len(change.get('records_changed') or []), len(late_files), kinds['late_record'], corrections,
                              json.dumps(summary, default=str)))
            tick = cur.lastrowid
            db.executemany('INSERT INTO tick_file VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           [(tick, f['relpath'], f['series'], f['period'], f['cadence'], f['delivered'], f['lag_days'], f['usual_lag_days'],
                             f['late'], f['replaced'], f['rows'], f['tables']) for f in facts])
            reasons = defaultdict(set)
            for f in flags:
                if f.get('patient_id'):
                    reasons[f['patient_id']].add(f['kind'])
            db.executemany('INSERT OR REPLACE INTO tick_patient VALUES (?,?,?,?,?,?,?)',
                           [(tick, pid, v['added'], v['removed'], v['changed'], json.dumps(sorted(s for s in v['streams'] if s)),
                             ','.join(sorted(reasons.get(pid, ()))) or None) for pid, v in per_patient.items() if pid])
            db.executemany('INSERT OR REPLACE INTO tick_stream VALUES (?,?,?,?,?,?,?)',
                           [(tick, s, c['added'], c['removed'], c['changed'], c['late'], c['corrections']) for s, c in per_stream.items() if s])
            for t, info in (table_changes or {}).items():
                db.execute('INSERT OR REPLACE INTO tick_table VALUES (?,?,?,?,?,?)',
                           (tick, t, info.get('rows_changed'), len(info.get('patients') or ()), info.get('deleted'), info.get('inserted')))
                db.executemany('INSERT OR IGNORE INTO tick_table_patient VALUES (?,?,?)', ((tick, t, p) for p in info.get('patients') or ()))
            db.executemany('INSERT INTO tick_flag VALUES (?,?,?,?,?,?,?,?,?)',
                           [(tick, f['kind'], f.get('patient_id'), f.get('stream'), f.get('record_id'), f.get('relpath'),
                             f.get('event_date'), f.get('available_date'), f.get('detail')) for f in flags])
        return tick, dict(kinds)
    finally:
        db.close()


def tick_summary(ws_dir, tick_id):
    db = connect(ws_dir)
    try:
        row = db.execute('SELECT * FROM ticks WHERE tick_id = ?', (tick_id,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in db.execute('SELECT * FROM ticks LIMIT 0').description]
        out = dict(zip(cols, row))
        out['summary'] = json.loads(out['summary'] or '{}')
        out['tables'] = {t: {'rows_changed': r, 'patients_changed': p} for t, r, p in
                         db.execute('SELECT tbl, rows_changed, patients_changed FROM tick_table WHERE tick_id = ? AND rows_changed > 0', (tick_id,))}
        return out
    finally:
        db.close()
