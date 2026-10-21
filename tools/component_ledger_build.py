"""Build the Agere component ledger (read-only: parses source files, optional import check)."""
import ast, json, re, sys, importlib
from pathlib import Path

WT = Path('/Users/mikel/clinical-insights-local/.claude/worktrees/wire-all-proposers-115154')
OUT = Path('/private/tmp/claude-501/-Users-mikel/90323f53-aa24-4510-bf37-3d548a0418ad/scratchpad/component_ledger_draft.json')
C = 'clinical_core.agere.clinical.'
A = 'clinical_core.agere.'
P = 'clinical_core/agere/clinical/proposers.py'
SP = 'clinical_core/agere/clinical/shared_planner.py'
EP = 'clinical_core/agere/clinical/economical_planner.py'
AG = 'clinical_core/agere/clinical/analysis_graph.py'
DOC_BRIEF = 'docs/agere/brief-2026-10-21/BUILD_BRIEF_PHASE0-2.md'
OPUS, HAIKU, SONNET = 'paid:claude-opus-5-5', 'paid:claude-haiku-5-5', 'paid:claude-sonnet-5-5'

rows = []


def E(id, name, family, module, symbol, role, wiring, ev='', cost='free', calib=None, flag=None):
    rows.append(dict(id=id, name=name, family=family, module=module, symbol=symbol, role_on_question_path=role,
                     current_wiring=wiring, evidence=ev, cost=cost, calibration=calib, flag=flag))


def tier(n):
    return f'cascade_tier:{n}'


def stage(n):
    return f'cascade_tier:shared_planner.{n}'

# ---------------------------------------------------------------- question path spine (infrastructure)
E('api_stack_compose_route', '/api/stack/compose route', 'infrastructure', A + 'service', 'serve',
  'HTTP entry for a user question; hands the body to conversation.handle_compose.', stage('entry'),
  'clinical_core/agere/service.py:289-292')
E('conversation_handle_compose', 'Conversation-aware compose handler', 'infrastructure', C + 'conversation', 'handle_compose',
  'Resolves follow-ups, binds saved cohorts, calls Stack.compose, logs and captures verified pairs.', stage('conversation'),
  'conversation.py:689,706,739,756,761')
E('stack_compose', 'Stack.compose', 'infrastructure', C + 'stack_runtime', 'Stack.compose',
  'Only active question policy; delegates to shared_planner.compose (legacy dispatch disabled).', stage('entry'),
  'stack_runtime.py:122-126')
E('shared_planner_compose', 'Shared planner compose', 'infrastructure', C + 'shared_planner', 'compose',
  'data_domains gate, plan(), then executes the contract by kind (checked_graph/request_program/general_analysis/request).', stage('entry'),
  f'{SP}:889-933')
E('shared_planner_plan', 'Shared planner plan()', 'infrastructure', C + 'shared_planner', 'plan',
  'Exact cache -> learned shapes -> grammar (via checked_interpret with no model) -> proposers.Cascade on decline.', stage('entry'),
  f'{SP}:737-886')
E('checked_proposer_cascade', 'Checked-proposer cascade', 'infrastructure', C + 'proposers', 'Cascade',
  'Runs the 22 TIERS in ORDER after the deterministic decline; every proposal must pass literal + output checks.', stage('checked_proposer_cascade'),
  f'{P}:85-160; {SP}:844-856', flag='AGERE_PROPOSERS, AGERE_PROPOSER_SKIP')
E('proposers_execute', 'Checked proposal presenter', 'infrastructure', C + 'proposers', 'execute',
  'Presents the result already executed inside the cascade with proposer calibration disclosed.', stage('execution'),
  f'{P}:354-371; {SP}:897-899')
E('ladder_adapter', 'economical_planner.Ladder (rung adapter)', 'infrastructure', C + 'economical_planner', 'Ladder',
  'Wraps the former cost-ladder rungs as callables the cascade invokes one by one.', 'inside:checked_proposer_cascade',
  f'{EP}:312-618; {P}:105-122')
E('execute_contract', 'Typed request executor', 'infrastructure', C + 'shared_planner', 'execute_contract',
  'Executes compiled typed requests (grammar / shapes / cache / typed interpreters); rejects unresolved items.', stage('execution'),
  f'{SP}:935-1092')
E('request_program_execute', 'Request program executor', 'infrastructure', C + 'request_program', 'execute',
  'Executes multi-node request programs (one compiled request per node).', stage('execution'), 'request_program.py:222; ' + f'{SP}:900-902')
E('general_analysis_execute', 'General (staged SQL) analysis executor', 'infrastructure', C + 'general_analysis', 'execute',
  'Executes staged model-SQL analyses from the hosted interpreter rescue path; runs general_analysis.checks.', stage('execution'),
  'general_analysis.py:676; ' + f'{SP}:903-906')
E('analysis_graph_execute', 'Analysis graph executor', 'infrastructure', C + 'analysis_graph', 'execute',
  'Read-only execution of every graph node (engines, set ops, sql); attaches answer_checks + benchmarks.', 'inside:checked_proposer_cascade',
  f'{AG}:1365-2024; {P}:260')
E('analysis_graph_validate', 'Analysis graph validator', 'check', C + 'analysis_graph', 'validate',
  'Validates node operations, inputs and parameters before any graph executes.', 'inside:ladder_adapter',
  f'{AG}:806-1116')
E('sql_query_execute', 'Read-only semantic SQL executor (authorizer)', 'check', C + 'sql_query', 'execute',
  'Executes every SQL node read-only under a sqlite authorizer with row/time limits.', 'inside:analysis_graph_execute',
  'sql_query.py:44-48')
E('answer_format_present', 'Answer presentation', 'infrastructure', C + 'answer_format', 'present',
  'Formats money, org names, honest-unknown and outside-coverage notes on every answer.', stage('presentation'),
  f'answer_format.py:44; {SP}:909-910')
E('answer_log_record', 'Answer log', 'infrastructure', C + 'answer_log', 'record',
  'Logs each compose result/error with seconds.', stage('post_answer_learning'), 'conversation.py:691')
E('question_input_validate', 'Question input validation', 'check', C + 'question_input', 'validate_question',
  'Rejects empty/oversize questions before planning.', stage('entry'), f'question_input.py:24; {SP}:738')
E('model_roles_report', 'Stack.model_roles report', 'infrastructure', C + 'stack_runtime', 'Stack.model_roles',
  'Metadata of active model roles. STALE: still says qwen3_4b_active False and lists paraphrase reuse as retired; does not report proposers.NOT_PROPOSERS.',
  'not_on_path', 'stack_runtime.py:106-121; proposers.py:59-69')
E('budget_ledger', 'Budget ledger', 'infrastructure', A + 'gateway', 'BudgetLedger',
  'Content-addressed paid-call ledger/cache used by every hosted tier.', 'inside:checked_proposer_cascade', 'gateway.py:18; proposers.py:95-100')
E('hosted_model_client', 'Hosted Claude client (HostedModel)', 'infrastructure', C + 'nl2sql', 'HostedModel',
  'Anthropic Messages client with cache_control split used by hosted tiers.', 'inside:hosted_typed_interpreter', 'nl2sql.py:307-344', cost=OPUS)
E('context_student_send', 'Provider transport (context_student.send)', 'infrastructure', C + 'context_student', 'send',
  'Ledgered provider send for Perplexity decisions and hosted calls; raises RequestSizeLimitError.', 'inside:typed_decider',
  'context_student.py:42-105', cost='paid:pplx-decider-v1.1-27b')
E('persistent_gateway', 'PersistentGateway (Jev/Claude/Perplexity endpoints)', 'infrastructure', A + 'gateway', 'PersistentGateway',
  'Typed classify() for Jev, Perplexity Decisions and Claude; ENDPOINTS also lists perplexity_agent (v1/responses).', 'inside:jev_typesafe_review',
  'gateway.py:146-152', cost='paid:jev-1.13.0')
E('gateway_jev_claude_cascade', 'PersistentGateway.cascade (Jev then Claude)', 'evidence_tool', A + 'gateway', 'PersistentGateway.cascade',
  'Typed-specialist-then-frontier cascade; no caller in clinical_core.', 'not_on_path', 'gateway.py:191-198', cost='paid:jev-1.13.0 + claude-opus-5-5')

# ---------------------------------------------------------------- pre-cascade deterministic tiers
E('data_domains_gate', 'Received-data-domain gate', 'check', C + 'data_domains', 'check',
  'Before planning, declines a question whose data domain was never received (nothing could answer it).', stage('data_domains_gate'),
  f'data_domains.py:38; {SP}:890-894')
E('exact_contract_cache', 'Exact-contract cache (shared_requests)', 'proposer', C + 'shared_planner', 'plan',
  'Reuses a verified interpretation for the same question/source/policy (stable_key carry-forward across releases).', stage('exact_contract_cache'),
  f'{SP}:745-822 (question-plans.sqlite3 shared_requests)', calib='verified interpretations only (execution_accepted)')
E('correction_block', 'Saved-correction block', 'check', C + 'learning', 'correction_context',
  'An unresolved saved correction (incl. economics/healthcare learning context) blocks cache reuse and the grammar; forces model interpretation.', stage('exact_contract_cache'),
  f'learning.py:13-39; {SP}:773-774,469-473')
E('superseded_definition_check', 'Clinical definition supersession check', 'check', C + 'clinical_definitions', 'superseded',
  'Invalidates a cached plan when a clinical definition decided by Mike (2026-10-21) supersedes it.', stage('exact_contract_cache'),
  f'clinical_definitions.py:85; {SP}:784-785')
E('learned_request_shapes', 'Learned request shapes', 'proposer', C + 'request_shapes', 'lookup',
  'Instantiates a checked request shape (same family, new literals) learned from earlier verified answers.', stage('learned_shapes'),
  f'request_shapes.py:160; {SP}:829-833')
E('learned_analysis_shapes', 'Learned general-analysis shapes', 'proposer', C + 'general_analysis', 'lookup_shape',
  'Reuses a verified staged-analysis shape with swapped literals.', stage('learned_shapes'), f'general_analysis.py:908; {SP}:830-833')
