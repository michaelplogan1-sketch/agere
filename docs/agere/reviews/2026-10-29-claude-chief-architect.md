# Agere: critical architecture review (second pair of eyes on the §9 questions)

## 0. Scope, limits, and tags

**What I reviewed.** I reviewed the brief, the bottling paper excerpts you supplied (pp. 1, 4, 7, 8, 10) and the listed source. I had no repo access, execution or browsing in this turn. I did not run `check_agere_build_control.py`, changed no files, made no provider calls and contacted no one.

**What I could not see.**
- Run receipts and `system_one`.
- `answer_gate`, `bind_computed_prose`, `group_query_issues`, `numeric_role_review`, `dose.py`, `drug_classes.py`, `note_evidence.retrieve`, `request_lineage.component`.
- `tools/training` and `tools/bottling`.

Where I attribute a run failure to code, that is inspection, not a trace.

**Literature.** I did no literature search. Items marked "brief-cited" come from the brief's links, which I have not re-read. I make no claim about private TypeSafe, Google or OpenAI information. TypeSafe/System One points are general design judgment plus what the supplied paper says about Jev.

**Tags.**
- **[Observed]**: visible in the supplied code or brief.
- **[Hypothesis]**: plausible, not yet shown.
- **[Idea]**: research direction.
- **[Brief-reported]**: a number I cannot verify.

---

## 1. Verdict

**Keep the substrate, change the control flow and the data contract.**

- **Where the modular architecture is sound.** It is a reasonable foundation for the governed-data, deterministic-computation, provenance and rendering halves. Examples are `SemanticWarehouse._authorize`, `result_assembly.compose`, `evidence_projection.table_view/restore_table`, `verified_sources`, ledger accounting and code-built deliverables.
- **Where it is not.** The currently hardest half is turning free text into relation-typed facts and composing them. A free-form model sits in the middle of that, followed by a correlated, packet-scoped verifier. That is the weak point.
- **The evidence does not support the integrated cascade.** It cost 2.6× Haiku-only (≥$9.84 vs $3.82), took 1.8× as long (57.1 vs 31.5 min), and both ended partial. That is n=1 per arm with different trajectories. Nothing supplied shows a trained SLM, local reader or bottled rung contributed to an accepted output on this workflow. `bottled_sql` cost 62.09 s and declined. The planning SLMs timed out or declined.
- **The five failures share one root.** The system carries evidence (quotes, rows) to a free-form model and asks it to reconstruct relations in prose. Those relations are entity–reason–time–role–domain. Nothing in the supplied code makes the reconstruction checkable. Fixing them one by one with warnings repeats the pattern the brief already names ("a warning about an error is not its repair").
- **Recommendation.** Relation-first execution (§7, Alternative B). Typed facts and claims carry domain, entity and time. Deterministic composition replaces the free-form middle. Per-role model selection is measured, not fixed at Haiku-first.
- **Reassessment trigger.** Cold, repair and continuation have now ended partial three times with the same material defects. Your `AGENTS.md` rule calls for reassessment before another feature increment. This review can serve as that input. I cannot write the decision log.

---

## 2. What the measured evidence supports

| Observation | Reading |
|---|---|
| Integrated ≥$9.84 / 57.1 min / partial vs Haiku-only $3.82 / 31.5 min / partial | No benefit from the integrated stack on this workflow. Different trajectories, so not a component ablation. |
| Sonnet-only $11.08 / 17.6 min / invalid contract | **Not a valid comparator.** An accuracy-first system's strongest simple control has never been run cleanly. Its failure was a final-contract fault (see F9). `review_final` now rejects unexecuted tool envelopes, so a fixed-harness rerun is the single most decision-relevant missing arm. Keep the frozen arms frozen and run it as a new paired arm. |
| Continuation $2.7183, of which third-party $0.1747 | Third-party (Jev/Perplexity) is **6.4%** of that run. The Claude-side ~$2.54 dominates. In that run the Claude calls are the actor loop (51 turns) and any repair. Cheaper readers cannot move this run's cost much. |
| Continuation reused revalidated source observations and still showed defects 1–5 | **[Observed/inferred]** Because the readings were reused, recurring defects sit downstream of document reading or inside the saved observations. The reader-recall hypothesis is weakened. |
| 228 fill cells and 57 cohort patients agree with the source-defined check | Structured, deterministic parts work. That is the correct locus of trust today. |
| "157 / 156 isolated checks pass" | Overlapping groups, no provider calls, and they do not touch the failing relations. Not progress evidence. |
| Context −81.0% resident bytes | Bytes, not accuracy or dollars. The transport flattens the system prompt (F9), so cache economics may differ from API pricing. |

---

## 3. Current architecture: keep, simplify, remove, change

**Keep (strong parts, with the specific reason).**
- `SemanticWarehouse._authorize` read-only authorizer, plus request-local result views.
- `result_assembly.compose`. The base-key uniqueness requirement, `one` vs `collect` and the coverage counters are the right primitives.
- `evidence_projection.table_view/restore_table`, which checks dependency closure.
- `span_repair.repair`. Closed labels, with code copying source bytes and no model-written quotes, is the pattern to extend.
- `harness_improvement.recover_document_observations` (re-validates quotes, inherits no acceptance).
- `READ_ONLY` allowlist plus fresh-executor validation in `harness_improvement.propose`.
- Immutable, hash-pinned model registries (`component_learning.register`, `adapter_learning.capture`).
- Ledger accounting that surfaces `usage_unknown` and `api_cost_known=False` instead of reporting zero.
- Code-rendered deliverables.
- `materialize_table`, which keeps the model from transcribing patient tables.

**Simplify.**
- `Cascade.order`: collapse the permutation stack to three outcomes (see F8).
- The `policy_identity`/`continuation_identity` file-hash coupling (F10).
- The document-review fallback ladder (F7).
- Support-packet construction (F5).
- Learning invalidation scope (F11).
- The environment-flag space, around 15 interacting `AGERE_*` switches, so a run's configuration is no longer inferable from its outcome.

**Remove or disable on the compound path.**
- `bottled_sql`.
- Planning-SLM attempts with a 600 s ceiling.
- Dense ranking in exhaustive review.
- The local-reader rung.

**Change.**
- The document-review output contract: facts, not spans.
- Final-answer claims: typed, with domain.
- The ladder: role-aware and measured.
- Verification scoping and reuse.
- Transport honesty (`tool_choice`, effort).

---

## 4. Observed defects traced to code

