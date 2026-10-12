"""Paired local statement interpretation on one immutable received passage.

No benchmark targets, human reviews, training, clinical decisions or remote calls.
Each role keeps its original feature contract and its own source-bound receipt.
"""
from collections import Counter
from pathlib import Path
import json
import re
import sqlite3
import time

from ...contracts import digest
from . import semantic_reader as R, semantic_student as S, learning_cohorts as C
from . import windowed_student as W, local_decisions as D

VERSION = 'same-source-reader-comparison/1'
ROLES = ('cohort_lexical', 'cohort_windowed_minilm', 'kev', 'd1')
LIMITS = [
    'Development comparison, not clinical validation or a model promotion.',
    'Agreement is not correctness. Machine teacher supervision is not clinician adjudication.',
    'All roles receive the same exact source passage and marked target. Native readers also use the supplied date metadata; cohort features use text and target only.',
    'This tests statement, time and relation proposals. Retrieval completeness, source-meaning synthesis, planning and service matching are not evaluated.',
    'Training overlap is reported, not removed by matching model outputs. Absence of exact overlap cannot rule out near-duplicates or foundation-model exposure.',
    'No source interpretation establishes current patient state, entitlement, completed care or action authority.',
]


def identity():
    return digest([VERSION, S.sha(__file__), R.identity(), S.code_identity(), C.identity(), W.identity()])


def db(stack):
    con = sqlite3.connect(Path(stack)/'reader-comparisons.sqlite3', timeout=30)
    con.executescript('''CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS immutable_reader_comparison_update BEFORE UPDATE ON comparisons BEGIN SELECT RAISE(ABORT,'Comparison is immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_reader_comparison_delete BEFORE DELETE ON comparisons BEGIN SELECT RAISE(ABORT,'Comparison is immutable'); END;''')
    return con


def model_specs(stack, cohort_id, roles):
    output, _, rows, _ = C.dataset(stack, cohort_id)
    specs = {}
    for role in roles:
        if role == 'cohort_lexical':
            path = output/'models/lexical'; m = S.verify(path)
            fit = json.loads((output/'models/run.json').read_text())
            if (m['kind'] != 'lexical' or m['dataset_digest'] != digest(rows) or
                m['provenance']['teacher_snapshot_sha256'] != S.sha(output/'dataset/manifest.json') or
                fit['models']['lexical']['manifest_sha256'] != S.sha(path/'manifest.json') or
                fit['status'] not in ('partial_shadow_only', 'complete_shadow_only')):
                raise ValueError('Current cohort classifier required')
            specs[role] = {'model_identity': S.sha(path/'manifest.json'), 'supervision': m['supervision'],
                           'release': m['release'], 'features': 'complete source text, target and concept; character n-grams'}
        elif role == 'cohort_windowed_minilm':
            path, m, _ = W.model(stack, cohort_id)
            specs[role] = {'model_identity': S.sha(path/'manifest.json'), 'supervision': m['supervision'],
                           'release': m['release'], 'features': 'complete explicit text windows plus target windows and concept'}
        else:
            manifest = D.verify(R.ROOT/'models'/('kev-4b' if role == 'kev' else 'd1-3b')/'agere-model.json')
            specs[role] = {'model_identity': digest([D.VERSION, manifest, D.sha256(D.__file__)]),
                           'supervision': 'pinned upstream model; see native manifest', 'release': {'status': 'experimental'},
                           'features': 'exact serialized source packet, dates and shared typed question contract'}
    return specs, rows


def availability(stack, body):
    if body != {}: raise ValueError('Empty availability request required')
    con = C.registry(stack)
    try: keys = [r[0] for r in con.execute('SELECT id FROM cohorts ORDER BY rowid DESC')]
    finally: con.close()
    result = []
    for key in keys:
        try:
            specs, _ = model_specs(stack, key, ROLES[:2])
            result.append({'cohort_id': key, 'status': 'available', 'roles': specs})
        except (ValueError, FileNotFoundError, KeyError) as e:
            result.append({'cohort_id': key, 'status': 'unavailable', 'reason': str(e)})
    return {'cohorts': result, 'native_roles': list(ROLES[2:]), 'new_paid_api_calls': 0,
            'global_students_attached': (Path(stack)/'semantic-student-link.json').exists(), 'limits': LIMITS}


