"""Read-only ingestion adapter for client-realism warehouse releases (healthcare-warehouse/2, v2.3+).

v2.3 keeps the v2.2 population feeds and adds source-system artifacts (REALISM_DATA_ISSUES.md): outside documents
(`scanned_documents` rows pointing at PDFs under documents/outside/, about 45% image-only fax scans, some carrying
front-office OCR text), problem/medication lists with rot, HL7 lab quirks, claim versions, pharmacy reversals and ADT
gaps. This adapter is adapter/3 plus:

* Outside PDFs: for every `scanned_documents` record the referenced PDF (inside documents/, never private/) is
  checksummed against the release manifest and its text layer is read with the standard-library reader in
  outside_documents.py (no OCR). Results go to `source_document_text` (one row per record): status text_layer |
  image_only | missing | checksum_mismatch | unsupported, the text, the PDF's own 'Date of service' and how many records
  point at the same file. The record payload is unchanged (OCR text stays in its `ocr_text` field).
* Manifest count check: patients, source records and per-table counts claimed by the manifest (or by a sample
  manifest's `sample` block) are compared with what was read; disagreements are listed in the receipt.
* Evaluation tables: any public table named eval_* / *answer_key* is recorded as present and never read; private/
  is never opened.

Records are still projected with the unchanged adapter/1 projector, so record payloads, hashes and the record-level
`_warehouse_source.adapter` contract are identical to adapters 1-3. adapter/1-3 modules stay byte-identical, so v1.23,
v2.0 and v2.2 builds keep their release identity.
"""
from collections import Counter
from datetime import date
from pathlib import Path
import json
import re
import sqlite3
from ..contracts import digest
from . import healthcare_warehouse_v2 as v2, healthcare_warehouse_v3 as v3, outside_documents
from .healthcare_warehouse import checksum, project, validate_facts, VERSION as RECORD_PROJECTION

VERSION = 'original-cohort-warehouse-adapter/4'
SCHEMA = v3.SCHEMA
PANEL_SEGMENT = v3.PANEL_SEGMENT
STANDARD_FIELDS = {**v3.STANDARD_FIELDS, 'script_message_id': 'NCPDP SCRIPT message id',
                   'prescription_number': 'pharmacy Rx number', 'smoking_status_snomed': 'SNOMED CT'}
READ_BOUNDARY = v3.READ_BOUNDARY
DOCUMENT_STREAMS = ('scanned_documents',)
DOCUMENT_ROOT = 'documents'
_ENGINE_VERSION = re.compile(r'v(\d+)\.(\d+)')
DDL = """CREATE TABLE IF NOT EXISTS source_document_text(release TEXT, record_id TEXT, patient_id TEXT, document_id TEXT,
  relative_path TEXT, sha256 TEXT, manifest_sha256 TEXT, status TEXT, reason TEXT, text TEXT, text_chars INTEGER,
  pdf_date_of_service TEXT, records_sharing_file INTEGER, reader TEXT, PRIMARY KEY(release, record_id))"""


def client_realism(manifest):
    """An expanded v2 manifest from release v2.3 on (engine version >= 2.3, or outside PDFs listed in its files)."""
    if not v3.expanded(manifest): return False
    m = _ENGINE_VERSION.search(str(manifest.get('engine') or ''))
    if m and (int(m.group(1)), int(m.group(2))) >= (2, 3): return True
    return any(str(k).startswith(DOCUMENT_ROOT + '/outside/') for k in (manifest.get('files') or {}))


def adapter_for(manifest):
    """v1 -> adapter/1, panel-only v2 -> adapter/2, expanded v2.2 -> adapter/3, client-realism v2.3+ -> adapter/4."""
    base = v3.adapter_for(manifest)
    return VERSION if base == v3.VERSION and client_realism(manifest) else base


def _code_fields(facts):
    found = {}
    for key, system in STANDARD_FIELDS.items():
        if facts.get(key) not in (None, '', []): found[key] = system
    if facts.get('service_code_system') in ('CPT', 'HCPCS') and facts.get('service_code'): found['service_code'] = facts['service_code_system']
    return found


def evaluation_table(name):
    n = name.lower()
    return n.startswith('eval_') or 'answer_key' in n


def _document_path(root, relative):
    """Resolved PDF path when it stays inside the release's documents/ folder (never private/), else None."""
    if not isinstance(relative, str) or not relative.startswith(DOCUMENT_ROOT + '/'): return None
    path = (root / relative).resolve()
    if root / DOCUMENT_ROOT not in path.parents or (root / 'private') in path.parents: return None
    return path


def read_documents(root, manifest, docs):
    """source_document_text rows (without the release column) for [(record_id, patient_id, facts)] of scanned_documents."""
    files = manifest.get('files') or {}
    sharing = Counter(f.get('relative_path') for _, _, f in docs)
    cache, rows = {}, []
    for rid, pid, f in docs:
        rel = f.get('relative_path')
        if rel not in cache:
            path = _document_path(root, rel)
            if path is None: cache[rel] = (None, files.get(rel), 'missing', 'path_outside_documents', None)
            elif not path.is_file(): cache[rel] = (None, files.get(rel), 'missing', 'file_not_in_release', None)
            else:
                sha = checksum(path)
                if files.get(rel) not in (None, sha): cache[rel] = (sha, files.get(rel), 'checksum_mismatch', 'differs_from_manifest', None)
                else:
                    got = outside_documents.read(path.read_bytes())
                    status = 'text_layer' if got['text'] else ('image_only' if got['image_only'] else 'unsupported')
                    cache[rel] = (sha, files.get(rel), status, got['reason'], got['text'])
        sha, listed, status, reason, text = cache[rel]
        rows.append((rid, pid, f.get('document_id'), rel, sha, listed, status, reason, text, len(text) if text else 0,
                     outside_documents.date_of_service(text), sharing[rel], outside_documents.VERSION))
    return rows