### F1. The ladder is role-blind, so Haiku's silent errors set the accuracy floor
**[Observed]**
- `model_policy.ladder(role)` validates `role` and returns `list(LADDER)` regardless of it. `_resolve` calls `model_for()` with no role. Every question-path call gets the same rung.
- Escalation happens only after a detected failure: `tool_loop.escalate`, `Cascade.escalate`, and the Haiku→Sonnet loops in `document_review`.
- A wrong-but-valid Haiku output that passes the checks never reaches Sonnet.

Haiku-first is a cost-first rule. It is only as accurate as detection, and detection is weak (F5). Under "accuracy first, then cost" the rung should be chosen per role from measured paired accuracy, not fixed globally. Haiku remains right where measured sufficient, for example SQL authoring over documented views and bounded decomposition.

### F2. The typed text path returns evidence, not facts
**[Observed]**
- `typed_document_review.read` appends `{'value': span['text'], ..., 'quote': span['text']}`. The "value" is the block itself. `span_repair.repair` does the same.
- Decisions are block × field, with `OPTIONS['finding']` meaning "this block answers the question". No entity, relation type, polarity, temporality or reason code is extracted.
- Meaning is deferred to the final investigator, which has to rebuild relations from lists of quotes.
- `blocks()` cuts flattened lines every 2048 characters without sentence or section awareness. **[Hypothesis]** A reason can be separated from its entity across a boundary.

This is the main root of failures 2 and 5.

### F3. Clinician-role evidence is invisible in the actor's view, and the extractor is brittle
**[Observed]**
- `execution_state.catalog` and `semantic_analyst.actor_result` build `narrative_field_manifest` only from `review_json`. `source_clinicians_json` (added by `with_correspondence`) never appears in the compact state.
- The `RULES` text mentions the column only inside the `review_documents` tool description.
- In `correspondence_roles`, the signed-author case requires `re.sub(r'[^a-z]','',…)` of the signature name to equal that of the `Provider:` name. "Jane A. Smith, MD" becomes `janeasmithmd`, while "Jane Smith MD" becomes `janesmithmd`, so they mismatch and are silently dropped.
- The name pattern requires a credential from `MD|DO|NP|PA-C` and 2–6 capitalized tokens. "Dr. J. Smith" is not matched.
- The signed specialty is taken as one raw line, so a practice name can become `documented_role`.
- Being conservative (`current_assignment: not_established`) is correct. Being layout-brittle is not.

**[Hypothesis]** This explains most of the 13-patient role loss. Check with the counts of `source_clinicians_json` entries by patient before and after the actor's composition step.

### F4. Domain and denominator claims have no deterministic check
**[Observed]**
- `review_final` runs `numeric_role_review` only when `AGERE_QUANTITATIVE_ROLE_CHECK=='on'`. The default is `off` and returns `status: unselected_after_native_regressions`. The check that would target "89 rows called GDMT" is off by default.
- From the supplied code, `bind_computed_prose` binds a template slot to `(result_view,row,column)`. It binds a number, not the predicate or domain that produced it. `group_query_issues` compares the question to queries, not the prose label to the final view's class column.

**[Hypothesis]** The 89 "GDMT" rows passed because no check compares the claimed domain to the domain of the bound cell. I could not see `bind_computed_prose` or `group_query_issues`, so this is not confirmed.

### F5. Verification is mis-scoped, defeats its own reuse, and cannot localize
**[Observed]**
- Every patient packet in `support_packets` embeds `global_claims_context: summary`, `shared_evidence` and `native_bindings` (the digest of every tool call). Any change to the headline, any added tool call or any changed result alters all 57 patient packets, so exact-request cache hits across repair rounds are unlikely. This contradicts brief §4.7 ("reuse unaffected checks").
- `check_support_packets` returns, per packet, only `label`, `decided_by` and `probabilities`. A reason is attached only for the oversize case. `execution_state.messages` then forwards `unresolved_dependencies` with an id, a label and possibly no reason. The actor repairs blind. This is the code behind the brief's "weak repair feedback".
- A packet-level label covers every claim in the packet. A needle such as "the SGLT2 reason sits in the MRA field" must be found inside a JSON blob by a closed-choice reader.
- `check_support_packets` loops serially over about 58 packets.
- The same provider family (Jev/Perplexity, `AGERE_DOCUMENT_TYPED_ORDER='jev,perplexity'`) selects spans and judges support. Errors are correlated.
- The coverage check sees a projection where source cells are replaced by dependency markers (`evidence_projection.coverage_answer`), so by design it cannot see misattributed content.
- The legacy `document_review.review` completeness check asks one closed-choice question over up to 240 KB: "was anything relevant omitted?" Absence over a long context is where small typed readers are weakest (brief-cited "Lost in the Middle"). The typed path covers completeness by construction (every block decided), which is better. Which path ran in the cold run is unclear from the brief.

### F6. A noisy verifier drives expensive escalation
**[Observed]**
- In `tool_loop`, a non-deterministic review `repair` calls `escalate(...)`. After that the rest of the loop runs on Sonnet permanently.
- In the legacy `document_review.review`, a Haiku result with an uncertain typed check raises `ValueError('…investigate on Sonnet')` and re-extracts the whole batch (up to 240 KB) on Sonnet.

Escalation triggers inherit the verifier's false-positive rate. Check `model_escalations` in the run receipts.

### F7. Dead or non-paying compute in exhaustive document review
**[Observed]**
- `typed_document_review.rank` runs an UltraBERT encode plus reranker over every block, under `_RANK_LOCK`. It is on by default (`AGERE_DOCUMENT_RANKING != 'off'`). `read` then processes all batches regardless. The ranking only changes display order within a document, and thus the order of quoted findings. The brief says ranking should not reorder exhaustive work.
- `local_readers_then_recheck` runs a local reader (serialized under `_READER_LOCK`) and then **always** runs the paid typed `judge` on every still-pending field with the local answer as a hint. The local rung removes no paid call. At best it anchors the judge, and anchoring can bias. It adds latency.
- `isolate_unresolved_sources` re-asks the same judge with a narrower context. It is plausible and cheap, but unmeasured.
- `source_batches` re-sends a document's full text for every batch of 64 decisions. A document with 120 blocks × 5 fields is about 600 decisions, or 10 batches with 10 copies of the text. Fan-out is block × field, not evidence-bearing pairs.
- Deduplication is `digest([day,text])`, so identical text on different days is judged again. Paragraph-level copy-forward is not collapsed.

