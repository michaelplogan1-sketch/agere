"""Read-only adapter for the original-cohort public healthcare warehouse.

Only the four public SQLite tables are eligible. No private/ or generator files
are opened. Literal source facts, original IDs and unknown clocks are preserved.
"""
from collections import Counter
from datetime import date
from pathlib import Path
import hashlib
import json
import sqlite3
from ..contracts import digest

VERSION = 'original-cohort-warehouse-adapter/1'
PRIVATE = {'truth', 'target', 'scenario', 'source_family', 'family_id', 'clean_text',
           'original_text', 'oracle', 'latent_state', 'expected_answer'}


def checksum(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def validate_facts(value):
    if isinstance(value, dict):
        if set(value) & PRIVATE:
            raise ValueError('Private author field in public source facts')
        for child in value.values(): validate_facts(child)
    elif isinstance(value, list):
        for child in value: validate_facts(child)


def project(row):
    row = dict(row)
    raw = dict(row)
    facts = json.loads(row.pop('facts'))
    if not isinstance(facts, dict): raise ValueError('Source facts must be an object')
    validate_facts(facts)
    if row.get('synthetic') != 1: raise ValueError('Synthetic warehouse required')
    for field in ('event_date', 'recorded_date', 'available_date'):
        if row[field] is not None:
            if date.fromisoformat(row[field]).isoformat() != row[field]:
                raise ValueError('Canonical source date required')
    if row['available_date'] is None: raise ValueError('Receipt date required')
    # Metadata has a separate schema; an embedded fact cannot override its ID/date.
    for key in set(row) & set(facts):
        if row[key] != facts[key]: raise ValueError('Source metadata/fact collision: '+key)
    result = {**facts, **row, 'synthetic': True,
              'identity_status': 'matched', 'identity_link_basis': 'public_warehouse_patient_foreign_key',
              '_warehouse_fields': sorted(facts),
              '_warehouse_source': {'adapter': VERSION, 'row_sha256': digest(raw),
                  'source_table': row['source_table'], 'field_aliases': {},
                  'identity_scope': 'Literal source patient binding; no independent patient-matching adjudication'}}
    # A literal field-name alias, not a clinical code/diagnosis inference.
    if row['source_table'] == 'labs' and 'test' not in result and isinstance(facts.get('name'), str):
        result['test'] = facts['name']
        result['_warehouse_source']['field_aliases']['test'] = 'facts.name'
    return result


def ingest(warehouse, root):
    root = Path(root).resolve()
    manifest = json.loads((root/'manifest.json').read_text())
    if manifest.get('schema') != 'single-healthcare-warehouse/1':
        raise ValueError('Original-cohort healthcare warehouse manifest required')
    source = root/'warehouse.sqlite'
    if source.resolve().parent != root: raise ValueError('Source must be inside the pinned release')
    sha = checksum(source)
    if manifest['files'].get('warehouse.sqlite') != sha:
        raise ValueError('Warehouse checksum differs from release manifest')
    date.fromisoformat(manifest['as_of'])
    code = checksum(__file__)
    release = digest([VERSION, sha, code, manifest['as_of']])
    if warehouse.db.execute('SELECT 1 FROM releases WHERE id=?', (release,)).fetchone():
        return release, {'cache_hit': True, 'adapter': VERSION, 'source': str(source), 'sha256': sha}
    src = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    counts = Counter(); clocks = Counter()
    try:
        with warehouse.db:
            for raw in src.execute('SELECT * FROM patients ORDER BY patient_id'):
                patient = dict(raw)
                # Reported age remains tied to its profile date; no invented DOB.
                patient['synthetic'] = True
                warehouse.db.execute('INSERT INTO patients VALUES (?,?,?)',
                    (release, patient['patient_id'], json.dumps(patient)))
                counts['patients'] += 1
            for raw in src.execute('SELECT * FROM source_records ORDER BY record_id'):
                r = project(raw)
                if not warehouse.db.execute('SELECT 1 FROM patients WHERE release=? AND id=?',
                        (release,r['patient_id'])).fetchone(): raise ValueError('Unknown source patient')
                warehouse.db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (release,r['record_id'],r['patient_id'],r['source_table'],r['event_date'],r['available_date'],
                     r['identity_status'],r.get('supersedes_record_id'),digest(r),json.dumps(r,allow_nan=False)))
                counts[r['source_table']] += 1
                clocks['event_date_unknown' if r['event_date'] is None else 'event_date_present'] += 1
            # Clinical reference data only; no private manifest content is projected.
            warehouse.db.execute('CREATE TABLE IF NOT EXISTS source_entities(release TEXT, kind TEXT, id TEXT, payload TEXT, PRIMARY KEY(release,kind,id))')
            for table in ('organizations','clinicians'):
                for raw in src.execute('SELECT * FROM '+table):
                    obj=dict(raw); identifier=next((obj[k] for k in (table[:-1]+'_id','id') if k in obj),None)
                    if identifier is None: raise ValueError('Missing public entity identifier')
                    warehouse.db.execute('INSERT INTO source_entities VALUES (?,?,?,?)',
                        (release,table,identifier,json.dumps(obj)))
            if checksum(source) != sha: raise ValueError('Source changed during ingestion')
            receipt={'adapter':VERSION,'source':str(source),'source_sha256':sha,'code_sha256':code,
                'as_of':manifest['as_of'],'counts':dict(counts),'clocks':dict(clocks),
                'original_patient_id_digest':digest([r[0] for r in warehouse.db.execute(
                    'SELECT id FROM patients WHERE release=? ORDER BY id',(release,))]),
                'read_boundary':['patients','source_records','organizations','clinicians'],
                'private_files_read':False,'synthetic':True}
            warehouse.db.execute('INSERT INTO releases VALUES (?,?)',(release,json.dumps(receipt)))
        return release, {'cache_hit':False,**receipt}
    finally: src.close()
