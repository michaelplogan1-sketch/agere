# Received operational timing and explicit duration sensitivity

October 11, 2026. Local synthetic engineering capability; no clinical or staffing validation.

## Delivered behavior

Saved resource and dependency questions now open an immutable received-source review for exactly their selected task patients. The buyer supplies an appointment period and inclusive receipt-day cutoff. The review retains appointment, encounter and referral fields, exact record hashes, source clocks, explicit links and denominators. It reports unknown dates, unreceived links, duplicate completion reports, conflicting status histories and imprecise clocks without imputing them.

A unique fulfilled appointment report can link to a unique completed encounter report only within the same patient, source system, organization and source-patient identity. Earlier booked/in-progress reports remain visible. Inconsistent scheduled dates, start clocks or terminal statuses prevent interval summarization; no source is silently declared superseded. Offset-aware end-minus-start intervals and end-to-source-arrival delays remain separate. Raw observations are the baseline for exact mean, median and range summaries stratified by patient, source and encounter type. Referral fulfillment, patient waiting and staff effort remain unknown.

The saved review also supports an explicit duration-sensitivity question. Buyers supply low/central/high task durations, provenance and an applicability rationale. The original task order, services, patients, pools, readiness, dependencies and horizon are retained. Every original simulation must reproduce before the alternative is saved. Source observations never automatically replace task-duration assumptions.

## Actual application example

The existing `synthetic-domain-source-review` scenario selects four internal tasks for P-7193 and SYN-3C114EFC-C0C. Its received scope contains 297 distinct records: 106 appointment reports, 184 encounter reports and seven referral reports. 103 appointment records fall inside the selected scheduled period; two lack a usable scheduled date and one falls outside it. There are 46 usable linked intervals: two for P-7193 and 44 for SYN-3C114EFC-C0C. Remaining reports and precision exclusions remain inspectable. These are not 46 independent patients or validated care outcomes.

Both resource and dependency parents have saved contexts and comparisons. An engineering assumption of five extra minutes per selected review task changes central batch finish from 80 to 85 hypothetical minutes. At the unchanged 30-minute horizon, completed tasks change from 2 to 0 for the resource scenario and from 1 to 0 with the existing dependencies. These differences describe supplied assumptions, not measured staffing performance or care benefit.

UI path: Care capabilities → buyer `synthetic-domain-source-review` → Compare capacity for selected internal work → Load saved capacity comparisons → Model service teams and task readiness → saved resource scenario → Review received appointment and encounter timing. The same review is available under saved dependency scenarios.

## Verification and evidence

- 68 focused tests, including 31 new tests for timestamps, midnight/timezone arithmetic, precision, receipt cutoff, record histories, exact scope, conflict/duplicate exclusion, races, replay, immutable history and original-baseline preservation.
- Actual HTTP preview/save/replay/inspect/history and rejection checks for both parent types.
- Independent audit of 297 distinct source hashes and 1,386 literal fields, with 46 interval/arrival calculations and 24 summary statistics per attachment. The two attachments share this evidence scope; double-counted verification totals are identified in the receipt.
- Browser preview, save and explicit duration comparison exercised through actual form controls; persisted reload and screenshot evidence are recorded in the browser receipt.
- Broad preservation result and final checkpoint are recorded in the implementation receipt. An initial verification-harness variable shadowing failure was corrected; it did not alter product records.

Artifacts: `artifacts/agere-operational-context-v1/`. Scripts: `test_operational_context.py`, `verify_operational_http.py`, `verify_operational_sources.py`, `verify_operational_preservation.py`.

## Limits and continuation

This does not establish real staff effort, task comparability, source capture completeness, patient wait, verified capacity, outcomes, labor calibration or optimal scheduling. The store uses optimistic freshness checks rather than cross-store atomic transactions. All external execution remains unauthorized. No provider/token-count calls or new API cost were incurred; spend remains uncapped with accounting intact.

The five existing clinical-record outcome tasks remain not fitted because their qualified samples contain only one recorded class. Next work should define and qualify a different, explicit operational observation question before fitting: appointment follow-through after a received booking, with exact same-identity reports and missing/censored/ambiguous outcomes distinct from explicit fulfillment or no-show reports. Preserve original training-family partitions and frozen/reserved evaluations; qualify the endpoint and compare simple baselines only if its evidence supports fitting. Do not equate non-observation with nonattendance or claim clinical risk/benefit.

Continue broader clinical/payer applicability and production adapter work after this bounded operational path. Reviewed clinical recommendations, exact patient entitlement, production delivery integrations, reliable general interpretation and independent validation remain incomplete. No full acceptance or expert validation is claimed.
