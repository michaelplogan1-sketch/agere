# Ordinary full-question confirmation — November 2, 2026

The current application returned the original full HFrEF request, its complete 57-patient table, interactive patient review and PDF through the normal HTTP composition route. The answer remains **partial**. Recorded API-equivalent cost and new provider attempts decreased substantially against the earlier warm reference; total elapsed time did not improve. G3 remains active for this measured performance/reuse gap, and independent G5 qualification remains separate.

No product code changed during this increment. The application ran product `b4011fc8377a3f8e9b6ef03ad0e478c4972d493d`; all runtime code, source/config identity and 27,274 earlier provider rows remained exact. The three source-cache producers remain byte-identical to `fa28466`. The scheduler remains paused, the primary service/main branch remain unchanged, and all ten acceptance areas remain in scope.

## Actual request and comparison

Exactly one HTTP POST was submitted at 21:23:53 UTC and returned HTTP 200 at 21:45:07 UTC. No supplied plan, cohort, prior final draft, owned recovery or forced cache miss entered that request. Existing warm observations remained available. Execution ID: `4736ac3c62764e16b277271cc2b42a8f`; cost owner: `q-0e457d31f4024853852a588c`.

| Observation | Earlier warm reference | Current warm HTTP run |
| --- | ---: | ---: |
| Complete elapsed time | 1,226.386 s | 1,273.209 s |
| New ledger attempts | 1,616 | 817 |
| Known API-equivalent subtotal | $7.765660 | $2.684840 |
| Unpriced failed Perplexity attempts | 3 | 4 |
| New Jev validated calls | 1,504 | 750 |
| New Haiku validated calls | 72 | 51 |
| New Sonnet calls | 36 validated + 1 failed | 12 validated |
| Analytical status | Partial | Partial |

The known subtotal is 65.4% lower and new attempts are 49.4% lower. Elapsed time is 46.823 seconds, or 3.8%, higher. These are single warm observations at different product revisions; the earlier reference called the compose handler directly in a newly constructed Stack, while the current observation used the actual service and its normal startup bindings. This is neither a cold benchmark nor a statistical estimate or controlled attribution of every improvement to one code change.

Claude used the subscription transport. Its figure is an API-equivalent estimate, not a per-call subscription charge or invoice. Four Perplexity quota failures have no returned usage and remain unpriced; the in-process circuit then prevented further dispatch. There was no actual Claude subscription limit. Local compute, development, review burden and subscription allocation remain unpriced, so authentic all-in cost is unknown. Every new attempt has exactly one explicit question owner, zero ambiguous window attribution and agreement between the planner count, cost receipt and unique ledger delta. All six pre-existing pending rows remain unchanged.

## Which components actually worked

| Component | Observed role | Current measured activity |
| --- | --- | --- |
| Native semantic SQL and composition | Cohort, medication observations, dose objects, dated relationships, aggregates and report-table bindings | 33 SQL invocations including internal guarded execution; 2.425 s SQL wall-time union; the retained actor trajectory contains 25 SQL calls and four `compose_table` calls |
| MiniLM local retrieval embedding | Reused retrieval embeddings in proposer paths | Seven method invocations, cached inputs, zero newly encoded texts; this does not establish new Qwen/UltraBERT/Kev/d1 inference |
| Jev typed readers and verifiers | Source-span reading, relation/subject/time judgments and final claim support | 750 new validated calls; 139 typed-reader invocations, 53.898 s wall-time union |
| Relation-axis composition | Kept entity, relation and source context attached to readings | 33 invocations, 10.142 s wall-time union |
| Targeted frontier span repair | Repaired selected unsettled source fields, including cache reuse | 31 invocations, 156.994 s wall-time union; invocation count is not a new-charge count |
| Haiku/Sonnet investigator | Planned native actions, repaired construction/quantitative/report errors and assembled the final proposal | 32 investigator actor callbacks; Haiku started the role, Sonnet followed a repeated deterministic construction-review failure |
| Native saved-report renderer | Produced and reopened the patient dashboard/PDF from the bound saved context | Current context `ANS-072b2e40b263f01efdeec306`, report `DLV-991526416074` |
| Recursive execution learning | Recorded the outcome without treating a partial interpretation as a verified answer | No new clinical full-question bottle, saved answer pair, selected candidate or trained weights |

Component intervals overlap and must not be added as total wall time. Worker component events sometimes lack an execution ID; the component accounting therefore uses the isolated single-POST server trace. Ledger ownership is independently explicit for all 817 new attempts. Loaded models are not counted as invoked models.

