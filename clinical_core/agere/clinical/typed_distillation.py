"""Source-bound development fitting for the existing typed System One runtime.

One option-meaning contract per candidate. Received source and exclusion checks
precede loading observation text/labels; related inputs stay in one partition.
Machine supervision never becomes clinical review or an automatic release.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from ...contracts import digest
from ..narrative_sources import text_fields
from . import learning_queue as queue, semantic_learning, system_one_student as student

VERSION = 'typed-source-distillation/1'
SALT = 'agere-typed-source-development-v1'
PARTITIONS = ('train', 'calibration', 'validation')
LIMITS = [
    'Labels are saved machine judgments, not independently reviewed clinical references.',
    'Development subdivisions use only eligible outer training people. Frozen and outer evaluation people are excluded.',
    'Patient identity and normalized whole-document wording are grouping proxies; semantic and clinical independence are not established.',
    'Observation capture selected confident non-unknown responses. This is a selected development sample, not population performance.',
    'The candidate remains reading-only. Qualification, clinical authority and default activation are not created by this workflow.',
]


def option_contract(options):
    return hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()


@contextmanager
def _read_db(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try: yield db
    finally: db.close()


def partition(rows):
    """Transitive patient/source/template/state components, assigned without labels."""
    parent = list(range(len(rows))); seen = {}
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, row in enumerate(rows):
        for key in [('patient', row['patient_family']), ('source', row['source_hash']),
                    ('template', row['template']), ('input', row['decision_key'])]:
            if key in seen: parent[root(i)] = root(seen[key])
            else: seen[key] = i
    groups = defaultdict(list)
    for i in range(len(rows)): groups[root(i)].append(i)
    out = []
    for indices in groups.values():
        component = digest(sorted(rows[i]['decision_key'] for i in indices))
        bucket = int(digest([SALT, component])[:8], 16) % 10000
        part = 'train' if bucket < 7000 else 'calibration' if bucket < 8500 else 'validation'
        out.extend({**rows[i], 'component': component, 'partition': part} for i in indices)
    return sorted(out, key=lambda r: r['decision_key'])


def _bind(row, source):
    """Require the exact complete document input used by judge_documents()."""
    from .backstop_agent import JUDGE_CHUNK
    evidence = json.loads(row['evidence'])
    cutoff = row['as_of']
    if not cutoff: raise ValueError('missing_cutoff')
    if any(str(source[k] or '')[:10] > cutoff for k in ('event_date', 'available_date')):
        raise ValueError('source_not_visible_at_cutoff')
    record = {**json.loads(source['facts']), 'source_table': source['source_table']}
    for field in text_fields(record):
        text = field['text']
        chunks = [text[i:i + JUDGE_CHUNK] for i in range(0, max(len(text), 1), JUDGE_CHUNK - 200)] or ['']
        expected = [{'id': f"{source['record_id']}#{i}", 'record_id': source['record_id'],
                     'text': chunk, 'date': str(source['event_date'] or '')[:10]} for i, chunk in enumerate(chunks)]
        state = student.decision_state(row['question'], json.loads(row['options']), evidence, as_of=cutoff)
        if state == student.decision_state(row['question'], json.loads(row['options']), expected, as_of=cutoff):
            return state, field['field'], text
    raise ValueError('observation_not_complete_current_received_text')


def prepare(config, lineage, output, *, options_sha256):
    """Freeze one exact document task from saved judgments; no new provider calls."""
    config = json.loads(Path(config).read_text()); output = Path(output)
    if output.exists(): raise ValueError('Fresh dataset directory required; preserve prior attempts')
    if config.get('synthetic') is not True: raise ValueError('Synthetic development sources required')
    stack = Path(config['build'])/'stack'
    meta = json.loads((stack/'run.json').read_text())
    if meta['snapshot']['release'] != config['snapshot']['release']:
        raise ValueError('Runtime and configured source release differ')
    policy, lineage_manifest = queue.lineage(lineage, meta)
    environment = config['runtime']['environment']
    protected = semantic_learning.protections(
        plan_path=environment.get('AGERE_LEARNING_PROTECTION_PLAN'),
        plan_sha256=environment.get('AGERE_LEARNING_PROTECTION_PLAN_SHA256'))
    if lineage_manifest.get('protection_register_sha256') != protected['register_sha256']:
        raise ValueError('Dataset partitions use a different protection registry')
    source_path = Path(config['source_release'])/'warehouse.sqlite'
    source_hash = queue.sha(source_path)
    if source_hash != config['snapshot']['source_dataset']['source_sha256']:
        raise ValueError('Warehouse changed since configured snapshot')
    contract = student.input_contract(); selected = []; excluded = []; groups = {}
    with _read_db(environment['AGERE_SYSTEM_ONE_DISTILL']) as observations, _read_db(source_path) as warehouse:
        # Only option metadata and source IDs are read before exclusion checks.
        candidates = observations.execute('SELECT id,options FROM labels WHERE task_key=? AND input_contract=? ORDER BY id',
            ('agere_chart_judgment', contract)).fetchall()
        for header in candidates:
            if option_contract(json.loads(header['options'])) != options_sha256: continue
            ident = header['id']; selected.append(ident)
            ids = {str(r[0] or '').rsplit('#', 1)[0] for r in observations.execute(
                "SELECT json_extract(value,'$.id') FROM labels,json_each(labels.evidence) WHERE labels.id=?", (ident,))}
            if len(ids) != 1:
                excluded.append({'observation_id': ident, 'reason': 'single_document_binding_required'}); continue
            rid = next(iter(ids))
            person = warehouse.execute('SELECT patient_id FROM source_records WHERE record_id=?', (rid,)).fetchone()
            reason = 'source_identifier_unresolved' if person is None else queue.eligibility(person[0], policy, protected)
            if reason:
                excluded.append({'observation_id': ident, 'reason': reason}); continue
            row = dict(observations.execute('SELECT * FROM labels WHERE id=?', (ident,)).fetchone())
            source = dict(warehouse.execute('SELECT * FROM source_records WHERE record_id=?', (rid,)).fetchone())
            if source.get('synthetic') != 1:
                excluded.append({'observation_id': ident, 'reason': 'non_synthetic_source'}); continue
            try: state, field, text = _bind(row, source)
            except ValueError as exc:
                excluded.append({'observation_id': ident, 'reason': str(exc)}); continue
            key = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            item = groups.setdefault(key, {'decision_key': key, 'state': state,
                'patient_family': policy['patient_families'][source['patient_id']], 'patient_id': source['patient_id'],
                'record_id': rid, 'source_field': field, 'source_hash': digest(source),
                'template': digest(queue.normalize(text)), 'observations': [], 'reference': None})
            item['observations'].append({k: row[k] for k in ('id', 'label', 'probability', 'provider', 'teacher_model', 'request_hash', 'recorded_at')})
    # No label-dependent movement or search for a more favorable split.
    rows = partition(list(groups.values()))
    for row in rows:
        eligible = [r for r in row['observations'] if r['probability'] is not None and
                    float(r['probability']) >= student.TEACHER_MIN and r['label'] in row['state']['options']]
        labels = {r['label'] for r in eligible}
        row['label'] = next(iter(labels)) if len(labels) == 1 else None
        row['fit_status'] = 'machine_supervision' if len(labels) == 1 else 'conflicting_machine_labels' if labels else 'no_eligible_machine_label'
        row['weight'] = sum(float(r['probability']) for r in eligible)/len(eligible) if eligible else 0
    if not rows: raise ValueError('No complete eligible received-source decisions for this option contract')
    output.mkdir(parents=True)
    queue.write(output/'targets.json', rows); queue.write(output/'exclusions.json', excluded)
    manifest = {'schema': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
        'task_key': 'agere_chart_judgment', 'input_contract': contract, 'options_sha256': options_sha256,
        'source_release': config['snapshot']['release'], 'source_sha256': source_hash,
        'source_path': str(source_path.resolve()), 'lineage_policy_sha256': queue.sha(Path(lineage)/'policy.json'),
        'protection_register_sha256': protected['register_sha256'], 'code_sha256': queue.sha(__file__),
        'partition_salt': SALT, 'selected_observations': len(selected), 'retained_observations': sum(len(r['observations']) for r in rows),
        'excluded_observations': len(excluded), 'exclusion_reasons': dict(Counter(r['reason'] for r in excluded)),
        'unique_decisions': len(rows), 'components': len({r['component'] for r in rows}),
        'partitions': {p: {'decisions': sum(r['partition'] == p for r in rows),
            'components': len({r['component'] for r in rows if r['partition'] == p}),
            'label_counts': dict(Counter(r['label'] for r in rows if r['partition'] == p and r['label'] is not None))} for p in PARTITIONS},
        'files': {name: queue.sha(output/name) for name in ('targets.json', 'exclusions.json')},
        'provider_calls': 0, 'independent_reviewed_references': 0, 'automatic_acceptance': False, 'limits': LIMITS}
    assert manifest['selected_observations'] == manifest['retained_observations'] + manifest['excluded_observations']
    queue.write(output/'manifest.json', manifest)
    return manifest


def load(dataset):
    dataset = Path(dataset); manifest = json.loads((dataset/'manifest.json').read_text())
    if manifest['schema'] != VERSION or manifest['input_contract'] != student.input_contract():
        raise ValueError('Dataset no longer matches typed runtime')
    if any(queue.sha(dataset/name) != value for name, value in manifest['files'].items()):
        raise ValueError('Frozen dataset changed')
    rows = json.loads((dataset/'targets.json').read_text()); seen = {}
    for row in rows:
        if option_contract(row['state']['options']) != manifest['options_sha256']:
            raise ValueError('Mixed option-meaning contracts')
        if row['partition'] not in PARTITIONS: raise ValueError('Unknown development partition')
        for key in [('patient', row['patient_family']), ('source', row['source_hash']), ('template', row['template']), ('input', row['decision_key'])]:
            if key in seen and seen[key] != row['partition']: raise ValueError('Related inputs cross development partitions')
            seen[key] = row['partition']
    return manifest, rows


def fit(dataset, output):
    """Fit once on the declared training partition; export reading-only weights.

    Calibration selects a descriptive teacher-agreement threshold. Validation
    never tunes it, and neither split can authorize clinical/default activation.
    """
    import joblib
    started = time.perf_counter(); output = Path(output)
    if output.exists(): raise ValueError('Fresh candidate directory required')
    manifest, rows = load(dataset)
    eligible = [r for r in rows if r['label'] is not None]
    training = [r for r in eligible if r['partition'] == 'train']
    examples = [student.example(r['state']['subject'], r['state']['options'], r['state']['evidence'], r['label'],
        as_of=r['state']['as_of'], weight=r['weight'], source='saved_machine_supervision') for r in training]
    model = student.train_rows(manifest['task_key'], examples)
    if model is None: raise ValueError('Declared training partition has insufficient distinct decisions or classes')
    majority = Counter(r['label'] for r in training).most_common(1)[0][0]
    predictions = []
    for row in eligible:
        if row['partition'] == 'train': continue
        s = row['state']; probabilities = student.probabilities(model, s['subject'], s['options'], s['evidence'], as_of=s['as_of'])
        predicted = max(probabilities, key=probabilities.get)
        predictions.append({'decision_key': row['decision_key'], 'component': row['component'], 'partition': row['partition'],
            'machine_reference': row['label'], 'predicted': predicted, 'confidence': probabilities[predicted],
            'agrees': predicted == row['label'], 'majority_agrees': majority == row['label']})
    threshold, accepted, agreement = student.threshold_from([(r['confidence'], r['agrees']) for r in predictions if r['partition'] == 'calibration'])
    options = set(model['options'])
    report = {'schema': VERSION, 'task_key': manifest['task_key'], 'input_contract': manifest['input_contract'],
        'dataset_manifest_sha256': queue.sha(Path(dataset)/'manifest.json'), 'training_decisions': len(training),
        'training_classes': model['classes'], 'unseen_training_options': sorted(options-set(model['classes'])),
        'baseline': {'method': 'training-majority constant', 'label': majority},
        'comparison': {}, 'calibration_candidate': {'threshold': threshold, 'accepted': accepted, 'agreement': agreement},
        'decides': False, 'automatic_acceptance': False, 'deployment_mode': 'reading_only_candidate',
        'cross_fitted': {'threshold': None, 'split_unit': 'transitive_patient_source_template_decision_component'},
        'scope': 'Development agreement with saved machine labels, not clinical accuracy.', 'limits': LIMITS,
        'provider_calls': 0, 'trained_at': datetime.now(timezone.utc).isoformat(), 'seconds': time.perf_counter()-started}
    for part in ('calibration', 'validation'):
        batch = [r for r in predictions if r['partition'] == part]
        accepted_rows = [r for r in batch if threshold is not None and r['confidence'] >= threshold]
        report['comparison'][part] = {'decisions': len(batch), 'components': len({r['component'] for r in batch}),
            'student_agreement': sum(r['agrees'] for r in batch)/len(batch) if batch else None,
            'majority_agreement': sum(r['majority_agrees'] for r in batch)/len(batch) if batch else None,
            'candidate_threshold_accepted': len(accepted_rows),
            'candidate_threshold_agreement': sum(r['agrees'] for r in accepted_rows)/len(accepted_rows) if accepted_rows else None,
            'missing_reference_options': sorted(options-{r['machine_reference'] for r in batch}),
            'per_label': {label: {'references': sum(r['machine_reference'] == label for r in batch),
                'correct': sum(r['machine_reference'] == label and r['agrees'] for r in batch)} for label in sorted(options)}}
    output.mkdir(parents=True)
    joblib.dump({'model': model, 'manifest': report}, output/(manifest['task_key']+'.joblib'), compress=3)
    queue.write(output/(manifest['task_key']+'.json'), report); queue.write(output/'predictions.json', predictions)
    return report
