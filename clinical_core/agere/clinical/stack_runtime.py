"""One question → registered operations → source evidence → selective model review."""
from pathlib import Path
import hashlib,json,os,sqlite3,time
from ...contracts import digest
from ..gateway import BudgetLedger, default_ledger_path
from .. import model_policy
from .warehouse_retrieval import Retriever
from .analytic_registry import execute,CATALOG
from .stack_planner import validate,propose,review
from .cohorts import select,capabilities

VERSION='integrated-healthcare-stack/2'
ROOT=Path(__file__).resolve().parents[3]

class Stack:
    shared_request_planning = True
    def __init__(self,path,*,allow_live=False,rerank=True):
        from .warehouse_concurrency import configure
        self.database_concurrency=configure(path)
        self.path=Path(path);self.retriever=Retriever(path,rerank=rerank);self.allow_live=allow_live
        self.receipts=self.path/'provider-receipts';self.receipts.mkdir(exist_ok=True)
        from . import schema_rerank;schema_rerank.use_encoder(self.retriever.encoder)  # schema reranker reuses the loaded encoder
    def close(self):self.retriever.close()
    def metadata(self):
        from .analysis_graph import catalog
        return {'schema':VERSION,'synthetic':True,'retrieval':self.retriever.meta,
                'local_retrieval_runtime':{
                    'encoder_weights_loaded':getattr(self.retriever.encoder,'weights_loaded',None),
                    'reranker_weights_loaded':getattr(self.retriever.reranker,'weights_loaded',None) if self.retriever.reranker else False,
                    'scope':'Retrieval encoder/reranker only; configured availability is separate from loaded weights.'},
                'database_concurrency':self.database_concurrency,
                'model_roles':self.model_roles(),
                'operators':json.loads((self.path/'operators.json').read_text()),'allow_live':self.allow_live,
                'cohort_contract':capabilities(),
                'population_composition':catalog(self.path),
                'clinical_events':json.loads((self.path/'clinical-events.json').read_text()) if (self.path/'clinical-events.json').exists() else None,
                'observed_values':json.loads((self.path/'cohort-values.json').read_text()) if (self.path/'cohort-values.json').exists() else None,
                'layers':['Bitemporal identity/correction snapshot','Mapped structured and received narrative evidence',
                  'FTS5 + pinned local embedding model','Reciprocal-rank fusion + BGE cross-encoder',
                  'Source-diverse packets plus attached v4 chart-context annotations','Registered deterministic analytic operators',
                  'Complete typed cohort selection with unknown membership','Explicit clinical-event date bindings',
                  'Native source meanings across medication, care, access, finance and goals; unresolved links retained',
                  'Versioned received-source contracts for hospital, renewal, procedure, pathology, home-health and community accounts; typed scalar/list references',
                  'Medication interviews, pharmacy clarification and allergy-history accounts with explicit uncertainty',
                  'Item-level medication chains with typed source links, copied-response lineage and exact documentary review/acquisition contracts',
                  'Mutation-tracked patient input fingerprints for protocol and baseline reuse; changed patients rehash with exact invalidation',
                  'Nested diagnostic measurements with report quality, literal units and original-source dependencies',
                  'Inpatient echo measurements with acquisition clocks, source-specific quality and final-status requirements',
                  'Native account predicates composed with reviewed assertions into population requirements and program review',
                  'Minimal conditional evidence sets and cohort-scoped source-review requests with immutable provenance and stale-source checks',
                  'Same-patient specimen and encounter clock dependencies with exact field and record hashes',
                  'Cohort-bound CUSUM/EWMA and actuarial runout operators',
                  'Composable structured/narrative populations and exploratory statistical comparisons',
                  'Source-bound event sequences, literal narrative dates and provisional episode reconciliation',
                  'Account snapshot selection, integer-cent conservation and same-patient payment/refund/payer-cash reconciliation',
                  'Receipt-time decision windows with explicit future clinical endpoints, known-at-decision inputs and separate late historical exclusions',
                  'Patient-specific pre-index evidence joins, separate outcome targets, bounded arrival blocks and candidate feature exports',
                  'Index-availability and follow-up qualification, protected original-cohort development partitions and guarded local outcome-model comparisons',
                  'Source-bound protocol review combining structured measurements, narrative proposals, missing inputs and immutable program selection evidence',
                  'Versioned index/outcome windows, encounter-aware observation links, incomplete follow-up accounting and auditable training-target exports',
                  ('Fitted local research predictors attached to this snapshot' if (self.path/'forecast-link.json').exists() else 'Forecasting code available; no source-specific predictor attached to this snapshot'),
                  'Poisson block segmentation of source arrivals and forecast features',
                  'Versioned quoted program rules with three-valued input bindings',
                  'Quoted denominator/numerator/exclusion/attribution composition with shared-source logical sensitivity bounds and exact patient receipts',
                  'Conditional quoted-rule acquisition separating shared missing specifications from patient evidence requests',
                  'Persistent evidence-linked review work, ownership, program states and operational history',
                  'Buyer-declared care services, dated public pathway discovery, source-linked matching and assigned internal evidence handoffs; benefits and clinical execution remain unverified',
                  'Versioned clinical evidence prerequisites, source-bound documentary states and dose notation',
                  'Pinned offline RxNorm medication identities and documentary formulation/protocol context',
                  'Task-scoped source context, protected exceptions/history, omission receipts and native-token dispatch guard',
                  'Nested narrative leaves and structured siblings preserved together in source context',
                  'Whole-statement chart passes and source-linked specialist proposals with unresolved disagreement',
                  'Reusable source assertions, distinct relation/clock accounts, copy-forward controls and explicit evidence-acquisition steps',
                  'Shared typed statement refinement for Perplexity/Jev and experimental native Kev/d1; explicit source-review corrections',
                  'Reviewed-family training snapshots; fitted lexical and MiniLM semantic heads with calibration and shadow-only release receipts',
                  'Family-protected source learning queue, extraction audits, deliberate target marking and review-linked machine suggestions',
                  'Protected native Kev/d1 acquisition, provenance-bound machine supervision and optional teacher-imitation heads; no automatic acceptance',
                  'Reusable documentary source contracts attached to clinical protocol and program-selection evidence without altering clinical criteria',
                  'Exact-span document roles; signature/metadata exclusions from documentary accounts',
                  'Immutable program accountability with financial review queues, separate contract prerequisites and source-bound work assessments',
                  'Versioned documentary requirement composition, conditional evidence-acquisition impact and downstream population/worklist reuse',
                  'Local plan compiler and compact candidate retrieval; typed Perplexity routing; frontier escalation',
                  'Bounded planner scope selects operators and exact source-field contracts, retaining explicit unloaded and unmet requirements',
                  'Optional Jev/Perplexity patient-scoped evidence review',
                  'Content-addressed model/result caches and shared usage ledger'],
                'clinical_execution_authorized':False}
    def run_plan(self,plan):
        if isinstance(plan,dict) and 'nodes' in plan:return self.run_graph(plan)
        plan=validate(plan,self.retriever.meta['snapshot']['as_of'])
        # Resolve all patient references before executing any step.
        for step in plan['steps']:
            if step['patient_id'] and not self.retriever.db.execute('SELECT 1 FROM patients WHERE id=?',(step['patient_id'],)).fetchone():raise ValueError('Unknown patient in plan')
        t=time.perf_counter();results=[]
        selection=select(self.path,plan['cohort'])
        for step in plan['steps']:
            if step['operation']=='evidence_search':
                result=self.retriever.search(step['query'],patient=step['patient_id'] or None,
                                             patient_ids=selection['patient_ids'],analysis_window=plan['analysis_window'])
            else:result=execute(self.path,step['operation'],patient=step['patient_id'] or None,
                                drug_class=step['drug_class'] or None,view=step['view'],
                                cohort=plan['cohort'],analysis_window=plan['analysis_window'])
            results.append({'step':step,'result':result})
        return {'schema':VERSION,'synthetic':True,'plan':plan,'results':results,
                'cohort':{k:v for k,v in selection.items() if k not in ('patient_ids','unknown_patient_ids')},
                'elapsed_seconds':time.perf_counter()-t,
                'scope':'Only registered observations computed. All unmet clinical/data requirements remain open.',
                'new_paid_api_calls':0,'clinical_execution_authorized':False}
    def run_graph(self,plan,*,question=None):
        from .analysis_graph import execute
        return execute(self,plan,question=question)
    def model_roles(self):
        """The live question cascade as built (proposers.TIERS/ORDER/NOT_PROPOSERS), Qwen status from the env defaults and
        component-ledger counts; nothing here is hand-maintained, so it cannot go stale."""
        from collections import Counter
        from ..gateway import PersistentGateway
        from .nl2sql import MODEL_DIR
        from . import proposers as P
        live=bool(self.allow_live);env=lambda k,d='on':os.environ.get(k,d)
        cascade=[{'tier':n,'cost':P.TIERS[n][0],'calibration':P.TIERS[n][1],'proposes':P.TIERS[n][2],'paid':n in P.PAID,
                  'enabled':P.enabled(n,self)} for n in P.ORDER]
        qwen_on=MODEL_DIR.exists()
        qwen={'model_installed':qwen_on,
              'local_normalizer':qwen_on and env('AGERE_LOCAL_NORMALIZER')=='on',
              'qwen_typed_interpreter':qwen_on and env('AGERE_LOCAL_TYPED')!='off',
              'qwen_rewrite_compiler':qwen_on and env('AGERE_LOCAL_MODEL')!='off','bottled_sql':env('AGERE_BOTTLED')!='off',
              'local_free_sql':qwen_on and env('AGERE_LOCAL_SQL')=='on',
              'role':'free checked proposers: every proposal is recompiled and literal-checked before it executes'}
        try:
            components=json.loads((ROOT/'config/agere/component_ledger.json').read_text())['components']
            wiring=Counter(c['current_wiring'].split(':',1)[0] for c in components)
            ledger={'components':len(components),'by_wiring':dict(sorted(wiring.items())),
                    'off_path':sorted(c['id'] for c in components if c['current_wiring'] in ('not_on_path','named_only'))}
        except (OSError,ValueError,KeyError) as exc:
            ledger={'error':f'{type(exc).__name__}: {exc}'[:200]}
        return {'planning_order':['exact tiers: contract cache, learned shapes, fully consumed grammar',
                                  *[f"{c['tier']} ({c['cost']})" for c in cascade if c['enabled']]],
                'cascade':cascade,'cascade_version':P.VERSION,'shared_request_version':'warehouse-request/1',
                'not_proposers':dict(P.NOT_PROPOSERS),'feedback_readers':list(P.FEEDBACK_READERS),
                'typed_with_population':sorted(P.TYPED_WITH_POPULATION),
                'proposers_on':env('AGERE_PROPOSERS')!='off',
                'skipped_by_env':sorted(s.strip() for s in env('AGERE_PROPOSER_SKIP','').split(',') if s.strip()),
                'frontier_model':os.environ.get('AGERE_SHARED_INTERPRETER_MODEL') or 'tiered',
                'model_ladder':{'ladder':model_policy.ladder('planner'),'rule':'Sonnet entry; Opus escalation after this role failed. Native computation, qualified local models and System One retain their roles.'},
                'question_interpretation_llm_enabled':live,'deterministic_first':True,'hosted_enabled':live,
                'qwen3_4b_available':qwen_on,'qwen3_4b_active':any(v for k,v in qwen.items() if k not in ('model_installed','role')),
                'qwen_status':qwen,'component_ledger':ledger,
                'source_readers':['Jev/Perplexity source tools: patient-scoped evidence review (Stack.review)'],
                'scope':'Every proposal passes the literal-question and requested-output checks before it answers; not clinical qualification.'}
    def compose(self,question,*,planning='auto',population=None,parent=None):
        from .shared_planner import compose
        if planning not in ('auto', 'frontier'):
            raise ValueError('The shared planner is the active question policy; legacy candidate dispatch is disabled')
        return compose(self,question,population=population,parent=parent,planning=planning)
    def _compose(self,question,*,planning='auto'):
        return self.compose(question,planning=planning)
    def _legacy_compose(self,question,*,planning='auto'):
        if planning=='scoped_local_candidate':
            from .scoped_review_compiler import compile_question
            with sqlite3.connect(self.path/'stack.sqlite3') as db:
                roster={row[0] for row in db.execute('SELECT id FROM patients')}
            graph,receipt,seed=compile_question(question,as_of=self.retriever.meta['snapshot']['as_of'],known_patient_ids=roster)
            result=self.run_graph(graph,question=question)
            return {**result,'question':question,'route':'scoped_local_candidate','planner_receipt':receipt,
                    'decision_seed':seed,'new_paid_api_calls':result['new_paid_api_calls'],
                    'planning_economics':{'new_api_estimate_usd':0,'total_cost_known':False,
                        'scope':'No hosted planning. Local development, execution, maintenance and human review costs are unmeasured.'}}
        from .graph_planner import propose
        from .analysis_graph import catalog
        if planning not in ('auto','frontier'):raise ValueError('Unknown planning policy')
        if planning=='frontier' and not self.allow_live:raise ValueError('Live model route disabled on this server')
        ledger=BudgetLedger(default_ledger_path());pending=None
        try:
            values=json.loads((self.path/'cohort-values.json').read_text()) if (self.path/'cohort-values.json').exists() else None
            from .source_profile import load as load_profile
            from .semantic_layer import describe as describe_semantic
            profile=load_profile(self.path);semantic=describe_semantic(self.path)
            def frontier(capabilities,feedback=None,ledger=ledger):  # ledger: a worker thread passes its own connection
                return propose(question,self.receipts,ledger,snapshot=self.retriever.meta['snapshot'],
                               graph_catalog=capabilities,observed_values=values,profile=profile,semantic=semantic,feedback=feedback)
            timings={};t0=tick=time.perf_counter()  # per-stage timing receipt (seconds)
            def lap(stage):
                nonlocal tick
                now=time.perf_counter();timings[stage]=round(timings.get(stage,0.0)+now-tick,3);tick=now
            if planning=='auto':
                from .economical_planner import plan
                graph,receipt=plan(self,question,ledger,frontier)
                calls=receipt['new_paid_api_calls']
            else:
                graph,receipt=frontier(catalog(self.path));calls=0 if receipt.get('cache_hit') else 1
            lap('planning_s')
            if isinstance(receipt,dict) and receipt.get('route')=='deferred_to_analyst':
                result=self.run_graph(graph,question=question)
                usd=sum(((s.get('receipt') or {}).get('cost_estimate') or {}).get('estimated_usd') or 0 for s in receipt.get('steps') or [])
                return {**result,'question':question,'route':'deferred_to_analyst','planner_receipt':receipt,
                        'new_paid_api_calls':receipt.get('new_paid_api_calls',0)+result.get('new_paid_api_calls',0),
                        'planning_economics':{'new_api_estimate_usd':round(usd,6),'known_api_estimate_usd':round(usd,6),
                                              'total_cost_known':True,'scope':'Free and governed rungs only; the analysts answer.'}}
            if planning=='auto' and isinstance(receipt,dict):
                # The cross-check's independent path starts now, in parallel with the primary execution (answer_guard.start);
                # the answer waits for it at most until AGERE_CROSS_CHECK_BUDGET_S after the question started.
                from .answer_guard import start as start_cross_check,budget_s
                deadline=t0+budget_s()
                pending=start_cross_check(self,question,graph,receipt,ledger=ledger,profile=profile,deadline=deadline,
                                          encoder=getattr(getattr(self,'retriever',None),'encoder',None))
                timings['cross_check_started']='with_plan' if pending is not None else 'not_started'
            try:
                result=self.run_graph(graph,question=question)
            except ValueError as exc:
                # Heal, don't block: re-plan once with the execution error as feedback.
                if not self.allow_live:raise
                graph,retry=frontier(catalog(self.path),feedback=f'{type(exc).__name__}: {exc}')
                receipt={**receipt,'steps':[*receipt.get('steps',[]),{'stage':'replan_after_execution_error','error':str(exc)[:400],'receipt':retry}],
                         'new_paid_api_calls':receipt.get('new_paid_api_calls',0)+(0 if retry.get('cache_hit') else 1)}
                calls=receipt['new_paid_api_calls'] if planning=='auto' else calls+1
                result=self.run_graph(graph,question=question)
                # Replace the cached plan that failed so the next ask reuses the working one.
                with sqlite3.connect(self.path/'question-plans.sqlite3') as plans:
                    for key,payload in plans.execute('SELECT id,payload FROM plans').fetchall():
                        saved=json.loads(payload)
                        if saved.get('question')==question:
                            plans.execute('UPDATE plans SET payload=? WHERE id=?',(json.dumps({**saved,'graph':graph,'receipt':receipt}),key))
            lap('execution_s')
            if planning=='auto' and isinstance(receipt,dict):
                # Answer shape / engine fit (answer_shape.py): reshape when unambiguous (one-row table -> its column, the
                # any_source row, one row per patient -> patient set, stated rounding); a route whose output does not hold
                # the quantity in the requested shape is declined and the question re-planned once through verified SQL.
                from .answer_shape import review as fit_review
                graph,receipt,result,fit_calls,replaced=fit_review(self,question,graph,receipt,result,ledger=ledger,profile=profile)
                calls+=fit_calls
                if replaced and pending is not None:pending.cancel();pending=None  # the started cross-check checked the declined rung
                lap('fit_s')
            if planning=='auto' and isinstance(receipt,dict):
                # Confidently-wrong guard (answer_guard.py): a zero/empty/implausible answer probes the full received catalog and
                # re-plans once or abstains naming the unused source; an uncertified path is cross-checked by one independent path.
                from .answer_guard import review as guard_review
                graph,receipt,result,guard_calls=guard_review(self,question,graph,receipt,result,ledger=ledger,frontier=frontier,profile=profile,
                                                              pending=pending,deadline=deadline);pending=None
                calls+=guard_calls
                gt=(receipt.get('answer_guard') or {}).get('timings') or {}
                timings['guard_probe_s']=round(gt.get('guard_probe_s',0.0),3);timings['cross_check_s']=round(gt.get('cross_check_s',0.0),3)
                tick=time.perf_counter()
            readings=(receipt.get('hosted_sql') or {}).get('readings') if planning=='auto' and isinstance(receipt,dict) else None
            if readings and isinstance(result.get('answer'),dict):  # FM-19: both defensible readings, never a silent pick
                from .verification_v2 import attach_readings
                result={**result,'answer':attach_readings(result['answer'],readings)}
            if planning=='auto' and isinstance(receipt,dict) and isinstance(result.get('answer'),dict):  # metric-first answer contract
                from .metric_router import finalize_answer
                result={**result,'answer':finalize_answer(result['answer'],receipt,encoder=getattr(getattr(self,'retriever',None),'encoder',None))}
            from .economical_planner import economics
            costing=economics(receipt if planning=='auto' else {'steps':[{'receipt':receipt}]})
            if planning=='auto':  # guard confidence after the answer contract: abstention / disagreement low, agreement cross-checked
                from .answer_guard import finish as guard_finish
                result=guard_finish(result,receipt)
                from .answer_shape import final as fit_final  # the shipped answer: requested shape and stated rounding
                result=fit_final(question,graph,receipt,result)
                if isinstance(receipt,dict) and receipt.get('route') in ('dq_family','vbc_family'):
                    try:  # the deterministic family answer becomes a verified (question -> SQL) pair (family_common.record)
                        from .family_common import record_route
                        record_route(self.path,question,receipt,result.get('answer'))
                    except Exception:  # noqa: BLE001 - learning bookkeeping never fails an answer
                        pass
            lap('finish_s');timings['total_s']=round(time.perf_counter()-t0,3)
            if isinstance(receipt,dict):receipt['timings']=timings
            return {**result,'question':question,'route':'economical_composition_local_execution' if planning=='auto' else 'frontier_composition_local_execution',
                    'planner_receipt':receipt,'planning_economics':costing,'new_paid_api_calls':result['new_paid_api_calls']+calls}
        finally:
            if pending is not None:pending.cancel()  # an execution error: the started check is not needed
            ledger.close()
    def ask(self,question,*,live=False):
        if not live:return {'schema':VERSION,'synthetic':True,'question':question,'route':'local_retrieval',
                            'retrieval':self.retriever.search(question),'new_paid_api_calls':0,
                            'scope':'Ranked evidence examples. Use economical planning to select supported analytic operations.'}
        return self.compose(question)
    def synthesize_context(self,context_id):
        from .reconciliation import synthesize_context as synthesize
        ledger=BudgetLedger(default_ledger_path())
        try:return synthesize(self.path,context_id,ledger,allow_live=self.allow_live)
        finally:ledger.close()
    def prepare_question_context(self,patient,question,selection='passage_reference'):
        if selection=='passage_reference':
            from .question_context import prepare
        elif selection=='schema_candidate':
            from .source_selection import prepare
        else:raise ValueError('Unknown source-selection route')
        return prepare(self.path,patient,question,self.retriever)
    def find_need_candidates(self,request):
        from .need_candidates import prepare
        return prepare(self.path,request,self.retriever)
    def reconcile_stable_references(self,answer_id):
        from .stable_reconciliation import synthesize
        ledger=BudgetLedger(default_ledger_path())
        try:return synthesize(self.path,answer_id,ledger,allow_live=self.allow_live)
        finally:ledger.close()
    def plan_context(self,context_id):
        from .reconciliation import preview_context as preview,synthesize_context as synthesize
        result=preview(self.path,context_id)
        ledger=BudgetLedger(default_ledger_path())
        try:
            budget=ledger.receipt();saved=None
            try:saved=synthesize(self.path,context_id,ledger,allow_live=False)['id']
            except ValueError as error:
                if 'Live synthesis route disabled' not in str(error):raise
            return {**result,'remaining_reservation_usd':budget['remaining_reservation_usd'],
                'spending_limit':budget['spending_limit'],'authorized_cap_usd':budget['authorized_cap_usd'],
                'saved_answer_id':saved,'can_dispatch':bool(saved) or (self.allow_live and (budget['remaining_reservation_usd'] is None or budget['remaining_reservation_usd']>=result['reservation_ceiling_usd'])),
                'live_route_enabled':self.allow_live}
        finally:ledger.close()
    def review_passes(self,bundle_id,provider,maximum_passes):
        from .context_passes import review,inspect
        from .assertion_reconciliation import reconcile_bundle,bind_specialist_review
        # Compute/validate local dependencies before spending on new specialist calls.
        bundle=inspect(self.path,bundle_id,verify_sources=True)
        reconciliation=reconcile_bundle(self.path,bundle_id) if bundle['parameters']['concepts'] else None
        ledger=BudgetLedger(default_ledger_path())
        try:
            result=review(self.path,bundle_id,provider,maximum_passes,ledger,
                          self.path/'pass-review-receipts',allow_live=self.allow_live)
            return bind_specialist_review(bundle,result,reconciliation) if reconciliation else result
        finally:ledger.close()
    def refine_statement(self,target,provider):
        if provider in ('student_lexical','student_minilm','student_bioclinical'):
            from .semantic_student import read
            return read(self.path,target,provider)
        from .semantic_reader import read
        return read(self.path,target,provider,allow_live=self.allow_live)
    def review(self,question,patient,provider):
        if not self.allow_live:raise ValueError('Live model route disabled on this server')
        if not patient:raise ValueError('Explicit patient required for clinical evidence review')
        evidence=self.retriever.search(question,patient=patient,limit=8)
        ledger=BudgetLedger(default_ledger_path())
        try:
            proposal=review(question,evidence,provider,ledger)
            return {'synthetic':True,'question':question,'patient_id':patient,'retrieval':evidence,
                    'model_proposal':proposal,'new_paid_api_calls':0 if proposal.get('cache_hit') or proposal.get('status') in ('prepared','credential_missing','no_evidence') else 1,
                    'clinical_execution_authorized':False}
        finally:ledger.close()
