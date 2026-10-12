"""Declared service-resource and release-time scenarios; no live dispatch."""
from pathlib import Path
import hashlib
import json
import sqlite3
from ...contracts import digest
from . import work_capacity as base

VERSION = 'service-resource-capacity/1'
ASSUMPTIONS = [
    'Ready times, pool availability, staffing and service capabilities are explicit supplied assumptions, not verified staff skills, licenses or clinical readiness.',
    'Each task needs one worker from one compatible pool for its full supplied service duration. Workers within a pool are identical; no simultaneous multi-resource requirements are modeled.',
    'At each ready/available event, take ready tasks in original internal creation order, skipping tasks with no free compatible worker. Prefer the available worker capable of fewer declared services; ties use pool ID then worker number.',
    'This deterministic dispatch rule is a scenario policy, not an optimal schedule or clinical priority recommendation. New arrivals, deadlines, breaks, interruptions, travel and task dependencies are absent.',
    'Ready time is measured from hypothetical scenario start. Task wait begins at ready time; not-yet-ready time is not counted as queue wait or elapsed patient waiting.',
    'Unsupported or zero-staff tasks stay explicitly blocked. A partial schedule cannot claim completion of the whole selected batch.',
    'The one-worker-per-pool baseline retains declared capabilities, pool start times and disabled pools. The separate one-pooled-worker reference ignores capability restrictions and assumes availability at minute zero; it may be infeasible.',
    'Low, central and high supplied durations are separate scenarios, not probability or confidence bounds. Different dispatch decisions can change between scenarios.',
    'Selection represents proposed internal work, not inferred outstanding clinical care. No assignments, appointments, task changes, service reservations or external actions are performed.'
]


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), base.identity()])


def pools(value, service_ids):
    if not isinstance(value, list) or not 1 <= len(value) <= 32: raise ValueError('One through 32 explicit resource pools required')
    result = []
    for p in value:
        base.care.exact(p, 'id name workers available_from_minute service_ids assumption_source')
        row = {k:base.care.text(p[k], k, 2000 if k == 'assumption_source' else 150) for k in ('id','name','assumption_source')}
        row['workers'] = base.integer(p['workers'], 'Pool workers', 0, 128)
        row['available_from_minute'] = base.integer(p['available_from_minute'], 'Pool start minute', 0, 10080)
        row['service_ids'] = base.care.strings(p['service_ids'], 'pool services', 200, empty=False)
        if service_ids is not None and set(row['service_ids'])-set(service_ids): raise ValueError('Pool service must be a declared buyer service')
        result.append(row)
    if len({p['id'] for p in result}) != len(result): raise ValueError('Resource pool IDs must be distinct')
    if sum(p['workers'] for p in result) > 128: raise ValueError('Complete scenario exceeds 128-worker calculation bound')
    return sorted(result, key=lambda p:p['id'])


