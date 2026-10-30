"""Offline scoring of explicit development traces, never runtime patient evidence."""
from collections import Counter
from pathlib import Path
import hashlib,json
from ..contracts import digest

STAGES=('retrieval','reranking','source_scope','source_interpretation','reader_comparison','planning','matching','work_followthrough')
ROOT=Path(__file__).resolve().parents[2]
CONTRACT=ROOT/'config/agere/release_acceptance.json'

def saved_report(root=ROOT, snapshot=None):
    """Prefer the current evidence bundle; never present another source as current."""
    root=Path(root).resolve()
    current=root/'artifacts/agere-workflow-evaluation-current/scorecard.json'
    path=current if current.exists() else root/'artifacts/agere-workflow-evaluation-v1/scorecard.json'
    report=json.loads(path.read_text())
    if path==current:
        if not snapshot or report.get('snapshot')!=snapshot:
            raise ValueError('Current workflow report belongs to another source snapshot')
        traces=report.get('trace_sha256')
        if not isinstance(traces,dict) or not traces:
            raise ValueError('Current workflow report lacks saved execution evidence')
        for name,expected in traces.items():
            source=(root/name).resolve()
            if not source.is_relative_to(root) or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=expected:
                raise ValueError('Current workflow report execution evidence changed')
        if report.get('scorecard_implementation_sha256')!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
            raise ValueError('Current workflow report scoring implementation changed')
    return report

def source_selection_decision(root=ROOT):
    """Attach engineering diagnostics only; never provide patient/model evidence.

    A report for different source/code/input versions must not appear current.
    This cannot activate a route or grant clinical authority.
    """
    root=Path(root).resolve()
    target=root/'artifacts/agere-source-selection-v1/decision/report.json'
    if not target.exists():return {'status':'unassessed','summary':'No source-selection activation decision is attached.'}
    try:
        report=json.loads(target.read_text())
        if report.get('schema')!='agere-source-selection-decision/1' or report.get('result_digest')!=digest({k:v for k,v in report.items() if k!='result_digest'}):
            raise ValueError('Decision report integrity changed')
        active=json.loads((root/'config/agere/active_warehouse.json').read_text())
        if report['snapshot']!=active['snapshot']:raise ValueError('Decision report uses a different source snapshot')
        for group in ('code_sha256','evidence_sha256'):
            if not report.get(group):raise ValueError('Decision report has no versioned evidence')
            for path,expected in report[group].items():
                evidence=(root/path).resolve()
                if not evidence.is_relative_to(root) or not evidence.is_file() or hashlib.sha256(evidence.read_bytes()).hexdigest()!=expected:
                    raise ValueError('Decision report source, code or evidence changed')
        return {**report,'evidence_verified':True,'use':'engineering_display_only'}
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return {'status':'stale_or_invalid','summary':str(exc),'evidence_verified':False}

def acceptance_status(checks):
    """Unknown evidence cannot silently turn into a pass (including an empty set)."""
    statuses={c['status'] for c in checks}
    if not statuses:return 'unassessed'
    if statuses-{'passed','failed','blocked','unassessed'}:raise ValueError('Invalid acceptance status')
    return next((s for s in ('failed','blocked','unassessed') if s in statuses),'passed')

def question_matches(answer, case, decision):
    if answer.get('question') == case['question']: return True
    # Scoped reader prompts retain their original question through an exact
    # saved source-plan decision, never a startswith/string-stripping shortcut.
    return bool(decision.get('id') and decision.get('source_versions',{}).get('source_plan_id') and
        decision.get('question') == case['question'] and decision.get('patient_id') == case['patient'] and
        decision.get('answer_id') == answer.get('id') and decision.get('answer_digest') == digest(answer) and
        decision.get('context_id') == answer.get('context_id'))

