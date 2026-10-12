"""Question-triggered source tasks, kept separate from the fixed audit sample.

No inferred labels, changed family assignments, reader calls or promotion. Exact
candidate phrases enter the existing source-reader/review path only after the
current acquisition policy has excluded protected and nontraining families.
"""
from collections import Counter
from pathlib import Path
import json
import re
import sqlite3

from ...contracts import digest
from . import learning_queue as queue, need_candidates as candidates
from . import semantic_reader as reader, semantic_learning as learning
from . import semantic_student as student, source_structure as structure

VERSION = 'question-source-learning/1'
LIMITS = [
    'Question-triggered tasks are a selected development lane, not a score-independent audit or a measure of error prevalence.',
    'An exact phrase is a source target, not a relevance label, correct interpretation or resolved clinical question.',
    'Machine proposals and self-reported source reviews retain separate provenance; no clinician validation is inferred.',
    'Fitting still requires a frozen reviewed or explicitly declared machine-supervision dataset, component separation and calibration.',
    'No model release, clinical action or automatic question resolution follows from this task.',
]


def identity():
    return digest([VERSION, queue.sha(__file__), reader.identity(), structure.identity(), student.code_identity()])


def db(path):
    result = sqlite3.connect(Path(path)/'question-learning.sqlite3', timeout=30)
    result.executescript('''CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS immutable_question_learning_update BEFORE UPDATE ON tasks
        BEGIN SELECT RAISE(ABORT,'Learning tasks are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_question_learning_delete BEFORE DELETE ON tasks
        BEGIN SELECT RAISE(ABORT,'Learning tasks are append-only'); END;''')
    return result


def inputs(path, candidate_id):
    root, run, policy, _ = queue.connected(path)
    candidate = candidates.inspect(path, candidate_id)
    meta = json.loads((Path(path)/'run.json').read_text())
    if candidate['binding']['snapshot'] != meta['snapshot']:
        raise ValueError('Question and acquisition warehouse snapshots differ')
    patient = candidate['binding']['patient_id']
    reason = queue.eligibility(patient, policy, run['protection_register'])
    dependency = {'candidate_id': candidate_id, 'candidate_hash': digest(candidate),
        'queue_run_sha256': queue.sha(root/'run.json'), 'source_identity': run['source_identity'],
        'policy_sha256': run['lineage_policy_sha256'], 'protection_register': run['protection_register'],
        'patient_family': policy['patient_families'].get(patient)}
    return candidate, dependency, reason


def preview(path, candidate_id):
    candidate, dependency, reason = inputs(path, candidate_id)
    passages = []
    if reason is None:
        for record in candidate['candidates']:
            for passage in record['passages']:
                source = passage['source']
                target = {k: source[k] for k in ('patient_id', 'record_id', 'field', 'start', 'end')}
                target['concept'] = 'source learning preview'
                try:
                    received, text, _ = reader.source(path, target)
                    if digest(received) != record['record_hash'] or text[target['start']:target['end']] != passage['text']:
                        raise ValueError('Candidate source span changed')
                except ValueError:
                    # Structured retrieval representations are not received text
                    # fields. Do not turn generated display text into a label.
                    continue
                passages.append({'rank': passage['rank'], 'record_id': source['record_id'], 'field': source['field'],
                    'start': source['start'], 'end': source['end'], 'text': passage['text']})
    return {'schema': VERSION, 'candidate_id': candidate_id, 'eligible_for_acquisition': reason is None,
        'exclusion_reason': reason, 'passages': passages, 'original_question': candidate['original_question'],
        'dependency': dependency, 'training_labels_created': 0, 'new_paid_api_calls': 0, 'limits': LIMITS}


