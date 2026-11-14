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
import os
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
    'Legacy labels selected confident non-unknown responses; newly captured unresolved outcomes remain review inputs, not fitting labels. This is development data, not population performance.',
    'The candidate remains reading-only. Qualification, clinical authority and default activation are not created by this workflow.',
]


def option_contract(options):
    return hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()


def record_outcome(question, options, evidence, result, *, as_of, task_key='agere_chart_judgment'):
    """Retain unresolved outcomes without teaching the student's own predictions.

    Uses the existing observation store. Every distinct state/result is retained;
    exact repeats share one row. A capture failure must not fail a user answer.
    """
    state = student.decision_state(question, options, evidence, as_of=as_of)
    contract = student.input_contract()
    label = result.get('label')
    probability = (result.get('calibrated_probabilities') or result.get('probabilities') or {}).get(label)
    provider = result.get('decided_by') or result.get('provider')
    eligible = bool(result.get('decided_by') in ('jev', 'perplexity') and label in state['options'] and
                    label != 'unknown' and isinstance(probability, (int, float)) and student.TEACHER_MIN <= probability <= 1)
    key = digest([contract, task_key, state, result])
    path = student.store_path(); path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS typed_outcomes ('
                'id TEXT PRIMARY KEY,task_key TEXT,question TEXT,options TEXT,evidence TEXT,label TEXT,probability REAL,'
                'provider TEXT,recorded_at TEXT,input_contract TEXT,as_of TEXT,teacher_model TEXT,request_hash TEXT,'
                'fit_eligible INTEGER NOT NULL,result TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO typed_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (key,task_key,state['subject'],json.dumps(state['options']),json.dumps(state['evidence']),label,probability,
                 provider,datetime.now(timezone.utc).isoformat(),contract,state['as_of'],result.get('model'),
                 result.get('request_hash'),int(eligible),json.dumps(result,sort_keys=True,allow_nan=False)))
    finally: db.close()
    return {'id': key, 'fit_eligible': eligible, 'kind': 'machine_outcome'}


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


