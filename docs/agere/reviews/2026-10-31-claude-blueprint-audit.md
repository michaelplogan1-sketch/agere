# Independent review: blueprint conformance and the path to a finite MVP

I ran nothing, and I read only the files and receipts in this packet. Tags used below:

- **[C]** is visible in the supplied code.
- **[T]** is visible in the supplied tool trace.
- **[R]** is a reported receipt or measurement.
- **[H]** is my hypothesis.
- **[U]** is unavailable and needs the source named in section F.

## Verdict

Mike's instinct to step back is right, but the step back should be a short offline analysis plus about five targeted shared fixes, followed by one confirmatory run. The architecture does not need a rewrite, and neither the warehouse nor "weak small models" is the primary cause. The packet shows four things:

1. The active path for the original question is a free-form tool loop (`backstop_agent`) doing the orchestration the blueprint assigns to a spec-and-compile path.
2. Three contracts are left to the actor's improvisation: time-window semantics, relation/entity vocabulary, and tool ergonomics.
3. The final verifier cannot reach `answered` while any dependency check returns unknown.
4. The control file shows every gate except G5 as accepted, yet the north-star workflow is still partial. G5 is blocked on labels engineering cannot produce.

Codex's six proposals are mostly right in direction. They miss the concrete causes in sections B and C, and Codex's #4 (completeness gates blocking reuse) is confirmed by the code in D.

## A. Blueprint-to-active-path mapping

