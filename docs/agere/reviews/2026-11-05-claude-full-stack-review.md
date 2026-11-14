Reading control files now. One thing has already changed since the brief: the planner-handoff run it describes as not yet done actually returned before the pause. I'll inspect that receipt next.

The correct answer (92 orgs, 505, 106) was reached after 5 calls, but about 95% of the cost went to a compiler rule demanding SQL bindings for literals. I'm now locating that rule in the code.

The earlier 220s partial run shows the same literal rule ("30", "1", "zero") in every repair round, even though the requirement ledger marks "30" and "2029" as literals from the question and the binder never checks them. I'm now looking at the clinical run and the release assessment.

Still reviewing: 57 new artifact directories in three days, none a plain-LLM baseline. Next I'm checking the commit cadence and the routing code.

Checking the bottling paper directly, then the learning results.

Both delays come from two compilers rejecting correct work, not from planner handoff: the typed planner's plan was right and got turned down over a quote-wording rule.

# Independent review: Wollstonecraft / Agere, November 5, 2026

## Verdict for the founder

**The architecture direction is reasonable, but the shipped hot path doesn't match it.** The pieces are good: exact SQL, governed definitions, full-population denominators, source-linked quotes, and supported unknowns. But on every ordinary question I could trace, the system works like this:

1. About 25 specialised routes each decline within a few seconds.
2. A Claude Haiku agent with SQL tools (`backstop_agent`) does the real work and usually has a correct answer within about a minute.
3. Two over-strict compilers and a model-voting verifier then spend 5–15 more minutes and most of the money arguing about wording.

So in practice the product is "a competent LLM with warehouse tools, plus expensive guards." It has never been measured against that plain baseline.

**Why it's taking so long:** the cause is process more than difficulty.
- The flagship HFrEF question was re-run at least 12 times in three days.
- Most commits are receipts and seals rather than behaviour changes.
- The release predicates were drawn so they pass while ordinary questions still come back partial or slow.
- The October 11, November 2 and November 3 reviews each named this pattern, and it continued.

**The planned next step is already done, and it failed to help.** The same-question run returned before the pause: 446s and $0.625, against 220s and $0.333 before. The planner handoff wasn't the bottleneck. The answer-binding compiler is.

**Shortest credible route:** fix three specific guard behaviours (small code changes), run one honest baseline comparison, and run one fixed set of about 25 unfamiliar questions across all ten areas. Stop sealing, packaging and re-running the HFrEF question until those are done. My estimate is about 8–15 focused engineering days plus 1–3 days of waiting, with low-to-moderate confidence. Detail is in §7.

---

## 1. The actual current question path

```
POST /api/stack/compose
  │
  ├─ exact-question cache / bottled replay ──(hit, revalidated)──► answer   [6.7s, $0.004 on the 795-patient claims repeat]
  │
  ├─ 25-tier proposer cascade (exact_local_plan, specialist families, semantic_cache,
  │    learned_verified_sql, chart_reader, template_decoding, s1 routers, local_compiler,
  │    hcc_suspecting, evidence_retrieval, atomic_system_one, bottled_sql,
  │    qwen_typed_interpreter [disabled: "No qualified typed adapter"], …)
  │    → on the readmission question: all "no proposal"/"rejected", ~9s total
  │
  ├─ spec_path (Haiku fills a typed plan → request_plan.py compiler)
  │    → declined: "bind filter wording to its own predicate" (request_plan.py:237),
  │      even though the rejected plan was semantically correct
  │      (stay.organization_id, readmitted_30d = 1, ratio)
  │    → 4d1f3a9d now passes the rejected proposals forward as hints (not facts)
  │
  └─ backstop_agent (Haiku tool loop → Sonnet escalation)        ◄── every ordinary run I found lands here
        │  SQL tool calls (ms each) → draft answer with {placeholders} bound to result cells
        │
        ├─ computed-prose compiler (backstop_agent.py:990–1040, 1237)
        │    regex finds ANY numeral or number word not bound to an executed cell
        │    ("30" in 30-day, "2029", "100" in ×100, "1" in =1, "zero") → REPAIR
        │    ↺ repeat: Haiku ×N, then Sonnet; the model even runs SELECT 100 AS percent_scale
        │
        ├─ Jev per-row coverage judgments; Perplexity coverage decider
        │
        ├─ [clinical/narrative answers] per-field source-support votes (Jev + Perplexity),
        │    threshold 0.8; any field below it → packet "unsupported" → answer "partial"
        │    (backstop_agent.py:2754; includes native fields such as `age`)
        │
        └─ save decision context → reopen / follow-up / work / deliverable on request
             failure paths: partial saved + localized continuation; quarantine of reuse candidates
```