def _review_tables(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS typed_reviews (
        binding TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(binding,revision));
        CREATE TABLE IF NOT EXISTS typed_review_requests (id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS immutable_typed_review BEFORE UPDATE ON typed_reviews BEGIN SELECT RAISE(ABORT,'Reviews are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS preserve_typed_review BEFORE DELETE ON typed_reviews BEGIN SELECT RAISE(ABORT,'Reviews are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_typed_review_request BEFORE UPDATE ON typed_review_requests BEGIN SELECT RAISE(ABORT,'Review requests are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS preserve_typed_review_request BEFORE DELETE ON typed_review_requests BEGIN SELECT RAISE(ABORT,'Review requests are append-only'); END;''')


def review_binding(contract, release, key, source_hash):
    return digest([contract, release, key, source_hash])


def _latest_review(db, binding):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='typed_reviews'").fetchone(): return None
    row = db.execute('SELECT payload FROM typed_reviews WHERE binding=? ORDER BY revision DESC LIMIT 1', (binding,)).fetchone()
    return json.loads(row[0]) if row else None


def _review_context(stack):
    meta = json.loads((Path(stack)/'run.json').read_text())
    if meta.get('synthetic') is not True: raise ValueError('Synthetic source-review workspace required')
    lineage = os.environ.get('AGERE_TYPED_LEARNING_LINEAGE')
    if not lineage: raise ValueError('Typed source-learning partitions are not configured')
    policy, manifest = queue.lineage(lineage, meta); protected = semantic_learning.protections()
    if manifest.get('protection_register_sha256') != protected['register_sha256']:
        raise ValueError('Source-review protection registry changed')
    return meta, policy, protected


def _inspect_review(db, warehouse, context, observation_id):
    table, sep, ident = str(observation_id).partition(':')
    if not sep or table not in ('labels', 'typed_outcomes') or not ident:
        raise ValueError('Exact saved observation identity required')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        raise ValueError('Observation store is unavailable')
    ids = {str(r[0] or '').rsplit('#', 1)[0] for r in db.execute(
        "SELECT json_extract(value,'$.id') FROM "+table+",json_each("+table+".evidence) WHERE "+table+".id=?", (ident,))}
    if len(ids) != 1: raise ValueError('Single source-bound document required')
    rid = next(iter(ids)); person = warehouse.execute('SELECT patient_id FROM source_records WHERE record_id=?', (rid,)).fetchone()
    meta, policy, protected = context
    reason = 'source_identifier_unresolved' if person is None else queue.eligibility(person[0], policy, protected)
    if reason: raise ValueError(reason)
    row = dict(db.execute('SELECT * FROM '+table+' WHERE id=?', (ident,)).fetchone())
    if row['input_contract'] != student.input_contract() or row['task_key'] != 'agere_chart_judgment':
        raise ValueError('Observation does not match current typed task')
    source = dict(warehouse.execute('SELECT * FROM source_records WHERE record_id=?', (rid,)).fetchone())
    if source.get('synthetic') != 1: raise ValueError('Synthetic received source required')
    state, field, text = _bind(row, source)
    key = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    binding = review_binding(row['input_contract'], meta['snapshot']['release'], key, digest(source))
    review = _latest_review(db, binding)
    return {'observation_id': observation_id, 'binding': binding, 'decision_key': key, 'state': state,
        'patient_id': person[0], 'record_id': rid, 'source_field': field, 'source_text': text,
        'source_hash': digest(source), 'source_release': meta['snapshot']['release'], 'input_contract': row['input_contract'],
        'machine_label': row['label'], 'machine_provider': row['provider'], 'machine_probability': row['probability'],
        'review': review, 'revision': review['revision'] if review else 0,
        'authority': 'Local documentary review; reviewer identity and clinical authority are not independently verified.'}


def active_student():
    """Inspect the actual configured weights, without changing their acceptance bar."""
    state = {'task_key': 'agere_chart_judgment', 'enabled': student.enabled(),
             'automatic_decisions': False, 'clinical_qualification': False}
    if not state['enabled']: return {**state, 'status': 'disabled'}, None
    obj = student.load(state['task_key'])
    if not obj: return {**state, 'status': 'weights_unavailable'}, None
    if obj['manifest'].get('input_contract') != student.input_contract():
        return {**state, 'status': 'input_contract_mismatch'}, None
    threshold = obj['manifest']['cross_fitted']['threshold']
    return {**state, 'status': 'active_preliminary_reader' if threshold is None else 'active_selective_reader',
            'automatic_decisions': threshold is not None, 'acceptance_threshold': threshold,
            'input_contract': obj['manifest']['input_contract'],
            'training_decisions': obj['manifest'].get('training_decisions', obj['manifest'].get('rows')),
            'option_contracts': len(obj['model'].get('option_contracts', [])),
            'calibration': obj['manifest'].get('comparison', {}).get('calibration'),
            'scope': 'Exact task and option meanings only; preliminary readings are not accepted answers.'}, obj


def inspect_review(stack, observation_id):
    context = _review_context(stack)
    with _read_db(student.store_path()) as db, _read_db(context[0]['snapshot']['source_dataset']['source']) as warehouse:
        result = _inspect_review(db, warehouse, context, observation_id)
    state, _ = active_student(); question = result['state']
    prediction = student.predict('agere_chart_judgment', question['subject'], question['options'],
                                 question['evidence'], as_of=question['as_of'])
    result['local_student'] = {**state, 'matches_this_task': prediction is not None,
                               'reading': prediction.get('reading') if prediction else None,
                               'answer_accepted': bool(prediction and prediction.get('label'))}
    return result


def review_listing(stack, *, offset=0):
    if type(offset) is not int or offset < 0: raise ValueError('Nonnegative page offset required')
    context = _review_context(stack); candidates = []; excluded = Counter()
    local_state, local_model = active_student()
    contracts = set(local_model['model'].get('option_contracts', [])) if local_model else set()
    if not student.store_path().exists(): return {'rows': [], 'total': 0, 'offset': offset, 'next_offset': None, 'local_student': local_state, 'student_matching_readings': 0}
    with _read_db(student.store_path()) as db, _read_db(context[0]['snapshot']['source_dataset']['source']) as warehouse:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('typed_outcomes', 'labels'):
            if table not in tables: continue
            for header in db.execute('SELECT id FROM '+table+' WHERE task_key=? AND input_contract=? ORDER BY recorded_at DESC,id',
                    ('agere_chart_judgment', student.input_contract())).fetchall():
                ident = table+':'+header[0]
                try: detail = _inspect_review(db, warehouse, context, ident)
                except ValueError as exc: excluded[str(exc)] += 1; continue
                if any(r['binding'] == detail['binding'] for r in candidates): continue
                candidates.append({k: detail[k] for k in ('observation_id','binding','patient_id','record_id','machine_label','revision')}
                    | {'question': detail['state']['subject'], 'review_status': (detail['review'] or {}).get('status'),
                       'local_student_applicable': json.dumps(detail['state']['options'], sort_keys=True) in contracts})
    candidates.sort(key=lambda r: not r['local_student_applicable'])
    return {'local_student': local_state, 'student_matching_readings': sum(r['local_student_applicable'] for r in candidates),
        'rows': candidates[offset:offset+24], 'total': len(candidates), 'offset': offset,
        'next_offset': offset+24 if offset+24 < len(candidates) else None, 'excluded': dict(excluded),
        'automatic_training': False, 'clinical_authority_established': False}


def save_review(stack, payload):
    required = {'observation_id','expected_binding','expected_revision','request_id','actor','status','label','rationale','use_for_development'}
    if not isinstance(payload, dict) or set(payload) != required: raise ValueError('Exact source-bound correction fields required')
    if any(not isinstance(payload[k], str) or not payload[k].strip() for k in ('actor','rationale','request_id')):
        raise ValueError('Reviewer, rationale and request identity required')
    if payload['status'] not in ('accepted','corrected','unresolved','rejected') or type(payload['expected_revision']) is not int:
        raise ValueError('Review status and expected revision required')
    if type(payload['use_for_development']) is not bool: raise ValueError('Explicit development-use choice required')
    current = inspect_review(stack, payload['observation_id'])
    if current['binding'] != payload['expected_binding']: raise ValueError('Source or decision changed; reopen before saving')
    labeled = payload['status'] in ('accepted','corrected')
    if (labeled and payload['label'] not in current['state']['options']) or (not labeled and payload['label'] is not None):
        raise ValueError('Choose an exact option only for an accepted or corrected review')
    if payload['status'] == 'accepted' and payload['label'] != current['machine_label']:
        raise ValueError('A changed answer must be saved as a correction')
    if not labeled and payload['use_for_development']: raise ValueError('Unresolved or rejected reviews cannot teach a label')
    db = sqlite3.connect(student.store_path(), timeout=30); request_hash = digest(payload)
    try:
        _review_tables(db); db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT request_hash,payload FROM typed_review_requests WHERE id=?', (payload['request_id'],)).fetchone()
        if old:
            if old[0] != request_hash: raise ValueError('Request identity reused with different correction')
            return {**json.loads(old[1]), 'replayed': True}
        previous = _latest_review(db, current['binding']); revision = previous['revision'] if previous else 0
        if payload['expected_revision'] != revision: raise ValueError('Review changed; reload before saving')
        row = {**payload, 'binding': current['binding'], 'revision': revision+1, 'created_at': datetime.now(timezone.utc).isoformat(),
            'source_hash': current['source_hash'], 'source_release': current['source_release'], 'decision_key': current['decision_key'],
            'input_contract': current['input_contract'], 'training_eligible': labeled and payload['use_for_development'],
            'reviewer_identity_verified': False, 'authority': current['authority'], 'clinical_state_established': False}
        db.execute('INSERT INTO typed_reviews VALUES (?,?,?)', (current['binding'], revision+1, json.dumps(row)))
        db.execute('INSERT INTO typed_review_requests VALUES (?,?,?)', (payload['request_id'], request_hash, json.dumps(row)))
        db.commit(); return row
    finally: db.close()


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
        tables = {r[0] for r in observations.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        candidates = [(table, header) for table in ('labels', 'typed_outcomes') if table in tables
            for header in observations.execute('SELECT id,options FROM '+table+' WHERE task_key=? AND input_contract=? ORDER BY id',
                ('agere_chart_judgment', contract)).fetchall()]
        for table, header in candidates:
            if option_contract(json.loads(header['options'])) != options_sha256: continue
            ident = header['id']; observation_id = table+':'+ident; selected.append(observation_id)
            ids = {str(r[0] or '').rsplit('#', 1)[0] for r in observations.execute(
                "SELECT json_extract(value,'$.id') FROM "+table+",json_each("+table+".evidence) WHERE "+table+".id=?", (ident,))}
            if len(ids) != 1:
                excluded.append({'observation_id': observation_id, 'reason': 'single_document_binding_required'}); continue
            rid = next(iter(ids))
            person = warehouse.execute('SELECT patient_id FROM source_records WHERE record_id=?', (rid,)).fetchone()
            reason = 'source_identifier_unresolved' if person is None else queue.eligibility(person[0], policy, protected)
            if reason:
                excluded.append({'observation_id': observation_id, 'reason': reason}); continue
            row = dict(observations.execute('SELECT * FROM '+table+' WHERE id=?', (ident,)).fetchone())
            source = dict(warehouse.execute('SELECT * FROM source_records WHERE record_id=?', (rid,)).fetchone())
            if source.get('synthetic') != 1:
                excluded.append({'observation_id': observation_id, 'reason': 'non_synthetic_source'}); continue
            try: state, field, text = _bind(row, source)
            except ValueError as exc:
                excluded.append({'observation_id': observation_id, 'reason': str(exc)}); continue
            key = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            item = groups.setdefault(key, {'decision_key': key, 'state': state,
                'patient_family': policy['patient_families'][source['patient_id']], 'patient_id': source['patient_id'],
                'record_id': rid, 'source_field': field, 'source_hash': digest(source),
                'template': digest(queue.normalize(text)), 'observations': [], 'reference': None})
            binding = review_binding(contract, config['snapshot']['release'], key, digest(source))
            item['reference'] = _latest_review(observations, binding)
            item['observations'].append({**{k: row[k] for k in ('id', 'label', 'probability', 'provider', 'teacher_model', 'request_hash', 'recorded_at')},
                'observation_id': observation_id, 'fit_eligible': bool(row.get('fit_eligible', True)),
                'raw_outcome': json.loads(row['result']) if table == 'typed_outcomes' else None})
    # No label-dependent movement or search for a more favorable split.
    rows = partition(list(groups.values()))
    for row in rows:
        eligible = [r for r in row['observations'] if r['fit_eligible'] and r['probability'] is not None and
                    float(r['probability']) >= student.TEACHER_MIN and r['label'] in row['state']['options']]
        labels = {r['label'] for r in eligible}
        row['label'] = next(iter(labels)) if len(labels) == 1 else None
        row['fit_status'] = 'machine_supervision' if len(labels) == 1 else 'conflicting_machine_labels' if labels else 'no_eligible_machine_label'
        row['weight'] = sum(float(r['probability']) for r in eligible)/len(eligible) if eligible else 0
        reference = row['reference']
        if reference:
            row['label'] = reference['label'] if reference['training_eligible'] else None
            row['weight'] = 1.0 if reference['training_eligible'] else 0.0
            row['fit_status'] = 'local_documentary_review' if reference['training_eligible'] else 'review_withheld_from_training'
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
    manifest['local_documentary_references'] = sum(r['fit_status'] == 'local_documentary_review' for r in rows)
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
        as_of=r['state']['as_of'], weight=r['weight'], source=r['fit_status']) for r in training]
    model = student.train_rows(manifest['task_key'], examples)
    if model is None: raise ValueError('Declared training partition has insufficient distinct decisions or classes')
    majority = Counter(r['label'] for r in training).most_common(1)[0][0]
    predictions = []
    for row in eligible:
        if row['partition'] == 'train': continue
        s = row['state']; probabilities = student.probabilities(model, s['subject'], s['options'], s['evidence'], as_of=s['as_of'])
        predicted = max(probabilities, key=probabilities.get)
        predictions.append({'decision_key': row['decision_key'], 'component': row['component'], 'partition': row['partition'],
            'reference_label': row['label'], 'reference_kind': row['fit_status'], 'predicted': predicted, 'confidence': probabilities[predicted],
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
        'scope': 'Development agreement with declared machine/local documentary references, not clinical accuracy.', 'limits': LIMITS,
        'training_reference_kinds': dict(Counter(r['fit_status'] for r in training)),
        'provider_calls': 0, 'trained_at': datetime.now(timezone.utc).isoformat(), 'seconds': time.perf_counter()-started}
    for part in ('calibration', 'validation'):
        batch = [r for r in predictions if r['partition'] == part]
        accepted_rows = [r for r in batch if threshold is not None and r['confidence'] >= threshold]
        report['comparison'][part] = {'decisions': len(batch), 'components': len({r['component'] for r in batch}),
            'student_agreement': sum(r['agrees'] for r in batch)/len(batch) if batch else None,
            'majority_agreement': sum(r['majority_agrees'] for r in batch)/len(batch) if batch else None,
            'candidate_threshold_accepted': len(accepted_rows),
            'candidate_threshold_agreement': sum(r['agrees'] for r in accepted_rows)/len(accepted_rows) if accepted_rows else None,
            'missing_reference_options': sorted(options-{r['reference_label'] for r in batch}),
            'per_label': {label: {'references': sum(r['reference_label'] == label for r in batch),
                'correct': sum(r['reference_label'] == label and r['agrees'] for r in batch)} for label in sorted(options)}}
    output.mkdir(parents=True)
    joblib.dump({'model': model, 'manifest': report}, output/(manifest['task_key']+'.joblib'), compress=3)
    queue.write(output/(manifest['task_key']+'.json'), report); queue.write(output/'predictions.json', predictions)
    return report
