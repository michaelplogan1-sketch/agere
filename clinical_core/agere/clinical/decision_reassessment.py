"""Reanswer a saved question, then recompute its documentary service/work route.

This never carries a claim forward solely because its cited sources are unchanged.
Every replacement finding comes from a current, complete bounded interpretation.
"""
import hashlib
import json
from pathlib import Path
from ...contracts import digest
from . import question_decision as Q

PREPARATION = 'decision-reanswer-context/1'
REVISION = 'decision-reassessment/1'
WORK = 'decision-reassessment-work/1'
GROUP_PREPARATION = 'decision-reanswer-plan/1'
# Explicit compatibility for the already saved, approval-pending v1 context.
# Its scope, evidence, task, context and answer checks still run in full.
LEGACY_IMPLEMENTATION = '1d441d29ea102e77393121830c5819fa27edba32851fdd3a99423c431099e16b'


def identity():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def compatible(p):
    return (p.get('implementation') == identity() or
            p.get('schema') == PREPARATION and p.get('implementation') == LEGACY_IMPLEMENTATION)


def review_question(p):
    return (p['original_question'] + '\n\nEvidence scope: Reassess this saved question in its original date window '
        + json.dumps(p['analysis_window'], sort_keys=True, separators=(',', ':'))
        + '. Current prior-selected sources plus changed received sources and eligible linked records are supplied. '
        + str(len(p['absent_records'])) + ' prior received records are absent; '
        + str(len(p['date_exclusions'])) + ' records have excluded or unknown dates. '
        + 'Do not infer resolution from absence. Report conflicting accounts and missing dependencies. '
        + 'Selection sufficiency and clinical completeness remain unassessed.')


def plan_preview(retriever, origin):
    from collections import Counter
    p = Q.review_preview(retriever, origin)
    inventory = Q.received_inventory(retriever.path, p['patient_id'])
    if inventory['id'] != p['inventory_id']:
        raise Q.care.work.WorkConflict('Received sources changed during reanswer planning')
    excluded = Counter(v['source_table'] for k,v in inventory['records'].items() if k not in p['selected_record_ids'])
    # Use the existing source-plan transport, with a distinct preview contract.
    result = {**p, 'schema':'decision-reanswer-plan-scope/1', 'request':origin,
        'review_question':review_question(p), 'snapshot':inventory['snapshot'],
        'excluded_records':sum(excluded.values()), 'excluded_by_category':dict(sorted(excluded.items())),
        'source_review_preview_id':p['id'], 'implementation':identity()}
    result.pop('id');result['id'] = digest(result)
    return result


def save_group_plan(retriever, scope, groups):
    """Use the existing immutable plan format without editing its frozen engine."""
    plan = {'schema':Q.source_plans.VERSION, 'preview':scope,
        'groups':[{k:v for k,v in g.items() if k != 'context'} | {
            'context_id':g['context']['id'], 'context_hash':digest(g['context']),
            'record_ids':g['context']['record_scope']['record_ids'], 'group_number':i}
            for i,g in enumerate(groups,1)],
        'cross_group_interpretation':'not_assessed', 'status':'declared_record_scope_prepared',
        'new_paid_api_calls':0, 'automatic_actions':0}
    plan['id'] = digest(plan)
    with Q.source_plans.database(retriever.path) as db:
        old = db.execute('SELECT payload FROM plans WHERE preview_id=?', (scope['id'],)).fetchone()
        if old and json.loads(old[0]) != plan:
            raise ValueError('Prepared group selection changed for the same preview')
        if not old:
            db.execute('INSERT INTO plans VALUES (?,?,?)',(plan['id'],scope['id'],json.dumps(plan,allow_nan=False)))
    return plan


def install_source_plans():
    """Scoped adapter for the new preview kind; ordinary frozen plans delegate."""
    base = Q.source_plans.inspect
    if getattr(base, '_reassessment_scopes', False):return
    def inspect(retriever, body):
        Q.care.exact(body,'id')
        if not isinstance(body['id'],str):raise ValueError('Exact source-plan ID required')
        with Q.source_plans.database(retriever.path) as db:
            found = db.execute('SELECT payload FROM plans WHERE id=?',(body['id'],)).fetchone()
        if not found:return base(retriever,body)
        plan = json.loads(found[0])
        if plan['preview'].get('schema') != 'decision-reanswer-plan-scope/1':
            return base(retriever,body)
        if (plan.get('id') != body['id'] or digest({k:v for k,v in plan.items() if k != 'id'}) != body['id']):
            raise ValueError('Source-plan integrity changed')
        if plan_preview(retriever,plan['preview']['request']) != plan['preview']:
            raise ValueError('Source plan or received evidence changed')
        contexts = []
        for g in plan['groups']:
            c = Q.source_plans.C.inspect(retriever.path,g['context_id'])
            if (digest(c) != g['context_hash'] or c['record_scope']['record_ids'] != g['record_ids']
                    or c['omissions'] or not c['dispatch']['source_review_allowed'] or g['request_fit']=='blocked'):
                raise ValueError('Prepared source context changed or incomplete')
            contexts.append(c)
        ids = [rid for g in plan['groups'] for rid in g['record_ids']]
        if len(ids) != len(set(ids)) or sorted(ids) != plan['preview']['selected_record_ids']:
            raise ValueError('Saved source-plan coverage changed')
        return {'plan':plan,'contexts':contexts}
    inspect._reassessment_scopes = True
    Q.source_plans.inspect = inspect


