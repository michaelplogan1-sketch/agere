"""Mapping receipt from a workspace or build to the canonical model (canonical/1: Tuva input layer + OMOP CDM names).

    stream_receipt(streams, stream_map=None)    {raw stream: records} -> receipt (native / warehouse builds)
    entity_receipt(semantic_db)                 modeled semantic tables -> receipt (field-mapped workspaces)
    tuva_tables(tuva_db=None)                   row counts of the built Tuva input layer and core (read-only DuckDB)
    for_build(build_dir)                        receipt for an activated build (stack/source-profile.json + tuva.duckdb)

Each canonical table (config/agere/canonical/stream_map.json 'tables') names the Tuva table and OMOP table it feeds. A
receipt counts, per canonical table, the received records mapped to it, and states which streams (or source tables) do
not map to any canonical table and why (the coverage gate's 'unused' reasons). Shares are of received records, so a
large stream that maps nowhere is visible. Synthetic data only; read-only.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sqlite3

VERSION = 'canonical-mapping-receipt/1'
ROOT = Path(__file__).resolve().parents[3]
TUVA_DB = ROOT / 'artifacts' / 'test-warehouse' / 'tuva.duckdb'
# semantic-layer entity (onboarding/contract.py model) -> canonical table
ENTITY_CANONICAL = {'patient': 'person', 'coverage': 'enrollment_span', 'visit': 'encounter', 'encounter': 'encounter',
                    'condition': 'condition', 'measurement': 'lab_result', 'medication': 'medication', 'claim': 'medical_claim',
                    'claim_line': 'claim_line', 'clinician': 'provider', 'organization': 'organization', 'appointment': 'appointment',
                    'immunization': 'immunization', 'stay': 'encounter', 'claim_adjustment': 'claim_adjudication_history',
                    'encounter_diagnosis': 'condition', 'social_need': 'sdoh_attribute', 'note_fact': 'document'}


def _map(stream_map=None):
    from ..canonical import coverage
    return stream_map or coverage.load_stream_map()


def _summarize(by_table, unmapped, total, tables, coverage_report=None, source='streams'):
    mapped = sum(v['records'] for v in by_table.values())
    per_source = {}  # a source feeding several canonical tables counts once toward the Tuva / OMOP shares
    for t, v in by_table.items():
        for src in v['sources']:
            got = per_source.setdefault(src, {'records': v['by_source'][src], 'tuva': False, 'omop': False})
            got['tuva'] |= bool((tables.get(t) or {}).get('tuva')); got['omop'] |= bool((tables.get(t) or {}).get('omop'))
    tuva = sum(v['records'] for v in per_source.values() if v['tuva'])
    omop = sum(v['records'] for v in per_source.values() if v['omop'])
    rows = [{'canonical_table': t, 'tuva': (tables.get(t) or {}).get('tuva'), 'omop': (tables.get(t) or {}).get('omop'),
             'records': v['records'], 'sources': sorted(v['sources'])} for t, v in sorted(by_table.items(), key=lambda kv: -kv[1]['records'])]
    return {'schema': VERSION, 'source': source, 'records_total': total, 'records_mapped': mapped,
            'share_mapped': round(mapped / total, 4) if total else None,
            'share_tuva': round(tuva / total, 4) if total else None, 'share_omop': round(omop / total, 4) if total else None,
            'tables': rows, 'unmapped': sorted(unmapped, key=lambda u: -u['records']),
            'gate_ok': (coverage_report or {}).get('ok'), 'gate': {k: v for k, v in (coverage_report or {}).items() if k != 'feeds'}}


def stream_receipt(streams, stream_map=None):
    """streams: {raw stream name: received records}. Records of a stream mapped to several canonical tables count once per
    table in by-table rows, once in records_mapped (a stream maps or it does not)."""
    from ..canonical import coverage
    sm = _map(stream_map)
    tables, entries = sm.get('tables', {}), sm.get('streams', {})
    report = coverage.check(list(streams), sm)
    by_table = defaultdict(lambda: {'records': 0, 'sources': set(), 'by_source': {}})
    unmapped, total, mapped_total = [], 0, 0
    for s, n in streams.items():
        n = int(n or 0); total += n
        e = entries.get(s)
        if not e or 'unused' in e or not e.get('tables'):
            unmapped.append({'source': s, 'records': n, 'reason': (e or {}).get('unused') or 'not in the canonical stream map'})
            continue
        mapped_total += n
        for t in e['tables']:
            by_table[t]['records'] += n; by_table[t]['sources'].add(s); by_table[t]['by_source'][s] = n
    out = _summarize(by_table, unmapped, total, tables, report)
    out['records_mapped'] = mapped_total
    out['share_mapped'] = round(mapped_total / total, 4) if total else None
    return out


def entity_receipt(semantic_db, stream_map=None):
    """Field-mapped workspace: modeled semantic tables -> canonical tables (row counts); raw_* tables are unmapped sources."""
    tables = _map(stream_map).get('tables', {})
    db = sqlite3.connect(f'file:{semantic_db}?mode=ro', uri=True)
    try:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {t: db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in names if t != 'meta'}
    finally:
        db.close()
    by_table = defaultdict(lambda: {'records': 0, 'sources': set(), 'by_source': {}})
    unmapped, total = [], 0
    for t, n in counts.items():
        if t.startswith('raw_'):
            unmapped.append({'source': t[4:], 'records': n, 'reason': 'source table kept verbatim (raw_*); no modeled entity uses it'
                             if n else 'empty source table'}); continue
        c = ENTITY_CANONICAL.get(t)
        if c is None: continue  # derived/helper tables (concept_patient, measurement_latest, link, ...)
        total += n
        by_table[c]['records'] += n; by_table[c]['sources'].add(t); by_table[c]['by_source'][t] = n
    return _summarize(by_table, unmapped, total, tables, None, source='semantic entities')


def tuva_tables(tuva_db=None):
    """{'path', 'input_layer': {table: rows}, 'core': {table: rows}} from a built Tuva DuckDB (read-only), or None."""
    p = Path(tuva_db or TUVA_DB)
    if not p.is_file(): return None
    try:
        import duckdb
        db = duckdb.connect(str(p), read_only=True)
    except Exception:  # noqa: BLE001 - duckdb missing or the file is locked by a dbt build: no Tuva section
        return None
    try:
        out = {'path': str(p)}
        for schema in ('input_layer', 'core'):
            out[schema] = {t: db.execute(f'SELECT count(*) FROM "{schema}"."{t}"').fetchone()[0] for (t,) in db.execute(
                'SELECT table_name FROM duckdb_tables() WHERE schema_name = ? AND table_name NOT LIKE \'\\_%\' ESCAPE \'\\\' ORDER BY 1',
                [schema]).fetchall()}
        return out
    finally:
        db.close()


def for_build(build_dir, tuva_db=None):
    """Receipt for an activated warehouse build: streams and records from stack/source-profile.json; the Tuva section is
    attached when the built Tuva warehouse holds the same roster (input_layer.patient rows == received patients)."""
    build = Path(build_dir)
    prof_path = build / 'stack' / 'source-profile.json'
    if not prof_path.is_file(): prof_path = build / 'source-profile.json'
    prof = json.loads(prof_path.read_text())
    streams = {s: v.get('records', 0) for s, v in prof['streams'].items()}
    out = stream_receipt(streams)
    out['source'] = str(prof_path)
    tuva = tuva_tables(tuva_db or next((p for p in (build.parent / 'test-warehouse' / 'tuva.duckdb', TUVA_DB) if p.is_file()), None))
    patients = (prof.get('patients') or {}).get('records') if isinstance(prof.get('patients'), dict) else prof.get('patients')
    if tuva:
        tuva['roster_match'] = bool(patients) and tuva.get('input_layer', {}).get('patient') == patients
        tuva['received_patients'] = patients
        out['tuva_build'] = tuva
    return out
