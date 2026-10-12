"""Explicit task precedence and finish-to-start lag scenarios; never live dispatch."""
from pathlib import Path
import hashlib
import json
import sqlite3
from ...contracts import digest
from . import resource_capacity as R

VERSION = 'task-precedence-capacity/1'
ASSUMPTIONS = [
    'Every dependency and finish-to-start lag is explicitly supplied for this operational scenario. No clinical prerequisite, care sequence or patient priority is inferred.',
    'A task becomes eligible at the later of its supplied ready time and every predecessor finish plus its supplied lag. Lags consume no worker capacity.',
    'Dependency delay is effective-ready minus original-ready time. Resource queue wait is start minus effective-ready time. Their sum is total delay after original readiness; these are hypothetical task times, not measured patient waiting.',
    'Ready-task dispatch, service compatibility, most-specific-free-worker tie breaks, pool availability and duration assumptions are retained from the original resource scenario. This policy is not an optimal scheduler.',
    'A task with no staffed compatible pool is blocked. Its descendants are also blocked and cannot imply whole-batch completion; all root blockers stay visible.',
    'The no-dependency baseline retains exactly the original resource scenario. It may be infeasible under supplied prerequisites; differences are assumption sensitivity, not benefit, causality or staffing recommendations.',
    'Low, central and high durations are separate supplied scenarios, not probability bounds. No arrivals, appointments, deadlines, simultaneous resources, interruptions or empirical calibration are modeled.',
    'No work state, assignment, service slot, clinical decision or external action changes. The saved question is an immutable, explicitly hypothetical scenario.'
]


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), R.identity()])


def dependencies(value, ids):
    if not isinstance(value, list) or len(value) > 4096: raise ValueError('Complete bounded dependency list required (at most 4096 edges)')
    result = []; seen = set(); predecessors = {key:set() for key in ids}
    for row in value:
        R.base.care.exact(row, 'before_task_id after_task_id lag_minutes reason')
        before = R.base.care.text(row['before_task_id'], 'Predecessor task', 64)
        after = R.base.care.text(row['after_task_id'], 'Dependent task', 64)
        if before not in predecessors or after not in predecessors: raise ValueError('Dependencies must stay within the exact selected task scope')
        if before == after: raise ValueError('A task cannot depend on itself')
        if (before,after) in seen: raise ValueError('Duplicate dependency edge')
        seen.add((before,after));predecessors[after].add(before)
        result.append({'before_task_id':before,'after_task_id':after,
                       'lag_minutes':R.base.integer(row['lag_minutes'],'Dependency lag',0,10080),
                       'reason':R.base.care.text(row['reason'],'Dependency reason',2000)})
    remaining = dict(predecessors); done = set()
    while remaining:
        roots = {key for key,preds in remaining.items() if preds <= done}
        if not roots: raise ValueError('Dependency cycle rejected; a finite acyclic scenario is required')
        done |= roots
        remaining = {key:preds for key,preds in remaining.items() if key not in roots}
    return sorted(result, key=lambda e:(e['before_task_id'],e['after_task_id']))


