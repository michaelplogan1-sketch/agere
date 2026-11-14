# Revised review: finishing the original workflow, then testing transfer

I ran nothing. Tags: **[C]** supplied code, **[R]** reported receipt or measurement, **[D]** my arithmetic on supplied numbers, **[H]** hypothesis, **[U]** unknown. I have not read `BUILD_OWNER.json` or `AGERE_BUILD_CONTROL.json`. The increments below must be filed under whatever gate those name.

## 0. What I retract

| Earlier claim | Status after your corrections |
|---|---|
| Partial status was probably verifier-unknown gating | **Retracted.** All 8 reviews were `repair/deterministic_compiler`, and paid support checking was never reached [R]. The per-cell unknown policy has no evidence from this run. It is deferred, not cancelled. |
| Turn ranking off | **Moot.** It is already off [R]. |
| Recursive improvement is off, so it contributed nothing | **Wrong premise.** It is ON. It contributed nothing because no qualified artifact existed for this question [R]. |
| About 28 of 46 minutes are actor turns, with a 27 s per-turn floor | **Retracted.** Actor callbacks total 1068 s, about 40% of the 2681 s backstop stage [R][D]. Turn time tracks output tokens (r=0.97) and only weakly message bytes (r=0.54) [R]. |
| Doc loop is truncation, removable for about 30% of calls | **Revised in §2, item 4.** The upper bound is lower than I said. |

**Where the 2681 s went.** About 1068 s were actor callbacks. About 1613 s were everything else, including SQL, two source reviews, and 8 compiler reviews. The 47 source-span calls sum to 1506 s but ran concurrently, so wall time is lower. That wall time is not attributed [U]. This is the largest unexplained block, and it needs an offline ledger attribution before any speed claim.

**What the evidence now supports.**
- The terminal failure was stale-window lineage. `agere_result_9` (all-history `drug_fill`) fed the final binding `agere_result_51` through the plan [R].
- The actor fixed the window around call 40 and still could not clear the lineage in 8 identical repair reviews. That is a no-progress loop on a deterministic check, not a model-capability result.
- Source repair cost $1.71 against $0.87 for the actor. Source work scoped by a stale cohort is therefore the expensive form of the error [R].

## 1. Answers to your eight points

### Item 1: unknown projection

Agreed. I would build it only if a later run is blocked by verifier uncertainty.

If built, keep two cell states distinct:
- **Source-fact unknown:** no documentation found.
- **Unresolved verification:** a reading exists but is unconfirmed.

In both states the claim is withheld. The unaccepted reading goes in a separate field, for example `unaccepted_reading_json`, and never into the value column. The 35 quarantined titration positives (26 frontier + 9 typed) must reach composition in that form, not as positives [R].

### Items 2 and 7: runtime flags and recovery

Taken as stated. The 61-action zero-provider preflight is a **test fixture**, and I would use it as one. Replaying the recorded actions through the new lint (§3, I1) is a free executable acceptance check. It is not evidence of learned-program selection and should not be counted as gate progress. The interrupted 17-Haiku continuation shows nothing about speed.

### Item 3: timing

This is covered in §0. Source repair is the larger Claude cost. The first offline step is to attribute the 47 span calls and the 1613 s to review 1 (stale-scoped), review 2, or other work.

### Item 4: documentation retention

**Your fix is the right shared owner, with three constraints.**

**a. Retain from native executed rows, not from `actor_result` previews.**
- Query 1 returned 5 rows but the preview showed 2 [R].
- `catalog()` currently drops these rows. `computed_rows` survives only for results under 2500 serialized bytes with no `patient_id` [C].
- Retaining a section requires `section_id`, `sha256` and `documentation_text`, with `sha256(text)` matching. That makes a truncated or substr'd read ineligible by construction.
- Dedupe on `(section_id, sha256)`. Key the store to `semantic._local.documentation_identity`, which `install` already sets [C].
- On a changed sha for the same section, replace and flag. Never merge.
- Replace the duplicate `computed_rows` for those result views with section-ID pointers.

**b. Use a size cap, because the data allows it.**

I solved the per-query byte totals as simultaneous equations. They are overdetermined and consistent [D]:

| Section | Bytes |
|---|---|
| doc_15 | 670 |
| doc_22 | 871 |
| doc_29 | 1890 |
| doc_44 | 811 |
| doc_45 | 1707 |
| doc_46 | 408 |
| doc_67 | 194 |
| doc_72 | 2826 |
| doc_102 | 2057 |
| doc_103 | 5736 |
| doc_104 | 1730 |
| doc_82 | 31,661 |

The eleven small sections total about 18.9 KB. doc_82 was fetched once and should be retained by reference only (a per-section cap of about 8 KB, my proposal). Lazy retention then grows to at most about 19 KB resident.

