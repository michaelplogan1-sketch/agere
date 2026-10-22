"""OMOP round trip for a workspace onboarded from an OMOP CDM source: did the field-mapped semantic layer keep what the OMOP
tables hold, and (when a native reference build of the same people is available) does it reproduce the native build?

    source_parity(omop_db, semantic_db) -> {'checks': [{omop_table, entity, omop_rows, modeled_rows, ...}], 'all_match'}
    native_parity(omop_db, semantic_db, reference_semantic, out_dir) -> verify.reconcile report on the sampled people or None
    run(omop_db, semantic_db, reference_semantic=None, out_dir=None) -> round-trip report (round_trip.json)

Source parity counts distinct people (person -> patient), and rows of each clinical table against the entity it maps to
(visit_occurrence -> visit encounters, condition_occurrence -> condition, measurement -> measurement, drug_exposure ->
medication, cost -> claim, procedure_occurrence -> claim_line). Native parity reads person_source_value (the original ids an
OMOP ETL keeps), extracts those people from the reference semantic layer (verify.native_extract, read-only) and runs the
onboarding reconciliation (aggregates within 1% + acceptance questions). It annotates the workspace; it never blocks it.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

VERSION = 'omop-round-trip/1'
# omop table -> (entity table, count expression over the entity table)
PAIRS = [('person', 'patient', 'count(DISTINCT patient_id)', 'count(DISTINCT person_id)'),
         ('visit_occurrence', 'visit', 'count(DISTINCT encounter_id)', 'count(DISTINCT encounter_number)'),
         ('condition_occurrence', 'condition', 'count(*)', 'count(*)'),
         ('measurement', 'measurement', 'count(*)', 'count(*)'),
         ('drug_exposure', 'medication', 'count(*)', 'count(*)'),
         ('cost', 'claim', 'count(DISTINCT claim_id)', 'count(DISTINCT claim_source_value)'),
         ('procedure_occurrence', 'claim_line', 'count(*)', 'count(*)')]


def _ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def _scalar(db, sql):
    try:
        return db.execute(sql).fetchone()[0]
    except sqlite3.Error as exc:
        return f'error: {exc}'[:120]


def source_parity(omop_db, semantic_db, tolerance=0.01):
    src, sem = _ro(omop_db), _ro(semantic_db)
    checks = []
    try:
        omop_tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, entity, model_expr, omop_expr in PAIRS:
            if table not in omop_tables: continue
            a = _scalar(src, f'SELECT {omop_expr} FROM "{table}"')
            b = _scalar(sem, f'SELECT {model_expr} FROM "{entity}"')
            if isinstance(a, str) and a.startswith('error'): a = _scalar(src, f'SELECT count(*) FROM "{table}"')
            ok = isinstance(a, int) and isinstance(b, int) and (abs(a - b) <= tolerance * max(a, 1))
            checks.append({'omop_table': table, 'entity': entity, 'omop_rows': a, 'modeled_rows': b, 'match': ok})
    finally:
        src.close(); sem.close()
    return {'tolerance': tolerance, 'checks': checks, 'all_match': bool(checks) and all(c['match'] for c in checks)}


def source_ids(omop_db):
    db = _ro(omop_db)
    try:
        return sorted(r[0] for r in db.execute('SELECT DISTINCT person_source_value FROM person WHERE person_source_value IS NOT NULL'))
    except sqlite3.Error:
        return []
    finally:
        db.close()


def native_parity(omop_db, semantic_db, reference_semantic, out_dir):
    from . import verify
    ids = source_ids(omop_db)
    ref = Path(reference_semantic)
    if not ids or not ref.is_file(): return None
    db = _ro(ref)
    try:
        found = {r[0] for r in db.execute('SELECT patient_id FROM patient WHERE patient_id IN (SELECT value FROM json_each(?))', (json.dumps(ids),))}
    finally:
        db.close()
    if not found: return {'reference': str(ref), 'people': len(ids), 'found_in_reference': 0, 'skipped': 'no OMOP person is in the reference build'}
    extract = Path(out_dir) / 'round-trip-native-sample.sqlite3'
    verify.native_extract(ref, sorted(found), extract)
    rec = verify.reconcile(semantic_db, extract)
    extract.unlink(missing_ok=True)
    return {'reference': str(ref), 'people': len(ids), 'found_in_reference': len(found),
            'all_within_tolerance': rec['all_within_tolerance'], 'tolerance': rec['tolerance'],
            'acceptance_matched': rec['acceptance_matched'], 'acceptance_total': len(rec['acceptance']),
            'aggregates': rec['aggregates'], 'acceptance': rec['acceptance']}


def run(omop_db, semantic_db, reference_semantic=None, out_dir=None):
    report = {'schema': VERSION, 'omop_source': str(omop_db), 'semantic': str(semantic_db), 'source_parity': source_parity(omop_db, semantic_db)}
    if reference_semantic:
        try:
            report['native_parity'] = native_parity(omop_db, semantic_db, reference_semantic, out_dir or Path(semantic_db).parent)
        except Exception as exc:  # noqa: BLE001 - annotation only
            report['native_parity'] = {'reference': str(reference_semantic), 'error': f'{type(exc).__name__}: {exc}'[:400]}
    np_ = report.get('native_parity') or {}
    report['reproduced'] = report['source_parity']['all_match'] and (np_.get('all_within_tolerance', True) if 'error' not in np_ else False)
    return report


def default_reference():
    """The active warehouse's semantic layer (config/agere/active_warehouse.json), the native build an OMOP fixture of its
    people is checked against; None when it is not on this machine."""
    cfg = Path(__file__).resolve().parents[3] / 'config' / 'agere' / 'active_warehouse.json'
    try:
        p = Path(json.loads(cfg.read_text())['build']) / 'stack' / 'semantic.sqlite3'
        return p if p.is_file() else None
    except (OSError, KeyError, ValueError):
        return None
