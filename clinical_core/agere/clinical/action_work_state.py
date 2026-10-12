"""Combined internal work state without collapsing unknowns into clinical approval."""
from ...contracts import digest
from . import care_capabilities as care, care_readiness, coverage_sources, coverage_work, participation_work


def stored_plan(path, key):
    db = care.db_open(path)
    try: plan = care.load(db, 'capability_plans', key)
    finally: db.close()
    if digest({k: v for k, v in plan.items() if k != 'id'}) != plan['id']:
        raise care.work.WorkConflict('Stored matching plan integrity changed')
    return plan


def inspect(path, key):
    task = care_readiness.task(path, key); layers = {}; details = {}
    for name, action in [
        ('source_and_service', lambda: care.inspect_plan(path, task['plan_id'])),
        ('coverage', lambda: coverage_work.task_view(path, key)),
        ('participation', lambda: participation_work.task_view(path, key)),
        ('documentary_review', lambda: care_readiness.snapshot(path, key)),
    ]:
        try: details[name] = action()
        except (ValueError, KeyError, FileNotFoundError) as exc:
            layers[name] = {'state': 'unavailable_or_stale', 'reason': str(exc), 'packet_current': False}
    if 'source_and_service' in details:
        layers['source_and_service'] = {'state': 'dependencies_current', 'reason': 'Saved source evidence, selected service and matching inputs still agree.', 'packet_current': True}
    if 'coverage' in details:
        c = details['coverage']; bound = c['coverage_receipt'] is not None
        layers['coverage'] = {'state': 'source_changed' if not c['coverage_dependencies_current'] else 'received_report_bound' if bound else 'reported_context_without_received_record',
                              'reason': c['stale_reason'] or ('Exact received report is linked; its service date and limits still apply.' if bound else 'Coverage was manually reported; no received coverage record is attached.'),
                              'packet_current': c['coverage_dependencies_current'] and (not bound or c['handoff_coverage_current']),
                              'verified_benefits': False}
    if 'participation' in details:
        p = details['participation']
        layers['participation'] = {'state': 'queue_context_current' if p['queue_participation_current'] else 'queue_refresh_required',
                                  'reason': 'Declarations and clause dependencies are current for the internal queue.' if p['queue_participation_current'] else 'Queue has no current participation context; refresh matching evidence.',
                                  'packet_current': p['handoff_participation_current'], 'verified_participation': False,
                                  'declaration_revision': p['participation']['declaration_revision']}
    if 'documentary_review' in details:
        r = details['documentary_review']; counts = {}
        for route in r['routes']:
            for state, count in route['counts'].items(): counts[state] = counts.get(state, 0) + count
        layers['documentary_review'] = {'state': 'documentary_review_incomplete' if any(route['status'] != 'documentary_dispositions_recorded' for route in r['routes']) else 'documentary_dispositions_recorded',
                                        'reason': 'These are documentary decisions; clinical and benefit authority remains separate.',
                                        'packet_current': r['handoff_readiness_current'], 'counts_by_route': counts}
    # Only offer a local refresh when the exact origin and selected coverage context still validate.
    refreshable = False; refresh_reason = None
    try:
        old = stored_plan(path, task['plan_id']); care.origin(path, old['request']['origin'])
        coverage = coverage_work.association(path, old)
        refreshable = task['state'] != 'cancelled'
        if not refreshable: refresh_reason = 'Cancelled tasks cannot be reopened.'
    except (ValueError, KeyError, FileNotFoundError) as exc: refresh_reason = str(exc)
    current = all(layer['packet_current'] for layer in layers.values()) and task['state'] == 'handoff_prepared_for_review'
    steps = []
    if not refreshable and task['state'] != 'cancelled': steps.append('Revisit the saved answer and explicitly select current source or coverage evidence.')
    if not layers['source_and_service']['packet_current'] or layers.get('participation', {}).get('state') != 'queue_context_current':
        if refreshable: steps.append('Refresh matching evidence for this same service and internal task.')
    if not current: steps.append('After refreshing dependencies, prepare a new internal evidence packet.')
    steps.append('Review unresolved clinical, benefit, participation and patient-goal requirements with the accountable owner.')
    if task['state'] == 'cancelled': steps = ['This internal task is cancelled. It cannot be reopened by refreshing evidence.']
    return {'schema': 'combined-action-work-state/1', 'task_id': key, 'task_revision': task['revision'], 'task_state': task['state'],
            'patient_id': task['patient_id'], 'owner': task['owner'], 'layers': layers,
            'packet_matches_recorded_dependencies': current, 'local_matching_refresh_available': refreshable,
            'refresh_unavailable_reason': refresh_reason, 'next_steps': steps,
            'clinical_execution_authorized': False, 'verified_benefits': False, 'verified_participation': False,
            'scope': 'Internal evidence work status; a current packet is not clinical approval or service delivery.'}


def refresh(path, request):
    care.exact(request, 'task_id expected_revision actor')
    if type(request['expected_revision']) is not int: raise ValueError('Exact task revision required')
    actor = care.text(request['actor'], 'refresh actor'); task = care_readiness.task(path, request['task_id'])
    if task['revision'] != request['expected_revision']: raise care.work.WorkConflict('Task changed; reload before refreshing')
    if task['state'] == 'cancelled': raise care.work.WorkConflict('Cancelled task cannot be refreshed')
    old = stored_plan(path, task['plan_id']); coverage_work.association(path, old)
    receipt = coverage_sources.load(path, 'matches', old['id'])
    # Preserve the original explicit source selection; never replace it with the latest coverage row.
    match_request = receipt['request'] if receipt else old['request']
    plan = participation_work.prepare(path, match_request)
    option = next((o for o in plan['options'] if o['capability']['id'] == task['capability_id']), None)
    if not option or option['status'] == 'blocked':
        raise care.work.WorkConflict('This service no longer has a potentially applicable route; review service alternatives from the saved answer')
    current = care_readiness.task(path, task['id'])
    if current['revision'] != task['revision'] or current['state'] == 'cancelled': raise care.work.WorkConflict('Task changed while refreshing matching evidence')
    result = participation_work.enqueue(path, {'plan_id': plan['id'], 'option_id': option['id'], 'actor': actor,
                                             'participation_context_id': plan['participation_contexts'][option['id']]['id']})
    if result['task']['id'] != task['id']: raise care.work.WorkConflict('Refreshed context did not retain the original internal task')
    return {**result, 'work_state': inspect(path, task['id']), 'paid_calls': 0, 'external_actions_executed': 0}