def complete_selection_valid(case, row, answer, scope):
    """Check actual whole-group transport without erasing earlier retrieval misses."""
    try:
        plan=row['plan'];snap=row['snapshot'];context=row['context'];preview=plan['preview']
        sources=context['model_context']['sources'];groups=snap['groups']
        records=sorted(sources);selected=sorted(preview['selected_record_ids'])
        wanted=set(case['required_categories_if_received'])
        received={k for k,n in preview['excluded_by_category'].items() if n>0} | {r['source_category'] for r in preview['records']}
        categories={v['source']['source_table'] for v in sources.values()}
        return bool(selected and groups) and all((
            snap['plan_id']==plan['id']==answer['source_plan_id'], snap['plan_digest']==digest(plan),
            snap['id']==answer['snapshot_id'], digest(snap)==answer['dependencies']['answer_selection_digest'],
            answer['context_id']==context['id']==scope['context_id'], answer['context_digest']==digest(context),
            preview['patient_id']==answer['patient_id']==case['patient'],
            snap['original_question']==answer['question']==case['question'],
            records==selected==sorted(answer['record_scope']['record_ids']),
            sorted(g['context_id'] for g in groups)==sorted(g['context_id'] for g in plan['groups']),
            sorted(r for g in groups for r in g['record_ids'])==selected,
            wanted & received <= categories,
            all(v['source']['patient_id']==case['patient'] for v in sources.values()),
            answer['coverage']['group_answers']==len(groups),
            answer['coverage']['submitted_units']==snap['source_units']==len(context['model_context']['evidence']),
            not context['omissions']))
    except (KeyError,TypeError,ValueError):return False


def declared_units_accounted(answer):
    """A documented unresolved disposition is accounted, not clinically solved."""
    coverage=answer.get('coverage',{})
    counts=[coverage.get(k) for k in ('received_units','submitted_units','model_accounted_units')]
    if not coverage or not all(type(n) is int and n>=0 for n in counts) or len(set(counts))!=1:return False
    unresolved=coverage.get('unassessed_units')
    if unresolved==[]:return True
    if not isinstance(unresolved,list) or not unresolved or coverage.get('complete_declared_scope') is not True:return False
    reviews=[u for p in answer.get('passes',[]) for u in p.get('result',{}).get('unit_reviews',[])]
    if any(not isinstance(u,dict) or not isinstance(u.get('source_unit_id'),str) or not u['source_unit_id'] for u in reviews+unresolved):return False
    ids=[u.get('source_unit_id') for u in reviews]
    # Check actual dispositions, not a summary that merely asserts completeness.
    return (len(ids)==counts[0] and len(set(ids))==len(ids) and all(isinstance(i,str) and i for i in ids)
        and coverage.get('unit_dispositions',{}).get('unresolved')==len(unresolved)
        and sorted(u.get('source_unit_id','') for u in unresolved)==sorted(u['source_unit_id'] for u in reviews if u.get('status')=='unresolved')
        and all(isinstance(u,dict) and u.get('status')=='unresolved' and isinstance(u.get('reason'),str)
                and bool(u['reason'].strip()) and u in reviews for u in unresolved))


