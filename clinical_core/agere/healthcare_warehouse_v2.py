"""Read-only ingestion adapter for standards-coded original-cohort warehouses (schema healthcare-warehouse/2).

Release v2.0 keeps every source value and record ID of the original cohort and adds
standard codes beside them (ICD-10-CM, LOINC, RxNorm/NDC, CVX/MVX, CPT/HCPCS, MS-DRG),
synthetic provider identifiers and patient demographics. This adapter:

* accepts both manifest schemas (`single-healthcare-warehouse/1` and `healthcare-warehouse/2`);
* projects every source record with the unchanged adapter/1 record projector, so record
  payloads, hashes and the record-level `_warehouse_source.adapter` contract stay identical
  to what adapter/1 produces (downstream narrative, retrieval and identity code is untouched);
* carries the new public patient columns (birth_date, race, ethnicity, preferred_language,
  interpreter_needed, marital_status, address, postal_code, county_fips) and the new
  clinician/organization columns (npi, taxonomy_code, tin, address) literally;
* reads the public `data_dictionary` table and records standard-code coverage in the receipt.

Only public tables are read: patients, source_records, organizations, clinicians and
data_dictionary. Nothing under private/ is opened. The adapter/1 module stays byte-identical
so v1.23 builds remain reproducible with their original release identity.
"""
from collections import Counter
from datetime import date
from pathlib import Path
import json
import sqlite3
from ..contracts import digest
from .healthcare_warehouse import checksum, project, validate_facts, VERSION as RECORD_PROJECTION

VERSION = 'original-cohort-warehouse-adapter/2'
SCHEMAS = ('single-healthcare-warehouse/1', 'healthcare-warehouse/2')
# Fact keys that carry a standard code, by code system. Literal values; nothing is derived here.
STANDARD_FIELDS = {
    'icd10': 'ICD-10-CM', 'principal_diagnosis': 'ICD-10-CM', 'diagnosis_codes': 'ICD-10-CM', 'diagnoses': 'ICD-10-CM',
    'snomed': 'SNOMED CT', 'loinc': 'LOINC', 'rxcui': 'RxNorm', 'ndc': 'NDC', 'cvx_code': 'CVX', 'mvx_code': 'MVX',
    'ms_drg': 'MS-DRG', 'billing_provider_npi': 'NPI', 'rendering_provider_npi': 'NPI', 'revenue_code': 'NUBC revenue code',
    'type_of_bill': 'NUBC type of bill', 'place_of_service': 'CMS place of service'}
PATIENT_COLUMNS = ('birth_date', 'race', 'ethnicity', 'preferred_language', 'interpreter_needed', 'marital_status',
                   'address_line', 'city', 'state', 'postal_code', 'county_fips', 'phone')


def adapter_for(manifest):
    """Adapter that reproduces a release: v1 schemas keep adapter/1, v2 schemas need adapter/2."""
    schema = manifest.get('schema')
    if schema == 'single-healthcare-warehouse/1': return 'original-cohort-warehouse-adapter/1'
    if schema == 'healthcare-warehouse/2': return VERSION
    raise ValueError('Unsupported healthcare warehouse manifest schema: ' + str(schema))


def _check_manifest(manifest):
    if manifest.get('schema') not in SCHEMAS:
        raise ValueError('Original-cohort healthcare warehouse manifest required')
    metrics = manifest.get('metrics') or {}
    if manifest['schema'] == 'healthcare-warehouse/2':
        if metrics.get('original_patient_id_set_preserved') is not True or metrics.get('new_patients_added') not in (0, None):
            raise ValueError('v2 release must preserve the original patient roster')
    date.fromisoformat(manifest['as_of'])


def _code_fields(facts):
    found = {}
    for key, system in STANDARD_FIELDS.items():
        v = facts.get(key)
        if v not in (None, '', []): found[key] = system
    sys_ = facts.get('service_code_system')
    if sys_ in ('CPT', 'HCPCS') and facts.get('service_code'): found['service_code'] = sys_
    return found