def simulate(jobs, resource_pools, horizon):
    base.integer(horizon, 'Horizon minutes', 1, 10080)
    if not isinstance(jobs, list) or len(jobs) > base.MAX_TASKS: raise ValueError('Complete bounded task batch required')
    ids = [j['id'] for j in jobs]
    if len(set(ids)) != len(ids): raise ValueError('Duplicate task in resource scenario')
    for j in jobs:
        base.integer(j['duration_minutes'], 'Task duration', 1, 43200)
        base.integer(j['ready_minute'], 'Task ready minute', 0, 10080)
    # The pure simulator also validates the resource shape, including zero staffing.
    resources = pools(resource_pools, None)
    workers = [{'pool_id':p['id'], 'worker_number':i+1, 'free_at':p['available_from_minute'], 'services':p['service_ids']}
               for p in resources for i in range(p['workers'])]
    pending = []; blocked = []
    for j in jobs:
        compatible = [p for p in resources if j['service_id'] in p['service_ids']]
        reason = 'no_declared_capable_pool' if not compatible else 'no_staff_in_capable_pools' if not any(p['workers'] for p in compatible) else None
        if reason: blocked.append({**j, 'reason':reason})
        else: pending.append(j)
    schedule = []; minute = 0
    while pending:
        free = [w for w in workers if w['free_at'] <= minute]
        assigned = set()
        for j in pending:
            if j['ready_minute'] > minute: continue
            matches = [w for w in free if j['service_id'] in w['services']]
            if not matches: continue
            w = min(matches, key=lambda x:(len(x['services']), x['pool_id'], x['worker_number']))
            finish = minute+j['duration_minutes']; w['free_at'] = finish
            schedule.append({**j, 'pool_id':w['pool_id'], 'worker_number':w['worker_number'],
                             'start_minute':minute, 'finish_minute':finish, 'queue_wait_minutes':minute-j['ready_minute']})
            free.remove(w); assigned.add(j['id'])
        pending = [j for j in pending if j['id'] not in assigned]
        if pending:
            events = [j['ready_minute'] for j in pending if j['ready_minute'] > minute] + [w['free_at'] for w in workers if w['free_at'] > minute]
            if not events: raise ValueError('Resource scenario cannot advance; no task silently discarded')
            minute = min(events)
    # Restore the selected internal FIFO order for source/task inspection, not execution time order.
    order = {j['id']:i for i,j in enumerate(jobs)}; schedule.sort(key=lambda j:order[j['id']])
    total = sum(j['duration_minutes'] for j in jobs)
    processed = sum(min(j['duration_minutes'], max(0,horizon-j['start_minute'])) for j in schedule)
    available = sum(p['workers']*max(0,horizon-p['available_from_minute']) for p in resources)
    result = {'status':'partially_blocked' if blocked else 'simulated' if jobs else 'empty_batch',
              'task_count':len(jobs), 'workload_minutes':total, 'horizon_minutes':horizon,
              'schedule':schedule, 'blocked_tasks':blocked, 'blocked_count':len(blocked),
              'completed_by_horizon':sum(j['finish_minute'] <= horizon for j in schedule),
              'in_progress_at_horizon':sum(j['start_minute'] < horizon < j['finish_minute'] for j in schedule),
              'ready_waiting_at_horizon':sum(j['ready_minute'] <= horizon <= j['start_minute'] for j in schedule),
              'not_ready_at_horizon':sum(j['ready_minute'] > horizon for j in schedule),
              'remaining_work_minutes':total-processed, 'available_worker_minutes':available,
              'assumed_utilization':processed/available if available else None,
              'schedulable_clearance_minutes':max((j['finish_minute'] for j in schedule), default=0),
              'whole_batch_clearance_minutes':None if blocked else max((j['finish_minute'] for j in schedule), default=0),
              'mean_queue_wait_minutes_schedulable':sum(j['queue_wait_minutes'] for j in schedule)/len(schedule) if schedule else None,
              'max_queue_wait_minutes_schedulable':max((j['queue_wait_minutes'] for j in schedule), default=None)}
    result['pool_usage'] = []
    for p in resources:
        js = [j for j in schedule if j['pool_id']==p['id']]
        used = sum(min(j['duration_minutes'], max(0,horizon-j['start_minute'])) for j in js)
        cap = p['workers']*max(0,horizon-p['available_from_minute'])
        result['pool_usage'].append({'pool_id':p['id'],'tasks_scheduled':len(js),'work_minutes_processed':used,
                                     'available_worker_minutes':cap,'assumed_utilization':used/cap if cap else None})
    return result


def parent(path, key):
    r = base.inspect(path, key)
    if not r['current']: raise ValueError('Original capacity scenario is stale: '+r['stale_reason'])
    return {k:v for k,v in r.items() if k not in ('current','stale_reason')}


def validate(request, original):
    base.care.exact(request, 'parent_id question assumption_source pools ready_minutes')
    if request['parent_id'] != original['id']: raise ValueError('Exact original capacity question required')
    out = {k:base.care.text(request[k], k, 2000 if k=='assumption_source' else 1000) for k in ('question','assumption_source')}
    out['parent_id'] = original['id']
    out['pools'] = pools(request['pools'], [s['id'] for s in original['snapshot']['services']])
    ready = request['ready_minutes']
    if not isinstance(ready, dict) or set(ready) != set(original['selected_task_ids']):
        raise ValueError('Explicit ready minutes required for every selected task and no others')
    out['ready_minutes'] = {k:base.integer(ready[k], 'Task ready minute', 0, 10080) for k in sorted(ready)}
    return out