Earlier work is preserved well: native action replay, source-reading caches and saved contexts. It gets discarded or repeated at the repair loops, which re-prompt with the whole context each round, and at uncertain verification, which has no terminal state other than "partial."

---

## 2. Evidence for the central finding

**Readmission question, run on candidate `4d1f3a9d`** (`artifacts/agere-spec-routing-2026-11-05/ordinary-current/`):

| Segment | Calls | Wall time | API-equivalent cost |
|---|---|---|---|
| Attempts 1–5: read docs, 3 SQL queries, full answer draft | 5 Haiku | ~56s | $0.029 |
| Attempts 6–26: literal-binding repairs (`unbound literals: ["30","2029","0"]`, `["1","30","100"]`, `["92"]`) | Haiku ×13, Sonnet ×1, Jev, Perplexity | ~375s | $0.596 |

- The headline after repair matches the independent baseline: 92 organisations, 505 discharges, 106 readmissions. I checked the headline only, not all 92 rows.
- **About 95% of cost and about 85% of time bought no change to any number.**
- The earlier 220s **partial** run on `81f8bbf5` failed on the same rule. All four repair rounds were unbound "30", "1" or "zero" (`agere-hospital-question-2026-11-05/run-receipt.json`).
- The November 3 audit had already recorded that "five drafts required deterministic quantity-binding repairs." That defect was named two days ago and never became the critical path.

**There is also a duplicate responsibility.** `requirement_ledger` already extracts `lit:number:30` and `lit:number:2029` from the question and marks them covered. The prose compiler never consults that ledger.

**HFrEF flagship** (`agere-repaired-ordinary-2026-11-05/automatic-native47-reuse/assessment.json`):
- Status is partial because 5 fields in 3 of 57 patients scored 0.75–0.79 against a 0.8 threshold. One of those fields is **age**.
- `confirmed_field_faults: 0`. Nothing is wrong. An uncalibrated vote threshold turns "uncertain machine verification" into "unfinished answer." That is exactly the P1 defect the November 3 audit named, and it is still open.
- The parent run also produced a 549MB HTTP response that filled the disk.

---

## 3. Status table: all ten acceptance areas

| # | Area | Status | Evidence / caveat |
|---|---|---|---|
| 1 | Native source meanings → evidence | **Works (scoped)** | 4,115 patients, 2,396,490 visible records, as-of 2030-05-27 confirmed in `active-config.json`. The source is `healthcare-warehouse-v2.8-sample-0.30`. Prior exhaustive adapter reconciliation exists. |
| 2 | Evidence dependencies / acquisition | **Partial** | `evidence_change_reassessment` passed (scoped); per-patient invalidation fixed (`69359649`). Coverage on the flagship stays "undecided": the verification-state defect. |
| 3 | Deliberate warehouse migration | **Works (local scope)** | `source_and_adapter_conformance` passed. Not a production connection. |
| 4 | Broader context / learned interpretation | **Partial, economically poor** | Jev/Perplexity reading is connected. The full clinical run made 6,966 attempts in 57.66 min. The BGE/UltraBERT reranker is wired; source selection is a G2 candidate, not promoted. |
| 5 | Training / model qualification | **Failed qualification / not promoted** | Qwen typed adapter: 185/304 correct, 33–42 wrong; SQL adapter 173/304, 62 wrong. Gains and losses between rounds. Kev/d1 frozen. The cascade's Qwen tier is disabled at runtime. G5 blocked. |
| 6 | Clinical / program breadth | **Partial** | Two population programs reconcile (112 + 129 nominations, 4,115 members). The flagship clinical question is still partial. |
| 7 | Longitudinal prediction | **Engineering works, no useful predictor** | Correctly declines: selected targets have no positive labels. |
| 8 | Discovery / explanation | **Works (scoped)** | 20,432 appointments reconciled; baseline comparison present. |
| 9 | Financial / contract | **Works (scoped)** | 694,445 financial rows reconcile. The financial ordinary path ran 22 native actions with zero Claude calls. This is the strongest selective-computation evidence in the repo. |
| 10 | Product completion / unfamiliar curriculum | **Not done** | No broad unfamiliar-question curriculum has run. Ordinary runs since November 3 took 112–1,638s and were mostly partial or failed. |

