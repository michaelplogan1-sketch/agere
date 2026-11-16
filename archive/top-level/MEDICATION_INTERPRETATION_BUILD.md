# Selected medication interpretation and honest partial evidence

October 11, 2026. Executable local synthetic workflow; clinical accuracy and full-stack readiness are not established.

Medication source reviews and their existing pharmacy handoffs now let the user select exact original questions and received records, prepare a bounded interpretation context, inspect saved model results and attach those results to the original review. The task shows its current answer coverage without changing the original handoff revision or documentary question results.

## Delivered behavior

- Select 1–3 exact review questions and 1–24 distinct medication/narrative records. Preserve complete selected values, original contracts, source identities and hashes; show excluded records, narrative omissions and links outside scope. Canonical selection requests replay idempotently. Cross-patient, stale and mismatched attachments reject.
- Return a completed bounded interpretation only after the required source and reconciliation stages pass. It is a machine interpretation for review; it does not resolve the original documentary questions or establish a current regimen.
- Recover a saved ambiguous reconciliation quote only when it lies inside one unique, already-supplied verified parent excerpt. Preserve the original quote and raw-response digest, then rerun the strict parser. No fuzzy quote matching or clinical prose repair occurs.
- Retain valid source findings when the provider omitted unit dispositions, while marking every omitted unit as system-unassessed. A source pass with an invalid single-unit conflict is quarantined in full; no findings from that pass enter the answer. Other unsupported failures still reject.
- Attach an explicitly partial source answer to the actual pharmacy task when its indivisible reconciliation component exceeds the request bound. It remains separate from completed interpretations and carries the blocker, omissions and quarantined-pass counts. Attempts to attach it as completed reject.

Recovery reuses exact cached responses and preserves failed ledger rows and raw outputs. It does not resend failed requests or bypass pending outbound approvals. Remaining unattempted groups can use the existing bounded provider path.

## Actual source cases

| Case | Exact selected scope | Result | Important limits |
|---|---|---|---|
| P-7193 | 2 original questions, 13 records, 22 transport units; 155 received records outside scope | 3 source passes and 3 reconciliation groups completed; 12 retained source findings plus 12 reconciled findings, 55 exact citation occurrences verified | Cross-group relations remain unassessed. Original three documentary questions unchanged. Exact-plan mismatch still blocks pharmacy queueing. |
| P-1001 | 2 original questions, 17 records, 30 transport units; 502 of 519 received records outside scope | 17 accepted source findings and 16 review proposals; 18 model-accounted units, 12 system-unassessed units, one quarantined pass; 32 exact citation occurrences verified | Cross-pass reconciliation remains blocked by a linked component larger than the 20 KB bound. No reconciliation request was sent. A clearly labeled partial attachment is visible in the existing pharmacy task. |

P-7193 distinguishes a historical sertraline dose plan from a later medication list, copied pharmacy history from independent dispensing evidence, and one declined facility dose from longitudinal non-use. The model also unnecessarily questions identity despite matched canonical metadata because raw source identifiers differ. This is a known semantic-quality defect, not evidence of a patient mismatch. Excerpt reconciliation cannot independently reverify details outside its supplied excerpts, and retained source-pass claims need clearer separation from consolidated claims.

P-1001 retains unverified medication directions, account-specific dates and the limits of a pharmacy lookback window. Eight units lacked model dispositions; four additional units belong to the fully quarantined invalid-conflict pass. Late follow-up reports in that pass remain unassessed. The answer cannot establish the complete current regimen or non-use.

The existing P-1001 task `972c00cfb436e95844ab5b7d46f338938e1d40f96522c812d4669bb61d0801db` remains at revision 2, `handoff_prepared_for_review`. No new task event, clinical review, benefit confirmation, consent or external action was created.

## Verification and evidence

**82 unique focused checks passed, including 37 new checks:** 76 in the combined medication/source/work/reference/recovery/reconciliation suite and six partial-attachment checks. Tests cover immutable bindings, freshness, race checks, idempotence, exact citation expansion, offline saved-response revalidation, unassessed coverage, quarantined output and incomplete-answer rejection. Initial fixture failures were repaired; overlapping earlier runs are not added to this count.

Actual HTTP checks exercised selection, paid interpretation, saved-response recovery, completed and partial attachment, cache replay with zero new calls, cross-patient rejection, partial-as-completed rejection, unchanged original questions and unchanged task revision. Browser checks exercised selection and partial attachment in the real pharmacy task, visible omissions and answer coverage. No captured browser console errors. This turn did not repeat the entire main-question creation flow verified by the preceding source-review milestone.

Preservation passed for all prior 117 protected files, now extended to **122**, all **380 work items / 383 work events / 11 capability tasks / 33 capability events**, prior clinical/reference/operational/information scenarios and **19 separate Documents research files**. Original-cohort v1.23, reserved cohorts, source-family boundaries and frozen experiments remain unchanged. Citation audit verifies literal source location, not semantic or clinical correctness.

Evidence lives in `artifacts/agere-medication-interpretation-v1/`: `implementation-receipt.json`, `http-verification.json`, `browser-verification.json`, `browser-partial-handoff.png`, `source-and-usage-audit.json`, `preservation.json`, `final-focused-tests.txt`, `partial-tests.txt`, exact patient selections/answers/attachments and retained failure diagnostics. Continuation uses `protected-for-continuation.json` and `history-baseline.json`.

## Costs and remaining work

Twelve new provider calls returned usage: nine validated and three failed original output validation. Estimated cost including those failed responses is **USD 1.00736**. Zero failed requests were resent. Historical ledger totals are **821 validated / 13 failed**, with **USD 7.083111524 validated-request estimated cost**; that historical figure excludes failed-response cost. Local compute and review labor are unpriced. Spending remains uncapped. Three earlier exact outbound payload approvals remain pending and untouched; no new approval rejection occurred in this milestone.

Next: make large linked reconciliation components executable with complete, auditable source/claim coverage; distinguish canonical patient identity from raw source identifiers; and separate retained source-pass claims from consolidated conclusions. Preserve every existing answer and partial attachment. Do not silently split connected evidence into independent pieces, drop evidence to fit the bound or retry failed/approval-blocked payloads. Then continue the full clinical/payer, qualified prediction/discovery, empirical operations, reference-currency and portable acceptance queue. Full acceptance remains false.