def ingest(warehouse, root):
    root = Path(root).resolve()
    manifest = json.loads((root/'manifest.json').read_text())
    _check_manifest(manifest)
    source = root/'warehouse.sqlite'
    if source.resolve().parent != root: raise ValueError('Source must be inside the pinned release')
    sha = checksum(source)
    if manifest['files'].get('warehouse.sqlite') != sha:
        raise ValueError('Warehouse checksum differs from release manifest')
    code = digest([checksum(__file__), checksum(Path(__file__).with_name('healthcare_warehouse.py'))])
    release = digest([VERSION, sha, code, manifest['as_of']])
    if warehouse.db.execute('SELECT 1 FROM releases WHERE id=?', (release,)).fetchone():
        return release, {'cache_hit': True, 'adapter': VERSION, 'source': str(source), 'sha256': sha}
    src = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    counts = Counter(); clocks = Counter(); coded = Counter(); code_systems = Counter()
    demographics = Counter()
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        dictionary = ([dict(r) for r in src.execute('SELECT * FROM data_dictionary ORDER BY object_name, column_name')]
                      if 'data_dictionary' in tables else [])
        with warehouse.db:
            for raw in src.execute('SELECT * FROM patients ORDER BY patient_id'):
                patient = dict(raw)
                validate_facts(patient)
                # Literal public columns; reported age stays tied to its profile date.
                patient['synthetic'] = True
                for k in PATIENT_COLUMNS:
                    if patient.get(k) not in (None, ''): demographics[k] += 1
                warehouse.db.execute('INSERT INTO patients VALUES (?,?,?)',
                    (release, patient['patient_id'], json.dumps(patient)))
                counts['patients'] += 1
            for raw in src.execute('SELECT * FROM source_records ORDER BY record_id'):
                r = project(raw)  # adapter/1 record contract, unchanged
                if not warehouse.db.execute('SELECT 1 FROM patients WHERE release=? AND id=?',
                        (release, r['patient_id'])).fetchone(): raise ValueError('Unknown source patient')
                warehouse.db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (release, r['record_id'], r['patient_id'], r['source_table'], r['event_date'], r['available_date'],
                     r['identity_status'], r.get('supersedes_record_id'), digest(r), json.dumps(r, allow_nan=False)))
                counts[r['source_table']] += 1
                clocks['event_date_unknown' if r['event_date'] is None else 'event_date_present'] += 1
                found = _code_fields({k: r.get(k) for k in r['_warehouse_fields']})
                if found:
                    coded[r['source_table']] += 1
                    for system in set(found.values()): code_systems[system] += 1
            warehouse.db.execute('CREATE TABLE IF NOT EXISTS source_entities(release TEXT, kind TEXT, id TEXT, payload TEXT, PRIMARY KEY(release,kind,id))')
            entity_columns = {}
            for table in ('organizations', 'clinicians'):
                for raw in src.execute('SELECT * FROM '+table):
                    obj = dict(raw); identifier = next((obj[k] for k in (table[:-1]+'_id', 'id') if k in obj), None)
                    if identifier is None: raise ValueError('Missing public entity identifier')
                    entity_columns[table] = sorted(obj)
                    warehouse.db.execute('INSERT INTO source_entities VALUES (?,?,?,?)',
                        (release, table, identifier, json.dumps(obj)))
            if checksum(source) != sha: raise ValueError('Source changed during ingestion')
            receipt = {'adapter': VERSION, 'record_projection': RECORD_PROJECTION, 'manifest_schema': manifest['schema'],
                'source': str(source), 'source_sha256': sha, 'code_sha256': code, 'as_of': manifest['as_of'],
                'counts': dict(counts), 'clocks': dict(clocks),
                'original_patient_id_digest': digest([r[0] for r in warehouse.db.execute(
                    'SELECT id FROM patients WHERE release=? ORDER BY id', (release,))]),
                'patient_columns_populated': dict(demographics), 'entity_columns': entity_columns,
                'records_with_standard_codes': dict(coded), 'records_by_code_system': dict(code_systems),
                'data_dictionary': {'columns': len(dictionary), 'sha256': digest(dictionary),
                                    'coded_columns': sorted({f"{d['object_name']}.{d['column_name']}={d['code_system']}"
                                                             for d in dictionary if d.get('code_system')})},
                'read_boundary': ['patients', 'source_records', 'organizations', 'clinicians'] + (['data_dictionary'] if dictionary else []),
                'private_files_read': False, 'synthetic': True}
            warehouse.db.execute('INSERT INTO releases VALUES (?,?)', (release, json.dumps(receipt)))
        return release, {'cache_hit': False, **receipt}
    finally: src.close()