def prepare_payload(path, request):
    if not isinstance(request, dict) or set(request) != {'candidate_id', 'passage_rank', 'quote', 'occurrence', 'concept'}:
        raise ValueError('Exact candidate, passage rank, quote, occurrence and concept required')
    quote, concept, occurrence, rank = (request[k] for k in ('quote', 'concept', 'occurrence', 'passage_rank'))
    if (not isinstance(quote, str) or not 1 <= len(quote) <= 2000 or
        not isinstance(concept, str) or not 1 <= len(concept.strip()) <= 150 or
        type(occurrence) is not int or not 0 <= occurrence < 500 or type(rank) is not int or not 1 <= rank <= 8):
        raise ValueError('Bounded exact phrase, concept and zero-based passage occurrence required')
    candidate, dependency, reason = inputs(path, request['candidate_id'])
    if reason: raise ValueError('Learning acquisition excluded: '+reason)
    found = [(r, p) for r in candidate['candidates'] for p in r['passages'] if p['rank'] == rank]
    if len(found) != 1: raise ValueError('Select a passage belonging to this exact candidate receipt')
    record, passage = found[0]
    matches = [m.start() for m in re.finditer('(?='+re.escape(quote)+')', passage['text'])]
    if occurrence >= len(matches): raise ValueError('Exact phrase occurrence not found in candidate passage')
    source = passage['source']; start = source['start']+matches[occurrence]
    target = {k: source[k] for k in ('patient_id', 'record_id', 'field')}
    target.update(start=start, end=start+len(quote), concept=concept.strip())
    received, text, meta = reader.source(path, target)
    if (digest(received) != record['record_hash'] or
        text[source['start']:source['end']] != passage['text'] or text[start:start+len(quote)] != quote):
        raise ValueError('Candidate passage no longer matches the received source')
    packet = reader.packet(received, text, target, meta['snapshot']['as_of'])
    reason = None
    try: student.model_text(packet)
    except ValueError as exc: reason = str(exc)
    return {'schema': VERSION, 'implementation': identity(), 'request': request, 'dependency': dependency,
        'receipt_id': candidate['binding']['receipt_id'], 'need_id': candidate['binding']['need_id'],
        'original_question': candidate['original_question'], 'target': target, 'source_hash': digest(received),
        'context': packet, 'source_structure': structure.locate(text, target['start'], target['end']),
        'selection_lane': 'question_triggered_development',
        'qualification': {'acquisition_partition': 'train', 'protected_family_excluded': True,
            'exact_source_verified': True, 'complete_received_field': packet['context_complete'],
            'student_input_contract_ready': reason is None, 'student_input_rejection': reason,
            'labels': None, 'human_reviewed': False, 'fit_eligible_at_creation': False,
            'calibration_qualified': False, 'automatic_acceptance': False},
        'need_binding_changed': False, 'training_labels_created': 0, 'new_paid_api_calls': 0, 'limits': LIMITS}


def prepare(path, request):
    row = prepare_payload(path, request); row['id'] = digest(row)
    # Recheck the entire provenance after preparing source context, before save.
    if inputs(path, request['candidate_id'])[1] != row['dependency']:
        raise ValueError('Question learning inputs changed during preparation')
    database = db(path)
    try:
        with database:
            existing = database.execute('SELECT payload FROM tasks WHERE id=?', (row['id'],)).fetchone()
            if existing and json.loads(existing[0]) != row: raise ValueError('Learning task identity collision')
            database.execute('INSERT OR IGNORE INTO tasks VALUES (?,?)', (row['id'], json.dumps(row, allow_nan=False)))
    finally: database.close()
    return row


def inspect(path, key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key): raise ValueError('Exact learning task ID required')
    database = db(path)
    try: saved = database.execute('SELECT payload FROM tasks WHERE id=?', (key,)).fetchone()
    finally: database.close()
    if saved is None: raise ValueError('Unknown question learning task')
    row = json.loads(saved[0])
    if row['id'] != key or digest({k:v for k,v in row.items() if k != 'id'}) != key:
        raise ValueError('Learning task integrity changed')
    current = prepare_payload(path, row['request'])
    if current != {k:v for k,v in row.items() if k != 'id'}:
        raise ValueError('Learning task source, question or qualification dependencies changed')
    database = reader.db(path)
    try:
        readings = [json.loads(raw) for raw, in database.execute('SELECT payload FROM readings WHERE patient=?', (row['target']['patient_id'],))]
    finally: database.close()
    relevant = [r for r in readings if r['target'] == row['target'] and r['source_hash'] == row['source_hash'] and r['reader_identity'] == reader.identity()]
    return {**row, 'status': 'current', 'readings': [reader.inspect(path, r['id']) for r in relevant]}


def listing(path):
    database = db(path)
    try: ids = [r[0] for r in database.execute('SELECT id FROM tasks ORDER BY rowid DESC')]
    finally: database.close()
    rows = []
    for key in ids[:12]:
        try:
            r = inspect(path, key)
            rows.append({k:r[k] for k in ('id', 'status', 'original_question', 'target', 'qualification')})
        except (ValueError, FileNotFoundError, KeyError) as exc:
            rows.append({'id': key, 'status': 'stale', 'reason': str(exc)[:200]})
    return {'rows': rows, 'total': len(ids), 'limit': 12, 'new_paid_api_calls': 0}


def readiness(path):
    if not (Path(path)/'learning-queue-link.json').exists():
        return {'status': 'not_attached', 'automatic_acceptance': False, 'limits': LIMITS}
    _, run, policy, _ = queue.connected(path)
    exclusions = Counter(queue.eligibility(pid, policy, run['protection_register']) or 'eligible_acquisition_training'
                         for pid in policy['patient_families'])
    return {'schema': VERSION, 'status': 'current', 'source_identity': run['source_identity'],
        'family_policy_sha256': run['lineage_policy_sha256'], 'patient_eligibility': dict(exclusions),
        'queue': {k:run[k] for k in ('selected', 'selected_patients', 'selected_families', 'selection_counts', 'selected_streams', 'counts')},
        'source_reviews': learning.audit(path), 'attached_students': student.status(path),
        'question_tasks': listing(path), 'automatic_acceptance': False, 'new_paid_api_calls': 0,
        'qualification_boundary': 'Current acquisition eligibility is not qualified model performance. Older warehouse results do not transfer automatically.',
        'limits': LIMITS}
