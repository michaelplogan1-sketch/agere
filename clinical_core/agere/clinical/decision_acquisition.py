"""Local evidence requests attached to existing question-owned work.

Record availability is documentary state, never clinical resolution. No request
is sent and no original answer, task or date window is changed here.
"""
from ...contracts import digest
from . import question_decision as Q, record_context, clinical_events
from . import warehouse_interpretation as native, received_reference_graph as graph

PREVIEW = 'decision-dependency-preview/1'
REQUEST = 'decision-acquisition-request/1'
CHECK = 'decision-acquisition-check/1'
AVAILABLE = 'available_requires_interpretation'


def policy():
    return digest([PREVIEW, REQUEST, CHECK, graph.identity(), 'exact-slot-and-source-clocks-v1'])


def environment(retriever, origin):
    row = Q.load(retriever.path, origin['decision_id'])
    task = Q.bound_task(retriever, row, origin['task_id'])
    inventory = Q.received_inventory(retriever.path, row['patient_id'])
    records, meta = record_context.received(retriever.path, row['patient_id'])
    if {r['record_id']:digest(r) for r in records} != {k:v['record_hash'] for k,v in inventory['records'].items()}:
        raise Q.care.work.WorkConflict('Received sources changed during dependency inspection')
    window = row['analysis_window']
    dates = {'basis':'all_observed', 'start':'', 'end':''} if window['basis']=='declared_context' else window
    cutoff = meta['snapshot']['as_of']
    clinical_events.validate_window(dates, cutoff)
    index = native.index(records)
    states = {r['record_id']:clinical_events.window_state(r, dates, cutoff, index) for r in records}
    return row, task, inventory, records, states


def dependency(row, slot, inventory, records, states):
    by_id = {r['record_id']:r for r in records}
    target_id = slot['target_record_id']
    target = by_id.get(target_id) if isinstance(target_id, str) else None
    source = by_id.get(slot.get('source_record_id'))
    status = None
    if slot['kind']=='reference':
        if source is None:
            status = 'reference_source_absent'
        else:
            raw = source.get(slot['field'])
            position = slot['position']
            value = raw[position] if isinstance(raw, list) and isinstance(position, int) and position < len(raw) else raw if position is None and not isinstance(raw, list) else None
            if slot['field'] not in source.get('_warehouse_fields', []) or value != target_id:
                status = 'reference_changed_requires_new_request'
            elif states.get(source['record_id']) is not True:
                status = 'reference_source_unknown_date' if states.get(source['record_id']) is None else 'reference_source_outside_original_window'
            elif not isinstance(target_id, str) or not target_id:
                status = 'invalid_reference_value'
            elif not slot['expected_source_types']:
                status = 'reference_type_unmapped'
            elif target is not None and target['source_table'] not in slot['expected_source_types']:
                status = 'wrong_reference_type'
            elif source['record_id']==target_id:
                status = 'self_reference'
    if status is None:
        status = ('not_received_for_patient' if target is None else
                  'unknown_date' if states.get(target_id) is None else
                  'outside_original_window' if states[target_id] is False else AVAILABLE)
    identity = {'decision_id':row['id'], 'patient_id':row['patient_id'],
                'analysis_window':row['analysis_window'], **slot}
    finding_ids = sorted(f['finding_id'] for f in row.get('finding_sources', [])
                         if {slot.get('source_record_id'), target_id if isinstance(target_id,str) else None} & set(f['records']))
    observation = {'status':status,
        'source_version':inventory['records'].get(slot.get('source_record_id')),
        'target_version':inventory['records'].get(target_id) if isinstance(target_id,str) else None}
    action = ('retrieve_exact_received_record' if status=='not_received_for_patient' else
              'interpret_received_evidence' if status==AVAILABLE else
              'review_original_date_scope' if 'window' in status or 'date' in status else
              'review_reference_contract')
    result = {'id':digest(identity), 'identity':identity, 'observation':observation,
              'requested_action':action, 'direct_finding_ids':finding_ids,
              'clinical_relevance':'unassessed', 'finding_dependency_index_complete':'finding_sources' in row}
    result['version'] = digest(result)
    return result


