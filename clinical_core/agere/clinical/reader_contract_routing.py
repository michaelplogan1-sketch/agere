"""Full-contract statement proposals within an existing question and owned work.

Incomplete classifier heads abstain before inference. Complete output vocabularies
permit an experimental proposal, never automatic acceptance or a clinical fact.
Uses existing comparison receipts and decision follow-ups; no model store or fit.
"""
import json
import math
import time
from ...contracts import digest
from .. import request_lineage as L
from . import question_decision as Q, reader_comparison as C, reader_role_coverage as V
from . import semantic_reader as R, semantic_student as S

VERSION = 'decision-statement-contract/1'


def disposition(coverage):
    missing = {a: list(coverage['unavailable_labels'][a]) for a in R.AXES}
    unsupported = any(missing.values())
    return {'status': 'abstained_unsupported_contract' if unsupported else 'experimental_proposal_only',
            'unavailable_labels': missing, 'can_propose': not unsupported,
            'automatic_acceptance': False, 'clinical_authority': False,
            'reason': 'The selected head cannot return every required statement, time and care-relation label.' if unsupported else
                      'Output vocabulary is complete; interpretation quality and clinical applicability remain unqualified.'}


def inputs(retriever, body):
    Q.care.exact(body, 'decision_id task_id target cohort_id role')
    if body['role'] not in C.ROLES: raise ValueError('Explicit registered local reader role required')
    row = Q.inspect(retriever, body['decision_id'])
    if row.get('schema') == Q.POPULATION_VERSION:
        raise ValueError('Use a saved individual source-question decision for exact statement review')
    # Q.inspect already validated question and service dependencies. Reopening
    # the entire decision here would repeat interpretation/source reconstruction.
    # Validate the exact stored task binding directly, as decision economics does.
    with Q.database(retriever.path) as db:
        link = db.execute('SELECT payload FROM question_decision_tasks WHERE decision=? AND task=?',
                          (row['id'], body['task_id'])).fetchone()
    if not link: raise ValueError('Task is not bound to this exact decision version')
    link = json.loads(link[0]); task = Q.care_readiness.task(retriever.path, body['task_id'])
    if (task['patient_id'] != row['patient_id'] or task['plan_id'] != link['plan_id'] or
            task['option_id'] != link['option_id']):
        raise ValueError('Task is not bound to this exact decision version')
    if task['state'] == 'cancelled': raise ValueError('Cancelled work cannot receive a new statement proposal')
    record, text, meta = R.source(retriever.path, body['target'])
    if (body['target']['patient_id'] != row['patient_id'] or task['patient_id'] != row['patient_id'] or
            row['source_versions']['records'].get(record['record_id']) != digest(record)):
        raise ValueError('Target must belong to the exact question patient and selected source version')
    packet = R.packet(record, text, body['target'], meta['snapshot']['as_of'])
    S.model_text(packet)  # No partial/truncated source may acquire full-contract status.
    if body['role'] in C.ROLES[:2]:
        coverage = V.inspect(retriever.path, {'cohort_id': body['cohort_id']})['roles'][body['role']]
        model = {'model_identity':coverage['model_identity'], 'status':'unqualified_shadow_head'}
    else:
        specs, _ = C.model_specs(retriever.path, body['cohort_id'], [body['role']])
        model = specs[body['role']]
        R.request(packet, body['role'])  # Validate the native request bound without sending.
        coverage = {'unavailable_labels': {a: [] for a in R.AXES}, 'full_label_contract': True,
                    'selective_threshold': None, 'deployment_qualified': False}
    fixed = {'schema': VERSION, 'implementation': S.sha(__file__), 'request': body,
             'decision_id': row['id'], 'task_id': task['id'], 'task_digest': digest(task),
             'task_revision': task['revision'], 'question': row['question'], 'patient_id': row['patient_id'],
             'source_hash': digest(record), 'source_identity': meta['snapshot'],
             'context_digest': digest(packet), 'target_quote': packet['target_quote'],
             'required_labels': {a: list(v) for a,v in R.AXES.items()},
             'role': body['role'], 'model': model, 'coverage': coverage}
    return fixed, disposition(coverage)


def run(retriever, body):
    fixed, route = inputs(retriever, body)
    comparison = None
    if route['can_propose']:
        # Existing comparison store provides exact reuse and retains failed roles.
        L.observe('local_reader_started', decision_id=fixed['decision_id'], task_id=fixed['task_id'],
                  source_hash=fixed['source_hash'], role=body['role'], model=fixed['model'])
        started = time.perf_counter()
        prepared = C.prepare(retriever.path, {'target':body['target'], 'cohort_id':body['cohort_id'],
                                               'roles':[body['role']]})
        comparison = prepared['comparison']
        variant = comparison['variants'][body['role']]
        # Current comparison reuse is distinct from the variant's historical
        # reading cache flag. Never charge the original duration to this replay.
        L.observe('local_reader_returned', comparison_id=comparison['id'],
                  comparison_cache_hit=prepared.get('cache_hit'), role=body['role'],
                  reading_id=variant.get('reading_id'), status=variant['status'],
                  reading_cache_hit_this_attempt=(None if prepared.get('cache_hit') else variant.get('cache_hit')),
                  inference_this_attempt=(False if prepared.get('cache_hit') else
                      not variant['cache_hit'] if variant['status']=='completed' and 'cache_hit' in variant else None),
                  observed_elapsed_seconds=time.perf_counter()-started,
                  effort_scope='Current comparison call elapsed time, including lookup; no CPU/GPU price or human effort inferred.')
        if variant['status'] != 'completed':
            route = {**route, 'status':'reader_failed', 'reason':variant['reason']}
    else:
        variant = None  # No labels or zeros masquerading as a resolved interpretation.
        L.observe('local_reader_abstained', role=body['role'], source_hash=fixed['source_hash'],
                  inference_this_attempt=False, reason=route['status'])
    current, _ = inputs(retriever, body)
    if current != fixed: raise ValueError('Question, work, source or model changed during interpretation')
    result = {**fixed, **route, 'comparison_id':comparison['id'] if comparison else None,
              'reading_id':variant['reading_id'] if variant and variant['status']=='completed' else None,
              'proposed_labels':variant['labels'] if variant and variant['status']=='completed' else None,
              'new_paid_api_calls':0, 'reader_comparison_used':comparison is not None,
              'human_review_created':False, 'training_performed':False, 'task_state_changed':False,
              'answer_replaced':False,
              'scope':'Statement review for this saved question and task. All three axes require the complete label contract. Shadow comparisons remain separately available. No source conclusion, service plan, human review or care state is automatically changed.'}
    return Q.save_followup(retriever.path, result)


