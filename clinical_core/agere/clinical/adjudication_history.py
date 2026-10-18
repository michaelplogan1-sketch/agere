"""Payer adjudication history: every received adjudication version of every payer medical claim.

The semantic layer keeps only current versions, so a denial that was later reworked and paid (its record superseded by
the paid adjudication) disappears, and denial-history questions (top denial reasons, CO-18 duplicate denials, initial
denial rate, denials overturned) undercount. Build log step 8 measured it on the v2.8 sample: 850 claims had a CO-18
denial dated 2028 in the received data, but only 689 were still visible in the semantic layer.

This pass reads the build's own received warehouse (<build>/warehouse.sqlite3, which keeps every version) and writes
payer_adjudication into semantic.sqlite3. Rules: docs/agere/CANONICAL_MODEL.md, claim version resolution:
- order adjudications by adjudication_sequence, then adjudication (event) date; never by arrival;
- is_last marks each claim's final adjudication; superseded marks versions another record replaced;
- paid amounts are signed (reversals and recoupments negative), so summing by paid_date nets them out.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

VERSION = 'adjudication-history/1'

DDL = """
CREATE TABLE payer_adjudication(payer_claim_id TEXT, record_id TEXT PRIMARY KEY, patient_id TEXT, sequence INTEGER,
  claim_status TEXT, carc TEXT, claim_frequency_code TEXT, original_payer_claim_id TEXT, claim_type TEXT,
  adjudication_date TEXT, paid_date TEXT, service_from TEXT, paid_cents INTEGER, allowed_cents INTEGER,
  superseded INTEGER, superseded_by TEXT, is_first INTEGER, is_last INTEGER, available_date TEXT);
CREATE INDEX payer_adjudication_claim ON payer_adjudication(payer_claim_id, sequence);
CREATE INDEX payer_adjudication_status ON payer_adjudication(claim_status, carc, adjudication_date);
"""


def has_history(warehouse_path):
    """True when the received warehouse exists and keeps record versions (a records table); fixtures and directory-only
    stores carry just source_entities."""
    path = Path(warehouse_path)
    if not path.is_file():
        return False
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'").fetchone() is not None
    finally:
        db.close()


def _cents(v):
    try:
        return None if v is None else int(round(float(v) * 100))
    except (TypeError, ValueError):
        return None


def _seq(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def build(out, warehouse_path):
    """Write payer_adjudication into an open semantic-layer connection from the received warehouse. -> summary."""
    # The semantic layer's patient ids differ from the received store's for some segments (deep-chart P-* ids); every claim
    # with a current version is in payer_claim with the semantic id, and older versions share its payer_claim_id.
    person = dict(out.execute('SELECT payer_claim_id, patient_id FROM payer_claim'))
    src = sqlite3.connect(Path(warehouse_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        superseded_by = {old: new for new, old in src.execute(
            "SELECT id, supersedes FROM records WHERE stream = 'payer_medical_claims' AND supersedes IS NOT NULL")}
        rows = []
        for rid, pid, event, arrival, payload in src.execute(
                "SELECT id, patient, event, arrival, payload FROM records WHERE stream = 'payer_medical_claims'"):
            p = json.loads(payload)
            rows.append([p.get('payer_claim_id'), rid, person.get(p.get('payer_claim_id'), pid), _seq(p.get('adjudication_sequence')), p.get('claim_status'),
                         p.get('carc'), str(p.get('claim_frequency_code')) if p.get('claim_frequency_code') is not None else None,
                         p.get('original_payer_claim_id'), p.get('claim_type'), event, p.get('paid_date'), p.get('service_from'),
                         _cents(p.get('paid_amount')), _cents(p.get('allowed_amount')), 1 if rid in superseded_by else 0,
                         superseded_by.get(rid), 0, 0, arrival])
    finally:
        src.close()
    by_claim = {}
    for r in rows:
        by_claim.setdefault(r[0], []).append(r)
    for versions in by_claim.values():
        versions.sort(key=lambda r: (r[3], r[9] or '', r[1]))
        versions[0][16] = 1
        versions[-1][17] = 1
    out.execute('DROP TABLE IF EXISTS payer_adjudication')
    out.executescript(DDL)
    out.executemany('INSERT INTO payer_adjudication VALUES (' + ','.join('?' * 19) + ')', rows)
    return {'version': VERSION, 'adjudications': len(rows), 'claims': len(by_claim),
            'superseded': sum(r[14] for r in rows)}


def reconcile_payer_claim(out):
    """payer_claim.first_status / adjudications / first_available_date from the full adjudication history.

    payer_claim is built from the semantic layer's record table, which keeps current versions only, so a claim whose
    first adjudication was superseded (denied, then reworked and paid) shows the later status as first_status and
    counts only the surviving versions. payer_adjudication (built above from the received warehouse) has every version;
    where it holds a claim, its is_first row and version count win. Claims absent from the history keep their values.
    Called for native workspace builds (canonical staging retires superseded versions from the record store);
    -> {'claims_with_history', 'first_status_changed', 'adjudications_changed'}."""
    tables = {r[0] for r in out.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'payer_claim', 'payer_adjudication'} <= tables:
        return {'skipped': 'payer_claim or payer_adjudication missing'}
    out.execute('DROP TABLE IF EXISTS temp.claim_history')
    out.execute("""CREATE TEMP TABLE claim_history AS
        SELECT payer_claim_id, count(*) AS n, max(CASE WHEN is_first = 1 THEN claim_status END) AS first_status,
               min(available_date) AS first_available
        FROM payer_adjudication WHERE payer_claim_id IS NOT NULL GROUP BY payer_claim_id""")
    out.execute('CREATE INDEX temp.claim_history_id ON claim_history(payer_claim_id)')
    changed = out.execute("""SELECT count(*), sum(c.first_status IS NOT h.first_status), sum(c.adjudications IS NOT h.n)
        FROM payer_claim c JOIN claim_history h USING(payer_claim_id)""").fetchone()
    out.execute("""UPDATE payer_claim SET
        first_status = (SELECT h.first_status FROM claim_history h WHERE h.payer_claim_id = payer_claim.payer_claim_id),
        adjudications = (SELECT h.n FROM claim_history h WHERE h.payer_claim_id = payer_claim.payer_claim_id),
        first_available_date = coalesce((SELECT CASE WHEN h.first_available IS NOT NULL AND
                                                      (payer_claim.first_available_date IS NULL OR h.first_available < payer_claim.first_available_date)
                                                 THEN h.first_available END
                                         FROM claim_history h WHERE h.payer_claim_id = payer_claim.payer_claim_id),
                                        first_available_date)
        WHERE payer_claim_id IN (SELECT payer_claim_id FROM claim_history)""")
    out.execute('DROP TABLE temp.claim_history')
    return {'claims_with_history': changed[0], 'first_status_changed': changed[1] or 0, 'adjudications_changed': changed[2] or 0}


def apply(semantic_path):
    """Upgrade an existing semantic.sqlite3 in place (idempotent); records the pass in meta."""
    semantic_path = Path(semantic_path)
    warehouse = semantic_path.parent.parent / 'warehouse.sqlite3'
    out = sqlite3.connect(semantic_path)
    try:
        summary = build(out, warehouse)
        out.execute("DELETE FROM meta WHERE key = 'adjudication_history'")
        out.execute("INSERT INTO meta VALUES ('adjudication_history', ?)", (json.dumps(summary),))
        out.commit()
        return summary
    finally:
        out.close()


RECORD_DDL = """
CREATE TABLE record_correction(record_id TEXT PRIMARY KEY, stream TEXT, patient_id TEXT, superseded_by TEXT,
  replacement_stream TEXT, link_field TEXT, event_date TEXT, available_date TEXT, replacement_available_date TEXT);
