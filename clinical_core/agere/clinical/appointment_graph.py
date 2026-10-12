"""Compose the frozen first-booking observation contract with scoped analyses.

No model labels or experiment files are changed. Historical receipt cutoffs use
the same assessor; current-snapshot work stays on the existing program adapter.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
import sqlite3

from ...contracts import digest
from . import appointment_followthrough as A, evidence_threads as T, case_passes

VERSION = 'appointment-graph/1'
FILTERS = ('all', 'explicit_fulfilled', 'explicit_no_show', 'explicit_cancelled', 'unresolved')
TERMINAL = frozenset(FILTERS[1:4])
LIMITS = [*A.LIMITS,
    'This operation retains the first-booking endpoint, beginning 2026-01-01; it does not answer every-appointment or arbitrary event-window questions.',
    'Receipt cutoff filters received evidence, not discarded historical versions. Unknown receipt dates remain unresolved.',
    'Nonmatching explicit dispositions are documentary complements, never clinical negatives. Unresolved dispositions remain unknown when selecting an explicit status.',
    'Historical observations cannot feed current-snapshot work. Current work uses the existing documentary review adapter; no appointments are scheduled.']


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), A.identity()])


def validate(p, as_of):
    if not isinstance(p, dict) or set(p) != {'available_by', 'filter'}:
        raise ValueError('Appointment review requires an explicit receipt cutoff and disposition filter')
    if not A.O.day(p['available_by']) or p['available_by'] > as_of:
        raise ValueError('Appointment receipt cutoff must be a literal date no later than the snapshot')
    if p['filter'] not in FILTERS:
        raise ValueError('Unknown appointment disposition filter')
    return dict(p)


def dependency(path):
    """Exact relevant source bytes, including deletions and new unknowns.

    One ordered stream scan protects aggregate cache replay even if run metadata
    is unchanged. Do not invalidate on unrelated model fits or work events.
    """
    db, meta = T.connect(path)
    try:
        db.execute('BEGIN')
        roster = [r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')]
        sources = hashlib.sha256()
        count = 0
        for row in db.execute("SELECT id,patient,payload FROM records WHERE stream='appointments' ORDER BY id"):
            sources.update(json.dumps(list(row), ensure_ascii=False).encode())
            sources.update(b'\n')
            count += 1
        return digest([identity(), meta, roster, count, sources.hexdigest()])
    finally:
        db.close()


def build(path, patients, parameters):
    db, meta = T.connect(path)
    try:
        db.execute('BEGIN')
        p = validate(parameters, meta['snapshot']['as_of'])
        ids = sorted(set(patients))
        universe = {r[0] for r in db.execute('SELECT id FROM patients')}
        if not set(ids) <= universe:
            raise ValueError('Unknown appointment population patient')
        rows = []
        for pid in ids:
            records = [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM records WHERE patient=? AND stream='appointments' ORDER BY id", (pid,))]
            rows.append(A.assess(pid, records, p['available_by']))
        result = {'schema': VERSION, 'synthetic': True, 'code_identity': identity(),
            'source': meta, 'parameters': p, 'contract': A.CONTRACT,
            'patient_ids': ids, 'patients': rows, 'limits': LIMITS,
            'clinical_execution_authorized': False, 'new_paid_api_calls': 0}
        result['id'] = digest(result)
        return result
    finally:
        db.close()


def location(path, key):
    # Reuse the frozen receipt's identifier validation, in a separate namespace.
    return A.location(path, key).parent.parent / 'appointment-graph' / (key + '.json')


def prepare(path, patients, parameters):
    row = build(path, patients, parameters)
    target = location(path, row['id'])
    if not target.exists():
        case_passes.atomic_write(target, row)
    return row


def inspect(path, key):
    target = location(path, key)
    if not target.exists():
        raise ValueError('Unknown appointment graph receipt')
    row = json.loads(target.read_text())
    if row.get('id') != key or digest({k:v for k,v in row.items() if k != 'id'}) != key:
        raise ValueError('Appointment graph receipt integrity failed')
    if build(path, row['patient_ids'], row['parameters'])['id'] != key:
        raise ValueError('Appointment graph evidence or contract changed; prepare again')
    return row


def membership(path, patients, parameters):
    row = prepare(path, patients, parameters)
    selected, unknown = set(), set()
    state_filter = parameters['filter']
    for patient in row['patients']:
        pid, status = patient['patient_id'], patient['status']
        if state_filter == 'all' or status == state_filter or (state_filter == 'unresolved' and status not in TERMINAL):
            selected.add(pid)
        elif state_filter != 'unresolved' and status not in TERMINAL:
            unknown.add(pid)
    current_review = None
    if parameters['available_by'] == row['source']['snapshot']['as_of'] and row['patient_ids']:
        # Existing source inspector/navigation/work remains the current adapter.
        legacy = A.prepare(path, {'patient_ids': row['patient_ids']})
        if legacy['patients'] != row['patients']:
            raise ValueError('Appointment evidence changed during graph preparation')
        current_review = legacy['id']
    details = {'appointment_graph_id': row['id'], 'appointment_review_id': current_review,
        'parameters': parameters, 'contract': A.CONTRACT,
        'patients_evaluated': len(row['patients']), 'patient_ids': row['patient_ids'],
        'selected_patient_ids': sorted(selected), 'unknown_patient_ids': sorted(unknown),
        'status_counts': dict(Counter(p['status'] for p in row['patients'])),
        'source_count': sum(len(p['sources']) for p in row['patients']),
        'patients': [{k:v for k,v in p.items() if k != 'sources'} |
            {'source_count':len(p['sources'])} for p in row['patients']],
        'authority': 'received_source_review_only', 'limits': LIMITS,
        'clinical_execution_authorized': False, 'new_paid_api_calls': 0}
    return selected, unknown, details


def work_binding(path, details, question):
    """Recheck the exact receipts before handing membership to existing work."""
    refs = []
    for detail in details:
        row = inspect(path, detail['appointment_graph_id'])
        if row['parameters']['available_by'] != row['source']['snapshot']['as_of']:
            raise ValueError('Historical appointment evidence cannot feed current-snapshot work')
        refs.append({'receipt_id':row['id'], 'parameters':row['parameters'],
                     'selected_patient_ids':detail['selected_patient_ids'],
                     'unknown_patient_ids':detail['unknown_patient_ids']})
    return {'original_question':question, 'appointment_observations':refs,
            'authority':'documentary_review_only_not_clinical_need'}


def prepare_work(stack, request):
    """Follow one saved graph observation into the existing persistent work path."""
    from . import analysis_graph, program_work
    if not isinstance(request,dict) or set(request) != {'result_id','node_id','patient_id'}:
        raise ValueError('Exact graph result, appointment node and patient required')
    location(stack.path, request['result_id'])  # Validate digest syntax.
    with sqlite3.connect(Path(stack.path)/'stack.sqlite3') as db:
        saved = db.execute('SELECT payload FROM result_cache WHERE id=?', (request['result_id'],)).fetchone()
    if saved is None:
        raise ValueError('Unknown saved graph result')
    graph = json.loads(saved[0])
    if not graph.get('question'):
        raise ValueError('An original question must be bound before creating question-linked work')
    current = analysis_graph.execute(stack, graph['graph'], question=graph['question'])
    if current['result_id'] != request['result_id']:
        raise ValueError('Graph dependencies changed; reanswer the original question before creating work')
    detail = next((r['result'] for r in current['results'] if r['node']['id']==request['node_id']
                   and r['node']['operation']=='appointment_followthrough'), None)
    if detail is None or request['patient_id'] not in detail['selected_patient_ids']:
        raise ValueError('Patient must belong to the selected appointment observation')
    binding = work_binding(stack.path, [detail], graph['question'])
    binding.update(graph_result_id=current['result_id'], node_id=request['node_id'])
    proposal = program_work.prepare(stack.path, A.PROGRAM, [request['patient_id']],
        input_membership={'question_context':binding})
    # Recheck evidence after the adapter has constructed the proposal.
    inspect(stack.path, detail['appointment_graph_id'])
    return proposal
