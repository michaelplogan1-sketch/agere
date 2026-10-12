# Codex accountability audit and final handoff to Claude

October 12, 2026. Requested by Mike after stopping Codex implementation. Claude is the sole build owner. This is an audit and handoff, not a new build plan or permission to resume Codex. C2 is cancelled; automatic continuation remains paused.

## What I got wrong

**I built substantial machinery without establishing that the combined product could reliably answer the broad questions Mike wanted answered.** The repeated requests were for useful healthcare and healthcare-adjacent reasoning, unfamiliar combinations, measurable competitive advantages, and good total economics. I let narrow operators, preservation checks, integration receipts, and internal representations consume too much of the work. Explicitly documenting limitations did not make that sequencing effective.

My previous response to the Anthropic objection was also too narrow: explaining the C1 provider assignment did not address the earlier architecture that made unfamiliar English questions depend on paid frontier planning. I should have investigated that first.

This audit inspected the original Git baseline `8813b99`, subsequent relevant commits, current code and controls, the prior external review, preserved experiments and reports, benchmark attempts, and the shared product usage ledger. It did not rerun paid inference, alter product code, restart services, or inspect every line of every historical component. Pre-baseline work lacks granular Git history; individual-file authorship and lifetime billing cannot all be reconstructed from these records.

### 1. The English-question path was narrower and more expensive than the ambition

At baseline `8813b99`, `clinical_core/agere/clinical/economical_planner.py` accepts exact local recipes and registered examples, or reuses an exact-question cache. Its escalation rules include ordinary phrases such as “count,” “how many,” “why,” dates, numerical restrictions, and unfamiliar terms. When live inference is enabled, uncovered questions go to the frontier callback; when disabled, they fail to obtain a supported plan. A bounded alternate route uses local embedding retrieval followed by a **hosted Perplexity decision call**, which can still escalate to Claude.

`graph_planner.py` selects `PersistentGateway.MODELS['claude']`, then configured as `claude-opus-5-5`. The graph must use a supplied operator vocabulary. At that baseline, arbitrary SQL/code is explicitly excluded. The local runtime executes the returned graph, but that does not mean the English interpretation was local. `stack_runtime.ask(live=False)` returns ranked retrieval, not a general analytical answer; Qwen is explicitly inactive as the current planner/default reader.

The failure was using this narrow coverage plus expensive fallback as the expanding foundation without a representative correctness/cost benchmark. It was not justified to infer economical general answering from exact cached replays. `SCOPED_REVIEW_COMPILER_BUILD.md` already reported that both exact compilers abstained on **all eight historical free-form prompts**. That should have triggered a stronger change of approach.

### 2. I added a difficult internal language and optimized its packaging too early

`clinical_core/agere/clinical/planner_catalog.py` factors the catalog into common fields plus additions, prefix/suffix groups, set differences, and column/row tables. It then uses YAML aliases with hexadecimal anchor names, flow-style output, a very large line width, and removed separator spaces. The model also returns graph nodes with JSON encoded inside a `parameters_json` string.

This is a concrete match for the complaint about material that is difficult to read as English. I cannot establish which exact screen or payload Mike saw, but the representation itself is verified. The catalog's YAML round-trip check proves that a parser recovers the same values. It does **not** prove that a model understands those values, binds them to the actual warehouse, or produces the right answer.

The paid dependency comes from the chosen planner route, not an inherent requirement of YAML or graphs. Nevertheless, I chose that route and allowed catalog compression and scope-selection machinery to grow before demonstrating adequate semantic grounding and broad usefulness. Larger catalogs can also require a selector request followed by a planning request (`planner_scope.dispatch`), so one question need not mean one paid call.

### 3. Structural validity was allowed to outrun source meaning

The diabetes/HbA1c failure is independently documented in `AGERE_EXTERNAL_REVIEW_RESPONSE_2026-10-12.md`. A saved plan requested lab codes `4548-4`/`17856-6` even though the received lab rows had no code/LOINC fields. Names and units were available. The plan marked its assumptions unvalidated but still executed, producing unknowns instead of a useful result.

