# Larger source interpretation and question-led evidence planning

October 10, 2026. Synthetic engineering milestone; clinical correctness and full product acceptance remain unassessed/incomplete.

The complete four-category P-1001 context that previously produced a 27,327-byte request now completes through the existing 20,000-byte gateway. Whole original units pass through bounded source requests, then cited excerpts and every original finding pass through dependency-preserving reconciliation groups. The gateway and budget limits were not raised. Smaller requests retain the existing single-pass route and saved answers.

The main workspace also accepts a patient and an ordinary question. Existing hybrid retrieval proposes records; typed same-patient references expand their connected components; the current context builder includes every received record in the selected categories. Users can inspect selected and unselected sources before interpretation. This is reusable source planning without a question-specific handler, not proof of evidence sufficiency.

## Executed result

| Check | Observed result |
|---|---|
| Active snapshot | Original-cohort v1.23; 1,614 synthetic patients, 1,238,686 received records, 158 categories, 2,334,396 passages, 107,251 embedded unique texts |
| Larger case | 10 records, four categories, 22 original units; every unit submitted exactly once |
| Source passes | Three; provider requests 14,935 / 14,960 / 11,013 UTF-8 bytes |
| Reconciliation | Two complete dependency components; requests 15,584 / 14,133 bytes; four cross-pass references preserved |
| Findings and citations | 12 retained source-pass findings plus eight reconciliation findings; 41 exact citations checked structurally |
| Model dispositions | 20 units discussed with citations, two unresolved; these are unreviewed model judgments |
| Usage | Five paid inference calls and five native token-count requests in this milestone; estimated $0.385248 at recorded token prices, not an invoice |
| Recovery/replay | Three successful source calls survived reconciliation overflow; final run reused them and made two new calls; exact saved replay makes zero new calls |
| Review integration | One source-linked existing program assessment prepared; candidacy remains determined by existing rules; zero new work events or clinical actions |
| Local question plan | Six retrieved records → 12 connected records → 25 records across five categories; 52 units / 57,577 context bytes, 71 other received categories disclosed |
| Question-plan spending guard | Seven source passes, up to 15 total calls, $7.50 conservative ceiling; current $2.83 reservation balance disables dispatch in API/UI. No calls made for this larger question plan |
| Focused checks | 71 passing unittest executions, including imported fixture tests; Python compilation, JavaScript syntax, actual HTTP and browser flows verified |
| Browser | Exact citations open the connected source drawer; program preparation works; 390-pixel layout has no horizontal overflow; no captured warning/error logs |

Saved answer: http://localhost:8885/stack?case=0227207af37a3865659acbd25461b5a1eb5d6eee00ce23f4a17d259f3cbac08b

Receipt directory: `artifacts/agere-multipass-synthesis-v1/`. `live-verification.json` reports **two new calls in the resumed final invocation**; five is the full milestone total. `usage` retains the original cost of cached successful calls. The shared ledger remains capped at $50, with $8 legacy reservations and $39.17 current reservations. Its $2.83 remainder is reservation capacity, not an invoice balance. Failed reservations were not released and no limit was raised.

## Implementation and contracts

- `case_passes.py` wraps the unchanged `case_synthesis.py`. It reuses original request/parsing, exact citations, native token counting, provider dispatch, and the shared budget ledger. `context_passes.partition` accepts an actual-wire-size measurement, preserving existing default specialist behavior.
- Partitioning retains whole original evidence units. Indivisible overflows, incomplete contexts and call-cap overflow block explicitly. No text truncation or hidden source removal makes a request fit.
- Same-patient typed references, all finding citation groups and review dependencies define indivisible reconciliation components. Lossless tables reduce repeated metadata. A component that still exceeds the bound blocks; paid source responses and progress remain reusable.
- Reconciliation receives all original findings and exact cited source excerpts. Every finding is assigned exactly once. All original findings, questions and proposed reviews remain in the receipt. Separate groups do not establish clinical independence; relationships across groups remain explicitly unassessed.
- Source and dependency fingerprints are checked before and after calls. Added/deleted/changed records invalidate saved results. A conservative whole-patient fingerprint currently also invalidates unrelated patient-source changes. Failed/pending calls never silently retry. Partial interpretations cannot enter assessments.
- `question_context.py` reuses `Retriever`, typed source relations and the existing context builder. No retrieval match is recorded as failed discovery, never as evidence of absent care. Retrieval recall and scope sufficiency remain unassessed.
- `/api/stack/context/from-question`, `/context/plan`, `/context/progress`, synthesis inspection, graph-context actions, main question studio, coverage cards and existing program review are connected. The UI shows original findings and earlier questions separately from reconciliation and reports full saved-call cost.

## Research recovery and protected state

Read `AGERE_RESEARCH_RECOVERY_2026-10-10.md` and `research-recovery-index.json`: 66 saved files indexed, including the external Documents corpus. The reconciliation covers models, VBC knowledge, mathematical proposals and cross-field methods with implementation status, assumptions and remaining prerequisites. The inventory is a recovery snapshot by exact path/hash, not an independent review of every cited paper or a recovery of unsaved conversation-only ideas. Continuation documents indexed before this milestone's updates retain their pre-edit hashes in that recovery snapshot.

Frozen Kev/d1 runs, reserved cohorts, source-family boundaries, failed/shadow students and outcome qualification are preserved. No training, model promotion, source migration, generator modification, hidden-truth access, external publication or outreach occurred. Native warehouse analysis identities and the original single-pass implementation remain unchanged. The original saved single-pass answer still opens.

The previous chat was idle before ownership transfer. `BUILD_OWNER.json` names this chat; the existing `continue-building-the-agere-stack` heartbeat targets it. Only one automation exists for this continuation. Current server PID **73196**, tool session **72897**, loopback 8885, launched with `.venv/bin/python serve_current_warehouse.py --allow-live-stack --port 8885`. Older process/session references in historical docs are superseded.

## Actual limits and next implementation

This is demonstrated bounded transport and integration, not validated clinical interpretation or competitive superiority. Exact quotations do not prove entailment. The synthetic run itself exposes review-quality issues: a broad pass-level question about empagliflozin was carried into the narrower spironolactone reconciliation group, which then suggested a possible misattribution. The original empagliflozin finding remains in the other group. Likewise, earlier questions can be answered by later passes; they are retained as provenance and labelled accordingly. Do not convert this run into clinician-approved training labels or a quality score.

Current source planning expands whole categories and still has a 30-category / 60,000-byte assembly boundary. It may retrieve too much or miss required evidence. Final reconciliation does not revisit all raw text simultaneously. These boundaries and the two unresolved units are explicit, not removed to report success.

**Next substantive work:** build connected-record scope and typed evidence-need contracts that distinguish a source available in another pass, a source outside this selection, failed retrieval, ambiguous interpretation and genuinely unavailable evidence. Reuse current graphs, context, acquisition and correction machinery. Link questions/review proposals to exact source needs so unrelated pass-level questions do not contaminate narrower groups. Add bounded cross-group follow-up when needed, retaining unresolved global relationships and protected development/evaluation separation. Evaluate coverage and consequential interpretation independently before promoting automatic selection or training targets. Use the saved responses for local engineering rather than paying again for the same demonstration.

After that, continue the full warehouse capability queue, including payer/provider/contract applicability, useful prediction only with qualified targets, discovery/causal feasibility, portable interfaces and an honest capability matrix. Full queue acceptance is **false**; no readiness report declaring completion is warranted.