def reopen(retriever, body):
    Q.care.exact(body, 'result_id')
    row = Q.saved_followup(retriever.path, body['result_id'])
    if row['schema'] != VERSION: raise ValueError('Not a statement-contract follow-up')
    fixed, route = inputs(retriever, row['request'])
    if any(row[k] != v for k,v in fixed.items()): raise ValueError('Statement review dependencies changed')
    if row['comparison_id']:
        comparison = C.inspect(retriever.path, {'id':row['comparison_id']})
        if comparison['request'] != {'target':row['request']['target'], 'cohort_id':row['request']['cohort_id'], 'roles':[row['role']]}:
            raise ValueError('Statement comparison binding changed')
        variant = comparison['variants'][row['role']]
        if variant['status']=='completed':
            if row['reading_id'] != variant['reading_id'] or row['proposed_labels'] != variant['labels']:
                raise ValueError('Statement proposal differs from comparison')
        elif row['status'] != 'reader_failed': raise ValueError('Failed reader has a successful disposition')
    elif route['can_propose'] or row['status'] != 'abstained_unsupported_contract' or row['proposed_labels'] is not None:
        raise ValueError('Unsupported reader disposition changed')
    return row


REVIEW_VERSION = 'decision-statement-review-outcome/1'


def review_status(retriever, body):
    """Inspect, never create, the existing source review for this exact work."""
    Q.care.exact(body, 'result_id')
    statement = reopen(retriever, body)
    if not statement['reading_id']: raise ValueError('Abstained or failed statements have no reading to review')
    reading = R.inspect(retriever.path, statement['reading_id'])
    if (reading['id'] != statement['reading_id'] or reading['target'] != statement['request']['target'] or
            reading['source_hash'] != statement['source_hash'] or reading['reading']['labels'] != statement['proposed_labels']):
        raise ValueError('Reading differs from the exact statement proposal')
    review = reading['reviews'][-1] if reading['reviews'] else None
    if review and (review['reading_id'] != reading['id'] or review['source_hash'] != statement['source_hash'] or
                   review['revision'] != reading['review_revision']):
        raise ValueError('Review differs from the exact reading or source')
    return {'statement_result_id':statement['result_id'], 'decision_id':statement['decision_id'],
            'task_id':statement['task_id'], 'task_revision':statement['task_revision'],
            'reading_id':reading['id'], 'source_hash':statement['source_hash'],
            'review_revision':reading['review_revision'], 'review_digest':digest(review) if review else None,
            'review':review, 'status':'review_available' if review else 'not_reviewed',
            'reviewer_identity_verified':False, 'clinical_authority':False, 'human_review_created':False}


def attach_review(retriever, body):
    Q.care.exact(body, 'result_id reading_id review_revision review_digest effort')
    effort = body['effort']
    Q.care.exact(effort, 'seconds declared_by')
    seconds = effort['seconds']
    if seconds is None:
        if effort['declared_by'] is not None: raise ValueError('Unknown effort must have no declarant')
    elif (type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0 <= seconds <= 604800 or
          not isinstance(effort['declared_by'],str) or not 1 <= len(effort['declared_by'].strip()) <= 120):
        raise ValueError('Bounded declared review seconds and declarant required')
    status = review_status(retriever, {'result_id':body['result_id']})
    if (status['review'] is None or type(body['review_revision']) is not int or
            any(body[k] != status[k] for k in ('reading_id','review_revision','review_digest'))):
        raise ValueError('Exact latest saved reading review required; reload before attaching')
    result = {**status, 'schema':REVIEW_VERSION, 'request':body,
              'effort':{**effort, 'basis':'explicit_local_declaration' if seconds is not None else 'unknown',
                        'verified':False, 'cost_usd':None},
              'status':status['review']['status'], 'task_state_changed':False,
              'scope':'Existing documentary review linked to original question and work. Identity, effort and clinical authority are unverified; no care completion or new training permission is established.'}
    # Catch revisions/source/work changes before persisting. Reopen also checks
    # cross-store currentness; the immutable snapshot never claims a global lock.
    if review_status(retriever, {'result_id':body['result_id']}) != status:
        raise ValueError('Source review or work changed during attachment')
    return Q.save_followup(retriever.path,result)


def review_open(retriever, body):
    Q.care.exact(body, 'result_id')
    row = Q.saved_followup(retriever.path,body['result_id'])
    if row['schema'] != REVIEW_VERSION: raise ValueError('Not a saved statement review outcome')
    status = review_status(retriever, {'result_id':row['statement_result_id']})
    if any(row[k] != v for k,v in status.items() if k != 'status'):
        raise ValueError('Source review outcome superseded or dependencies changed')
    return row
