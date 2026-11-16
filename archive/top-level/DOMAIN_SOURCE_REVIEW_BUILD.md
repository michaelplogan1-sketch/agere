# Respiratory and depression/anxiety source review

Delivered October 10, 2026. Executable in the Analysis library, using the original-cohort v1.23 warehouse at the 2029-08-15 synthetic cutoff. This is source-supported review, not validated diagnosis, optimal treatment, current clinical state or benefit eligibility.

## Delivered behavior

One shared operator builds immutable, source-bound population receipts for two bounded domains. It selects exact received field values, retains source/record/arrival dates separately, displays literal observations and same-date conflicts, and leaves absent sources unresolved. Unknown dates are not replaced with arrival dates. All matching condition labels plus latest usable dated accounts are suggested for an explicit record selection; older records remain available and are not silently submitted.

| Source scope | Roster | Patients with matching accounts | Patients without those accounts | Records | Records without usable source dates |
|---|---:|---:|---:|---:|---:|
| Respiratory condition labels, asthma interviews, spirometry | 1,614 | 100 | 1,514 | 838 | 101 |
| Depression/anxiety condition labels | 1,614 | 24 | 1,590 | 26 | 26 |

Respiratory review exposes 92 latest reports of unconfirmed reliever directions, 11 reports with no postbronchodilator test and 22 missing reference-value questions (two per spirometry report). These counts overlap and do not establish a treatment gap, COPD, severity or rehabilitation indication. Behavioral coverage is condition-list evidence only; the adapter does not assess current symptoms, screening, treatment response or safety. No matching source is not a negative clinical finding or proof of absent care.

The visible flow is population source review → exact records → existing bounded source synthesis → unresolved evidence needs → buyer-specific internal handoff. Two saved demonstrations use three respiratory records/five units and two behavioral records/four units. Respectively, 2,602 and 166 other received records remain outside the submitted content. Saved answers replay without new inference. Public guidance appears with applicability limits and is not silently inserted as patient evidence or model input.

The explicitly fictional buyer `synthetic-domain-source-review` has two local evidence-worker options and two clinician-review examples. Pulmonary rehabilitation and behavioral-health integration are attached as public pathways requiring verification. Neither clinical example is queued or authorized. Only local evidence-packet tasks were prepared; no human review, patient contact, enrollment, service provision or external action occurred. Need tags and finding selection remain operator-selected.

## Evidence and quality limits

55 focused tests passed, including 23 new domain tests. Actual HTTP checks cover full-roster counts, pagination, exact record selection, invalid selection rejection, bounded synthesis, saved replay, source-needs lookup, buyer matching, idempotent queueing and internal handoff. Browser checks cover the visible respiratory workflow and its service boundaries; see the browser receipt for its exact scope. 70 previously protected source/config/model files, six earlier saved answers, existing functional work, two earlier buyer tasks and all 19 external Documents research Markdown files were preserved.

Both new answers have exact source citations and machine-proposal status. An automated development review found an unsubstantiated respiratory identity-conflict hypothesis based on differing source-system patient IDs despite the matched warehouse binding. The original answer remains unchanged; that hypothesis is excluded from the operator-selected buyer handoff. The model abbreviated order/session IDs in its proposed question, so automatic evidence-needs binding remains incomplete even though full references occur elsewhere in its findings. Do not treat successful schema/citation checks as clinical validation. The behavioral answer preserves labels, partial onset years and unanswered current-state questions; no clinical reviewer or independent accuracy labels exist.

Two new Claude calls and two token-counter calls used 9,957 actual input and 4,529 output tokens, costing **$0.130408 estimated**. The shared ledger remains uncapped, with 812 validated and 10 failed historical call records and $6.292875524 in priced historical estimates after this milestone. These are not a provider invoice, total development cost or a complete calendar-day bill.

## Source grounding

Primary public sources were inspected October 10, 2026: [NHLBI asthma treatment/action plans](https://www.nhlbi.nih.gov/health/asthma/treatment-action-plan), [Medicare pulmonary rehabilitation](https://www.medicare.gov/coverage/pulmonary-rehabilitation-programs), [USPSTF adult depression/suicide-risk screening](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/screening-depression-suicide-risk-adults), and [Medicare behavioral-health integration](https://www.medicare.gov/coverage/behavioral-health-integration-services). The catalog records their limited application to source review. Public guidance is not an exact member-benefit decision or evidence of future synthetic-date validity. No screening recommendation is applied as a treatment protocol to diagnosed/symptomatic patients.

## Remaining work

- Extend domains through a selected-adapter program registry and population graph without invalidating saved historical or functional assessments. Respiratory/behavioral currently enter persistent work through source answers and buyer tasks, not dedicated program definitions.
- Harden optional machine-context preservation when bulk program assessments are saved, and preserve exact source references in proposed review questions.
- Expand beyond literal selectors and condition labels using supported source semantics, independent reviewed evaluation, patient goals and exact benefit/participation evidence. No full-chart recall or broad clinical quality is established.
- Continue qualified current-source prediction/discovery, broader VBC/contract reasoning, governed execution adapters and portable full-stack acceptance. Earlier frozen model results and research proposals remain as recorded.

Implementation: `clinical_core/agere/clinical/domain_review.py`, `config/agere/domain_review_sources.json`, `clinical_core/agere/clinical/assets/domain_review.js`, additive service routes and workspace loading. Domain engine/catalog identities are now receipt-bound; preserve them when extending behavior. Evidence lives in `artifacts/agere-domain-review-v1/`, especially `http-verification.json`, `quality-observations.json`, `preservation.json`, source/context/answer receipts and usage snapshots. No source migration, generator modification, hidden-truth access, frozen-cohort change or external research edit occurred. Full queue acceptance remains false.
