"""Offline family-protected fitting for qualified *received-record* outcomes.

Runtime features contain received evidence only. The existing isolated ancestry
register is used for exclusions/grouping, never supplied as a model feature.
All new partitions are inside the original training partition.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import math
import pickle
import time

from ...contracts import digest
from . import baseline_evidence as baseline, learning_queue, semantic_learning

VERSION = 'observed-outcome-learning/1'
SPLITS = ('train', 'calibration', 'development_validation')
PLAN = {'salt': 'agere-observed-outcome-development/1', 'buckets': [70, 85, 100],
        'minimum_per_class': {'train': 10, 'calibration': 5, 'development_validation': 5},
        'candidates': ['logistic', 'logistic_without_arrival_blocks'],
        'automatic_promotion': False, 'target': 'received_observation_only'}


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path, value): Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def identity():
    return digest([sha(__file__), baseline.code_identity(), sha(semantic_learning.__file__), sha(learning_queue.__file__)])


def groups(features, policy, protected):
    """Union before exclusion: a protected relative/template cannot leak through."""
    parent = list(range(len(features))); seen = {}
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, row in enumerate(features):
        family = policy['patient_families'].get(row['patient_id'])
        keys = [('patient', row['patient_id'])]
        if family: keys.append(('family', family))
        keys += [('source_template', k) for k in row['source_template_families']]
        keys += [('record', k) for k in row['input_record_ids']]
        keys += [('household', k) for k in row.get('household_groups', [])]
        for key in keys:
            if key in seen: parent[root(i)] = root(seen[key])
            else: seen[key] = i
    components = defaultdict(list)
    for i, row in enumerate(features): components[root(i)].append(row)
    assignments = {}
    for rows in components.values():
        reasons = sorted({reason for row in rows if (reason := learning_queue.eligibility(row['patient_id'], policy, protected))})
        key = digest(sorted(row['patient_id'] for row in rows))
        bucket = int(digest([PLAN['salt'], key])[:8], 16) % 100
        part = 'train' if bucket < 70 else 'calibration' if bucket < 85 else 'development_validation'
        for row in rows:
            assignments[row['patient_id']] = {'component': key, 'partition': None if reasons else part,
                'family': policy['patient_families'].get(row['patient_id']),
                'exclusions': ['related_component:'+r for r in reasons]}
    return assignments


def qualify(features, targets, policy, protected):
    semantic_learning.validate_policy(policy)
    byid = {r['patient_id']: r for r in targets}
    if len(byid) != len(targets) or len({r['patient_id'] for r in features}) != len(features) or set(byid) != {r['patient_id'] for r in features}:
        raise ValueError('Exact unique feature/target patient join required')
    assignment = groups(features, policy, protected); rows = []; excluded = []
    for x in features:
        y = byid[x['patient_id']]; a = assignment[x['patient_id']]
        if x['baseline_receipt_id'] != y['baseline_receipt_id']: raise ValueError('Feature/target receipt mismatch')
        q = y['qualification']
        if q['patient_id'] != x['patient_id'] or q['baseline_receipt_id'] != x['baseline_receipt_id'] or q['recorded_label'] != y['recorded_label']:
            raise ValueError('Target qualification mismatch')
        reasons = a['exclusions'] + q['exclusion_reasons']
        if not q['timing_eligible_observed_target'] and not q['exclusion_reasons']: raise ValueError('Missing exclusion explanation')
        if y['clinical_label'] is not None: raise ValueError('Clinical targets are outside this contract')
        if reasons:
            excluded.append({'patient_id': x['patient_id'], 'component': a['component'], 'reasons': reasons}); continue
        if type(y['recorded_label']) is not int or y['recorded_label'] not in (0, 1): raise ValueError('Binary observed target required')
        rows.append({'patient_id': x['patient_id'], 'baseline_receipt_id': x['baseline_receipt_id'],
            'outcome_receipt_id': y['outcome_receipt_id'], 'partition': a['partition'],
            'component': a['component'], 'family': a['family'], 'values': x['values'],
            'recorded_label': y['recorded_label'], 'feature_cutoff': x['feature_cutoff']})
    counts = {part: dict(Counter(str(r['recorded_label']) for r in rows if r['partition'] == part)) for part in SPLITS}
    gaps = []
    for part in SPLITS:
        if any(counts[part].get(str(label), 0) < PLAN['minimum_per_class'][part] for label in (0, 1)):
            gaps.append(part+': insufficient independently grouped observations of each recorded class')
    return {'schema': VERSION, 'synthetic': True, 'plan': PLAN, 'rows': rows, 'excluded': excluded,
        'partition_class_counts': counts, 'patients_assessed': len(features), 'eligible_rows': len(rows),
        'component_count': len({a['component'] for a in assignment.values()}),
        'excluded_reason_counts': dict(Counter(reason for row in excluded for reason in row['reasons'])),
        'fit_input_contract_ready': not gaps, 'readiness_gaps': gaps,
        'source_policy_digest': digest(policy), 'protection_register': protected,
        'outer_evaluation_used_for_fitting_or_scoring': False,
        'independent_clinical_validation': False, 'automatic_promotion': False}


def encoded(values):
    out = {}
    for key, value in values.items():
        if value is None: out['missing:'+key] = 1.0
        elif type(value) in (int, float) and math.isfinite(value): out['value:'+key] = float(value)
        elif isinstance(value, str): out['category:'+key+'='+value] = 1.0
        else: raise ValueError('Unsupported feature value')
    return out


def fit_rows(qualification, output):
    """Fit a frozen small-model comparison only when the data contract passes."""
    import numpy as np
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from .warehouse_prediction import metrics
    output = Path(output)
    if output.exists(): raise ValueError('Fresh outcome-model directory required')
    output.mkdir(parents=True)
    report = {'schema': VERSION, 'synthetic': True, 'plan': PLAN, 'code_identity': identity(),
        'qualification_digest': digest(qualification), 'partition_class_counts': qualification['partition_class_counts'],
        'status': 'not_fitted', 'reasons': qualification['readiness_gaps'], 'models': {},
        'new_paid_api_calls': 0, 'automatic_promotion': False, 'clinical_prediction_authorized': False,
        'validation_scope': 'Development subdivision of original training families; outer/frozen evaluation untouched.',
        'target_meaning': 'Future received endpoint observation, not clinical-event risk or intervention benefit.'}
    if not qualification['fit_input_contract_ready']:
        write(output/'run.json', report); return report
    if qualification['plan'] != PLAN: raise ValueError('Frozen fitting contract changed')
    start = time.perf_counter(); rows = qualification['rows']
    y = np.array([r['recorded_label'] for r in rows]); part = np.array([r['partition'] for r in rows])
    masks = {k: part == k for k in SPLITS}; tr, cal, val = [masks[k] for k in SPLITS]
    # Recheck the rows rather than trust the supplied readiness flag.
    for split in SPLITS:
        for label in (0, 1):
            if int(sum((y == label) & masks[split])) < PLAN['minimum_per_class'][split]: raise ValueError('Insufficient fitting rows')
    for key in ('patient_id', 'family', 'component'):
        related = defaultdict(set)
        for r in rows: related[r[key]].add(r['partition'])
        if any(len(v) != 1 for v in related.values()): raise ValueError('Related fitting rows cross partitions')
    report['constant_training_prevalence'] = metrics(y[val], np.full(sum(val), float(y[tr].mean())))
    predictions = {r['patient_id']: {'patient_id': r['patient_id'], 'recorded_label': r['recorded_label'], 'models': {}}
                   for r in rows if r['partition'] == 'development_validation'}
    for name in PLAN['candidates']:
        data = [encoded({k: v for k, v in r['values'].items()
                 if name == 'logistic' or not k.startswith('source_blocks:')}) for r in rows]
        vec = DictVectorizer(sparse=True); xtrain = vec.fit_transform([v for v, keep in zip(data, tr) if keep])
        model = make_pipeline(StandardScaler(with_mean=False), LogisticRegression(C=1, max_iter=1000, random_state=1729))
        model.fit(xtrain, y[tr])
        def scores(mask):
            return np.clip(model.predict_proba(vec.transform([v for v, keep in zip(data, mask) if keep]))[:, 1], 1e-7, 1-1e-7)
        rawcal = scores(cal)
        calibrator = LogisticRegression(C=1, max_iter=1000).fit(np.log(rawcal/(1-rawcal)).reshape(-1, 1), y[cal])
        raw = scores(val); calibrated = calibrator.predict_proba(np.log(raw/(1-raw)).reshape(-1, 1))[:, 1]
        payload = pickle.dumps({'vectorizer': vec, 'model': model, 'calibrator': calibrator, 'candidate': name,
                                'code_identity': identity(), 'qualification_digest': digest(qualification)})
        (output/(name+'.pkl')).write_bytes(payload)
        report['models'][name] = {'weights_sha256': hashlib.sha256(payload).hexdigest(), 'artifact_bytes': len(payload),
            'training_feature_count': len(vec.feature_names_), 'raw': metrics(y[val], raw), 'calibrated': metrics(y[val], calibrated)}
        for pid, value in zip(predictions, calibrated): predictions[pid]['models'][name] = float(value)
    write(output/'development-predictions.json', list(predictions.values()))
    report.update(status='research_comparison_fitted', elapsed_seconds=time.perf_counter()-start,
                  predictions_sha256=sha(output/'development-predictions.json'))
    write(output/'run.json', report); return report


def attach(stack, reports):
    stack = Path(stack)
    entries = [{'path': str(Path(root).resolve()), 'sha256': sha(Path(root)/'run.json')} for root in reports]
    # Check before activation; preserve the prior pointer if validation fails.
    result = attachment_status(stack, entries)
    write(stack/'outcome-learning-link.json', {'schema': VERSION, 'reports': entries})
    return result


def attachment_status(stack, entries=None):
    stack = Path(stack); meta = json.loads((stack/'run.json').read_text())
    pointer = stack/'outcome-learning-link.json'
    if entries is None:
        if not pointer.exists(): return {'synthetic': True, 'available': False, 'reports': []}
        link = json.loads(pointer.read_text())
        if link['schema'] != VERSION: raise ValueError('Unknown outcome learning attachment')
        entries = link['reports']
    current = identity(); protected = semantic_learning.protections(); reports = []
    for entry in entries:
        root = Path(entry['path']); r = json.loads((root/'run.json').read_text())
        if sha(root/'run.json') != entry['sha256'] or r['code_identity'] != current:
            raise ValueError('Outcome learning report/code changed')
        if r['source_identity']['snapshot'] != meta['snapshot'] or r['source_identity']['source_digest'] != meta['source_digest']:
            raise ValueError('Outcome learning source changed')
        if r['protected_register'] != protected: raise ValueError('Outcome learning protections changed')
        if sha(root/'qualification.json') != r['qualification_sha256'] or sha(root/'models/run.json') != r['model_report_sha256']:
            raise ValueError('Outcome learning qualification/models changed')
        dataset = Path(r['dataset']); manifest = json.loads((dataset/'manifest.json').read_text())
        if sha(dataset/'manifest.json') != r['dataset_manifest_sha256']: raise ValueError('Outcome learning dataset manifest changed')
        for name, expected in manifest['files'].items():
            if name not in ('features.jsonl','targets.jsonl') or sha(dataset/name) != expected:
                raise ValueError('Outcome learning dataset changed')
        # Inputs must still match, not merely the static warehouse manifest.
        op = baseline.outcomes.prepare(stack, manifest['outcome_parameters'])
        prep = baseline.prepare(stack, {'profile': manifest['profile']['id']}, op)
        if prep['identity'] != r['dataset_identity']: raise ValueError('Outcome learning evidence changed; rebuild required')
        reports.append({'contract': manifest['outcome_parameters']['contract'], 'status': r['status'],
            'counts': r['counts'], 'readiness_gaps': r['readiness_gaps'], 'model_report': json.loads((root/'models/run.json').read_text()),
            'report_path': str(root/'run.json'), 'report_sha256': entry['sha256']})
    return {'schema': VERSION, 'synthetic': True, 'available': True, 'reports': reports,
        'clinical_prediction_authorized': False, 'new_paid_api_calls': 0}


def build(stack, lineage_root, dataset, output):
    """Reuse the safe baseline export; no raw hidden generator data are read."""
    stack, dataset, output = Path(stack), Path(dataset), Path(output)
    if output.exists(): raise ValueError('Fresh outcome-learning directory required')
    manifest = json.loads((dataset/'manifest.json').read_text()); meta = json.loads((stack/'run.json').read_text())
    if manifest.get('synthetic') is not True or manifest['source_identity']['snapshot'] != meta['snapshot'] or manifest['source_identity']['source_digest'] != meta['source_digest']:
        raise ValueError('Outcome learning source snapshot mismatch')
    if manifest['source_identity']['code'] != baseline.code_identity(): raise ValueError('Baseline export code changed')
    policy, lm = learning_queue.lineage(lineage_root, meta); protected = semantic_learning.protections()
    for name, expected in manifest['files'].items():
        if name not in ('features.jsonl', 'targets.jsonl') or sha(dataset/name) != expected: raise ValueError('Outcome learning export changed')
    features, targets = [[json.loads(line) for line in (dataset/name).read_text().splitlines()]
                         for name in ('features.jsonl', 'targets.jsonl')]
    qualification = qualify(features, targets, policy, protected)
    output.mkdir(parents=True); write(output/'qualification.json', qualification)
    model = fit_rows(qualification, output/'models')
    report = {'schema': VERSION, 'synthetic': True, 'code_identity': identity(), 'source_identity': manifest['source_identity'],
        'dataset_identity': manifest['identity'], 'dataset': str(dataset.resolve()), 'dataset_manifest_sha256': sha(dataset/'manifest.json'),
        'lineage_policy_sha256': lm['policy_sha256'], 'protected_register': protected,
        'qualification_sha256': sha(output/'qualification.json'), 'model_report_sha256': sha(output/'models/run.json'),
        'counts': {k: qualification[k] for k in ('patients_assessed', 'eligible_rows', 'component_count', 'partition_class_counts', 'excluded_reason_counts')},
        'status': model['status'], 'readiness_gaps': qualification['readiness_gaps'], 'new_paid_api_calls': 0,
        'clinical_prediction_authorized': False, 'automatic_promotion': False}
    if semantic_learning.protections() != protected: raise ValueError('Protection register changed during build')
    write(output/'run.json', report); return report
