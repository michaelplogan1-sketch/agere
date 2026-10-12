"""Bind received coverage contexts into existing matching, queue and handoff routes."""
from pathlib import Path
import hashlib
import json
from ...contracts import digest
from . import care_capabilities as care, care_readiness as readiness, coverage_sources as sources


def identity():
    return digest(['coverage-bound-work/1', hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), sources.identity()])


def prepare(path, request):
    if 'coverage_binding' not in request: return care.prepare(path, request)
    care.exact(request, 'buyer_id origin need_tags rationale coverage coverage_binding')
    b = request['coverage_binding']; care.exact(b, 'context_id record_id')
    context = sources.inspect(path, b['context_id']); selected = sources.select(context, b['record_id'])
    src = care.origin(path, request['origin'])
    if src['patient_id'] != context['patient_id']: raise ValueError('Coverage source belongs to another patient')
    cov = request['coverage']; care.coverage(cov)
    if any(cov[k] != v for k, v in selected['routing_fields'].items()):
        raise ValueError('Coverage form differs from its bound report; reload or explicitly detach the source')
    base = {k: v for k, v in request.items() if k != 'coverage_binding'}
    base['coverage'] = {**cov, 'provenance': cov['provenance'] + ' Work adapter ' + identity()}
    plan = care.prepare(path, base)
    receipt = {'plan_id': plan['id'], 'request': request, 'coverage_selection': selected, 'code_identity': identity(),
               'plan_hash': digest(plan), 'clinical_execution_authorized': False, 'verified_benefits': False}
    receipt['id'] = digest(receipt)
    # Core plan provenance has the exact context/record IDs, so a missing sidecar fails closed below.
    db = sources.database(path)
    try:
        with db: db.execute('INSERT OR IGNORE INTO matches VALUES (?,?)', (plan['id'], json.dumps(receipt)))
    finally: db.close()
    return {**plan, 'received_coverage': receipt}


def association(path, plan):
    receipt = sources.load(path, 'matches', plan['id'])
    if receipt is None:
        if plan['coverage']['provenance'].startswith('received-coverage:'):
            raise care.work.WorkConflict('Bound coverage receipt missing; prepare the match again')
        return None
    if digest({k: v for k, v in receipt.items() if k != 'id'}) != receipt['id'] or receipt['code_identity'] != identity() or receipt['plan_hash'] != digest(plan):
        raise care.work.WorkConflict('Coverage matching receipt changed; prepare again')
    b = receipt['request']['coverage_binding']; current = sources.select(sources.inspect(path, b['context_id']), b['record_id'])
    if current != receipt['coverage_selection']: raise care.work.WorkConflict('Coverage evidence changed; prepare again')
    return receipt


def enqueue(path, request):
    plan = care.inspect_plan(path, request['plan_id']); association(path, plan)
    return care.enqueue(path, request)


def task_view(path, key):
    task = readiness.task(path, key); plan = care.inspect_plan(path, task['plan_id'])
    try: receipt = association(path, plan); current = True; reason = None
    except (ValueError, KeyError, FileNotFoundError) as exc:
        receipt = sources.load(path, 'matches', plan['id']); current = False; reason = str(exc)
    db = sources.database(path)
    try: row = db.execute('SELECT payload FROM handoffs WHERE task=? ORDER BY revision DESC LIMIT 1', (key,)).fetchone()
    finally: db.close()
    attached = json.loads(row[0]) if row else None
    return {'task_id': key, 'patient_id': task['patient_id'], 'coverage_receipt': receipt,
            'coverage_dependencies_current': current, 'stale_reason': reason, 'latest_handoff_attachment': attached,
            'handoff_coverage_current': bool(receipt and current and attached and attached['coverage_receipt_id'] == receipt['id'] and attached['task_revision'] == task['revision']),
            'verified_benefits': False, 'clinical_execution_authorized': False}


def task_event(path, request):
    if request.get('action') != 'prepare_handoff': return readiness.task_event(path, request)
    task = readiness.task(path, request['task_id']); plan = care.inspect_plan(path, task['plan_id'])
    receipt = association(path, plan)
    result = readiness.task_event(path, request)
    if receipt is None: return result
    if association(path, care.inspect_plan(path, result['task']['plan_id'])) != receipt:
        raise care.work.WorkConflict('Coverage changed while preparing handoff; refresh the internal packet')
    attachment = {'task_id': task['id'], 'task_revision': result['task']['revision'], 'coverage_receipt_id': receipt['id'],
                  'coverage_selection': receipt['coverage_selection'], 'verified_benefits': False, 'clinical_execution_authorized': False}
    db = sources.database(path)
    try:
        with db:
            old = db.execute('SELECT payload FROM handoffs WHERE task=? AND revision=?', (task['id'], attachment['task_revision'])).fetchone()
            if old and json.loads(old[0]) != attachment: raise care.work.WorkConflict('Existing handoff has different coverage; issue a new preparation')
            db.execute('INSERT OR IGNORE INTO handoffs VALUES (?,?,?)', (task['id'], attachment['task_revision'], json.dumps(attachment)))
    finally: db.close()
    return {**result, 'coverage_attachment': attachment}
