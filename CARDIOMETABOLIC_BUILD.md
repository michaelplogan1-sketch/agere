# Cardiometabolic sources, review work and buyer responsibilities

Agere now has an executable patient review for received blood-pressure pairs, glycemic laboratory accounts, diabetes-response interviews and a bounded cardiometabolic condition-code subset. It connects exact source questions to persistent review work, internal buyer handoff, dated-reference applicability questions, an unranked comparison and component-level delivery proposals.

The implementation adds selected adapters and reference/component bundles. Earlier respiratory, behavioral, functional, medication and source-reader definitions are preserved. No original-cohort migration or generator change was made.

## Executable behavior

- BP records retain source-event, recorded and received dates, source hashes, units and missing measurement context. Separate systolic and diastolic records are never paired. Complete dated mmHg pairs receive exact rational daily means with individual records as the baseline. Duplicate encounter/time records, incomplete pairs, invalid values, units and missing dates are excluded with reasons. Differences between the two most recent observed days are descriptive only; no treatment response, hypertension classification or forecast is inferred.
- Glycemic and diabetes-response fields remain literal accounts. Same-date differences are retained, explicit `false` is distinct from missing, and recorded/receipt dates do not replace unknown source-event dates. Laboratory specimen links are visible but do not establish clinical diagnosis or treatment success.
- Users reopen immutable source reviews, select whole records for an evidence question, save source-bound review work and access the existing buyer matcher. Context preparation remains local; no interpretation call was made.
- An additive AHA summary bundle creates seven persistent applicability questions. The comparison distinguishes a local review baseline from considering home measurement with team follow-up. There are no comparative estimates or clinical rankings. A separate CMS notice describes current public program information; the frozen pathway catalog is preserved.
- Component review separates qualified clinical assessment, device/measurement support and coordinated follow-up. A generic internal evidence-review service establishes none of these delivery capabilities.

## Actual original-cohort case

Synthetic **P-1007** has **61 selected source records**: 22 BP records, including20 complete pairs on20 source-event dates and two isolated values excluded from arithmetic. Six selected records are connected to the exact assessment question. Seven reference-applicability questions remain unresolved.

One new program work item is `review_needed`, with no human owner or clinical review recorded. One engineering-only buyer task has an internal evidence handoff at revision2. Its saved comparison carries **59 received coverage reports and five patient/access reports** as separate review context; no current plan is selected and no benefit is verified. The home-measurement proposal leaves **all three delivery components unassigned**. No patient contact, titration, diagnosis, care delivery or service-slot reservation occurred.

## Public evidence inspected

- [AHA 2025 hypertension guideline official summary](https://professional.heart.org/en/science-news/2025-high-blood-pressure-guideline/top-things-to-know), published August14,2025; inspected October11,2026. Points3 and9 support the measurement/team process context. The full guideline, recommendation grading and supporting trials were not inspected, so none is claimed. DOI10.1161/CIR.0000000000001356.
- [CMS ACCESS model](https://www.cms.gov/priorities/innovation/innovation-models/access), current public overview, tracks and MA distinction inspected October11,2026. The dated notice is public context, not member entitlement, a verified provider contract or payment terms. Public inspection in2026 does not establish currency at the synthetic2029 decision date.

Both mappings are machine curated and unreviewed. The review approach and component decomposition are Agere workflow designs informed by the source, not source-reported head-to-head treatment alternatives.

## Verification

**70 focused checks passed, including18 new checks** across source arithmetic/missingness/identity, work persistence, selected bundle isolation and prior functional/component behavior. Initial verification exposed a repeated-installer marker problem; the additive wrapper now preserves existing install markers, and the combined suite passes. A mistaken test-module name and an overly broad future-date assertion were corrected; source-event exclusion and received-date visibility remain distinct.

Actual HTTP verification saves/replays source review, work, task, reference questions, source acquisition, comparison, coverage/access attachment and component proposals. Browser verification reopens the source review, saves existing work idempotently and displays current comparison/components. A cramped table header was adjusted and visually checked. No browser console errors were observed. The independent source audit verifies **61 hashes,566 literal fields and20 daily means**.

Evidence: `artifacts/agere-cardiometabolic-v1/` contains `http-verification.json`, `source-audit.json`, `browser-verification.json`, screenshots and exact saved artifacts. Broad preservation and current checkpoint details are in `implementation-receipt.json` and `preservation.json`.

## Remaining limitations

This is a bounded source-review adapter and process comparison, not clinically validated hypertension/diabetes decision support. Current measurement validity, confirmation, symptoms, contraindications, regimen, goals and patient agreement remain unresolved. The initial arithmetic does not normalize device protocols or prove independent observations. Medication evidence can be selected through existing question machinery but is not automatically synthesized by this adapter. Only explicitly supported field names and codes are included.

Full guideline appraisal, calibrated risk/benefit models, commercial/member-specific program terms, verified providers and actual clinical/device/follow-up delivery remain incomplete. Operational capacity inputs are still mostly supplied scenarios; production connectors, tenant/security/deployment work and broader product acceptance remain open. Five unchanged single-class prediction targets remain unqualified; no refit or promotion was performed.

This milestone made **zero provider/token-count calls and incurred zero new API cost**. Spending remains uncapped. The four existing exact outbound approval boundaries were neither retried nor changed. Full-stack acceptance remains false; autonomous continuation remains active.