### F8. The cascade is a stack of permutations with swallowed failures
**[Observed]**
- `Cascade.order` applies, in sequence: the System One router, `FEEDBACK_READERS`, `router_order`, `learned_reorder` (cascade_router), `tier_router.reorder`, `staged`, `free_first`, `heavy_last` (with `qwen_after_quick_claude` and `analyst_first`), `backstop_before_generalists`, the adapter-candidate insertion, `nonfrontier_first` and `investigator_first_for_one_patient`.
- Most are wrapped in `except Exception: pass`.
- `backstop_before_generalists` hard-overrides the learned routers on evidence of 7/17 vs 3/17. That is an admission that the learned routers cannot be trusted there, and a small sample.
- In owned recovery, `Cascade.order` returns `('backstop_agent',)` alone. For this workflow class the cascade is demonstrably dispensable.
- `Cascade._run` still gives `reserved_backstop` a turn after every tier declines, so the compound path ends at the investigator anyway after paying for the tiers before it.
- `_interpret` allows up to 3 plan+audit proposals (two model calls each), then 3 rescue plan+audit rounds. Each can be a hosted call.

### F9. The test transport is a fidelity gap
**[Observed]**
- `subscription_transport.build_prompt`/`respond` ignore `tool_choice`. `tool_loop` sets `{'type':'none'}` when `force_text` or the tool budget is reached, but the emulated system prompt still lists the tools and the `{"tool_use":…}` protocol. A "reply with ONLY the JSON answer" retry conflicts with the system prompt.
- `cache_control` is dropped when system blocks are flattened to a file.
- `output_config.effort` is forwarded only if a caller sets it, and `tool_loop` does not. The CLI default effort applies to every actor turn.
- The docstring says results are "close to, not identical with, API runs", and the module is for tests only.

**[Hypothesis]** The conflict plausibly contributed to the Sonnet-only "pending arguments mistaken for an answer", and uncontrolled internal effort may explain the 1,017.87 s / 128,199-token SQL turn. **[Hypothesis]** All timing, cost and cache economics measured on this transport may not transfer to a native tool-use product path.

I do not recommend a spending cap. I recommend recording per-call duration, turns and output tokens so outliers are visible. A liveness timeout policy is your call.

### F10. Reuse almost never hits under active development
**[Observed]**
- `harness_improvement.identity` includes the full text of `harness_improvement.py`, `continuation_identity` (which hashes 15 executor files), and `physician_feedback_revision`. That revision is `MAX(seq)` over **all** `physician_feedback` events, so feedback on any unrelated question invalidates every candidate.
- `shared_planner.policy_identity` hashes about 100 files.
- `adapter_learning.contract` hashes about 35 files, including `proposers.py`, `sql_query.py` and `analysis_graph.py`. A cascade edit invalidates an SQL adapter's recorded contract, and `training_revision` then refuses it.
- Conservative invalidation is defensible. Combined with daily edits, the reuse hit rate is near zero. **[Hypothesis]** Measure it from stored candidate events.

### F11. Learning labels come from the checkers; one correction wipes all roles
**[Observed]**
- `component_learning.observe` marks a tier_router label `machine_review` and eligible when the run is `answered`, verified `checked` or `system_one_reviewed`, and the workflow is complete. The labels are therefore produced by the same unvalidated checkers.
- `harness_improvement._finish` selects a candidate when `complete and not any_regression`. The only regression test is **cost**, actor and full-request. There is no output-diff or accuracy regression test against the parent candidate.
- A user correction (or an unaccepted `answer_review`) executes `for role in ALL_ROLES: feedback(..., accepted=False)`. This quarantines every active component and, via `record_feedback`, the implicated harness candidates. A correction on one question resets all learning. Learning cannot accumulate under real use.

### F12. Bottled-module inconsistencies
**[Observed]**
- The comment above `NARROW_VERSION` says the narrow rung's "Default stays off." The constant reads `NARROW_DEFAULT = 'on'`. Which is true determines what ran in a given run.
- `benchmark_match` makes the bottled rung decline benchmark questions (unless a release root or learning flag is set). The system measured in benchmarks is therefore not the shipped system.
- `bottled.check` verifies lint, read-only execution and sanity. `bottled.decide` verifies agreement among samples of the same model. Neither verifies meaning.

### How the brief's five failures map to this

| Brief failure | Most probable locus | Evidence level | Smallest general fix |
|---|---|---|---|
| 1. 89 "GDMT" rows, 75 `other` | Domain not verified (F4) | Check gap observed; cause hypothesized | Claim IR with domain predicate, deterministic recompute |
| 2. Reason in the wrong class field | Reader (F2: value==quote, block×field), the actor's question phrasing, or SQL/JSON-path composition. **Cannot be separated from the supplied material.** | Hypothesis | Trace the one known instance end to end first. Then entity-bound relation reading |
| 3. Dated clinician objects dropped | F3 | Observed (manifest gap, brittleness) | Role table with relationship types, shown in state |
| 4. Target range / reference max / use / history / MTD conflated | Distinctions exist only as prose in `RULES`. The dose columns appear to exist (`reference_max_status`, `documented_reference_max_status`) | Observed (prose-only) | Render statuses from enum columns by template. Missing prerequisites as typed unknowns |
| 5. Quote flood | No fact-level span selection (F2), unbounded lists in rank order (F7) | Observed | One primary span per fact plus a corroboration count |

---

## 5. Best component order by task family

Each ordering is a proposal to qualify. Haiku/Sonnet placement is conditional on role-level measurement (E5).

| Task family | Order | Where components earn their cost | Where they do not |
|---|---|---|---|
| **A. Known governed measure, count, cohort** | grammar / `population_engines` → `execute_metric` / `ask_agere` / SQL → deterministic acceptance contract | None is a model. The narrow slot SLM earns only on paraphrase misses of closed families. | Learned routers, any planner |
| **B. Novel multi-table structured composition** | One plan (Haiku-class; Sonnet if E5 says so) over a schema slice → compile → execute → deterministic invariants (grain, join cardinality, scope, as-of, units, registered domain) → one semantic interpretation audit → stronger model only on a failed audit | Encoders for schema and concept retrieval (`schema_prefetch`) | Multiple independent planners, whole-question SQL bottle |
| **C. Focused document question** | lexical/concept `search_documents` with neighbor window → (reranker only if candidates exceed one judge call) → typed closed-choice judgments on whole docs (Jev→Perplexity, batched) → code copies spans → Haiku only for contradictions | Reranker/encoder as candidate generator; Jev for bounded closed choices | Dense ranking when every candidate is judged anyway |
| **D. Exhaustive cohort narrative relation** (reasons, preferences, barriers) | structured enumeration of (patient, entity) pairs → anchored candidate blocks with neighbors plus a cheap sweep of unanchored blocks → **entity-bound** typed relation judgments → calibrated gate → Haiku on unknown/disagree with only block + entity + structured context → Sonnet if Haiku is unknown/contradictory → deterministic fact table | Jev/Perplexity batching; Claude only on residual | Block×field fan-out; local-reader rung; rank on the critical path |
| **E. Clinician / care-team relationships** | structured `care_team` and encounter-attributed providers → letter-derived roles by a high-precision regex fast path plus a typed reader for variant layouts (spans copied in code) → relationship-typed table (never infer responsibility from authorship) | Regex as fast path; typed reader for layout variants | Name-equality string heuristics as sole path |
| **F. Longitudinal, prediction, survival, forecast, causal design** | SQL cohort/time series → **baseline first** (rate, Kaplan–Meier, simple trend, small logistic) → registered complex operator only if the estimand needs it → diagnostics (events per variable, censoring, calibration, out-of-time split) → language limited by code to the claim class the design supports | Strong model for estimand and identification statement and interpretation inside an allowed-claims taxonomy | A model ever computing the estimate; causal wording from association |
| **G. Financial, contract, provider performance** | governed metric/engine (`mlr`, `vbc`, `risk_adjustment`, …) → typed contract-term extraction with clause quotes → deterministic calculation → case-mix and uncertainty → template narrative | Typed readers for terms | LLM arithmetic |
| **H. Discovery / explanation ("what drove X")** | SQL decomposition and stratification, `change_attribution` → hypotheses from a model → association-only claims enforced in code | Model for hypothesis generation | Causal claims without design |
| **I. Deliverables, worklists, follow-ups** | Render from the saved result and decision context in code. Novel editorial or design work only: frontier, separately metered | None | Any model call that re-derives analysis |