| Blueprint element | What the active original-question path does | Assessment |
|---|---|---|
| Governed definitions and deterministic compute | SQL over definition views and macros, `result_assembly.compose`/`measure`, `materialize_table`, code-built prose bindings [C][T] | Matches. 228 pillar values matched the source-checked check [R]. One semantic gap: see C1. |
| Haiku writes a spec; a compiler executes | Not used for this question. The trace is a 60-call `backstop_agent` loop [T]. The recovery runs forced `('backstop_agent',)` in `Cascade.order` [C]. | **Largest deviation.** I cannot see why the compile or spec path declined the ordinary run [U]. |
| System One closed-question judgments | Heavily used: block×field decisions, relation axes, claim-support checks [C] | Matches. Volume is the issue (C3). |
| Sonnet investigates | Haiku starts; Sonnet is escalation only (`model_policy`, Mike's 10-24 instruction) [C] | The 10-26 CTO document ("Sonnet investigates") is stale against the code and Mike's earlier instruction. Fix the document, not the code. |
| Local SLM/encoder/reranker "where qualified" | `AGERE_DOCUMENT_LOCAL_READERS` defaults empty; ranking only reorders (C3); bottled SQL declined in the earlier cold run [C][R] | Effectively unexercised. Acceptable under "where qualified", but ranking is pure cost here. |
| Typed assertions, state and reconciliation | Findings carry entity, relation, polarity, temporality and subject. `reconcile()` produces `documentary_state_json` [C][T]. The final composition joins only `source_findings_json` and `source_clinicians_json`. | **Second-largest deviation.** The state layer is computed, then bypassed. `patient_evidence.review_cards.reasons()` re-groups raw findings by string match. |
| Render from saved results; Opus for deliverables only | Code rendering, 0 model calls [R] | Matches, with the dispatch problem in B. |
| Shared decision context, work, follow-up | Sealed engineering operations [R] | Not exercised in this path as far as the packet shows [U]. |
| Guarded reuse, bottling, learning | Recursive improvement is off by default (`harness_improvement.enabled`). The source-observation cache and owned recovery are manual or per-key [C]. | Only the exact-key source cache has demonstrated value. |

**Largest deviations, in order:**

1. The actor, not a compiler, owns orchestration. Mid-run window changes are propagated by the model's discipline across a lexical `agere_result_N` graph.
2. Typed state is produced but not consumed. The renderer and the actor each reinvent vocabulary and grouping.
3. The control plane marks the flagship path accepted, while G5, a label-blocked model gate, is "active".
4. Dispatch to the clinical report is HF-shaped (see B).

**Code that already solves problems the previous review thought missing:**

- **Role/collection visibility.** `semantic_analyst.collection_manifest` and `actor_result` now expose `*_json` collections. `RULES` instructs the `items_from` collect join. `collection_retention_issues` enforces it. The trace shows the actor doing exactly this at calls 26, 49 and 55 [T]. The remaining weakness is the regex extractor in `correspondence_roles`. The equality-of-names bug described earlier is not in the supplied function.
- **Domain and denominator binding.** `result_assembly.measure` plus `bind_computed_prose` bind counts to population, domain, grain and unit. The gap is that `measure` is optional ("You may add measure"). `AGERE_QUANTITATIVE_ROLE_CHECK` is off by default.
- **Claim-level verification feedback.** `localize_support_failure` returns field, path, label and source record IDs, and `execution_state.messages` forwards them. It only runs after a packet fails.
- **Parallel support checks.** `check_support_packets` runs a thread pool (default 8) after the summary packet.
- **Global-summary packet coupling.** The old coupling is gone. Patient packets now carry `global_claims_scope`, not the summary.
- **Closed-set document selection.** `span_repair` and `typed_document_review` copy bytes from source; no model writes quotes.

## B. Is this the best way to finish? Codex's six proposals, challenged

**#1, stop broad runs and reconcile the blueprint: agree, with a caveat.** Do it as computed checks, not another document, per `AGENTS.md`. Two things should be recorded in the decision log:

- The reassessment rule has effectively triggered. Recent increments fixed real defects (recovery, stale-field detection, report projection), yet the ordinary run is still partial.
- G3 and G6 are "accepted" while the original compound question, which Mike called table stakes, is not. Either add an explicit gate for the ordinary original-question path or reopen G3. Do not keep G5 as the driver for delivery work.

**#2, typed assertions as input to deterministic composition: agree, but this needs no new layer.** The shared changes belong to existing owners:

- **Relation and entity vocabulary → `document_review`.** `validate_questions` currently accepts any `^[a-z][a-z0-9_]*$` relation and any registered or group entity ID. The renderer then hard-codes spellings. `patient_evidence.review_cards` filters on `{'treatment_reason','reason_not_on_therapy','not_prescribed_reason','documented_reason_not_on_therapy'}`. It also hard-codes pillar entity IDs (`hf_evidence_based_beta_blocker`), while the first review in the trace used `beta_blocker` [T]. This is an alias catalog in disguise, and it is the same mechanism behind the 116-page fallback. The fix is a small closed registry of relation kinds. Each kind carries a meaning and a canonical question text. Entities resolve through `drug_classes.CLASSES/GROUPS` membership, not string equality. An explicit `exploratory` kind renders generically. This also stabilizes the source-observation cache key, which currently includes the actor's wording and alias lists (`cached_source_judge`) [C].
- **State consumption → `patient_evidence.review_cards`.** It should read `documentary_state_json` groups, not regroup quotes.
- **Composition → `result_assembly`.** A hand-typed `domain` value list (as in the trace's five drug keys) should optionally accept a registered group name resolved by code.

Do not add a dependency database. Column-level lineage already exists (`group_query_issues.consumed_columns`, `native_result_dependencies`). The one missing dependency edge is the scope of a source review. A joined `review_documents` result whose `field_patient_queries` derive from a superseded flag view should raise the same stale-field error. Review 1 here was scoped from all-history flags (`agere_result_19`), and review 2 from the 12-month view (`agere_result_42`) [T].

**#3, one connected path, then a few other-domain compositions: agree.** The other compositions should exercise the same mechanisms rather than add domains: focused entity-relation review, a changed window, collection retention. Candidates are a readmission reason from notes plus structured utilization, a social-barrier relation against missed visits, and a provider/contract-clause table. Use independent public-source SQL for the structured parts. Do not use protected keys.

**#4, reusable workplans and completeness gates: the answer is yes, they wrongly block.**

- `backstop_agent.answer` only calls `attempt_propose` when `status=='answered'` and the last review is `confirmed`.
- `review_final` confirms only if coverage is `answers_fully`, support is `supported` with a decider, and there is no feedback. A packet whose typed check returns unknown becomes "unresolved" feedback sent back to the actor ("an uncertain verifier is not a supported check") [C].
- So any single unknown dependency check prevents `answered`, and it also prevents candidate creation. An answer with honest, source-supported unknowns is never eligible for reuse.
- **Policy change:** classify verification outcomes as contradicted/not-established (blocks), uncertain-after-both-providers (annotate the cell, do not block), and supported. Ship `answered` with per-cell unknown markers. This is not a loosening of accuracy, because unknowns stay visible. It needs validation against P0 item 3 below before coding.

**#5, keep local models and measure roles; don't switch to stronger-first: agree.** `model_policy.ladder(role)` ignores `role` [C], so the ladder is role-blind. Don't change defaults without paired data. One cheap offline check: compare turn counts for the Haiku-only (31.5 min), Sonnet-only (17.6 min) and integrated arms from existing receipts. If Sonnet needs far fewer turns, Haiku-first costs time without saving much money. That is an observation to make, not a policy change yet.

**#6, curate deliverables from a stable typed contract: agree, and the current renderer is HF-shaped.**

- `patient_report_pdf.render` dispatches to the clinical template only if every card has exactly four therapies, a "Heart failure evidence" context row, and boolean fill status [C]. `review_cards` holds alias maps (`on_rasi_pillar` ← `on_raas_inhibitor`…).
- `_render_clinical` hard-codes "HFrEF", "GDMT titration program" and "four-pillar" copy. Anything that fails the shape test falls through to the generic path. That fallthrough is the 116-page, 31.9 MB behavior.
- **Fix:** have the template declare required slots, and check them at compile time in `review_final`, via the existing `workflow_contract.validate`. The actor then sees "missing slot on_mra_pillar" and can repair. Render failures should be loud, not a silent generic fallback. If this template stays HF-specific, label it as a registered program-review template, not as general delivery.

**Stop now:**

- Repeated ordinary or continuation runs for confirmation.
- Recovery-test repetition (82 checks already pass).
- Adding aliases to the report projection.
- Whole-question bottled SQL on compound questions. It cost 62 s to decline in the earlier cold run [R].
- Any further model-catalog or training work.
- Treating G5 as the driver of delivery work.

**Keep:** the semantic layer and governed macros, `compose_table`/`measure`, the focused review with entity precheck, the source-observation cache and owned recovery, the code-rendered deliverables, and the `group_query_issues` family of compiler checks.

### Additional code defects (not in Codex's list)

- **Latent `NameError`.** `backstop_agent.py` uses `sqlite3.Error` in exception clauses in `review_final` and `check_support_packets`, but I see no `import sqlite3` in the supplied file. This only raises when an exception reaches those clauses, for example a failed observation quarantine. It would mask the original error.
- **Ambiguous `compose_table` error.** `result_assembly.compose` raises one message for four conditions: not collect, `items_from` not a string, column not found, or `columns` present. The trace shows calls 47 and 51 failing identically, each with `mode: collect`, a valid `items_from`, and a stray `columns` key. The actor fixed it at 49 and 52 [T]. Split the messages. Two wasted turns per run is small, but it recurs.
- **Ranking that cannot change membership.** `typed_document_review.read` calls `rank()` (UltraBERT plus reranker) on the focused path. `priority` only sorts decisions inside `source_batches`, and every decision is processed anyway [C]. `_RANK_LOCK` serializes it across the 3 patient workers. The effect is finding order only. Turning it off is cheap and its safety is testable: same findings, different order.

## C. Why is it still slow and partial after source reuse?

**What the data supports:**

- Continuation v2 used 503.5 s, of which about 47 s was the literal replay [R]. Zero Jev/Perplexity attempts and 17 Haiku attempts remained [R]. That is roughly 27 s per Haiku turn. This is the hard floor of actor overhead on the subscription transport [R, derived].
- The 60-call ordinary trace had at most about 62 actor turns [T]. If turns cost similar time, about 28 of the 46 minutes would be actor turns [H, derived]. Verification and source reads would account for the remaining ~18 minutes [H].
- Claude is about 2.8% of attempts but about 50% of known cost in continuation v1 (36 of 1,296 attempts; ~$0.29 of ~$0.57) [R]. Third-party cost is about $0.0002 per attempt. High provider attempt counts are cheap in dollars. Turns are what matter for time and Claude cost.
- The 46-minute ordinary run was warm: 10,918 prior ledger rows [R]. It is neither a cold estimate nor a clean warm estimate. The cache hit count is not in the packet [U].

**What the trace shows that nobody has named:**

- **C1. A governed-window gap, then a rebuild.**
  - The decision context says "on" a drug means a picked-up fill in the last 12 months.
  - The actor used `{{drug_fill:…}}`, which the compiler later treats as all-history (`group_query_issues` temporal check) [C].
  - The window was corrected at call 40 [T]. Roughly the first 40 of 60 calls built flags, narrative review scope, doses and plan on the stale basis [T].
  - The temporal check is a good post-hoc guard. But it fires at final review, after review 1 was already scoped by stale flags (`field_patient_queries` on `agere_result_19`) and a second source review was needed.
  - The final answer's table lineage is not in the packet, so I cannot say which composition (call 52 → `agere_result_51` with a plan from the all-history `agere_result_31`, or call 55 → `agere_result_54` with a plan from the corrected `agere_result_52`) was bound last [U].
  - I need `general_analysis._expand` and the macro definitions to decide whether `drug_fill` should default to the governed window with an explicit "ever" variant.
- **C2. Documentation fetch loop: 18 of 60 calls.**
  - They are `SELECT … FROM agere_data_model_context WHERE section_id IN (…)`. doc_102 is requested 12 times and doc_103 13 times.
  - The IN-lists shrink (5, 4, 4, 3, 4, 3, 1 sections) at calls 1–7 [T].
  - That pattern is consistent with preview truncation. `actor_result` shows only a few rows and about 10 KB when a result exceeds 12,000 characters, and long documentation text hits that [C][H].
  - Preloading the sections a question's macros need, or a full-text single-section read exempt from preview truncation, removes about 30% of calls. Confirm with `analyst_context.install` and the section text [U].
- **C3. Reading volume has avoidable structure.**
  - The status fields (2,151 positives) have no `field_patient_queries`, so they cover all 57 patients' focused documents [T].
  - In continuation v1, Jev attempts (611) and Perplexity attempts (649) are nearly 1:1 [R]. If Jev rarely meets the 0.8 acceptance bar, nearly every decision pays two providers [H]. Per-decision `decided_by` would settle this [U].
  - `document_review.review` runs 3 patient workers, and the batches within a patient are serial [C]. Ranking (above) adds a lock.
- **C4. Free-form field declarations.**
  - Four field IDs changed question, relation or entity between the two reviews [R][T]. For example `raas_not_on_reason` changed from relation `not_prescribed_reason` to `documented_reason_not_on_therapy`, and `beta_blocker` became `hf_evidence_based_beta_blocker`.
  - Any wording change misses the cache (`cached_source_judge` keys the full question object) [C]. So the "reuse" worked for continuation but could not work within the run.
  - The fix that stops ID drift removes a correctness hazard. It does not remove the waste. The relation registry in B does.
- **C5. Quote containers versus typed content.**
  - Findings are quote spans with typed axes: `value == quote`, plus entity, relation, polarity, temporality, subject [C].
  - That is sufficient for display, but not for aggregation such as "how many were hyperkalemia". A closed `reason_category` axis would be cheap (86 reason findings). It is not on the critical path. Defer.
- **C6. Verification locality.**
  - `support_packets` fills a patient's `evidence` with every observed row for that patient from every executed call, including superseded all-history flags and the superseded review. `native_calculation_contracts` follows the same closure.
  - The verifier therefore sees stale and current facts side by side. `local_shared` is closure-limited, but `by[pid]` is not [C].
  - Packet size is recorded in `evidence_coverage` (`max_dependency_bytes`, `total_dependency_bytes`), but those values are not in the packet [U].
  - The fix is to build patient packets from the dependency closure of the final row's cells, not from all rows ever seen for the patient. I would verify the premise first (P0 item 3).
- **C7. Transport.**
  - Per the supplied notes, the subscription path flattens system blocks, drops `cache_control`, and previously ignored `tool_choice` and effort [R].
  - Per-turn latency, message bytes and output tokens are available in `actor_prompt_receipts` and the ledger. An offline regression will say whether latency tracks prompt size, output tokens or fixed overhead [U].

**Why partial?** [H] The most probable cause is the verifier gating above (unknown support never confirms, so the actor loops until `no_progress`). I can't confirm this without `answer_reviews[-1]`. That is the single most decisive missing observation.

**What I would not attribute:** the 228/228 pillar match and the unchanged source argue against the warehouse. Haiku repeated an identical failing call and re-fetched documents 12+ times, which looks like tool-contract design more than model weakness. A paired Sonnet probe could test that, but offline turn-count comparison comes first.

## D. Does recursive improvement or bottling earn its keep?

**Operational caches versus model-weight learning (the former only):**

- **Source-observation cache (`cached_source_judge`, `span_repair` cache).** Demonstrated: the continuation restored 86 reason and 2,151 status observations with zero new Jev/Perplexity attempts [R]. Dollar value is trivial (~$0.0002 per attempt); time value is moderate. Its reuse is limited by C4: keyed on the actor's wording, so within-request reuse fails.
- **Owned recovery.** It replays recorded literal actions (61 restored in 46.8 s [R]). It is useful continuity, not transfer. The new "expected pure error" handling in `recover_actions` is correct.
- **Candidate selection (`harness_improvement`).**
  - It is off by default (`enabled()`).
  - `identity()` includes the full code hash, a global `physician_feedback_revision` and the exact question [C]. Hits are near zero under daily edits [H].
  - It can only trigger from an `answered`, confirmed run, which this workflow never reaches (B #4).
  - It has contributed nothing here, by construction.
- **Bottled SQL.** It targets single-statement answers. This workflow is a multi-source tool trajectory. The expected outcome is decline plus latency.
- **Model-weight learning.** None occurred in this increment [R].
- **`component_learning.observe`.** The supplied packet does not include it, so I cannot assess the label-source or invalidation-scope concerns raised earlier [U].

**Shortest safe route to demonstrated reuse and transfer:**

1. Make the successful composition into a parameterized skeleton, after one clean ordinary run. Parameters are the cohort query, entity bindings from the relation registry, relations, and the window. Fixed steps are flags, doses, care team, focused reviews, compose and plan.
2. Re-run it for a different condition's cohort (for example a diabetes cohort with its own medication classes) and compare actor turns, failures and time against cold Haiku on the same cohort. Do the same for one change-of-window recompute (W3 in E).
3. A cached source reading transfers across questions only for the same patient and source documents. Transfer across populations is a plan-level property, not a cache property.
4. Claim savings only against a matched baseline, or label a single warm run as single and warm.

## E. A finite synthetic MVP acceptance boundary

This is a demonstration boundary. It does not narrow the ten-area charter; only Mike can do that.

**Critical engineering (executable by Codex, no new labels):**

- **W1. Ordinary original question, terminal state.**
  - Terminal state is `answered` with per-cell unknown markers allowed. Time and cost are recorded, not capped.
  - The 57-patient cohort and all 228 fill cells match independent SQL under the governed 12-month window.
  - Every joined field's lineage is consistent with that window (compiler passes).
  - Every narrative cell is a bound finding or an explicit supported unknown.
  - Dose evidence is not conflated with tolerated maxima.
  - Dated clinician objects are retained.
- **W2. Replay.** An identical re-ask returns the same table with zero new provider calls.
- **W3. Definition change.** Change the window (12 to 6 months). Only dependent fields recompute, unchanged readings are reused, and no stale-lineage error remains.
- **W4. Two other compositions** through the same path (domains in B #3), each either `answered` or an honest unknown, passing the same compiler checks.
- **W5. Deliverables.** The report and interactive artifact render from the saved result for W1 and one W4. They fail loudly on missing slots. No technical companion.
- **W6. Decision-context bind.** Save and reopen, plus a follow-up work item. This is already sealed [R]; just keep it from regressing.

**Model/clinical qualification, separate:** reader accuracy, reason interpretation quality, and role-versus-baseline comparison (G5). None of this blocks engineering acceptance.

**External prerequisites, separate:** independent clinical review, live payer and EHR connectors, tenant security, and production authority.

**Later research:** causal designs, forecasting, learned adapters and distillation. They remain registered operators, not W1–W6 dependencies.

A supported unknown is a pass. Missing wiring is not. A synthetic pass is not clinical or superiority evidence.

## F. Prioritized plan, checks and stop conditions

**P0, offline, existing receipts only (no provider calls).** These resolve whether the 46 minutes is actor turns or verification:

1. Ledger groupby by component tag, provider, model and `decided_by` with wall seconds, for the ordinary run.
2. Per-turn actor duration versus `message_bytes` and output tokens from `actor_prompt_receipts` and the ledger.
3. The final `answer_reviews` (status, feedback, `support.packets` labels, `evidence_coverage`) and which `result_view` the final table bound.
4. Cascade stage timeline for the ordinary question (`proposers`, `answer_timing`, `stage_diagnostics`), to learn why the compile path did not answer and how much time preceded the backstop.

**P1, tool-contract fixes (small, with a user-visible effect):**

- Split the `compose_table` error messages.
- Fix the documentation delivery (preload or full-text read).
- Add an early window lint at `query_sql` time. It is a call to the existing temporal check, so a stale `drug_fill` window is caught before 30 dependent calls.
- Turn ranking off on the focused path.
- Add `import sqlite3` to `backstop_agent.py`.
- Record and validate field scope lineage for `review_documents`.

**P2, the relation/entity registry and consumption.** Registry in `document_review`, entity resolution via `drug_classes`, `review_cards` reading `documentary_state_json`, and slot checks for the template. This removes the alias fallback class and stabilizes cache keys.

**P3, conditional on P0 item 3.** Dependency-closure patient packets, and the per-cell unknown policy (`answered` with markers). Do not code this before confirming the cause of partial.

**P4, optional.** Review worker count (`ThreadPoolExecutor(max_workers=3)`), after checking provider capacity. Provider-order review if Perplexity fires on more than ~90% of decisions.

**P5, one confirmatory ordinary run** (declared warm or cold), then W3, W4, W5, and then packaging.

**Check before and after P1/P2** (offline replays where possible):

- Same pillar cells and findings, different order only.
- Actor turn count, the number of doc fetches (expect ~0), and compose errors (expect 0).
- No 116-page fallback.

**Stop and reassess if:**

- Two consecutive increments produce no change in terminal status or actor turns (per `AGENTS.md`).
- P0 shows the time is dominated by something other than actor turns, verification, or reading volume.
- After P1 and P2, a clean run still cannot reach `answered`, or the compile path cannot express the pattern. Then register `review_documents`/`compose_table` as graph operators for the existing compile path rather than keeping the free-form loop. That is my fallback structural change; it needs the sources below.
- Any provider run is proposed before P0 and P1 are done.

**Additional source I would need, and the decision each resolves:**

1. `general_analysis._expand` and the macro definitions for `drug_fill`/`current_dose`: whether the macro window needs a governed default.
2. `analyst_context.install` and `semantic_docs.schema_doc` text for doc_102/103: whether the fetch loop is truncation.
3. `analysis_graph` operator registry and `proposers.tier_spec_path`/`shared_planner._compose`: whether the compile path can express focused review and compose.
4. The listing of `drug_classes.CLASSES`/`GROUPS`: the entity IDs for registry canonicalization.
5. `component_learning.observe`: only if learning invalidation is brought back in scope.

## Limits of this review

- I did not see the final answer, the cascade log, per-component time, or the cache hit rate for the ordinary run.
- I did not verify any clinical content or run any code.
- All cost figures are API-equivalent estimates from your receipts.
- The turn-time estimate in C is derived from one interrupted run (17 turns) and one trace, not a controlled measurement.
- The Jev/Perplexity fall-through, the preview-truncation explanation for the fetch loop, and the cause of the partial status are hypotheses until P0.
