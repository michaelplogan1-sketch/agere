"""Freeze reviewed source skills for training without leaking reserved examples.

This prepares data; it never trains or promotes weights. Family lineage and split
assignments must be supplied explicitly, not guessed from warehouse patient IDs.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json

from ...contracts import digest
from . import semantic_reader as reader

VERSION = 'reviewed-semantic-learning/1'
PARTITIONS = ('train', 'calibration', 'validation')
ROOT = Path(__file__).resolve().parents[3]


def protections():
    root = ROOT/'artifacts/agere-local-decisions-v1'
    plan = json.loads((root/'experiment-plan.json').read_text())
    sources = root/'reserved-sources.json'
    actual = hashlib.sha256(sources.read_bytes()).hexdigest()
    if actual != plan['files']['reserved-sources.json']: raise ValueError('Frozen reserved source register changed')
    return {'patient_ids': sorted(json.loads(sources.read_text())),
            'family_ids': sorted(set(plan['reserved_families']+plan['calibration_source_families'])),
            'register_sha256': digest([hashlib.sha256((root/'experiment-plan.json').read_bytes()).hexdigest(), actual]),
            'scope': 'Existing local-decision reserved and calibration patient families excluded from new fitting. Additional evaluation families must be supplied in the lineage policy.'}


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != {'lineage_source','patient_families','family_partitions','additional_protected_families'}:
        raise ValueError('Explicit family lineage, partition assignments and additional protections required')
    if not isinstance(policy['lineage_source'], str) or not policy['lineage_source'].strip():
        raise ValueError('Declared family-lineage source required')
    for key in ('patient_families','family_partitions'):
        if not isinstance(policy[key], dict) or any(not isinstance(k,str) or not k or not isinstance(v,str) or not v for k,v in policy[key].items()):
            raise ValueError('Invalid family mapping')
    if set(policy['family_partitions'].values())-set(PARTITIONS): raise ValueError('Unknown training partition')
    if not isinstance(policy['additional_protected_families'], list) or any(not isinstance(x,str) or not x for x in policy['additional_protected_families']):
        raise ValueError('Explicit additional protected families required')
    return policy


def prepare(export, policy, protected):
    policy = validate_policy(policy)
    if export.get('synthetic') is not True: raise ValueError('Synthetic-only learning export')
    protected_families = set(protected['family_ids']) | set(policy['additional_protected_families'])
    grouped = defaultdict(list)
    for r in export['rows']:
        # Four accepted readers of one occurrence are one training example.
        grouped[digest(r['target'])].append(r)
    rows, excluded = [], list(export['excluded'])
    for occurrence, items in sorted(grouped.items()):
        first = items[0]; patient = first['target']['patient_id']; family = policy['patient_families'].get(patient)
        labels = {digest(r['labels']) for r in items}
        reason = None
        if len(labels) != 1: reason = 'conflicting_labels_of_same_occurrence'
        elif patient in protected['patient_ids'] or family in protected_families: reason = 'protected_evaluation_family'
        elif not family: reason = 'missing_verified_patient_family_lineage'
        elif family not in policy['family_partitions']: reason = 'unassigned_family_partition'
        elif any(not r['context']['context_complete'] for r in items): reason = 'incomplete_source_context'
        elif any(set(r['labels']) != set(reader.AXES) or any(r['labels'][a] not in reader.AXES[a] for a in reader.AXES) for r in items):
            reason = 'invalid_task_labels'
        if reason:
            excluded.append({'occurrence_id': occurrence, 'reading_ids':[r['reading_id'] for r in items], 'reason':reason});continue
        if len({digest(r['context']) for r in items}) != 1 or len({r['source_hash'] for r in items}) != 1:
            raise ValueError('Same occurrence has inconsistent source contexts')
        context = first['context']
        if context['text'][context['target_start']:context['target_end']] != context['target_quote']:
            raise ValueError('Training input lost its exact target span')
        rows.append({'id': occurrence, 'input': context, 'labels': first['labels'],
            'partition': policy['family_partitions'][family], 'patient_family': family,
            'source_families': sorted({r['source_family'] for r in items}),
            'patient_id': patient, 'source_hash': first['source_hash'],
            'reviews': [{'reading_id':r['reading_id'],'review_revision':r['review']['revision'],
                         'review_digest':digest(r['review'])} for r in sorted(items,key=lambda r:r['reading_id'])],
            'supervision': 'Explicit local source review; reviewer identity is self-reported',
            'clinical_judgment_label': False})
    # A patient family, repeated template, identical source, or identical model
    # input may not bridge training/calibration/validation, including transitively.
    keys = defaultdict(list)
    for i,row in enumerate(rows):
        for key in [('patient',row['patient_family']),('source',row['source_hash']),('input',digest(row['input']))]+[('template',x) for x in row['source_families']]:
            keys[key].append(i)
    parent = list(range(len(rows)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]];i = parent[i]
        return i
    for values in keys.values():
        for i in values[1:]: parent[root(i)] = root(values[0])
    components = defaultdict(list)
    for i in range(len(rows)): components[root(i)].append(i)
    for values in components.values():
        if len({rows[i]['partition'] for i in values}) > 1:
            raise ValueError('Related patient/source/template component crosses learning partitions')
    by_input = defaultdict(set)
    for row in rows: by_input[digest(row['input'])].add(digest(row['labels']))
    if any(len(v)>1 for v in by_input.values()): raise ValueError('Identical model input has conflicting reviewed labels')
    counts = {part:sum(r['partition']==part for r in rows) for part in PARTITIONS}
    classes = {part:{axis:dict(Counter(r['labels'][axis] for r in rows if r['partition']==part)) for axis in reader.AXES} for part in PARTITIONS}
    missing = {axis:sorted(set(reader.AXES[axis])-set(classes['train'][axis])) for axis in reader.AXES}
    readiness = []
    if not rows: readiness.append('No eligible source-reviewed examples with assigned family lineage.')
    if any(not counts[p] for p in PARTITIONS): readiness.append('Training, calibration and validation partitions must each contain separate reviewed examples.')
    if any(len(classes['train'][a])<2 for a in reader.AXES): readiness.append('At least two observed training classes per axis are required to fit a classifier.')
    return {'schema':VERSION, 'synthetic':True, 'reader_identity':reader.identity(), 'rows':rows,
        'counts':counts, 'class_counts':classes, 'unrepresented_training_classes':missing,
        'excluded':excluded, 'review_export_digest':digest(export), 'policy_digest':digest(policy),
        'protection_register':protected, 'fit_input_contract_ready':not readiness,
        'unmet_data_requirements':readiness, 'training_performed':False, 'automatic_promotion':False,
        'limits':['Data preparation is not evidence of model accuracy or clinical knowledge.',
                  'A family map is supplied provenance, not inferred or independently authenticated lineage.',
                  'Exact normalized template checks cannot prove absence of semantic near-duplicates or prior model exposure.',
                  'Validation families are developer-managed; this is not a sealed external clinical evaluation.',
                  'Missing label classes and task-specific quality/cost thresholds need explicit handling before deployment.']}


def audit(path):
    exported = reader.learning_export(path)
    db = reader.db(path)
    try:
        count = db.execute("SELECT count(*) FROM readings WHERE json_extract(payload,'$.reader_identity')=?",(reader.identity(),)).fetchone()[0]
        review_count = db.execute('SELECT count(*) FROM reviews').fetchone()[0]
    finally: db.close()
    return {'schema':VERSION,'synthetic':True,'saved_current_reader_results':count,
        'recorded_review_revisions':review_count,'eligible_reviewed_readings':len(exported['rows']),
        'distinct_reviewed_occurrences':len({digest(r['target']) for r in exported['rows']}),
        'excluded':exported['excluded'], 'axes':reader.AXES,
        'requires':['Exact source review/correction labels','Verified source-patient family lineage',
                    'Separate train/calibration/validation families and held-out evaluation protection',
                    'Task-specific fitting, quality/cost comparison, calibration and versioned release'],
        'changing_knowledge':'Guidelines, programs, payer contracts and measures stay versioned outside model weights.',
        'training_performed':False,'model_promoted':False}


def freeze(path, output, policy):
    before = reader.learning_export(path)
    result = prepare(before, policy, protections())
    if digest(reader.learning_export(path)) != digest(before): raise ValueError('Reviews changed while preparing learning data')
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    rows = result.pop('rows')
    files = {}
    for part in PARTITIONS:
        file = output/(part+'.jsonl')
        file.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows if r['partition']==part))
        files[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
    result.update(files=files, policy=policy, code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def load_snapshot(path, dataset):
    """Verify frozen rows and current source reviews before any training stage."""
    dataset = Path(dataset); manifest = json.loads((dataset/'manifest.json').read_text())
    expected_files={p+'.jsonl' for p in PARTITIONS}
    if set(manifest['files']) != expected_files: raise ValueError('Exact training/calibration/validation files required')
    if manifest['code_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest(): raise ValueError('Learning preparation contract changed')
    for name,checksum in manifest['files'].items():
        if hashlib.sha256((dataset/name).read_bytes()).hexdigest()!=checksum: raise ValueError('Training file checksum mismatch')
    fresh = prepare(reader.learning_export(path),manifest['policy'],protections())
    if fresh['review_export_digest'] != manifest['review_export_digest'] or fresh['protection_register'] != manifest['protection_register']:
        raise ValueError('Source reviews or protected evaluation register changed; rebuild learning snapshot')
    if not fresh['fit_input_contract_ready']: raise ValueError('Reviewed training partitions are not ready for fitting')
    for part in PARTITIONS:
        stored=[json.loads(line) for line in (dataset/(part+'.jsonl')).read_text().splitlines() if line]
        if digest(stored)!=digest([r for r in fresh['rows'] if r['partition']==part]):
            raise ValueError('Frozen training rows differ from verified source reviews')
    return fresh, manifest


def load_fit(path, dataset):
    """Return train rows only; calibration/validation are never fitting inputs."""
    fresh,_=load_snapshot(path,dataset)
    return [r for r in fresh['rows'] if r['partition']=='train']