def preview(retriever, request):
    Q.care.exact(request, 'decision_id task_id')
    from . import graph_decision
    graph_row = Q.load(retriever.path, request['decision_id'])
    if graph_row.get('schema') == graph_decision.MEMBER:
        task = Q.bound_task(retriever, graph_row, request['task_id'])
        inv = Q.received_inventory(retriever.path, graph_row['patient_id'])
        # bound_task already verifies this exact graph member, its service
        # plan and task binding. Every gap uses that one checked observation.
        items = [graph_decision.bound_gap_dependency(graph_row, {'kind':'graph_gap','gap_index':i},current=True)
                 for i in range(len(graph_row['evidence_gaps']))]
        # Recheck source/rule dependencies after building the preview. Saving a
        # request still reruns the complete preview before and after its CAS.
        graph_decision.inspect_member(retriever,graph_row)
        result = {'schema':PREVIEW, **request, 'patient_id':graph_row['patient_id'], 'question':graph_row['question'],
            'analysis_window':graph_row['analysis_window'], 'graph_result_id':graph_row['graph_result_id'],
            'nodes':graph_row['nodes'], 'inventory_id':inv['id'], 'snapshot':inv['snapshot'],
            'task_digest':digest(task), 'owner':task['owner'], 'task_revision':task['revision'],
            'policy':digest([policy(),graph_decision.identity()]), 'dependencies':items, 'new_paid_api_calls':0,
            'scope':'Exact graph request/meaning/timing gaps, retaining each node scope. Local internal review only; no absent target ID, missed care, current service need or external request inferred.'}
        result['id'] = digest(result)
        return result
    row, task, inv, records, states = environment(retriever, request)
    changes = Q.compare_received(row, inv)['changes']
    roots = sorted((set(row['source_versions']['records']) | {c['record_id'] for c in changes}) & set(states))
    linked = graph.build(records, row['patient_id'], inv['snapshot']['as_of'], roots) if roots else {'record_ids':[], 'edges':[], 'issues':[]}
    slots = []
    # Include absent historical inventory IDs explicitly, without asserting that
    # an unselected record was a necessary premise of the original answer.
    record_ids = set(row['source_versions']['records']) | set(linked['record_ids']) | {c['record_id'] for c in changes if c['current'] is None}
    for key in sorted(record_ids):
        slots.append({'kind':'record', 'target_record_id':key})
    for link in linked['edges'] + linked['issues']:
        slots.append({'kind':'reference', 'source_record_id':link['source_id'],
            'target_record_id':link['target_id'], 'field':link['field'],
            'position':link['position'], 'expected_source_types':link['expected_source_types']})
    items = [dependency(row, slot, inv, records, states) for slot in slots]
    items = sorted({d['id']:d for d in items if d['observation']['status'] != AVAILABLE}.values(), key=lambda d:d['id'])
    result = {'schema':PREVIEW, **request, 'patient_id':row['patient_id'], 'question':row['question'],
        'analysis_window':row['analysis_window'], 'inventory_id':inv['id'], 'snapshot':inv['snapshot'],
        'task_digest':digest(task), 'owner':task['owner'], 'task_revision':task['revision'],
        'policy':policy(), 'dependencies':items, 'new_paid_api_calls':0,
        'scope':'Exact documentary reference and date gaps in the proposed follow-up scope. Clinical necessity and completeness are unassessed. Requests remain local and require review by the existing work owner.'}
    result['id'] = digest(result)
    return result


