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


def review_feedback(path,key):
    """Read criticism only from this source-bound workflow's saved artifacts."""
    import re
    context=A.load(path,key,current=True);saved=read(path,key);reports=[]
    for step in saved.get('steps') or []:
        ident=step.get('deliverable_id')
        if step.get('kind')!='deliverable' or step.get('status')!='blocked' or not isinstance(ident,str) or not re.fullmatch(r'DLV-[A-F0-9]{12}',ident):continue
        file=A.root(path)/key/'deliverables'/ident/'spec.json'
        if not file.is_file():continue
        spec=json.loads(file.read_text());review=(spec.get('verification') or {}).get('semantic_review')
        if spec.get('status')=='rejected' and isinstance(review,dict) and review.get('issues'):
            reports.append({'artifact':ident,'context_id':key,'source':context['source'],
                            'authority':'machine error reports to investigate, not clinician truth','review':review})
    return reports


def _progress(context):
    """Native evidence or a newly confirmed interpretation permits continuation.

    Rewording a headline, duplicating calls or changing cost/timing does not.
    """
    from ..clinical.harness_improvement import semantic_result
    def stable(value):
        if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if k not in ('result_view','owned_run_recovery','request_hash','attempt_id')}
        if isinstance(value,list):return [stable(v) for v in value]
        return value
    evidence=A.investigation_evidence(context.get('investigation'))
    calls={fingerprint(stable(semantic_result(c))) for c in evidence['tool_calls']}
    reviews={fingerprint(stable(semantic_result(r))) for r in evidence['document_reviews']}
    return fingerprint({'source':context['source'],'input_patient_ids':context['input_patient_ids'],
                        'calls':sorted(calls),'reviews':sorted(reviews),'results':stable(semantic_result(context.get('results'))),
                        'confirmed':(context['answer'].get('investigation_review') or {}).get('status')=='confirmed',
                        'workflow':[{k:s.get(k) for k in ('kind','purpose','unresolved')} for s in context.get('workflow') or []]})


