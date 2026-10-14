"""Read-only ingestion adapter for expanded-population warehouse releases (healthcare-warehouse/2, v2.2+).

Release v2.2 keeps the 1,614-patient deep-chart panel and adds a broader client population
(claims-plus-EHR and claims-only members with payer enrollment spans, payer medical/pharmacy
claims, ADT, HIE documents, connected-practice notes and e-prescriptions, a purchased SDOH file)
plus MPI duplicate charts. Its manifest still reports `new_patients_added: 0` (a panel-level
metric), so this adapter does not trust the roster claim; it measures the roster by segment.

* Records are projected with the unchanged adapter/1 projector, so record payloads, hashes and
  the record-level `_warehouse_source.adapter` contract are identical to adapter/1 and adapter/2.
* Patients keep every public column literally, including `data_segment`
  (deep_chart | claims_plus_ehr | claims_only | duplicate_chart).
* The receipt records the full-roster digest (`original_patient_id_digest`, the build roster the
  activation contract checks), the deep-chart panel digest, segment counts, patients without any
  received record, the manifest's roster claims and whether they agree with the data.
* Only patients, source_records, organizations, clinicians and data_dictionary are read. Evaluation
  tables shipped in the public file (`eval_mpi_answer_key`, `mpi_duplicate_answer_key`) and
  everything under private/ are never opened; their presence is recorded.

adapter/1 and adapter/2 stay byte-identical, so v1.23 and v2.0 builds keep their release identity.
"""
from collections import Counter
from datetime import date
from pathlib import Path
import json
import sqlite3
from ..contracts import digest
from . import healthcare_warehouse_v2 as v2
from .healthcare_warehouse import checksum, project, validate_facts, VERSION as RECORD_PROJECTION

VERSION = 'original-cohort-warehouse-adapter/3'
SCHEMA = 'healthcare-warehouse/2'
PANEL_SEGMENT = 'deep_chart'
# Literal standard-code fields added by the population feeds (beside adapter/2's fields).
STANDARD_FIELDS = {**v2.STANDARD_FIELDS, 'procedure_code': 'CPT/HCPCS', 'loinc_document_code': 'LOINC', 'panel_loinc': 'LOINC',
                   'pharmacy_npi': 'NPI', 'prescriber_npi': 'NPI', 'attribution_npi': 'NPI', 'county_fips': 'FIPS'}
# Public tables never read: evaluation answer keys shipped inside the warehouse file.
EVALUATION_TABLES = ('eval_mpi_answer_key', 'mpi_duplicate_answer_key')
READ_BOUNDARY = ('patients', 'source_records', 'organizations', 'clinicians', 'data_dictionary')


def expanded(manifest):
    """A v2 manifest that declares broader-population feeds (v2.1+ population process)."""
    metrics = manifest.get('metrics') or {}
    return manifest.get('schema') == SCHEMA and (metrics.get('population_v21') is not None or
                                                 'population' in str(manifest.get('engine') or ''))


def adapter_for(manifest):
    """v1 schemas keep adapter/1, panel-only v2 releases keep adapter/2, expanded v2 releases use adapter/3."""
    base = v2.adapter_for(manifest)
    return VERSION if base == v2.VERSION and expanded(manifest) else base


def _code_fields(facts):
    found = {}
    for key, system in STANDARD_FIELDS.items():
        if facts.get(key) not in (None, '', []): found[key] = system
    if facts.get('service_code_system') in ('CPT', 'HCPCS') and facts.get('service_code'): found['service_code'] = facts['service_code_system']
    return found