**Cross-cutting rules.**
- Independent work runs concurrently: the SQL half and the text half, per-patient judgments, and the support checks that are currently serial.
- **Perplexity-first 96/96 vs Jev-first 95/96** is statistically indistinguishable and is a 96-check authored sample. Order those two by latency and cost, not by that result.
- **Where Jev/System One earns its cost.**
  - Narrow closed-choice judgments with explicit options and bounded context.
  - Calibrated probabilities used for routing, with per-task calibration sets.
  - Cheap parallel verification when the decomposition differs from the producer's.
  - Not absence-over-long-context.
  - Not grading its own family.
  - Not open-ended generation. The supplied paper says Jev cannot generate free text.
- **Where trained SLMs earn their cost.** Closed-vocabulary mappings with deterministic round-trip, as in `bottled.cohort_slots` → `spec_from_slots` → `_expect`. They do not earn it in open compound planning, based on the 600 s timeout and the decline.
- **Where encoders and rerankers earn their cost.**
  - Candidate generation over large per-patient corpora.
  - Near-duplicate collapse.
  - Query-to-schema retrieval.
  - Not ordering exhaustive work.

---

## 6. Representing and verifying entity, time, causality, current-vs-historical, domain, and supported unknowns

### 6.1 Data contract (a sketch, not an implementation)

```
fact:
  fact_id, patient_id
  relation   ∈ {withheld_for_reason, dose_limited_for_reason, intolerance_stated,
                contraindication_stated, patient_declined, access_barrier,
                planned_change, other_documented}
  entity     {kind: drug|class|test|service, registered_id, mention_text}
  subject    ∈ {patient, family_member, other}
  polarity   ∈ {affirmed, negated, uncertain}
  status     ∈ {current, historical, planned, hypothetical, retracted}   # as stated, not inferred
  reason     {concept_id | span}
  event_time, recorded_time (note date), visible_from (available_date)
  evidence   {record_id, span_start, span_end, quote, context_digest}   # bytes copied by code
  produced_by {component, version, calibrated_p}
unknown:
  patient_id, question_id,
  cause ∈ {no_candidate_blocks, candidates_irrelevant, ambiguous, conflicting,
           prerequisite_missing, not_visible_as_of},
  searched {docs_examined, blocks_examined, anchors_used}
claim (final-answer unit):
  claim_id, kind ∈ {count, rate, dose_status, reason, role},
  value_binding (view, row, column),
  domain (registered_group_id or view+predicate),
  fact_ids / evidence_ids
```

### 6.2 Principles

- **Entity.** The entity comes from the structured side (the (patient, class) gap pairs: 57 × 4 = 228 cells here), not from the reader's guess. The question asked of the text names the entity. Failure 2 is then excluded by construction instead of by a downstream warning. The mechanism generalizes to any (patient, drug/test/service) pair.
- **Time.**
  - Keep event time, recorded time and visibility time distinct. `read_document` already enforces `available <= as_of`.
  - "Current" is derived by code from dated structured data under the decision-context rules (for example a fill in the last 12 months). The reader emits only the status as stated in the note.
  - A signed specialist, a referral, an addressee, a historical assignment, current responsibility and active attendance are different `relationship` values. None is promoted to another without a dated rule.
- **Causality.** Store causal links as "document states X because Y" with the span for both ends. Never convert an adjacent mention into a cause. A cause inferred by a model is a different `relation` value ("documented_association") than a stated cause.
- **Domain and grain.**
  - Every registered result carries `grain` (key columns) and `domain` (a registered predicate id). `result_assembly.compose` already enforces base uniqueness. Extend it to refuse joins and claims across incompatible domains.
  - Final numeric claims reference `(view, domain)`, and the system **recomputes** the claim by a registered query. Failure 1 is then a mismatch between the claim's domain and the cell's domain, caught without a language model. This is a program-aided, claim-to-query check. **[Idea]**
- **Supported unknowns.** An unknown has a cause and a search receipt. "No documented reason" is only reportable with the blocks-examined count. A missing prerequisite (for example weight) is a typed unknown, never a contraindication. This is how a supported unknown becomes an engineering success rather than an omission.
- **Dose semantics.** Render dose statuses from enum columns by template (`at_target` meaning range, reference upper dose, documented use, individual MTD, prerequisite missing). The prose rules in `RULES` stay as documentation, not as the enforcement mechanism.

### 6.3 Verification redesign (Q5)

Verification is **too weak, too correlated and incorrectly scoped**, and it is cheap in dollars but not in latency or in escalation side effects. In the continuation, third-party was $0.17 in total.

1. **Deterministic first**, cost near zero:
   - membership, grain, domain recompute, units;
   - quote existence and offset (`verified_sources`, `validate_saved_source_fields`);
   - entity-pair identity: does the fact's entity equal the cell's column entity?
   - date and `as_of` visibility.
