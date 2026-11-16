# Original-cohort warehouse and evidence-resolution build

October 9, 2026. Implemented and activated in the personal Agere project. All data and patient examples are synthetic. The active configuration is `config/agere/active_warehouse.json`; workbench: http://127.0.0.1:8885/stack.

## What is now connected

The original-cohort v1.18 source is pinned by SHA-256 and patient-roster digest. The migration retains every previous received row unchanged, adds the newer received records, and preserves the previous activation configuration. Runtime uses received public source data; generator private labels are excluded. This is a transport and interpretation integration, not a clinical-accuracy claim.

Eight additional medication/allergy source relations now preserve patient interviews, reported products, pharmacy requests and responses, received outside information, clinician reconciliation records, clarification contacts and allergy-history reports. Same-patient typed references remain explicit. Reported use, historical listings, prescribing intent, pharmacy activity and verified ingestion remain distinct.

New narrative fields participate in shared retrieval and the existing source-linked chart reader. Nested echo measurements use exact `measurements.LVEF_percent` values, report quality/status, literal units and order/session/original-report dependencies. No test date is fabricated from document receipt time. Accepted historical measurements can nominate documentary protocol review; they do not establish a current indication or treatment decision.

Native account predicates now compose into reusable documentary requirements and program-review proposals. A new evidence-resolution layer calculates inclusion-minimal sufficient binding assignments under three-valued Boolean logic, including shared variables and negation. Every returned assignment is independently evaluated against the expression. Bounded computation reports incompleteness instead of presenting a truncated result as complete. These are established Boolean methods applied to this evidence problem, not a claim of invented mathematics.

The evidence-acquisition layer converts unresolved conditions into source-specific review proposals: locate an account, clarify a literal field, retrieve or verify a linked source, resolve competing accounts, verify a statement date, or review narrative interpretation. Proposals preserve the chosen population and source/rule version, deduplicate equivalent requests, paginate without changing denominators, and reject stale source or review dependencies. Refreshes reuse unaffected patient receipts. No requests are sent and no patient care is executed.

The workbench exposes an evidence-review queue builder using its population filters, per-item blocking-source inspection and conditional evidence sets. A derived-index WAL configuration separates active source readers from short cache commits; the pinned input warehouse is untouched.

## Measured source and execution counts

| Observation | Result | Receipt in `artifacts/agere-healthcare-v118/` |
|---|---:|---|
| Original synthetic patients | 1,614 | `connection.json` |
| Received and searchable records | 1,049,339 | `connection.json`, `verification.json` |
| Source categories | 113 | `connection.json` |
| Added records; prior rows changed/missing | 155,116; 0 | `migration-audit.json` |
| Searchable passages | 1,959,342 | `stack/run.json` |
| Cached narrative embeddings reused; newly encoded texts | 74,967; 14,651 | `stack/run.json` |
| Received echo measurement records | 115 | `diagnostic-verification.json` |
| Usable literal echo measurements | 96 | `diagnostic-verification.json` |
| Patients with usable received LVEF ≤40 | 14 | `diagnostic-verification.json` |
| Patients with newly mapped reported-product accounts | 860 | `diagnostic-verification.json` |
| Evidence-review proposals across four declared contracts | 5,990 | `acquisition-verification.json` |
| Requests to locate source accounts / clarify fields / reconcile accounts | 4,892 / 1,089 / 9 | `acquisition-verification.json` |
| Patient-contract results reused on repeat | 6,456 | `acquisition-verification.json` |
| Migration/local acquisition API calls / requests sent | 0 / 0 | verification receipts |

The four-contract acquisition demonstration deliberately evaluates every patient under every declared documentary condition. Its request counts are **not** clinically indicated care gaps, prevalence estimates, or a recommendation to seek every document for every patient. The selected contract and population define the work.

## Full-population documentary queries

Each evaluates all 1,614 synthetic patients. Exact supported and unresolved patient sets agree with independent queries of public received source accounts. Repeats reuse all patient receipts. Source: `native-requirements/verification.json`.