def prepare_inputs(stack, body):
    if not isinstance(body, dict) or set(body) != {'target', 'cohort_id', 'roles'}:
        raise ValueError('Exact target, cohort and local roles required')
    roles = body['roles']
    if (not isinstance(roles, list) or not 1 <= len(roles) <= 4 or
        any(not isinstance(x, str) or x not in ROLES for x in roles) or len(set(roles)) != len(roles)):
        raise ValueError('Distinct supported local reader roles required')
    if not isinstance(body['cohort_id'], str) or not re.fullmatch('[a-f0-9]{64}', body['cohort_id']):
        raise ValueError('Exact registered cohort required')
    record, text, meta = R.source(stack, body['target'])
    packet = R.packet(record, text, body['target'], meta['snapshot']['as_of'])
    # Reject partial shared text rather than calling a subset and suggesting parity.
    S.model_text(packet)
    for role in roles:
        if role in ROLES[2:]: R.request(packet, role)
    roles = [r for r in ROLES if r in roles]
    request = {**body, 'roles': roles}
    specs, rows = model_specs(stack, body['cohort_id'], roles)
    fixed = {'schema': VERSION, 'implementation': identity(), 'request': request,
             'source_hash': digest(record), 'source_identity': {k: meta[k] for k in ('source_digest', 'snapshot')},
             'context': packet, 'context_digest': digest(packet), 'models': specs,
             'cohort_dataset_digest': digest(rows)}
    return fixed, rows


def overlap(target, packet, source_hash, rows):
    result = {}
    for part in S.PARTITIONS:
        selected = [r for r in rows if r['partition'] == part]
        result[part] = {
            'same_patient': sum(r.get('patient_id') == target['patient_id'] for r in selected),
            'same_source': sum(r['source_hash'] == source_hash for r in selected),
            'same_model_text': sum(S.model_text(r['input']) == S.model_text(packet) for r in selected),
        }
    return result


def baselines(rows):
    train = [r for r in rows if r['partition'] == 'train']
    if not train: raise ValueError('Training-only majority baseline requires training rows')
    majority = {a: sorted(Counter(r['labels'][a] for r in train).items(), key=lambda x: (-x[1], x[0]))[0][0] for a in R.AXES}
    return {'abstain_all': {'labels': {a: 'unknown' for a in R.AXES}, 'basis': 'No interpretation; abstention reference'},
            'training_majority': {'labels': majority, 'basis': 'Per-axis mode of machine-labelled training rows only; source ignored', 'training_rows': len(train)}}


def run_role(stack, request, role):
    if role in ROLES[2:]: return R.read(stack, request['target'], role, allow_live=False)
    body = {'cohort_id': request['cohort_id'], 'target': request['target'],
            'kind': 'lexical' if role == 'cohort_lexical' else 'windowed_minilm'}
    return (C.read if role == 'cohort_lexical' else W.read)(stack, body)


def assess(variants):
    valid = {k: v['labels'] for k, v in variants.items() if v['status'] == 'completed'}
    axes = {}
    for axis in R.AXES:
        choices = {k: v[axis] for k, v in valid.items()}
        decided = {v for v in choices.values() if v != 'unknown'}
        axes[axis] = {'choices': choices, 'abstaining_roles': [k for k, v in choices.items() if v == 'unknown'],
                      'disagreement': len(set(choices.values())) > 1, 'nonabstaining_disagreement': len(decided) > 1}
    return {'axes': axes, 'completed_roles': len(valid), 'failed_roles': [k for k, v in variants.items() if v['status'] != 'completed'],
            'semantic_accuracy': 'not_assessed', 'retrieval': 'not_run', 'reranking': 'not_run',
            'source_meaning_synthesis': 'not_run', 'planning': 'not_run', 'service_matching': 'not_run'}