def case_acceptance(case, stages, evidence, planning):
    """Mechanical lineage/transport acceptance; never clinical adjudication.

    Decision, persistence and reassessment traces are required observations. They
    are not inferred from matching patient IDs or isolated successful API calls.
    """
    checks=[]
    def add(id, status, detail, traces):
        checks.append({'id':id,'status':status,'detail':detail,'traces':traces})
    def observed(id, present, valid, detail, traces):
        add(id, 'passed' if valid else 'failed' if present else 'unassessed',detail,traces)
    scope=response(evidence('source_scope'));answer=response(evidence('source_interpretation'))
    selection=response(evidence('complete_source_selection'))
    for id, stage_name in [('received_category_coverage','retrieval'),('source_transport','source_scope')]:
        if id=='received_category_coverage' and selection:
            observed(id,True,complete_selection_valid(case,selection,answer,scope),
                'All received named categories are transported by the explicitly prepared complete source plan. Earlier retrieval/reranker misses remain separate diagnostics; no default activation or recall qualification.', ['complete_source_selection','source_interpretation','source_scope'])
            continue
        s=stages[stage_name]
        add(id, 'passed' if s['status']=='checked' else 'unassessed' if s['status']=='not_run' else 'failed',
            s['detail'],['coverage' if stage_name=='retrieval' else 'source_scope'])
    observed('question_evidence_identity', bool(scope and answer),
        bool(scope.get('context_id') and answer.get('id')) and
        scope.get('patient_id')==answer.get('patient_id')==case['patient'] and
        scope.get('question')==case['question'] and question_matches(answer,case,response(evidence('decision_context'))) and
        scope.get('context_id')==answer.get('context_id'),
        'Same patient, original question and nonempty prepared context identity.', ['source_scope','source_interpretation'])
    coverage=answer.get('coverage',{});accounted=declared_units_accounted(answer)
    observed('declared_unit_accounting',bool(coverage),accounted,
        'Every declared unit accounted for. Explicit unresolved dispositions remain unknown and require review; accounting does not establish retrieval recall or clinical meaning.', ['source_interpretation'])
    findings=answer.get('findings',[]);unanswered=answer.get('unanswered',[])
    supported=all(f.get('citations') and all(c.get('citation_verified') is True and c.get('quote') and
        c.get('source_unit_id') and c.get('source',{}).get('record_hash') and
        c.get('source',{}).get('patient_id')==case['patient'] for c in f['citations']) for f in findings)
    observed('supported_answer_or_explicit_unknown',bool(answer),
        bool(findings or unanswered) and supported and isinstance(answer.get('unanswered'),list) and
        isinstance(answer.get('limits'),list) and bool(answer['limits']) and answer.get('clinical_execution_authorized') is False,
        'Findings retain runtime-verified citation identities, or the answer explicitly remains unresolved. Neither implies clinical authority.', ['source_interpretation'])
    decision=response(evidence('decision_context'))
    if not decision:
        add('planning_consumes_answer','blocked' if planning else 'unassessed',
            'No saved decision context binds this planner result to the original answer; patient overlap is insufficient.', ['decision_context','planning','source_interpretation'])
    else:
        observed('planning_consumes_answer',True,
            bool(answer and planning.get('result_id') and decision.get('id')) and
            decision.get('question')==case['question'] and decision.get('patient_id')==case['patient'] and
            decision.get('answer_id')==answer.get('id') and decision.get('answer_digest')==digest(answer) and
            decision.get('planning_result_id')==planning.get('result_id') and decision.get('planning_digest')==digest(planning) and
            decision.get('context_id')==scope.get('context_id'),
            'Saved decision binds exact answer and executed planner result digests.', ['decision_context','planning','source_interpretation'])
    matching=response(evidence('connected_matching'));selection=decision.get('service_selection',{})
    observed('supported_service_selection',bool(decision and matching),
        bool(decision and matching.get('id') and decision.get('rule_versions')) and
        selection.get('matching_id')==matching.get('id') and selection.get('matching_digest')==digest(matching) and
        selection.get('method')=='derived_from_supported_findings' and
        bool(selection.get('finding_ids')) and set(selection['finding_ids'])<=set(f.get('id') for f in findings) and
        stages['matching'].get('original_question_connected') is True,
        'Evaluator-supplied tags alone cannot establish supported service selection; versioned rules and exact finding/match links are required.', ['decision_context','connected_matching'])
    if matching and not decision:checks[-1]['status']='blocked'
    task=response(evidence('connected_handoff')).get('task',{})
    observed('owned_internal_work', bool(task),
        bool(task.get('id') and task.get('owner',{}).get('id') and matching.get('id')) and
        task.get('patient_id')==case['patient'] and task.get('plan_id')==matching.get('id') and
        task.get('clinical_execution_authorized') is False and stages['work_followthrough'].get('original_question_connected') is True,
        'A source-linked, owned internal task is distinct from care delivered.', ['connected_matching','connected_handoff'])
    reopened=response(evidence('task_reopen'))
    saved=([reopened['task']] if reopened.get('task') else reopened.get('tasks',[]))
    saved=[t for t in saved if t.get('id')==task.get('id')]
    preserved=bool(task) and len(saved)==1 and all(saved[0].get(k)==task.get(k) for k in
        ('id','patient_id','plan_id','owner','handoff','clinical_execution_authorized'))
    observed('work_persists',bool(reopened),preserved,
        'Independently reopened task retains identity, ownership and source handoff; its queue state may advance. Handoff response alone does not prove persistence.', ['connected_handoff','task_reopen'])
    follow=response(evidence('followup'))
    observed('followup_reassesses_same_decision',bool(follow),
        bool(decision.get('id') and task.get('id') and follow.get('source_versions') and follow.get('result_id')) and
        follow.get('decision_id')==decision.get('id') and follow.get('task_id')==task.get('id') and
        follow.get('patient_id')==case['patient'] and follow.get('question')==case['question'] and
        follow.get('previous_answer_digest')==digest(answer) and follow.get('external_dispatch_performed') is False and
        isinstance(follow.get('changes'),list) and isinstance(follow.get('unresolved'),list),
        'New source versions produce an explicit changed/unchanged assessment tied to the same decision and task.', ['followup','decision_context','connected_handoff'])
    return {'status':acceptance_status(checks),'checks':checks,
        'qualification':{'clinical':'unassessed','model_roles':'unassessed'},
        'meaning':'Computed mechanical engineering checks on saved execution traces; not clinical validation.'}