**Model and component roles:**

| Component | Status |
|---|---|
| Rules / SQL | Active, the core value |
| Claude Haiku | Active as the de facto planner and answerer |
| Claude Sonnet | Active as repair escalation |
| Jev | Active for per-row and per-field judgments |
| Perplexity decider | Active for coverage and support votes |
| BGE / UltraBERT reranker | Active in document candidate ranking; not qualified |
| MiniLM | Historical |
| Qwen adapters | Available, failed qualification, disabled |
| Kev/d1 | Frozen experiment |
| Learned cascade router (`cascade_router.py`) | Available; reorders tiers that all decline anyway, so effectively unneeded |

---

## 4. Ranked findings

| # | Severity | Finding | Observed or hypothesis | User impact | Smallest correction |
|---|---|---|---|---|---|
| F1 | **Critical** | The computed-prose compiler rejects definitional and question literals ("30-day", "×100", "=1", "2029", "zero") and loops repairs (`backstop_agent.py:1000–1040`). | Observed in 2 of 2 readmission runs and in November 3 drafts | 4–8× latency, ~20× cost, turns correct answers into partials | Whitelist numerals in the original question, the requirement ledger and governed definition constants. Mask unit-bearing phrases (N-day, ×100, = 0/1 flags). Cap repair at 1 round; after that, return answered with an annotation instead of looping. |
| F2 | **Critical** | Uncertain support votes make the whole answer partial. Native-derived fields (age) are voted on at all. | Observed (`automatic-native47-reuse`) | Flagship never reaches "answered" despite 0 confirmed faults | Fields whose value comes from a native result cell are supported by provenance, not a vote. Below-threshold narrative fields become "answered, N fields unverified" with the reason. Keep "partial" for genuinely unperformed work. |
| F3 | **High** | No competent baseline has ever been run. `frontier-first-comparison` changes only the investigator model inside the same guards. | Observed (absence) | The core value hypothesis is untested; you can't tell which guards earn their place | Same SQL and note tools, Sonnet/Opus, no compiler or votes, on a fixed question set scored against independent native baselines. |
| F4 | **High** | `spec_path` rejects correct plans on lexical quote rules (`request_plan.py:226–237`). It adds 8–56s before the fallback and has no recorded win on ordinary questions I found. | Observed for this question; "no wins" is a hypothesis from ~25 receipts | Latency; the 4d1f3a9d handoff fixes a symptom of this | Take it off the hot path until a measured set shows it wins. Don't add more hint plumbing. |
| F5 | **High** | The release predicates (9/9 pass) contain nothing about ordinary-question completion, latency or cost. Much of the evidence is October 30–November 2 receipts carried forward by hash. "Six original questions" are fixed regressions. | Observed (`acceptance/assessment.json`, 736KB) | "All ten areas pass" can be true while the flagship is partial and ordinary questions take 7 minutes | Add predicates: answered rate on an unfamiliar set, p50/p90 latency, cost per answered question, and correctness against native baselines. |
| F6 | **High (process)** | 228 commits since November 2, about 129 of them record/seal/receipt/package/release. A 4,501-line control file with 309 decision-log entries. A 520KB run receipt that hashes every code file. 57 artifact directories in 3 days. 13 new review documents in November. | Observed | Throughput goes to bookkeeping; each defect is re-measured instead of fixed | See §6. |
| F7 | **Medium** | The clinical note-reading decomposition (108,062 statement decisions, 3,357 Jev + 3,480 Perplexity calls) is the one real large repetitive workload. It is handled by per-item API calls. | Observed (November 3 audit) | 57-minute cold run | This is where the bottling paper actually applies (§5). Address it after F1–F3. |
| F8 | **Medium** | Oversized responses (433–549MB). The client failed with a full disk. | Observed | Delivery failures | Project the response for the client; keep the full state server-side. |
| F9 | **Low** | The requirement ledger marks "died" (deceased qualifier) as `dropped` while the answer correctly includes the deceased. | Observed | Noisy self-assessment | Fix it, or stop surfacing that check. |
| F10 | **Low** | 2 of 102 tests fail in the broader suite and also fail on the unchanged baseline: a timing test and a tie-reporting test. | Observed (`test-disposition.json`) | Minor | Fix or quarantine explicitly. Don't report the whole suite as passing. |

