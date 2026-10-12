# Registered domain programs and question-to-work delivery

October 10, 2026. Implemented and verified locally on synthetic data; clinical quality is not validated.

Respiratory and depression/anxiety source reviews now enter the shared population composition, persistent program work and buyer-capability path. The full **1,614-patient** original-cohort warehouse is evaluated. Existing domain source reviews and paid answers are reused.

| Program | Received-account review candidates / persistent items | Others with unknown need in this source selection |
|---|---:|---:|
| Respiratory source review | 100 | 1,514 |
| Depression/anxiety source review | 24 | 1,590 |

All 124 items are **unassigned and review-needed**. Two selected assessments retain their previously saved machine answers, with reassessment required after attaching that context. These are evidence-review nominations, not confirmed care gaps, diagnoses, current assessments or service eligibility.

The shared registry accepts explicit local adapters and prevents duplicate IDs or shadowing existing programs. New assessments bind their selected adapter and shared engine. Adding an unrelated registered program does not invalidate selected work. Historical and functional engines remain byte-identical. The UI now supports population composition, patient-specific work preparation, persistent source observations, domain-specific unresolved requirements and assessment-origin buyer matching.

The question planner now exposes domain and functional source-review contracts. Four exact commands compile locally: `Review respiratory sources across the population`, `Review behavioral sources across the population`, `Prepare respiratory source-review work`, and `Prepare behavioral source-review work`. Additional patient, time, payer, negation or clinical-action qualifiers are not stripped to fit these commands. The existing planning path handles unsupported questions separately; this delivery does not validate arbitrary natural-language composition.

Actual HTTP and browser verification covered population → work proposal → saved work → attached existing answer → buyer-specific internal evidence packet. Two additional assessment-origin buyer tasks are handoff-prepared for the existing fictional buyer `synthetic-domain-source-review`. No clinician review, clinical service delivery, offer, enrollment, contact or external execution was created. Need tags remain explicitly operator-selected; automatic clinical service matching is unfinished.

- Respiratory demonstration: `SYN-3C114EFC-C0C`, assessment `3e294071608c14950a2f2167c32442e37bcaf1d46ba98bdc4a42bd9dba773dea`, task `c3c213ae30badc57604a3d8adc11b30de9ecb727da267b9a3561040b093035d3`.
- Behavioral demonstration: `P-7193`, assessment `1d3886e6f97b447446ad67c8e3d09ab11107c1741b5cee0b2c2689c604a42146`, task `358675575fb73a31ff0ef989b1d2db48412e60856844044019598cab44ec3b42`.

**Verification:** 113 unique focused checks, including 23 new domain/registry/planning checks, plus actual HTTP/browser flows and JavaScript/Python syntax checks. Tests cover stale evidence and selected-adapter rejection, unrelated-adapter stability, per-patient scope, unknown membership, mixed historical/functional/domain graphs, optimistic revisions, idempotence and attachment preservation. Full-population refresh retained 100 respiratory and 24 behavioral items unchanged; stale answer-dropping proposals returned 409 and rolled back. The first HTTP harness searched only a 25-item page; it was corrected to follow pagination. The original failure log is retained. This was a harness correction, not a clinical result.

**Preservation:** all 73 earlier protected files, eight saved paid answers and four previous buyer tasks remain current; both new tasks also verify. The 256 functional work items and 257 events retain the exact serialized digests recorded by the preceding receipt. All 19 external research Markdown files and the active warehouse configuration remain unchanged. There was no warehouse migration, generator edit, training, new human label or model qualification. No new model or token-count calls; estimated incremental API cost **$0**. Usage accounting remains uncapped and unchanged.

**Remaining gaps:** the respiratory saved answer's unsupported identity hypothesis and abbreviated source-reference questions remain unchanged and unvalidated. The new assessment packet carries the full saved answer as machine context; its rationale explicitly does not endorse that hypothesis. Behavioral records remain undated condition labels, without current symptoms, standardized screens, treatment response or safety assessment. Broader source semantics, exact question/reference resolution, reviewed clinical knowledge, patient goals, payer applicability, real execution integrations, useful qualified prediction/discovery and full product acceptance remain incomplete. Stale-answer retirement/supersession and preservation of every optional decision/financial context are still missing; never drop attachments to bypass a stale check.

Evidence: [implementation receipt](artifacts/agere-domain-programs-v1/implementation-receipt.json), [HTTP verification](artifacts/agere-domain-programs-v1/http-verification.json), [local question verification](artifacts/agere-domain-programs-v1/question-verification.json), [browser verification](artifacts/agere-domain-programs-v1/browser-verification.json), [preservation](artifacts/agere-domain-programs-v1/preservation.json), [unique checks](artifacts/agere-domain-programs-v1/test-summary.json).

Preserve `registered_program_work.py` and `domain_programs.py` for these saved assessments, alongside all previously frozen modules. Extend `work_adapters.py` with separately versioned adapters; do not mutate the older engines or the frozen domain source catalog. Current server: PID **96700**, session **51726**, port 8885. Full queue acceptance remains false.
