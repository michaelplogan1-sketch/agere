"""Answer-bound deliverables, synthetic work and manual snapshot monitoring.

No new interpretation or external dispatch occurs here. The immutable answer
owns scope; each requested stage records its own success or unresolved needs.
"""
import copy
import json
import sqlite3
import threading
from pathlib import Path
from . import answer_context as A
from .receipts import fingerprint

_lock = threading.RLock()


def _file(path, key):
    A.load(path, key)  # validate identity before using it in a path
    return A.root(path) / key / 'workflow.json'


def read(path, key):
    target = _file(path, key)
    return json.loads(target.read_text()) if target.exists() else {'steps': [], 'synthetic': True}


def _save(path, key, record):
    target = _file(path, key)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, default=str))
    temporary.replace(target)


def measurement(answer):
    """Retain actual computed outputs, not an invented intervention outcome."""
    return {k: answer[k] for k in ('status', 'headline', 'count', 'value', 'table', 'sections', 'patient_ids', 'missing') if k in answer}


def materialize(path, key):
    with _lock:
        c = A.load(path, key, current=True)
        saved = read(path, key)
        if saved.get('initialized'):
            return saved
        steps = []
        # Prepare explicitly requested tasks before freezing deliverable views,
        # while preserving the user's step order in the returned workflow.
        ordered = sorted(enumerate(c.get('workflow', [])), key=lambda x: (x[1]['kind'] != 'simulation', x[0]))
        for index, step in ordered:
            s = copy.deepcopy(step)
            s['index'] = index
            try:
                if s.get('origin'):
                    from ..clinical.workflow_contract import validate_selection
                    validate_selection(s)
                if c['answer'].get('status') != 'answered' or c['answer'].get('missing'):
                    s.update(status='blocked', reason='Resolve the incomplete analytical answer first.')
                elif s.get('unresolved'):
                    s.update(status='needs_definition', reason='; '.join(s['unresolved']))
                elif s['kind'] == 'deliverable':
                    from .mock_views import create
                    d = create(path, {'context_id': key, 'template': s['template'], 'request': s['title']})
                    s.update(status='ready', deliverable_id=d['id'], reason='Interactive synthetic view saved from this exact answer.')
                elif s['kind'] == 'simulation':
                    if not c['patient_ids']:
                        s.update(status='needs_definition', reason='This answer has no patient worklist. Select an explicit population before starting patient agents.')
                    else:
                        A.create_work(path, {'context_id': key, 'program': s['program'], 'patient_ids': c['patient_ids'],
                                             'owner': 'Synthetic care team', 'note': s['title'] + ' · simulation only; eligibility not established'})
                        s.update(status='ready', patients=len(c['patient_ids']),
                                 reason='Review queue prepared. Open a task to run its simulated agent journey.')
                elif s['kind'] == 'monitor':
                    if not (c.get('planner_receipt') or {}).get('request_contract'):
                        s.update(status='needs_definition', reason='An executable shared request is required for exact-definition monitoring.')
                    else:
                        input_population = c['planner_receipt']['request_contract']['population']
                        population = c['patient_ids'] if s['scope'] == 'result' else input_population
                        if s['scope'] == 'result' and not c['patient_ids']:
                            raise ValueError('This result has no patient population to follow')
                        s.update(status='watching', patient_scope=population,
                                 scope_label='Frozen result patients' if s['scope'] == 'result' else 'Original saved input population' if population is not None else 'Entire warehouse query',
                                 schedule='Manual snapshot checks', observations=[{
                                     'at': A.now(), 'source': c['source'], 'measurement': measurement(c['answer']), 'baseline': True}],
                                 reason='Baseline recorded. Check again after the warehouse changes; no causal benefit is inferred.')
            except (ValueError, OSError, sqlite3.Error) as exc:
                s.update(status='blocked', reason=str(exc))
            steps.append(s)
        steps.sort(key=lambda s: s['index'])
        record = {'initialized': True, 'synthetic': True, 'context_id': key, 'steps': steps,
                  'external_dispatch_performed': False, 'new_paid_api_calls': 0}
        _save(path, key, record)
        return record


def refresh(stack, request):
    if set(request) != {'context_id', 'index'} or type(request['index']) is not int:
        raise ValueError('Choose an exact saved monitor')
    key, index = request['context_id'], request['index']
    with _lock:
        c = A.load(stack.path, key)
        if c.get('invalidated'):
            raise ValueError('This answer was invalidated. Re-run the original question before checking a monitor.')
        record = read(stack.path, key)
        steps = record['steps']
        if not 0 <= index < len(steps) or steps[index]['kind'] != 'monitor':
            raise ValueError('Saved monitor not found')
        step = steps[index]
        if step.get('unresolved') or not step.get('observations'):
            raise ValueError('Define this monitor before checking it')
        if 'patient_scope' not in step:
            raise ValueError('Monitor scope policy changed. Re-run this question to create a monitor with explicit scope.')
        source = A.source_identity(stack.path)
        previous = step['observations'][-1]
        if previous['source'] == source:
            step.update(last_checked=A.now(), reason='Same warehouse snapshot. No new observation or outcome change.')
        else:
            from ..clinical import request_plan as R, shared_planner as S
            original = c['planner_receipt']
            if original.get('version') != S.VERSION or original.get('policy_identity') != S.policy_identity():
                raise ValueError('Planner policy changed. Re-run the original question before starting a new monitor.')
            population = step['patient_scope']
            catalog = R.Catalog.load(stack.path)
            if population:
                with sqlite3.connect(f'file:{catalog.target}?mode=ro', uri=True) as db:
                    present = {r[0] for r in db.execute('SELECT patient_id FROM patient')}
                if not set(population) <= present:
                    raise ValueError('Saved patients are missing from the new warehouse. Reconcile the source before checking this monitor.')
            contract = R.compile_request(catalog, c['question'], original['request_contract']['request'], population=population)
            receipt = {**original, 'request_contract': contract, 'new_paid_api_calls': 0,
                       'known_api_estimate_usd': 0, 'api_cost_known': True}
            result = S.execute_contract(stack, c['question'], S.graph_for(contract), receipt, population=population)
            answer = result['answer']
            if answer.get('status') != 'answered' or answer.get('missing'):
                raise ValueError('New snapshot could not produce a complete comparable answer')
            if A.source_identity(stack.path) != source:
                raise ValueError('Warehouse changed during the check. Retry against a stable snapshot.')
            measured = measurement(answer)
            changed = fingerprint(measured) != fingerprint(previous['measurement'])
            step['observations'].append({'at': A.now(), 'source': source, 'measurement': measured, 'changed': changed,
                                         'new_paid_api_calls': result.get('new_paid_api_calls', 0),
                                         'planning_economics': result.get('planning_economics')})
            step.update(last_checked=A.now(), reason='Observed answer changed; investigate the difference.' if changed else 'New snapshot, same computed answer.')
        _save(stack.path, key, record)
        return record