The two clear positives under the saved “any observed HbA1c > 9%” interpretation are **P-1008 and P-4007**. The earlier external review's one-person answer was itself incomplete. This correction does not excuse the product failure. A missing mapping should have been identified as a plan/grounding defect before being propagated into patient-level clinical uncertainty.

Reusable concept, field, unit, temporal and denominator grounding should have preceded further clinical/workflow breadth. Claude has since implemented significant grounding and semantic-layer changes; do not revert those to the earlier baseline.

### 4. Process and preservation displaced capability

I preserved useful source boundaries, frozen evaluations and failed experiments, but extended preservation machinery into active implementation. Repeated runtime installation patches, copies of active modules, broad source-file hash invalidation, multiple stores and a large service dispatcher made the system harder to understand and improve. A helper/UI change could invalidate otherwise reusable work. Git was only introduced at `8813b99`.

Earlier principal/CTO reviews had already identified narrow increments, incomplete qualification and integration debt. I continued to report many finite checks without closing the core gap in broad answer correctness. I should have treated those findings as reasons to simplify and redirect implementation sooner. Claims of “50 documents/day,” “75% process,” or hundreds of GB physically reclaimable have not been independently established here; the engineering-priority failure is established without those exact numbers.

The synthetic-data setting should have remained clear. Some earlier outbound attempts were stopped by automatic tool approval review, as recorded in the existing review documents. Those were specific tool restrictions; they did not establish that local synthetic product development was generally prohibited. I should have kept those distinctions clearer and progressed the unblocked capability work.

### 5. The benchmark added avoidable cost and weak experimental control

The C1 assignment in `PARALLEL_WORK.md` at `eab853e^` explicitly specified an Opus 5.5 SQL baseline, its credential location, a 20-tool-call limit, and full acceptance runs. Earlier product direction also allowed useful hosted inference. Those records explain the provider choice; they do not establish that my implementation and execution were economical or sufficiently clear to Mike.

`bench/adapters.py` calls Anthropic directly, outside the shared product ledger. Each model turn appends SQL results and resends the growing conversation. Individual tool results are bounded to 2,000 rows/60,000 characters, but the full accumulated conversation has no corresponding aggregate token budget. There is **no prompt caching in this baseline**. The credential is loaded from the environment or the previously assigned `.env` file; its value is not included in this handoff.

What leaves the machine is the question, schema/tool instructions, model/tool history and retrieved SQL results. SQL runs locally in the read-only harness. The provider is not executing the repository's Python program. The custom graph planner is a separate product path from this benchmark SQL loop.

I should have made the expected call multiplication and spend clearer before a full run, reduced unnecessary repeated context, and used a stable product revision for comparison. The benchmark's per-question tool bound did not provide a useful overall experiment-cost forecast. Its batch runner also lacks a shared stop on fatal provider/credit failures. Avoiding identical paid retries is insufficient when the remaining batch continues against an unusable provider.

## Verified spend and its limits

Amounts below are estimates from recorded returned usage and the project's pinned prices, **not invoices**. These four C1 runs are attributable to my work. Paths are relative to `/Users/mikel/agere-codex/bench/results/`.

| Run | Attempts | Known estimated USD | Attempts with unknown total cost |
|---|---:|---:|---:|
| `frontier_sql/20261012T212944.923204Z.jsonl` | 50 | 21.116276 | 0 |
| `agere/20261012T213009.947409Z.jsonl` | 3 | 0.258184 | 0 |
| `agere/20261012T214641.278002Z.jsonl` | 3 | 0.117504 | 2 |
| `agere/20261012T215101.289043Z.jsonl` | 50 | 1.381044 | 39 |
| **Known C1 subtotal** | **106** | **22.873008** | **41** |

