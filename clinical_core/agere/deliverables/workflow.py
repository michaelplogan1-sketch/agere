"""Answer-bound deliverables, synthetic work and manual snapshot monitoring.

No new analytical interpretation or external action dispatch occurs here. The immutable answer
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
    record = json.loads(target.read_text()) if target.exists() else {'steps': [], 'synthetic': True}
    if record.get('program_links'):
        steps = []
        for link in record['steps']:
            try:
                child = json.loads(_file(path, link['context_id']).read_text())
                latest = child['steps'][link['node_index']]
                steps.append({**latest, **{k: link[k] for k in ('index', 'node_index', 'node_id', 'context_id')}})
            except (ValueError, KeyError, IndexError, OSError):
                steps.append({**link, 'status': 'blocked', 'reason': 'Producing workflow is unavailable; open its saved output.'})
        record['steps'] = steps
    return record


def _save(path, key, record):
    target = _file(path, key)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, default=str))
    temporary.replace(target)


def measurement(answer):
    """Retain actual computed outputs, not an invented intervention outcome."""
    return {k: answer[k] for k in ('status', 'headline', 'count', 'value', 'table', 'sections', 'patient_ids', 'missing') if k in answer}


def save_program_workflow(path, key, steps):
    """Aggregate links for inspection; every effect belongs to its child context."""
    with _lock:
        _save(path, key, {'initialized': True, 'synthetic': True, 'context_id': key,
                         'steps': steps, 'program_links': True, 'external_dispatch_performed': False,
                         'new_paid_api_calls': 0, 'known_api_estimate_usd': 0, 'api_cost_known': True})


def work_measurement(path, context):
    """Observed internal simulation states, never patient outcomes or delivered care."""
    items = A.work(path, context)
    rows = [{'item_id': i['id'], 'patient_id': i['patient_id'], 'program': i['program'],
             'revision': i['revision'], 'review_state': i['state'],
             'simulation_status': (i.get('simulation') or {}).get('status', 'not_started')}
            for i in items]
    return {'synthetic': True, 'external_dispatch_performed': False,
            'scope': 'Saved simulated work; completion does not establish actual care or clinical benefit.',
            'total': len(rows), 'completed': sum(i['simulation_status'] == 'complete' for i in rows),
            'needs_attention': sum(i['simulation_status'] == 'needs_attention' for i in rows),
            'cancelled': sum(i['review_state'] == 'cancelled' for i in rows), 'items': rows}


def selected_patients(context, step):
    ids = context['patient_ids']
    if not step.get('selection'):
        return ids, None
    if not A.supports_patient_work(context):
        raise ValueError('Work selection requires an explicitly selected patient table')
    field = step['selection']['output']
    rows = context['answer'].get('table') or []
    if len(rows) != len(ids) or {r.get('patient_id') for r in rows} != set(ids):
        raise ValueError('Work criterion must have one row per saved patient')
    if any(field not in r or r[field] not in (None, 0, 1) for r in rows):
        raise ValueError('Work criterion must be a computed boolean 1/0/NULL for every patient')
    selected = sorted(r['patient_id'] for r in rows if r[field] == 1)
    return selected, {'output': field, 'selected': len(selected),
                      'not_selected': sum(r[field] == 0 for r in rows),
                      'unknown': sum(r[field] is None for r in rows), 'quote': step['selection']['quote']}


def materialize(path, key, *, allow_live=False, generation=None, send=None):
    """Prepare requested work; frontier use requires the caller's live authority.

    Legacy/offline callers retain explicitly labeled local templates. The main
    conversation integration should pass generation='frontier' and allow_live.
    An explicit frontier request never falls back to a local template.
    """
    generation = generation or ('frontier' if allow_live else 'local_template')
    if generation not in ('frontier', 'local_template'):
        raise ValueError('Choose frontier or local_template generation')
    with _lock:
        try:
            c = A.load(path, key, current=True)
        except ValueError as exc:
            historical = A.load(path,key)
            if not (historical.get('stale') or historical.get('invalidated')):
                raise
            # Historical evidence remains readable. Do not silently reuse an
            # old initialized workflow or write new work from invalid premises.
            return {'context_id':key,'initialized':False,
                    'steps':[{**s,'status':'blocked','reason':str(exc)} for s in historical.get('workflow',[])],
                    'new_paid_api_calls':0,'known_api_estimate_usd':0,'api_cost_known':True,
                    'external_dispatch_performed':False,'refresh_required':True}
        saved = read(path, key)
        if saved.get('initialized'):
            # Keep original attempt costs on the steps, but do not charge them
            # again to a conversation that merely reuses this saved workflow.
            return {**saved, 'new_paid_api_calls': 0, 'known_api_estimate_usd': 0,
                    'api_cost_known': True}
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
                incomplete = c['answer'].get('status') != 'answered' or c['answer'].get('missing')
                # A local inspection view can show partial evidence and its gaps.
                # Work and monitoring still require an accepted complete answer.
                inspection_view = s['kind'] == 'deliverable' and generation == 'local_template'
                if incomplete and not inspection_view:
                    s.update(status='blocked', reason='Resolve the incomplete analytical answer first.')
                elif s.get('unresolved'):
                    s.update(status='needs_definition', reason='; '.join(s['unresolved']))
                elif s['kind'] == 'deliverable':
                    s['generation'] = generation
                    if generation == 'frontier':
                        from ..clinical.request_plan import quote_text
                        brief = quote_text(s['quote']) if s.get('quote') else s['title']
                        d = A.compose_frontier(path, {'context_id': key, 'request': brief},
                                               allow_live=allow_live, send=send)
                        reason = ('Frontier-generated view saved from this answer; structural evidence checks passed.' if d['status'] == 'delivered'
                                  else d.get('failure') or 'Generated view failed evidence/number checks. Open the studio to review the failure and costs.')
                    else:
                        from .mock_views import create
                        d = create(path, {'context_id': key, 'template': s['template'], 'request': s['title']})
                        reason = ('Partial evidence view saved with unresolved requirements; patient work remains blocked.'
                                  if incomplete else 'Local template saved from this exact answer; no frontier generation.')
                    s.update(status='ready' if d['status'] == 'delivered' else 'blocked', deliverable_id=d['id'],
                             reason=reason, costs=d['costs'], new_paid_api_calls=d.get('new_paid_api_calls', 0))
                elif s['kind'] == 'simulation':
                    if not c['patient_ids']:
                        s.update(status='needs_definition', reason='This answer has no patient worklist. Select an explicit population before starting patient agents.')
                    else:
                        ids, selection = selected_patients(c, s)
                        if selection:
                            s['selection_summary'] = selection
                        if not ids:
                            s.update(status='needs_definition' if selection and selection['unknown'] else 'no_matches', patients=0,
                                     reason='No confirmed patients meet the computed work criterion; unknowns remain unresolved.')
                            steps.append(s)
                            continue
                        A.create_work(path, {'context_id': key, 'program': s['program'], 'patient_ids': ids,
                                             'owner': 'Synthetic care team', 'note': s['title'] + ' · simulation only; eligibility not established'})
                        s.update(status='ready', patients=len(ids),
                                 reason='Review queue prepared. Open a task to run its simulated agent journey.')
                elif s['kind'] == 'monitor':
                    if s['mode'] == 'work':
                        measured = work_measurement(path, c)
                        if not measured['total']:
                            s.update(status='needs_definition', reason='No saved simulated work exists for this answer yet.')
                        else:
                            s.update(status='watching', schedule='Manual work checks',
                                     scope_label='Simulated work attached to this saved answer', observations=[{
                                         'at': A.now(), 'source': c['source'], 'measurement': measured, 'baseline': True}],
                                     reason='Simulated work baseline recorded; check progress and exceptions as tasks advance.')
                    elif not (c.get('planner_receipt') or {}).get('request_contract'):
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
                  'external_dispatch_performed': False,
                  'new_paid_api_calls': sum(s.get('new_paid_api_calls', 0) for s in steps),
                  'known_api_estimate_usd': sum((s.get('costs') or {}).get('known_api_estimate_usd',
                                                (s.get('costs') or {}).get('total_usd') or 0) for s in steps),
                  'api_cost_known': all((s.get('costs') or {}).get('api_cost_known', True) for s in steps)}
        _save(path, key, record)
        return record


def retry_deliverable(path, request, *, allow_live=False, send=None):
    """Explicit, revision-checked retry; retain failed artifacts and all attempt costs."""
    if set(request) != {'context_id', 'index', 'attempt'} or type(request['index']) is not int or type(request['attempt']) is not int:
        raise ValueError('Choose a saved deliverable and its next attempt number')
    with _lock:
        key, index = request['context_id'], request['index']
        A.load(path, key, current=True)
        record = read(path, key)
        if record.get('program_links'):
            raise ValueError('Open the producing output workspace to retry its deliverable')
        steps = record['steps']
        if not 0 <= index < len(steps):
            raise ValueError('Saved deliverable not found')
        step = steps[index]
        if step['kind'] != 'deliverable' or step.get('status') != 'blocked' or not step.get('deliverable_id'):
            raise ValueError('Only a failed saved deliverable can be retried')
        attempts = copy.deepcopy(step.get('attempts') or [{k: step.get(k) for k in
            ('deliverable_id', 'status', 'reason', 'costs', 'new_paid_api_calls')}])
        if request['attempt'] != len(attempts) + 1:
            raise ValueError('The attempt changed. Reload before retrying.')
        from ..clinical.request_plan import quote_text
        brief = quote_text(step['quote']) if step.get('quote') else step['title']
        d = A.compose_frontier(path, {'context_id': key, 'request': brief +
            f'\nExplicit retry attempt {request["attempt"]} for this saved workflow. Preserve its exact evidence and scope.'},
            allow_live=allow_live, send=send)
        latest = {'deliverable_id': d['id'], 'status': 'ready' if d['status'] == 'delivered' else 'blocked',
                  'reason': 'View saved after retry; earlier attempts and costs are retained.' if d['status'] == 'delivered'
                            else d.get('failure') or 'Generated view failed evidence checks.',
                  'costs': d['costs'], 'new_paid_api_calls': d.get('new_paid_api_calls', 0), 'at': A.now()}
        attempts.append(latest)
        known = sum((a.get('costs') or {}).get('known_api_estimate_usd', (a.get('costs') or {}).get('total_usd') or 0) for a in attempts)
        complete = all((a.get('costs') or {}).get('api_cost_known', True) for a in attempts)
        step.update(latest, attempts=attempts, costs={'known_api_estimate_usd': known, 'api_cost_known': complete,
                    'total_usd': known if complete else None, 'scope': 'All saved generation attempts; local compute is unpriced.'},
                    new_paid_api_calls=sum(a.get('new_paid_api_calls', 0) for a in attempts))
        record.update(new_paid_api_calls=sum(s.get('new_paid_api_calls', 0) for s in steps),
                      known_api_estimate_usd=sum((s.get('costs') or {}).get('known_api_estimate_usd', (s.get('costs') or {}).get('total_usd') or 0) for s in steps),
                      api_cost_known=all((s.get('costs') or {}).get('api_cost_known', True) for s in steps))
        _save(path, key, record)
        return {**record, 'new_paid_api_calls': d.get('new_paid_api_calls', 0),
                'retry_costs': d['costs']}


def refresh(stack, request):
    if set(request) != {'context_id', 'index'} or type(request['index']) is not int:
        raise ValueError('Choose an exact saved monitor')
    key, index = request['context_id'], request['index']
    with _lock:
        c = A.load(stack.path, key)
        if c.get('invalidated'):
            raise ValueError('This answer was invalidated. Re-run the original question before checking a monitor.')
        record = read(stack.path, key)
        if record.get('program_links'):
            raise ValueError('Open the producing output workspace to check its monitor')
        steps = record['steps']
        if not 0 <= index < len(steps) or steps[index]['kind'] != 'monitor':
            raise ValueError('Saved monitor not found')
        step = steps[index]
        if step.get('unresolved') or not step.get('observations'):
            raise ValueError('Define this monitor before checking it')
        if step['mode'] == 'work':
            measured = work_measurement(stack.path, c)
            if fingerprint(measured) != fingerprint(step['observations'][-1]['measurement']):
                step['observations'].append({'at': A.now(), 'source': c['source'], 'measurement': measured,
                                             'changed': True, 'new_paid_api_calls': 0})
                reason = 'Simulated work changed; review completion and exceptions. No actual care outcome is inferred.'
            else:
                reason = 'No change in saved simulated work.'
            step.update(last_checked=A.now(), reason=reason)
            _save(stack.path, key, record)
            return {**record, 'new_paid_api_calls': 0, 'known_api_estimate_usd': 0,
                    'api_cost_known': True}
        if 'patient_scope' not in step:
            raise ValueError('Monitor scope policy changed. Re-run this question to create a monitor with explicit scope.')
        source = A.source_identity(stack.path)
        previous = step['observations'][-1]
        usage={'new_paid_api_calls':0,'known_api_estimate_usd':0,'api_cost_known':True}
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
            original_contract = original['request_contract']
            general = original_contract.get('kind') == 'general_analysis'
            if general:
                from ..clinical import general_analysis as G
                G.sealed(original_contract)
                contract = G.prepare(catalog, c['question'], original_contract['analysis'], population=population)
            else:
                contract = R.compile_request(catalog, c['question'], original_contract['request'], population=population)
            receipt = {**original, 'request_contract': contract, 'new_paid_api_calls': 0,
                       'known_api_estimate_usd': 0, 'api_cost_known': True}
            if general:
                result = G.execute(stack, c['question'], G.graph_for(contract), receipt,
                                   population=population, economics=S.economics)
            else:
                result = S.execute_contract(stack, c['question'], S.graph_for(contract), receipt, population=population)
            econ=result.get('planning_economics') or {}
            calls=result.get('new_paid_api_calls',0)
            known=econ.get('known_api_estimate_usd',econ.get('new_api_estimate_usd'))
            total=econ.get('new_api_estimate_usd',known)
            usage={'new_paid_api_calls':calls,'known_api_estimate_usd':known or 0,
                   'api_cost_known':econ.get('api_cost_known',True) and (total is not None or not calls)}
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
        return {**record, **usage}
