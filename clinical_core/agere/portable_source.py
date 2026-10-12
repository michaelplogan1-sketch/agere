"""Explicit, literal field mapping for bounded synthetic intake packages.

No native-warehouse impersonation, code inference, fuzzy identity or clock fallback.
Unmapped payloads remain in the original package, outside analytic evidence.
"""
from collections import Counter
from datetime import date
import json
import hashlib
from pathlib import Path
from ..contracts import digest
from .healthcare_warehouse import validate_facts

VERSION = 'portable-synthetic-adapter/1'
FIELDS = {
    'labs': {'test','code','value','unit','status','collection_date','specimen_date'},
    'financial': {'difficulty_paying_medical_bills','assessment_date'},
    'referrals': {'service','attendance_status','attendance_date'},
}
CLOCKS = {'event_date','recorded_date','available_date'}


def exact(obj, keys, label):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise ValueError('Exact '+label+' fields required')


def word(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ValueError('Nonempty bounded identifier required')
    return value


def day(value):
    if value is not None and (not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value):
        raise ValueError('Canonical date or explicit null required')
    return value


def project(package, mapping):
    exact(package, ('schema','source_id','synthetic','as_of','tables','crosswalk'), 'source package')
    exact(mapping, ('schema','version','roster','records','kinds'), 'mapping')
    if package['schema'] != 'portable-synthetic-source/1' or package['synthetic'] is not True:
        raise ValueError('Explicit synthetic source package required')
    if mapping['schema'] != 'portable-source-map/1': raise ValueError('Unsupported mapping schema')
    word(package['source_id']); word(mapping['version']); day(package['as_of'])
    if package['as_of'] is None: raise ValueError('Source cutoff required')
    validate_facts(package); validate_facts(mapping)
    # JSON round trip detaches caller-owned objects and rejects non-finite numbers.
    raw = json.dumps(package, allow_nan=False)
    if len(raw.encode()) > 2_000_000: raise ValueError('Source package exceeds 2 MB bound')
    package = json.loads(raw); mapping = json.loads(json.dumps(mapping, allow_nan=False))
    exact(mapping['roster'], ('table','id'), 'roster mapping')
    exact(mapping['records'], ('table','id','patient','kind','clocks','supersedes'), 'record mapping')
    spec = mapping['records']; exact(spec['clocks'], CLOCKS, 'source clocks')
    for value in [*mapping['roster'].values(), *(spec[k] for k in ('table','id','patient','kind'))]: word(value)
    for value in [*spec['clocks'].values(), spec['supersedes']]:
        if value is not None: word(value)
    if mapping['roster']['table'] == spec['table']: raise ValueError('Separate roster and record tables required')
    exact(package['tables'], (mapping['roster']['table'], spec['table']), 'declared tables')
    roster = package['tables'][mapping['roster']['table']]; raw_records = package['tables'][spec['table']]
    if not isinstance(roster, list) or not 1 <= len(roster) <= 200 or not isinstance(raw_records, list) or len(raw_records) > 2000:
        raise ValueError('Bounded complete roster (1–200) and records (0–2000) required')
    if not isinstance(mapping['kinds'], dict) or not isinstance(package['crosswalk'], list): raise ValueError('Explicit mappings required')
    for kind, m in mapping['kinds'].items():
        word(kind); exact(m, ('source_table','fields'), 'kind mapping')
        if m['source_table'] not in FIELDS or not isinstance(m['fields'], dict) or set(m['fields']) - FIELDS[m['source_table']]:
            raise ValueError('Unsupported analytic field mapping')
        for v in m['fields'].values(): word(v)
    crosswalk = {}; canonical = set()
    for row in package['crosswalk']:
        exact(row, ('source_patient_id','patient_id'), 'crosswalk')
        sid = word(row['source_patient_id']); pid = word(row['patient_id'])
        if sid in crosswalk or pid in canonical: raise ValueError('Explicit one-to-one identity mapping required')
        crosswalk[sid] = pid; canonical.add(pid)
    roster_ids = []
    for row in roster:
        if not isinstance(row, dict): raise ValueError('Roster row object required')
        roster_ids.append(word(row.get(mapping['roster']['id'])))
    if len(set(roster_ids)) != len(roster_ids) or set(crosswalk) != set(roster_ids):
        raise ValueError('Crosswalk must cover the exact unique roster')
    patients = [{'patient_id': crosswalk[sid], 'synthetic': True,
                 '_portable_source': {'source_id': package['source_id'], 'source_patient_id': sid,
                   'row_hash': digest(row), 'mapping_hash': digest(mapping)}} for sid, row in zip(roster_ids, roster)]
    issues = []; records = []; row_index = {}; inventory = Counter()
    def issue(pid, rid, reason, **extra):
        issues.append({'patient_id': pid, 'source_record_id': rid, 'reason': reason, **extra})
    for raw in raw_records:
        if not isinstance(raw, dict): raise ValueError('Record row object required')
        rid = word(raw.get(spec['id'])); sid = word(raw.get(spec['patient'])); kind = word(raw.get(spec['kind']))
        if rid in row_index: raise ValueError('Duplicate source record identity')
        if sid not in crosswalk: raise ValueError('Record identity outside explicit roster crosswalk')
        pid = crosswalk[sid]; row_index[rid] = raw; inventory[kind] += 1
        m = mapping['kinds'].get(kind)
        if m is None:
            issue(pid, rid, 'source_kind_unmapped', source_kind=kind); continue
        clocks = {k: day(raw.get(v)) if v else None for k, v in spec['clocks'].items()}
        if clocks['available_date'] is None:
            issue(pid, rid, 'receipt_date_unmapped_or_unobserved', source_kind=kind); continue
        values = {}
        for target, source in m['fields'].items():
            if source in raw:
                value = raw[source]
                if target.endswith('_date'): day(value)
                if target == 'value' and value is not None and (type(value) not in (int,float,str)):
                    raise ValueError('Literal numeric value or numeric text required; boolean is not a measurement')
                if target not in ('value','difficulty_paying_medical_bills') and value is not None and not isinstance(value,str):
                    raise ValueError('Literal text field required; no field coercion')
                if target == 'difficulty_paying_medical_bills' and value is not None and type(value) is not bool:
                    raise ValueError('Literal affordability boolean required; no coercion')
                values[target] = value
            else: issue(pid, rid, 'mapped_field_not_received', target_field=target, source_field=source)
        for k, source in spec['clocks'].items():
            if source is None: issue(pid, rid, 'source_clock_unmapped', target_field=k)
        previous = raw.get(spec['supersedes']) if spec['supersedes'] else None
        if previous is not None: word(previous)
        records.append({**values, **clocks, 'record_id': rid, 'patient_id': pid,
            'source_table': m['source_table'], 'source_system': package['source_id'],
            'identity_status': 'matched', 'identity_link_basis': 'supplied_exact_one_to_one_crosswalk',
            'synthetic': True, 'supersedes_record_id': previous,
            '_portable_source': {'adapter': VERSION, 'source_id': package['source_id'],
                'source_table': spec['table'], 'source_kind': kind, 'source_record_id': rid,
                'source_patient_id': sid, 'row_hash': digest(raw), 'mapping_hash': digest(mapping),
                'field_aliases': m['fields'], 'clock_aliases': spec['clocks'],
                'semantic_mapping_validated': False}})
    # Missing correction parents cannot silently create a new current account.
    by_id = {r['record_id']: r for r in records}
    for r in records:
        previous = r['supersedes_record_id']
        if previous and previous not in by_id:
            r['identity_status'] = 'quarantined_correction'
            issue(r['patient_id'], r['record_id'], 'correction_parent_not_mapped_or_received', parent=previous)
    return {'patients': patients, 'records': records, 'issues': issues,
            'source_kind_counts': dict(inventory), 'received_records': len(raw_records),
            'package_hash': digest(package), 'mapping_hash': digest(mapping)}


def ingest(warehouse, package, mapping):
    projected = project(package, mapping)
    code = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    release = digest([VERSION, code, projected['package_hash'], projected['mapping_hash']])
    existing = warehouse.db.execute('SELECT manifest FROM releases WHERE id=?', (release,)).fetchone()
    if existing: return release, json.loads(existing[0])
    receipt = {**{k:v for k,v in projected.items() if k not in ('patients','records')},
        'adapter': VERSION, 'code_sha256': code, 'source': package['source_id'], 'source_sha256': projected['package_hash'],
        'original_patient_id_digest': digest(sorted(p['patient_id'] for p in projected['patients'])),
        'synthetic': True, 'as_of': package['as_of'], 'roster_patients': len(projected['patients']),
        'mapped_records': len(projected['records']), 'clinical_mapping_validated': False}
    with warehouse.db:
        for p in projected['patients']:
            warehouse.db.execute('INSERT INTO patients VALUES (?,?,?)', (release,p['patient_id'],json.dumps(p)))
        for r in projected['records']:
            warehouse.db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',
                (release,r['record_id'],r['patient_id'],r['source_table'],r['event_date'],r['available_date'],
                 r['identity_status'],r['supersedes_record_id'],digest(r),json.dumps(r,allow_nan=False)))
        warehouse.db.execute('INSERT INTO releases VALUES (?,?)', (release,json.dumps(receipt)))
    return release, receipt
