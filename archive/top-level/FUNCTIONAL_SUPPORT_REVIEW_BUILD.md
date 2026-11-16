# Functional and support population review

Delivered October 10, 2026, immediately after the availability-synthesis milestone. Agere now has an executable **Falls, mobility and support** review in the Analysis library. It reads the active original-cohort warehouse locally, nominates dated source accounts for review, displays exact supporting fields and missingness, and prepares the existing bounded evidence-answer workflow. This is an additive review operator; the earlier three protocol definitions and saved program assessments remain intact.

The review preserves three distinct things: a reported fall/support concern; a compatible recorded mobility-screen observation; and unresolved home-health requirements. It never turns `homebound_assessed:false` into “not assessed,” a clinical negative or an eligibility determination. The literal value and the accompanying source language remain available for review. The earlier Claude answer and its wording ambiguity are preserved rather than overwritten.

## What executes

The operator reads all received records in `functional_assessments`, `functional_mobility_exams` and `care_management_contacts`. For each field, it considers the latest usable **source date** within its category. Same-date disagreement remains conflicting; a missing value is not backfilled from older positive reports. Older records stay inspectable. Undated/future-source observations do not produce dated screening conclusions. No received record in these categories is an explicit unknown, not evidence of no clinical need.

A positive reported fall history or appointment/medication-support report nominates source review. A numeric TUG result at or above 12 seconds, with a compatible recorded test protocol and the configured reported age of at least 65, nominates mobility-source review. The threshold comes from the [CDC STEADI TUG assessment](https://www.cdc.gov/steadi/media/pdfs/STEADI-Assessment-TUG-508.pdf). The numeric age restriction is explicitly a conservative development choice: the quoted sheet says older adult without a numeric age. Recorded protocol wording does not prove correct administration. No individual probability, diagnosis or outcome benefit is inferred.

The [Medicare home-health source](https://www.medicare.gov/coverage/home-health-services) informs a separate review checklist covering skilled need, homebound criteria, provider evaluation/orders, agency requirements and exact-plan checks. The operator leaves eligibility unresolved. Patient goals, consent, test quality and clinical judgment remain missing requirements. Current public guidance was checked October 10, 2026; it does not establish policy validity at the warehouse's future synthetic cutoff.

## Active population evidence

| Measure | Result |
|---|---:|
| Roster patients | 1,614 |
| Patients with one or more selected-category records | 308 |
| Patients without those received categories | 1,306 |
| Source-review candidates | 256 |
| Received records in the three categories | 855 |
| Patients with latest reported fall-history signal | 102 |
| Patients with latest support-report signal | 199 |
| Patients with compatible recorded TUG threshold signal | 163 |

Signal groups overlap. These are observed source-review nominations, not confirmed care gaps, service indications or clinical prevalence. Other categories and outside care remain beyond this operator's scope.

Population receipt: `37421dee92cdae5cb293423bcdd9cc31bb36ba61f3652898b00b33b5013cf730`. The demonstration patient `SYN-69283717-971` has all three kinds of dated signal, while its homebound Boolean remains literally false. Preparing its four-record source context reproduces context `a70c29b710a4f3ccb3b07c1af79a96a0310dc0921c0fee02087aa28237c133d8` and replays saved answer `4937dbd744271141f3ed99482cef4fce506c59eb3295096319c6841e524dd9fe`. The existing evidence-needs receipt and buyer task remain linked through that answer. No new answer, clinician review or clinical state is fabricated.

The public guidance belongs to the deterministic review receipt; it is not silently presented as patient evidence or retroactively inserted into the saved model request. Buyer need mapping remains an operator decision after source interpretation. This operator is exposed in the actual Analysis library and context-to-answer workflow; it is not yet a composable analysis-graph node, a persistent functional program cohort, or an automatic buyer-service recommendation.

## Verification and remaining work

**16 new focused tests pass**, including inclusive threshold handling; age/protocol/missing/invalid numeric values; literal false versus missing/conflicting; same-date disagreement; later source updates; exact field evidence; undated/future observations; cross-patient/identity/synthetic checks; duplicate sources; hidden-field exclusion; complete roster denominators; tampering; source deletion; and exact context handoff. Combined with the preceding milestone, **65 focused checks passed this turn**.

Real HTTP verification covers population counts, pagination, patient inspection, exact context preparation, saved-answer replay, evidence needs and the existing buyer task. Browser verification follows the population review into the existing answer controls. **65 protected prior files**, five earlier paid answers plus the new availability-aware answer, and 19 external research files remain unchanged/current. The active warehouse, frozen learning datasets/models and generator remain unchanged. No additional paid calls, fitting or external actions occurred for this operator.

Next: broaden clinical/support interpretation and recommendations to respiratory, behavioral and other curriculum domains; add stronger source-semantic checks and reviewed interpretations; connect these review operators to persistent, composable population/program work and justified buyer-service topics. Retain exact member-benefit, patient-goal and execution requirements. Calibrated prediction/discovery and portable full-stack acceptance remain open. The earlier financial-account exact-payload approval is still pending and was not retried.

Implementation: `clinical_core/agere/clinical/functional_review.py`, `config/agere/functional_review_sources.json`, `clinical_core/agere/clinical/assets/functional_review.js`, additive service/workspace entry points. Evidence: `artifacts/agere-functional-review-v1/implementation-receipt.json`, `population.json`, `focused-tests.log`, `http-verification.json`, `preservation.json` and browser screenshots.