**c. Don't promise a large speedup.**
- Under native-row retention, 12 of the 18 doc queries (indices 5, 6, 7, 9, 25, 32, 35, 36, 37, 39, 42, 48) request only already-loaded sections [D].
- That is at most 12 of 69 actor turns, roughly 160 s at the 13 s median. Call that about 6% of wall time.
- It is a reliability and hygiene fix. The bigger prize is in item 5.

**Should owner definitions stay resident separately? Yes, pinned.**
- `HEADINGS` makes `CLINICAL OWNER DEFINITIONS` and `GOVERNED SQL MACROS` indexed sections, not prefix [C].
- With `AGERE_ANALYST_CONTEXT=discover`, they are lazily fetched [R].
- doc_102 and doc_103 were fetched 12 and 13 times. I suspect they are those sections, but titles are [U].
- Pinning removes the first-fetch turn and the forgetting risk. It is keyed by section sha, so it stays versioned.

**The crucial caveat is that reading the documentation did not prevent the error.**
- The actor read the owner text about 25 times and still used all-history `drug_fill` [T][R].
- Retention therefore fixes cost and hygiene. It does not fix the window. That needs a deterministic guard (item 5).

### Item 5: temporal lint without changing macros

Agreed: leave `_macro_sql` and `drug_fill` semantics alone. All-history membership is legitimate for historical, comparison and financial uses.

Design the lint around **decision context, not macro text**:
- It fires only when the decision context asserts a current-state window and a result's expanded SQL uses `drug_fill` with no `event_date` predicate or `{{as_of}}` bound.
- It computes `window_basis` on the result view. The actor does not declare it.
- It is **advisory** at creation. The tool result carries the existing deterministic message plus the pinned owner sentence.
- It is **blocking** where cost multiplies: a `review_documents` call whose `field_patient_queries` or scope derives from such a view. That would have blocked the review-1 scoping (via `agere_result_19`) before any paid work [T].
- Allow an explicit override such as `scope_basis: historical_all_time` with a recorded reason. A labelled historical result can never feed a cell labelled current. The existing final check already enforces labelling.

Two further pieces are needed:
- **Feedback path.** `execution_state.messages` forwards only `status/feedback/reason/repair_kind`, plus coverage and unresolved dependencies [C]. If the compiler review carries a lineage path, it is dropped. The repair text names `agere_result_9` but not the chain 51←31←9 or the corrected counterpart. I need the 8 review dicts' key sets [U].
- **Missing function.** I have not seen `group_query_issues` [U]. A pure `temporal_basis(expanded_sql, context) -> {macro, all_history, date_predicate, columns}` must be callable on a single query. If the existing temporal check cannot be called without a final candidate, extracting this function is the one required change.

### Item 6: typed state, created versus consumed

Created is not consumed. `with_correspondence` produces `documentary_state_json`, but the final composition and `review_cards` join only `source_findings_json` and `source_clinicians_json`, and regroup quotes by string match [C]. Consumption must preserve three states:
- accepted
- unresolved prior reading
- contradicted

Composition uses accepted only and shows the rest as explicit unknown plus a separate reading. I cannot specify the `reconcile` output schema until I see it [U].

### Item 8: sticky escalation receipt

**I would reject it as a default now and keep a narrow form in reserve.**

- The observed failure was a deterministic lineage loop, not an established Haiku capability limit. Sonnet might loop the same way. The Haiku-only versus Sonnet-only times (31.5 vs 17.6 min) are unmatched [R].
- A continuation restores actions and feedback. Starting Haiku from restored state is cheaper reasoning on repaired state, not a restart from scratch.
- If I1 and I2 land and Haiku still stalls on the same feedback digest, a receipt is justified.

**The key must be** `(question_digest, role, rung, failure_signature)`, where the signature is `repair_kind` plus the feedback digest plus a stall count. Guards:
- The receipt expires when the checker, lint or tool-contract hash changes, because the failure may have been contract-shaped.
- It records what failed and how many times.
- It never applies to a fresh role or question, so Haiku-first is preserved elsewhere.
- Today `model_policy.ladder(role)` ignores `role` [C]. A role-keyed receipt needs that dimension, and `model_policy.py` is hash-covered (see §2).

## 2. Registry risks and cache provenance

**Canonicalization hazards.**
- The registry must be a **closed, reviewed table**. Each relation kind has an explicit definition and each alias is an auditable entry. It should reject unknown names with the valid list and never guess by name similarity.
- `not_prescribed_reason`, `documented_reason_not_on_therapy`, stopped-for-intolerance and contraindication are potentially different questions. Map only pairs a reviewer asserts identical.
- The cache identity must be the **resolved frozen class-ID set**, plus relation kind, polarity, temporality (current vs ever), subject and qualifier.
- `beta_blocker` is a strict superset of `hf_evidence_based_beta_blocker`, and `raas_inhibitor` is a different set from the pillar's. A note about stopping atenolol does not answer the evidence-based question. Never alias these. Reject `beta_blocker` as a pillar entity in program-review context.
- Reuse is exact-set only. Narrower-evidence-for-broader reuse needs per-drug evidence re-filtered by code. Defer it.