def simulate(jobs, pools, horizon, edges):
    # Validate using the frozen resource contract and retain an exact no-edge reference.
    baseline = R.simulate(jobs,pools,horizon)
    edges = dependencies(edges,[j['id'] for j in jobs])
    incoming = {j['id']:[] for j in jobs}
    for e in edges: incoming[e['after_task_id']].append(e)
    direct = {j['id']:j['reason'] for j in baseline['blocked_tasks']}
    roots = {key:{key} for key in direct}
    changed = True
    while changed:
        changed = False
        for j in jobs:
            found = set().union(*(roots.get(e['before_task_id'],set()) for e in incoming[j['id']]))
            merged = roots.get(j['id'],set()) | found
            if merged and merged != roots.get(j['id']): roots[j['id']] = merged;changed = True
    blocked = [{**j,'reason':direct.get(j['id'],'blocked_predecessor'),
                'blocking_root_task_ids':sorted(roots[j['id']]),
                'blocked_predecessor_task_ids':sorted(e['before_task_id'] for e in incoming[j['id']] if e['before_task_id'] in roots)}
               for j in jobs if j['id'] in roots]
    resources = R.pools(pools,None)
    workers = [{'pool_id':p['id'],'worker_number':i+1,'free_at':p['available_from_minute'],'services':p['service_ids']}
               for p in resources for i in range(p['workers'])]
    pending = [j for j in jobs if j['id'] not in roots]; scheduled = {}; minute = 0
    def effective(j):
        preds = incoming[j['id']]
        if any(e['before_task_id'] not in scheduled for e in preds): return None
        return max([j['ready_minute']]+[scheduled[e['before_task_id']]['finish_minute']+e['lag_minutes'] for e in preds])
    while pending:
        free = [w for w in workers if w['free_at'] <= minute]; assigned = set()
        for j in pending:
            ready = effective(j)
            if ready is None or ready > minute: continue
            matches = [w for w in free if j['service_id'] in w['services']]
            if not matches: continue
            w = min(matches,key=lambda x:(len(x['services']),x['pool_id'],x['worker_number']))
            finish = minute+j['duration_minutes'];w['free_at'] = finish
            scheduled[j['id']] = {**j,'pool_id':w['pool_id'],'worker_number':w['worker_number'],
                'start_minute':minute,'finish_minute':finish,'effective_ready_minute':ready,
                'dependency_delay_minutes':ready-j['ready_minute'],'queue_wait_minutes':minute-ready,
                'total_delay_after_ready_minutes':minute-j['ready_minute'],
                'predecessor_task_ids':sorted(e['before_task_id'] for e in incoming[j['id']])}
            free.remove(w);assigned.add(j['id'])
        pending = [j for j in pending if j['id'] not in assigned]
        if pending:
            releases = [effective(j) for j in pending]
            events = [t for t in releases if t is not None and t > minute]+[w['free_at'] for w in workers if w['free_at'] > minute]
            if not events: raise ValueError('Dependency scenario cannot advance; no task silently discarded')
            minute = min(events)
    schedule = [scheduled[j['id']] for j in jobs if j['id'] in scheduled]
    total = sum(j['duration_minutes'] for j in jobs)
    processed = sum(min(j['duration_minutes'],max(0,horizon-j['start_minute'])) for j in schedule)
    available = baseline['available_worker_minutes']
    result = {'status':'partially_blocked' if blocked else 'simulated' if jobs else 'empty_batch',
        'task_count':len(jobs),'workload_minutes':total,'horizon_minutes':horizon,
        'schedule':schedule,'blocked_tasks':blocked,'blocked_count':len(blocked),
        'direct_resource_blocked_count':len(direct),'dependency_blocked_count':len(roots)-len(direct),
        'completed_by_horizon':sum(j['finish_minute'] <= horizon for j in schedule),
        'in_progress_at_horizon':sum(j['start_minute'] < horizon < j['finish_minute'] for j in schedule),
        'ready_waiting_at_horizon':sum(j['effective_ready_minute'] <= horizon <= j['start_minute'] for j in schedule),
        'dependency_waiting_at_horizon':sum(j['ready_minute'] <= horizon < j['effective_ready_minute'] for j in schedule),
        'not_ready_at_horizon':sum(j['ready_minute'] > horizon for j in schedule),
        'remaining_work_minutes':total-processed,'available_worker_minutes':available,
        'assumed_utilization':processed/available if available else None,
        'schedulable_clearance_minutes':max((j['finish_minute'] for j in schedule),default=0),
        'whole_batch_clearance_minutes':None if blocked else max((j['finish_minute'] for j in schedule),default=0),
        'mean_queue_wait_minutes_schedulable':sum(j['queue_wait_minutes'] for j in schedule)/len(schedule) if schedule else None,
        'max_queue_wait_minutes_schedulable':max((j['queue_wait_minutes'] for j in schedule),default=None),
        'mean_dependency_delay_minutes_schedulable':sum(j['dependency_delay_minutes'] for j in schedule)/len(schedule) if schedule else None,
        'mean_total_delay_after_ready_minutes_schedulable':sum(j['total_delay_after_ready_minutes'] for j in schedule)/len(schedule) if schedule else None}
    result['pool_usage'] = []
    for p in resources:
        js = [j for j in schedule if j['pool_id']==p['id']]
        used = sum(min(j['duration_minutes'],max(0,horizon-j['start_minute'])) for j in js)
        cap = p['workers']*max(0,horizon-p['available_from_minute'])
        result['pool_usage'].append({'pool_id':p['id'],'tasks_scheduled':len(js),'work_minutes_processed':used,
                                    'available_worker_minutes':cap,'assumed_utilization':used/cap if cap else None})
    return result


def parent(path, key):
    row = R.inspect(path,key)
    if not row['current']: raise ValueError('Original resource scenario is stale: '+row['stale_reason'])
    return {k:v for k,v in row.items() if k not in ('current','stale_reason')}