What earns its complexity:
- native execution
- result-cell binding, as a rendering mechanism rather than a regex gate
- source provenance and quotes
- supported-unknown semantics
- revalidated execution replay
- the saved decision context
- dependency invalidation

What doesn't, on current evidence:
- the 25-tier upfront cascade
- `spec_path` on the hot path
- the learned cascade router
- the Qwen tier
- per-field vote thresholds on native values
- unbounded repair loops

---

## 5. Research → implementation reconciliation (consequential ideas only)

| Idea | Source quality | Code / activation | Measured result | Verdict |
|---|---|---|---|---|
| **Bottling** (Sonthalia et al., arXiv 2610.08775; I checked the abstract and intro in your PDF) | Primary paper. Its own result: 48 of 60 bottling runs fall below zero-shot, 31 of 60 below distillation baselines. | Implemented as exact-question execution replay with revalidation and quarantine | 128s→6.7s, $1.81→$0.004 on one exact repeat. HFrEF native replay cut cost 88%, but the answer stayed partial. | Useful cache. It is **not** the paper's setting: the paper is about large batches of homogeneous items. The paper-shaped workload here is F7, the per-statement note judgments. Keep the replay, stop calling it learning, and point any real bottling study at F7 later. |
| "Total cost per verified useful answer" (model-economics report) | Sound framing | Cost telemetry is excellent | Never used as a gate | Adopt as a release predicate (F5). |
| Cache hierarchy / dependency recomputation | Engineering design | Implemented: source caches, native replay, per-patient invalidation | Real savings on continuations | Keep. Strongest research-to-product link. |
| Decision-preserving evidence selection (information bottleneck) | Analogy; the report admits the selector "lost relevant evidence" | Heuristic BGE/UltraBERT ranking; G2 not promoted | No qualified gain | Later. Needs labels you don't have. |
| SLM / adapter specialisation (Qwen, Kev/d1) | Development studies | Trained, not promoted, disabled at runtime | 11–14% wrong; rounds both gain and lose | Stop for the MVP. |
| Conformal / calibrated escalation | Literature only | Not implemented. The 0.8 threshold is uncalibrated and drives F2. | n/a | The fix for F2 doesn't need conformal methods; it needs correct state semantics. |
| Bayesian Blocks, Lomb–Scargle, tensor networks, quantum | Analogies, honestly labelled | Change-point primitive exists, off the hot path | No product gain | Defer. |
| Jev/TypeSafe System One | Vendor model, plus a favourable comparison in the bottling paper | Active and cheap ($0.0045 for 7 calls) | Works as a cheap judge; never compared with a rule baseline for coverage labelling | Keep. Qualify it inside F3. |

Review limitations:
- I read the research reports' structure and the decision-relevant sections, not every source register entry.
- I did not open the Documents corpus, protected evaluation questions, answer keys or frozen case contents.
- I did not run tests or any provider call.
- I did not reconcile all 92 organisation rows.

---

## 6. Why it's taking so long, and the management corrections

1. **Re-measuring instead of fixing.** The HFrEF question ran at least 12 times between November 3 and 5. Durations included 885s, 864s, 986s, 861s, 751s, 1,638s and 452s. Each run diagnosed a new local symptom.
   - *Correction:* no re-run of a question until a code change targets the dominant cost in its last receipt.
2. **Optimising the wrong segment.** November 4–5 went to reuse, packaging, response size and planner handoff. The receipts show repair loops dominating.
   - *Correction:* each increment starts from a time/cost breakdown of the last run and names the largest segment it targets.
3. **Over-investment in proof.** Every increment is sealed, packaged and hash-manifested, and the checkpoint is rewritten several times an hour.
   - *Correction:* seal only at release points. Commit messages should describe behaviour change. Drop the per-file hash manifests from run receipts.
4. **Finish line drawn to pass.** Scoped predicates pass; "full MVP" is permanently false.
   - *Correction:* one finite definition (§7), measured on unfamiliar questions.
5. **The builder reviews itself.** The prior warnings were the builder's own and went unheeded.
   - *Correction:* a weekly external check against the §7 metrics only.

**Learning, defined for this product:**

| Form | Status | Recommendation |
|---|---|---|
| Exact answer cache | Exists | Keep |
| Execution-plan replay with revalidation | Exists | Keep, with invalidation and rollback |
| Parameterised artifacts | Partial (`agere-parameter-transfer`) | Worth it for repeated finance and ops reports |
| Source / knowledge updates | Exists | Versioned definitions |
| Feedback-driven routing | Exists but unproven | Defer |
| Trained weights | Failed qualification | Defer |