def fulfill(stack,question,result,*,original_question=None,generation=None):
    """Capture and finish the existing request, including upstream artifact repair.

    Costs and every immutable context are retained. Progress, not an iteration
    quota, controls repetition. Compound internal work is preserved for explicit
    reconciliation instead of being duplicated on a changed analytical context.
    """
    from ..clinical import evaluation
    if generation not in (None,'saved_report','frontier'):
        raise ValueError('Choose saved_report or frontier delivery')
    original_question=original_question or question
    seen=set();attempts=[];initial=None;stop=None;total_calls=0;known=0.;complete=True
    while True:
        if generation is not None:
            result={**result,'workflow':[{**s,**({'generation':generation} if s.get('kind')=='deliverable' else {})}
                                        for s in result.get('workflow') or []]}
        key=A.capture(stack.path,question,result,original_question=original_question)
        if not key:
            if attempts:
                attempts.append({'context_id':None,'phase':'analysis_without_context','new_paid_api_calls':result.get('new_paid_api_calls',0),
                                 'planning_economics':result.get('planning_economics') or {}})
                stop='no_capturable_answer'
            break
        reference=key;key=key['id'];initial=initial or key;result={**result,'action_context':reference}
        econ=dict(result.get('planning_economics') or {})
        analysis_calls=result.get('new_paid_api_calls') or 0
        analysis_known=econ.get('known_api_estimate_usd') or 0
        analysis_complete=(econ.get('new_api_estimate_usd') is not None or analysis_calls==0 and analysis_known==0) and econ.get('api_cost_known') is not False
        try:
            context=A.load(stack.path,key,current=True,source_review=True)
        except (ValueError,OSError,sqlite3.Error) as exc:
            # Capturing succeeded. Preserve that inspectable reference even
            # when current authority is unavailable; do not lose it in the
            # conversation caller's exception handler.
            result.update(action_context_error=str(exc),
                answer={**result['answer'],'captured_status':result['answer'].get('captured_status',result['answer'].get('status')),
                        'status':'partial','verification':'unverified'},
                workflow=[{**step,'status':'blocked','reason':str(exc)} for step in result.get('workflow') or []])
            attempts.append({'context_id':key,'phase':'analysis_with_blocked_delivery',
                'new_paid_api_calls':analysis_calls,'planning_economics':{
                    'known_api_estimate_usd':analysis_known,'api_cost_known':analysis_complete},
                'workflow':copy.deepcopy(result['workflow']),'rendered':False,'error':str(exc)})
            stop='source_or_review_unavailable'
            break
        source_review=context.get('source_review_required',False)
        if source_review:
            result.update(source_review_required=True,clinical_execution_authorized=False,
                answer={**result['answer'],'captured_status':result['answer'].get('captured_status',result['answer'].get('status')),
                        'status':'partial','verification':'unverified'})
            if result.get('program_results'):
                parts=result['program_results']
                if isinstance(parts,list):
                    result['program_results']=[{**part,'source_review_required':True,'clinical_execution_authorized':False}
                        if isinstance(part,dict) else part for part in parts]
                elif isinstance(parts,dict):
                    result['program_results']={**parts,'source_review_required':True,'clinical_execution_authorized':False}
        progress=_progress(context)
        if attempts and progress in seen:
            stop='no_new_native_evidence_or_confirmed_interpretation'
            # Do not purchase another rendering of the same failed evidence.
            result['workflow']=[{**s,'status':'blocked','reason':'Prior artifact review remains unresolved; retained attempts are linked below.'} for s in context.get('workflow') or []]
            attempts.append({'context_id':key,'phase':'analysis','new_paid_api_calls':result.get('new_paid_api_calls',0),
                             'planning_economics':result.get('planning_economics') or {},'rendered':False})
            break
        seen.add(progress)
        if evaluation.active():break
        if result.get('workflow') and result.get('program_results') and not source_review:
            save_program_workflow(stack.path,key,result['workflow']);break
        if source_review and generation=='frontier':
            delivered={'steps':[{**step,'status':'blocked',
                'reason':('The requested frontier view requires current source readings. Open the saved source evidence for review; no alternative generation was substituted.'
                          if step.get('kind')=='deliverable' else 'A suspended source reading blocks program preparation and monitoring.')}
                for step in result.get('workflow') or []],
                'new_paid_api_calls':0,'known_api_estimate_usd':0,'api_cost_known':True}
        else:
            chosen_generation='saved_report' if source_review else generation or 'frontier'
            try:
                delivered=materialize(stack.path,key,generation=chosen_generation,
                    source_review=source_review,allow_live=bool(getattr(stack,'allow_live',False))) if result.get('workflow') else {
                    'steps':[],'new_paid_api_calls':0,'known_api_estimate_usd':0,'api_cost_known':True}
            except (ValueError,OSError,sqlite3.Error) as exc:
                # A second current check can fail after capture. Keep the link
                # and distinguish an unpriced hosted failure from a local one.
                local=chosen_generation=='saved_report' or not getattr(stack,'allow_live',False)
                delivered={'steps':[{**step,'status':'blocked','reason':str(exc)} for step in result.get('workflow') or []],
                    'new_paid_api_calls':0,'known_api_estimate_usd':0,'api_cost_known':local,
                    'delivery_error':str(exc),'new_paid_api_calls_known':local}
        if delivered.get('delivery_error') or delivered.get('source_review_currency_failure'):
            result.update(action_context_error=delivered.get('delivery_error') or delivered['source_review_currency_failure'],
                answer={**result['answer'],'captured_status':result['answer'].get('captured_status',result['answer'].get('status')),
                        'status':'partial','verification':'unverified'})
            if delivered.get('new_paid_api_calls_known') is False:result['new_paid_api_calls_known']=False
        result['workflow']=delivered['steps']
        stage={'context_id':key,'phase':'analysis_and_delivery','new_paid_api_calls':analysis_calls+delivered['new_paid_api_calls'],
               'planning_economics':{'known_api_estimate_usd':analysis_known+delivered['known_api_estimate_usd'],
                                     'api_cost_known':analysis_complete and delivered['api_cost_known']},
               'workflow':copy.deepcopy(delivered['steps']),
               'rendered':not(source_review and generation=='frontier') and any(s.get('deliverable_id') for s in delivered['steps'])}
        if delivered.get('prior_workflow_costs'):stage['prior_workflow_costs']=delivered['prior_workflow_costs']
        attempts.append(stage)
        if result.get('action_context_error'):
            stop='source_or_review_unavailable';break
        if source_review:
            stop='source_review_required'
            break  # No whole-answer repair loop or implicit program authority.
        if any((s.get('provider_failure') or {}).get('kind')=='rate_limit' and (s.get('provider_failure') or {}).get('transport')=='subscription' for s in delivered['steps']):
            stop='subscription_limit';break
        try:feedback=review_feedback(stack.path,key)
        except (ValueError,OSError,sqlite3.Error) as exc:
            result['action_context_error']=str(exc);stop='source_or_review_unavailable';break
        if not feedback or not getattr(stack,'allow_live',False):break
        if any(s.get('kind')!='deliverable' for s in context.get('workflow') or []):
            stop='existing_internal_work_requires_reconciliation';break
        from ..clinical import proposers
        try:
            repaired=proposers.continue_from_artifact(stack,key,feedback)
        except Exception as exc:
            from ..clinical.system_one_gate import outage
            usage=getattr(exc,'planner_receipt',{})
            attempts.append({'context_id':key,'phase':'analysis_failure','new_paid_api_calls':usage.get('new_paid_api_calls',0),
                             'planning_economics':{'known_api_estimate_usd':usage.get('known_api_estimate_usd',0),
                                                   'api_cost_known':usage.get('api_cost_known',False)},
                             'error':type(exc).__name__,'error_detail':str(exc) if isinstance(exc,ValueError) else None,'provider_failure':outage(exc)})
            stop='subscription_limit' if outage(exc)=='rate_limit' and __import__('os').environ.get('AGERE_CLAUDE_TRANSPORT')=='subscription' else 'investigation_unavailable'
            break
        # The original requested artifact remains owed even when the analyst
        # repairs data without repeating its workflow declaration.
        if not repaired.get('workflow'):repaired['workflow']=copy.deepcopy(context.get('workflow') or [])
        result=repaired
    if attempts:
        total_calls=sum(a['new_paid_api_calls'] for a in attempts)
        known=sum((a['planning_economics'].get('known_api_estimate_usd') or 0) for a in attempts)
        complete=all(a['planning_economics'].get('api_cost_known',a['planning_economics'].get('new_api_estimate_usd') is not None) for a in attempts)
        econ={**(result.get('planning_economics') or {}),'known_api_estimate_usd':known,
              'new_api_estimate_usd':known if complete else None,'api_cost_known':complete,
              'scope':'Current invocation analysis and artifact attempts; earlier workflow costs remain in retained_workflow_costs and persisted history. Subscription figures are API equivalents; local compute and review are unpriced.'}
        retained={a['context_id']:a['prior_workflow_costs'] for a in attempts if a.get('prior_workflow_costs')}
        if retained:
            econ.update(retained_workflow_costs=retained,
                all_recorded_api_cost_known=complete and all(v.get('api_cost_known',True) for v in retained.values()))
        result.update(new_paid_api_calls=total_calls,planning_economics=econ)
    if len(attempts)>1 or stop:
        record={'status':stop or 'returned','original_context_id':initial,'latest_context_id':(result.get('action_context') or {}).get('id'),
                'attempts':attempts,'clinical_acceptance':False,'external_dispatch_performed':False}
        result['workflow_continuation']=record
        target=A.root(stack.path)/initial/'continuation.json';temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(record,default=str));temp.replace(target)
    return result


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