The baseline alone made **284 paid model calls**, consumed **4,767,209 input tokens and 102,372 output tokens**, and reported **zero cache-read or cache-write tokens**. That is 5.68 model calls per question on average. Unknown-cost attempts may or may not have incurred charges; they must not be priced at zero. This subtotal excludes earlier Agere work, engineering-chat usage, local compute and review time.

A separate read-only snapshot of `artifacts/agere-full/provider-budget.sqlite3` found 1,323 rows: 645 validated Claude, 296 validated Jev, 306 validated Perplexity, and 76 failed/abandoned rows without returned usage. Using the current recorded price schedule, the known returned usage estimates are $33.0947088, $0.039219264 and $0.02856426 respectively. The ledger spans work before and after Claude's takeover, includes some of the Agere runs above, and has a legacy reservation import. **Do not add it to the C1 subtotal or attribute it all to this chat.** Reservations are not spending. The ledger has no per-call timestamp column; other lineage/receipt records would be needed for fuller attribution.

The old provider gateway also stores a failed ledger row without the returned response when validation fails after a response arrives. Consequently, billed usage can be absent from the aggregate ledger even though a model produced output. Provider receipts/lineage and account billing need reconciliation to establish a lifetime total. I have not verified the account's actual total expense or the exact cause of its exhausted credit balance. The known estimates above should not be used to minimize Mike's reported expense.

## Additional benchmark defects and interpretation limits for Claude

- **C03/C04 timeout robustness remains unfixed.** Mike reported reference SQL timing out at 30 seconds with `--jobs 3`. `bench/run.py` still defaults to 30 seconds and executes a reference query inside every worker before calling either system. `--sql-timeout 180` is an existing workaround, not a demonstrated robustness fix. No C2 code was written.
- **The first full Agere run was operationally invalid as a controlled comparison.** It used four concurrent jobs while the product service was changing/restarting and preserved 39 attempts without cost totals. A health check did not establish a stable evaluation window. Connection failures are operational failures, not wrong clinical answers.
- **Scores are development evidence.** The scorer was corrected after seeing results, including count/value contract distinctions and explicit exclusions. Preserved answers can be rescored, but the cases are visible development/challenge cases, not a sealed independent clinical evaluation. Unsupported answers use prerequisite-phrase checks, not full semantic adjudication.
- **The scorecard mixes latest attempts.** It records benchmark commit/source/suite identity but does not enforce a single product implementation revision across every answer. Its aggregate is useful for development tracking, not a controlled performance or competitive-superiority claim.
- **49/50 needs context.** The frontier result consists of 44 structured matches and five mechanically scored abstentions. D16 fails the requested record-row interpretation because the model explicitly deduplicated encounters; it is not evidence of a fabricated number. The benchmark does not qualify general explanation, causal inference, discovery or clinical reasoning.
- **38/50 is Claude's checkpoint, not a fresh audit run.** The saved scorecard shows 38 correct, one comparable-value mismatch and 11 execution errors, with incomplete costs. Claude's subsequent fixes and current local compiler work are not evaluated by this audit. Credit failures still mean those answers were not delivered; they cannot be assumed correct.

## Exact takeover state and files that must not be lost

