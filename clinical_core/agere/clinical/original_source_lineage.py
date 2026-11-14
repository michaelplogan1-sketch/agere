"""Training acquisition partitions from public original-patient identity only.

This is a development partition, not new blind clinical validation. It does not
infer household independence or inspect private generator labels/clinical truth.
"""
from collections import Counter
import hashlib,json,sqlite3
from pathlib import Path
from ...contracts import digest
from ..healthcare_warehouse import checksum,VERSION as ADAPTER
from . import semantic_learning as learning

VERSION='original-source-acquisition-partitions/1'


def partition(patient):
    bucket=int(digest([VERSION,patient])[:16],16)%10000
    return 'train' if bucket<7000 else 'calibration' if bucket<8500 else 'validation'


def prepare(stack,release,output):
    stack,release,output=Path(stack),Path(release).resolve(),Path(output)
    if output.exists():raise ValueError('Fresh original-source policy output required')
    meta=json.loads((stack/'run.json').read_text());manifest=json.loads((release/'manifest.json').read_text())
    source=release/'warehouse.sqlite';dataset=meta['snapshot'].get('source_dataset',{})
    schema = manifest.get('schema')
    original = (schema == 'single-healthcare-warehouse/1' and dataset.get('adapter') == ADAPTER and
                manifest.get('population') == 'same_original_patient_roster' and
                manifest.get('metrics', {}).get('original_patient_id_set_preserved') is True)
    expanded = (schema == 'healthcare-warehouse/2' and dataset.get('adapter') in
                ('original-cohort-warehouse-adapter/2', 'original-cohort-warehouse-adapter/3',
                 'original-cohort-warehouse-adapter/4'))
    if meta.get('synthetic') is not True or not (original or expanded):
        raise ValueError('Explicit supported received-patient source required')
    sha=checksum(source)
    if (sha!=manifest['files'].get('warehouse.sqlite') or sha!=dataset.get('source_sha256') or
        source.resolve()!=Path(dataset['source']).resolve()):raise ValueError('Original-cohort source hash/location mismatch')
    src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    db=sqlite3.connect((stack/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        ids=[r[0] for r in src.execute('SELECT patient_id FROM patients ORDER BY patient_id')]
        actual=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')]
    finally:src.close();db.close()
    if ids!=actual or len(ids)!=len(set(ids)) or digest(ids)!=dataset['original_patient_id_digest']:
        raise ValueError('Original-source identity does not match the exact warehouse roster')
    protected=learning.protections()
    if set(protected['family_ids'])-set(ids):raise ValueError('Frozen source families cannot be resolved against this original roster')
    policy={'lineage_source':str(source)+'#patients.patient_id (preserved original source person)',
            'patient_families':{pid:pid for pid in ids},
            'family_partitions':{pid:partition(pid) for pid in ids},'additional_protected_families':[]}
    learning.validate_policy(policy)
    held=set(protected['family_ids'])|set(protected['patient_ids'])
    output.mkdir(parents=True,exist_ok=False)
    text=json.dumps(policy,indent=2,allow_nan=False)+'\n';(output/'policy.json').write_text(text)
    receipt={'schema':VERSION,'synthetic':True,'warehouse_release':meta['snapshot']['release'],
        'warehouse_manifest_schema':schema,'warehouse_adapter':dataset['adapter'],
        'generator_manifest_sha256':checksum(release/'manifest.json'),'ancestry_source_sha256':sha,
        'policy_sha256':checksum(output/'policy.json'),'patients':len(ids),'families':len(ids),
        'extracted_fields':['patients.patient_id'],'private_files_read':False,'hidden_clinical_labels_exported':False,
        'protection_register_sha256':protected['register_sha256'],'frozen_original_families_excluded':len(set(ids)&held),
        'partition_patient_counts':dict(Counter(policy['family_partitions'].values())),
        'unprotected_partition_patient_counts':dict(Counter(policy['family_partitions'][pid] for pid in ids if pid not in held)),
        'partition_rule':'Stable SHA-256 bucket per original patient: <7000 train, <8500 calibration, otherwise validation; modulo 10000. Frozen protections override fitting eligibility.',
        'purpose':'Source-review acquisition and future development fitting; no labels or weights created.',
        'code_sha256':checksum(__file__),
        'limits':['Original source-person identity is the grouping unit; household and wording dependence are not established.',
                  'Existing models may have seen these original sources; these are not new blind evaluation families.',
                  'Existing cross-partition template/source checks still apply before any fit; model agreement is not a human label.']}
    (output/'manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt
