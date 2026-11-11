"""Loopback research interface; hosted stack routes require explicit enablement."""
import json
import threading,mimetypes,time
from functools import lru_cache
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from .warehouse import lines

@lru_cache(maxsize=1)
def planner():
    from ..question_planner import QuestionPlanner
    return QuestionPlanner(Path(__file__).resolve().parents[2]/'artifacts/research-training-semantic')

def query(run,question):
    metadata=json.loads((Path(run)/'run.json').read_text())
    if metadata.get('schema')=='clinical-workflow-run/1':
        from .clinical.query import query as clinical_query
        return clinical_query(run,question)
    from .query_scope import scope_query
    cards=json.loads((Path(run)/'insights.json').read_text())
    scoped=scope_query(question,cards,json.loads((Path(run)/'run.json').read_text())['input_requirements'])
    plan=planner().propose(question)
    evidence=None
    if (Path(run)/'evidence.sqlite3').exists():
        from .evidence_index import search
        evidence=search(Path(run)/'evidence.sqlite3',question)
    return {'question':question,'trained_domain_proposal':plan,**scoped,'narrative_evidence':evidence,
            'scope':'Bounded screening dispatch; trained domain proposal is informational only','synthetic':True}

def _pack_status():
    try:
        from . import packs;return packs.status()
    except Exception as exc:return {'error':str(exc)[:120]}


def _failure_receipts(exc):
    """Retained work, usage and isolation from a failed compose or resume."""
    out={}
    if getattr(exc,'cost_receipt',None):out['cost_receipt']=exc.cost_receipt
    if getattr(exc,'cost_telemetry',None):out['cost_telemetry']=exc.cost_telemetry
    if getattr(exc,'evaluation',None):out['evaluation']=exc.evaluation
    if getattr(exc,'action_context',None):out['action_context']=exc.action_context
    if getattr(exc,'action_context_error',None):out['action_context_error']=exc.action_context_error
    if hasattr(exc,'new_paid_api_calls'):out['new_paid_api_calls']=exc.new_paid_api_calls
    if getattr(exc,'planning_economics',None):out['planning_economics']=exc.planning_economics
    return out

def answer_feedback(stack,body):
    from os import environ
    if environ.get('AGERE_COMPONENT_LEARNING','off')=='on':
        from .clinical.component_learning import handle_feedback
        return handle_feedback(stack,body)
    from .clinical import learning
    if set(body)-{'question','note','expected','rule'} or not isinstance(body.get('question'),str) or not isinstance(body.get('note'),str):
        raise ValueError('Correction needs question and note (optional expected)')
    return learning.record_correction(stack.path,body['question'],body['note'],body.get('expected'),body.get('rule'))