def materialize(path, key, *, allow_live=False, generation=None, send=None, source_review=False):
    """Prepare requested work; frontier use requires the caller's live authority.

    Legacy/offline callers retain explicitly labeled local templates. The main
    conversation integration should pass generation='frontier' and allow_live.
    An explicit frontier request never falls back to a local template.
    """
    generation = generation or ('frontier' if allow_live else 'local_template')
    if generation not in ('frontier', 'local_template', 'saved_report'):
        raise ValueError('Choose frontier, saved_report or local_template generation')
    if source_review and generation!='saved_report':
        raise ValueError('Source-review fulfillment supports an explicit saved inspection report only')
    with _lock:
        try:
            c = A.load(path, key, current=True, source_review=source_review)
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
        refresh_source_view = (saved.get('initialized') and (source_review or saved.get('source_review_identity'))
                               and saved.get('source_review_identity') != A.source_disposition_identity(c))
        prior_costs={'known_api_estimate_usd':saved.get('known_api_estimate_usd') or 0,
                     'new_paid_api_calls':saved.get('new_paid_api_calls') or 0,
                     'api_cost_known':saved.get('api_cost_known',True)}
        if source_review and saved.get('program_links'):
            # Aggregate links belong to their producing contexts. Never replace
            # them with locally executed root steps or erase their provenance.
            return {**saved,'steps':[{**step,'status':'blocked',
                'reason':'A suspended source reading blocks this aggregate workflow. Open the producing output for source-evidence inspection.'}
                for step in saved.get('steps') or []],
                'source_review_required':True,'source_review_requires_producing_outputs':True,
                'new_paid_api_calls':0,'known_api_estimate_usd':0,'api_cost_known':True,
                'prior_workflow_costs':prior_costs}
        if saved.get('initialized') and not refresh_source_view:
            # Keep original attempt costs on the steps, but do not charge them
            # again to a conversation that merely reuses this saved workflow.
            return {**saved, 'new_paid_api_calls': 0, 'known_api_estimate_usd': 0,
                    'api_cost_known': True,**({'prior_workflow_costs':prior_costs}
                        if prior_costs['known_api_estimate_usd'] or prior_costs['new_paid_api_calls'] or not prior_costs['api_cost_known'] else {})}
        steps = [];subscription_stopped=False
        # Prepare explicitly requested tasks before freezing deliverable views,
        # while preserving the user's step order in the returned workflow.
        ordered = sorted(enumerate(c.get('workflow', [])), key=lambda x: (x[1]['kind'] != 'simulation', x[0]))
        for index, step in ordered:
            s = copy.deepcopy(step)
            s['index'] = index
            if subscription_stopped:
                s.update(status='blocked',reason='Claude subscription limit stopped this workflow; no later provider call was attempted.')
                steps.append(s);continue
            try:
                if s.get('origin'):
                    from ..clinical.workflow_contract import validate_selection
                    validate_selection(s)
                incomplete = source_review or c['answer'].get('status') != 'answered' or c['answer'].get('missing')
                # A local inspection view can show partial evidence and its gaps.
                # Work and monitoring still require an accepted complete answer.
                inspection_view = s['kind'] == 'deliverable' and (generation in ('local_template','saved_report') or s.get('purpose') in ('inspection','proposal'))
                if incomplete and not inspection_view:
                    s.update(status='blocked', reason='A suspended source reading permits only source-evidence inspection; program preparation and monitoring remain blocked.'
                             if source_review else 'Resolve the incomplete analytical answer first.')
                elif s.get('unresolved'):
                    s.update(status='needs_definition', reason='; '.join(s['unresolved']))
                elif s['kind'] == 'deliverable':
                    s['generation'] = generation
                    if generation == 'frontier':
                        from ..clinical.request_plan import quote_text
                        brief = quote_text(s['quote']) if s.get('quote') else s['title']
                        if s.get('purpose') in ('inspection','proposal'):
                            brief += ('\nThis is an evidence inspection or proposed design. Display the saved answer status, unknowns and open items prominently. '
                                      'Preserve all source uncertainty. Do not claim eligibility, completed clinical review, enrollment, orders, shipment or new analytical facts.')
                        d = A.compose_frontier(path, {'context_id': key, 'request': brief},
                                               allow_live=allow_live, send=send)
                        reason = ('Frontier-generated view saved from this answer; structural evidence checks passed.' if d['status'] == 'delivered'
                                  else d.get('failure') or 'Generated view failed evidence/number checks. Open the studio to review the failure and costs.')
                    else:
                        if generation == 'saved_report':
                            from .saved_report import create
                        else:
                            from .mock_views import create
                        # This boundary surrounds pure local rendering only.
                        # Provider exceptions and their accounting never enter it.
                        try:
                            d = create(path, {'context_id': key, 'template': s['template'], 'request': s['title'],
                                **({'retained_inspection':True} if source_review else {})})
                        except Exception as exc:
                            if isinstance(exc,(ValueError,OSError,sqlite3.Error)):raise
                            s.update(status='blocked',reason='Local report rendering failed; the saved answer is retained. Retry this report after repair.',
                                render_error={'type':type(exc).__name__,'message':str(exc),'context_id':key},
                                costs={'known_api_estimate_usd':0,'api_cost_known':True},new_paid_api_calls=0)
                            steps.append(s);continue
                        reason = ('Source-evidence inspection report saved with suspended readings and unresolved items explicit; no clinical acceptance or new model call.' if source_review else
                                  'Reusable report saved from the exact answer, including analytical status and uncertainty; no model call for rendering.' if generation=='saved_report' else
                                  'Partial evidence view saved with unresolved requirements; patient work remains blocked.' if incomplete else
                                  'Local template saved from this exact answer; no frontier generation.')
                    if d.get('provider_failure'):
                        s['provider_failure']=d['provider_failure']
                        subscription_stopped=d['provider_failure'].get('kind')=='rate_limit' and d['provider_failure'].get('transport')=='subscription'
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
        currency_failure=None
        if source_review:
            try:
                fresh=A.load(path,key,current=True,source_review=True)
                if A.source_disposition_identity(fresh)!=A.source_disposition_identity(c):
                    raise ValueError('Source-reading status changed during fulfillment. Reopen the current source review.')
            except (ValueError,OSError,sqlite3.Error) as exc:
                currency_failure=str(exc)
                for step in steps:
                    if step.get('kind')=='deliverable' and step.get('status')=='ready':
                        step.update(status='blocked',reason=currency_failure)
        record = {'initialized': True, 'synthetic': True, 'context_id': key, 'steps': steps,
                  'external_dispatch_performed': False,
                  'new_paid_api_calls': sum(s.get('new_paid_api_calls', 0) for s in steps),
                  'known_api_estimate_usd': sum((s.get('costs') or {}).get('known_api_estimate_usd',
                                                (s.get('costs') or {}).get('total_usd') or 0) for s in steps),
                  'api_cost_known': all((s.get('costs') or {}).get('api_cost_known', True) for s in steps)}
        current_cost = record['known_api_estimate_usd']
        current_calls = record['new_paid_api_calls']
        current_cost_known = record['api_cost_known']
        if source_review and not currency_failure:
            record['source_review_identity'] = A.source_disposition_identity(c)
        if currency_failure:record['source_review_currency_failure']=currency_failure
        if refresh_source_view:
            # A newly suspended reading changes the inspection view, not the
            # historical work or paid attempts. Keep the prior workflow intact
            # in its history and charge only this new rendering to this call.
            record['source_review_history'] = [*(saved.get('source_review_history') or []),
                {k:v for k,v in saved.items() if k!='source_review_history'}]
            record['known_api_estimate_usd'] += saved.get('known_api_estimate_usd') or 0
            record['new_paid_api_calls'] += saved.get('new_paid_api_calls') or 0
            record['api_cost_known'] = record['api_cost_known'] and saved.get('api_cost_known', True)
            record['prior_workflow_costs'] = prior_costs
        _save(path, key, record)
        return {**record,'new_paid_api_calls':current_calls,'known_api_estimate_usd':current_cost,
                'api_cost_known':current_cost_known,'prior_workflow_costs':prior_costs} if refresh_source_view else record