def calculate(original, request):
    request = validate(request, original)
    services = sorted(original['request']['durations'])
    baseline_pools = [{**p,'workers':min(1,p['workers'])} for p in request['pools']]
    unconstrained = [{'id':'pooled-reference','name':'Unrestricted single-worker reference','workers':1,
                      'available_from_minute':0,'service_ids':services,'assumption_source':'Hypothetical relaxed reference; all service skills and availability assumed'}]
    selected = {t['id']:t for t in original['snapshot']['tasks']}
    comparisons = {}
    for case in ('low','central','high'):
        jobs = [{'id':key,'service_id':selected[key]['service_id'],'patient_id':selected[key]['patient_id'],
                 'duration_minutes':original['request']['durations'][selected[key]['service_id']][case],
                 'ready_minute':request['ready_minutes'][key]} for key in original['selected_task_ids']]
        comparisons[case] = {'proposed':simulate(jobs,request['pools'],original['request']['horizon_minutes']),
                             'one_worker_per_active_pool':simulate(jobs,baseline_pools,original['request']['horizon_minutes']),
                             'unrestricted_single_worker_reference':simulate(jobs,unconstrained,original['request']['horizon_minutes'])}
    row = {'schema':VERSION,'implementation':identity(),'buyer_id':original['buyer_id'],
           'parent_id':original['id'],'parent_hash':digest(original),'original_question':original['question'],
           'question':request['question'],'request':request,'selected_task_ids':original['selected_task_ids'],
           'unselected_task_ids':original['unselected_task_ids'],'unique_patients':original['unique_patients'],
           'duration_assumptions':original['request']['durations'],'duration_assumption_source':original['request']['assumption_source'],
           'comparisons':comparisons,'assumptions':ASSUMPTIONS,
           'method':'Discrete-event ready-task FIFO with declared service compatibility and most-specific-free-worker tie break',
           'method_sources':base.METHOD_SOURCES,'optimality_claimed':False,
           'authority':'supplied_operational_scenario','clinical_priority_established':False,
           'tasks_changed':0,'service_slots_reserved':0,'external_actions_executed':0,'new_paid_api_calls':0}
    row['id'] = digest(row)
    return row


def database(path):
    db = sqlite3.connect(Path(path)/'resource-capacity.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY,parent TEXT,buyer TEXT,payload TEXT)')
    for op in ('UPDATE','DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS scenarios_{op} BEFORE {op} ON scenarios BEGIN SELECT RAISE(ABORT,'Resource scenario history is immutable'); END")
    return db


def compare(path, request):
    if not isinstance(request, dict): raise ValueError('Explicit resource scenario required')
    original = parent(path,request.get('parent_id'));row = calculate(original,request);db = database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if parent(path,original['id']) != original: raise ValueError('Original task scope changed during resource comparison')
            db.execute('INSERT OR IGNORE INTO scenarios VALUES (?,?,?,?)',(row['id'],row['parent_id'],row['buyer_id'],json.dumps(row)))
    finally: db.close()
    return row


def inspect(path, key):
    base.care.text(key,'Scenario ID',64);db = database(path)
    try: found = db.execute('SELECT payload FROM scenarios WHERE id=?',(key,)).fetchone()
    finally: db.close()
    if not found: raise ValueError('Unknown resource scenario')
    row = json.loads(found[0])
    if digest({k:v for k,v in row.items() if k!='id'}) != key: raise ValueError('Resource scenario integrity mismatch')
    try:
        if row['implementation'] != identity(): raise ValueError('Resource calculation implementation changed')
        original = parent(path,row['parent_id'])
        if digest(original) != row['parent_hash']: raise ValueError('Original capacity scenario changed')
        if calculate(original,row['request']) != row: raise ValueError('Resource calculation no longer reproduces')
        return {**row,'current':True,'stale_reason':None}
    except (ValueError,KeyError,FileNotFoundError) as e:
        return {**row,'current':False,'stale_reason':str(e)}


def history(path, parent_id):
    base.care.text(parent_id,'Parent scenario ID',64);db = database(path)
    try: keys = [r[0] for r in db.execute('SELECT id FROM scenarios WHERE parent=? ORDER BY rowid',(parent_id,))]
    finally: db.close()
    return {'parent_id':parent_id,'scenarios':[inspect(path,key) for key in keys]}