2. **Fact-level typed checks** (Jev/Perplexity): one closed choice per fact, with a **different decomposition** from the producer. Example: "Does this quote state why *[entity]* was held, for *[patient]*, and not about another medication?" Where calibrated confidence is low, add an independent second family (see §12, MedGemma, only if it adds detections).
3. **Claim-level units.** Patient packets carry only that patient's claims and facts, plus versioned shared definitions. Move global statements into the summary packet. Generate global statements from cells so they contain no free-text patient claims.
4. **Content-addressed reuse:** key = H(claim unit, evidence unit, definitions version, check-prompt version, verifier identity). Invalidate along the existing `evidence_projection` dependency edges. A check is reused only under identical key. Source equality alone is insufficient, as the brief says.
5. **Return reasons per claim** so repair feedback names the claim and the quote.
6. **Monitor, don't gate, with a stronger model.** A small stratified sample, audited by a stronger model or a reviewer you designate, estimates per-claim-type error rates. It is not used on every item.
7. **Parallelize** independent checks. The serial loop in `check_support_packets` is a free speed gain. Speed ranks third, so this is low priority.

---

## 7. Three credible compositions, one recommendation

### A. Hardened current system
Keep the cascade and the free-form investigator, and add the five targeted checks plus the verification fixes.
- **Pros:** smallest migration, preserves all existing receipts.
- **Cons:** keeps the stack of order permutations and the model-in-the-middle. Each new check is another layer. **[Hypothesis]** Partial outcomes persist because composition errors remain probabilistic.
- **Fails when:** a new question shape makes the actor misattribute a relation that no check anticipates.

### B. Relation-first investigator (recommended)
A planner emits a typed workplan: output schema, named relations (SQL, entity-bound text, registered math), and the join plan. A deterministic executor runs each relation with the component that suits it. `compose_table` joins them. Prose is rendered from claim IR, or a single constrained model call that cites cells. The LLM actor is limited to planning and residual adjudication.
- **Pros:**
  - Removes the free-form middle (failures 1, 2, 4, 5 by construction; 3 by table).
  - Fewer actor turns, and the verifier gets typed units.
  - Gives bottling a stable thing to bottle.
  - Per-role model choice becomes natural.
  - Reuses existing infrastructure: `compose_table`, the result views, the dependency edges, `request_program`/`analysis_graph` typed graphs, `review_documents`, `execute_analysis`.
- **Cons:**
  - Planner errors. A wrong workplan is a new failure class, mitigated by typed validation and one audit.
  - Entity-anchored candidate generation can miss reasons stated without naming the entity. It needs neighbor windows, an unanchored sweep and a measured recall check.
  - It needs structured enumeration of pairs. Purely discovery questions still use the open-field path.
  - Refactoring cost.

### C. Frontier-first single agent over a deterministic substrate
One Sonnet-class agent plans, composes and adjudicates with the same governed tools. Cheap models only do bulk reading. An independent verifier checks it.
- **Pros:** fewest moving parts, aligned with "accuracy first".
- **Cons:** cost and latency. The only evidence is the invalid Sonnet-only arm. The same model composes and judges, so errors are correlated. It still lacks the typed fact layer.
- **Fails when:** a rerun with the fixed harness costs several times B at no accuracy gain.

### Recommendation
**B, with C as the control arm and as the residual composer when measurement says Haiku is insufficient.** This reconciles Haiku-first with accuracy-first: the rung is set per role by measured paired accuracy. The rule "Haiku first" should survive only where Haiku is measured sufficient.

### Stop doing now
- Adding tiers or reorderers to the cascade.
- Running `bottled_sql` and the planning SLMs on compound questions.
- Treating checker-pass as the label for routers or candidate selection.
- Letting the actor write numeric or domain prose.
- Comparing end-to-end arms with different trajectories as component evidence.
- Counting overlapping isolated checks as progress.
- Promoting the 81% context reduction before it is shown to pay. First do E1 and E5.

### What would reverse the recommendation
- A fixed-harness Sonnet-only arm reaching equal or better invariant, metamorphic and evidence-exactness scores at comparable all-attempt cost per complete-correct answer. Then C becomes the default and B's deterministic substrate stays.
- The entity-anchored path's recall falls materially below the exhaustive reader (E4) and no cheap sweep closes the gap.
- Planner validity on unfamiliar compositions stays low even with schema slices and one audit.
- The 5-fault detection test (E3) shows the current verifier already catches the seeded faults. Then the verification redesign drops in priority.

---

## 8. Unearned complexity

| Component | Why it is unearned now | What would earn it |
|---|---|---|
| Learned router stack (`cascade_router`, `tier_router`, `router_order`, System One router) on compound questions | Overridden by `backstop_before_generalists`. Labels come from checker-passed outcomes (F11). Unmeasured ablation | Ablation on the compound path with independent outcomes |
| `bottled_sql` | 29% precision on lint-passing held-out, 62 s decline in the run | A named stable operation inside broad questions (not the whole question) |
| Planning-SLM attempts on compound questions | One timed out at about 600 s, one declined; no contribution | A closed-vocabulary planning subtask with deterministic round-trip |
| Dense ranking in exhaustive review | Changes display order only (F7) | A measured recall gain in a targeted-selection mode |
| Local-reader rung | Always followed by a paid recheck (F7) | An independent-vote role, shown to add detections |
| `_interpret` plan+audit loops (up to 6 proposals) | Many hosted calls | Measured acceptance by iteration number |
| Cascade-level `escalate` re-running every Haiku tier on Sonnet | Same tiers failed once, then run again | Tier-specific escalation with evidence |
| ~100-file `policy_identity`; 35-file adapter contract | Reuse near zero under edits (F10) | Narrow identity plus a regression suite |
| "Recursive improvement" naming | `harness_improvement` is validated memoization. It cannot improve accuracy, since it replays what was produced | Parameterized template transfer (§10) |
| `AGERE_ANALYST_CONTEXT=discover` | Dollar and accuracy effect unmeasured; adds tool turns | E5 with context mode as a factor |
| Environment-flag space | Unattributable runs | One configuration manifest per run |

---

## 9. Missing capabilities

1. A typed fact and claim layer with relation, time, polarity, domain and search receipts (§6).
2. Clinician entity resolution and a relationship ontology that merges `care_team` and letter-derived roles.
3. A claim-localizing verifier with seeded-fault monitoring.
4. Role-level paired model measurement to set the ladder.
5. Cost attribution by `component()` and a defined cost per complete-correct answer, with zero-denominator reporting.
6. Calibration of typed confidence per relation type and document family, with a risk–coverage curve.
7. A label-free evaluation harness (metamorphic and cross-modal) plus a path for reviewed labels.
8. Parameterized workflow templates (not literal replay).
9. Versioned reference and guideline knowledge with applicability (age, weight, renal) as typed prerequisites.
10. Transport parity for any product path (native tool use, real caching). This is an external prerequisite and not mine to scope.
11. Throughput planning for 10⁴–10⁶ documents (batching, queueing, resumability).
12. Security, PHI, regulatory status and clinical safety review. These are external prerequisites for qualified people to assess, not claims I can make here.

---

## 10. Bottling

