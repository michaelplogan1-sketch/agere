"""One question → registered operations → source evidence → selective model review."""
from pathlib import Path
import hashlib,json,sqlite3,time
from ...contracts import digest
from ..gateway import BudgetLedger
from .warehouse_retrieval import Retriever
from .analytic_registry import execute,CATALOG
from .stack_planner import validate,propose,review
from .cohorts import select,capabilities

VERSION='integrated-healthcare-stack/2'
ROOT=Path(__file__).resolve().parents[3]

class Stack:
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
        from ..gateway import PersistentGateway
        return {'planning_order':['local compiler / exact question cache','local MiniLM plan-candidate retrieval',
                                  'Perplexity typed route selection','Claude for unsupported or uncertain composition'],
                'frontier_model':PersistentGateway.MODELS['claude'],
                'decision_router':PersistentGateway.MODELS['perplexity'],
                'retrieval_model':Path(self.retriever.meta['encoder_path']).name,
                'evidence_reviewers':{k:PersistentGateway.MODELS[k] for k in ('jev','perplexity')},
                'trained_source_students':{'attached':(self.path/'semantic-student-link.json').exists(),
                    'roles':['lexical three-axis classification','frozen MiniLM with learned three-axis heads'],
                    'status':'optional shadow proposals; no automatic acceptance',
                    'details':'/api/stack/semantic-students'},
                'source_distillation':{'attached':(self.path/'source-distillation-link.json').exists(),
                    'status':'protected development acquisition and machine-supervised shadow readers; teacher agreement is not clinical accuracy',
                    'details':'/api/stack/source-distillation'},
                'source_synthesis':{'provider':'claude','role':'bounded cross-source explanation with exact citations and review proposals',
                    'clinical_authority':False,'native_token_check':True,'saved_answer_route':'/api/stack/case-synthesis',
                    'large_context':'bounded source passes and dependency-preserving excerpt reconciliation',
                    'question_scope':'hybrid retrieval plus typed source closure; sufficiency unassessed'},
                'qwen3_4b_active':False,'qwen_status':'Earlier experimental reader; not a current planner or default reader',
                'scope':'Local models and code handle bounded tasks. Hosted decision routing is not a newly trained local SLM. No universal best model or calibrated end-to-end routing quality is established.'}
    def compose(self,question,*,planning='auto'):
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
        ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
        try:
            values=json.loads((self.path/'cohort-values.json').read_text()) if (self.path/'cohort-values.json').exists() else None
            from .source_profile import load as load_profile
            from .semantic_layer import describe as describe_semantic
            profile=load_profile(self.path);semantic=describe_semantic(self.path)
            def frontier(capabilities,feedback=None):
                return propose(question,self.receipts,ledger,snapshot=self.retriever.meta['snapshot'],
                               graph_catalog=capabilities,observed_values=values,profile=profile,semantic=semantic,feedback=feedback)
            if planning=='auto':
                from .economical_planner import plan
                graph,receipt=plan(self,question,ledger,frontier)
                calls=receipt['new_paid_api_calls']
            else:
                graph,receipt=frontier(catalog(self.path));calls=0 if receipt.get('cache_hit') else 1
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
            readings=(receipt.get('hosted_sql') or {}).get('readings') if planning=='auto' and isinstance(receipt,dict) else None
            if readings and isinstance(result.get('answer'),dict):  # FM-19: both defensible readings, never a silent pick
                from .verification_v2 import attach_readings
                result={**result,'answer':attach_readings(result['answer'],readings)}
            if planning=='auto' and isinstance(receipt,dict) and isinstance(result.get('answer'),dict):  # metric-first answer contract
                from .metric_router import finalize_answer
                result={**result,'answer':finalize_answer(result['answer'],receipt,encoder=getattr(getattr(self,'retriever',None),'encoder',None))}
            from .economical_planner import economics
            costing=economics(receipt if planning=='auto' else {'steps':[{'receipt':receipt}]})
            return {**result,'question':question,'route':'economical_composition_local_execution' if planning=='auto' else 'frontier_composition_local_execution',
                    'planner_receipt':receipt,'planning_economics':costing,'new_paid_api_calls':result['new_paid_api_calls']+calls}
        finally:ledger.close()
    def ask(self,question,*,live=False):
        if not live:return {'schema':VERSION,'synthetic':True,'question':question,'route':'local_retrieval',
                            'retrieval':self.retriever.search(question),'new_paid_api_calls':0,
                            'scope':'Ranked evidence examples. Use economical planning to select supported analytic operations.'}
        return self.compose(question)
    def synthesize_context(self,context_id):
        from .reconciliation import synthesize_context as synthesize
        ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
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
        ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
        try:return synthesize(self.path,answer_id,ledger,allow_live=self.allow_live)
        finally:ledger.close()
    def plan_context(self,context_id):
        from .reconciliation import preview_context as preview,synthesize_context as synthesize
        result=preview(self.path,context_id)
        ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
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
        ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
        try:
            result=review(self.path,bundle_id,provider,maximum_passes,ledger,
                          self.path/'pass-review-receipts',allow_live=self.allow_live)
            return bind_specialist_review(bundle,result,reconciliation) if reconciliation else result
        finally:ledger.close()
    def refine_statement(self,target,provider):
        if provider in ('student_lexical','student_minilm'):
            from .semantic_student import read
            return read(self.path,target,provider)
        from .semantic_reader import read
        return read(self.path,target,provider,allow_live=self.allow_live)
    def review(self,question,patient,provider):
        if not self.allow_live:raise ValueError('Live model route disabled on this server')
        if not patient:raise ValueError('Explicit patient required for clinical evidence review')
        evidence=self.retriever.search(question,patient=patient,limit=8)
        ledger=BudgetLedger(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
        try:
            proposal=review(question,evidence,provider,ledger)
            return {'synthetic':True,'question':question,'patient_id':patient,'retrieval':evidence,
                    'model_proposal':proposal,'new_paid_api_calls':0 if proposal.get('cache_hit') or proposal.get('status') in ('prepared','credential_missing','no_evidence') else 1,
                    'clinical_execution_authorized':False}
        finally:ledger.close()