E('compositional_grammar', 'Compositional grammar (request_language.parse)', 'proposer', C + 'request_language', 'parse',
  'Free deterministic parser; must consume the whole question, then request_plan compiles it to SQL.', stage('compositional_grammar'),
  f'request_language.py:1135; {SP}:476-487', calib='fully consumed grammar; BUILD_LOG step 0: declined 20/20 definition-heavy dev questions')
E('compositional_grammar_program', 'Compositional grammar programs', 'proposer', C + 'request_language', 'parse_program',
  'Multi-part question programs from the grammar, prepared by request_program.', stage('compositional_grammar'), f'request_language.py:1139; {SP}:476-481')
E('request_compiler', 'Typed request compiler (request_plan.compile_request)', 'check', C + 'request_plan', 'compile_request',
  'Compiles every typed request to SQL with quote binding; also re-compiles proposals against the original question.', 'inside:compositional_grammar',
  f'request_plan.py:564; proposers.py:345-351')
E('sealed_contract_check', 'Sealed contract assertion', 'check', C + 'request_plan', 'assert_sealed',
  'Rejects a contract that did not come from this process compiler.', 'inside:execute_contract', 'request_plan.py:603')
E('procedural_memory_select', 'Procedural memory (lessons)', 'learning', C + 'procedural_memory', 'select',
  'Selects probed lessons; their ids key the cache and GuidedModel injects them into hosted interpretation.', stage('exact_contract_cache'),
  f'procedural_memory.py:170,238; {SP}:745-747,448-451')

# ---------------------------------------------------------------- the 22 cascade tiers
T = [
 ('exact_local_plan', 'Exact authored local plans + recipes', 'proposer', 'economical_planner', 'local_plan', 31, 286,
  'Exact normalized-question match to authored recipes / registered examples (evidence portfolio, domain review, program documents, measures).', 'free', 'exact authored plans and recipes', None),
 ('specialist_families_early', 'DQ/VBC specialist families (stated definition)', 'proposer', 'economical_planner', '_specialist_families', 32, 287,
  'Code-rendered SQL for data-quality and VBC metric families when the question states its own definition.', 'free', 'DQ/VBC certified, bounds 2.45% / 2.5%', 'AGERE_DQ_FAMILIES, AGERE_VBC_FAMILIES'),
 ('cohort_family_early', 'Cohort families (stated definition)', 'proposer', 'economical_planner', '_cohort_family', 33, 288,
  'Population-health patient-list families (follow-up gap, therapy gap, utilization, screening, PDC) rendered to SQL.', 'free (Haiku slot filler only when slots missing and live)', 'certified 0.71, 752 cases / 0 errors', 'AGERE_COHORT_FAMILIES'),
 ('undelivered_input', 'Undelivered-input gate', 'check', 'delivered_inputs', 'undelivered', 34, 289,
  'Declines (held) when the definition depends on a field this workspace does not deliver.', 'free', 'deterministic delivered-field check; declines only', 'AGERE_UNDELIVERED_GATE'),
 ('paraphrase_reuse', 'Paraphrase reuse', 'proposer', 'paraphrase', 'find', 35, 189,
  'Reuses a verified interpretation of a slot-identical rewording, recompiled against its own wording.', 'free (MiniLM)', 'verified interpretation of a slot-identical rewording; MIN_SIMILARITY 0.65 secondary', None),
 ('semantic_cache', 'Semantic cache (meaning cache)', 'learning', 'semantic_cache', 'lookup', 36, 290,
  'A paraphrase of a verified question with identical slots reuses its verified SQL (re-linted).', 'free (MiniLM)', 'certified, 0/94 test errors; SIMILARITY 0.85 fallback or LTT value', 'AGERE_SEMANTIC_CACHE'),
 ('learned_verified_sql', 'Learned verified SQL', 'learning', 'learning', 'lookup', 37, 291,
  'Exact question previously answered through the verified hosted ladder reuses its verified query.', 'free', 'verified hosted answers, n=3, uncalibrated', 'AGERE_LEARNED'),
 ('specialist_families_late', 'DQ/VBC specialist families (convention defaults)', 'proposer', 'economical_planner', '_specialist_families', 38, 292,
  'Plain questions a DQ/VBC family represents with recorded convention defaults.', 'free', 'DQ/VBC families with recorded convention defaults', 'AGERE_DQ_FAMILIES, AGERE_VBC_FAMILIES'),
 ('cohort_family_late', 'Cohort families (remaining)', 'proposer', 'economical_planner', '_cohort_family', 39, 293,
  'Remaining cohort-family questions.', 'free (Haiku slot filler only when slots missing and live)', 'certified 0.71', 'AGERE_COHORT_FAMILIES'),
 ('local_normalizer', 'Local normalizer (Qwen3-4B LoRA)', 'local_model', 'local_normalizer', 'propose', 40, 194,
  'Rewrites the question into parser wording; guard keeps numbers/years/negation, then the grammar must consume it.', 'free (Qwen3-4B LoRA)', 'rewording guarded, then fully consumed grammar', 'AGERE_LOCAL_NORMALIZER, AGERE_NORMALIZER_ADAPTER'),
 ('qwen_rewrite_compiler', 'Qwen rewrite -> local compiler', 'local_model', 'nl2sql', 'canonicalize', 41, 294,
  'Qwen3-4B rewrites into canonical phrasing; local compiler must parse it and rewrite_dropped must pass.', 'free (Qwen3-4B)', 'dropped a statin constraint once (447 patients); rewrite_dropped + literal check', 'AGERE_LOCAL_MODEL'),
 ('bottled_sql', 'Bottled SQL (Qwen3-4B LoRA, k-agree)', 'local_model', 'bottled', 'answer', 42, 295,
  'Fine-tuned local model writes k queries; kept when greedy passes lint+sanity and decodes agree.', 'free (Qwen3-4B LoRA, k-agree)', 'distilled from verified answers; lint + sanity + agreement', 'AGERE_BOTTLED (cascade default on; bottled.DEFAULT_ENABLED=off elsewhere), AGERE_BOTTLED_K, AGERE_BOTTLED_AGREE, AGERE_BOTTLED_ADAPTER'),
 ('qwen_typed_interpreter', 'Qwen typed interpreter (isolated worker)', 'local_model', 'shared_planner', 'isolated_local', 43, 198,
  'Local Qwen interprets into a typed contract in an isolated worker; local audit + recompile + literal check. Honors saved cohorts.', 'free (Qwen3-4B, isolated worker)', 'unqualified; compiler + literal check', 'AGERE_LOCAL_TYPED, AGERE_LOCAL_INTERPRET_SECONDS'),
 ('typed_decider', 'Typed decider (MiniLM candidates + Perplexity)', 'proposer', 'economical_planner', '_typed_decider', 44, 297,
  'MiniLM retrieves curated recipes; Perplexity Decisions picks one at the 0.95 threshold.', 'paid:pplx-decider-v1.1-27b (~$0.000006/call)', 'curated unfiltered recipes only, 0.95 threshold', 'AGERE_TYPED_DECIDER_FIRST, --allow-live-stack'),
 ('hosted_typed_interpreter', 'Hosted typed interpreter (Opus)', 'proposer', 'shared_planner', 'checked_interpret', 45, 214,
  'Opus interprets into typed request/program/analysis with discovery, audit, quote binding; partial with named gap held. Honors saved cohorts.', OPUS + ' (~$0.27/question)', '20-question dev set; compiler + quote binding + audit', 'AGERE_SHARED_INTERPRETER_MODEL, --allow-live-stack'),
 ('hosted_verified_sql', 'Hosted verified SQL ladder', 'proposer', 'nl2sql', 'verified_answer', 46, 298,
  'Haiku writes SQL, Sonnet confirms, Opus/Perplexity judge disagreements; verification_v2 + oracles decide.', 'paid:claude-haiku-5-5 -> claude-sonnet-5-5 -> claude-opus-5-5 + pplx-decider', 'two-model agreement + oracles; uncalibrated', 'AGERE_HOSTED_SQL, AGERE_PRIMARY_MODEL, AGERE_SECOND_MODEL, AGERE_JUDGE_MODEL, AGERE_DECIDER_JUDGE, AGERE_VERIFICATION_V2, AGERE_PARTITION_ORACLE'),
 ('frontier_graph_planner', 'Frontier graph planner (Opus)', 'proposer', 'graph_planner', 'propose', 47, 299,
  'Opus proposes a typed graph over the full capability catalog; Ladder.frontier_planner also holds the free exact-question plan cache (gated as paid here).', OPUS, 'full capability catalog; uncalibrated', 'AGERE_PLAN_CACHE, --allow-live-stack'),
 ('hybrid_analysts', 'Hybrid 3-analyst core', 'proposer', 'together', '_run_hybrid', 48, 307,
  'Three independent Opus analysts query the semantic layer, vote, escalate splits; executed SQL literal-checked.', OPUS + ' x3 + vote (~$0.14/question)', '18/20 dev, 7/10 held-out, pilot 75/100', 'AGERE_HYBRID, AGERE_HYBRID_MODEL, AGERE_HYBRID_ESCALATION_MODEL'),
 ('local_compiler', 'Local question compiler', 'proposer', 'local_compiler', 'compile', 49, 301,
  'Free regex/rule compiler to graphs incl. 25+ engine ops; proposes only (failed calibration).', 'free', 'FAILED calibration: held-out 19/49 wrong; proposes only', 'AGERE_COMPILER_RULES'),
 ('metric_router', 'Governed metric router (Haiku)', 'proposer', 'metric_router', 'route', 50, 302,
  'One Haiku tool call picks a governed metric and fills typed slots; template computes deterministically.', HAIKU, 'FAILED calibration: held-out 13/13 wrong; proposes only', 'AGERE_METRIC_ROUTER, AGERE_FREEFORM_POLICY'),
 ('operator_router', 'Operator router (Haiku)', 'proposer', 'operator_router', 'route', 51, 303,
  'One Haiku call picks an analytic engine and fills its typed parameters (learned router may pre-choose).', HAIKU, 'FAILED calibration: held-out 19/19 wrong; proposes only', 'AGERE_ROUTER'),
 ('local_free_sql', 'Free-form local Qwen SQL', 'local_model', 'nl2sql', 'answer', 52, 304,
  'Qwen3-4B writes free-form SQL; proposes only.', 'free (Qwen3-4B)', 'FAILED calibration: precision too low; proposes only', 'AGERE_LOCAL_SQL'),
]
for tid, name, fam, mod, sym, tl, ml, role, cost, calib, flag in T:
    paid = cost.startswith('paid') or tid in ('metric_router', 'operator_router', 'frontier_graph_planner', 'hybrid_analysts', 'hosted_typed_interpreter', 'hosted_verified_sql', 'typed_decider')
    E(tid, name, fam, C + mod, sym, role, tier(tid), f'{P}:{tl} (TIERS), {P}:{ml} (tier method)' + ('; PAID set proposers.py:55 (skipped unless allow_live)' if paid else ''), cost, calib, flag)