### 10.1 What the paper says (source data, from the excerpts; numbers as reported, unverified)
- An agent builds an artifact once for a large unlabeled batch: code, a small trained model, a retrieval index or a hybrid with fallback.
- Across 10 models × 3 tasks × 2 runs, 48/60 runs fall below the zero-shot 95% CI of their own model. 31/60 fall below the better of two naive distilled students that used the same token budget.
- Best cases retain 82–97% of zero-shot or Jev quality at 650–890× lower reported cost, mostly on classification-style tasks.
- Zero-shot competence does not predict bottling ability. Agents also fail in harness ways: exhausting the budget without output, or stopping after a truncated read (49 of millions of instances).
- The tasks are narrow, with millions of instances, and not healthcare. Its fixed budgets are experimental conditions, not Agere rules.
- One implication: **the naive distillation baseline is strong and is the control** for any artifact claim.

### 10.2 What exists in Agere, and what it contributed

| Item | State | Contribution to the compound HFrEF workflow |
|---|---|---|
| `bottled.answer` / `BottledModel` (whole-question SQL, Qwen3-4B LoRA, k-sample agreement) | Off by default (`DEFAULT_ENABLED='off'`). Held-out (brief-reported in code comments): 22/102 greedy exact; precision of lint-passing 22/76 = 0.29; best rule k=3/m=3 gives 7/10 at 10% coverage | Attempted in the cold run: **62.09 s, decline**. Zero accepted answers. Latency only |
| `bottled.cohort_slots` + `bottled_cohort_hook.plan` (closed slots → canonical wording → deterministic parser round-trip, 5/5 agreement) | Code default `'on'` (conflicts with its comment). Held-out comment: 81.7% coverage at 0.927 exact-spec precision vs Haiku 0.915; 2/65 false claims on non-family questions | Not reported for this run. It applies only to cohort-family sub-clauses and only where the parser declines or leaves slots missing. Receipt field: `receipt['bottled']` |
| `adapter_learning` / `component_learning` | Pinned, immutable registries and release machinery. Adapter data come only from reviewed observations (`adapter_learning.export`), so there is a reviewed-label bottleneck. No training or promotion overnight | None demonstrated |
| `harness_improvement.propose/replay` | Literal read-only action prefixes under exact guards. Observation-only in the latest run | None. Reuse is dead under active edits (F10) |
| Explicit owned recovery (`recover_actions`, `recover_document_observations`) | Exact-source observation reuse with quotes re-validated and no acceptance inherited | **The only reuse that demonstrably did something**: the continuation reused revalidated observations. Savings are unquantified |
| Deterministic engines, SQL macros, dose function, code renderer | Hand-built | These are Agere's real "bottles". They produced the correct portions (228-cell agreement, zero-model-call HTML/PDF) |

**Candid note.** `bottled.py` is a conventional distillation pipeline: hosted answers → LoRA → agreement gate. That is the paper's **baseline** in spirit, not an agent autonomously constructing and budgeting an artifact. The paper's measured object is not implemented in Agere. Using the word "bottling" for both blurs what has been validated.

### 10.3 What breaks whole-question SQL bottling
1. Executability and sanity (`bottled.check`) do not verify meaning. Agreement among samples of one model measures self-consistency, and the file itself states its errors correlate with the hosted family it was distilled from.
2. The labels come from the unqualified hosted ladder ("two independent models agree, or the judge confirms"), so it distills agreement, not correctness.
3. Compound requests contain clauses SQL cannot express (note reading, role reconciliation, deliverables).
4. A decline costs wall-clock time (62 s) with no saving.
5. Precision is 0.29 on lint-passing greedy answers. The code header itself concludes "no learnable scope" for the general case.

### 10.4 Which next investment: guarded program reuse or role-specific distillation?

There are two separate considerations, and they point in different directions.
- **Technical fit** favors distilling a **closed-choice, entity-bound relation judge**. That is the shape of the paper's best results (classification), and the narrow slot rung is proof of pattern.
- **Economic leverage** favors removing Claude actor turns. In the one run with a split, third-party judging was 6.4% of cost. Distilling the readers cannot move cost much. **[Caveat]** The cold run's split is unknown, so this is conditional on E1.

**My answer:** neither before the typed-relation refactor, because neither has a stable object yet. After it:
1. **Parameterized program reuse** is the better first investment. It targets the actor loop, and the output of a reused program can be checked by output equality on deterministic columns. It is not literal replay: lift a validated workplan into a template with typed slots (population predicate, entity group, relation questions, output columns). Literal replay keyed to an exact question is memoization and is not transfer.
2. **Role distillation of the relation judge** second. It is justified mainly by latency and throughput at scale, by a second-family verifier, or if E1 shows judge cost or latency dominating after fan-out shrinks.

### 10.5 Minimum experiment (no new large training run)
**E7, program transfer.**
- Build one parameterized workplan from the first composition that passes the new deterministic invariants.
- Apply it to at least 3 different bindings (for example another therapeutic class group and population, and a screening or care-gap relation). Keep HF/CKD as regressions.
- Arms: (a) template replay with a fresh executor; (b) the current Haiku-first investigator; (c) a fixed-harness Sonnet-only agent.
- Measure:
  - deterministic-invariant pass;
  - metamorphic pass (E2);
  - evidence exactness;
  - honesty of unknowns;
  - wall clock;
  - **all-attempt cost including build and failed attempts**;
  - number of manual edits to the template per binding.
- Pre-register the decision rule:
  - Adopt reuse only if (a) has no invariant regression against (b) and the break-even count N\* = C_build / (C_fresh − C_exec) is below a plausible reuse count. Unlike the paper, C_exec > 0 here because fallbacks exist.
  - Reject the template if manual edits per binding are non-trivial.

**E9 (distillation baseline, design only).** Use logged typed judgments as fidelity targets. Split by source family into fit, calibration and test. Compare a naive small student against the current judge on matched coverage and precision, and on the metamorphic suite. Report **fidelity to the system, not clinical accuracy**. Agreement among models is not a clinician label.

---

## 11. Learning, calibration, evaluation

### 11.1 Is the recursive-learning loop sound?
The separation into evidence reuse, execution learning and model learning is correct. The implementation has these gaps:

- **Self-supervision through the checkers** (F11). Selection and routing labels depend on checks of unproven reliability. Positive feedback loops are possible.
- **No accuracy-regression test.** Only cost regression is checked in `_finish`.
- **Over-invalidation.** The global feedback revision (F10) and the all-roles quarantine on any correction (F11) prevent accumulation. Attribute corrections to the implicated role or candidate.
- **No transfer evidence.** Exact-question replay does not show transfer, as the brief says.
- **Supervision bottleneck.** Adapter data come only from reviewed observations, so training waits on reviews.

