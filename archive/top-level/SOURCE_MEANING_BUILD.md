# Source meaning and unresolved medication evidence

October 11, 2026. Two connected engineering capabilities are implemented. The new source reader has fixture verification and actual request preparation, but no newly returned provider answer or clinical-quality validation.

New source contexts distinguish locally verified canonical patient links from raw source-system IDs. Each supplied unit must receive an explicit subject, time and assertion judgment with a reason; mixed and uncertain meanings are allowed. The original text, source dates and exact citation checks remain authoritative. A chart link does not prove the narrative subject, and an account at its source date does not establish present use. Plans, reported use, dispensing, copied history and individual administrations remain distinct in the instructions. Output labels are unvalidated model judgments. Source packing includes the full added schema and metadata within the existing20,000-byte request bound; no units are silently clipped.

A separate deployment cutover preserves the original routes for33 historical contexts, including contexts with no saved progress file and the three earlier blocked payloads. This fixes a routing gap found before any transmission: checking only saved progress would have selected a new prompt for some unsent old contexts. Historical receipts and old request contracts are unchanged. Only two new prepared contexts and future contexts use the new reader. The existing availability-aware reconciliation stage is reused; it retains the new per-unit judgments in saved source passes but does not treat those judgments as original evidence or independent clinical validation.

The actual pharmacy task now exposes a **find unassessed sources** control. It binds to a current answer attachment in that exact medication review, maps unresolved unit IDs back to complete received records and selects the original questions. Users can narrow that selection before preparing a new context. Unknown, duplicate, stale, cross-patient and foreign-review bindings reject. This does not recover rejected findings, revise the original answer, mark old units assessed or resend a failed request.

For P-1001, the current claim review's12 unassessed units map to four complete records. Two new selections retain the same two original questions:

| Selection | Complete records / units | Actual request preparation | Status |
|---|---:|---:|---|
| Historical medication list and follow-up note |2 /8 |14,310 bytes, one pass |External interpretation rejected before execution by automatic approval review; exact-payload permission requested. |
| Two dated medication follow-up reports |2 /4 |14,526 bytes, one pass |Prepared locally; not submitted. |

Each excludes517 other received records. The original17-record answer still has12 system-unassessed units and one quarantined pass. The original pharmacy task stays at revision2 and its documentary questions keep their original states. A fresh interpretation of selected source records would supplement that history; it would not retroactively validate the old output or establish a regimen.

## Source currency check

The [ACP guideline index](https://www.acponline.org/clinical-information/clinical-guidelines-recommendations) and [PubMed metadata](https://pubmed.ncbi.nlm.nih.gov/41802255/) confirm the March2026 Update Alert4 listing. The publisher full-text URL again returned HTTP403, and PubMed provides no abstract. No recommendation change was inferred. The existing behavioral reference, comparison, service components and unresolved currency question are preserved. The separate systematic-review update is a distinct publication, not interchangeable clinical-guideline content. See `source-access.json`.

## Checks and limits

**77 unique focused checks, including23 new checks, pass.** The initial67-check suite and final23-check suite overlap on13 tests; they are not additive. Checks cover packing, per-unit accounting, canonical identity, raw-ID distinction, invalid meaning fields, exact citations, one/multi-pass fixture execution, cached replay, failure refusal, old completed/unsent routes, immutable cutover and exact-review gap binding. Initial test failures were fixture/assertion issues (metadata scope, transient dispatch counts and inspection routing) and were corrected; no live response was altered.

Actual HTTP verifies both saved contexts and their bounded plans, exact selection replay, all current saved answers, the unchanged original gap preview and foreign-review rejection. Browser verification exercises the actual task's gap preview, selection, narrowing and idempotent local save. The new meaning-result display has syntax and fixture-path checks; no new live result exists to claim browser-rendered clinical output.

Broad preservation covers125 prior implementation files,837 prior ledger rows,380 work items/383 events,11 capability tasks/33 events, prior clinical/reference/operational/information scenarios and19 separate Documents research files. The final manifest protects129 implementation files plus the routing cutover. Original-cohort v1.23, frozen experiments and source-family/reserved boundaries remain unchanged.

No new provider or token-count calls, no failed requests resent and no external care actions. Ledger remains824 validated/13 failed and an estimatedUSD7.513555524 for historical validated requests, excluding failed-response costs. Spending remains uncapped.

Automatic approval review rejected the historical packet command because it requires specific permission to transmit patient-linked medication/note content to the existing Claude Opus5.5 API. That exact context is `b1d9eedca7c2e819783efcf03e8ca156b2aaf72331db83164f8399a5220e1216`. The permission question remains pending. This is a fourth exact outbound boundary, not a spending limit; do not retry or change route to bypass it. The second selection was not submitted. Independent build work can continue.

Artifacts: `artifacts/agere-source-meaning-v1/implementation-receipt.json`, `focused-tests.txt`, `new-tests.txt`, `http-verification.json`, `preservation.json`, `prior-pending-routes.json`, `external-interpretation-pending.json`, exact selections/plans and browser proof. Full product acceptance remains false.