# ---------------------------------------------------------------- components inside tiers
E('dq_families', 'Data-quality / feed families', 'proposer', C + 'dq_families', 'plan',
  'File counts, missing periods, duplicate charts, renumbering, corrected results etc. rendered to SQL.', 'inside:specialist_families_early',
  f'dq_families.py:431; {EP}:261-293', calib='certified bound 2.45%', flag='AGERE_DQ_FAMILIES, AGERE_DQ_RESTATED_FROM_REBUILT_LINEAGE')
E('vbc_families', 'VBC metric families', 'proposer', C + 'vbc_families', 'plan',
  'Claims finance, PMPM, member months, attribution, readmissions, appointments etc. rendered to SQL.', 'inside:specialist_families_early',
  f'vbc_families.py:663; {EP}:261-293', calib='certified bound 2.5%', flag='AGERE_VBC_FAMILIES')
E('cohort_families_module', 'Cohort family renderer', 'proposer', C + 'cohort_families', 'plan',
  'Five cohort families; reuse() of verified family answers.', 'inside:cohort_family_early', f'cohort_families.py:1110; {EP}:223-259',
  cost='free; paid:claude-haiku-5-5 slot filler when live and slots missing', calib='certified 0.71', flag='AGERE_COHORT_FAMILIES')
E('bottled_cohort_slots', 'Narrow bottled cohort slots (Qwen3-4B LoRA)', 'local_model', C + 'bottled', 'cohort_slots',
  'Free slot filler before the Haiku filler inside cohort families; k decodes must agree.', 'inside:cohort_families_module',
  f'bottled.py:286-294,597; bottled_cohort_hook.py:18-30; {EP}:246-247', calib='81.7% coverage, 0.927 exact-spec precision (held-out)', flag='AGERE_BOTTLED_COHORT (NARROW_DEFAULT on)')
E('compiler_rules', 'Compiler rules (config/agere/compiler_rules)', 'knowledge', C + 'compiler_rules', 'active_rules',
  'Accepted phrase rules extending the local compiler.', 'inside:local_compiler', 'compiler_rules.py:99-101; config/agere/compiler_rules (8 files)', flag='AGERE_COMPILER_RULES')
E('grounding', 'Predicate grounding', 'check', C + 'grounding', 'prepare',
  'Binds cohort predicates to observed fields/values; GroundingError reports unsupported predicates.', 'inside:local_compiler', f'grounding.py:119; {AG}:1564-1574')
E('concept_resolver', 'Concept / terminology resolver', 'knowledge', C + 'concepts', 'resolve',
  'Resolves conditions, measurements and encounters to codes/labels; UltraBERT terminology fallback.', 'inside:grounding', 'concepts.py:452; grounding.py:19')
E('rewrite_dropped_check', 'Rewrite-dropped check', 'check', C + 'economical_planner', 'rewrite_dropped',
  'Rejects a Qwen rewrite that drops anything the compiler recognised in the original.', 'inside:qwen_rewrite_compiler', f'{EP}:30-44')
E('meaning_drift_check', 'Meaning drift check', 'check', C + 'nl2sql', 'meaning_drift',
  'Each meaning class present in the question must be present in the rewrite.', 'inside:qwen_rewrite_compiler', 'nl2sql.py:407-438')
E('local_normalizer_guard', 'Normalizer guard', 'check', C + 'local_normalizer', 'guard',
  'Keeps numbers, years, negation and qualifiers of the client wording.', 'inside:local_normalizer', 'local_normalizer.py:70; shared_planner.py:701')
E('paraphrase_same_meaning', 'Paraphrase meaning guard', 'check', C + 'paraphrase', 'same_meaning',
  'Deterministic signature guard deciding paraphrase equivalence.', 'inside:paraphrase_reuse', 'paraphrase.py:62')
E('semantic_cache_slots', 'Semantic cache slot equality', 'check', C + 'semantic_cache', 'same_slots',
  'Critical slots (numbers, months, concepts, logic) must match before reuse.', 'inside:semantic_cache', 'semantic_cache.py:137')
E('verify_lint', 'SQL lint', 'check', C + 'verify', 'lint',
  'Streams/fields/concepts in reused, bottled and hosted SQL must exist in the source profile.', 'inside:ladder_adapter', f'verify.py:27; {EP}:412-417')
E('verify_sanity', 'SQL sanity probe', 'check', C + 'verify', 'sanity',
  'Probe-based sanity of bottled/hosted SQL results.', 'inside:bottled_sql', 'verify.py:69; bottled.py:130')
E('verify_metric_lint', 'Metric lint / heal', 'check', C + 'verify', 'metric_lint',
  'Metric-definition lint with one repair pass on hosted SQL.', 'inside:hosted_verified_sql', 'verify.py:105-123', flag='AGERE_METRIC_LINT')
E('verification_v2', 'Verification v2 (N-version + clustering)', 'check', C + 'verification_v2', 'decide',
  'Independent candidates (hosted, compiler, local model) clustered; agreement decides; dissent blocks capture.', 'inside:hosted_verified_sql',
  f'verification_v2.py:107; {EP}:299-303', flag='AGERE_VERIFICATION_V2, AGERE_COMPILER_DISSENT, AGERE_COMPILER_CANDIDATE, AGERE_LOCAL_CANDIDATE')
E('verification_v2_readings', 'Alternative readings disclosure', 'check', C + 'verification_v2', 'attach_readings',
  'When candidates disagree on definition, both readings and a clarifying question are attached.', 'inside:verification_v2', 'verification_v2.py:160-231')
E('sql_oracles', 'SQL oracles (partition, perturbation, shape, empty)', 'check', C + 'oracles', 'check',
  'Metamorphic/partition invariants over SQL and answers.', 'inside:verification_v2', f'oracles.py:113,210,277,290,304; {EP}:303', flag='AGERE_PARTITION_ORACLE')
E('perplexity_ab_decider', 'Perplexity A/B decider (hosted SQL judge)', 'evidence_tool', C + 'nl2sql', 'Decider',
  'Perplexity Decisions judges between two SQL candidates on disagreement.', 'inside:hosted_verified_sql', f'nl2sql.py:858; {EP}:297',
  cost='paid:pplx-decider-v1.1-27b', flag='AGERE_DECIDER_JUDGE')
E('missing_claim_check', 'Missing-data claim check', 'check', C + 'nl2sql', 'missing_claim_check',
  'Verifies a model claim that data is missing by probing the source.', 'inside:hosted_verified_sql', 'nl2sql.py:794-857')
E('harvested_examples', 'Harvested few-shot examples', 'retrieval', C + 'nl2sql', 'harvested_examples',
  'Retrieves similar verified question/SQL pairs as shots for hosted SQL.', 'inside:hosted_verified_sql', 'nl2sql.py:480-512', flag='AGERE_HARVEST_SHOTS, AGERE_HARVEST_SHOTS_RANK')
E('metric_pins', 'Metric context pins', 'knowledge', C + 'nl2sql', 'metric_context',
  'Pins governed metric definitions into hosted SQL prompts.', 'inside:hosted_verified_sql', 'nl2sql.py:780-793', flag='AGERE_METRIC_PINS')
E('typed_decider_recipes', 'Curated typed-decider recipes', 'knowledge', C + 'economical_planner', 'recipes',
  'Six full-population observation recipes (warehouse interpretation, source completeness, renal, feed, runout, prerequisites).', 'inside:typed_decider', f'{EP}:45-64')
E('typed_decider_candidates', 'Compact MiniLM candidate retrieval', 'retrieval', C + 'economical_planner', 'candidates',
  'MiniLM ranks curated recipes for the Perplexity decision.', 'inside:typed_decider', f'{EP}:125-162')
E('escalation_reason', 'Escalation reason (decider eligibility)', 'check', C + 'economical_planner', 'escalation_reason',
  'Questions with filters skip the typed decider.', 'inside:typed_decider', f'{EP}:106-124')
E('exact_question_plan_cache', 'Exact-question plan cache (frontier)', 'learning', C + 'economical_planner', 'Ladder.frontier_planner',
  'Free cache of earlier frontier/decider graphs keyed by question+policy+snapshot; only reachable via the PAID frontier tier.', 'inside:frontier_graph_planner',
  f'{EP}:562-580; {P}:55,299', flag='AGERE_PLAN_CACHE')
E('graph_planner_contracts', 'Capability contracts (graph_planner.CONTRACTS)', 'knowledge', C + 'graph_planner', 'CONTRACTS',
  '72 operation contracts offered to the frontier planner and (62 of them) to the hosted interpreter menu.', 'inside:frontier_graph_planner', 'graph_planner.py:24; shared_planner.py:315-318')
E('capability_registry_product', 'PRODUCT engine registry', 'knowledge', C + 'capability_registry', 'PRODUCT',
  '29 product engines with population/view/use; answer-shape contract (NATIVE_SCALARS).', 'inside:graph_planner_contracts', 'capability_registry.py:12-170')
E('hybrid_live_answer', 'Hybrid analyst runs + escalation', 'proposer', A + 'hybrid.live', 'answer',
  'RUNS=3 analyst tool loops over versioned docs; tie-break escalation run on a split.', 'inside:hybrid_analysts', 'hybrid/live.py:27-29,197; together.py:295', cost=OPUS + ' x3', flag='AGERE_HYBRID_MODEL, AGERE_HYBRID_ESCALATION_MODEL')
E('hybrid_vote', 'Hybrid vote', 'check', A + 'hybrid.vote', 'decide', 'Canonicalizes and votes analyst answers.', 'inside:hybrid_live_answer', 'hybrid/vote.py:40')
E('hybrid_contract', 'Hybrid answer contract', 'check', A + 'hybrid.contract', 'validate',
  'Analyst answer contract (answer_type, grain, population, window...) and readings/missing/clarify.', 'inside:hybrid_live_answer', 'hybrid/contract.py:43-104')