### 11.2 What not to learn
- Clinical definitions or thresholds from model agreement.
- "Reasons" or causal labels from unreviewed outputs.
- Unknown→negative conversions.
- Routing labels derived solely from checker-passed answers.
- Patient-specific facts into weights.
- Any student evaluated against the system that generated its labels, then described as accuracy.

### 11.3 Supervision available without new clinician labels
- **Metamorphic tests** (label-free, on scratch copies): swap two class mentions inside a reason sentence → the attributed entity must swap. Move the sentence to another note → the date and record id change, the entity stays. Negate or change the experiencer → polarity or subject changes. Duplicate a note with a new date → no new fact. Reorder documents → identical output. Never alter the original cohort or frozen data.
- **Cross-modal concordance** **[Idea]**: note-stated current use vs structured fills. This is an independent modality, so agreement rates give a weak reader-accuracy signal. Discordance may be real clinical state, so use it as triage, not as truth.
- **Seeded-fault injection** for the verifier (E3).
- **Reviewed labels:** only through channels you choose. This review requests none. Your acceptance of earlier outputs does not label unseen ones.

### 11.4 Calibration
- Calibrate Jev/Perplexity probabilities per relation type and document family (progress note, HIE, scanned OCR text), with held-out families for the calibration and verification splits. Pooling across families will hide miscalibration.
- Report reliability curves with bootstrap intervals and a selective-risk vs coverage curve. Choose the escalation threshold from that curve to meet a stated error target, then confirm on an untouched family.
- Treat OCR noise as a covariate.
- **[Idea]** Split-conformal or risk-controlling prediction sets for abstention (from memory, not verified here).

### 11.5 Evaluation
- Complete-workflow acceptability is the headline, not label accuracy and not "HTML produced".
- Report all-attempt cost per complete-correct answer. The current denominator is zero, so cost per complete-correct is undefined, not zero.
- Keep frozen arms and independent baselines, and add paired role-boundary tests on **identical inputs**.
- Do not count overlapping test groups.

---

## 12. Outside methods (focused, not exhaustive, not guaranteed best)

| Idea (tier) | Role / existing alternative | Benefit · cost/risk · readiness | Smallest falsifiable experiment |
|---|---|---|---|
| Entity-bound typed relation reading and cheap-extract → typed-verify → targeted repair (brief-cited TypeSafe cookbooks; links not re-read) | Replaces block×field fan-out in `typed_document_review.read` | Right attribution by construction; fewer decisions · planner/anchor misses · moderate | E2 (metamorphic) and E4 (recall). Falsified if swap tests still misattribute, or recall falls below the exhaustive reader |
| Calibrated selective prediction / conformal (remembered) | Existing probabilities and `route_receipt` | Principled escalation threshold · needs per-family calibration data · ready once judgments are logged | Risk–coverage curve on held-out family. Falsified if no threshold meets the target error on an untouched family |
| Claim-to-query recomputation (program-aided verification; remembered) | Replaces LLM-only domain checks | Deterministic catch of failure 1 · needs claim IR · high readiness | Inject a wrong-domain count. Falsified if recompute does not flag it |
| Second-family verifier, e.g. MedGemma (brief-cited; license, hardware and readiness unknown) | Independent vote beside Jev/Perplexity | Reduces correlated error · hardware and serialization cost · unqualified | Run on E2/E3 suites. Adopt only if it adds detections the current family misses (complementarity), not mere accuracy |
| Context placement (brief-cited, Lost in the Middle) | 240 KB batches and 240 KB packets | Fewer misses on absence checks · test cost only | Move the evidence position and measure miss rate at fixed content |
| Classical statistics baselines (remembered): KM vs complex survival, empirical-Bayes shrinkage and funnel plots for providers, case-mix adjustment, FDR control, out-of-time calibration and decision-curve analysis for predictors | Registered operators in `analysis_tools.ANALYTICAL` | Prevents unearned model complexity; honest uncertainty · low cost | Each operator must beat its simple baseline out-of-time, or the baseline is reported instead |
| Learned routing (RouteLLM, brief-cited) | `tier_router` / `cascade_router` | Needs independent outcome labels first · circular today | Defer; E5 supplies role-level outcomes |
| DSPy-style prompt optimization (brief-cited) | Manual `RULES` / `SYSTEM` | Only after the metamorphic suite exists, else overfits | Defer |
| SGLang / prefix caching (brief-cited) | Local MLX serving | Only if a measured serving bottleneck remains after fan-out shrinks | Profile local inference share first |
| Metamorphic testing, cross-modal concordance (remembered / **[Idea]**) | Label-free evaluation | Cheap, runnable now | E2, E8 |

---

## 13. Full cost

### 13.1 Accounting
| Bucket | Known | Unknown or unpriced |
|---|---|---|
| Integrated cold run (Claude via subscription, API-equivalent) | ≥ $9.8446 | 4 unpriced context rejections; internal CLI recovery requests; split between Claude and third-party not reported |
| Failed repair | $0.4789102 | Whatever the failure left unmetered |
| Owned continuation | $2.718297068, of which third-party $0.174670218 (6.4%); about $2.54 Claude-ledgered; 281 ledgered transport requests | Internal CLI requests beyond the transport count |
| Cumulative on this workflow | ≥ **$13.041780032** | Preserved failures are unknown, not zero |
| Baselines (separate) | Haiku-only $3.8188; Sonnet-only $11.0814 (invalid) | Not part of the workflow total |
| Review production | — | The first failed review and the second local receipt-storage failure are preserved. Costs are unknown, not zero |
| Local compute | — | The 600 s SLM timeout, the 62 s bottled decline, MLX memory and lock contention. Unpriced |
| Subscription allocation | — | Marginal dollar cost is $0, but the plan limit stops work, which is a real opportunity cost |
| Engineering, test maintenance, identity-hash invalidation churn | — | Not in the ledger |
| Training and adapter build | None overnight | Prior runs not supplied |
| Clinical review time | — | Unpriced, and not requested here |

### 13.2 Cost per complete-correct answer
Currently there are zero complete-correct answers, so the ratio is undefined. Report the numerator (all attempts, failures included) separately and never substitute a favorable denominator.

### 13.3 A model to fill in (inputs from E1)
```
C_request = C_plan + C_sql
          + N_pairs × (b̄ × c_judge + p_esc × c_haiku + p_esc2 × c_sonnet)
          + C_compose + C_verify × (1 − reuse_rate)
          + C_deliverable(0 if rendered in code)
C_bottle  = C_build + C_failed_builds + Σ C_exec + Σ C_fallback
N*        = C_build / (C_fresh − C_exec)       # break-even, with C_exec > 0
```
Quality must hold before savings count. The paper's own headline savings, with many runs losing quality, are not evidence that an Agere bottle will be accurate or economical.