def calculate(original, request):
    R.base.care.exact(request,'parent_id question assumption_source dependencies')
    if request['parent_id'] != original['id']: raise ValueError('Exact original resource question required')
    request = {'parent_id':original['id'],'question':R.base.care.text(request['question'],'Dependency question',1000),
        'assumption_source':R.base.care.text(request['assumption_source'],'Dependency assumption source',2000),
        'dependencies':dependencies(request['dependencies'],original['selected_task_ids'])}
    comparisons = {}
    for case in ('low','central','high'):
        baseline = original['comparisons'][case]['proposed']
        alljobs = {j['id']:{k:j[k] for k in ('id','service_id','patient_id','duration_minutes','ready_minute')}
                   for j in baseline['schedule']+baseline['blocked_tasks']}
        jobs = [alljobs[key] for key in original['selected_task_ids']]
        if R.simulate(jobs,original['request']['pools'],baseline['horizon_minutes']) != baseline:
            raise ValueError('Original resource baseline does not reproduce')
        proposed = simulate(jobs,original['request']['pools'],baseline['horizon_minutes'],request['dependencies'])
        a=proposed['whole_batch_clearance_minutes'];b=baseline['whole_batch_clearance_minutes']
        comparisons[case] = {'without_dependencies':baseline,'with_dependencies':proposed,
            'completion_count_difference':proposed['completed_by_horizon']-baseline['completed_by_horizon'],
            'whole_batch_minutes_difference':a-b if a is not None and b is not None else None}
    row = {'schema':VERSION,'implementation':identity(),'buyer_id':original['buyer_id'],
        'parent_id':original['id'],'parent_hash':digest(original),'original_question':original['original_question'],
        'resource_question':original['question'],'question':request['question'],'request':request,
        'selected_task_ids':original['selected_task_ids'],'unique_patients':original['unique_patients'],
        'resource_assumptions':original['request'],'duration_assumptions':original['duration_assumptions'],
        'duration_assumption_source':original['duration_assumption_source'],'comparisons':comparisons,
        'assumptions':ASSUMPTIONS,'method':'Discrete-event ready-task FIFO with explicit acyclic finish-to-start constraints',
        'method_sources':original['method_sources'],'optimality_claimed':False,
        'authority':'supplied_operational_scenario','clinical_priority_established':False,
        'tasks_changed':0,'service_slots_reserved':0,'external_actions_executed':0,'new_paid_api_calls':0}
    row['id'] = digest(row)
    return row


def database(path):
    db = sqlite3.connect(Path(path)/'precedence-capacity.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY,parent TEXT,buyer TEXT,payload TEXT)')
    for op in ('UPDATE','DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS scenarios_{op} BEFORE {op} ON scenarios BEGIN SELECT RAISE(ABORT,'Dependency scenario history is immutable'); END")
    return db


def compare(path, request):
    if not isinstance(request,dict): raise ValueError('Explicit dependency scenario required')
    original = parent(path,request.get('parent_id'));row = calculate(original,request);db = database(path)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if parent(path,original['id']) != original: raise ValueError('Original task scope changed during dependency comparison')
            db.execute('INSERT OR IGNORE INTO scenarios VALUES (?,?,?,?)',(row['id'],row['parent_id'],row['buyer_id'],json.dumps(row)))
    finally: db.close()
    return row


def inspect(path, key):
    R.base.care.text(key,'Scenario ID',64);db = database(path)
    try: found = db.execute('SELECT payload FROM scenarios WHERE id=?',(key,)).fetchone()
    finally: db.close()
    if not found: raise ValueError('Unknown dependency scenario')
    row = json.loads(found[0])
    if digest({k:v for k,v in row.items() if k!='id'}) != key: raise ValueError('Dependency scenario integrity mismatch')
    try:
        if row['implementation'] != identity(): raise ValueError('Dependency calculation implementation changed')
        original = parent(path,row['parent_id'])
        if digest(original) != row['parent_hash']: raise ValueError('Original resource scenario changed')
        if calculate(original,row['request']) != row: raise ValueError('Dependency calculation no longer reproduces')
        return {**row,'current':True,'stale_reason':None}
    except (ValueError,KeyError,FileNotFoundError) as e:
        return {**row,'current':False,'stale_reason':str(e)}


def history(path, parent_id):
    R.base.care.text(parent_id,'Parent scenario ID',64);db = database(path)
    try: keys = [r[0] for r in db.execute('SELECT id FROM scenarios WHERE parent=? ORDER BY rowid',(parent_id,))]
    finally: db.close()
    return {'parent_id':parent_id,'scenarios':[inspect(path,key) for key in keys]}