| Declared documentary condition | Supported | Contradicted | Unresolved |
|---|---:|---:|---:|
| Received affordability difficulty report | 115 | 334 | 1,165 |
| Received medication non-use report | 69 | 78 | 1,467 |
| Outside service documentation with resolved links | 106 | 0 | 1,508 |
| Advance document marked legal validity not determined | 223 | 0 | 1,391 |

The affordability condition feeds 115 program-review assessments with exact selection evidence. These are internal review proposals, not enrollments or eligibility decisions.

## Training acquisition, distinct from model qualification

A new original-source lineage adapter uses preserved patient IDs as source-family units. It excludes 52 frozen Kev/d1 families from fitting. Unprotected partitions contain 1,102 training, 233 calibration and 227 validation patients. These are development partitions; prior reader exposure and possible shared wording/households prevent treating them as a new blind evaluation.

The source-learning scan covers all 1,049,339 received records. Its 96-item acquisition queue spans 77 synthetic patients/families, with 24 extraction audits, 24 score-independent audits and 48 interpretation-priority items. It produces no human labels and no new model weights. No-annotation fields are audit candidates, not proven extraction failures. Source: `source-learning/lineage/manifest.json` and `source-learning/queue/run.json`.

The frozen Kev/d1 experiment is unchanged. Old source-specific shadow students and forecasts are not attached to this new source contract without separate qualification. No model winner, clinical superiority or outcome improvement is established here.

## Verification and retained failures

- `regression.log`: 149 focused source/clinical/context/planner checks.
- `learning-regression.log`: 15 lineage and protected acquisition checks.
- `acquisition-regression.log`: 63 focused acquisition, logic, concurrency, native requirements and activation checks.
- `verification.json`, `native-requirements/verification.json`, `diagnostic-verification.json`: qualified source, full-population and diagnostic-to-protocol checks on the same snapshot and final qualification code identity.
- `acquisition-verification.json`: exact full-population work, repeat reuse and inspection of every observed request kind.
- `activation.json`: prior configuration retained under `config/agere/activation-history/`.
- Planner request for the maximum tested 1,500-character ASCII question: 19,985 serialized bytes, under the existing 20,000-byte limit; no capability enumeration was dropped to pass it.
- A first cache-path attempt and a concurrent-query SQLite lock failure are retained in their logs. The builder now accepts the intended cache path form, and derived-index WAL allows source reads alongside cache commits. A meaningful held-reader/concurrent-writer test verifies the fix. A request-size failure was fixed by shortening descriptive prose; the guard was not raised.

## Post-activation specialist-source integration

The new source-learning queue has now exercised both existing bounded API readers on six selected synthetic source statements: 12 successful readings, no execution errors. Jev and Perplexity disagree on the temporal axis for four of the six statements. This is a disagreement finding, not evidence of which reader is correct. All 12 source bindings and exact target spans were rechecked; the readings replayed from the local cache with live access disabled.

Logged usage is 17,274 input tokens and 1,582 output tokens; the configured pricing estimate totals $0.000536682, not invoice-verified and excluding local compute/human review. This separate post-activation batch made 12 paid API calls. No reviewed labels or new weights were created. Receipt: `source-learning/specialist-summary.json`; individual provider/source receipts remain attached to the new stack.

The live API checks pass in `http-contracts.json`; the medication acquisition flow and source inspection were exercised through the browser in `browser-verification.json` / `workbench-dom.txt`. Browser console errors were absent. Screenshot capture returned blank frames or capture errors, so no screenshot is presented as visual proof.

## Concrete remaining build work

Full transport/search coverage is not universal clinical interpretation. Next work is item-level medication reconciliation across the newly received interview, pharmacy and clinician-review records; wider source-bound narrative concepts and relations; requalification of economical local interpretation routes; and development training with explicit supervision provenance. Broader program/contract applicability, outcome attribution, useful calibrated prediction, customer adapters and deployment remain. Follow `AUTONOMOUS_STACK_QUEUE.md`; do not restart the old 10,000-patient development variant or repeatedly rebuild an unchanged source release.