E('hybrid_execute_checks', 'Hybrid execution checks', 'check', A + 'hybrid.execute', 'checks', 'Runs analyst SQL read-only and checks answers.', 'inside:hybrid_live_answer', 'hybrid/execute.py:57,152')
E('hybrid_docs_catalog', 'Hybrid docs catalog (incl. verified example queries)', 'knowledge', A + 'hybrid.catalog', 'table_doc',
  'Versioned per-table docs + catalog index + verified example queries (L6 library) read by analysts.', 'inside:hybrid_live_answer',
  'hybrid/catalog.py:174; hybrid/docs (63 files incl. section_verified_example_queries_question_sql_ov.md); docs/agere/LAYER_LOG.md:116-131')
E('learned_router', 'Learned router (MiniLM logistic)', 'local_model', C + 'learned_router', 'decide',
  'May make the choose decision for the operator router; Ladder.engine_lean is only called by legacy_plan, not the cascade.', 'inside:operator_router',
  f'learned_router.py:118-132; operator_router.py:235-250; {EP}:172-186,627', calib='12.7% wrong as a router', flag='AGERE_LEARNED_ROUTER, AGERE_ENGINE_LEAN')
E('metric_families_vocabulary', 'Metric family vocabulary', 'metric_or_measure', C + 'metric_families', 'vocabulary',
  'Atom kinds, period presets, groups for metric_router slots.', 'inside:metric_router', 'metric_families.py:28; config/agere/metric_vocabulary.json')
E('gate_policy', 'Gate policy (certified confidence)', 'check', C + 'gate_policy', 'assess',
  'Certificate-based confidence labels per route; only metric_router (and answer_guard) call it.', 'inside:metric_router', 'gate_policy.py:164; metric_router.py:610,667', flag='AGERE_CONFIDENCE_RULE')
E('calibrated_thresholds', 'Calibrated thresholds (LTT values)', 'check', C + 'calibrated_thresholds', 'threshold',
  'Learn-then-Test fitted thresholds read by semantic cache, learned router, nl2sql, answer guard.', 'inside:semantic_cache',
  'calibrated_thresholds.py:38-56; semantic_cache.py:185', flag='AGERE_CALIBRATED_THRESHOLDS, AGERE_THRESHOLDS_FILE')
E('cascade_certificates', 'Certified cascade (LTT certificates)', 'check', C + 'cascade', 'certify_route',
  'Route/tier certificates from config/agere/cascade_thresholds.json; called by together (legacy) and therapy barriers (build).', 'not_on_path',
  'cascade.py:188,273; together.py:216-224; therapy_barriers.py:754-767', calib='LTT alpha 0.05 delta 0.10', flag='AGERE_CASCADE, AGERE_CASCADE_DEFER_UNCERTIFIED, AGERE_CASCADE_THRESHOLDS')
E('conformal', 'Conformal prediction library', 'check', C + 'conformal', 'split_conformal',
  'Split/CV+/Mondrian/ACI intervals, Venn-Abers, LTT thresholds; used by forecast engines and the calibration engine.', 'inside:engine_calibration',
  'conformal.py:48-284; analysis_graph.py:339-341')

# typed-interpreter internals
E('hosted_interpreter_audit', 'Semantic review (model audit) of typed plans', 'check', C + 'shared_planner', 'AUDIT_SYSTEM',
  'Second hosted pass compares question vs compiled SQL; equivalence + issues gate acceptance.', 'inside:hosted_typed_interpreter', f'{SP}:191-219; {P}:228-238', cost=OPUS)
E('request_discovery', 'Request discovery (need_discovery probes)', 'retrieval', C + 'request_discovery', 'run',
  'Model-requested value search/probes over received tables before forming the typed request.', 'inside:hosted_typed_interpreter', f'request_discovery.py:92,227,315; {SP}:514-518')
E('schema_slice', 'Planner schema slice', 'retrieval', C + 'shared_planner', 'schema_slice',
  'Lexically selected schemas, observed values, operator contracts, governed measures for the hosted prompt.', 'inside:hosted_typed_interpreter', f'{SP}:289-356')
E('general_analysis_rescue', 'General-analysis rescue (staged model SQL)', 'proposer', C + 'general_analysis', 'prepare',
  'Hosted interpreter may return a staged SQL analysis; prepared (relaxed) and audited.', 'inside:hosted_typed_interpreter', f'general_analysis.py:439; {SP}:559-603', cost=OPUS)
E('general_analysis_checks', 'General-analysis checks + uncovered words', 'check', C + 'general_analysis', 'checks',
  'Result checks and uncovered-quote detection on staged analyses.', 'inside:general_analysis_execute', 'general_analysis.py:399,612')
E('request_program_audit', 'Request program audit validation', 'check', C + 'request_program', 'validate_audit', 'Per-node audit equivalence.', 'inside:hosted_typed_interpreter', 'request_program.py:182; proposers.py:230-231')
E('guided_model', 'Procedural-memory guided model', 'learning', C + 'procedural_memory', 'GuidedModel',
  'Wraps the hosted model with selected lessons; outcomes recorded per use.', 'inside:hosted_typed_interpreter', f'procedural_memory.py:238; {SP}:451-462')
E('planner_catalog', 'Planner catalog serializer', 'infrastructure', C + 'planner_catalog', 'serialize', 'Factorized catalog serialization for planner prompts.', 'inside:frontier_graph_planner', 'planner_catalog.py:56; economical_planner.py:163-166')

# checks on every graph proposal
E('literal_check_missing', 'Literal-question check', 'check', C + 'literal_check', 'missing',
  'Every number, month, negation, rate, average, superlative and relative time in the question must appear in the executable plan.', 'inside:checked_proposer_cascade',
  f'literal_check.py:84; {P}:185,257,327')
E('literal_check_shape', 'Requested-output check', 'check', C + 'literal_check', 'shape_misfit',
  'Executed answer must have the requested shape (count/value/patient_set/table).', 'inside:checked_proposer_cascade', f'literal_check.py:119; {P}:268,329')
E('answer_shape_final', 'Answer shape reshaper (final)', 'check', C + 'answer_shape', 'final',
  'Reshapes unambiguous outputs before the requested-output check.', 'inside:checked_proposer_cascade', f'answer_shape.py:484; {P}:263-267', flag='AGERE_ANSWER_FIT')
E('answer_shape_review', 'Answer-fit review with re-plan', 'check', C + 'answer_shape', 'review',
  'Withholds wrong-shaped answers and can re-plan (paid); only called from the legacy compose path.', 'not_on_path', 'answer_shape.py:427-483; stack_runtime.py:129+', flag='AGERE_FIT_REPLAN, AGERE_GUARD_POLICY')
E('answer_checks', 'Answer checks (FMEA QC controls)', 'check', C + 'answer_checks', 'annotate',
  'Units, denominators, versions, unused numbers, parts, clock, rate arithmetic, metric findings; warnings only.', 'inside:analysis_graph_execute',
  f'answer_checks.py:357; {AG}:1983', flag='AGERE_METRIC_LINT')
E('benchmark_references', 'Benchmark references on answers', 'knowledge', C + 'analysis_graph', 'benchmark_references',
  'Attaches external benchmark ranges (config/agere/benchmarks.json) to engine answers.', 'inside:analysis_graph_execute', f'{AG}:159,1542,1988; benchmarks.py:80')

# legacy guards / routes not on path
E('answer_guard_suspicion', 'Answer guard (raw-catalog probe + zero contradictions)', 'check', C + 'answer_guard', 'zero_contradictions',
  'Full-catalog probe for zeros/empties and suspicious answers; only reachable from legacy compose and the routers.', 'not_on_path',
  'answer_guard.py:156,393,558; stack_runtime.py:129', flag='AGERE_ANSWER_GUARD, AGERE_GUARD_POLICY')
E('answer_guard_cross_check', 'Answer guard cross-check (Sonnet + Opus judge)', 'check', C + 'answer_guard', '_review_decline',
  'Re-plans uncertified answers with a second model; off.', 'not_on_path', 'answer_guard.py:29-35,653; proposers.py:66; docs/agere/BUILD_LOG.md (step 1x: 4/22 for $3.19)',
  cost=SONNET + ' + ' + OPUS, calib='4/22 caught for $3.19', flag='AGERE_CROSS_CHECK, AGERE_CROSS_CHECK_BUDGET_S')
E('answer_guard_dense_catalog', 'Dense catalog retrieval (UltraBERT RRF)', 'retrieval', C + 'answer_guard', 'dense_encoder',
  'Second-encoder catalog retrieval fused with lexical for the guard probe.', 'inside:answer_guard_suspicion', 'answer_guard.py:334-392', flag='AGERE_CATALOG_DENSE, AGERE_SECOND_ENCODER')
E('unchecked_zero_guard', 'Unchecked-zero guard', 'check', C + 'together', 'decide',
  'Withholds an analyst-unconfirmed 0/empty; lives in the legacy together decision.', 'not_on_path', 'together.py:519,583-592; docs/agere/BUILD_LOG.md step 10', flag='AGERE_UNCHECKED_ZERO')
E('legacy_compose_together', 'Together orchestration (legacy)', 'proposer', C + 'together', 'legacy_compose_together',
  'Fast path + parallel analyst + reconcile; compose_together now just calls shared_planner.compose.', 'not_on_path', 'together.py:721-726', cost=HAIKU + ' + ' + OPUS, flag='AGERE_TOGETHER, AGERE_TOGETHER_BUDGET_S, AGERE_DEFER_FAST, AGERE_SPECIALISTS_FIRST')
E('semantic_analyst', 'Single semantic analyst + Opus reconcile', 'proposer', C + 'semantic_analyst', 'analyze',
  'Legacy Haiku analyst tool loop with Opus reconcile.', 'not_on_path', 'semantic_analyst.py:324,377; together.py:321', cost=HAIKU + ' + ' + OPUS, flag='AGERE_SEMANTIC_MODEL, AGERE_RECONCILE_MODEL')
E('legacy_cost_ladder', 'Legacy 18-rung cost ladder', 'proposer', C + 'economical_planner', 'legacy_plan',
  'Former ordered ladder; superseded by the cascade (rungs reused through Ladder).', 'not_on_path', f'{EP}:619-651')