def retry_deliverable(path, request, *, allow_live=False, send=None):
    """Explicit, revision-checked retry; retain failed artifacts and all attempt costs."""
    if set(request) != {'context_id', 'index', 'attempt'} or type(request['index']) is not int or type(request['attempt']) is not int:
        raise ValueError('Choose a saved deliverable and its next attempt number')
    with _lock:
        key, index = request['context_id'], request['index']
        record = read(path, key)
        A.load(path, key, current=True,source_review=bool(record.get('source_review_identity')))
        if record.get('program_links'):
            raise ValueError('Open the producing output workspace to retry its deliverable')
        steps = record['steps']
        if not 0 <= index < len(steps):
            raise ValueError('Saved deliverable not found')
        step = steps[index]
        local_error=bool(step.get('render_error'))and step.get('generation')in('saved_report','local_template')
        if step['kind'] != 'deliverable' or step.get('status') != 'blocked' or not(step.get('deliverable_id')or local_error):
            raise ValueError('Only a failed saved deliverable can be retried')
        attempts = copy.deepcopy(step.get('attempts') or [{k: step.get(k) for k in
            ('deliverable_id', 'status', 'reason', 'costs', 'new_paid_api_calls','render_error')}])
        if request['attempt'] != len(attempts) + 1:
            raise ValueError('The attempt changed. Reload before retrying.')
        from ..clinical.request_plan import quote_text
        brief = quote_text(step['quote']) if step.get('quote') else step['title']
        if local_error:
            if step['generation']=='saved_report':
                from .saved_report import create
            else:
                from .mock_views import create
            try:
                d=create(path,{'context_id':key,'template':step['template'],'request':step['title'],
                    **({'retained_inspection':True}if record.get('source_review_identity')else{})})
            except Exception as exc:
                if isinstance(exc,(ValueError,OSError,sqlite3.Error)):raise
                d={'id':None,'status':'blocked','failure':'Local report rendering failed; saved answer retained.',
                    'render_error':{'type':type(exc).__name__,'message':str(exc),'context_id':key},
                    'costs':{'known_api_estimate_usd':0,'api_cost_known':True},'new_paid_api_calls':0}
        else:
            d = A.compose_frontier(path, {'context_id': key, 'request': brief +
                f'\nExplicit retry attempt {request["attempt"]} for this saved workflow. Preserve its exact evidence and scope.'},
                allow_live=allow_live, send=send)
        latest = {'deliverable_id': d['id'], 'status': 'ready' if d['status'] == 'delivered' else 'blocked',
                  'reason': 'View saved after retry; earlier attempts and costs are retained.' if d['status'] == 'delivered'
                            else d.get('failure') or 'Generated view failed evidence checks.',
                  'costs': d['costs'], 'new_paid_api_calls': d.get('new_paid_api_calls', 0), 'at': A.now()}
        if d.get('render_error'):latest['render_error']=d['render_error']
        else:step.pop('render_error',None)
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
        if not c['source_disposition']['source_readings_current']:
            raise ValueError('Resolve suspended or unavailable source readings before checking this saved monitor.')
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