def serve(run,port,annotations=None,evidence_queries=None,stack=None,allow_live_stack=False,recovery=None):
    if recovery and allow_live_stack:raise ValueError('Restored installations have hosted inference disabled')
    from .local_release import READ_POSTS,recovery_routes,check_local_request
    work_resume = bool(recovery and recovery.get('mode') == 'owned_work_only')
    local_operations = bool(recovery and recovery.get('mode') == 'local_operations')
    recovery_gets, recovery_posts = recovery_routes(recovery.get('mode', 'inspection_only')) if recovery else (set(),set())
    root=Path(run).resolve()
    if not (root/'run.json').exists():raise ValueError('Completed run required')
    annotation_root=Path(annotations).resolve() if annotations is not None else None
    reader_metadata=None
    if annotation_root is not None:
        reader_metadata=json.loads((annotation_root/'run.json').read_text())
        clinical_metadata=json.loads((root/'run.json').read_text())
        if reader_metadata.get('schema')!='factorized-reader-run/2' or reader_metadata.get('synthetic') is not True:
            raise ValueError('Completed synthetic reader run required')
        if not clinical_metadata.get('cache_key_digest') or reader_metadata.get('source_run')!=clinical_metadata['cache_key_digest']:
            raise ValueError('Reader annotations belong to another clinical snapshot')
        transfer_path=annotation_root.parent/'warehouse-transfer.json'
        if transfer_path.exists():
            transfer=json.loads(transfer_path.read_text())
            if transfer.get('model_identity')==reader_metadata['model_identity']:
                reader_metadata['transfer_diagnostic']={k:transfer[k] for k in ('examples','metrics','review_candidates','scope')}
    query_root=Path(evidence_queries).resolve() if evidence_queries is not None else None
    connected_stack=None
    request_locks=None
    if stack is not None:
        from .clinical.stack_runtime import Stack
        connected_stack=Stack(stack,allow_live=allow_live_stack)
        # No service-wide lock: each request holds only the resources it writes (request_locks.resources); questions run
        # concurrently. stack.answer_lock is the per-thread write lock interpretation windows release.
        from .request_locks import install as install_request_locks
        request_locks=install_request_locks(connected_stack)
        clinical_metadata=json.loads((root/'run.json').read_text())
        if connected_stack.retriever.meta['source_run']!=clinical_metadata.get('cache_key_digest'):
            raise ValueError('Connected stack belongs to another clinical snapshot')
    if query_root is not None:
        query_metadata=json.loads((query_root/'run.json').read_text())
        clinical_metadata=json.loads((root/'run.json').read_text())
        if query_metadata.get('schema')!='evidence-query/1' or query_metadata.get('synthetic') is not True or query_metadata.get('source_run')!=clinical_metadata.get('cache_key_digest'):
            raise ValueError('Evidence query index belongs to another clinical snapshot')
        if reader_metadata is not None and query_metadata.get('model_identity')!=reader_metadata.get('model_identity'):
            raise ValueError('Evidence query index belongs to another annotation model')
    validation_cache=None
    validation_index=None
    if connected_stack is not None:
        from .clinical.native_snapshot_cache import install,prepare_vocabulary_index
        validation_index=prepare_vocabulary_index(connected_stack.path)
        validation_cache=install(connected_stack.path)
        from .clinical.answer_retirement import install_origin_guard
        install_origin_guard()
        from .clinical.graph_decision import install as install_graph_origin
        install_graph_origin()
        from .clinical.medication_source_review import install as install_medication_review
        install_medication_review()
        from .clinical.patient_context import install_plan_guard
        install_plan_guard()
        from .clinical.patient_fit_work import install as install_patient_fit
        install_patient_fit()
        from .clinical.reference_applicability import install as install_reference_applicability
        install_reference_applicability()
        from .clinical.reference_lifecycle import install as install_reference_lifecycle
        install_reference_lifecycle()
        from .clinical.option_evidence_registry import install as install_option_registry
        install_option_registry()
        from .clinical.option_component_registry import install as install_components
        install_components()
        from .clinical.cardiometabolic_components import install as install_cardiometabolic
        install_cardiometabolic()
        from .clinical.appointment_navigation import install as install_appointment_navigation
        install_appointment_navigation()
        from .clinical.literal_reference_runtime import install as install_literal_patterns
        install_literal_patterns()
        from .clinical.structured_transport import install as install_structured_transport
        install_structured_transport()
        from .clinical.reference_table_context import install as install_reference_tables
        from .clinical.reference_reconciliation_transport import install as install_reconciliation_tables
        install_reference_tables()
        install_reconciliation_tables()
        from .clinical.interpretation_recovery import install as install_interpretation_recovery
        install_interpretation_recovery()
        from .clinical.linked_claim_integrity import install as install_linked_claims
        install_linked_claims()
        from .clinical.meaning_source_dispatch import install as install_source_meaning
        install_source_meaning(connected_stack.path)
        from .clinical.source_synthesis import configure_plan_answers
        configure_plan_answers(connected_stack.retriever)
        from .clinical.reference_assessment import install as install_reference_assessments
        install_reference_assessments()
    class Handler(BaseHTTPRequestHandler):
        def send_error(self, code, message=None, explain=None):
            # API callers always receive their declared JSON contract, including
            # denied recovery routes. HTML errors used to break initial UI fetches.
            if urlsplit(self.path).path.startswith('/api/'):
                raw=json.dumps({'error':message or self.responses.get(code,('Request failed',))[0]}).encode()
                self.send_response(code);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
                return
            super().send_error(code,message,explain)
        def _workspace(self,method,path,body=None):
            # Customer workspaces (workspace_api.py) run outside the global stack lock: builds take minutes.
            from . import workspace_api
            if path=='/workspaces':
                raw=workspace_api.page().encode();mime='text/html; charset=utf-8';code=200
            else:
                code,payload=workspace_api.handle(method,path,body);raw=json.dumps(payload,default=str).encode();mime='application/json'
            self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            host=self.headers.get('Host','').split(':')[0]
            origin=self.headers.get('Origin')
            if host not in {'127.0.0.1','localhost'} or (origin and origin not in {f'http://127.0.0.1:{port}',f'http://localhost:{port}'}):self.send_error(403);return
            if urlsplit(self.path).path.startswith('/api/workspaces') and not recovery:
                length=int(self.headers.get('Content-Length','0') or 0)
                if not 0<=length<=32000 or (length and self.headers.get('Content-Type','').split(';')[0]!='application/json'):self.send_error(400,'Bounded JSON body required');return
                try:body=json.loads(self.rfile.read(length)) if length else {}
                except ValueError:self.send_error(400,'Invalid JSON');return
                if not isinstance(body,dict):self.send_error(400,'Object body required');return
                self._workspace('POST',urlsplit(self.path).path,body);return
            if connected_stack is None:self.send_error(404,'Connected stack unavailable');return
            execution=None
            held=ExitStack()
            try:
                if recovery and urlsplit(self.path).path not in recovery_posts:
                    raise PermissionError('This route is unavailable in installation mode '+recovery.get('mode','inspection_only')+'. Hosted inference and workspace-bound learning are disabled.')
                length=int(self.headers.get('Content-Length','0'))
                intake_path=urlsplit(self.path).path
                body_bound=2100000 if intake_path in ('/api/stack/portable-intake/preview','/api/stack/portable-intake/save','/api/stack/action-work/create') else 32000
                if not 0<length<=body_bound or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Bounded JSON body required')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('Object body required')
                path=urlsplit(self.path).path
                if local_operations:check_local_request(path,body)
                held.enter_context(request_locks.for_route(path,body))  # per-resource locks (request_locks.py), never one global lock
                # Cover dispatch-journal start, provider events and finish as well
                # as composition; evaluation operational records stay in scratch.
                from .clinical import evaluation
                if path == '/api/stack/compose':
                    held.enter_context(evaluation.request_scope(self.headers, connected_stack.path))
                from . import request_lineage
                execution=None if recovery and path in READ_POSTS else request_lineage.begin(connected_stack.path,path,body,
                    tracking=self.headers.get('X-Agere-Progress-Key')if path in ('/api/stack/compose','/api/stack/investigation/resume')else None)
                if execution:held.enter_context(execution)  # activates the lineage scope; closed with the request, even on error
                if path=='/api/stack/appointment-graph/work':
                    from .clinical import appointment_graph
                    result=appointment_graph.prepare_work(connected_stack,body)
                elif path=='/api/stack/appointment-graph/inspect':
                    from .clinical import appointment_graph
                    if set(body)!={'receipt_id','patient_id'}:raise ValueError('Exact appointment graph receipt and patient required')
                    row=appointment_graph.inspect(connected_stack.path,body['receipt_id'])
                    patient=next((p for p in row['patients'] if p['patient_id']==body['patient_id']),None)
                    if patient is None:raise ValueError('Patient absent from appointment graph receipt')
                    result={'receipt_id':row['id'],'parameters':row['parameters'],'contract':row['contract'],'patient':patient,'limits':row['limits']}
                elif path.startswith('/api/stack/appointment-navigation/'):
                    from .clinical import appointment_navigation as navigation
                    action=path.rsplit('/',1)[1]
                    actions={'prepare':navigation.prepare,'task':navigation.task_view,'preview':navigation.proposal_snapshot,'save':navigation.prepare_proposal}
                    if action not in actions:raise ValueError('Unknown appointment navigation action')
                    result=actions[action](connected_stack.path,body)
                elif path.startswith('/api/stack/appointment-followthrough/'):
                    from .clinical import appointment_followthrough as af, appointment_qualification as aq
                    action=path.rsplit('/',1)[-1]
                    if action=='prepare':result=af.summary(af.prepare(connected_stack.path,body))
                    elif action=='work':result=af.prepare_work(connected_stack.path,body)
                    elif action in ('history','qualification'):
                        if body:raise ValueError('Empty request required')
                        result=af.history(connected_stack.path) if action=='history' else aq.status(connected_stack.path)
                        if action=='qualification' and result.get('current'):
                            from .clinical import appointment_uncertainty
                            result['comparison_uncertainty']=appointment_uncertainty.status(connected_stack.path,result)
                    elif action=='inspect':
                        if set(body)!={'review_id','patient_id'}:raise ValueError('Exact appointment review and patient required')
                        review=af.inspect(connected_stack.path,body['review_id'])
                        if body['patient_id'] not in review['patient_ids']:raise ValueError('Patient absent from appointment review')
                        result={'review_id':review['id'],'contract':review['contract'],'limits':review['limits'],'patient':next(p for p in review['patients'] if p['patient_id']==body['patient_id'])}
                    elif action=='open':
                        if set(body)!={'review_id'}:raise ValueError('Exact appointment review required')
                        result=af.summary(af.inspect(connected_stack.path,body['review_id']))
                    else:raise ValueError('Unknown appointment action')
                elif path in ('/api/stack/portable-intake/example','/api/stack/portable-intake/preview','/api/stack/portable-intake/save','/api/stack/portable-intake/list','/api/stack/portable-intake/inspect','/api/stack/portable-intake/compare'):
                    from .clinical import portable_intake
                    action={'example':portable_intake.example,'preview':portable_intake.preview,'save':portable_intake.save,
                            'list':portable_intake.listing,'inspect':portable_intake.inspect,'compare':portable_intake.compare}[path.rsplit('/',1)[1]]
                    result=action(connected_stack.path,body)
                elif path=='/api/stack/retrieval-comparison/prepare':
                    from .clinical import retrieval_comparison
                    result=retrieval_comparison.prepare(connected_stack.retriever,body)
                elif path=='/api/stack/retrieval-comparison/inspect':
                    from .clinical import retrieval_comparison
                    if set(body)!={'id'}:raise ValueError('Exact comparison required')
                    result=retrieval_comparison.inspect(connected_stack.retriever,body['id'])
                elif path=='/api/stack/retrieval-comparison/coverage':
                    from .clinical import retrieval_coverage
                    result=retrieval_coverage.inspect(connected_stack.retriever,body)
                elif path in ('/api/stack/retrieval-source-plan/preview','/api/stack/retrieval-source-plan/prepare','/api/stack/retrieval-source-plan/inspect'):
                    from .clinical import retrieval_source_plan
                    result=getattr(retrieval_source_plan,path.rsplit('/',1)[1])(connected_stack.retriever,body)
                elif path=='/api/stack/source-plan-answers/compose':
                    from .clinical import plan_answer
                    result=plan_answer.prepare(connected_stack.retriever,body)
                elif path in ('/api/stack/source-plan-answers/attach','/api/stack/source-plan-answers/listing','/api/stack/source-plan-answers/prepare','/api/stack/source-plan-answers/inspect'):
                    from .clinical import source_plan_answers
                    result=getattr(source_plan_answers,path.rsplit('/',1)[1])(connected_stack.retriever,body)
                elif path in ('/api/stack/source-plan-reconciliation/preview','/api/stack/source-plan-reconciliation/inspect','/api/stack/source-plan-reconciliation/reconcile'):
                    from .clinical import source_plan_reconciliation
                    if path.endswith('/reconcile'):
                        from .clinical.stack_runtime import ROOT
                        from .gateway import BudgetLedger, default_ledger_path
                        ledger=BudgetLedger(default_ledger_path())
                        try:result=source_plan_reconciliation.reconcile(connected_stack.retriever,body,ledger,allow_live=connected_stack.allow_live)
                        finally:ledger.close()
                    else:
                        result=getattr(source_plan_reconciliation,path.rsplit('/',1)[1])(connected_stack.retriever,body)
                        if path.endswith('/preview'):
                            progress=source_plan_reconciliation.location(connected_stack.path,result['id']).with_name(result['id']+'-progress.json')
                            if progress.exists():
                                state=json.loads(progress.read_text())
                                if state.get('id')==result['id'] and state.get('status')=='incomplete':
                                    result['saved_failure']={'error':state.get('error'),'retry_allowed':False}
                elif path in ('/api/stack/source-plan-recovery/preview','/api/stack/source-plan-recovery/recover','/api/stack/source-plan-recovery/inspect'):
                    from .clinical import source_plan_recovery
                    from .clinical.stack_runtime import ROOT
                    from .gateway import BudgetLedger, default_ledger_path
                    ledger=BudgetLedger(default_ledger_path())
                    try:result=getattr(source_plan_recovery,path.rsplit('/',1)[1])(connected_stack.retriever,body,ledger)
                    finally:ledger.close()
                elif path=='/api/stack/search':
                    if not set(body)<={'question','patient','cohort','analysis_window'}:raise ValueError('Unknown search fields')
                    from .clinical.cohorts import select
                    selection=select(connected_stack.path,body.get('cohort'),patient=body.get('patient') or None)
                    result={'synthetic':True,'retrieval':connected_stack.retriever.search(body.get('question'),patient=body.get('patient') or None,
                              patient_ids=selection['patient_ids'],analysis_window=body.get('analysis_window')),
                              'cohort':{k:v for k,v in selection.items() if k not in ('patient_ids','unknown_patient_ids')},'new_paid_api_calls':0}
                    result['retrieval']['comparison_request']=body
                elif path=='/api/stack/cohort':
                    if not set(body)<={'cohort','patient'}:raise ValueError('Unknown cohort fields')
                    from .clinical.cohorts import select
                    selected=select(connected_stack.path,body.get('cohort'),patient=body.get('patient') or None)
                    result={k:v for k,v in selected.items() if k not in ('patient_ids','unknown_patient_ids')}
                elif path=='/api/stack/execute-plan':
                    result=connected_stack.run_plan(body)
                elif path=='/api/stack/ask':
                    if set(body)!={'question','live'} or type(body['live']) is not bool:raise ValueError('Explicit question/live route required')
                    result=connected_stack.ask(body['question'],live=body['live'])
                elif path=='/api/stack/action-populations':
                    from .deliverables import answer_context
                    result=answer_context.prepare_population_scopes(connected_stack.path,body)
                elif path=='/api/stack/action-population-members':
                    from .deliverables import answer_context
                    result=answer_context.open_population_members(connected_stack.path,body)
                elif path in ('/api/stack/action-work/create', '/api/stack/action-work/update', '/api/stack/action-work/refresh', '/api/stack/action-deliverable', '/api/stack/action-report', '/api/stack/action-mockup', '/api/stack/action-simulate'):
                    from .deliverables import answer_context
                    if path.endswith('/action-mockup'):
                        from .deliverables.mock_views import create
                        result=create(connected_stack.path,body)
                    elif path.endswith('/action-report'):
                        from .deliverables.saved_report import create
                        result=create(connected_stack.path,body)
                    elif path.endswith('/action-simulate'):
                        from .deliverables.synthetic_actions import advance
                        result=advance(connected_stack.path,body)
                    elif path.endswith('/create'):
                        result=answer_context.create_work(connected_stack.path,body)
                    elif path.endswith('/update'):
                        result=answer_context.update_work(connected_stack.path,body)
                    elif path.endswith('/refresh'):
                        result=answer_context.refresh_review_sources(connected_stack.path,body)
                    else:
                        result=answer_context.compose_frontier(connected_stack.path,body,allow_live=connected_stack.allow_live)
                    if path.endswith(('/action-work/create','/action-work/update','/action-work/refresh')):
                        from .deliverables.action_center import inspection_view
                        result=inspection_view(result,compact_rows=self.headers.get('X-Agere-Response-View')=='workspace')
                elif path=='/api/stack/action-workflow/retry':
                    from .deliverables.workflow import retry_deliverable
                    result=retry_deliverable(connected_stack.path,body,allow_live=connected_stack.allow_live)
                elif path=='/api/stack/contract-input/save':
                    from .clinical.contract_performance import save_input
                    result=save_input(body)
                elif path=='/api/stack/investigation/resume':
                    if set(body)!={'context_id'}:raise ValueError('Exact saved investigation required')
                    from .clinical.proposers import continue_from_artifact
                    from .deliverables import answer_context
                    result=continue_from_artifact(connected_stack,body['context_id'])
                    saved=answer_context.capture(connected_stack.path,result['question'],result)
                    result={'action_context':saved,'status':result['answer']['status'],
                            'verification':result['answer'].get('verification'),
                            'new_paid_api_calls':result.get('new_paid_api_calls'),
                            'planning_economics':result.get('planning_economics')}
                elif path=='/api/stack/action-monitor':
                    from .deliverables.workflow import refresh
                    result=refresh(connected_stack,body)
                elif path=='/api/stack/briefing-pathway':
                    from .clinical.chief_staff import open_pathway
                    result=open_pathway(connected_stack.path,body)
                elif path=='/api/stack/compose':
                    # {question}, {question, planning} and, for follow-ups, either plus conversation_id.
                    from .clinical import conversation
                    result=conversation.handle_compose(connected_stack,body)
                elif path=='/api/stack/correct':
                    result=answer_feedback(connected_stack,body)
                elif path=='/api/stack/briefing-feedback':  # implicit feedback for the briefing ranker (briefing_rank)
                    from .clinical import briefing_rank
                    result=briefing_rank.handle_feedback(connected_stack.path,body)
                elif path=='/api/stack/context/synthesize':
                    if set(body)!={'context_id'}:raise ValueError('Exact source context required')
                    result=connected_stack.synthesize_context(body['context_id'])
                elif path=='/api/stack/decision/scoped-prepare':
                    from .clinical.scoped_review_compiler import prepare_decision
                    result=prepare_decision(connected_stack.retriever,body)
                elif path in ('/api/stack/context/output-recovery-plan','/api/stack/context/output-recover'):
                    from .clinical.source_citation_recovery import recovery_engine
                    from .clinical import reconciliation as current_reconciliation
                    if current_reconciliation.context_recovery_available(connected_stack.path,body.get('context_id')):
                        preview_recovery=current_reconciliation.preview_context_recovery
                        run_recovery=current_reconciliation.run_context_recovery
                    else:
                        engine=recovery_engine(connected_stack.path,body.get('context_id'))
                        preview_recovery,run_recovery=engine.preview,engine.run
                    if path.endswith('/output-recovery-plan'):result=preview_recovery(connected_stack.path,body)
                    else:
                        from .clinical.stack_runtime import ROOT
                        from .gateway import BudgetLedger, default_ledger_path
                        ledger=BudgetLedger(default_ledger_path())
                        try:result=run_recovery(connected_stack.path,body,ledger,allow_live=connected_stack.allow_live)
                        finally:ledger.close()
                elif path in ('/api/stack/context/recovery-plan','/api/stack/context/recover'):
                    from .clinical import interpretation_recovery
                    if path.endswith('/recovery-plan'):result=interpretation_recovery.preview(connected_stack.path,body)
                    else:
                        from .clinical.stack_runtime import ROOT
                        from .gateway import BudgetLedger, default_ledger_path
                        ledger=BudgetLedger(default_ledger_path())
                        try:result=interpretation_recovery.run(connected_stack.path,body,ledger,allow_live=connected_stack.allow_live)
                        finally:ledger.close()
                elif path in ('/api/stack/claims/plan','/api/stack/claims/reconcile'):
                    if set(body)!={'answer_id'}:raise ValueError('Exact saved source answer required')
                    from .clinical import linked_claim_reconciliation as claims
                    if path.endswith('/plan'):result=claims.preview(connected_stack.path,body['answer_id'])
                    else:
                        from .clinical.stack_runtime import ROOT
                        from .gateway import BudgetLedger, default_ledger_path
                        ledger=BudgetLedger(default_ledger_path())
                        try:result=claims.synthesize(connected_stack.path,body['answer_id'],ledger,allow_live=connected_stack.allow_live)
                        finally:ledger.close()
                elif path=='/api/stack/context/plan':
                    if set(body)!={'context_id'}:raise ValueError('Exact context identity required')
                    result=connected_stack.plan_context(body['context_id'])
                elif path in ('/api/stack/references/plan','/api/stack/references/reconcile'):
                    if set(body)!={'answer_id'}:raise ValueError('Exact saved answer required')
                    from .clinical import stable_reconciliation
                    result=(stable_reconciliation.preview(connected_stack.path,body['answer_id']) if path.endswith('/plan')
                            else connected_stack.reconcile_stable_references(body['answer_id']))
                elif path=='/api/stack/context/from-question':
                    if not {'patient','question'}<=set(body) or set(body)-{'patient','question','selection'}:raise ValueError('Explicit patient, question and optional source-selection route required')
                    result=connected_stack.prepare_question_context(body['patient'],body['question'],body.get('selection','passage_reference'))
                elif path=='/api/stack/evidence-needs':
                    from .clinical import evidence_needs
                    if set(body)!={'answer_id','selectors'}:raise ValueError('Exact answer and explicit selectors required')
                    result=evidence_needs.prepare(connected_stack.path,body['answer_id'],body['selectors'])
                elif path=='/api/stack/evidence-needs/candidates':
                    result=connected_stack.find_need_candidates(body)
                elif path=='/api/stack/evidence-needs/answer':
                    from .clinical import evidence_answers,need_references
                    result=(need_references if body.get('selection_kind')=='exact_references' else evidence_answers).attach(connected_stack.path,body)
                elif path in ('/api/stack/evidence-needs/references','/api/stack/evidence-needs/reference-context'):
                    from .clinical import need_references
                    result=(need_references.select if path.endswith('/reference-context') else need_references.prepare)(connected_stack.path,body)
                elif path in ('/api/stack/evidence-needs/comparison-preview','/api/stack/evidence-needs/compare','/api/stack/evidence-needs/comparisons'):
                    from .clinical import reference_comparison
                    action={'/api/stack/evidence-needs/comparison-preview':reference_comparison.preview,
                            '/api/stack/evidence-needs/compare':reference_comparison.prepare,
                            '/api/stack/evidence-needs/comparisons':reference_comparison.listing}[path]
                    result=action(connected_stack.path,body)
                elif path in ('/api/stack/evidence-timing','/api/stack/evidence-timing/window','/api/stack/evidence-timing/select'):
                    from .clinical import evidence_timing
                    action={'/api/stack/evidence-timing':evidence_timing.prepare,'/api/stack/evidence-timing/window':evidence_timing.window,'/api/stack/evidence-timing/select':evidence_timing.select}[path]
                    result=action(connected_stack.path,body)
                elif path=='/api/stack/evidence-needs/select-candidates':
                    from .clinical import need_candidates
                    result=need_candidates.select(connected_stack.path,body)
                elif path in ('/api/stack/evidence-needs/accounts','/api/stack/evidence-needs/account-context'):
                    from .clinical import need_accounts
                    action=need_accounts.context if path.endswith('/account-context') else need_accounts.prepare
                    result=action(connected_stack.path,body)
                elif path in ('/api/stack/decision/graph-prepare','/api/stack/decision/graph-member','/api/stack/decision/graph-refresh'):
                    from .clinical import graph_decision
                    result = (graph_decision.refresh(connected_stack,body) if path.endswith('/graph-refresh')
                              else graph_decision.prepare(connected_stack,body) if path.endswith('/graph-prepare')
                              else graph_decision.open_member(connected_stack.retriever,body))
                elif path in ('/api/stack/decision/observation-request','/api/stack/decision/observation-request-open'):
                    from .clinical import graph_observation
                    result=(graph_observation.open_request if path.endswith('-open') else graph_observation.request)(connected_stack.retriever,body)
                elif path in ('/api/stack/decision/reference-preview','/api/stack/decision/reference-prepare','/api/stack/decision/reference-assess','/api/stack/decision/reference-open','/api/stack/decision/reference-review'):
                    from .clinical import reference_assessment
                    if path.endswith('/reference-assess'):
                        from .clinical.stack_runtime import ROOT
                        from .gateway import BudgetLedger, default_ledger_path
                        ledger=BudgetLedger(default_ledger_path())
                        try: result=reference_assessment.assess(connected_stack.retriever,body,ledger,allow_live=allow_live_stack)
                        finally: ledger.db.close()
                    else:
                        action={'reference-preview':reference_assessment.preview,'reference-prepare':reference_assessment.prepare,'reference-open':reference_assessment.reopen,'reference-review':reference_assessment.review}[path.rsplit('/',1)[1]]
                        result=action(connected_stack.retriever,body)
                elif path in ('/api/stack/decision/information-propose','/api/stack/decision/information-compare','/api/stack/decision/information-request','/api/stack/decision/information-observe','/api/stack/decision/information-open'):
                    from .clinical import decision_information as information
                    route=path.rsplit('/',1)[1]
                    if route in ('information-propose','information-observe'):
                        from .clinical.stack_runtime import ROOT
                        from .gateway import BudgetLedger, default_ledger_path
                        ledger=BudgetLedger(default_ledger_path())
                        try: result=(information.propose if route=='information-propose' else information.observe)(connected_stack.retriever,body,ledger,allow_live=allow_live_stack)
                        finally: ledger.db.close()
                    else:
                        result={'information-compare':information.compare,'information-request':information.request,'information-open':information.reopen}[route](connected_stack.retriever,body)
                elif path=='/api/stack/decision/lineage':
                    result=request_lineage.for_decision(connected_stack.retriever,body)
                elif path in ('/api/stack/decision/prepare','/api/stack/decision/queue','/api/stack/decision/event','/api/stack/decision/followup','/api/stack/decision/observe','/api/stack/decision/review-preview','/api/stack/decision/review-prepare','/api/stack/decision/review-answer','/api/stack/decision/reanswer-prepare','/api/stack/decision/reanswer-refresh','/api/stack/decision/reanswer-queue','/api/stack/decision/acquisition-preview','/api/stack/decision/acquisition-create','/api/stack/decision/acquisition-open','/api/stack/decision/acquisition-check','/api/stack/decision/population-prepare','/api/stack/decision/population-match','/api/stack/decision/population-protocol-member','/api/stack/decision/population-queue','/api/stack/decision/population-route-member','/api/stack/decision/population-allocate','/api/stack/decision/population-execute','/api/stack/decision/population-accountability','/api/stack/decision/population-accountability-open','/api/stack/decision/population-prediction-options','/api/stack/decision/population-prediction','/api/stack/decision/population-prediction-open','/api/stack/decision/population-discovery-options','/api/stack/decision/population-discovery','/api/stack/decision/population-discovery-open','/api/stack/decision/economics','/api/stack/decision/economics-open'):
                    from .clinical import question_decision
                    from .clinical import population_discovery, decision_economics, decision_reassessment, decision_acquisition
                    action={'prepare':question_decision.prepare,'queue':question_decision.queue,'event':question_decision.event,'followup':question_decision.followup,'observe':question_decision.observe,'review-preview':question_decision.review_preview,'review-prepare':question_decision.prepare_review,'review-answer':question_decision.review_answer,'reanswer-prepare':decision_reassessment.prepare,'reanswer-refresh':decision_reassessment.refresh,'reanswer-queue':decision_reassessment.queue,'acquisition-preview':decision_acquisition.preview,'acquisition-create':decision_acquisition.create,'acquisition-open':decision_acquisition.reopen,'acquisition-check':decision_acquisition.check,'population-prepare':question_decision.prepare_population,'population-match':question_decision.match_population_member,'population-protocol-member':question_decision.open_population_protocol_member,'population-queue':question_decision.queue_population_member,'population-route-member':question_decision.route_population_member,'population-allocate':question_decision.allocate_population,'population-execute':question_decision.execute_population,'population-accountability':question_decision.population_accountability,'population-accountability-open':question_decision.open_population_accountability,'population-prediction-options':question_decision.prediction_contracts,'population-prediction':question_decision.population_prediction,'population-prediction-open':question_decision.open_population_prediction,'population-discovery-options':population_discovery.options,'population-discovery':population_discovery.run,'population-discovery-open':population_discovery.reopen,'economics':decision_economics.run,'economics-open':decision_economics.reopen}[path.rsplit('/',1)[1]]
                    if path.endswith('/prepare') and body.get('planner')=='buyer_documentary_candidate':
                        from .clinical.stack_runtime import ROOT
                        from .gateway import BudgetLedger, default_ledger_path
                        ledger=BudgetLedger(default_ledger_path())
                        try: result=action(connected_stack.retriever,body,ledger=ledger,allow_live=allow_live_stack)
                        finally: ledger.db.close()
                    else: result=action(connected_stack.retriever,body)
                elif path in ('/api/stack/capabilities/save','/api/stack/capabilities/prepare','/api/stack/capabilities/queue','/api/stack/capabilities/event'):
                    from .clinical import care_capabilities
                    from .clinical import participation_work,patient_context
                    action={'save':care_capabilities.save_portfolio,'prepare':patient_context.prepare_match,'queue':participation_work.enqueue,'event':patient_context.task_event}[path.rsplit('/',1)[1]]
                    result=action(connected_stack.path,body)
                elif path in ('/api/stack/medication-source-review/prepare','/api/stack/medication-source-review/list'):
                    from .clinical import medication_source_review
                    result={'prepare':medication_source_review.prepare,'list':medication_source_review.listing}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path=='/api/stack/medication-interpretation/gaps':
                    from .clinical import medication_review_gaps
                    result=medication_review_gaps.preview(connected_stack.path,body)
                elif path in ('/api/stack/medication-interpretation/select','/api/stack/medication-interpretation/answer','/api/stack/medication-interpretation/list','/api/stack/medication-interpretation/task'):
                    from .clinical import medication_interpretation
                    result={'select':medication_interpretation.select,'answer':medication_interpretation.attach,'list':medication_interpretation.listing,'task':medication_interpretation.task_state}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path in ('/api/stack/medication-interpretation/partial','/api/stack/medication-interpretation/partials'):
                    from .clinical import medication_partial_answers
                    result=(medication_partial_answers.attach if path.endswith('/partial') else medication_partial_answers.listing)(connected_stack.path,body)
                elif path in ('/api/stack/information-robustness/preview','/api/stack/information-robustness/prepare','/api/stack/information-robustness/list'):
                    from .clinical import information_robustness
                    result={'preview':information_robustness.preview,'prepare':information_robustness.prepare,'list':information_robustness.listing}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path in ('/api/stack/shared-information/choices','/api/stack/shared-information/preview','/api/stack/shared-information/prepare','/api/stack/shared-information/list'):
                    from .clinical import shared_information
                    result={'choices':shared_information.choices,'preview':shared_information.preview,'prepare':shared_information.prepare,'list':shared_information.listing}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path in ('/api/stack/work-information/preview','/api/stack/work-information/prepare','/api/stack/work-information/list'):
                    from .clinical import work_information
                    result={'preview':work_information.preview,'prepare':work_information.prepare,'list':work_information.listing}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path in ('/api/stack/care-options/portfolio-choices','/api/stack/care-options/portfolio-preview','/api/stack/care-options/portfolio-prepare','/api/stack/care-options/portfolio-list'):
                    from .clinical import care_portfolio_delivery
                    result={'portfolio-choices':care_portfolio_delivery.choices,'portfolio-preview':care_portfolio_delivery.preview,'portfolio-prepare':care_portfolio_delivery.prepare,'portfolio-list':care_portfolio_delivery.listing}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path in ('/api/stack/care-options/delivery-choices','/api/stack/care-options/delivery-preview','/api/stack/care-options/delivery-prepare','/api/stack/care-options/delivery-list'):
                    from .clinical import care_option_delivery
                    result={'delivery-choices':care_option_delivery.choices,'delivery-preview':care_option_delivery.preview,'delivery-prepare':care_option_delivery.prepare,'delivery-list':care_option_delivery.listing}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path in ('/api/stack/care-options/context-list','/api/stack/care-options/context-preview','/api/stack/care-options/context-prepare'):
                    from .clinical import care_option_context
                    result={'context-list':care_option_context.listing,'context-preview':care_option_context.preview,'context-prepare':care_option_context.prepare}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path in ('/api/stack/care-options/choices','/api/stack/care-options/preview','/api/stack/care-options/prepare','/api/stack/care-options/list'):
                    from .clinical import care_option_comparison
                    result={'choices':care_option_comparison.choices,'preview':care_option_comparison.preview,'prepare':care_option_comparison.prepare,'list':care_option_comparison.listing}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path in ('/api/stack/reference-reassessment/list','/api/stack/reference-reassessment/preview','/api/stack/reference-reassessment/prepare'):
                    from .clinical import reference_reassessment
                    result={'list':reference_reassessment.listing,'preview':reference_reassessment.preview,'prepare':reference_reassessment.prepare}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path in ('/api/stack/reference-lifecycle/inspect','/api/stack/reference-lifecycle/preview','/api/stack/reference-lifecycle/commit','/api/stack/reference-lifecycle/successor'):
                    from .clinical import reference_lifecycle
                    if path.endswith('/inspect'):
                        reference_lifecycle.R.care.exact(body,'reference_id')
                        result=reference_lifecycle.inspect(connected_stack.path,body['reference_id'])
                    else:result={'preview':reference_lifecycle.preview,'commit':reference_lifecycle.commit,'successor':reference_lifecycle.successor}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path in ('/api/stack/reference-applicability/prepare','/api/stack/reference-applicability/inspect'):
                    from .clinical import reference_applicability
                    if path.endswith('/prepare'):result=reference_applicability.materialize(connected_stack.path,body)
                    else:
                        reference_applicability.care.exact(body,'task_id')
                        result=reference_applicability.inspect(connected_stack.path,body['task_id'])
                elif path in ('/api/stack/payment-comparison/compare','/api/stack/payment-comparison/inspect','/api/stack/payment-comparison/history'):
                    from .clinical import payment_comparison
                    if path.endswith('/compare'):result=payment_comparison.compare(connected_stack.path,body)
                    elif path.endswith('/inspect'):
                        payment_comparison.P.C.R.base.care.exact(body,'id');result=payment_comparison.inspect(connected_stack.path,body['id'])
                    else:
                        payment_comparison.P.C.R.base.care.exact(body,'cost_id');result=payment_comparison.history(connected_stack.path,body['cost_id'])
                elif path in ('/api/stack/conditional-payment/compare','/api/stack/conditional-payment/inspect','/api/stack/conditional-payment/history','/api/stack/conditional-payment/scope'):
                    from .clinical import conditional_payment
                    if path.endswith('/compare'):result=conditional_payment.compare(connected_stack.path,body)
                    elif path.endswith('/inspect'):
                        conditional_payment.C.R.base.care.exact(body,'id');result=conditional_payment.inspect(connected_stack.path,body['id'])
                    else:
                        conditional_payment.C.R.base.care.exact(body,'cost_id');result=(conditional_payment.history if path.endswith('/history') else conditional_payment.describe)(connected_stack.path,body['cost_id'])
                elif path in ('/api/stack/operational-context/preview','/api/stack/operational-context/prepare','/api/stack/operational-context/history','/api/stack/operational-context/inspect'):
                    from .clinical import operational_context as oc
                    if path.endswith('/history'):
                        oc.C.R.base.care.exact(body,'parent_type parent_id');result=oc.history(connected_stack.path,body['parent_type'],body['parent_id'])
                    elif path.endswith('/inspect'):
                        oc.C.R.base.care.exact(body,'id');result=oc.inspect(connected_stack.path,body['id'])
                    else:result=(oc.preview if path.endswith('/preview') else oc.prepare)(connected_stack.path,body)
                elif path in ('/api/stack/operational-sensitivity/compare','/api/stack/operational-sensitivity/history','/api/stack/operational-sensitivity/inspect'):
                    from .clinical import operational_sensitivity as os
                    if path.endswith('/compare'):result=os.compare(connected_stack.path,body)
                    elif path.endswith('/history'):
                        os.C.R.base.care.exact(body,'context_id');result=os.history(connected_stack.path,body['context_id'])
                    else:
                        os.C.R.base.care.exact(body,'id');result=os.inspect(connected_stack.path,body['id'])
                elif path in ('/api/stack/work-cost-context/preview','/api/stack/work-cost-context/prepare','/api/stack/work-cost-context/history'):
                    from .clinical import work_cost_context
                    if path.endswith('/history'):
                        work_cost_context.C.R.base.care.exact(body,'cost_id'); result=work_cost_context.history(connected_stack.path,body['cost_id'])
                    else:result=(work_cost_context.preview if path.endswith('/preview') else work_cost_context.prepare)(connected_stack.path,body)
                elif path in ('/api/stack/work-cost/compare','/api/stack/work-cost/history','/api/stack/work-cost/inspect'):
                    from .clinical import work_cost
                    if path.endswith('/compare'): result=work_cost.compare(connected_stack.path,body)
                    elif path.endswith('/inspect'):
                        work_cost.R.base.care.exact(body,'id'); result=work_cost.inspect(connected_stack.path,body['id'])
                    else:
                        work_cost.R.base.care.exact(body,'parent_type parent_id'); result=work_cost.history(connected_stack.path,body['parent_type'],body['parent_id'])
                elif path in ('/api/stack/precedence-capacity/compare','/api/stack/precedence-capacity/history','/api/stack/precedence-capacity/inspect'):
                    from .clinical import precedence_capacity
                    if path.endswith('/compare'): result=precedence_capacity.compare(connected_stack.path,body)
                    elif path.endswith('/inspect'):
                        precedence_capacity.R.base.care.exact(body,'id'); result=precedence_capacity.inspect(connected_stack.path,body['id'])
                    else:
                        precedence_capacity.R.base.care.exact(body,'parent_id'); result=precedence_capacity.history(connected_stack.path,body['parent_id'])
                elif path in ('/api/stack/resource-capacity/compare','/api/stack/resource-capacity/history','/api/stack/resource-capacity/inspect'):
                    from .clinical import resource_capacity
                    if path.endswith('/compare'): result=resource_capacity.compare(connected_stack.path,body)
                    elif path.endswith('/inspect'):
                        resource_capacity.base.care.exact(body,'id'); result=resource_capacity.inspect(connected_stack.path,body['id'])
                    else:
                        resource_capacity.base.care.exact(body,'parent_id'); result=resource_capacity.history(connected_stack.path,body['parent_id'])
                elif path in ('/api/stack/work-capacity/preview','/api/stack/work-capacity/compare','/api/stack/work-capacity/history','/api/stack/work-capacity/inspect'):
                    from .clinical import work_capacity
                    if path.endswith('/compare'): result=work_capacity.compare(connected_stack.path,body)
                    elif path.endswith('/inspect'):
                        work_capacity.care.exact(body,'id'); result=work_capacity.inspect(connected_stack.path,body['id'])
                    else:
                        work_capacity.care.exact(body,'buyer_id')
                        result=(work_capacity.snapshot if path.endswith('/preview') else work_capacity.history)(connected_stack.path,body['buyer_id'])
                elif path=='/api/stack/reference-scope/transport':
                    from .clinical import structured_transport
                    result=structured_transport.prepare_selection(connected_stack.path,body)
                elif path in ('/api/stack/reference-scope/preview','/api/stack/reference-scope/select','/api/stack/reference-scope/answer','/api/stack/reference-scope/list'):
                    from .clinical import reference_scope_work
                    action={'preview':reference_scope_work.preview,'select':reference_scope_work.select,'answer':reference_scope_work.attach,'list':reference_scope_work.listing}[path.rsplit('/',1)[1]]
                    result=action(connected_stack.path,body)
                elif path in ('/api/stack/patient-fit/answer','/api/stack/patient-fit/answers'):
                    from .clinical import patient_fit_answers
                    action=patient_fit_answers.attach if path.endswith('/answer') else patient_fit_answers.inspect
                    result=action(connected_stack.path,body)
                elif path=='/api/stack/patient-fit/prepare':
                    from .clinical import patient_fit_work
                    result=patient_fit_work.materialize(connected_stack.path,body)
                elif path=='/api/stack/patient-context':
                    from .clinical import patient_context
                    result=patient_context.prepare(connected_stack.path,body)
                elif path=='/api/stack/participation/save':
                    from .clinical import participation_evidence
                    result=participation_evidence.save(connected_stack.path,body)
                elif path=='/api/stack/work-state/refresh':
                    from .clinical import patient_context
                    result=patient_context.refresh_match(connected_stack.path,body)
                elif path in ('/api/stack/internal-execution/run','/api/stack/internal-execution/review'):
                    from .clinical import internal_execution
                    action=internal_execution.run if path.endswith('/run') else internal_execution.review
                    result=action(connected_stack.path,body,self.headers.get('Authorization'))
                elif path in ('/api/stack/reference-packet/prepare','/api/stack/reference-packet/answer','/api/stack/reference-packet/list'):
                    from .clinical import reference_scope_packets
                    if path.endswith('/prepare'):result=reference_scope_packets.prepare(connected_stack.path,body,self.headers.get('Authorization'))
                    elif path.endswith('/answer'):result=reference_scope_packets.attach(connected_stack.path,body)
                    else:
                        if set(body)!={'task_id'}:raise ValueError('Exact task ID required')
                        result=reference_scope_packets.inspect(connected_stack.path,body['task_id'])
                elif path=='/api/stack/packet-followup/prepare':
                    from .clinical import packet_followup
                    result=packet_followup.prepare(connected_stack.path,body,self.headers.get('Authorization'))
                elif path=='/api/stack/packet-followup/answer':
                    from .clinical import followup_answers
                    result=followup_answers.attach(connected_stack.path,body)
                elif path in ('/api/stack/coverage/context','/api/stack/coverage/select'):
                    from .clinical import coverage_sources
                    if path.endswith('/context'):result=coverage_sources.prepare(connected_stack.path,body)
                    else:
                        if set(body)!={'context_id','record_id'}:raise ValueError('Exact coverage context and record required')
                        result=coverage_sources.select(coverage_sources.inspect(connected_stack.path,body['context_id']),body['record_id'])
                elif path in ('/api/stack/readiness/inspect','/api/stack/readiness/source','/api/stack/readiness/review','/api/stack/readiness/context'):
                    from .clinical import evidence_dispositions as dispositions
                    if path.endswith('/inspect'):
                        if set(body)!={'target'}:raise ValueError('Exact documentary target required')
                        result=dispositions.inspect(connected_stack.path,body['target'])
                    else:
                        action={'source':dispositions.source_preview,'review':dispositions.save,'context':dispositions.acquisition_context}[path.rsplit('/',1)[1]]
                        result=action(connected_stack.path,body)
                elif path=='/api/stack/evidence-needs/context':
                    from .clinical import evidence_needs
                    if set(body)!={'receipt_id','need_id'}:raise ValueError('Exact evidence receipt and need required')
                    result=evidence_needs.context_for_need(connected_stack.path,body['receipt_id'],body['need_id'])
                elif path=='/api/stack/context/prepare':
                    from .clinical import context_builder
                    if set(body)!={'patient','parameters'} or not isinstance(body['patient'],str) or not 0<len(body['patient'])<=150:raise ValueError('Explicit patient and bounded context parameters required')
                    result=context_builder.execute(connected_stack.path,[body['patient']],body['parameters'])
                elif path=='/api/stack/evidence-portfolio/compare':
                    from .clinical import evidence_portfolio
                    if set(body)!={'proposal_id','review_slots'}:raise ValueError('Exact acquisition proposal and hypothetical review slots required')
                    result=evidence_portfolio.save(connected_stack.path,body['proposal_id'],body['review_slots'])
                elif path=='/api/stack/evidence-acquisition/prepare':
                    if not set(body)<={'contracts','cohort','patient'} or 'contracts' not in body:raise ValueError('Explicit contracts and population scope required')
                    from .clinical import evidence_acquisition,cohorts
                    selected=cohorts.select(connected_stack.path,body.get('cohort'),patient=body.get('patient') or None)
                    result=evidence_acquisition.prepare(connected_stack.path,body['contracts'],selected['patient_ids'],
                        selection={k:v for k,v in selected.items() if k not in ('patient_ids','unknown_patient_ids','cache_hit')})
                elif path=='/api/stack/operator':
                    if not set(body)<={'operator','patient','drug_class','view','cohort','analysis_window'}:raise ValueError('Unknown operator fields')
                    from .clinical.analytic_registry import execute
                    result=execute(connected_stack.path,body.get('operator'),patient=body.get('patient') or None,drug_class=body.get('drug_class'),view=body.get('view','all'),
                                   cohort=body.get('cohort'),analysis_window=body.get('analysis_window'))
                elif path=='/api/stack/program-import':
                    if 'input_bindings' in body:
                        from .clinical.program_documents import install
                        result=install(connected_stack.path,body)
                    else:
                        from .clinical.program_registry import install_candidate
                        result=install_candidate(connected_stack.path,body)
                elif path=='/api/stack/program-bind':
                    from .clinical.program_documents import bind_existing
                    if set(body)!={'policy_hash','input_bindings','provenance','role'}:raise ValueError('Exact existing policy binding request required')
                    result=bind_existing(connected_stack.path,**body)
                elif path=='/api/stack/program-document-review':
                    from .clinical.program_documents import review
                    result=review(connected_stack.path,body)
                elif path=='/api/stack/semantic/read':
                    if set(body)!={'target','provider'}:raise ValueError('Explicit semantic target/provider required')
                    result=connected_stack.refine_statement(body['target'],body['provider'])
                elif path in ('/api/stack/decision/statement-review','/api/stack/decision/statement-review-open'):
                    from .clinical import reader_contract_routing
                    action=reader_contract_routing.reopen if path.endswith('-open') else reader_contract_routing.run
                    result=action(connected_stack.retriever,body)
                elif path in ('/api/stack/decision/statement-review-status',
                              '/api/stack/decision/statement-review-attach',
                              '/api/stack/decision/statement-review-outcome-open'):
                    from .clinical import reader_contract_routing as routing
                    action={'statement-review-status':routing.review_status,
                            'statement-review-attach':routing.attach_review,
                            'statement-review-outcome-open':routing.review_open}[path.rsplit('/',1)[1]]
                    result=action(connected_stack.retriever,body)
                elif path=='/api/stack/reader-comparison/dates':
                    from .clinical import reader_target_dates
                    result=reader_target_dates.inspect(connected_stack.path,body)
                elif path=='/api/stack/reader-comparison/coverage':
                    from .clinical import reader_role_coverage
                    result=reader_role_coverage.inspect(connected_stack.path,body)
                elif path in ('/api/stack/reader-comparison/availability','/api/stack/reader-comparison/prepare','/api/stack/reader-comparison/inspect'):
                    from .clinical import reader_comparison
                    result=getattr(reader_comparison,path.rsplit('/',1)[1])(connected_stack.path,body)
                elif path=='/api/stack/semantic/review':
                    from .clinical.semantic_reader import save_review
                    result=save_review(connected_stack.path,body)
                elif path=='/api/stack/typed-learning/review':
                    from .clinical.typed_distillation import save_review
                    result=save_review(connected_stack.path,body)
                elif path=='/api/stack/learning-queue/target':
                    from .clinical.learning_queue import mark_target
                    result=mark_target(connected_stack.path,body)
                elif path in ('/api/stack/source-span-proposals','/api/stack/source-span-proposals/select',
                              '/api/stack/question-span-availability','/api/stack/question-span-availability/select'):
                    from .clinical import source_span_proposals as proposals
                    if path.startswith('/api/stack/question-span-availability'):
                        from .clinical import question_span_availability as proposals
                    if path.endswith('/select'):
                        try:result=proposals.select(connected_stack.path,body)
                        except FileNotFoundError as exc:raise ValueError('Unknown saved source-proposal receipt') from exc
                    else:
                        from .gateway import BudgetLedger, default_ledger_path
                        from .clinical.stack_runtime import ROOT
                        ledger=BudgetLedger(default_ledger_path())
                        try:result=proposals.propose(connected_stack.path,body,ledger,allow_live=connected_stack.allow_live)
                        finally:ledger.close()
                elif path in ('/api/stack/cohort-spans','/api/stack/cohort-spans/select'):
                    from .clinical import cohort_span_acquisition as spans
                    if path.endswith('/select'):
                        try:result=spans.select(connected_stack.path,body)
                        except FileNotFoundError as exc:raise ValueError('Unknown saved cohort proposal') from exc
                    else:
                        if set(body)!={'cohort_id','item_id'}:raise ValueError('Exact cohort and sampled field required')
                        from .gateway import BudgetLedger, default_ledger_path
                        from .clinical.stack_runtime import ROOT
                        ledger=BudgetLedger(default_ledger_path())
                        try:result=spans.Session(connected_stack.path,body['cohort_id']).propose(body['item_id'],ledger,allow_live=connected_stack.allow_live)
                        finally:ledger.close()
                elif path=='/api/stack/question-learning':
                    from .clinical.question_learning import prepare
                    result=prepare(connected_stack.path,body)
                elif path=='/api/stack/learning-cohorts/read':
                    from .clinical.learning_cohorts import read
                    if body.get('kind')=='windowed_minilm':
                        from .clinical.windowed_student import read
                    try:result=read(connected_stack.path,body)
                    except FileNotFoundError as exc:raise ValueError('The requested cohort has no complete fitted snapshot available') from exc
                elif path=='/api/stack/reconcile-passes':
                    if set(body)!={'bundle_id'}:raise ValueError('Explicit pass bundle required')
                    from .clinical.assertion_reconciliation import reconcile_bundle
                    result=reconcile_bundle(connected_stack.path,body['bundle_id'])
                elif path=='/api/stack/pass-review':
                    if set(body)!={'bundle_id','provider','maximum_passes'}:raise ValueError('Explicit bounded pass review required')
                    result=connected_stack.review_passes(body['bundle_id'],body['provider'],body['maximum_passes'])
                elif path=='/api/stack/review':
                    if set(body)!={'question','patient','provider'}:raise ValueError('Explicit patient model-review contract required')
                    result=connected_stack.review(body['question'],body['patient'],body['provider'])
                elif path in ('/api/stack/cardiometabolic/prepare','/api/stack/cardiometabolic/work','/api/stack/cardiometabolic/context'):
                    from .clinical import cardiometabolic_review as cm
                    result={'prepare':cm.prepare,'work':cm.prepare_work,'context':cm.context}[path.rsplit('/',1)[1]](connected_stack.path,body)
                elif path=='/api/stack/domain-review/context':
                    from .clinical.domain_review import context
                    result=context(connected_stack.path,body)
                elif path=='/api/stack/domain-review/work':
                    from .clinical.domain_programs import prepare_work
                    result=prepare_work(connected_stack.path,body)
                elif path=='/api/stack/functional-review/context':
                    from .clinical.functional_review import context
                    result=context(connected_stack.path,body)
                elif path=='/api/stack/functional-review/work':
                    from .clinical.functional_programs import prepare_work
                    result=prepare_work(connected_stack.path,body)
                elif path=='/api/stack/work/prepare':
                    from .clinical.program_work import prepare,definition
                    from .clinical.cohorts import select
                    if not set(body)<={'program','cohort','patient','accountability','synthesis_ids'}:raise ValueError('Unknown worklist fields')
                    definition(body.get('program'))
                    selected=select(connected_stack.path,body.get('cohort'),patient=body.get('patient') or None)
                    membership={k:v for k,v in selected.items() if k not in ('patient_ids','unknown_patient_ids')}
                    accountability=None;contexts=None
                    if 'accountability' in body:
                        from .clinical import program_accountability as accountability_engine
                        accountability=accountability_engine.execute(connected_stack.path,selected['patient_ids'],
                            {'contract':body['accountability'],'available_by':connected_stack.retriever.meta['snapshot']['as_of']},selection=membership)
                        contexts=accountability_engine.contexts(connected_stack.path,accountability['id'],selected['patient_ids'])
                    synthesis_contexts=None
                    if 'synthesis_ids' in body:
                        from .clinical.synthesis_work import contexts as synthesis_links
                        synthesis_contexts=synthesis_links(connected_stack.path,body['synthesis_ids'],selected['patient_ids'])
                    result=prepare(connected_stack.path,body.get('program'),selected['patient_ids'],input_membership=membership,accountability_contexts=contexts,synthesis_contexts=synthesis_contexts)
                    if accountability:result['accountability_review']=accountability
                elif path=='/api/stack/work/save':
                    from .clinical.program_work import materialize
                    if set(body)!={'proposal_id','actor'}:raise ValueError('Explicit proposal and actor required')
                    result=materialize(connected_stack.path,body['proposal_id'],body['actor'])
                elif path in ('/api/stack/work/answers/prepare','/api/stack/work/answers/apply'):
                    from .clinical import answer_retirement
                    result=(answer_retirement.prepare if path.endswith('/prepare') else answer_retirement.apply)(connected_stack.path,body)
                elif path=='/api/stack/work/event':
                    from .clinical.program_work import change
                    result=change(connected_stack.path,body)
                else:self.send_error(404);return
                if execution:
                    execution.finish(result)
                    result={**result,'request_execution_id':execution.id}
                encoding=None
                if path=='/api/stack/compose' and self.headers.get('X-Agere-Response-View')=='answer':
                    from .deliverables.action_center import compose_view,encode_inspection
                    result=compose_view(connected_stack.path,result)
                    raw,encoding=encode_inspection(result,self.headers.get('Accept-Encoding',''))
                else:
                    raw=json.dumps(result,allow_nan=False).encode()
                status=200
            except PermissionError as exc:
                raw=json.dumps({'error':str(exc)[:400]}).encode();status=403
            except (ValueError,TypeError,KeyError) as exc:
                from .clinical.program_work import WorkConflict
                from .clinical.question_input import failure
                details = failure(exc) if path=='/api/stack/compose' else {'error':str(exc)[:400]}
                if path in ('/api/stack/compose','/api/stack/investigation/resume'): details = {**details, **_failure_receipts(exc)}
                raw=json.dumps(details,default=str).encode();status=409 if isinstance(exc,WorkConflict) else 400
            except RuntimeError as exc:
                raw=json.dumps({'error':str(exc)[:400],**(_failure_receipts(exc) if path in ('/api/stack/compose','/api/stack/investigation/resume') else {})},default=str).encode();status=502
            finally:
                try:
                    if execution:
                        if 'status' not in locals() or status!=200:execution.finish()
                finally:
                    if execution:execution.close()
                    held.close()
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            if path=='/api/stack/compose':
                self.send_header('Vary','X-Agere-Response-View, Accept-Encoding')
            if status==200 and encoding:self.send_header('Content-Encoding',encoding)
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            parsed=urlsplit(self.path);params=parse_qs(parsed.query)
            # Block browser-origin requests from unrelated sites, including DNS rebinding hosts.
            host=self.headers.get('Host','').split(':')[0]
            if host not in {'127.0.0.1','localhost'}:self.send_error(403);return
            origin=self.headers.get('Origin')
            if origin and origin not in {f'http://127.0.0.1:{port}',f'http://localhost:{port}'}:self.send_error(403);return
            if recovery and parsed.path not in recovery_gets:
                self.send_error(403,'This route is unavailable in installation mode '+recovery.get('mode','inspection_only'));return
            if (parsed.path=='/workspaces' or parsed.path.startswith('/api/workspaces')) and not recovery:
                self._workspace('GET',parsed.path);return
            if connected_stack is not None and not recovery and (parsed.path in ('/', '/site') or parsed.path.startswith('/site/')):
                from .site_view import asset
                try: raw,mime=asset(parsed.path)
                except FileNotFoundError: self.send_error(404); return
            elif parsed.path=='/api/stack/meta':
                if connected_stack is None:self.send_error(404);return
                raw=json.dumps({**connected_stack.metadata(),'recovery':recovery,'runtime_source_validation':validation_cache.status(),'runtime_vocabulary_index':validation_index,'request_locks':request_locks.status() if request_locks else None,'domain_pack':_pack_status()}).encode();mime='application/json'
            elif parsed.path in ('/stack-evaluation','/api/stack/workflow-evaluation'):
                from .clinical.stack_runtime import ROOT
                from .workflow_evaluation_view import page as evaluation_page
                from .workflow_scorecard import source_selection_decision, release_acceptance, saved_report
                try:report=saved_report(ROOT,connected_stack.retriever.meta['snapshot'] if connected_stack is not None else None)
                except FileNotFoundError:self.send_error(404);return
                except (ValueError,KeyError,TypeError):self.send_error(409,'Saved workflow report evidence needs revalidation');return
                report['source_selection_decision']=source_selection_decision(ROOT)
                evidence_config=ROOT/'config/agere/capability_acceptance.json'
                if evidence_config.exists() and connected_stack is not None:
                    # Separate engineering evidence never enters runtime clinical
                    # sources. The existing verifier validates snapshot and hashes.
                    report['release_acceptance']=release_acceptance(report['cases'],
                        capability_evidence=json.loads(evidence_config.read_text()),
                        snapshot=connected_stack.retriever.meta['snapshot'])
                raw=(json.dumps(report).encode() if parsed.path.startswith('/api/') else evaluation_page(report).encode())
                mime='application/json' if parsed.path.startswith('/api/') else 'text/html; charset=utf-8'
            elif parsed.path=='/api/stack/economics':
                if connected_stack is None:self.send_error(404);return
                from .clinical.answer_log import summary as economics_summary
                raw=json.dumps(economics_summary(connected_stack.path)).encode();mime='application/json'
            elif parsed.path=='/api/stack/analysis-progress':
                if connected_stack is None:self.send_error(404);return
                from . import request_lineage
                params=parse_qs(parsed.query)
                try:result=request_lineage.progress(connected_stack.path,params.get('key',[''])[0],after=int(params.get('after',['-1'])[0]))
                except ValueError as exc:self.send_error(400,str(exc));return
                raw=json.dumps(result).encode();mime='application/json'
            elif parsed.path=='/api/stack/intelligence-activity':
                if connected_stack is None:self.send_error(404);return
                from .clinical.intelligence_activity import snapshot as intelligence_snapshot
                raw=json.dumps(intelligence_snapshot(connected_stack.path)).encode();mime='application/json'
            elif parsed.path=='/api/stack/healthcare-learning':
                if connected_stack is None: self.send_error(404); return
                from .clinical import healthcare_learning, economics_learning
                result=healthcare_learning.summary(connected_stack.path)
                question=parse_qs(parsed.query).get('question',[''])[0]
                result['rule_options']=healthcare_learning.rule_options(question) + economics_learning.rule_options(question)
                result['source_scope']=healthcare_learning.knowledge_context(question)
                raw=json.dumps(result).encode();mime='application/json'
            elif parsed.path=='/api/stack/economics-learning':
                if connected_stack is None: self.send_error(404); return
                from .clinical import economics_learning
                result=economics_learning.summary(connected_stack.path)
                result['rule_options']=economics_learning.rule_options(parse_qs(parsed.query).get('question',[''])[0])
                raw=json.dumps(result).encode();mime='application/json'
            elif parsed.path=='/api/stack/learning':
                if connected_stack is None:self.send_error(404);return
                from .clinical.learning import summary as learning_summary
                raw=json.dumps(learning_summary(connected_stack.path)).encode();mime='application/json'
            elif parsed.path=='/api/stack/learning-loop':  # read-only summary of the last tools/learning_loop run
                if connected_stack is None:self.send_error(404);return
                from .clinical.learning import loop_summary
                raw=json.dumps(loop_summary(connected_stack.path),default=str).encode();mime='application/json'
            elif parsed.path=='/api/stack/briefing':
                if connected_stack is None:self.send_error(404);return
                from .clinical.chief_staff import cached as briefing
                raw=json.dumps(briefing(connected_stack.path),default=str).encode();mime='application/json'
            elif parsed.path=='/api/stack/conversation':
                if connected_stack is None:self.send_error(404);return
                from .clinical import conversation
                try:
                    cid=conversation.check_id(params.get('id',[''])[0] or '')
                    raw=json.dumps({'id':cid,'turns':conversation.turns(connected_stack.path,cid)},default=str).encode();mime='application/json'
                except ValueError as exc:self.send_error(400,str(exc));return
            elif parsed.path=='/action-center':
                if connected_stack is None:self.send_error(404);return
                from .deliverables.action_center import page
                raw=page().encode();mime='text/html; charset=utf-8'
            elif parsed.path in ('/api/stack/action-contexts','/api/stack/action-context','/api/stack/action-deliverables','/api/stack/action-deliverable'):
                if connected_stack is None:self.send_error(404);return
                from .deliverables import answer_context
                try:
                    key=params.get('context',[''])[0]
                    if parsed.path.endswith('/action-contexts'):
                        result={'contexts':answer_context.listing(connected_stack.path)}
                    elif parsed.path.endswith('/action-context'):
                        result={**answer_context.inspect(connected_stack.path,key),'allow_frontier':bool(connected_stack.allow_live and not recovery)}
                        from .deliverables.compose import context_review
                        result['independent_review']=context_review(connected_stack.path,result['context'])
                        from .deliverables.action_center import inspection_view,evidence_view
                        view=params.get('view',[''])[0]
                        if view not in ('','native','evidence'):raise ValueError('Unknown saved evidence view')
                        if view=='evidence':
                            result=evidence_view(result,binding=params.get('binding',[''])[0],
                                patient_id=params.get('patient',[None])[0])
                        elif view!='native':
                            result=inspection_view(result,compact_rows=self.headers.get('X-Agere-Response-View')=='workspace')
                    else:
                        if parsed.path.endswith('/action-deliverable') and params.get('format'):
                            # read_format loads the context and validates its source,
                            # evidence bundle and recorded bytes. Do not load the same
                            # potentially large context again before that guarded read.
                            from .deliverables.saved_report import read_format
                            raw,mime,filename=read_format(connected_stack.path,key,params.get('id',[''])[0],params['format'][0])
                            self.send_response(200);self.send_header('Content-Type',mime)
                            self.send_header('Content-Length',str(len(raw)))
                            self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
                            self.end_headers();self.wfile.write(raw);return
                        from .deliverables.compose import load,listing
                        context=answer_context.load(connected_stack.path,key)
                        ws=answer_context.workspace(connected_stack.path,context)
                        if parsed.path.endswith('/action-deliverables'):
                            result={'deliverables':listing(ws)}
                        else:
                            spec,_,_,doc=load(ws,params.get('id',[''])[0])
                            result={**spec,'html':doc if spec.get('status')=='delivered' else None}
                    mime='application/json'
                    if parsed.path.endswith('/action-context'):
                        from .deliverables.action_center import encode_inspection
                        raw,encoding=encode_inspection(result,self.headers.get('Accept-Encoding',''))
                        self.send_response(200);self.send_header('Content-Type',mime)
                        self.send_header('Content-Length',str(len(raw)))
                        self.send_header('Vary','X-Agere-Response-View, Accept-Encoding')
                        self.send_header('Cache-Control','no-store')
                        self.send_header('X-Content-Type-Options','nosniff')
                        if encoding:self.send_header('Content-Encoding',encoding)
                        self.end_headers();self.wfile.write(raw);return
                    raw=json.dumps(result,default=str).encode()
                except (ValueError,FileNotFoundError) as exc:
                    self.send_response(400);self.send_header('Content-Type','application/json');self.end_headers()
                    self.wfile.write(json.dumps({'error':str(exc)}).encode());return
            elif parsed.path=='/ask':
                if connected_stack is None:self.send_error(404);return
                from .ask_view import page as ask_page
                raw=ask_page().encode();mime='text/html; charset=utf-8'
            elif parsed.path in ('/api/scorecard','/api/stack/quality'):  # Six Sigma defect rates (answer log) + public error-rate scorecard, JSON
                from .clinical.quality_scorecard import scorecard as quality_scorecard
                raw=json.dumps(quality_scorecard(connected_stack.path if connected_stack is not None else None),default=str).encode();mime='application/json'
            elif parsed.path=='/scorecard':  # error-rate scorecard from bench/results (read-only) + learning summary
                from .scorecard_view import page as scorecard_page
                from .clinical.learning import summary as learning_summary
                raw=scorecard_page(learning=learning_summary(connected_stack.path) if connected_stack is not None else None).encode();mime='text/html; charset=utf-8'
            elif parsed.path=='/stack':
                if connected_stack is None:self.send_error(404);return
                from .clinical.stack_workbench import page
                raw=page(connected_stack.retriever.meta['snapshot']['as_of']).encode();mime='text/html; charset=utf-8'
                if recovery:
                    notice = ('Restored local installation: saved internal work can be held, cancelled or prepared for review with version checks. Model execution and qualification transfer are disabled. The original build is unchanged.' if work_resume else 'Recovery inspection: saved questions and work are available. Work changes and model execution are disabled. This copy has not replaced the active build.')
                    if local_operations:notice='Local installation: source search, scoped questions, buyer/payer work and synthetic intake are available. Hosted inference and relocated learning qualifications are unavailable. The original build is unchanged.'
                    raw=raw.replace(b'<main class="workspace-main">',b'<main class="workspace-main"><aside role="status" style="padding:16px;background:#fff4ce;color:#302500;font-weight:bold">'+notice.encode()+b'</aside>',1)
            elif parsed.path in ('/api/stack/source-atlas','/api/stack/source-records','/api/stack/evidence-thread'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import evidence_threads
                try:
                    if parsed.path.endswith('source-atlas'):
                        result=evidence_threads.atlas(connected_stack.path)
                    elif parsed.path.endswith('source-records'):
                        result=evidence_threads.records(connected_stack.path,params.get('stream',[''])[0],
                            params.get('patient',[''])[0],int(params.get('offset',['0'])[0]))
                    else:
                        result=evidence_threads.inspect(connected_stack.path,params.get('patient',[''])[0],params.get('record',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/semantics':
                if connected_stack is None:self.send_error(404);return
                from .clinical.semantic_workbench import page
                raw=page().encode();mime='text/html; charset=utf-8'
            elif parsed.path=='/learn':
                if connected_stack is None:self.send_error(404);return
                from .clinical.learning_workbench import page
                raw=page().encode();mime='text/html; charset=utf-8'
            elif parsed.path in ('/api/stack/typed-learning','/api/stack/typed-learning/item'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import typed_distillation
                try:
                    result=(typed_distillation.inspect_review(connected_stack.path,params.get('id',[''])[0])
                            if parsed.path.endswith('/item') else typed_distillation.review_listing(connected_stack.path,offset=int(params.get('offset',['0'])[0])))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/programs':
                if connected_stack is None:self.send_error(404);return
                from .clinical.program_workbench import page
                raw=page().encode();mime='text/html; charset=utf-8'
            elif parsed.path=='/api/stack/work':
                if connected_stack is None:self.send_error(404);return
                from .clinical.program_work import list_items,inspect_item
                try:
                    if 'item' in params:result=inspect_item(connected_stack.path,params['item'][0])
                    else:result=list_items(connected_stack.path,program=params.get('program',[None])[0],state=params.get('state',[None])[0],offset=int(params.get('offset',['0'])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/semantic','/api/stack/semantic/prerequisites','/api/stack/semantic/learning','/api/stack/semantic/list'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import semantic_reader
                try:
                    if parsed.path.endswith('/list'):result=semantic_reader.list_readings(connected_stack.path,params.get('patient',[''])[0],int(params.get('offset',['0'])[0]))
                    elif parsed.path.endswith('/prerequisites'):result=semantic_reader.prerequisites(connected_stack.path,params.get('patient',[''])[0])
                    elif parsed.path.endswith('/learning'):result=semantic_reader.learning_export(connected_stack.path)
                    else:
                        result=semantic_reader.inspect(connected_stack.path,params.get('id',[''])[0])
                        from .clinical.source_structure import locate
                        _,source_text,_=semantic_reader.source(connected_stack.path,result['target'])
                        result['source_structure']=locate(source_text,result['target']['start'],result['target']['end'])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/learning-queue','/api/stack/learning-queue/item'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import learning_queue
                try:
                    if parsed.path.endswith('/item'):
                        from .clinical.learning_suggestions import attempts
                        result=learning_queue.inspect(connected_stack.path,params.get('id',[''])[0])
                        result['suggestion_attempts']=attempts(connected_stack.path,params.get('id',[''])[0])
                        from .clinical.source_structure import locate
                        result['source_roles']=[{'target':t,'structure':locate(result['text'],t['start'],t['end'])} for t in result['target_options']]
                    else:result=learning_queue.list_items(connected_stack.path,lane=params.get('lane',[''])[0],stream=params.get('stream',[''])[0],offset=int(params.get('offset',['0'])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/source-span-proposals','/api/stack/source-span-proposals/preview',
                                 '/api/stack/question-span-availability','/api/stack/question-span-availability/preview'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import source_span_proposals as proposals
                if parsed.path.startswith('/api/stack/question-span-availability'):
                    from .clinical import question_span_availability as proposals
                try:
                    if parsed.path.endswith('/preview'):
                        result=proposals.preview(connected_stack.path,{'candidate_id':params.get('candidate_id',[''])[0],
                            'passage_rank':int(params.get('passage_rank',['0'])[0])})
                    else:result=proposals.inspect(connected_stack.path,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/question-learning','/api/stack/question-learning/preview','/api/stack/current-learning'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import question_learning
                try:
                    if parsed.path.endswith('/preview'):
                        result=question_learning.preview(connected_stack.path,params.get('candidate_id',[''])[0])
                    elif parsed.path.endswith('/current-learning'):result=question_learning.readiness(connected_stack.path)
                    elif params.get('id'):
                        result=question_learning.inspect(connected_stack.path,params['id'][0])
                        from .clinical.source_span_proposals import origins
                        result['machine_acquisition']=origins(connected_stack.path,params['id'][0])
                        from .clinical.question_span_availability import origins as available_origins
                        result['machine_acquisition']+=available_origins(connected_stack.path,params['id'][0])
                    else:result=question_learning.listing(connected_stack.path)
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/cohort-spans','/api/stack/cohort-spans/item','/api/stack/cohort-spans/task'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import cohort_span_acquisition as spans
                try:
                    cohort_id=params.get('cohort_id',[''])[0]
                    if parsed.path.endswith('/task'):
                        result=spans.task(connected_stack.path,cohort_id,params.get('id',[''])[0])
                    elif parsed.path.endswith('/item'):
                        session=spans.Session(connected_stack.path,cohort_id);item_id=params.get('item_id',[''])[0]
                        prepared=session.request(item_id)
                        result={'cohort_id':cohort_id,'item_id':item_id,'text':prepared['wire']['complete_received_field'],
                            'source':prepared['wire']['source'],'field':prepared['wire']['field'],'partition':prepared['partition'],
                            'source_availability':prepared['wire']['source_availability'],'request_bytes':prepared['request_bytes'],
                            'saved':session.inspect(item_id) if (session.root/'receipts'/(item_id+'.json')).exists() else None,
                            'attempted':any((session.root/kind/(item_id+'.json')).exists() for kind in ('attempts','preflight-failures')),
                            'limits':spans.LIMITS}
                    else:result=spans.status(connected_stack.path,cohort_id,offset=int(params.get('offset',['0'])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/learning-cohorts/diagnostics':
                if connected_stack is None:self.send_error(404);return
                from .clinical.cohort_diagnostics import inspect
                params=parse_qs(parsed.query)
                try:result=inspect(connected_stack.path,params.get('id',[''])[0],params.get('kind',[''])[0],
                    outcome=params.get('outcome',['disagreement'])[0],offset=int(params.get('offset',['0'])[0]))
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
                raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
            elif parsed.path=='/api/stack/learning-cohorts':
                if connected_stack is None:self.send_error(404);return
                if local_operations:
                    from .runnable_release import qualification_status
                    try:result=qualification_status(recovery.get('frozen_cohorts',[]),params.get('id',[None])[0])
                    except ValueError as exc:self.send_error(400,str(exc));return
                    raw=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
                    self.end_headers();self.wfile.write(raw);return
                from .clinical import learning_cohorts
                try:
                    result=learning_cohorts.status(connected_stack.path,params['id'][0]) if params.get('id') else learning_cohorts.listing(connected_stack.path)
                    from .clinical import windowed_student
                    from .clinical.cohort_diagnostics import coverage
                    for cohort in result.get('cohorts',[result]):
                        if cohort.get('status')!='current':continue
                        cohort['sampling_coverage']=coverage(connected_stack.path,cohort['id'])
                        try:cohort['windowed_reader']=windowed_student.status(connected_stack.path,cohort['id'])
                        except (ValueError,FileNotFoundError,KeyError) as exc:cohort['windowed_reader']={'status':'stale','error':str(exc)[:200]}
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/learning-qualification':
                if connected_stack is None:self.send_error(404);return
                from .clinical.distillation_qualification import inspect
                try:
                    raw=json.dumps(inspect(connected_stack.path),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/source-distillation':
                if connected_stack is None:self.send_error(404);return
                from .clinical.source_distillation import status
                try:
                    raw=json.dumps(status(connected_stack.path),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/semantic-students':
                if connected_stack is None:self.send_error(404);return
                from .clinical.semantic_student import status
                try:
                    raw=json.dumps(status(connected_stack.path),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/learning-readiness':
                if connected_stack is None:self.send_error(404);return
                from .clinical.semantic_learning import audit
                try:
                    raw=json.dumps(audit(connected_stack.path),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/outcome-learning','/api/stack/prediction-qualification'):
                if connected_stack is None:self.send_error(404);return
                from .clinical.outcome_learning import attachment_status
                try:
                    from .clinical import prediction_registry
                    result=prediction_registry.status(connected_stack.path) if (connected_stack.path/prediction_registry.LINK).exists() or parsed.path.endswith('prediction-qualification') else attachment_status(connected_stack.path)
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/accountability':
                if connected_stack is None:self.send_error(404);return
                from .clinical import program_accountability
                try:
                    result=program_accountability.inspect(connected_stack.path,params.get('id',[''])[0],patient=params.get('patient',[None])[0],offset=int(params.get('offset',['0'])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/financial-accounts':
                if connected_stack is None:self.send_error(404);return
                from .clinical import financial_accounts
                try:
                    raw=json.dumps(financial_accounts.inspect(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/decision-landmarks','/api/stack/landmark-contracts'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import decision_landmarks
                try:
                    result=({'synthetic':True,'contracts':list(decision_landmarks.definitions().values())}
                            if parsed.path.endswith('landmark-contracts') else
                            decision_landmarks.inspect(connected_stack.path,params.get('id',[''])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/baseline','/api/stack/baseline-profiles'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import baseline_evidence
                try:
                    result=({'synthetic':True,'profiles':list(baseline_evidence.profiles().values())}
                            if parsed.path.endswith('baseline-profiles') else
                            baseline_evidence.inspect(connected_stack.path,params.get('id',[''])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/measures':
                if connected_stack is None:self.send_error(404);return
                from .clinical.measure_workbench import page
                raw=page().encode();mime='text/html; charset=utf-8'
            elif parsed.path=='/api/stack/measure':
                if connected_stack is None:self.send_error(404);return
                from .clinical.measure_composition import inspect
                try:
                    result=inspect(connected_stack.path,params.get('id',[''])[0],params.get('patient',[None])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/program-acquisition':
                if connected_stack is None:self.send_error(404);return
                from .clinical import program_acquisition
                try:
                    key=params.get('id',[''])[0]
                    result=(program_acquisition.inspect_patient(connected_stack.path,key,params['patient'][0]) if 'patient' in params else
                            program_acquisition.list_items(connected_stack.path,key,int(params.get('offset',['0'])[0])))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/program-documents':
                if connected_stack is None:self.send_error(404);return
                from .clinical import program_documents
                try:
                    if 'package' in params:result=program_documents.inspect_package(connected_stack.path,params['package'][0])
                    elif 'receipt' in params:result=program_documents.inspect(connected_stack.path,params['receipt'][0])
                    else:result={'synthetic':True,'packages':program_documents.packages(connected_stack.path),'limits':program_documents.LIMITS}
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/program-documents':
                if connected_stack is None:self.send_error(404);return
                from .clinical.program_document_workbench import page
                raw=page().encode();mime='text/html; charset=utf-8'
            elif parsed.path=='/api/stack/program-document-change':
                if connected_stack is None:self.send_error(404);return
                from .clinical.program_document_change import inspect
                try:
                    raw=json.dumps(inspect(connected_stack.path,params.get('id',[''])[0],params.get('patient',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/protocol-change':
                if connected_stack is None:self.send_error(404);return
                from .clinical import protocol_change
                try:
                    result=protocol_change.inspect(connected_stack.path,params.get('id',[''])[0],params.get('patient',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/protocol-review','/api/stack/care-protocols'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import care_protocols
                try:
                    result=({'synthetic':True,'protocols':list(care_protocols.definitions().values()),'archived_protocols':list(care_protocols.read_catalog(care_protocols.HISTORY).values())}
                            if parsed.path.endswith('care-protocols') else
                            care_protocols.inspect(connected_stack.path,params.get('id',[''])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/outcomes','/api/stack/outcome-contracts'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import outcomes
                try:
                    result=({'synthetic':True,'contracts':list(outcomes.definitions().values())}
                            if parsed.path.endswith('outcome-contracts') else
                            outcomes.inspect(connected_stack.path,params.get('id',[''])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/requirements','/api/stack/requirement-contracts'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import evidence_requirements
                try:
                    result=({'synthetic':True,'contracts':list(evidence_requirements.definitions().values())}
                            if parsed.path.endswith('requirement-contracts') else
                            evidence_requirements.inspect(connected_stack.path,params.get('id',[''])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/evidence-portfolio':
                if connected_stack is None:self.send_error(404);return
                from .clinical import evidence_portfolio
                try:
                    result=evidence_portfolio.inspect(connected_stack.path,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/evidence-resolution':
                if connected_stack is None:self.send_error(404);return
                from .clinical import evidence_resolution
                try:
                    result=evidence_resolution.inspect(connected_stack.path,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/evidence-acquisition','/api/stack/evidence-acquisition/item'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import evidence_acquisition
                try:
                    key=params.get('id',[''])[0]
                    result=(evidence_acquisition.inspect_item(connected_stack.path,key,params.get('item',[''])[0])
                        if parsed.path.endswith('/item') else evidence_acquisition.list_items(connected_stack.path,key,offset=int(params.get('offset',['0'])[0])))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/pre-event-state':
                if connected_stack is None:self.send_error(404);return
                from .clinical.pre_event_state import inspect
                try:
                    result=inspect(connected_stack.path,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/source-state':
                if connected_stack is None:self.send_error(404);return
                from .clinical.source_state import inspect
                try:
                    result=inspect(connected_stack.path,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/interpretation':
                if connected_stack is None:self.send_error(404);return
                from .clinical.interpretation_state import inspect
                try:
                    result=inspect(connected_stack.path,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/assertions':
                if connected_stack is None:self.send_error(404);return
                from .clinical.assertion_reconciliation import inspect
                try:
                    result=inspect(connected_stack.path,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/passes':
                if connected_stack is None:self.send_error(404);return
                from .clinical.context_passes import inspect
                try:
                    result=inspect(connected_stack.path,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/context/source-stage':
                if connected_stack is None:self.send_error(404);return
                from .clinical.source_synthesis import saved_source_stage
                try:
                    if set(params)!={'context_id'} or len(params['context_id'])!=1:raise ValueError('Exact source context required')
                    result=saved_source_stage(connected_stack.path,params['context_id'][0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/context/progress':
                if connected_stack is None:self.send_error(404);return
                from .clinical.reconciliation import progress_context as progress
                try:
                    result=progress(connected_stack.path,params.get('context_id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/readiness/documents':
                if connected_stack is None:self.send_error(404);return
                from .clinical import document_clause_bindings
                try:
                    raw=json.dumps(document_clause_bindings.catalog(connected_stack.path),allow_nan=False).encode();mime='application/json'
                except (ValueError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/evidence-needs/candidates','/api/stack/evidence-needs/candidate-context'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import need_candidates
                try:
                    action=need_candidates.inspect_selection if parsed.path.endswith('/candidate-context') else need_candidates.inspect
                    raw=json.dumps(action(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/evidence-needs/accounts','/api/stack/evidence-needs/account-context'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import need_accounts
                try:
                    action=need_accounts.inspect_context if parsed.path.endswith('/account-context') else need_accounts.inspect
                    raw=json.dumps(action(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/evidence-timing','/api/stack/evidence-timing/window','/api/stack/evidence-timing/select'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import evidence_timing
                try:
                    action={'/api/stack/evidence-timing':evidence_timing.inspect,'/api/stack/evidence-timing/window':evidence_timing.inspect_window,'/api/stack/evidence-timing/select':evidence_timing.inspect_selection}[parsed.path]
                    raw=json.dumps(action(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/evidence-needs/answers':
                if connected_stack is None:self.send_error(404);return
                from .clinical import need_references
                try:
                    raw=json.dumps(need_references.answers(connected_stack.path,{k:params.get(k,[''])[0] for k in ('receipt_id','need_id')}),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/packet-followup/answers':
                if connected_stack is None:self.send_error(404);return
                from .clinical import followup_answers
                try:raw=json.dumps(followup_answers.inspect(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/packet-followup':
                if connected_stack is None:self.send_error(404);return
                from .clinical import packet_followup
                try:raw=json.dumps(packet_followup.inspect(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/internal-execution':
                if connected_stack is None:self.send_error(404);return
                from .clinical import internal_execution
                try:raw=json.dumps(internal_execution.inspect(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/work/answers':
                if connected_stack is None:self.send_error(404);return
                from .clinical import answer_retirement
                try:raw=json.dumps(answer_retirement.inspect(connected_stack.path,params.get('item',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/work-state':
                if connected_stack is None:self.send_error(404);return
                from .clinical import action_work_state
                try:raw=json.dumps(action_work_state.inspect(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/participation','/api/stack/participation/task'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import participation_work,participation_evidence
                try:
                    result=(participation_work.task_view(connected_stack.path,params.get('id',[''])[0]) if parsed.path.endswith('/task') else participation_evidence.registry(connected_stack.path,params.get('buyer',[''])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/patient-fit':
                if connected_stack is None:self.send_error(404);return
                from .clinical import patient_fit_work
                try:raw=json.dumps(patient_fit_work.inspect(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/patient-context/task':
                if connected_stack is None:self.send_error(404);return
                from .clinical import patient_context
                try:raw=json.dumps(patient_context.task_view(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/coverage/task':
                if connected_stack is None:self.send_error(404);return
                from .clinical import coverage_work
                try:raw=json.dumps(coverage_work.task_view(connected_stack.path,params.get('id',[''])[0]),allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/readiness/task','/api/stack/readiness/answer'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import care_readiness,evidence_dispositions
                try:
                    result=(care_readiness.snapshot(connected_stack.path,params.get('id',[''])[0]) if parsed.path.endswith('/task') else evidence_dispositions.answer_view(connected_stack.path,params.get('id',[''])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/cardiometabolic':
                if connected_stack is None:self.send_error(404);return
                from .clinical.cardiometabolic_review import inspect
                try:
                    result=inspect(connected_stack.path,params.get('id',[''])[0]);raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/domain-review':
                if connected_stack is None:self.send_error(404);return
                from .clinical.domain_review import page
                try:
                    result=page(connected_stack.path,params.get('domain',['respiratory'])[0],params.get('id',[''])[0],params.get('patient',[''])[0],int(params.get('offset',['0'])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/functional-review':
                if connected_stack is None:self.send_error(404);return
                from .clinical.functional_review import page
                try:
                    result=page(connected_stack.path,params.get('id',[''])[0],params.get('patient',[''])[0],int(params.get('offset',['0'])[0]))
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/decision','/api/stack/decision/source','/api/stack/decision/population-config'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import question_decision
                try:
                    if parsed.path.endswith('/population-config'):
                        from .clinical import cohorts,program_work
                        result={'as_of':connected_stack.retriever.meta['snapshot']['as_of'],'programs':program_work.registered_programs(),'cohort':cohorts.empty(),'scope':'Current received snapshot; typed cohort specification controls membership.'}
                    elif parsed.path.endswith('/source'):
                        a,c,b=question_decision.source_binding(connected_stack.retriever,params.get('answer_id',[''])[0])
                        result={'binding':b,'planning':question_decision.review_plan(a,c,b)}
                    else:result=question_decision.reopen(connected_stack.retriever,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/capabilities/availability':
                if connected_stack is None:self.send_error(404);return
                from .clinical.medication_partial_answers import task_availability as prepare
                try:
                    result=prepare(connected_stack.path,params.get('task_id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path in ('/api/stack/capabilities','/api/stack/capabilities/catalog','/api/stack/capabilities/tasks','/api/stack/capabilities/plan','/api/stack/capabilities/assessment'):
                if connected_stack is None:self.send_error(404);return
                from .clinical import care_capabilities
                try:
                    if parsed.path.endswith('/catalog'):result=care_capabilities.catalog()
                    elif parsed.path.endswith('/assessment'):result=care_capabilities.origin(connected_stack.path,{'kind':'assessment','id':params.get('id',[''])[0],'finding_ids':[]})
                    elif parsed.path.endswith('/tasks'):result=care_capabilities.task_view(connected_stack.path,params.get('buyer',[''])[0])
                    elif parsed.path.endswith('/plan'):result=care_capabilities.inspect_plan(connected_stack.path,params.get('id',[''])[0])
                    else:result=care_capabilities.portfolio(connected_stack.path,params.get('buyer',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/case-synthesis':
                if connected_stack is None:self.send_error(404);return
                from .clinical.source_synthesis import inspect
                try:
                    result=inspect(connected_stack.path,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/context':
                if connected_stack is None:self.send_error(404);return
                from .clinical.source_synthesis import context as inspect
                try:
                    result=inspect(connected_stack.path,params.get('id',[''])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/stack/state':
                if connected_stack is None:self.send_error(404);return
                from .clinical.clinical_state import review
                from .clinical.cohorts import select
                try:
                    selected=select(connected_stack.path,patient=params.get('patient',[None])[0])
                    result=review(connected_stack.path,selected['patient_ids'],params.get('contract',['all'])[0])
                    raw=json.dumps(result,allow_nan=False).encode();mime='application/json'
                except (ValueError,TypeError,KeyError,FileNotFoundError) as exc:self.send_error(400,str(exc)[:200]);return
            elif parsed.path=='/api/query':
                try: result=query(root,params.get('q',[''])[0])
                except ValueError: self.send_error(400,'A bounded nonempty question is required');return
                raw=json.dumps(result).encode();mime='application/json'
            elif parsed.path=='/api/evidence':
                if not (root/'evidence.sqlite3').exists():self.send_error(404,'Narrative index unavailable for this historical run');return
                try:
                    from .evidence_index import search
                    result=search(root/'evidence.sqlite3',params.get('q',[''])[0],patient_id=params.get('patient',[None])[0])
                except ValueError:self.send_error(400,'A bounded nonempty question is required');return
                raw=json.dumps(result).encode();mime='application/json'
            elif parsed.path=='/api/patient':
                pid=params.get('id',[''])[0];row=next((r for r in lines(root/'patients.jsonl') if r['patient_id']==pid),None)
                if row is None:self.send_error(404);return
                if annotation_root is not None:
                    from .clinical.reader_warehouse import search as annotation_search
                    row['learned_text_annotations']=annotation_search(annotation_root/'annotations.sqlite3',patient=pid,limit=25)
                raw=json.dumps(row).encode();mime='application/json'
            elif parsed.path=='/api/annotations':
                if annotation_root is None:self.send_error(404,'Reader annotations not attached');return
                try:
                    from .clinical.reader_warehouse import search as annotation_search
                    filters={key:params[key][0] for key in ('patient','assertion','subject','time','relation','priority') if key in params}
                    result=annotation_search(annotation_root/'annotations.sqlite3',**filters,
                        limit=int(params.get('limit',['20'])[0]),offset=int(params.get('offset',['0'])[0]))
                except (ValueError,TypeError):self.send_error(400,'Invalid annotation filters');return
                raw=json.dumps(result).encode();mime='application/json'
            elif parsed.path=='/api/evidence-catalog':
                if query_root is None:self.send_error(404,'Evidence query index unavailable');return
                from .clinical.evidence_queries import CATALOG
                from .clinical.evidence_language import EXAMPLES
                raw=json.dumps({'synthetic':True,'queries':CATALOG,'question_examples':EXAMPLES,'metadata':query_metadata}).encode();mime='application/json'
            elif parsed.path=='/api/evidence-ask':
                if query_root is None:self.send_error(404,'Evidence query index unavailable');return
                try:
                    from .clinical.evidence_language import answer
                    result=answer(query_root,params.get('q',[''])[0],limit=int(params.get('limit',['20'])[0]),offset=int(params.get('offset',['0'])[0]))
                except (ValueError,TypeError):self.send_error(400,'Invalid bounded evidence question');return
                raw=json.dumps(result).encode();mime='application/json'
            elif parsed.path=='/api/evidence-query':
                if query_root is None:self.send_error(404,'Evidence query index unavailable');return
                try:
                    from .clinical.evidence_queries import execute
                    encoded=params.get('plan',[''])[0]
                    if not encoded or len(encoded)>12000:raise ValueError('Bounded plan required')
                    result=execute(query_root,json.loads(encoded),limit=int(params.get('limit',['20'])[0]),offset=int(params.get('offset',['0'])[0]))
                except (ValueError,TypeError):self.send_error(400,'Invalid evidence plan');return
                raw=json.dumps(result).encode();mime='application/json'
            elif parsed.path=='/explore':
                if query_root is None:self.send_error(404,'Evidence query index unavailable');return
                from .clinical.evidence_workbench import page
                raw=page().encode();mime='text/html; charset=utf-8'
            elif parsed.path=='/api/reader':
                if reader_metadata is None:self.send_error(404,'Reader annotations not attached');return
                raw=json.dumps({**reader_metadata,'evidence_queries_available':query_root is not None}).encode();mime='application/json'
            elif parsed.path=='/reader':
                if annotation_root is None:self.send_error(404,'Reader annotations not attached');return
                from .clinical.reader_review import page
                raw=page().encode();mime='text/html; charset=utf-8'
            elif parsed.path=='/health':raw=b'{"status":"ready","synthetic":true}';mime='application/json'
            else:
                path=(root/(parsed.path.lstrip('/') or 'index.html')).resolve()
                if path.parent!=root or path.suffix not in {'.html','.json'} or not path.is_file():self.send_error(404);return
                raw=path.read_bytes();mime=mimetypes.guess_type(path)[0] or 'application/octet-stream'
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    if connected_stack is not None and request_locks is not None and not recovery:
        # Saved monitors recompute on a new snapshot in the background (never delays startup or blocks questions).
        def _monitor_sweep():
            try:
                from .clinical.monitor_sweep import sweep_if_due
                with request_locks.hold('state', ('saved_state',)):  # same lock family as saved-state writes: never races an answer
                    sweep_if_due(connected_stack)
            except Exception as exc:  # noqa: BLE001 - a failed sweep is logged, never fatal
                print(f'monitor sweep skipped: {type(exc).__name__}: {exc}',flush=True)
        threading.Thread(target=_monitor_sweep,name='agere-monitor-sweep',daemon=True).start()
    print(f'Agere ready: http://127.0.0.1:{port}',flush=True);server.serve_forever()