E('scoped_review_compiler', 'Scoped local candidate compiler', 'proposer', C + 'scoped_review_compiler', 'compile_question',
  'Only reachable from Stack._legacy_compose (no callers).', 'not_on_path', 'stack_runtime.py:129-134')
E('stack_planner_propose', 'Stack planner propose (older)', 'proposer', C + 'stack_planner', 'propose', 'Older analytic plan proposer imported by stack_runtime; no active caller on compose.', 'not_on_path', 'stack_planner.py:74; stack_runtime.py:8')
E('intent_router', 'Workspace intent router', 'infrastructure', A + 'intent_router', 'classify',
  'Workspace one-box: deliverable / simulate / question (question -> Workspace.ask -> compose_together -> shared compose, or hybrid-only pipeline).', 'named_only',
  'intent_router.py:22-50; workspace_api.py:253-258; onboarding/workspace.py:568-580')
E('workspace_hybrid_pipeline', 'Workspace hybrid-only pipeline', 'proposer', A + 'onboarding.workspace', 'Workspace',
  "pipeline='hybrid' answers through hybrid.live.answer alone (also fallback when 'full' fails).", 'named_only', 'onboarding/workspace.py:16-18,568-580', cost=OPUS + ' x3')

# ---------------------------------------------------------------- conversation / saved cohorts
E('followup_resolver', 'Deterministic follow-up resolver', 'proposer', C + 'conversation', 'resolve',
  'Rewrites a follow-up into a standalone question from the structured frame of the prior question.', 'inside:conversation_handle_compose', 'conversation.py:145,232,428,756')
E('followup_hosted_resolver', 'Hosted follow-up resolver (Haiku)', 'proposer', C + 'conversation', 'hosted_resolver',
  'Cheap hosted rewrite when the deterministic frame cannot resolve the follow-up.', 'inside:followup_resolver', 'conversation.py:468-490', cost=HAIKU, flag='AGERE_FOLLOWUP_MODEL')
E('saved_cohort_binding', 'Saved-cohort follow-up binding', 'infrastructure', C + 'conversation', 'requires_saved_cohort',
  'Pronoun follow-ups bind the parent patient_ids as population; then only grammar(bound) and the two typed interpreters run.', 'inside:conversation_handle_compose',
  f'conversation.py:367,650,682,739; {P}:57,133')
E('followup_answers', 'Follow-up answer binding store', 'infrastructure', C + 'followup_answers', 'attach', 'Binds work follow-ups to answers.', 'named_only', 'followup_answers.py:48')

# ---------------------------------------------------------------- engines: one entry per analysis_graph operation
MENU = {'accountability_review', 'aggregate', 'analyze', 'appointment_followthrough', 'assertion_review', 'baseline', 'calibration', 'claims_review', 'context', 'context_passes', 'contract_strategy', 'decision_landmark', 'demand', 'diagnostic_delay', 'discover', 'domain_review', 'early_signal', 'evidence_portfolio', 'explain', 'financial_accounts', 'forecast', 'functional_review', 'governed_metric', 'interpretation', 'line_of_therapy', 'longitudinal', 'loop_closure', 'market', 'measure_review', 'medication_identity', 'monitor', 'network', 'observation_process', 'outcome', 'outreach', 'pathway', 'pre_event_state', 'profile', 'program_acquisition', 'program_document', 'program_document_change', 'program_effect', 'protocol_change', 'protocol_review', 'queueing', 'reference_review', 'requirements', 'retrieve', 'risk_adjustment', 'risk_outcomes', 'scheduling', 'sequence', 'source_changes', 'source_state', 'state_review', 'survival', 'target_trial', 'trajectory', 'trial_feasibility', 'uplift', 'vbc', 'worklist'}
LOCAL_COMPILER_OPS = {'aggregate', 'briefing', 'calibration', 'claims_review', 'cohort', 'contract_strategy', 'demand', 'discover', 'early_signal', 'explain', 'knowledge', 'line_of_therapy', 'market', 'monitor', 'network', 'outreach', 'pathway', 'profile', 'program_effect', 'queueing', 'risk_adjustment', 'scheduling', 'sql', 'survival', 'target_trial', 'trajectory', 'trial_feasibility', 'uplift', 'vbc'}
OPS = [  # op, module, symbol, dispatch line, role
 ('query_population', 'sql_query', 'execute', 1553, 'Typed request population (membership SQL) feeding engines.'),
 ('cohort', 'cohorts', 'select', 1564, 'Observed cohort from a predicate specification (three-valued membership).'),
 ('aggregate', 'aggregate', 'execute', 1579, 'Counts/rates/breakdowns over a membership.'),
 ('trajectory', 'trajectory', 'execute', 1584, 'Per-patient lab/vital trends, Kalman forecast, threshold crossing.'),
 ('survival', 'survival', 'execute', 1589, 'Kaplan-Meier, log-rank, Cox time-to-event.'),
 ('uplift', 'uplift', 'execute', 1594, 'Heterogeneous treatment effect, RATE/Qini, targeting.'),
 ('queueing', 'queueing', 'engine_execute', 1599, 'Erlang staffing / bed sizing.'),
 ('briefing', 'briefing_rank', 'serve', 1604, 'Ranked proactive briefing (learning-to-rank).'),
 ('pathway', 'pathway', 'execute', 1607, 'Markov / process-mining care pathways.'),
 ('discover', 'discover', 'execute', 1612, 'Unexpected outcome concentrations (hotspots).'),
 ('vbc', 'vbc', 'execute', 1617, 'Member months, attribution, PMPM, quality measures, settlement under program rulebooks.'),
 ('program_effect', 'program_effect', 'execute', 1622, 'Did a program/drug start work (pooled effect).'),
 ('outreach', 'outreach', 'execute', 1626, 'Capacity-limited outreach worklist by expected value.'),
 ('knowledge', 'vbc_knowledge', 'execute', 1630, 'Cited VBC knowledge base passages (BM25 + MiniLM).'),
 ('early_signal', 'early_signal', 'execute', 1642, 'Matched-filter pre-admission signatures.'),
 ('profile', 'profile', 'execute', 1647, 'Shrunk fair PCP/practice comparison.'),
 ('scheduling', 'scheduling', 'execute', 1652, 'No-show model, overbooking, reminders.'),
 ('calibration', 'calibration', 'execute', 1657, 'Conformal backtest coverage, Brier, ECE of forecasts.'),
 ('monitor', 'monitor', 'execute', 1661, 'SPC charts, feed breaks, claims integrity.'),
 ('claims_review', 'claims_review', 'execute', 1666, 'Payment integrity / revenue leakage in dollars.'),
 ('network', 'network', 'execute', 1671, 'Patient-sharing provider network, leakage, steering.'),
 ('loop_closure', 'loop_closure', 'execute', 1676, 'Open diagnostic loops.'),
 ('diagnostic_delay', 'diagnostic_delay', 'execute', 1676, 'SPADE look-back missed/delayed diagnoses.'),
 ('risk_outcomes', 'risk_outcomes', 'execute', 1676, 'Risk-standardized outcome rates (RSRR, O/E).'),
 ('risk_adjustment', 'risk_adjustment', 'execute', 1682, 'CMS-HCC RAF, coding gaps, recapture, suspects.'),
 ('target_trial', 'target_trial', 'execute', 1687, 'Target trial emulation drug A vs B.'),
 ('line_of_therapy', 'line_of_therapy', 'execute', 1687, 'Lines of therapy, switching, persistence/PDC.'),
 ('trial_feasibility', 'trial_feasibility', 'execute', 1687, 'Eligibility funnel, sites, diversity.'),
 ('contract_strategy', 'contract_strategy', 'execute', 1693, 'Risk contracts as options, track selection, Nash bargaining.'),
 ('market', 'market', 'execute', 1698, 'Practice/PCP/payer tickers.'),
 ('demand', 'demand', 'execute', 1702, 'Volume forecasts with intervals, beds needed.'),
 ('governed_metric', 'request_vocabulary', 'execute', 1707, 'Governed measure computation for a typed request (61 definitions).'),
 ('sql', 'sql_query', 'execute', 1711, 'Read-only SQL node (every compiled typed request becomes one).'),
 ('explain', 'explain', 'execute', 1716, 'Drivers / associations of an outcome.'),
 ('evidence', 'analysis_graph', 'evidence_membership', 1721, 'Membership from indexed narrative evidence clauses.'),
 ('intersect', 'analysis_graph', 'combine', 1723, 'Three-valued set intersection.'),
 ('union', 'analysis_graph', 'combine', 1723, 'Three-valued set union.'),
 ('difference', 'analysis_graph', 'combine', 1723, 'Three-valued set difference.'),
 ('compare', 'analysis_graph', 'compare_populations', 1726, 'Fisher/BH comparison of populations.'),
 ('analyze', 'analytic_registry', 'execute', 1729, 'Registered analytic operators (11 in analytic_registry.CATALOG).'),
 ('retrieve', 'warehouse_retrieval', 'Retriever.search', 1735, 'Hybrid passage retrieval for a membership.'),
 ('forecast', 'warehouse_prediction', 'forecast', 1739, 'Fitted outcome forecast artifact.'),
 ('observation_process', 'graph_observation', 'execute', 1742, 'Observation-process analysis.'),
 ('source_changes', 'source_blocks', 'source_summary', 1746, 'Source record change summary.'),
 ('review', 'stack_runtime', 'Stack.review', 1753, 'Bounded Jev/Perplexity patient evidence review sample (paid).'),
 ('sequence', 'longitudinal', 'sequence', 1762, 'Longitudinal event sequence membership.'),
 ('longitudinal', 'longitudinal', 'context', 1767, 'Longitudinal patient context.'),
 ('appointment_followthrough', 'appointment_graph', 'membership', 1772, 'Appointment follow-through membership.'),
 ('functional_review', 'functional_programs', 'membership', 1777, 'Functional program membership review.'),
 ('domain_review', 'domain_programs', 'membership', 1783, 'Domain program membership review.'),
 ('worklist', 'program_work', 'prepare', 1789, 'Program worklist preparation.'),
 ('state_review', 'clinical_state', 'review', 1824, 'Clinical state review.'),
 ('program_document', 'program_documents', 'execute', 1829, 'Imported program document evaluation.'),
 ('reference_review', 'graph_references', 'execute', 1837, 'Reference review.'),
 ('measure_review', 'measure_composition', 'execute', 1841, 'Documentary measure composition.'),
 ('evidence_portfolio', 'evidence_portfolio', 'execute', 1845, 'Evidence portfolio.'),
 ('program_acquisition', 'program_acquisition', 'prepare', 1849, 'Program acquisition.'),
 ('program_document_change', 'program_document_change', 'execute', 1853, 'Program document change comparison.'),
 ('protocol_change', 'protocol_change', 'execute', 1858, 'Protocol change evaluation.'),
 ('accountability_review', 'program_accountability', 'execute', 1863, 'Program accountability review.'),
 ('financial_accounts', 'financial_accounts', 'execute', 1868, 'Financial accounts review.'),
 ('decision_landmark', 'decision_landmarks', 'execute', 1873, 'Decision landmarks.'),
 ('baseline', 'baseline_evidence', 'execute', 1878, 'Baseline evidence.'),
 ('protocol_review', 'care_protocols', 'execute', 1883, 'Care protocol review.'),
 ('outcome', 'outcomes', 'execute', 1891, 'Outcome contracts.'),
 ('requirements', 'evidence_requirements', 'execute', 1897, 'Evidence requirements.'),
 ('pre_event_state', 'pre_event_state', 'execute', 1905, 'Pre-event state projection.'),
 ('source_state', 'source_state', 'execute', 1915, 'Source state projection.'),
 ('interpretation', 'interpretation_state', 'execute', 1923, 'Interpretation state of a population.'),
 ('assertion_review', 'assertion_reconciliation', 'execute', 1930, 'Assertion reconciliation.'),
 ('context_passes', 'context_passes', 'execute', 1935, 'Context pass bundles.'),
 ('context', 'context_builder', 'execute', 1940, 'Context builder.'),
 ('medication_identity', 'medication_identity', 'execute', 1945, 'Medication identity resolution.'),
]
import importlib as _il
sys.path.insert(0, str(WT))
_cr = (WT / 'clinical_core/agere/clinical/capability_registry.py').read_text().splitlines()
def cr_line(op):
    for i, l in enumerate(_cr, 1):
        if l.startswith(f"    '{op}':") or l.startswith(f"PRODUCT['{op}']"):
            return i