def capability_checks(spec, receipt_path, snapshot, evidence_root=ROOT):
    """Consume dedicated verifier receipts, never infer broad readiness from cases.

    Receipts are engineering observations, not clinical labels. Keep both their
    check results and hashed raw evidence so they can be independently inspected.
    """
    required=spec['required_checks']
    if not receipt_path:
        return [{'id':name,'status':'unassessed','detail':'Dedicated workflow verifier evidence not attached.'} for name in required]
    if spec['axis']!='engineering':
        raise ValueError('Machine engineering receipts cannot qualify clinical/model or production authority')
    def path(value):
        result=(Path(evidence_root)/value).resolve()
        if not result.is_relative_to(Path(evidence_root).resolve()) or not result.is_file():raise ValueError('Capability evidence path missing or outside project')
        return result
    p=path(receipt_path);r=json.loads(p.read_text())
    if not isinstance(r,dict) or r.get('schema')!='agere-capability-acceptance/1' or r.get('requirement_id')!=spec['id']:
        raise ValueError('Capability receipt contract mismatch')
    if not snapshot or r.get('snapshot_digest')!=digest(snapshot):raise ValueError('Capability receipt belongs to another snapshot')
    rows=r.get('checks',[])
    if not isinstance(rows,list) or any(not isinstance(v,dict) for v in rows) or len(rows)!=len(required) or {v.get('id') for v in rows}!=set(required):raise ValueError('Capability receipt must cover every declared check exactly once')
    for row in rows:
        acceptance_status([row])
        if row['status']=='passed' and not row.get('evidence'):raise ValueError('Passed capability check lacks raw evidence')
        for e in row.get('evidence',[]):
            if hashlib.sha256(path(e['path']).read_bytes()).hexdigest()!=e['sha256']:raise ValueError('Capability raw evidence hash mismatch: '+e['path'])
    # Raw execution evidence is immutable. Current implementation compatibility
    # is a separate assertion and must be checked when the verifier declares it.
    implementation=r.get('implementation_sha256',{})
    if not isinstance(implementation,dict):raise ValueError('Capability implementation manifest must be a mapping')
    for name,expected in implementation.items():
        if hashlib.sha256(path(name).read_bytes()).hexdigest()!=expected:
            raise ValueError('Capability evaluated implementation changed: '+name)
    return [{**row,'receipt':str(p),'receipt_sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for row in rows]

def release_acceptance(cases, contract_path=CONTRACT, capability_evidence=None, snapshot=None, evidence_root=ROOT):
    contract=json.loads(Path(contract_path).read_text())
    expected=contract['individual_cases']
    if sorted(c['id'] for c in cases)!=sorted(expected):raise ValueError('Release contract requires the exact declared individual case set')
    if set(capability_evidence or {})-{s['id'] for s in contract['requirements'] if s['evidence_kind']!='individual_workflows'}:
        raise ValueError('Unknown capability evidence requirement')
    requirements=[]
    for spec in contract['requirements']:
        # Only the individual path is measured by these six traces. Missing
        # population/qualification/package evidence stays visible, not inferred.
        error=None;verification=None;scope=None
        if spec['evidence_kind']=='individual_workflows':
            checks=[{'id':c['id'],'status':c['acceptance']['status']} for c in cases]
        else:
            try:
                checks=capability_checks(spec,(capability_evidence or {}).get(spec['id']),snapshot,evidence_root)
                if checks and checks[0].get('receipt'):
                    receipt=json.loads(Path(checks[0]['receipt']).read_text())
                    scope=receipt.get('scope')
                    replay_path=receipt.get('workflow_verification')
                    if replay_path:
                        # Display only a report already verified as raw evidence.
                        if replay_path not in {e['path'] for c in checks for e in c.get('evidence',[])}:
                            raise ValueError('Workflow verification lacks hashed receipt evidence')
                        verification=json.loads((Path(evidence_root)/replay_path).read_text())
                        if not isinstance(verification,dict) or verification.get('schema')!='agere-current-workflow-matrix/1' or verification.get('snapshot_digest')!=digest(snapshot):
                            raise ValueError('Workflow verification contract or snapshot mismatch')
            except (ValueError,KeyError,TypeError,OSError) as exc:
                # A bad receipt blocks every check it purported to establish.
                # Do not hide other requirements or reuse a cached green status.
                error={'status':'invalid_evidence','receipt':(capability_evidence or {}).get(spec['id']),'detail':str(exc)}
                verification=None;scope=None
                checks=[{'id':name,'status':'blocked','detail':'Acceptance evidence could not be verified: '+str(exc),'evidence':[]} for name in spec['required_checks']]
        requirements.append({**spec,'status':acceptance_status(checks),'observations':checks,
            'evidence_error':error,
            'verification_scope':scope,'workflow_verification':verification,
            'remaining_evidence':None if acceptance_status(checks)=='passed' else spec['required_evidence']})
    engineering=[r for r in requirements if r['axis']=='engineering']
    return {'contract_id':contract['id'],'contract_path':str(contract_path),
        'contract_sha256':hashlib.sha256(Path(contract_path).read_bytes()).hexdigest(),
        'engineering_status':acceptance_status(engineering),'requirements':requirements,
        'engineering_requirements_passed':sum(r['status']=='passed' for r in engineering),
        'engineering_requirements_total':len(engineering),'scope_areas':sorted(set(a for r in requirements for a in r['scope_areas'])),
        'invalid_evidence_requirements':[r['id'] for r in requirements if r['evidence_error']],
        'limitations':'Unassessed capability requirements need their dedicated workflow evidence. Existing isolated components are not declared absent or accepted. External production dependencies and clinical/model qualification are separate from local engineering completion.'}

def load(folder,name):
    p=Path(folder)/(name+'.json')
    return json.loads(p.read_text()) if p.exists() else {'status':'not_run'}

def response(trace):return trace.get('response',{}) if trace.get('status')=='executed' else {}

def stage(status,detail,**facts):return {'status':status,'detail':detail,**facts}

def patient_bound(nodes,patient):
    # This scorecard verifies a narrow, conservative graph shape. An atom in
    # any/exclude or a second unrestricted root cannot establish this scope.
    roots=[n for n in nodes if not n.get('inputs')]
    return bool(roots) and all(n.get('operation')=='cohort' and any(
        a.get('kind')=='patient' and a.get('field')=='patient_id' and a.get('op')=='eq' and a.get('value')==patient
        for a in n.get('parameters',{}).get('specification',{}).get('all',[])) for n in roots)

def reader_baselines(folder,case):
    files=sorted(Path(folder).glob('reader_*.json'));verified=[];errors=[]
    for p in files:
        t=json.loads(p.read_text());r=response(t)
        if not r:errors.append({'trace':str(p),'error':t.get('error',t['status'])});continue
        verified.append({'id':r.get('id'),'patient':r.get('request',{}).get('target',{}).get('patient_id'),
            'target':r.get('request',{}).get('target'), 'roles':r.get('request',{}).get('roles'),
            'same_workflow_patient':r.get('request',{}).get('target',{}).get('patient_id')==case['patient'],
            'context_digest':r.get('context_digest'),'trace':str(p)})
    return stage('runtime_error' if errors else 'isolated_diagnostic' if verified else 'not_run',
        'Reopened existing exact-statement comparisons: lexical, MiniLM, Kev, d1, majority and abstain baselines. Same text within each comparison; NOT the full question evidence used above. Patient overlap alone does not connect the paths. No new inference, fitting, adjudication or promotion.',
        comparisons=verified,errors=errors,original_question_connected=False,clinical_quality='unadjudicated')

def score_case(case,folder,planning_folder=None,trace_overrides=None):
    folder=Path(folder);stages={};trace_paths={f.stem:str(f) for f in sorted(folder.glob('*.json'))}
    for name,path in (trace_overrides or {}).items():
        if name not in {'complete_source_selection','source_scope','interpretation_preflight','source_interpretation','matching','handoff','connected_matching','connected_handoff','reject_unavailable','reject_wrong-plan','decision_context','task_reopen','followup'}:
            raise ValueError('Unknown workflow stage override')
        if not Path(path).is_file():raise ValueError('Workflow override trace missing')
        trace_paths[name]=str(path)
    def evidence(name):
        return json.loads(Path(trace_paths[name]).read_text()) if name in trace_paths else {'status':'not_run'}
    coverage=response(evidence('coverage'));comparison=response(evidence('retrieval'))
    inventory={r['source_category']:r for r in coverage.get('categories',[])}
    wanted=case['required_categories_if_received'];present=[s for s in wanted if inventory.get(s,{}).get('received_records',0)>0]
    absent=sorted(set(wanted)-set(present))
    missing=[s for s in present if not inventory[s]['candidates']]
    stages['retrieval']=stage('coverage_gap' if missing else 'checked' if coverage else 'not_run',
        'Minimal named-category probe only; no clinical relevance or complete recall label.',
        missing_candidate_categories=missing,received_anchor_categories=present,anchor_categories_not_observed=absent)
    losses=[s for s in present if inventory[s]['candidates'] and not inventory[s]['reranked_selected']]
    baseline_missing=[s for s in present if not inventory[s]['rrf_selected']]
    stages['reranking']=stage('not_comparable' if comparison and comparison.get('candidate_set_identical') is not True else 'selection_gap' if losses else 'checked' if comparison and coverage else 'not_run',
        'RRF and BGE on the same candidates; cannot recover categories missing before reranking.',
        missing_selected_categories=losses,rrf_missing_categories=baseline_missing,
        bge_missing_categories=[s for s in present if not inventory[s]['reranked_selected']],
        identical_candidates=comparison.get('candidate_set_identical'),clinical_relevance_adjudicated=False)
    t=evidence('source_scope');scope=response(t);omissions=scope.get('context_omissions',[])
    missing_scope=sorted(set(present)-set(scope.get('selected_categories',[])))
    status='runtime_error' if t['status'] in ('runtime_error','transport_error') else 'input_mismatch' if scope and scope.get('question')!=case['question'] else 'incomplete' if omissions or missing_scope else 'checked' if scope else 'not_run'
    stages['source_scope']=stage(status,'Typed source closure and transport completeness; source sufficiency remains unvalidated.',
        selected_records=scope.get('selected_record_count'),omitted_units=len(omissions),missing_anchor_categories=missing_scope,
        question_retained=scope.get('question')==case['question'],context_id=scope.get('context_id'),error=t.get('error'))
    t=evidence('source_interpretation');r=response(t);pre=evidence('interpretation_preflight')
    aligned=bool(r) and question_matches(r,case,response(evidence('decision_context'))) and r.get('patient_id')==case['patient'] and (not scope or r.get('context_id')==scope.get('context_id'))
    status='input_mismatch' if r and not aligned else 'executed_unvalidated' if r else 'runtime_error' if t['status'] in ('runtime_error','transport_error') else 'blocked_by_scope' if pre['status']=='runtime_error' else 'not_run'
    stages['source_interpretation']=stage(status,'Exact citation validation is distinct from clinical meaning or adjudication.',
        answer_id=r.get('id'),question_patient_context_retained=aligned,findings=len(r.get('findings',[])),review_questions=len(r.get('review_steps',[])),
        source_coverage=r.get('coverage'),error=t.get('error',pre.get('error')),new_paid_api_calls=r.get('new_paid_api_calls'),clinical_quality='unadjudicated')
    stages['reader_comparison']=reader_baselines(folder,case)
    t=load(planning_folder or folder,'planning');r=response(t);plan=r.get('plan',r.get('graph',{}))
    nodes=plan.get('nodes',[])
    exact_patient=patient_bound(nodes,case['patient'])
    status='runtime_error' if t['status'] in ('runtime_error','transport_error') else 'input_mismatch' if r and r.get('question')!=case['planning_question'] else 'unsupported' if r and plan.get('coverage')=='unsupported' else 'executed_unvalidated' if r and exact_patient else 'scope_not_verified' if r else 'not_run'
    stages['planning']=stage(status,'Actual natural-language composition, not the manually selected program baseline.',
        error=t.get('error'),coverage=plan.get('coverage'),exact_patient_predicate=exact_patient,
        original_question_retained=r.get('question')==case['planning_question'],operations=[n['operation'] for n in nodes],
        unmet_requirements=plan.get('unmet_requirements'),new_paid_api_calls=r.get('new_paid_api_calls'),latency_seconds=t.get('elapsed_seconds'),
        work_proposals=[{'id':x['result'].get('proposal_id'),'program':x['node'].get('parameters',{}).get('program'),
                         'counts':x['result'].get('counts'), 'program_suitability_validated':False}
                        for x in r.get('results',[]) if x.get('node',{}).get('operation')=='worklist'])
    t=evidence('matching');r=response(t);options={o['capability']['id']:o['status'] for o in r.get('options',[])}
    excluded=[o['capability_id'] for o in r.get('excluded',[])]
    controls=bool(r) and options.get('available')=='requires_verification' and all(options.get(k)=='blocked' for k in ('unavailable','wrong-plan')) and 'unrelated' in excluded
    rejection_checks=all(evidence('reject_'+k).get('http_status') in (400,409) for k in ('unavailable','wrong-plan'))
    stages['matching']=stage('isolated_diagnostic' if controls and rejection_checks else 'control_failure' if r else t['status'],
        'Diagnostic assessment origin and manually supplied need tag; NOT an automatic continuation of the saved question/answer.',
        observed_options=options,excluded=excluded,constraint_checks_passed=controls and rejection_checks,
        original_question_connected=False,benefits_verified=False,error=t.get('error',t.get('reason')))
    t=evidence('handoff');r=response(t)
    stages['work_followthrough']=stage('isolated_diagnostic' if r else 'not_run',
        'Internal diagnostic handoff only; original natural-language question is not linked into this branch.',
        task_id=r.get('task',{}).get('id'),task_state=r.get('task',{}).get('state'),original_question_connected=False,clinical_care_delivered=False)
    connected=response(evidence('connected_matching'))
    if connected:
        answer=response(evidence('source_interpretation'));origin=connected.get('origin',{})
        linked=bool(answer) and origin.get('kind')=='answer' and origin.get('id')==answer.get('id') and origin.get('patient_id')==case['patient'] and origin.get('question')==case['question'] and origin.get('findings')==answer.get('findings')
        options={o['capability']['id']:o['status'] for o in connected.get('options',[])}
        guarded=options.get('available')=='requires_verification' and all(options.get(k)=='blocked' for k in ('unavailable','wrong-plan'))
        stages['matching']=stage('connected_review_unvalidated' if linked and guarded else 'lineage_failure',
            'Actual saved answer carries its original question and every finding into service matching. The service tag is an explicit evaluator choice; no model inferred a clinical indication.',
            original_question_connected=linked,manual_service_selection=True,planning_branch_connected=False,
            answer_id=answer.get('id'),plan_id=connected.get('id'),finding_count=len(origin.get('findings',[])),
            observed_options=options,benefits_verified=False,diagnostic_constraint_checks_passed=controls and rejection_checks)
        handoff=response(evidence('connected_handoff'));task=handoff.get('task',{});packet=task.get('handoff',{})
        retained=linked and packet.get('origin')==origin and packet.get('human_review_completed') is False and packet.get('external_dispatch_performed') is False and task.get('state')=='handoff_prepared_for_review'
        stages['work_followthrough']=stage('connected_review_unvalidated' if retained else 'lineage_failure' if handoff else 'not_run',
            'Internal evidence-review handoff retains the saved answer lineage. No contact, care, clinical review, eligibility or automatic service selection is established.',
            original_question_connected=retained,manual_service_selection=True,planning_branch_connected=False,
            task_id=task.get('id'),task_state=task.get('state'),clinical_care_delivered=False)
    decision=response(evidence('decision_context'));local_plan=response(load(planning_folder or folder,'planning'))
    if decision.get('schema')=='original-question-decision/1':
        answer=response(evidence('source_interpretation'))
        plan_bound=(decision.get('planning_result_id')==local_plan.get('result_id') and
            decision.get('planning_digest')==digest(local_plan) and decision.get('answer_digest')==digest(answer) and
            decision.get('patient_id')==case['patient'] and decision.get('question')==case['question'])
        stages['planning']=stage('executed_unvalidated' if plan_bound else 'lineage_failure',
            'Shared saved-answer planner derives documentary review topics. The recorded method distinguishes the local source-type baseline from the unqualified buyer-aware machine candidate. No clinical indication or urgency is established.',
            original_question_retained=plan_bound,exact_patient_predicate=plan_bound,
            operations=['documentary_review_topics'],unmet_requirements=local_plan.get('unresolved',[]),
            new_paid_api_calls=0,review_topics=local_plan.get('review_topics',[]),unmapped_finding_ids=local_plan.get('unmapped_finding_ids',[]))
        selected=decision.get('service_selection',{});origin=connected.get('origin',{})
        selected_ids=set(selected.get('finding_ids',[]))
        expected=[f for f in answer.get('findings',[]) if f['id'] in selected_ids]
        linked=(plan_bound and bool(connected) and selected.get('matching_id')==connected.get('id') and
            selected.get('matching_digest')==digest(connected) and origin.get('id')==answer.get('id') and
            origin.get('patient_id')==case['patient'] and origin.get('findings')==expected and bool(expected))
        stages['matching']=stage('connected_review_unvalidated' if linked else 'lineage_failure',
            'Cited finding categories supply review topics through the shared matcher. Buyer services and payer/participation requirements remain unverified.',
            original_question_connected=linked,manual_service_selection=False,planning_branch_connected=plan_bound,
            answer_id=answer.get('id'),plan_id=connected.get('id'),finding_count=len(expected),benefits_verified=False)
        handoff=response(evidence('connected_handoff'));task=handoff.get('task',{});packet=task.get('handoff',{})
        retained=(linked and handoff.get('decision_id')==decision.get('id') and task.get('plan_id')==connected.get('id') and
            packet.get('origin')==origin and packet.get('human_review_completed') is False and packet.get('external_dispatch_performed') is False)
        stages['work_followthrough']=stage('connected_review_unvalidated' if retained else 'lineage_failure',
            'Persistent internal work retains original-question decision and exact answer/source versions; no care delivery or clinical review is asserted.',
            original_question_connected=retained,manual_service_selection=False,planning_branch_connected=plan_bound,
            task_id=task.get('id'),task_state=task.get('state'),clinical_care_delivered=False)
    acceptance=case_acceptance(case,stages,evidence,response(load(planning_folder or folder,'planning')))
    for check in acceptance['checks']:
        check['evidence_paths']=[str(Path(planning_folder or folder)/'planning.json') if n=='planning' else trace_paths[n] for n in check['traces'] if n=='planning' or n in trace_paths]
    return {'id':case['id'],'patient':case['patient'],'question':case['question'],'stages':stages,
        'end_to_end':'complete' if acceptance['status']=='passed' else 'incomplete','acceptance':acceptance,
        'clinical_validated':False,'expectations':'assistant_authored_development_checks','trace_links':trace_paths,
        'planning_trace':str(Path(planning_folder or folder)/'planning.json')}

def build(plan_path,baseline,planning=None,trace_overrides=None,contract_path=CONTRACT,capability_evidence=None):
    plan=json.loads(Path(plan_path).read_text())
    if isinstance(planning,dict) and set(planning)!={c['id'] for c in plan['cases']}:
        raise ValueError('Planning trace map must explicitly cover every declared workflow')
    def planning_folder(case):
        if isinstance(planning,dict):return Path(planning[case['id']])
        return Path(planning)/case['id'] if planning else None
    if set(trace_overrides or {})-{c['id'] for c in plan['cases']}:raise ValueError('Unknown workflow override case')
    cases=[score_case(c,Path(baseline)/c['id'],planning_folder(c),(trace_overrides or {}).get(c['id'])) for c in plan['cases']]
    statuses={s:dict(Counter(c['stages'][s]['status'] for c in cases)) for s in STAGES}
    files=sorted(set(f for c in cases for f in list(c['trace_links'].values())+[c['planning_trace']] if Path(f).exists()))
    baseline_cases=[score_case(c,Path(baseline)/c['id']) for c in plan['cases']] if planning else cases
    before_counts=dict(Counter(c['stages']['planning']['status'] for c in baseline_cases))
    return {'schema':'agere-workflow-scorecard/2','plan_sha256':hashlib.sha256(Path(plan_path).read_bytes()).hexdigest(),
        'plan_path':str(plan_path),'cases':cases,'stage_counts':statuses,
        'complete_workflows':sum(c['acceptance']['status']=='passed' for c in cases),'workflow_count':len(cases),
        'engineering_status_counts':dict(Counter(c['acceptance']['status'] for c in cases)),
        'release_acceptance':release_acceptance(cases,contract_path,capability_evidence,plan.get('snapshot')),
        'connected_answer_handoffs':sum(c['stages']['work_followthrough']['status']=='connected_review_unvalidated' for c in cases),
        'scope':'Six previously inspected original-training development examples. Engineering diagnostics, not a representative or independent clinical benchmark.',
        'timing_note':'Baseline scope/planning probes overlapped in time on one serialized server; their wall times include contention. Do not claim isolated latency improvements from those timings.',
        'targets_entered_runtime':False,'clinical_validated':False,'model_promoted':False,
        'planner_comparison':{'before':before_counts,'current':statuses['planning'],
            'before_traces':{c['id']:c['planning_trace'] for c in baseline_cases},
            'meaning':'Same six development questions, corrected shared contract. These previously inspected cases are regression diagnostics, not independent generalization evidence.'},
        'trace_sha256':{f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files},
        'scorecard_implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'completion_rule':'Completion is computed from every required engineering check on connected evidence. Unknown clinical facts can pass when explicitly represented; absent implementation or evidence cannot. Six complete individual examples alone cannot complete the population/VBC, qualification and packaging contract.'}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--plan',default='research/agere/workflow-evaluation-v1/plan.json');p.add_argument('--baseline',default='artifacts/agere-workflow-evaluation-v1/baseline');p.add_argument('--planning');p.add_argument('--output',required=True);a=p.parse_args()
    r=build(a.plan,a.baseline,a.planning);Path(a.output).write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r['stage_counts'],indent=2))