One Haiku call lasted **558.106 seconds** and reported **128,912 aggregate output tokens across two internal CLI turns**, returning a roughly 2 KB SQL action. The receipt does not expose enough intermediate content to determine precisely why the CLI used those internal turns. It establishes a planning/transport cost concentration, not a database bottleneck. Later deterministic reviews also required repairs to complete collection objects, numeric prose bindings and patient-derived recommendations. The final two source-support reviews remained unsettled; their low-confidence judgments were not converted into established clinical faults.

## Patient deliverable and verification

Read-only native and real HTTP checks confirm the exact original 57-patient membership, 228 pillar cells, 17 dose observations and 64 named medication groups. Every returned patient and medication name appears in the PDF. The PDF has 74 pages, 236,214 bytes, no attachments, and SHA-256 `ed2bf845852289ac7c4ac8cbbef8827ecc4b200499a5ef88928b1168383d859b`. The interactive HTML is 1,886,842 bytes. No separate evidence companion was produced.

All-page PDF bounds checks found no text outside the page. Rendered pages 1, 2, 3, 4, 20, 37, 56 and 74 were visually inspected for layout/readability, with no observed clipping or overlap. This is selected-page visual QA and mechanical QA of every page, not an independent clinical review. Current report downloads match the audited bytes; both the earlier clinical context and answered 12-row financial context still reopen as current. The later reopening timings are separate observations and are not substituted for the prior matched read-performance experiment.

The current verifier completed all 58 declared packets, with one machine-decided packet and 57 undecided packets. Patient presentation consequently shows one machine-supported patient and 56 patients with unresolved support. There are 522 unresolved claim-feedback entries, all observed as `below_acceptance_threshold`. These labels do not establish clinical accuracy, superiority, absence of source errors or suitability for treatment. The answer remains partial. The earlier retained answer and its 1,497 review states are preserved separately; the fresh trace is a different composition and is not a matched accuracy comparison.

## Native replay diagnostic and next engineering action

After the ordinary request actually ended, a separate explicit offline diagnostic tried its retained native trajectory with provider reservation, sockets and provider subprocesses blocked. This was not ordinary automatic planning, a new full workflow benchmark or a bottle promotion.

The first diagnostic stopped at the trajectory's first actual SQL error, `no such column: lp.sex`, as the existing recovery guard requires. Its assertion failure and 1.391-second receipt are retained. A corrected diagnostic explicitly omitted that failed action, which produced no result view, and checked the remaining 29 successful native actions against their original result-view identities. It ran in **15.545 seconds**, with zero provider attempts and zero actor callbacks. **27 of 29** results passed the diagnostic semantic comparison; `review_documents` and its immediate projection differed. No result was silently called equivalent.

Further inspection establishes that all 57 documentary rows, question contracts and document manifests match after named schema-bound seed-count normalization for diagnosis. Source-coverage/provenance representations still differ, including ordering and recovery bookkeeping. Those differences need a precise comparison contract; they must not be erased wholesale. The original raw results, failed provider attempts and complete accounting remain retained. No candidate was selected, and existing full-request/source-support guards remain unchanged.

The next connected engineering target is **reusable native acquisition/composition with exact result and coverage guards**, using this retained trajectory and an appropriate simple native baseline. First locate every remaining result difference, distinguish operational recovery metadata from clinical values and selected-source coverage, and test changed-source/subject/time/cohort/field cases. Then assess whether existing bottling can reuse verified native work while requiring fresh final interpretation and complete downstream checks. Preserve existing selected financial reuse and rollback. Do not promote a partial clinical answer, repeat the whole provider cohort, introduce a disease-specific handler, lower thresholds or launch a broad training campaign.

The previously sealed finite engineering decision remains 9/9 within its declared ten-area compatibility scope, supplemented by the read-path improvement and this actual ordinary confirmation. It is not a whole-product commercial or clinical qualification. Independent source-family/model/clinical/outcome evidence and authentic total economics remain open. Customer deployment prerequisites are distinct; OWASP/HHS work remains deferred under Mike's instruction.

## Retained evidence

Canonical receipt: `artifacts/agere-ordinary-warm-confirmation-2026-11-02/delivery-receipt.json`. The same directory retains the exact run protocol, complete HTTP response, all new attempts, prior-row digests, isolated component trace, terminal/cost analysis, PDF/HTML audit, all-page bounds inventory, rendered-page inspection, real HTTP reopening checks and both native diagnostic attempts. The original diagnostic and the first verification-summary counting mistake remain identifiable; `verification-summary-v2.json` reads the actual nested observation state. Durable selected proof and patient outputs are copied to `/Users/mikel/clinical-insights-local/artifacts/wollstonecraft-ordinary-warm-confirmation-2026-11-02/`. Full response/source material remains in its governed original stores; no protected corpora, generator truth or external publication was used.