**Cache provenance.**
- The span cache key includes full-file hashes of `document_review.py`, `span_repair.py` and `model_policy.py` [R]. Editing any of them invalidates the whole cache. That is a full reread of roughly $1.71 and 1500 s of concurrent span repair.
- **Rule: I1 through I4 do not edit those three files.** `execution_state`, `analyst_context`, `semantic_analyst`, `backstop_agent`, `group_query_issues` and a new registry module sit outside the hash [C].
- Put canonicalization at the tool boundary. Look up the canonical key first, then the legacy key. On a legacy hit, write an alias pointer record to the existing entry. Check source digest and a reviewed equivalence entry. Do not re-derive the reading.
- If an edit to a hashed file is ever unavoidable, rekey only after proving that the old and new code build byte-identical provider requests for sampled cached decisions. Do that offline.
- Offline check before assuming a rename is free: does the key include `field_id`, the wording, or alias lists? From the earlier packet it includes the full question object [U]. Compute whether a field-ID rename alone changes the key.

## 3. Revised finite sequence

**S0: offline, zero provider calls.**
1. Attribute the 47 span calls and the 1613 s non-actor time to review 1 vs review 2 vs other.
2. Get the titles of doc_102 and doc_103 from `partition()` on the exact documentation text.
3. Read the 8 compiler review dicts' keys, including any lineage path.
4. Read the `spec_path` decline reason (it declined in 6.65 s) from stage diagnostics.
5. Check cache-key composition.
6. Replay the 69 compiled prompts with proposed retention and count satisfied fetches.

**I1, the first increment I would make: window basis before paid work.**
- It contains the pinned owner definition, the `temporal_basis` function, the advisory-at-creation lint, the blocking gate at `review_documents`, and lineage-path feedback.
- It targets the measured terminal failure and the most expensive rework.
- **Check:** replay the 61 recovered actions with zero provider calls. The lint must flag `agere_result_9` at creation, block the `agere_result_19`-scoped review, and pass the corrected 12-month path.
- Also check that it does not misfire on an explicit "ever" query (see I5).

**I2: loaded-documentation retention** (§1, item 4). Check by prompt replay: after first load, each fetched section appears byte-identical with a matching sha, and 12 of 18 fetches are avoidable.

**I3: minimal relation registry** for only the kinds this workflow uses, with the alias bridge above. Exact-set resolution, and reject-not-alias for mismatches.

**I4: `review_cards` consumes the typed state**, with unresolved readings separate. This depends on I3 and on the `reconcile` schema [U].

**I5: one bounded confirmatory ordinary run, then transfer.**
- Run the original workflow once, declared warm or cold. Record the terminal state, turns, doc fetches, compiler repairs and source calls.
- Then run W2 (replay, zero provider), W3 (window 12→6 months, dependent recompute only), and one non-HF composition, for example a diabetes class with its own window.
- The non-HF composition should run an "ever" variant next to the current-window one. That tests that the lint does not misfire on legitimate historical use.
- Do not add a second composition until this one passes.

**Deferred, with triggers:**
- Compile-path registration.
- The unknown-cell projection.
- The sticky escalation receipt.
- Dependency-closure patient packets.

**Stop and reassess** if two increments produce no change in terminal status or repair count. Also stop if the confirmatory run still cannot reach a terminal state with an actionable verdict, or if S0 shows the unexplained 1613 s is dominated by something other than source work. Record any reassessment in the control decision log.

## 4. Compile path: later, not now

- I1 through I4 are tool-contract and state fixes inside the existing loop. Do not wait for the compile path.
- The graph already has `review`, `sql` and `assertion_review` operators. The visible gap is that I have not seen an operator for `result_assembly.compose` or `measure` [U]. I also don't know why `spec_path` declined [U].
- **Trigger:** after one run reaches a terminal state, extract its action trace as a parameterized skeleton. That shows what must be expressible.
- **Likely precise requirement:** register `result_assembly.compose`/`measure` under the `CLINICAL_LOGIC` validate/execute/answer contract. Add a window-basis parameter for the lint.
- Doing this before I1 would encode the stale-window pattern into a compiled program.

## 5. Limits

- Everything here is from the supplied excerpts and receipts. I have not seen `group_query_issues`, `reconcile`, the review dicts, or the cache-key code.
- The per-section byte sizes in §1 item 4 are my derivation, not a measurement.
- The roughly 6% doc-turn estimate assumes native-row retention and that the actor would not re-fetch anyway. The actual effect needs the post-fix run.
- No clinical content was assessed.
