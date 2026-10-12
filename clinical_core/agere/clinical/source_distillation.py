"""Provenance-bound machine supervision on protected development sources.

This is teacher imitation, never source adjudication. Patient/template components
are partitioned before new readings; outer calibration/validation stay untouched.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import json

from ...contracts import digest
from . import learning_queue as queue, semantic_reader as reader
from . import semantic_student as student, learning_suggestions

VERSION = 'source-distillation/1'
PROVIDERS = ('kev', 'd1')
PARTITION_SALT = 'agere-source-imitation-development-v1'
LIMITS = [
    'Machine labels teach imitation of the nominated teacher, not clinical truth.',
    'All partitions are subdivisions of the acquisition training families. Outer calibration, validation and frozen Kev/d1 families remain excluded.',
    'This exposed development set is not a blind evaluation. Normalized templates do not establish semantic independence.',
    'Exact target context is supplied. Extraction-audit fields remain unassessed; no retrieval-recall or interpretation-error rate is inferred without reference labels.',
    'No human reviews, clinical authority, default-reader replacement or automatic acceptance are created.',
]


def code_identity():
    return digest([queue.sha(__file__), student.code_identity()])


def partition(items):
    """Union patient, literal source, whole-field template and exact model input."""
    parent = list(range(len(items)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    seen = {}
    for i, item in enumerate(items):
        keys = [('patient', item['patient_family']), ('source', item['source_hash']),
                ('template', item['template']), ('input', digest(student.model_text(item['input'])))]
        for key in keys:
            if key in seen: parent[root(i)] = root(seen[key])
            else: seen[key] = i
    groups = defaultdict(list)
    for i in range(len(items)): groups[root(i)].append(i)
    result = [dict(i) for i in items]
    for indices in groups.values():
        component = digest(sorted(items[i]['item_id'] for i in indices))
        bucket = int(digest([PARTITION_SALT, component])[:8], 16) % 10000
        part = 'train' if bucket < 7000 else 'calibration' if bucket < 8500 else 'validation'
        for i in indices: result[i].update(component=component, partition=part)
    return result


def prepare(stack, output, *, teacher='kev'):
    if teacher not in PROVIDERS: raise ValueError('Explicit installed native teacher required')
    output = Path(output)
    if output.exists(): raise ValueError('Fresh development plan required')
    root, run, _, items = queue.connected(stack)
    selected, unassessed = [], []
    for item in items:
        if not item['target']:
            unassessed.append({'item_id': item['id'], 'reason': 'no_marked_target_not_a_retrieval_miss'}); continue
        record, text, meta = reader.source(stack, item['target'])
        if digest(record) != item['source_hash']: raise ValueError('Acquisition source changed')
        p = reader.packet(record, text, item['target'], meta['snapshot']['as_of'])
        try: student.model_text(p)
        except ValueError as exc:
            unassessed.append({'item_id': item['id'], 'reason': 'context_not_fit_ready', 'detail': str(exc)}); continue
        selected.append({'item_id': item['id'], 'target': item['target'], 'patient_family': item['patient_family'],
            'source_hash': item['source_hash'], 'template': digest(queue.normalize(text)), 'input': p,
            'annotation_hint': item['machine_hint']})
    from . import local_decisions
    identities = {}
    for provider in PROVIDERS:
        manifest = local_decisions.verify(reader.ROOT/'models'/('kev-4b' if provider == 'kev' else 'd1-3b')/'agere-model.json')
        identities[provider] = digest([local_decisions.VERSION, manifest, local_decisions.sha256(local_decisions.__file__)])
    rows = partition(selected)
    output.mkdir(parents=True)
    queue.write(output/'targets.json', rows)
    plan = {'schema': VERSION, 'synthetic': True, 'created_at': datetime.now(timezone.utc).isoformat(),
        'code_identity': code_identity(), 'queue_root': str(root.resolve()), 'queue_run_sha256': queue.sha(root/'run.json'),
        'source_identity': run['source_identity'], 'reader_identity': reader.identity(),
        'protection_register': run['protection_register'], 'model_identities': identities,
        'teacher': teacher, 'teacher_selection': 'Declared before new readings; native implementation source for imitation, not a superiority decision.',
        'files': {'targets.json': queue.sha(output/'targets.json')}, 'partition_salt': PARTITION_SALT,
        'counts': dict(Counter(r['partition'] for r in rows)), 'target_count': len(rows),
        'patient_families': len({r['patient_family'] for r in rows}),
        'components': len({r['component'] for r in rows}), 'unassessed': unassessed,
        'human_reviewed_labels_created': 0, 'limits': LIMITS}
    queue.write(output/'plan.json', plan)
    link = Path(stack)/'source-distillation-link.json'
    queue.write(link.with_suffix('.tmp'), {'path': str(output.resolve()), 'plan_sha256': queue.sha(output/'plan.json')})
    link.with_suffix('.tmp').replace(link)
    return plan


def connected(stack, output=None):
    if output is None:
        link = json.loads((Path(stack)/'source-distillation-link.json').read_text())
        output = Path(link['path'])
        if queue.sha(output/'plan.json') != link['plan_sha256']: raise ValueError('Development plan changed')
    output = Path(output)
    plan = json.loads((output/'plan.json').read_text())
    root, run, policy, items = queue.connected(stack)
    if plan['schema'] != VERSION or plan['code_identity'] != code_identity(): raise ValueError('Distillation implementation changed')
    if (str(root.resolve()) != plan['queue_root'] or queue.sha(root/'run.json') != plan['queue_run_sha256'] or
            run['source_identity'] != plan['source_identity'] or reader.identity() != plan['reader_identity'] or
            run['protection_register'] != plan['protection_register']): raise ValueError('Acquisition provenance changed')
    if queue.sha(output/'targets.json') != plan['files']['targets.json']: raise ValueError('Development targets changed')
    rows = json.loads((output/'targets.json').read_text()); by_id = {r['id']: r for r in items}
    for row in rows:
        item = by_id.get(row['item_id'])
        if item is None or item['target'] != row['target'] or item['patient_family'] != row['patient_family']:
            raise ValueError('Development target not in protected acquisition queue')
        if queue.eligibility(row['target']['patient_id'], policy, plan['protection_register']): raise ValueError('Protected development family')
        record, text, meta = reader.source(stack, row['target'])
        p = reader.packet(record, text, row['target'], meta['snapshot']['as_of'])
        if digest(record) != row['source_hash'] or p != row['input'] or digest(queue.normalize(text)) != row['template']:
            raise ValueError('Development received source/context changed')
    if rows != partition(rows): raise ValueError('Development family partition changed')
    return output, plan, rows


def reading_for(stack, row, plan, provider):
    key = digest([plan['reader_identity'], plan['source_identity'], row['target'], row['source_hash'],
                  provider, plan['model_identities'][provider], row['input']])
    database = reader.db(stack)
    try: exists = database.execute('SELECT 1 FROM readings WHERE id=?', (key,)).fetchone()
    finally: database.close()
    return reader.inspect(stack, key) if exists else None


def acquire(stack, output, provider, *, notify=lambda row: None):
    if provider not in PROVIDERS: raise ValueError('Native acquisition provider required')
    output, plan, rows = connected(stack, output)
    file = output/('attempts-'+provider+'.json')
    attempts = json.loads(file.read_text()) if file.exists() else {'plan_sha256': queue.sha(output/'plan.json'), 'rows': []}
    if attempts['plan_sha256'] != queue.sha(output/'plan.json'): raise ValueError('Attempt plan changed')
    done = {r['item_id'] for r in attempts['rows']}
    model = None
    for row in rows:
        if row['item_id'] in done: continue  # Failures are retained; no implicit retry.
        record = {'item_id': row['item_id'], 'provider': provider}
        try:
            result = reading_for(stack, row, plan, provider)
            cached = result is not None
            if result is None:
                if model is None:
                    from .local_decisions import KevMLX, LiquidD1
                    model = KevMLX() if provider == 'kev' else LiquidD1()
                    if model.identity != plan['model_identities'][provider]: raise ValueError('Native teacher identity changed')
                result = reader.read(stack, row['target'], provider, local_model=model)
            record.update(reading_id=result['id'], cache_hit=cached, usage=result['usage'], new_paid_api_calls=0)
        except (ValueError, RuntimeError, OSError, ImportError) as exc:
            record.update(error=str(exc)[:500], failure_kind=learning_suggestions.failure_kind(str(exc)), retry_attempted=False)
        attempts['rows'].append(record)
        queue.write(file.with_suffix('.tmp'), attempts); file.with_suffix('.tmp').replace(file)
        notify(record)
        if record.get('error') and model is None: break  # One failed load is enough.
    return attempts


def snapshot_rows(stack, output):
    output, plan, targets = connected(stack, output)
    rows, excluded = [], []
    for target in targets:
        r = reading_for(stack, target, plan, plan['teacher'])
        if r is None:
            excluded.append({'item_id': target['item_id'], 'reason': 'teacher_reading_unavailable'}); continue
        rows.append({'id': target['item_id'], 'input': r['context'], 'labels': r['reading']['labels'],
            'partition': target['partition'], 'patient_family': target['patient_family'],
            'patient_id': target['target']['patient_id'], 'source_hash': target['source_hash'],
            'source_families': [target['template']], 'target': target['target'],
            'teacher': {'provider': plan['teacher'], 'model_identity': r['model_identity'], 'reading_id': r['id'],
                        'labels_digest': digest(r['reading']['labels']), 'context_digest': digest(r['context'])},
            'supervision': 'Unreviewed machine teacher; imitation target only', 'clinical_judgment_label': False})
    return rows, excluded


def freeze(stack, output, dataset):
    output, plan, _ = connected(stack, output); rows, excluded = snapshot_rows(stack, output)
    if excluded: raise ValueError('Complete fixed acquisition required before freezing; failures cannot silently select training data')
    student.validate_rows(rows)
    dataset = Path(dataset); dataset.mkdir(parents=True, exist_ok=False)
    files = {}
    for part in student.PARTITIONS:
        f = dataset/(part+'.jsonl')
        f.write_text(''.join(json.dumps(r, sort_keys=True)+'\n' for r in rows if r['partition'] == part))
        files[f.name] = queue.sha(f)
    manifest = {'schema': VERSION, 'synthetic': True, 'supervision': 'machine_teacher_snapshot',
        'plan': str(output.resolve()), 'plan_sha256': queue.sha(output/'plan.json'), 'code_identity': code_identity(),
        'files': files, 'rows_digest': digest(rows), 'teacher': plan['teacher'], 'counts': plan['counts'],
        'human_reviewed_labels_created': 0, 'limits': LIMITS}
    queue.write(dataset/'manifest.json', manifest); return manifest


def load_snapshot(stack, dataset):
    dataset = Path(dataset); m = json.loads((dataset/'manifest.json').read_text())
    if m['schema'] != VERSION or m['code_identity'] != code_identity(): raise ValueError('Machine-supervision contract changed')
    if queue.sha(Path(m['plan'])/'plan.json') != m['plan_sha256']: raise ValueError('Machine-supervision plan changed')
    if set(m['files']) != {p+'.jsonl' for p in student.PARTITIONS}: raise ValueError('Exact distillation partitions required')
    rows, excluded = snapshot_rows(stack, m['plan'])
    if excluded or digest(rows) != m['rows_digest']: raise ValueError('Teacher source or labels changed')
    for part in student.PARTITIONS:
        f = dataset/(part+'.jsonl')
        if queue.sha(f) != m['files'][f.name]: raise ValueError('Distillation checksum mismatch')
        saved = [json.loads(line) for line in f.read_text().splitlines()]
        if saved != [r for r in rows if r['partition'] == part]: raise ValueError('Frozen teacher rows changed')
    student.validate_rows(rows); return rows, m


def status(stack):
    if not (Path(stack)/'source-distillation-link.json').exists(): return {'status': 'not_attached', 'synthetic': True}
    output, plan, targets = connected(stack)
    providers = {}; paired = []; complete = defaultdict(dict)
    for provider in PROVIDERS:
        attempts_file = output/('attempts-'+provider+'.json')
        attempts = json.loads(attempts_file.read_text())['rows'] if attempts_file.exists() else []
        count = 0; seconds = 0; tokens = 0
        for row in targets:
            r = reading_for(stack, row, plan, provider)
            if r:
                complete[row['item_id']][provider] = r['reading']['labels']; count += 1
                seconds += r['usage'].get('seconds', 0); tokens += r['usage'].get('input_tokens', 0)
        providers[provider] = {'successful_readings': count, 'pending': len(targets)-len(attempts),
            'execution_failures': dict(Counter(r['failure_kind'] for r in attempts if r.get('error'))),
            'recorded_inference_seconds': seconds, 'recorded_local_input_tokens': tokens}
    for key, readings in complete.items():
        if set(readings) == set(PROVIDERS):
            paired.append({'item_id': key, 'disagreement_axes': [a for a in reader.AXES if readings['kev'][a] != readings['d1'][a]]})
    return {'status': 'development_machine_supervision', 'synthetic': True, 'teacher': plan['teacher'],
        'source_identity': plan['source_identity'], 'counts': plan['counts'], 'target_count': plan['target_count'],
        'unassessed_source_fields': len(plan['unassessed']), 'providers': providers, 'paired_comparisons': paired,
        'new_paid_api_calls': 0, 'human_reviewed_labels_created': 0, 'winner_selected': False,
        'retrieval_recall': None, 'interpretation_error_rate': None, 'limits': LIMITS}