PRODUCT_OPS = {'explain', 'discover', 'trajectory', 'survival', 'vbc', 'program_effect', 'outreach', 'calibration', 'monitor', 'market', 'demand', 'profile', 'pathway', 'briefing', 'scheduling', 'queueing', 'uplift', 'early_signal', 'claims_review', 'knowledge', 'risk_adjustment', 'network', 'contract_strategy', 'target_trial', 'line_of_therapy', 'trial_feasibility', 'loop_closure', 'diagnostic_delay', 'risk_outcomes'}
for op, mod, sym, dl, role in OPS:
    if op in ('sql', 'query_population', 'governed_metric'): wiring = 'inside:compositional_grammar'
    elif op in ('knowledge', 'briefing'): wiring = 'inside:local_compiler'
    elif op == 'cohort': wiring = 'inside:exact_local_plan'
    elif op in ('intersect', 'union', 'difference', 'compare', 'evidence'): wiring = 'inside:frontier_graph_planner'
    elif op == 'review': wiring = 'named_only'
    elif op in MENU: wiring = 'planner_menu'
    else: wiring = 'not_on_path'
    ev = f'{AG}:{dl} (dispatch)'
    if op in PRODUCT_OPS: ev += f'; capability_registry.py:{cr_line(op)} (PRODUCT)'
    if op in MENU: ev += f'; {SP}:317-318 (menu)'
    notes = []
    if op in LOCAL_COMPILER_OPS: notes.append('also emitted by the free local_compiler tier (last, failed calibration)')
    if op in MENU and op not in ('governed_metric',): notes.append('planner_menu = offered only to the PAID hosted typed interpreter; free reach otherwise via local_compiler / registered examples')
    if op == 'governed_metric': notes.append('grammar emits it free (request_language.py:945-954); also on the hosted menu')
    if op in ('knowledge', 'briefing'): notes.append('excluded from the hosted menu (population none); also /api/stack/briefing route')
    if op == 'review': notes.append("excluded from the hosted menu (shared_planner.py:317); requires allow_live")
    full_role = role + (' [' + '; '.join(notes) + ']' if notes else '')
    cost = 'paid:jev-1.13.0 / pplx-decider-v1.1-27b' if op == 'review' else 'free'
    fam = 'metric_or_measure' if op == 'governed_metric' else 'engine'
    flag = 'AGERE_KNOWLEDGE_SYNTHESIS (off), AGERE_KNOWLEDGE_ENCODER' if op == 'knowledge' else None
    calib = 'conformal intervals with backtested coverage (forecast mode)' if op in ('demand', 'trajectory', 'calibration', 'scheduling', 'outreach') else None
    E('engine_' + op, f'Engine op: {op}', fam, C + mod, sym, full_role, wiring, ev, cost, calib, flag)

for sub in ['warehouse_interpretation', 'clinical_timeline', 'source_volume_monitor', 'claims_runout', 'heart_failure_review', 'affordability_review', 'program_rule_review', 'kidney_trajectory', 'medication_evidence', 'source_completeness', 'admission_patterns']:
    E('analyze_' + sub, f'Analytic operator: {sub}', 'engine', C + 'analytic_registry', 'CATALOG',
      f"analytic_registry operator '{sub}' run through the analyze op; also /api/stack/operator.", 'inside:engine_analyze',
      'analytic_registry.py:26 (CATALOG), :220 (execute); service.py:605-609' + ('; economical_planner.py:45-64 (recipe)' if sub in ('warehouse_interpretation', 'source_completeness', 'kidney_trajectory', 'source_volume_monitor', 'claims_runout') else ''))

# ---------------------------------------------------------------- governed metrics / measures / knowledge
E('governed_metric_layer', 'Governed metric layer (config/agere/metrics)', 'metric_or_measure', C + 'metrics', 'library',
  'Typed governed metrics (81 routable incl. measures) with grains, windows, versioning; rendered to SQL.', 'inside:metric_router',
  'metrics.py:29-34,171,361; config/agere/metrics (6 files); metric_router.py:71-87')
E('governed_measure_vocabulary', 'Shared request vocabulary (governed measures)', 'metric_or_measure', C + 'request_vocabulary', 'definitions',
  '61 governed measure definitions + named concepts the grammar and hosted prompt use.', 'inside:compositional_grammar', f'request_vocabulary.py:96,130; {SP}:339-343')
E('measure_library', 'Measure library (HEDIS/Stars/eCQM approximations)', 'metric_or_measure', C + 'measures', 'library',
  '25 measures (BCS, CBP, COL, GSD, KED, PQI, PDC, APP Plus...) with gaps/patients outputs.', 'inside:engine_vbc', 'measures.py:26-56; config/agere/measures (26 files)')
E('program_rulebooks', 'Program rulebooks (config/agere/programs)', 'metric_or_measure', C + 'programs', 'load',
  '14 cited program rulebooks (MSSP tracks, ACO REACH, LEAD, episodes) for settlement and contract strategy.', 'inside:engine_vbc',
  f'programs.py:18-136; capability_registry.py:27; {SP}:64,70')
E('governed_knowledge', 'Governed knowledge payload', 'knowledge', C + 'general_analysis', 'governed_knowledge',
  'Program definitions, settlement programs, reviewed healthcare catalog injected into hosted prompts.', 'inside:hosted_typed_interpreter', f'general_analysis.py:153-190; {SP}:66,350')
E('clinical_definitions', 'Clinical definitions (Mike 2026-10-21)', 'metric_or_measure', C + 'clinical_definitions', 'describe',
  'HFrEF phenotype, current GDMT, tolerance, age, primary-care visit, care-gap living, medical claims definitions.', stage('execution'), f'clinical_definitions.py:7-113; {SP}:981-999')
E('semantic_layer_docs', 'Semantic layer schema docs + source coverage', 'knowledge', C + 'semantic_layer', 'SCHEMA_DOC',
  'Table meanings and source-family coverage exposed in planner prompts.', 'inside:schema_slice', f'semantic_layer.py:84,101; {SP}:298-305')
E('vbc_knowledge_synthesis', 'VBC knowledge synthesis (Haiku)', 'knowledge', C + 'vbc_knowledge', 'synthesize',
  'Optional hosted synthesis over retrieved knowledge entries.', 'inside:engine_knowledge', 'vbc_knowledge.py:376-380', cost=HAIKU, flag='AGERE_KNOWLEDGE_SYNTHESIS (default off)')
E('vbc_knowledge_context_for', 'VBC knowledge context_for', 'knowledge', C + 'vbc_knowledge', 'context_for', 'Question-level knowledge snippets; no caller found.', 'not_on_path', 'vbc_knowledge.py:477')
E('healthcare_source_context', 'Healthcare learning knowledge context', 'knowledge', C + 'healthcare_learning', 'knowledge_context',
  'Reviewed CMS/AHRQ/CDC sources matched to the question in the hosted prompt.', 'inside:hosted_typed_interpreter', f'healthcare_learning.py:220; {SP}:351')
E('domain_question_plans', 'Domain question plans (exact)', 'knowledge', C + 'domain_question_plans', 'local_question', 'Exact domain source-review plans.', 'inside:exact_local_plan', f'domain_question_plans.py:19; {EP}:80-82')
E('risk_adjustment_tables', 'CMS-HCC risk adjustment tables', 'knowledge', C + 'risk_adjustment', 'execute', 'V24/V28 tables, eligible CPT/HCPCS, payment parameters.', 'inside:engine_risk_adjustment', 'config/agere/risk_adjustment (19 files)')

# ---------------------------------------------------------------- retrieval
E('warehouse_retriever', 'Warehouse hybrid retriever (FTS5 + MiniLM + RRF + BGE)', 'retrieval', C + 'warehouse_retrieval', 'Retriever',
  'Passage search used by the retrieve op, Stack.review, /api/stack/search and non-live ask.', 'named_only', 'warehouse_retrieval.py:152-295; stack_runtime.py:18,242-246,302')
