# Focused verifier comparison — November 3, 2026

G3 remains active. The dependency-packet candidate passed the planned focused comparison and is selected in the owned local app on port8890 at product `168ae81624cb381cb7a41a4763370ff9baca9abc`. Primary8885/main is unchanged. This is a scoped engineering decision, not full MVP or clinical qualification.

## Observed change

The previous projection copied unrelated exploratory payer SQL contracts into all795 patient packets for a provider-only numeric question. The earlier offline proof removed73.56% of patient packet bytes while preserving all2385 numeric claims and the entire57-patient clinical claim/context projection. This comparison tests whether that change helps the actual typed verifiers without accepting deliberately wrong claims.

Three retained patient packets were selected by lexicographic position (first, middle, last), before any new model votes. Four authored mutations change source domain, amount, year or add an unsupported medication-barrier claim. These mutations are sent directly to the verifier as engineering stress cases; native guard acceptance is not inferred from them. Both projections contain the same final claims, source dates, documents and review scope. Existing packet/field prompts and0.8 thresholds are unchanged. No protected labels or generator truth were used.

| Provider / projection | Retained positives accepted | Authored faults explicitly rejected | Authored faults unresolved | False accepts | Attempts | API estimate | Serial case time |
|---|---:|---:|---:|---:|---:|---:|---:|
| Jev / previous |0/3|4/4|0/4|0|14|$0.009586878|3.521s|
| Jev / current |3/3|4/4|0/4|0|11|$0.001974336|2.452s|
| Perplexity / previous |3/3|1/4|3/4|0|11|$0.003506080|12.355s|
| Perplexity / current |3/3|4/4|0/4|0|11|$0.000855920|4.093s|

All47 provider attempts validated and remain in four fresh arm ledgers, with complete raw responses, errors, model labels, usage, per-case telemetry and receipt files. Total estimated API cost is$0.015923214; no attempt is unpriced under the recorded pricing table. The original shared34062 rows remain exact. Entire comparison wall time was22.517s. Arm order alternated; there was one observation per case/arm, so these times are not robust latency benchmarks. Costs are API estimates, not invoice confirmation. Both candidates pass this narrow development set; this is not a reason to duplicate every production judgment across both providers or change routing globally.

Mike confirmed credits were restored; all22 Perplexity attempts succeeded. Responses identify `pplx-decider-v1.1-27b`. Current [official pricing documentation](https://docs.perplexity.ai/docs/getting-started/pricing) says that name now routes to v1.2 at$0.02 per million input tokens. Requested/returned names and this discrepancy are preserved; the precise backend checkpoint is not independently established. Older model scores do not qualify a changed backend.

## Selection and preservation

The previously scoped90 affected regression checks and the retained whole-packet comparison remain applicable: this turn changes no product code. The new evidence supports selecting product168ae81, including the previously verified native-progress and provider-source guards. The owned app is ready atPID5728/session12656. The exact prior22c8c27 source archive, hash and launch references are retained in `rollback.json`; live sources and the provider ledger are never rollback targets.

Real HTTP inspection preserves all57 clinical rows and12 financial rows exactly, with no provider calls or exports. Their old partial/answered statuses remain historical. Both contexts are correctly invalidated for new work because the planner policy changed; they were not rebound or presented as fresh results. Source dates/bytes are unchanged. Users can inspect the historical evidence; a current ordinary answer is the next executable check.

## Remaining and next action

The focused numeric verifier comparison passes, but no current ordinary whole-question performance confirmation or complete clinical acceptance is established. Historical57-patient and12-row contexts remain exact and inspectable, explicitly invalidated for new work under the changed planner policy; no inference was rebound. Original clinical answer is partial with522 unresolved field judgments. Independent clinical/model qualification and customer-deployment prerequisites remain separate; OWASP/HHS deferred.

Run one normal application request for the original provider-claims numeric question on candidate168ae81, retaining the automatic route, exact provider-only/year scope, native results, all attempts, total cost and latency. If qualified execution reuse answers it, report that actual route; do not mislabel it as investigator evidence or force frontier calls for appearance. If investigation is needed, verify the measured progress/source corrections and stop only on an observed failure or service limit, not an invented call cap. Do not rerun the full clinical cohort or restamp historical answers as fresh inference. Then assess the remaining clinical source-reading uncertainty using retained dependencies and targeted evidence. Preserve all ten areas and active quiet continuation.

Evidence: `artifacts/agere-focused-verifier-comparison-2026-11-03/delivery-receipt.json`. Durable copy: `/Users/mikel/clinical-insights-local/artifacts/wollstonecraft-focused-verifier-2026-11-03`. No new weights, model thresholds, routine PDF, whole-clinical rerun, new services, main merge or publication.