def manifest_count_check(manifest, counts):
    """Disagreements between the counts a manifest claims and the counts read ({} when consistent or not claimed)."""
    sample = manifest.get('sample')
    if sample:
        claimed = {'patients': sample.get('patients'), 'source_records': sample.get('records'), 'source_tables': sample.get('source_tables')}
    else:
        m = manifest.get('metrics') or {}
        claimed = {'patients': m.get('patients'), 'source_records': m.get('source_records'), 'source_tables': m.get('source_tables')}
    tables = {k: n for k, n in counts.items() if k != 'patients'}
    read = {'patients': counts.get('patients', 0), 'source_records': sum(tables.values()), 'source_tables': tables}
    out = {}
    for key in ('patients', 'source_records'):
        if claimed[key] is not None and claimed[key] != read[key]: out[key] = {'manifest': claimed[key], 'read': read[key]}
    if isinstance(claimed['source_tables'], dict):
        diff = {t: {'manifest': claimed['source_tables'].get(t), 'read': tables.get(t)}
                for t in set(claimed['source_tables']) | set(tables)
                if (claimed['source_tables'].get(t) or 0) != (tables.get(t) or 0)}
        if diff: out['source_tables'] = diff
    return out


def ingest(warehouse, root):
    root = Path(root).resolve()
    manifest = json.loads((root/'manifest.json').read_text())
    if manifest.get('schema') != SCHEMA: raise ValueError('Expanded healthcare warehouse manifest (healthcare-warehouse/2) required')
    date.fromisoformat(manifest['as_of'])
    source = root/'warehouse.sqlite'
    if source.resolve().parent != root: raise ValueError('Source must be inside the pinned release')
    sha = checksum(source)
    if manifest['files'].get('warehouse.sqlite') != sha: raise ValueError('Warehouse checksum differs from release manifest')
    here = Path(__file__)
    code = digest([checksum(__file__), checksum(here.with_name('healthcare_warehouse.py')), checksum(here.with_name('outside_documents.py'))])
    release = digest([VERSION, sha, code, manifest['as_of']])
    if warehouse.db.execute('SELECT 1 FROM releases WHERE id=?', (release,)).fetchone():
        return release, {'cache_hit': True, 'adapter': VERSION, 'source': str(source), 'sha256': sha}
    src = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    counts = Counter(); clocks = Counter(); coded = Counter(); code_systems = Counter()
    demographics = Counter(); segments = Counter(); with_records = set()
    roster, panel, docs = set(), [], []
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
                segment = patient.get('data_segment') or PANEL_SEGMENT
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
                if r['source_table'] in DOCUMENT_STREAMS: docs.append((r['record_id'], r['patient_id'], r))
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
            warehouse.db.execute(DDL)
            doc_rows = read_documents(root, manifest, docs)
            warehouse.db.executemany('INSERT INTO source_document_text VALUES (' + ','.join('?' * 14) + ')', [(release, *r) for r in doc_rows])
            if checksum(source) != sha: raise ValueError('Source changed during ingestion')
            metrics = manifest.get('metrics') or {}
            claims = {'new_patients_added': metrics.get('new_patients_added'),
                      'original_patient_id_set_preserved': metrics.get('original_patient_id_set_preserved')}
            added = sum(n for s, n in segments.items() if s != PANEL_SEGMENT)
            doc_status = Counter(r[6] for r in doc_rows)
            mismatch = manifest_count_check(manifest, counts)
            receipt = {'adapter': VERSION, 'record_projection': RECORD_PROJECTION, 'manifest_schema': manifest['schema'],
                'source': str(source), 'source_sha256': sha, 'code_sha256': code, 'as_of': manifest['as_of'],
                'counts': dict(counts), 'clocks': dict(clocks),
                'original_patient_id_digest': digest(sorted(roster)),
                'panel_patient_id_digest': digest(sorted(panel)), 'segments': dict(segments),
                'patients_without_received_records': len(roster - with_records),
                'manifest_roster_claims': claims,
                'manifest_roster_claim_consistent': not (claims['new_patients_added'] in (0, None) and added > 0),
                'manifest_count_mismatches': mismatch, 'manifest_counts_consistent': not mismatch,
                'outside_documents': {'records': len(doc_rows), 'files': len({r[3] for r in doc_rows}),
                                      'status': dict(doc_status), 'records_sharing_a_file': sum(1 for r in doc_rows if r[11] > 1),
                                      'manifest_claimed': metrics.get('outside_documents'), 'reader': outside_documents.VERSION},
                'patient_columns_populated': dict(demographics), 'entity_columns': entity_columns,
                'organization_types': dict(entity_types),
                'records_with_standard_codes': dict(coded), 'records_by_code_system': dict(code_systems),
                'data_dictionary': {'columns': len(dictionary), 'sha256': digest(dictionary),
                                    'coded_columns': sorted({f"{d['object_name']}.{d['column_name']}={d['code_system']}"
                                                             for d in dictionary if d.get('code_system')})},
                'read_boundary': [t for t in READ_BOUNDARY if t != 'data_dictionary' or dictionary] +
                                 ([DOCUMENT_ROOT + '/ (PDFs named by scanned_documents records)'] if doc_rows else []),
                'evaluation_tables_present_not_read': sorted(t for t in tables if evaluation_table(t)),
                'private_files_read': False, 'synthetic': True}
            warehouse.db.execute('INSERT INTO releases VALUES (?,?)', (release, json.dumps(receipt)))
        return release, {'cache_hit': False, **receipt}
    finally: src.close()