def ingest(warehouse, root):
    root = Path(root).resolve()
    manifest = json.loads((root/'manifest.json').read_text())
    if manifest.get('schema') != SCHEMA: raise ValueError('Expanded healthcare warehouse manifest (healthcare-warehouse/2) required')
    date.fromisoformat(manifest['as_of'])
    source = root/'warehouse.sqlite'
    if source.resolve().parent != root: raise ValueError('Source must be inside the pinned release')
    sha = checksum(source)
    if manifest['files'].get('warehouse.sqlite') != sha: raise ValueError('Warehouse checksum differs from release manifest')
    code = digest([checksum(__file__), checksum(Path(__file__).with_name('healthcare_warehouse.py'))])
    release = digest([VERSION, sha, code, manifest['as_of']])
    if warehouse.db.execute('SELECT 1 FROM releases WHERE id=?', (release,)).fetchone():
        return release, {'cache_hit': True, 'adapter': VERSION, 'source': str(source), 'sha256': sha}
    src = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    counts = Counter(); clocks = Counter(); coded = Counter(); code_systems = Counter()
    demographics = Counter(); segments = Counter(); with_records = set()
    roster, panel = set(), []
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        dictionary = ([dict(r) for r in src.execute('SELECT * FROM data_dictionary ORDER BY object_name, column_name')]
                      if 'data_dictionary' in tables else [])
        with warehouse.db:
            for raw in src.execute('SELECT * FROM patients ORDER BY patient_id'):
                patient = dict(raw)
                validate_facts(patient)
                patient['synthetic'] = True
                for k in v2.PATIENT_COLUMNS:
                    if patient.get(k) not in (None, ''): demographics[k] += 1
                segment = patient.get('data_segment') or PANEL_SEGMENT  # releases without segments are panel-only
                segments[segment] += 1
                if segment == PANEL_SEGMENT: panel.append(patient['patient_id'])
                roster.add(patient['patient_id'])
                warehouse.db.execute('INSERT INTO patients VALUES (?,?,?)', (release, patient['patient_id'], json.dumps(patient)))
                counts['patients'] += 1
            for raw in src.execute('SELECT * FROM source_records ORDER BY record_id'):
                r = project(raw)  # adapter/1 record contract, unchanged
                if r['patient_id'] not in roster: raise ValueError('Unknown source patient')
                warehouse.db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (release, r['record_id'], r['patient_id'], r['source_table'], r['event_date'], r['available_date'],
                     r['identity_status'], r.get('supersedes_record_id'), digest(r), json.dumps(r, allow_nan=False)))
                counts[r['source_table']] += 1; with_records.add(r['patient_id'])
                clocks['event_date_unknown' if r['event_date'] is None else 'event_date_present'] += 1
                found = _code_fields({k: r.get(k) for k in r['_warehouse_fields']})
                if found:
                    coded[r['source_table']] += 1
                    for system in set(found.values()): code_systems[system] += 1
            warehouse.db.execute('CREATE TABLE IF NOT EXISTS source_entities(release TEXT, kind TEXT, id TEXT, payload TEXT, PRIMARY KEY(release,kind,id))')
            entity_columns = {}; entity_types = Counter()
            for table in ('organizations', 'clinicians'):
                for raw in src.execute('SELECT * FROM '+table):
                    obj = dict(raw); identifier = next((obj[k] for k in (table[:-1]+'_id', 'id') if k in obj), None)
                    if identifier is None: raise ValueError('Missing public entity identifier')
                    entity_columns[table] = sorted(obj)
                    if table == 'organizations': entity_types[obj.get('organization_type') or 'unknown'] += 1
                    warehouse.db.execute('INSERT INTO source_entities VALUES (?,?,?,?)', (release, table, identifier, json.dumps(obj)))
            if checksum(source) != sha: raise ValueError('Source changed during ingestion')
            metrics = manifest.get('metrics') or {}
            claims = {'new_patients_added': metrics.get('new_patients_added'),
                      'original_patient_id_set_preserved': metrics.get('original_patient_id_set_preserved')}
            added = sum(n for s, n in segments.items() if s != PANEL_SEGMENT)
            receipt = {'adapter': VERSION, 'record_projection': RECORD_PROJECTION, 'manifest_schema': manifest['schema'],
                'source': str(source), 'source_sha256': sha, 'code_sha256': code, 'as_of': manifest['as_of'],
                'counts': dict(counts), 'clocks': dict(clocks),
                'original_patient_id_digest': digest(sorted(roster)),
                'panel_patient_id_digest': digest(sorted(panel)), 'segments': dict(segments),
                'patients_without_received_records': len(roster - with_records),
                'manifest_roster_claims': claims,
                # The roster is measured, not taken from the manifest: a release that adds members while claiming
                # new_patients_added == 0 is recorded as inconsistent (it is ingested; activation needs --expansion).
                'manifest_roster_claim_consistent': not (claims['new_patients_added'] in (0, None) and added > 0),
                'patient_columns_populated': dict(demographics), 'entity_columns': entity_columns,
                'organization_types': dict(entity_types),
                'records_with_standard_codes': dict(coded), 'records_by_code_system': dict(code_systems),
                'data_dictionary': {'columns': len(dictionary), 'sha256': digest(dictionary),
                                    'coded_columns': sorted({f"{d['object_name']}.{d['column_name']}={d['code_system']}"
                                                             for d in dictionary if d.get('code_system')})},
                'read_boundary': [t for t in READ_BOUNDARY if t != 'data_dictionary' or dictionary],
                'evaluation_tables_present_not_read': sorted(t for t in EVALUATION_TABLES if t in tables),
                'private_files_read': False, 'synthetic': True}
            warehouse.db.execute('INSERT INTO releases VALUES (?,?)', (release, json.dumps(receipt)))
        return release, {'cache_hit': False, **receipt}
    finally: src.close()
