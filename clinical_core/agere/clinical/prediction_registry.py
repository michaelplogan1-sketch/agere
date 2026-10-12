"""Source-bound offline model qualification, exposed without predictor promotion.

An adapter registers existing baseline/landmark exports; no new labels, grouping,
fitting rules or clinical authority are introduced. Runtime returns aggregate
qualification and received-evidence references, never private partition rows.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import tempfile

from ...contracts import digest
from . import baseline_evidence as B, decision_landmarks as D, outcome_learning as L

VERSION = 'prediction-qualification-registry/1'
LINK = 'prediction-qualification-link.json'
FILES = {'features.jsonl', 'targets.jsonl'}
LIMITS = [
    'Recorded outcomes describe received observations, not adjudicated clinical events or absence of care.',
    'Development partitions lie inside original training families; outer and frozen evaluation families are excluded.',
    'Sample minimums are engineering gates, not clinical statistical guarantees.',
    'No automatic promotion, clinical risk score, treatment benefit or clinical execution is authorized.',
    'Counts describe the saved build population, which may differ from the population selected in a question.',
]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity():
    return digest([sha(__file__), L.identity(), D.code_identity()])


def prepare(stack, adapter, manifest):
    if adapter == 'index_day':
        op = B.outcomes.prepare(stack, manifest['outcome_parameters'])
        return B.prepare(stack, {'profile': manifest['profile']['id']}, op)
    if adapter == 'receipt_day':
        return D.prepare(stack, manifest['parameters'])
    raise ValueError('Unknown prediction qualification adapter')


def describe(adapter, manifest):
    if adapter == 'index_day':
        p = manifest['outcome_parameters']
        return {'adapter': adapter, 'contract': p['contract'], 'parameters': p,
                'profile': manifest['profile']['id'], 'title': B.outcomes.definition(p['contract'])['title']}
    if adapter == 'receipt_day':
        return {'adapter': adapter, 'contract': manifest['parameters']['contract'],
                'parameters': manifest['parameters'], 'profile': manifest['profile']['id'],
                'title': manifest['contract']['title']}
    raise ValueError('Unknown prediction qualification adapter')


def verify_dataset(stack, adapter, dataset):
    dataset = Path(dataset); m = read(dataset/'manifest.json'); meta = read(Path(stack)/'run.json')
    if m.get('synthetic') is not True or any(m['source_identity'][k] != meta[k] for k in ('snapshot', 'source_digest')):
        raise ValueError('Prediction qualification source snapshot changed')
    if set(m['files']) != FILES:
        raise ValueError('Exact separate feature and target files required')
    for name, expected in m['files'].items():
        if sha(dataset/name) != expected:
            raise ValueError('Prediction qualification dataset changed')
    if prepare(stack, adapter, m)['identity'] != m['identity']:
        raise ValueError('Prediction qualification evidence changed')
    if read(dataset/'manifest.json') != m or read(Path(stack)/'run.json') != meta or any(sha(dataset/n) != h for n, h in m['files'].items()):
        raise ValueError('Prediction qualification dataset changed during verification')
    return m


def source_summary(features, targets):
    """Clinical-source qualification only; contains no protected assignments."""
    xs = {r['patient_id']: r for r in features}; labels = Counter(); reasons = Counter(); examples = {}
    for row in targets:
        q = row['qualification']; pid = row['patient_id']; x = xs[pid]
        if q['timing_eligible_observed_target']:
            label = str(row['recorded_label']); labels[label] += 1; group = 'recorded_'+label
        else:
            reasons.update(q['exclusion_reasons']); group = 'excluded'
        bucket = examples.setdefault(group, [])
        if len(bucket) < 3:
            bucket.append({'patient_id': pid, 'baseline_receipt_id': x['baseline_receipt_id'],
                          'outcome_receipt_id': row['outcome_receipt_id'], 'feature_cutoff': x['feature_cutoff'],
                          'recorded_label': row['recorded_label'], 'timing_eligible': q['timing_eligible_observed_target'],
                          'reasons': q['exclusion_reasons']})
    return {'patients_assessed': len(features), 'timing_eligible': sum(labels.values()),
            'timing_eligible_label_counts': dict(labels), 'exclusion_reason_counts': dict(reasons),
            'exclusion_counts_overlap': True, 'examples': examples,
            'source_capture_established': False, 'clinical_label': None}


def class_deficits(qualification):
    return [{'partition': part, 'recorded_class': label,
             'observed': qualification['partition_class_counts'][part].get(str(label), 0),
             'minimum': L.PLAN['minimum_per_class'][part],
             'deficit': max(0, L.PLAN['minimum_per_class'][part] - qualification['partition_class_counts'][part].get(str(label), 0))}
            for part in L.SPLITS for label in (0, 1)]


def build(stack, lineage, adapter, dataset, output):
    stack, dataset, output = Path(stack), Path(dataset).resolve(), Path(output)
    if output.exists():
        raise ValueError('Fresh prediction qualification directory required')
    m = verify_dataset(stack, adapter, dataset); meta = read(stack/'run.json')
    policy, lm = L.learning_queue.lineage(lineage, meta); protected = L.semantic_learning.protections()
    features, targets = [[json.loads(line) for line in (dataset/name).read_text().splitlines()]
                         for name in ('features.jsonl', 'targets.jsonl')]
    q = L.qualify(features, targets, policy, protected)
    output.mkdir(parents=True); L.write(output/'qualification.json', q)
    model = L.fit_rows(q, output/'models')
    r = {'schema': VERSION, 'synthetic': True, 'implementation_identity': identity(),
         'task': describe(adapter, m), 'dataset': str(dataset), 'dataset_identity': m['identity'],
         'dataset_manifest_sha256': sha(dataset/'manifest.json'), 'source_identity': m['source_identity'],
         'lineage_root': str(Path(lineage).resolve()), 'lineage_policy_sha256': lm['policy_sha256'],
         'protected_register_digest': digest(protected), 'qualification_sha256': sha(output/'qualification.json'),
         'model_report_sha256': sha(output/'models/run.json'), 'source_qualification': source_summary(features, targets),
         'counts': {k: q[k] for k in ('patients_assessed', 'eligible_rows', 'component_count', 'partition_class_counts', 'excluded_reason_counts')},
         'class_deficits': class_deficits(q), 'status': model['status'], 'readiness_gaps': q['readiness_gaps'],
         'fit_input_contract_ready': q['fit_input_contract_ready'], 'clinical_prediction_authorized': False,
         'automatic_promotion': False, 'new_paid_api_calls': 0, 'limits': LIMITS}
    if m != verify_dataset(stack, adapter, dataset) or digest(L.semantic_learning.protections()) != digest(protected):
        raise ValueError('Prediction qualification inputs changed during build')
    _, now = L.learning_queue.lineage(lineage, read(stack/'run.json'))
    if now['policy_sha256'] != lm['policy_sha256']:
        raise ValueError('Prediction qualification lineage changed during build')
    r['id'] = digest(r); L.write(output/'run.json', r)
    return r


def verify(stack, entry):
    root = Path(entry['path']); r = read(root/'run.json')
    if sha(root/'run.json') != entry['sha256'] or r.get('schema') != VERSION:
        raise ValueError('Prediction qualification report changed')
    if r['id'] != digest({k: v for k, v in r.items() if k != 'id'}) or r['implementation_identity'] != identity():
        raise ValueError('Prediction qualification implementation changed')
    dataset = Path(r['dataset'])
    if sha(dataset/'manifest.json') != r['dataset_manifest_sha256']:
        raise ValueError('Prediction qualification manifest changed')
    m = verify_dataset(stack, r['task']['adapter'], dataset)
    if m['identity'] != r['dataset_identity'] or m['source_identity'] != r['source_identity'] or describe(r['task']['adapter'], m) != r['task']:
        raise ValueError('Prediction qualification task binding changed')
    _, lm = L.learning_queue.lineage(r['lineage_root'], read(Path(stack)/'run.json'))
    if lm['policy_sha256'] != r['lineage_policy_sha256'] or digest(L.semantic_learning.protections()) != r['protected_register_digest']:
        raise ValueError('Prediction qualification protections changed')
    if sha(root/'qualification.json') != r['qualification_sha256'] or sha(root/'models/run.json') != r['model_report_sha256']:
        raise ValueError('Prediction qualification or model report changed')
    model = read(root/'models/run.json'); q = read(root/'qualification.json')
    if model['qualification_digest'] != digest(q) or model['status'] != r['status']:
        raise ValueError('Prediction qualification model binding changed')
    if model['automatic_promotion'] or model['clinical_prediction_authorized'] or r['clinical_prediction_authorized']:
        raise ValueError('Clinical prediction authority is outside this registry')
    for name, payload in model['models'].items():
        if name not in L.PLAN['candidates'] or sha(root/'models'/(name+'.pkl')) != payload['weights_sha256']:
            raise ValueError('Prediction comparison weights changed')
    if model.get('predictions_sha256') and sha(root/'models/development-predictions.json') != model['predictions_sha256']:
        raise ValueError('Prediction comparison results changed')
    if sha(root/'run.json') != entry['sha256'] or sha(root/'qualification.json') != r['qualification_sha256'] or sha(root/'models/run.json') != r['model_report_sha256']:
        raise ValueError('Prediction qualification report changed during verification')
    return {k: r[k] for k in ('id', 'task', 'dataset_identity', 'status', 'source_qualification', 'counts', 'class_deficits',
                              'readiness_gaps', 'fit_input_contract_ready', 'clinical_prediction_authorized',
                              'automatic_promotion', 'limits')} | {'current': True, 'model_report': model,
                              'source_snapshot': r['source_identity']['snapshot'], 'report_sha256': entry['sha256']}


def status(stack, entries=None):
    if entries is None:
        pointer = Path(stack)/LINK
        if not pointer.exists():
            return {'schema': VERSION, 'available': False, 'reports': [], 'clinical_prediction_authorized': False}
        link = read(pointer)
        if link.get('schema') != VERSION:
            raise ValueError('Unknown prediction qualification registry')
        entries = link['reports']
    rows = []
    for entry in entries:
        try:
            rows.append(verify(stack, entry))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            rows.append({'id': entry.get('id'), 'task': entry.get('task'), 'current': False,
                         'status': 'unavailable', 'reason': str(exc)[:250], 'clinical_prediction_authorized': False})
    return {'schema': VERSION, 'synthetic': True, 'available': bool(rows), 'reports': rows,
            'all_current': bool(rows) and all(r['current'] for r in rows),
            'clinical_prediction_authorized': False, 'new_paid_api_calls': 0, 'limits': LIMITS}


def attach(stack, roots):
    entries = []
    for root in roots:
        root = Path(root).resolve(); r = read(root/'run.json')
        entries.append({'path': str(root), 'sha256': sha(root/'run.json'), 'id': r['id'], 'task': r['task']})
    if not entries or len({digest(e['task']) for e in entries}) != len(entries):
        raise ValueError('Nonempty unique prediction qualification tasks required')
    result = status(stack, entries)
    if not result['all_current']:
        raise ValueError('Cannot attach invalid prediction qualification reports: '+str([r.get('reason') for r in result['reports'] if not r['current']]))
    fd, temp = tempfile.mkstemp(prefix='.prediction-qualification-', dir=stack)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'schema': VERSION, 'reports': entries}, stream, indent=2, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, Path(stack)/LINK)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return result