- PR [#1](https://github.com/michaelplogan1-sketch/agere/pull/1) is **merged**, no longer draft. C1 ends at `166dba1`; main merged it as `61eb56a`. Commit `df2cee7` pauses Codex and cancels C2. No further readiness action is needed.
- `/Users/mikel/agere-codex` is on local branch `codex/insight-benchmark`, at the C1 commit. **No C2 implementation or PR exists.** The worktree's `bench/SCORECARD.md` has later run updates; preserve it.
- **Raw `bench/results/` files are gitignored and were not transferred by merging PR #1.** They remain in that worktree. Preserve them before removing it. Later run files also exist there and must not automatically be attributed to Codex. `reference/20261012T212938.933783Z.jsonl` contains the final successful 50-reference run; retain earlier failed attempts too.
- My interrupted response promised removal of the Anthropic benchmark adapter, but **no removal was implemented** before Mike requested this audit. The adapter remains in merged code. Do not infer that inference has been disabled by that promise.
- Claude is actively editing main. During this audit I observed changes to `cohorts.py`, `grounding.py`, `economical_planner.py`, and a new `local_compiler.py`. These are Claude's ongoing work; I did not alter, stage, commit, revert or test them.
- `BUILD_OWNER.json` and `PARALLEL_WORK.md` correctly identify Claude as owner. **`CONTINUE_AGERE.md` and `AGERE_BUILD_CONTROL.json.checkpoint.next_action` still mention Codex C2 and API-credit reruns.** They are stale relative to Mike's latest instruction and `df2cee7`. The continuity checker passed despite that conflict; passing it does not authorize resuming those instructions. I left Claude's control files unchanged and am flagging the conflict here.
- No benchmark process was observed running in the process check for this audit. Claude's port-8885 service was left alone. Codex will not start another run or resume automatically.

## Scope and prior work Claude already has

The previous architectural assessment is already in `AGERE_EXTERNAL_REVIEW_RESPONSE_2026-10-12.md`; the baseline reviews are `AGERE_PRINCIPAL_REVIEW_2026-10-11.md` and `AGERE_CTO_REVIEW_2026-10-11_EVENING.md`. This addendum supplies the missing accountability, call amplification, cost evidence, benchmark caveats and takeover hazards rather than replacing those documents.

The ten-area charter remains in **`AUTONOMOUS_STACK_QUEUE.md`, “Preserved full-product acceptance backlog”**: (1) native source meaning, (2) evidence dependencies/acquisition, (3) warehouse migration, (4) broader interpretation, (5) training/model qualification, (6) clinical/program/action breadth, (7) longitudinal outcomes/prediction, (8) discovery/explanation, (9) financial/provider/contract performance, and (10) product/full-stack acceptance. The review should not erase those ambitions or treat medication/appointments as the whole product.

`AGERE_STACK_RECONCILIATION.md`, `AGERE_BUILD_GAPS.md` and `AGERE_RESEARCH_HANDOFF.md` map the partially connected or unqualified layers. They are historical dispositions, not proof that every current feature is complete. Statistical components such as Fisher/BH, beta-binomial comparisons, change detection, shrinkage and causal estimators exist in selected paths; broad, appropriately grounded discovery and qualification remain incomplete. A qualified smaller general planner, broader narrative semantics, reliable prediction, complete measure/attribution/contract logic, and production deployment are not established by C1.

The research index is `artifacts/agere-multipass-synthesis-v1/research-recovery-index.json`; relevant corpora are `research/agere/`, `research/model-economics/`, `research/vbc-landscape/` and the separate read-only `/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/`. The earlier `/Users/mikel/insight-engine/reports/REPORT.md` contains reusable work, but its competitor-class baselines are local reimplementations, not demonstrated vendor comparisons. Preserve frozen Kev/d1 evaluations, reserved answers, source-family boundaries and the original v1.23 roster. Keep hidden generator truth out of runtime evidence.

For the website, Mike explicitly selected **“Find Agere site inspiration”**, thread `01a125ae-00d8-7673-8057-80428998526a`; the recorded public preview is [Agere](https://agere-inside-intelligence.michael-p-loga331119.chatgpt.site/#top), with active source under `artifacts/agere-interactive-site`. That separate design checkout is concurrent work. The local MVP is `/Users/mikel/caresignal-mvp`. Product claims must stay tied to delivered behavior, not the full research ambition.

The practical correction is to make ordinary-question correctness and measured useful-answer economics the acceptance criteria for architectural work, simplify the English-to-data path, and then qualify the differentiating layers through that same path. Claude owns the implementation choices and sequence. This audit does not request another planning phase or authorize Codex to continue building.