Thousands of generated questions would not improve the MVP now. The current defects are structural and show up within 25 questions. Machine-generated questions with machine-authored answers would add correlated mistakes and cost.

---

## 7. Finite completion plan

**The three stages are separate:**
- **Synthetic engineering MVP finished:** WP1–WP6 below pass on the synthetic warehouse.
- **Clinically / model qualified:** needs independent qualified clinical review and source-family-disjoint evaluation. You can't estimate this from coding effort.
- **Ready for customer data:** needs authentication and tenancy, governed adapters, OWASP/HHS work (deferred), and deployment authority. These are outside dependencies.

| WP | Outcome | Depends on | Acceptance check | Hands-on effort | Waiting | Stop condition |
|---|---|---|---|---|---|---|
| **WP1** Binder fix (F1) | Question, ledger and definition literals allowed; 1 repair cap; annotate instead of looping | none | Offline: retained readmission drafts compile with 0 repairs. Live: one run answered in ≤2 Claude rounds; numbers still match the baseline. | 4–8h | <15 min | Answered, no number changed |
| **WP2** Verification state (F2) | Native-provenance fields exempt from votes; "answered with N unverified fields" state | none | Offline re-evaluation of the retained HFrEF state gives "answered, 5 unverified, 0 faults" with 0 provider calls | 4–8h | none | Same |
| **WP3** Competent baseline (F3) | Plain Sonnet/Opus + the same SQL and note tools, no guards | none (parallel with WP1/WP2) | Comparison table on the WP4 set: correctness against native baselines, unsupported claims, time, all-in cost | 6–10h | 2–4h of runs; cost not estimated from evidence | Table produced; a "guards lose" result is acceptable |
| **WP4** Fixed unfamiliar curriculum | About 25 hand-written questions across all ten areas with independent native baselines | WP1, WP2 | Answered/partial rate, p50/p90 latency and cost per answered question recorded; each failure classified as implementation gap, unsupported fact or uncertain verification | 8–14h to write; defect fixing 2–5 days (biggest unknown) | 3–6h per pass, 2–3 passes | All failures classified; no "missing wiring" class left |
| **WP5** Hot-path pruning (F4) | Cascade reduced to cache/replay + native families + backstop; spec_path off unless WP4 shows wins | WP3, WP4 data | p50 latency on WP4 does not get worse; no correctness loss | 4–8h | one WP4 pass | Same |
| **WP6** Release predicates + one package (F5) | Predicates from WP4 metrics; seal once | WP4, WP5 | Assessment fails if ordinary metrics regress | 4–8h | <1h | One sealed release |
| *Later* WP7 | Clinical note-reading economics (F7: batch classifier / bottling proper) | WP3 result | HFrEF cold run time and cost vs the 57-min baseline, same findings | 2–4 days | hours | Only if WP3 shows the guarded reading path is worth keeping |

**Total for WP1–WP6:** about 60–120 hands-on hours (8–15 focused days), plus 1–3 days of runs. My 50% estimate is about 10 days. The wide range comes from WP4 defect fixing, which can't be known before the curriculum runs. The November 3 "22–38 hours" estimate didn't include WP3 or WP4 and is superseded.

**Critical path:** WP1 + WP2 → WP4, with WP3 in parallel.

**First three actions:**
1. Fix the F1 binder and verify it offline on the two retained readmission receipts.
2. Fix F2 and re-evaluate the retained HFrEF state offline.
3. Write the 25-question curriculum and its native baselines before any further live run.

**Stop now:**
- HFrEF re-runs
- the planner-handoff follow-up run
- per-increment packaging and sealing
- per-file code hashes in receipts
- new review documents
- Qwen, Kev/d1 and cascade-router training
- bottling expansion
- generated-question campaigns

**Defer:**
- WP7
- evidence-selection learning
- conformal calibration
- the astrophysics/quantum methods
- OWASP/HHS (as you directed)

**One fact the builder's reconciliation needs:** the "unsealed" planner-handoff run is a **regression**, 446s/$0.625 against 220s/$0.333. Its own receipt says `ordinary_performance_improved: false`. Spec routing still declined. Repair-round variance dominated, and the handoff change didn't move the critical path. Record it as a negative result, not a candidate to promote.

This was read-only. I moved the session's working folder to the integration checkout (W) for reading. I edited nothing, ran no tests or provider calls, and stopped nothing.