def prepare(retriever, request):
    Q.care.exact(request, 'decision_id task_id preview_id')
    origin = {k:request[k] for k in ('decision_id', 'task_id')}
    p = Q.review_preview(retriever, origin)
    if p['id'] != request['preview_id']:
        raise Q.care.work.WorkConflict('Source review preview changed')
    if not p['can_prepare']:
        raise ValueError('; '.join(p['blockers']))
    # Retain the established date/closure selection; disclose missing records to
    # the reader without inventing their old contents or importing old answers.
    question = review_question(p)
    if len(question) > 1500:
        raise ValueError('Question and reassessment scope exceed the existing question bound')
    groups = Q.source_plans.contexts(retriever, {**p, 'review_question':question})
    records = [rid for g in groups for rid in g['context']['record_scope']['record_ids']]
    if (not groups or sorted(records) != p['selected_record_ids'] or len(records) != len(set(records))
            or any(g['request_fit'] == 'blocked' or g['context']['omissions']
                   or not g['context']['dispatch']['source_review_allowed'] for g in groups)):
        raise ValueError('Reassessment requires complete, nonoverlapping source groups without omissions')
    if len(groups) > 1:
        scope = plan_preview(retriever, origin)
        if scope['source_review_preview_id'] != p['id'] or Q.review_preview(retriever, origin) != p:
            raise Q.care.work.WorkConflict('Sources or work changed during reanswer preparation')
        plan = save_group_plan(retriever, scope, groups)
        return Q.save_followup(retriever.path, {'schema':GROUP_PREPARATION, **origin,
            'preview':p, 'source_plan_id':plan['id'], 'source_plan_digest':digest(plan),
            'review_question':question, 'question':p['original_question'], 'analysis_window':p['analysis_window'],
            'changes':p['arrivals']['changes'], 'unresolved':[], 'implementation':identity(),
            'status':'complete_group_selection_required', 'new_paid_api_calls':0,
            'scope':'Every declared group requires a current saved answer. Cross-group interpretation remains unassessed unless separately reconciled.'})
    c = groups[0]['context']
    if Q.review_preview(retriever, origin) != p:
        raise Q.care.work.WorkConflict('Sources or work changed during reanswer preparation')
    return Q.save_followup(retriever.path, {'schema':PREPARATION, **origin,
        'preview':p, 'context_id':c['id'], 'context_hash':digest(c),
        'review_question':question, 'question':p['original_question'], 'analysis_window':p['analysis_window'],
        'changes':p['arrivals']['changes'], 'unresolved':[], 'implementation':identity(),
        'status':'current_scope_prepared_for_reanswer', 'new_paid_api_calls':0,
        'scope':'One complete declared source group; not a complete chart or clinical adjudication.'})


def binding(retriever, answer, context):
    """Recognize only our exact saved scope; ordinary answers keep their path."""
    with Q.database(retriever.path) as db:
        rows = db.execute("SELECT id FROM question_decision_followups WHERE json_extract(payload,'$.schema')=? AND json_extract(payload,'$.context_id')=?",
                          (PREPARATION, context['id'])).fetchall()
    if not rows:
        return None
    preparations = [Q.saved_followup(retriever.path, r[0]) for r in rows]
    scopes = {(p['question'], digest(p['analysis_window'])) for p in preparations}
    if len(scopes) != 1:
        raise ValueError('Ambiguous saved reassessment scope')
    p = preparations[0]
    if not compatible(p) or digest(context) != p['context_hash']:
        raise ValueError('Reassessment context or implementation changed')
    if (answer['question'] != p['review_question'] or answer['patient_id'] != p['preview']['patient_id']
            or not context['dispatch']['source_review_allowed'] or context['omissions']):
        raise ValueError('Complete current reanswer context required')
    inventory = Q.received_inventory(retriever.path, answer['patient_id'])
    sources = context['model_context']['sources']
    if (inventory['id'] != p['preview']['inventory_id'] or set(sources) != set(p['preview']['selected_record_ids'])
            or any(inventory['records'].get(k, {}).get('record_hash') != s['source']['record_hash'] for k,s in sources.items())):
        raise ValueError('Reassessment received-source scope changed')
    versions = {'snapshot':context['model_context']['snapshot'], 'context_digest':digest(context),
                'records':{k:s['source']['record_hash'] for k,s in sources.items()},
                'reanswer_preparation_id':p['result_id'], 'reanswer_preparation_digest':digest(p)}
    return answer, context, {'question':p['question'], 'patient_id':answer['patient_id'],
        'context_id':context['id'], 'answer_id':answer['id'], 'answer_digest':digest(answer),
        'analysis_window':p['analysis_window'], 'source_versions':versions,
        'record_scope':answer.get('record_scope'), 'answer_coverage':answer.get('coverage', {})}