def checked_reading(stack, reading_id, fixed, role):
    r = R.inspect(stack, reading_id)
    if (r['target'] != fixed['request']['target'] or r['source_hash'] != fixed['source_hash'] or
        r['source_identity'] != fixed['source_identity'] or r['context'] != fixed['context'] or
        r['model_identity'] != fixed['models'][role]['model_identity'] or r['provider'] != role or
        r['reading']['automatic_acceptance'] or r['reading']['clinical_authority']):
        raise ValueError('Reader returned different evidence, identity or authority')
    labels = r['reading']['labels']
    if set(labels) != set(R.AXES) or any(labels[a] not in R.AXES[a] for a in R.AXES):
        raise ValueError('Invalid reader labels')
    return r


def prepare(stack, body):
    fixed, rows = prepare_inputs(stack, body); key = digest(fixed)
    con = db(stack)
    try: old = con.execute('SELECT payload FROM comparisons WHERE id=?', (key,)).fetchone()
    finally: con.close()
    if old: return {'comparison': inspect(stack, {'id': key}), 'cache_hit': True, 'new_paid_api_calls': 0}
    variants = {}
    for role in fixed['request']['roles']:
        started = time.perf_counter()
        try:
            receipt = run_role(stack, fixed['request'], role)
            r = checked_reading(stack, receipt['id'], fixed, role)
            variants[role] = {'status': 'completed', 'reading_id': r['id'], 'labels': r['reading']['labels'],
                              'cache_hit': receipt['cache_hit'], 'usage': r['usage'], 'student': r.get('student'),
                              'elapsed_seconds': time.perf_counter()-started}
        except (ValueError, RuntimeError, FileNotFoundError) as e:
            variants[role] = {'status': 'failed', 'reason': str(e)[:500], 'elapsed_seconds': time.perf_counter()-started}
    current, _ = prepare_inputs(stack, body)
    if current != fixed: raise ValueError('Source or models changed during comparison; not saved')
    result = {**fixed, 'id': key, 'variants': variants, 'baselines': baselines(rows),
              'training_overlap': overlap(body['target'], fixed['context'], fixed['source_hash'], rows),
              'stage_assessment': assess(variants), 'limits': LIMITS, 'synthetic': True,
              'new_paid_api_calls': 0, 'external_model_calls': 0, 'human_review_created': False,
              'training_performed': False, 'clinical_validated': False, 'automatic_acceptance': False,
              'model_promoted': False, 'work_created': False}
    con = db(stack)
    try:
        with con: con.execute('INSERT OR IGNORE INTO comparisons VALUES (?,?)', (key, json.dumps(result, allow_nan=False)))
        saved = json.loads(con.execute('SELECT payload FROM comparisons WHERE id=?', (key,)).fetchone()[0])
    finally: con.close()
    return {'comparison': saved, 'cache_hit': False, 'new_paid_api_calls': 0}


def inspect(stack, body):
    if not isinstance(body, dict) or set(body) != {'id'} or not isinstance(body['id'], str) or not re.fullmatch('[a-f0-9]{64}', body['id']):
        raise ValueError('Exact saved reader comparison required')
    con = db(stack)
    try: old = con.execute('SELECT payload FROM comparisons WHERE id=?', (body['id'],)).fetchone()
    finally: con.close()
    if old is None: raise ValueError('Unknown reader comparison')
    result = json.loads(old[0]); fixed, rows = prepare_inputs(stack, result['request'])
    if result['id'] != body['id'] or digest(fixed) != body['id'] or any(result[k] != v for k, v in fixed.items()):
        raise ValueError('Comparison source or model contract changed')
    for role, variant in result['variants'].items():
        if variant['status'] == 'completed':
            r = checked_reading(stack, variant['reading_id'], fixed, role)
            if variant['labels'] != r['reading']['labels']: raise ValueError('Comparison labels changed')
    if result['stage_assessment'] != assess(result['variants']) or result['baselines'] != baselines(rows):
        raise ValueError('Comparison assessment changed')
    return result
