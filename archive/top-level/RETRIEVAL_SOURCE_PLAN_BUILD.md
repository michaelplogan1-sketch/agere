# Explicit source plans and bounded review groups

Checkpoint: 2026-10-11T11:09:42.961805+00:00. Implemented and engineering-verified in the actual local question workflow. Full Agere product acceptance and clinical validation remain incomplete.

A saved retrieval comparison can now retain either its RRF or BGE selection, explicitly add received source categories, preview the exact patient/date/record scope, and prepare immutable review contexts. The preview distinguishes baseline whole-record expansion from category additions, out-of-window records, unknown dates, and other received records outside the answer. It rejects changed previews and sources. No linked records are silently added across the date window. Category presence does not establish relevance, medication use, benefits or absent care.

## Delivered examples

| Development question | Original selected records | Explicit additions | Complete scope | Review grouping |
|---|---:|---:|---:|---|
| P-1001 medication | 8 | Both received medication orders, 2 | 10 records / 33 source units; 509 other received records excluded | 1 group, 6 bounded source passes |
| P-1006 payer | 8 | 1 coverage + 15 coverage-verification reports | 24 records / 53 source units; 474 other received records excluded | 2 groups of 12 records, 8 source passes total |

All 86 selected units fit with zero omissions. Original-training development patients only; no reserved/frozen or outer-evaluation use, new training or model promotion. The unchanged generic retriever still omits these categories; the new explicit source-plan path makes their inclusion possible and reviewable. These are source-coverage checks, not recall or clinical-accuracy scores.

Whole-record grouping preserves each selected record exactly once. Each group connects to the existing source-meaning interpretation controls, source timing, saved answers, unresolved questions, program review and buyer-capability paths. No new model answer was obtained in this milestone. A group answer covers that group; the payer groups do not yet produce a unified interpretation or automatic parent-plan attachment.

## Request transport repair

Live verification found a medication order whose typed relationships and availability metadata made even a small source unit exceed the existing 20,000-byte request bound. The new opt-in reference-table context preserves original units and response contracts, uses column dictionaries and explicit aliases for repeated relationship metadata, and verifies exact reconstruction. It applies only to newly prepared contexts. Historical request bodies and 33 pinned source routes remain unchanged. Existing structured-field transport is available when needed; it was not needed for the two final examples.

Independent reconstruction checks pass for all 14 source requests. The largest problematic packet falls from 32,760 to 19,498 bytes with identical restored metadata and source units. The maximum final request is 19,699 bytes. Total bytes across these packets fall from 244,176 to 235,737; the codec can add overhead to small metadata sets, so this is not a universal cost-performance claim. Source groups use local packing checks; provider-native token checks still occur before a real inference call.

## Verification and limits

- 83 focused tests pass, including 26 new scope/transport tests. Coverage includes stale and cross-patient scope, unknown clocks, date exclusions, duplicate categories, context overflow, whole-record grouping, exact cache replay, immutable storage, codec reconstruction and unchanged historical requests.
- Real HTTP preparation/replay/inspection, exact context retrieval, existing bounded interpretation planning and negative 400 responses pass for both examples. Both group controls are enabled in the saved browser view; no inference was submitted.
- 34 source records and all 86 units independently accounted for; no duplicate or missing records between groups. Prior 159 protected files unchanged; 161 now protected. All 382 work items / 385 work events / 13 capability tasks / 37 capability events and 837 provider-ledger rows remain exact. 19 Documents research files and the original v1.23 snapshot remain unchanged.
- No new paid API calls, native token-count calls, clinical work, patient contact or external transmission. Spending remains uncapped with accounting intact.

The fresh-retrieval profile uses the same three development questions and exact cached model scores. Medication/payer/appointment searches take 18.74/22.74/15.56 seconds; scoped FTS/BM25 takes 16.08/21.71/14.66 seconds. Final evidence IDs are unchanged. Two SQL loop-order alternatives did not improve the medication query (about 16.3 seconds); neither was deployed. This profile identifies a bottleneck, not an implemented speedup.

## Next work

Implement the approved shared model-role evaluation on identical visible evidence across medication, respiratory, cardiometabolic, functional, appointment and payer workflows. Inspect `semantic_reader`, `semantic_student`, `semantic_learning`, `reader_factory`, `local_reader` and existing qualification contracts first. Keep statement/time/relation classifiers separate from retrieval ranking, source-meaning judgments, planning and service matching. Reuse existing roles and simple baselines, predeclare development examples and machine-authored expectations, preserve frozen tests/families, and retain every measured error and abstention. Clinical adjudication is still absent.

Then connect grouped source answers back to their parent question with explicit cross-group reconciliation and evidence limits, improve measured source selection/latency without invalidating old retrieval, and continue the full product queue. Do not substitute more catalogs or unchanged tests for implementation. The broader production, clinical/payer applicability, externally validated models and deployment gaps remain open.

Evidence: `artifacts/agere-retrieval-source-plan-v1/implementation-receipt.json`, `http-verification.json`, `preservation.json`, `retrieval-stage-profile.json`, `focused-tests.log`, `browser-verification.json` and `browser-source-plan.png`.