E('fts5_index', 'FTS5 BM25 index', 'retrieval', C + 'warehouse_retrieval', 'build', 'Built at warehouse build; lexical half of the retriever.', 'inside:warehouse_retriever', 'warehouse_retrieval.py:61,94')
E('minilm_encoder', 'all-MiniLM-L6-v2 encoder', 'retrieval', 'clinical_core.local_encoders', 'LocalEncoder',
  'Shared local encoder: retriever, paraphrase/semantic cache, typed decider candidates, learned router, schema reranker, knowledge index.', 'inside:warehouse_retriever',
  'warehouse_retrieval.py:84; stack_runtime.py:18-21; local_encoders.py:29')
E('bge_reranker', 'BGE reranker v2-m3', 'retrieval', 'clinical_core.local_encoders', 'LocalReranker', 'Cross-encoder rerank of fused passages.', 'inside:warehouse_retriever', 'warehouse_retrieval.py:168; local_encoders.py:123')
E('schema_reranker', 'Learned schema reranker', 'retrieval', C + 'schema_rerank', 'schema_slice',
  'Learned logistic schema selection for hosted SQL schema slices.', 'inside:hosted_verified_sql', 'schema_rerank.py:453,791; nl2sql.py:211-225', flag='AGERE_SCHEMA_RERANK, AGERE_SCHEMA_RERANK_ENCODER')
E('ultrabert_second_encoder', 'UltraBERT-Embed-150M second encoder', 'retrieval', 'clinical_core.local_encoders', 'second_encoder',
  'Raw-catalog retrieval (answer guard) and terminology fallback (concepts).', 'inside:answer_guard_dense_catalog', 'local_encoders.py:170-372; config/agere/encoders/ultrabert-embed-150m.json', flag='AGERE_SECOND_ENCODER, AGERE_SECOND_ENCODER_PATH')
E('nl2sql_stream_retrieval', 'Stream doc retrieval (nl2sql)', 'retrieval', C + 'nl2sql', 'retrieve_streams', 'Retrieves stream docs for local/hosted SQL prompts.', 'inside:local_free_sql', 'nl2sql.py:187-225')
E('vbc_knowledge_index', 'VBC knowledge index (BM25 + MiniLM)', 'retrieval', C + 'vbc_knowledge', 'index', 'Retrieval over config/agere/knowledge (8 jsonl corpora).', 'inside:engine_knowledge', 'vbc_knowledge.py:210-330')
E('question_context_selection', 'Patient passage/schema selection', 'retrieval', C + 'question_context', 'prepare', 'Patient question context selection (passage_reference / schema_candidate).', 'named_only', 'stack_runtime.py:252-258; service.py:345')
E('granite_hybrid_retrieval', 'Granite R2 BM25 hybrid retrieval', 'retrieval', 'clinical_core.hybrid_retrieval', 'retrieve', 'Research neural retrieval branch (BM25 + Granite cosine RRF).', 'not_on_path', 'hybrid_retrieval.py:72; agere/runtime.py:25,42')

# ---------------------------------------------------------------- local models (outside tiers)
E('qwen3_4b_local_model', 'Qwen3-4B base (LocalModel)', 'local_model', C + 'nl2sql', 'LocalModel',
  'Base local model behind rewrite, free SQL, typed interpreter and verification_v2 local candidate.', 'inside:qwen_rewrite_compiler', 'nl2sql.py:19,277-306')
E('bottled_model', 'Bottled SQL adapter (adapters-v2b)', 'local_model', C + 'bottled', 'BottledModel', 'LoRA adapter distilled from verified ladder answers.', 'inside:bottled_sql', 'bottled.py:19,164-222', flag='AGERE_BOTTLED_ADAPTER')
E('bottled_candidate', 'Bottled local candidate for verification', 'local_model', C + 'bottled', 'candidate', 'Bottled query as an independent verification_v2 candidate.', 'inside:verification_v2', 'bottled.py:251-280', flag='AGERE_LOCAL_CANDIDATE')
E('semantic_student_heads', 'Semantic student heads (lexical / MiniLM)', 'local_model', C + 'semantic_student', 'Student',
  'Calibrated statement-reading heads selectable as providers student_lexical / student_minilm.', 'named_only', 'semantic_student.py:28,213-300; stack_runtime.py:293-296')
E('context_student', 'Context student', 'local_model', C + 'context_student', 'ContextStudent', 'Distilled context reader fitted from verified batches.', 'build_time_only', 'context_student.py:116-225')
E('windowed_student', 'Windowed MiniLM student', 'local_model', C + 'windowed_student', 'read', 'Windowed reader for cohort reading comparisons.', 'named_only', 'windowed_student.py:135')
E('kev_reader', 'Kev 4B local reader', 'local_model', C + 'local_decisions', 'KevMLX', 'Research reader (MLX LoRA); reader comparison role only.', 'named_only', 'local_decisions.py:89-150; reader_comparison.py:18,60', calib='did not meet promotion criteria (component catalog c022)')
E('d1_reader', 'Liquid d1-3B local reader', 'local_model', C + 'local_decisions', 'LiquidD1', 'Research reader; reader comparison role only.', 'named_only', 'local_decisions.py:153; reader_comparison.py:18,60', calib='did not replace default reader (c023)')
E('reader_comparison', 'Same-source reader comparison', 'evidence_tool', C + 'reader_comparison', 'prepare', 'Compares lexical, windowed MiniLM, Kev and d1 readings of the same source.', 'named_only', 'reader_comparison.py:17-194')
E('qwen_local_reader', 'Qwen3-4B local reader', 'local_model', C + 'local_reader', 'run', 'Local clinical reader comparison run.', 'build_time_only', 'local_reader.py:21-61')
E('neural_specialist_branch', 'Granite + BioClinical-ModernBERT + BGE specialist pass', 'local_model', A + 'runtime', 'specialist_pass', 'Neural research branch over notes; release runtime only.', 'build_time_only', 'agere/runtime.py:19-55; neural_stack.py:64-130')
E('therapy_barrier_classifier', 'Therapy-barrier tier-2 classifier (TF-IDF LR)', 'local_model', C + 'therapy_barriers', 'build', 'Build-time tier 2 between rules and Jev/Perplexity.', 'build_time_only', 'therapy_barriers.py:9-35,69-70,928')
E('tensor_train_model', 'Tensor-train model', 'local_model', A + 'tensor_train', None, 'Research comparator.', 'not_on_path', 'clinical_core/agere/tensor_train.py; component catalog c089')
E('outcome_forecast_models', 'Learned outcome forecast models', 'local_model', C + 'outcome_learning', 'fit_rows', 'Fitted outcome models attached for the forecast op.', 'build_time_only', 'outcome_learning.py:113-213; warehouse_prediction.py:238')
E('relation_learning', 'Relation/assertion learner', 'learning', C + 'relation_learning', 'fit', 'Labels with providers then fits assertion/relation heads.', 'build_time_only', 'relation_learning.py:201,236')

# ---------------------------------------------------------------- evidence tools
E('jev_typesafe_review', 'Jev 1.13.0 (TypeSafe) source review', 'evidence_tool', C + 'stack_planner', 'review',
  'Typed patient-scoped evidence review (Stack.review / review op / /api/stack/review); not question interpretation.', 'named_only',
  'stack_planner.py:123-124; stack_runtime.py:299-310; gateway.py:147,152; service.py:696-698', cost='paid:jev-1.13.0', flag='TYPESAFE_API_KEY, --allow-live-stack')
E('perplexity_decisions_review', 'Perplexity Decisions source review', 'evidence_tool', C + 'stack_planner', 'review', 'Same typed review contract with provider perplexity.', 'named_only',
  'stack_planner.py:123-124; gateway.py:149,152', cost='paid:pplx-decider-v1.1-27b', flag='PERPLEXITY_API_KEY, --allow-live-stack')
E('perplexity_agent_search', 'Perplexity Agent API (responses/search)', 'evidence_tool', A + 'gateway', 'PersistentGateway', 'Endpoint registered; only tools/benchmarks/build_benchmarks.py references it.', 'build_time_only',
  'gateway.py:150-151; tools/benchmarks/build_benchmarks.py', cost='paid:perplexity agent (server-chosen)')
E('semantic_statement_reader', 'Semantic statement reader (Perplexity/Jev/Claude)', 'evidence_tool', C + 'semantic_reader', 'read', 'Statement refinement via /api/stack refine (provider chosen by request).', 'named_only', 'semantic_reader.py:173; stack_runtime.py:293-298; service.py:626', cost='paid:jev/perplexity/claude')
E('context_pass_review', 'Specialist context-pass review', 'evidence_tool', C + 'context_passes', 'review', 'Bounded specialist review of context bundles.', 'named_only', 'context_passes.py:230; stack_runtime.py:281-292; service.py:695', cost='paid:jev/perplexity')
E('model_reader_compare', 'Clinical reader comparison (hosted)', 'evidence_tool', C + 'model_reader', 'compare', 'Provider reader comparison harness.', 'build_time_only', 'model_reader.py:106-141', cost='paid:jev/perplexity/claude')
E('therapy_barrier_cascade', 'Therapy-barrier cascade (rules -> classifier -> Jev -> Perplexity)', 'evidence_tool', C + 'therapy_barriers', 'build',
  'Build-time barrier facts read later through general_analysis/semantic layer.', 'build_time_only', 'therapy_barriers.py:1-51,928; cascade.py:61', cost='free (tiers 1-2); paid:jev-1.13.0 / pplx-decider tier 3 when llm=True')
E('note_facts', 'Note fact extraction', 'evidence_tool', C + 'note_facts', 'build', 'Build-time note facts (rules) admitted into the semantic layer.', 'build_time_only', 'note_facts.py:319,548')
E('jev_normalization_check', 'Jev normalization check (tools)', 'evidence_tool', 'tools.normalization.jev_check', None, 'Offline Jev check of normalization dictionaries.', 'build_time_only', 'tools/normalization/jev_check.py; config/agere/normalization', cost='paid:jev-1.13.0')
E('perplexity_definitions_research', 'Perplexity definitions research', 'evidence_tool', None, None, 'Stored Perplexity research for definitions.', 'build_time_only', 'config/agere/definitions-research/perplexity-2026-10-13.json', cost='paid:perplexity (historical)')
E('learning_queue_suggest', 'Learning queue suggestions', 'evidence_tool', C + 'learning_queue', 'suggest', 'Provider suggestions for learning items.', 'named_only', 'learning_queue.py:285', cost='paid:jev/perplexity')