def create(retriever, request):
    Q.care.exact(request, 'decision_id task_id preview_id dependency_id actor')
    origin = {k:request[k] for k in ('decision_id', 'task_id')}
    p = preview(retriever, origin)
    if p['id'] != request['preview_id']:
        raise Q.care.work.WorkConflict('Dependency preview changed; inspect current evidence and work')
    d = next((d for d in p['dependencies'] if d['id']==request['dependency_id']), None)
    if d is None:
        raise ValueError('Dependency does not belong to this saved question and task')
    actor = Q.care.text(request['actor'], 'Internal request actor')
    result = {'schema':REQUEST, **origin, 'patient_id':p['patient_id'], 'question':p['question'],
        'analysis_window':p['analysis_window'], 'dependency':d, 'preview_id':p['id'],
        'inventory_id':p['inventory_id'], 'snapshot':p['snapshot'], 'policy':p['policy'],
        'task_digest':p['task_digest'], 'task_revision':p['task_revision'], 'owner':p['owner'], 'actor':actor,
        'status':'local_evidence_request_saved', 'requested_action':d['requested_action'],
        'changes':[], 'unresolved':[d['observation']['status']], 'new_paid_api_calls':0,
        'external_dispatch_performed':False, 'clinical_execution_authorized':False,
        'scope':p['scope']}
    if preview(retriever, origin) != p:
        raise Q.care.work.WorkConflict('Sources or work changed during request creation')
    return Q.save_followup(retriever.path, result)


def reopen(retriever, request):
    Q.care.exact(request, 'request_id')
    saved = Q.saved_followup(retriever.path, request['request_id'])
    if saved.get('schema') != REQUEST:
        raise ValueError('Saved dependency acquisition request required')
    origin = {k:saved[k] for k in ('decision_id', 'task_id')}
    row, task, inv, records, states = environment(retriever, origin)
    identity = saved['dependency']['identity']
    slot = {k:v for k,v in identity.items() if k not in ('decision_id', 'patient_id', 'analysis_window')}
    from . import graph_decision
    is_graph = slot.get('kind') == 'graph_gap'
    current = graph_decision.gap_dependency(retriever, row, slot) if is_graph else dependency(row, slot, inv, records, states)
    return {'request':saved, 'current_dependency':current, 'inventory_id':inv['id'],
        'snapshot':inv['snapshot'], 'current_owner':task['owner'], 'current_task_digest':digest(task),
        'dependency_unchanged':current['version']==saved['dependency']['version'],
        'work_binding_unchanged':digest(task)==saved['task_digest'],
        'policy_current':saved['policy']==(digest([policy(),graph_decision.identity()]) if is_graph else policy()),
        'request_sent':False, 'clinical_resolution_established':False, 'new_paid_api_calls':0}


def check(retriever, request):
    """Persist a current observation; never close the owner task or old request."""
    Q.care.exact(request, 'request_id')
    view = reopen(retriever, request)
    if not view['policy_current']:
        raise Q.care.work.WorkConflict('Dependency policy changed; create a current request')
    saved = view['request']; current = view['current_dependency']
    result = {'schema':CHECK, 'decision_id':saved['decision_id'], 'task_id':saved['task_id'],
        'acquisition_request_id':saved['result_id'], 'question':saved['question'],
        'dependency_id':current['id'], 'current_dependency':current,
        'inventory_id':view['inventory_id'], 'snapshot':view['snapshot'],
        'current_owner':view['current_owner'], 'current_task_digest':view['current_task_digest'],
        'work_binding_unchanged':view['work_binding_unchanged'],
        'status':current['observation']['status'],
        'changes':[] if view['dependency_unchanged'] else [{'dependency_id':current['id'], 'previous_version':saved['dependency']['version'], 'current_version':current['version']}],
        'unresolved':['Interpretation and clinical relevance remain unassessed.'],
        'task_state_changed':False, 'request_sent':False, 'clinical_resolution_established':False,
        'new_paid_api_calls':0}
    if reopen(retriever, request) != view:
        raise Q.care.work.WorkConflict('Sources or work changed during acquisition check')
    return Q.save_followup(retriever.path, result)
