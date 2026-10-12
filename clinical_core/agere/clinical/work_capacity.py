"""Finite internal-work scenarios; supplied durations are not clinical predictions."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import heapq
import json
import sqlite3
from ...contracts import digest
from . import care_capabilities as care

VERSION = 'internal-work-capacity/1'
MAX_TASKS = 2000
STATES = {'queued_for_internal_review', 'handoff_prepared_for_review'}
METHOD_SOURCES = [
    {'title': 'MIT 6.854: parallel-machine list scheduling and workload bounds',
     'url': 'https://courses.csail.mit.edu/6.854/18/Scribe/s20-ApproxNP/s20-ApproxNP.html'},
    {'title': 'SimPy: shared resources and first-in-first-out waiting',
     'url': 'https://simpy.readthedocs.io/en/stable/simpy_intro/shared_resources.html'}]
ASSUMPTIONS = [
    'Only explicitly selected internal tasks are modeled. They are not appointments or a measured clinical backlog.',
    'All selected tasks are ready at scenario minute zero. Internal creation time determines FIFO order; it is not patient waiting time.',
    'Each task occupies one identical pooled worker without interruption. Every worker can perform every selected task; no skill, licensure or routing feasibility is verified.',
    'No new arrivals, breaks, travel, setup, shared equipment, abandonment, dependencies or urgent priorities are modeled.',
    'Low, central and high durations are three supplied scenarios, not confidence intervals or learned distributions.',
    'Service slot declarations are not worker counts or processing rates. Unknown or expired service capacity is not filled in by this calculation.',
    'A prepared packet may already have had local activity; selection describes proposed additional workload, not an inferred outstanding obligation.',
    'No worker assignment, task transition, appointment reservation, clinical priority, treatment recommendation or external action is performed.'
]


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), care.identity()])


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer from {low} through {high}')
    return value


def task_clock(value):
    try:
        d = datetime.fromisoformat(value)
        if d.tzinfo is None: raise ValueError()
        return d.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        raise ValueError('Internal task creation requires an explicit operational timezone') from None


def snapshot(path, buyer_id):
    buyer_id = care.text(buyer_id, 'buyer ID')
    portfolio = care.portfolio(path, buyer_id)
    tasks = care.task_view(path, buyer_id)['tasks']
    if len(tasks) > MAX_TASKS: raise ValueError('Buyer exceeds the complete 2000-task scenario bound; no tasks were sampled')
    services = {s['id']: s for s in portfolio['capabilities']}
    rows = []
    for t in tasks:
        reasons = []
        if not t['dependencies_current']: reasons.append('Source or service dependencies changed')
        if t['state'] not in STATES: reasons.append('Internal task is not in a selectable review state')
        if t['capability_id'] not in services: reasons.append('Service no longer in current buyer portfolio')
        try: created = task_clock(t['created_at'])
        except ValueError as e: created = None; reasons.append(str(e))
        rows.append({'id': t['id'], 'revision': t['revision'], 'task_hash': digest(t),
                     'patient_id': t['patient_id'], 'service_id': t['capability_id'], 'name': t['name'],
                     'owner': t['owner'], 'state': t['state'], 'created_at': created,
                     'selectable': not reasons, 'exclusion_reasons': reasons})
    rows.sort(key=lambda t: (t['created_at'] or '', t['id']))
    row = {'schema': VERSION, 'implementation': identity(), 'buyer_id': buyer_id,
           'portfolio_id': portfolio.get('id'), 'portfolio_revision': portfolio['revision'],
           'services': portfolio['capabilities'], 'tasks': rows, 'assumptions': ASSUMPTIONS}
    row['id'] = digest(row)
    return row


def simulate(jobs, workers, horizon):
    """Exact FIFO simulation under explicitly supplied finite-batch assumptions."""
    integer(workers, 'Pooled workers', 0, 128); integer(horizon, 'Horizon minutes', 1, 10080)
    if not isinstance(jobs, list) or len(jobs) > MAX_TASKS: raise ValueError('Complete bounded task batch required')
    ids = [j['id'] for j in jobs]
    if len(set(ids)) != len(ids): raise ValueError('Duplicate task in simulated batch')
    for j in jobs: integer(j['duration_minutes'], 'Task duration', 1, 43200)
    total = sum(j['duration_minutes'] for j in jobs)
    result = {'workers': workers, 'horizon_minutes': horizon, 'task_count': len(jobs),
              'workload_minutes': total, 'schedule': [], 'assumed_worker_utilization': 0.0,
              'completed_by_horizon': 0, 'in_progress_at_horizon': 0, 'waiting_at_horizon': len(jobs),
              'remaining_work_minutes': total, 'clearance_minutes': 0 if not jobs else None,
              'mean_wait_minutes': 0 if not jobs else None, 'max_wait_minutes': 0 if not jobs else None,
              'clearance_lower_bound_minutes': 0 if not jobs else None}
    if not workers: return {**result, 'status': 'no_workers' if jobs else 'empty_batch'}
    heap = [(0, i+1) for i in range(workers)]; heapq.heapify(heap)
    for j in jobs:
        start, worker = heapq.heappop(heap); finish = start + j['duration_minutes']
        result['schedule'].append({**j, 'scenario_worker': worker, 'start_minute': start, 'finish_minute': finish})
        heapq.heappush(heap, (finish, worker))
    schedule = result['schedule']
    processed = sum(min(j['duration_minutes'], max(0, horizon-j['start_minute'])) for j in schedule)
    result.update(status='simulated' if jobs else 'empty_batch',
                  completed_by_horizon=sum(j['finish_minute'] <= horizon for j in schedule),
                  in_progress_at_horizon=sum(j['start_minute'] < horizon < j['finish_minute'] for j in schedule),
                  waiting_at_horizon=sum(j['start_minute'] >= horizon for j in schedule),
                  remaining_work_minutes=total-processed, assumed_worker_utilization=processed/(workers*horizon),
                  clearance_minutes=max((j['finish_minute'] for j in schedule), default=0),
                  mean_wait_minutes=sum(j['start_minute'] for j in schedule)/len(jobs) if jobs else 0,
                  max_wait_minutes=max((j['start_minute'] for j in schedule), default=0),
                  clearance_lower_bound_minutes=max(total/workers, max((j['duration_minutes'] for j in jobs), default=0)))
    return result


def validate(request, snap):
    care.exact(request, 'buyer_id snapshot_id task_ids question assumption_source workers horizon_minutes durations')
    if request['buyer_id'] != snap['buyer_id'] or request['snapshot_id'] != snap['id']:
        raise ValueError('Buyer queue or service declarations changed; inspect current work again')
    question = care.text(request['question'], 'Operational question', 1000)
    source = care.text(request['assumption_source'], 'Duration and pooling assumption source', 2000)
    workers = integer(request['workers'], 'Pooled workers', 0, 128)
    horizon = integer(request['horizon_minutes'], 'Horizon minutes', 1, 10080)
    ids = care.strings(request['task_ids'], 'selected tasks', MAX_TASKS, empty=False)
    tasks = {t['id']: t for t in snap['tasks']}
    if any(key not in tasks or not tasks[key]['selectable'] for key in ids):
        raise ValueError('Select only current tasks from this buyer in a review state')
    required = {tasks[key]['service_id'] for key in ids}; durations = request['durations']
    if not isinstance(durations, dict) or set(durations) != required:
        raise ValueError('Explicit duration scenarios required for every selected service and no others')
    for d in durations.values():
        care.exact(d, 'low central high')
        for v in d.values(): integer(v, 'Duration minutes', 1, 43200)
        if not d['low'] <= d['central'] <= d['high']: raise ValueError('Require low <= central <= high duration')
    return {**request, 'task_ids': ids, 'question': question, 'assumption_source': source,
            'workers': workers, 'horizon_minutes': horizon}


def calculate(snap, request):
    request = validate(request, snap)
    selected = [t for t in snap['tasks'] if t['id'] in request['task_ids']]
    comparisons = {}
    for case in ('low', 'central', 'high'):
        jobs = [{'id': t['id'], 'service_id': t['service_id'], 'patient_id': t['patient_id'],
                 'duration_minutes': request['durations'][t['service_id']][case]} for t in selected]
        baseline = simulate(jobs, 1, request['horizon_minutes'])
        proposed = simulate(jobs, request['workers'], request['horizon_minutes'])
        comparisons[case] = {'single_worker_baseline': baseline, 'proposed': proposed,
                            'additional_completed_by_horizon': proposed['completed_by_horizon']-baseline['completed_by_horizon'],
                            'clearance_minutes_saved': None if proposed['clearance_minutes'] is None else baseline['clearance_minutes']-proposed['clearance_minutes']}
    row = {'schema': VERSION, 'implementation': identity(), 'buyer_id': snap['buyer_id'],
           'question': request['question'], 'request': request, 'snapshot': snap,
           'selected_task_ids': [t['id'] for t in selected], 'unselected_task_ids': [t['id'] for t in snap['tasks'] if t not in selected],
           'unique_patients': len({t['patient_id'] for t in selected}),
           'reported_owners': sorted({json.dumps(t['owner'], sort_keys=True) for t in selected}),
           'comparisons': comparisons, 'method': 'FIFO list scheduling on identical hypothetical workers',
           'method_sources': METHOD_SOURCES, 'assumptions': ASSUMPTIONS,
           'time_basis': 'Minutes after a hypothetical batch start; not synthetic clinical dates or elapsed patient wait',
           'authority': 'supplied_operational_scenario', 'clinical_priority_established': False,
           'service_slots_reserved': 0, 'tasks_changed': 0, 'external_actions_executed': 0, 'new_paid_api_calls': 0}
    row['id'] = digest(row)
    return row


def database(path):
    db = sqlite3.connect(Path(path)/'work-capacity.sqlite3', timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY,buyer TEXT,payload TEXT)')
    for op in ('UPDATE', 'DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS scenarios_{op} BEFORE {op} ON scenarios BEGIN SELECT RAISE(ABORT,'Capacity scenario history is immutable'); END")
    return db


def compare(path, request):
    if not isinstance(request, dict): raise ValueError('Explicit scenario required')
    snap = snapshot(path, request.get('buyer_id'))
    row = calculate(snap, request)
    db = database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if snapshot(path, snap['buyer_id']) != snap: raise ValueError('Queue changed during comparison; no scenario saved')
            db.execute('INSERT OR IGNORE INTO scenarios VALUES (?,?,?)', (row['id'], row['buyer_id'], json.dumps(row)))
    finally: db.close()
    return row


def inspect(path, key, snap=None):
    care.text(key, 'Scenario ID', 64)
    db = database(path)
    try: saved = db.execute('SELECT payload FROM scenarios WHERE id=?', (key,)).fetchone()
    finally: db.close()
    if not saved: raise ValueError('Unknown capacity scenario')
    row = json.loads(saved[0]); payload = {k:v for k,v in row.items() if k != 'id'}
    if digest(payload) != key: raise ValueError('Capacity scenario integrity mismatch')
    try:
        if row['implementation'] != identity(): raise ValueError('Capacity calculation implementation changed')
        current = snap or snapshot(path, row['buyer_id'])
        if current != row['snapshot']: raise ValueError('Buyer tasks, source dependencies or service declarations changed')
        if calculate(current, row['request']) != row: raise ValueError('Capacity calculation no longer reproduces')
        return {**row, 'current': True, 'stale_reason': None}
    except (ValueError, KeyError, FileNotFoundError) as e:
        return {**row, 'current': False, 'stale_reason': str(e)}


def history(path, buyer_id):
    snap = snapshot(path, buyer_id); db = database(path)
    try: keys = [r[0] for r in db.execute('SELECT id FROM scenarios WHERE buyer=? ORDER BY rowid', (buyer_id,))]
    finally: db.close()
    return {'buyer_id': buyer_id, 'snapshot_id': snap['id'], 'scenarios': [inspect(path, key, snap) for key in keys]}