# ---------------------------------------------------------------- learning
E('learned_store', 'Learning store (learning.sqlite3)', 'learning', C + 'learning', 'record_verified', 'Verified pairs keyed by release; feeds learned SQL, semantic cache, families reuse.', 'inside:conversation_handle_compose', 'learning.py:55-112')
E('verified_pair_capture', 'Verified-pair capture (FM-23 after checks)', 'learning', C + 'learning', 'capture_verified', 'Captures pairs only after the answer was built and checked.', 'inside:conversation_handle_compose', 'learning.py:145-183; conversation.py:848-853')
E('verified_pair_quarantine', 'Verified-pair quarantine', 'check', C + 'learning', 'quarantine', 'Quarantines pairs flagged by QC controls.', 'inside:verified_pair_capture', 'learning.py:140-193')
E('correction_record', 'User corrections', 'learning', C + 'learning', 'record_correction', 'POST /api/stack/correct records corrections (with economics/healthcare rule verification).', 'named_only', 'learning.py:194-225; service.py:293-296')
E('request_shape_save', 'Request shape save', 'learning', C + 'request_shapes', 'save', 'Saves checked request shapes after executed answers.', 'inside:execute_contract', f'request_shapes.py:131; {SP}:1083-1090')
E('analysis_shape_save', 'Analysis shape save', 'learning', C + 'general_analysis', 'save_shape', 'Saves verified staged-analysis shapes.', 'inside:general_analysis_execute', 'general_analysis.py:862')
E('economics_learning', 'Economics learning', 'learning', C + 'economics_learning', 'feedback_context', 'Financial lessons/rules feeding correction context.', 'inside:correction_block', 'economics_learning.py:105-156; learning.py:29')
E('healthcare_learning', 'Healthcare domain learning', 'learning', C + 'healthcare_learning', 'feedback_context', 'Healthcare lessons/rules feeding correction context.', 'inside:correction_block', 'healthcare_learning.py:177-240; learning.py:29')
E('learning_loop', 'Learning loop (offline)', 'learning', 'tools.learning_loop.loop', 'run', 'Clusters gaps, regression cases, promotes pairs, retrain decision.', 'build_time_only', 'tools/learning_loop/loop.py:531; learning.py:256-260', flag='AGERE_LEARNING_LOOP_DIR')
E('verified_carry_forward', 'Verified carry-forward', 'learning', A + 'verified_carry_forward', None, 'Replays prior verified pairs on a new build.', 'build_time_only', 'verified_carry_forward.py:3-5,244')
E('semantic_learning', 'Reviewed semantic learning', 'learning', C + 'semantic_learning', 'freeze', 'Train/calibration/validation snapshots of reviewed readings.', 'build_time_only', 'semantic_learning.py:138')
E('question_learning', 'Question-source learning', 'learning', C + 'question_learning', 'prepare', 'Question-source learning candidates.', 'named_only', 'question_learning.py:120')
E('briefing_ltr', 'Briefing learning-to-rank', 'learning', C + 'briefing_rank', 'serve', 'Feedback-trained ranking of briefing findings.', 'inside:engine_briefing', 'briefing_rank.py:411')
E('cascade_calibration_tools', 'Cascade / gate calibration tools', 'learning', 'tools.certify_gates', None, 'Fits LTT certificates and calibrated thresholds.', 'build_time_only', 'tools/calibrate_cascades.py; tools/certify_gates.py; calibrated_thresholds.py:10')
E('bottling_training', 'Bottling training (adapters)', 'learning', None, None, 'Trains bottled SQL / narrow slot adapters.', 'build_time_only', 'tools/bottling; docs/agere/BOTTLING_INTEGRATION.md')
E('local_planner_training', 'Local normalizer dataset/eval', 'learning', None, None, 'Builds the Qwen normalizer LoRA dataset.', 'build_time_only', 'tools/local_planner/build_dataset.py')

# ---------------------------------------------------------------- data / build infrastructure affecting answers
E('semantic_layer_build', 'Semantic layer build', 'infrastructure', C + 'semantic_layer', 'build', 'Builds semantic.sqlite3 every SQL tier reads.', 'build_time_only', 'semantic_layer.py:16,886')
E('workspace_stack', 'Workspace stack builder', 'infrastructure', A + 'onboarding.workspace_stack', 'stack_for', 'Builds/attaches a Stack for a connected workspace.', 'build_time_only', 'onboarding/workspace_stack.py:174,335')
E('ingest_canonical', 'Ingest canonical staging (connectors)', 'infrastructure', A + 'ingest.canonical', 'stage', 'Native feed adapters to canonical records (FHIR, HL7, X12, NCPDP, flat).', 'build_time_only', 'ingest/canonical.py:68')
E('source_profile', 'Source profile', 'infrastructure', C + 'source_profile', 'load', 'Received-field profile used by ladder rungs (lint, compiler, bottled).', 'inside:ladder_adapter', f'{EP}:325-330')
E('delivered_inputs_route', 'Undelivered-input requirement', 'check', C + 'delivered_inputs', 'requirements', 'Names missing inputs on decline answers.', 'inside:undelivered_input', 'delivered_inputs.py:145')

# ---------------------------------------------------------------- requested but not built
NB = [
 ('provider_error_fallback', 'Provider-error classification + model fallback', 'infrastructure', 'Classify billing/rate/overload errors as provider_unavailable and retry once on AGERE_INTERPRETER_FALLBACK.', f'{DOC_BRIEF} 0.1', 'AGERE_INTERPRETER_FALLBACK'),
 ('medication_state_macro', 'Medication state reconciliation macro', 'metric_or_measure', 'Shared governed macro: filled_current / listed_not_filled / documented_stopped_or_intolerant / never_recorded with evidence dates.', f'{DOC_BRIEF} 1.4', None),
 ('default_definitions', 'Named standard default definitions (avoidable spend etc.)', 'metric_or_measure', 'AHRQ PQI + NYU/Billings ED avoidability, measure denominator-minus-numerator gaps, PDC>=0.8, guideline target doses instead of refusals.', f'{DOC_BRIEF} 1.3; docs/agere/VBC_QUESTION_COVERAGE.md:107 (ED avoidability not implemented)', None),
 ('zero_result_probe_shared', 'Zero-result discovery probe on the shared path', 'check', 'Before returning 0/empty, probe alternative sources per predicate and re-plan once (answer_guard.zero_contradictions exists only on the legacy path).', f'{DOC_BRIEF} 1.6 (Mike 10-14)', None),
 ('answer_time_budget', 'End-to-end answer time budget', 'infrastructure', 'Return best executed candidate as partial when AGERE_ANSWER_BUDGET_SECONDS runs out.', f'{DOC_BRIEF} 1.7', 'AGERE_ANSWER_BUDGET_SECONDS'),
 ('shared_judge_model', 'Separate shared judge model', 'infrastructure', 'Measured planner/judge model split via AGERE_SHARED_JUDGE_MODEL.', f'{DOC_BRIEF} 2.4', 'AGERE_SHARED_JUDGE_MODEL'),
 ('static_prompt_cache_split', 'Static/dynamic planner prompt cache blocks', 'infrastructure', 'Cache the question-independent catalog block (1h TTL); live ledger shows 0 cache reads.', f'{DOC_BRIEF} 2.1', None),
 ('workspace_passage_search', 'Workspace passage search', 'retrieval', 'Passage search for connected workspaces.', 'docs/agere/WORKSPACE_FULL_PIPELINE.md:45', None),
 ('medgemma_reader', 'MedGemma document reader', 'local_model', 'Named candidate document reader.', 'docs/agere/architecture/component-catalog-2026-10-19.json c091; clinical_core/capabilities.py:30 (status candidate)', None),
 ('chandra_ocr', 'Chandra OCR 2', 'local_model', 'Named candidate OCR.', 'component-catalog-2026-10-19.json c092; clinical_core/capabilities.py:30', None),
]
for nid, name, fam, role, ev, flag in NB:
    E(nid, name, fam, None, None, role, 'not_built', ev, 'free', None, flag)

# ---------------------------------------------------------------- verify
def defline(path, sym):
    src = path.read_text().splitlines()
    parts = sym.split('.')
    pat = [re.compile(rf'^\s*(?:def|class)\s+{re.escape(parts[-1])}\b'), re.compile(rf'^\s*{re.escape(parts[-1])}\s*=')]
    for i, l in enumerate(src, 1):
        if any(p.search(l) for p in pat): return i


def modfile(mod):
    p = WT / Path(*mod.split('.'))
    return p.with_suffix('.py') if p.with_suffix('.py').exists() else (p / '__init__.py' if (p / '__init__.py').exists() else None)

failures = []
do_import = '--import' in sys.argv
ids = set()
for r in rows:
    assert r['id'] not in ids, r['id']; ids.add(r['id'])
    if r['module'] is None: continue
    f = modfile(r['module'])
    if f is None:
        failures.append((r['id'], r['module'], r['symbol'], 'module file missing')); continue
    if r['symbol']:
        ln = defline(f, r['symbol'])
        if ln is None:
            failures.append((r['id'], r['module'], r['symbol'], 'symbol not found in source'))
        else:
            loc = f'{f.relative_to(WT)}:{ln}'
            if loc not in r['evidence']: r['evidence'] = f'{loc} (def); ' + r['evidence']
    if do_import and r['module'].startswith('clinical_core'):
        try:
            m = importlib.import_module(r['module'])
            if r['symbol']:
                o = m
                for part in r['symbol'].split('.'): o = getattr(o, part)
        except Exception as exc:
            failures.append((r['id'], r['module'], r['symbol'], f'import: {type(exc).__name__}: {exc}'[:200]))
for r in rows:
    w = r['current_wiring']
    if w.startswith('inside:'):
        assert w[7:] in ids, (r['id'], w)

from collections import Counter
OUT.write_text(json.dumps({'components': rows}, indent=1))
print('total', len(rows))
print('family', dict(Counter(r['family'] for r in rows)))
def wk(w): return w.split(':')[0] if not w.startswith('cascade_tier') else ('cascade_tier(TIERS)' if w[13:] in [t[0] for t in T] else 'cascade_tier(shared_planner.*)')
print('wiring', dict(Counter(wk(r['current_wiring']) for r in rows)))
print('failures', failures)