---

## 14. Falsifiable experiments

None of these has been run. Spend is not capped by this review. Original cohort and frozen data are not edited. Use scratch copies and the same source family boundaries.

| ID | Question | Procedure | Decision rule |
|---|---|---|---|
| **E1** | Where does the cost go? | Analyze existing ledgers and receipts by `component()` label (offline, no provider calls). Include `model_escalations`, `document_field_repair`, `document_span_repair`, actor turns, support checks | Falsifies "the actor loop dominates Claude spend" or "document reading dominated the cold run". Sets bottling priority |
| **E2** | Does the reader attribute entities correctly? | Metamorphic suite on scratch copies (swap, move, negate, experiencer, date shift, duplicate) against the current block×field path and an entity-bound path | Entity-bound must pass swap/move tests that block×field fails, with no loss on other classes |
| **E3** | Does `review_final` detect seeded faults? | Inject wrong-domain count, wrong-class reason, dropped role, range-vs-max conflation and quote flood into copies of the saved partial answer. Run `check_support_packets` | Detection rate per fault class and false-reject rate on clean copies. A low rate confirms "too weak" |
| **E4** | Does anchored candidate generation lose evidence? | Compare recall of lexical + neighbor windows (±dense, ±rerank, chronological) against the saved exhaustive observations as a **machine reference, not clinical truth**, per relation type | Retire dense ranking if recall is unchanged. Keep an exhaustive sweep for any relation type where recall is lower |
| **E5** | Which rung per role? Is the cheap-first ladder right? | (i) Role-boundary paired tests: Haiku vs Sonnet vs Jev vs Perplexity on identical inputs (planning, SQL authoring, relation adjudication, composition). (ii) A fixed-harness Sonnet-only whole-workflow arm beside Haiku-first and relation-first, with a pre-declared rubric. Include actor `effort` as a factor | Choose the cheapest rung that matches the strongest rung's invariant and metamorphic pass on that role |
| **E6** | Does packet identity defeat reuse? | `support_packets` is a pure function. Hash packets across consecutive saved review rounds offline, before and after a redesign that drops global context and tool digests from patient packets | If ~0% of patient packets are byte-identical today and most are after the change, F5 is confirmed |
| **E7** | Does a parameterized program transfer? | See §10.5 | Pre-registered |
| **E8** | Cross-modal concordance as label-free reader evaluation | Compare note-stated current use with structured fills for the same patient-drug pairs, per document family | Treat discordance as triage. If the rate differs sharply by family, investigate that family's reader |
| **E9** | Does the local-reader rung pay? | Run with and without `local_readers_then_recheck` on the same pending fields | Expect no accuracy gain and added latency. If it adds detections as an independent vote, keep it in that role only |
| **E10** | Does a naive distilled relation student match the judge? | See §10.5 | Report fidelity and coverage at matched precision, with family-held-out calibration |

---

## 15. Ordered next steps (by expected product impact)

1. **E1 plus a per-run configuration manifest.** Offline and free. It sets priorities and ends the ambiguity about which flags ran.
2. **Trace failure 2 end to end** for the one known instance (reader output → actor SQL/JSON path → final cell). It decides whether the fix is in the reader or in composition.
3. **Claim IR with domain, plus enum-rendered dose statuses.** Highest-value deterministic guard (failures 1 and 4).
4. **Entity-bound relation facts** from `review_documents`, replacing `value=quote`. Add a clinician relationship table that fuses `care_team` and letter roles, and show it in the actor state (failures 2, 3 and 5). Fix the name-normalization brittleness.
5. **Verification re-scoping:** claim-level, content-addressed units, per-claim reasons, parallel execution, seeded-fault monitor (E3, E6).
6. **Transport honesty and the control arm:** honor or neutralize `tool_choice` in the emulation; set effort per role by experiment; run the fixed-harness Sonnet-only arm (E5).
7. **Route compound and novel questions directly to the investigator.** Disable `bottled_sql` and planning-SLM attempts on that path. Keep deterministic and known-shape routes unchanged.
8. **Retire dense ranking and the local-reader rung in exhaustive mode**, unless E4 or E9 shows they pay.
9. **Parameterized program reuse (E7),** then the unfamiliar compositions across admissions, care gaps and social context, ACO and financial priorities, and clinician and hospital performance. HF, diabetes and respiratory remain regressions.
10. **Learning hygiene:** scope corrections to the implicated role, narrow identity hashes, add accuracy-regression checks to `_finish`, calibrate by family. Hold any new training until E2 and E3 exist.

---

## 16. What would justify selling a declared MVP, and what stays unknown

### 16.1 Declared scope
Governed population analytics, document-grounded gap and worklist workflows with supported unknowns, and code-built deliverables. State what it does not do: unvalidated prediction, causal claims, clinical decisions and real-care execution.

### 16.2 Finite evidence package
1. **Breadth:** each of the ten acceptance areas demonstrated on unfamiliar compositions through the same path, with deterministic invariants passing (membership, grain, domain, units, quote and offset binding, as-of visibility).
2. **Relations:** metamorphic and cross-modal results per relation type (E2, E8), and verifier detection rates by seeded-fault class (E3).
3. **Review of new outputs:** a pre-declared rubric applied by reviewers whom Mike designates, to a stratified sample of **new** outputs per family. Report per-claim-type error rates with exact binomial intervals and cluster by patient. With zero observed errors in about 59 independent items, the 95% upper bound is about 5% (rule of three). Items clustered within patients carry less information than the count suggests.
4. **Cost and latency:** all-attempt cost per complete-correct answer and a latency distribution, with unknown-cost items listed as unknown.
5. **Honesty:** the rate and causes of supported unknowns, abstentions and partials, and no unknown converted to a negative.
6. **Reproducibility:** repeated runs produce the same deterministic columns, with the typed columns varying only within documented bounds.

### 16.3 Still unknown
- Behavior on real, non-synthetic notes (OCR noise, template diversity, copy-forward, abbreviations).
- Clinical validity of the recommendations, guideline applicability and reference-dose handling in edge cases.
- Predictive and causal operator validity outside synthetic data.
- Drift and long-term maintenance cost.
- Transport parity: results come from an emulated, test-only subscription path.
- Throughput and cost at real scale.
- Security, privacy, regulatory status and liability. These are external prerequisites for qualified people to assess.

---

## 17. Closing note

This review is advisory. It approves nothing, proposes no deployment or training, and no experiment above has been run.

For an implementation turn, the gate / workflow change / evidence / remaining gap line would read:
- **Gate:** unchanged.
- **Workflow change:** none.
- **Evidence:** this review.
- **Remaining gap:** the §15 steps 1–6.