CREATE INDEX record_correction_stream ON record_correction(stream);
"""


def build_corrections(out, warehouse_path):
    """record_correction: every received record that a later record supersedes, across all streams (the semantic layer's
    record table keeps current versions only). Patient ids are mapped to the semantic layer's where it knows the record."""
    src = sqlite3.connect(Path(warehouse_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        links = [(*r, 'supersedes_record_id') for r in src.execute(
            'SELECT id, supersedes, stream, arrival FROM records WHERE supersedes IS NOT NULL')]
        # Lab corrections also link through the payload (replaces_result_record_id), not mirrored in supersedes.
        links += [(*r, 'replaces_result_record_id') for r in src.execute(
            "SELECT id, json_extract(payload, '$.replaces_result_record_id'), stream, arrival FROM records "
            "WHERE stream = 'labs' AND json_extract(payload, '$.replaces_result_record_id') IS NOT NULL")]
        olds = {old for _, old, _, _, _ in links}
        info = {rid: (stream, pid, event, arrival) for rid, stream, pid, event, arrival in src.execute(
            'SELECT id, stream, patient, event, arrival FROM records') if rid in olds}
        rows = []
        for new, old, stream, arrival, field in links:
            o = info.get(old)
            if o:
                rows.append((old, o[0], o[1], new, stream, field, o[2], o[3], arrival))
    finally:
        src.close()
    known = {r[0] for r in out.execute('SELECT patient_id FROM patient')}
    out.execute('DROP TABLE IF EXISTS record_correction')
    out.executescript(RECORD_DDL)
    out.executemany('INSERT OR IGNORE INTO record_correction VALUES (?,?,?,?,?,?,?,?,?)', rows)
    return {'corrections': len(rows), 'patients_known': sum(1 for r in rows if r[2] in known)}


def apply_corrections(semantic_path):
    semantic_path = Path(semantic_path)
    out = sqlite3.connect(semantic_path)
    try:
        summary = build_corrections(out, semantic_path.parent.parent / 'warehouse.sqlite3')
        out.execute("DELETE FROM meta WHERE key = 'record_correction'")
        out.execute("INSERT INTO meta VALUES ('record_correction', ?)", (json.dumps(summary),))
        out.commit()
        return summary
    finally:
        out.close()