def refresh(retriever, request):
    Q.care.exact(request, 'preparation_id answer_id coverage')
    p = Q.saved_followup(retriever.path, request['preparation_id'])
    if p.get('schema') not in (PREPARATION, GROUP_PREPARATION) or not compatible(p):
        raise ValueError('Current reanswer preparation required')
    origin = {k:p[k] for k in ('decision_id', 'task_id')}
    if Q.review_preview(retriever, origin) != p['preview']:
        raise Q.care.work.WorkConflict('Sources or work changed since reanswer preparation')
    old = Q.load(retriever.path, p['decision_id'])
    answer, context, bound = Q.source_binding(retriever, request['answer_id'])
    if p['schema'] == GROUP_PREPARATION:
        from . import plan_answer
        matches = (answer.get('schema') == plan_answer.VERSION and
            bound['source_versions'].get('source_plan_id') == p['source_plan_id'] and
            bound['source_versions'].get('source_plan_digest') == p['source_plan_digest'])
    else:
        matches = answer.get('context_id') == p['context_id']
    if (not matches or any(bound[k] != old[k] for k in ('patient_id','question','analysis_window'))):
        raise ValueError('Reanswer must use this exact prepared question, patient and window')
    replacement = Q.prepare(retriever, {'answer_id':answer['id'], 'buyer_id':old['buyer_id'],
        'coverage':request['coverage'], 'planner':'cited_source_type_baseline'})
    changed = {x['record_id'] for x in p['changes']}
    current = {digest(f):f['id'] for f in answer['findings']}
    comparisons = []
    for f in old.get('finding_sources', []):
        same = f.get('finding_digest') in current
        comparisons.append({'previous_finding_id':f['finding_id'], 'previous_result_id':f.get('finding_digest'),
            'current_finding_id':current.get(f.get('finding_digest')),
            'result_id':f.get('finding_digest') if same else None,
            'changed_cited_records':sorted(set(f['records']) & changed),
            'status':'exact_result_reproduced' if same else 'superseded_by_current_answer_no_equivalence_claim'})
    result = {'schema':REVISION, **origin, 'preparation_id':p['result_id'],
        'question':old['question'], 'analysis_window':old['analysis_window'],
        'previous_answer_digest':old['answer_digest'], 'answer_id':answer['id'], 'answer_digest':digest(answer),
        'replacement_decision_id':replacement['id'], 'replacement_decision_digest':digest(replacement),
        'previous_planner':old['planning']['method'], 'replacement_planner':'cited_source_type_baseline',
        'finding_comparisons':comparisons, 'current_finding_results':[{'finding_id':f['id'],'result_id':digest(f)} for f in answer['findings']],
        'dependency_index_complete':p['preview']['finding_dependency_index_complete'],
        'changes':p['changes'], 'unresolved':answer.get('unanswered', []),
        'date_exclusions':p['preview']['date_exclusions'], 'reference_edges':p['preview']['reference_edges'],
        'absent_records':p['preview']['absent_records'], 'prior_task_digest':p['preview']['task_digest'],
        'status':'reanswered_and_service_options_recomputed', 'new_paid_api_calls':0,
        'clinical_execution_authorized':False, 'external_dispatch_performed':False,
        'scope':'All replacement findings come from this current answer. Exact equality alone retains result identity. Other old findings are historical, not resolved or automatically equivalent. Current baseline service topics still require review; payer and participation prerequisites remain independent.'}
    if Q.review_preview(retriever, origin) != p['preview']:
        raise Q.care.work.WorkConflict('Sources or prior work changed during decision reassessment')
    Q.inspect(retriever, replacement['id'])
    return Q.save_followup(retriever.path, result)


def queue(retriever, request):
    Q.care.exact(request, 'reassessment_id option_id actor')
    revision = Q.saved_followup(retriever.path, request['reassessment_id'])
    if revision.get('schema') != REVISION:
        raise ValueError('Saved decision reassessment required')
    old = Q.load(retriever.path, revision['decision_id'])
    task = Q.bound_task(retriever, old, revision['task_id'])
    if digest(task) != revision['prior_task_digest']:
        raise Q.care.work.WorkConflict('Prior work changed; reassess before linking replacement work')
    result = Q.queue(retriever, {'decision_id':revision['replacement_decision_id'],
                                'option_id':request['option_id'], 'actor':request['actor']})
    return Q.save_followup(retriever.path, {'schema':WORK, 'decision_id':old['id'], 'task_id':task['id'],
        'reassessment_id':revision['result_id'], 'replacement_decision_id':revision['replacement_decision_id'],
        'replacement_task_id':result['task']['id'], 'replacement_task_binding':result['decision_binding'],
        'question':old['question'], 'changes':revision['changes'], 'unresolved':revision['unresolved'],
        'status':'replacement_internal_work_linked', 'old_task_retained':True,
        'new_paid_api_calls':0, 'clinical_execution_authorized':False, 'external_dispatch_performed':False})


install_source_plans()
