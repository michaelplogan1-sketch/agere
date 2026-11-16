# Availability-aware synthesis and internal review work

Delivered October 10, 2026. The default route for **new** source contexts now supplies locally verified received-record availability to bounded clinical source passes and excerpt reconciliation. Previously saved answers and in-progress historical source stages retain their original engines and request bodies.

The provider sees which selected units are complete in its current request, which belong to other passes, which exact referenced records are received outside the selection, and which typed references cannot be resolved in the received same-patient snapshot. Literal known-ID mentions are identified separately from typed documentary relationships. Other records contribute only presence, category/date metadata and dependency fingerprints—not their clinical contents. The full envelope participates in request packing; indivisible overflow blocks rather than silently dropping units or metadata. Reconciliation explicitly records zero complete original units and counts only its cited excerpts.

The real answer UI displays this coverage. Existing evidence needs and stored program assessments accept the versioned answer without converting proposals into clinical state. Buyer-service tasks now have **Inspect answer coverage**, a read-only, source- and task-revision-bound view. It works for direct answer and assessment origins. Historical answers without inference-time availability are labeled explicitly; their handoffs are not rewritten.

## Live synthetic demonstration

- Patient `SYN-69283717-971`: four typed-linked functional-assessment, care-management and mobility-exam records; seven complete source units in one 16,804-byte request. The remaining **1,872 of 1,876 received records** were not sent as content.
- Context `a70c29b710a4f3ccb3b07c1af79a96a0310dc0921c0fee02087aa28237c133d8`; answer `4937dbd744271141f3ed99482cef4fce506c59eb3295096319c6841e524dd9fe`.
- Four source-linked findings, four review proposals and four unanswered questions. Five units marked discussed and two unresolved by the model. These are machine judgments.
- The answer recommends reviewing already received records before external acquisition and keeps payer eligibility, preferences and execution authority unresolved. One model wording issue remains: “homebound status was not assessed” interprets `homebound_assessed:false` beyond its literal Boolean. The accompanying narrative states that homebound status is not established. This requires source-semantic review; exact quote validation does not resolve it.
- An existing **affordability** assessment carries the answer as machine context. This tests the existing assessment bridge only; it does **not** implement or validate a functional-health population rule or establish an affordability need.
- Fictional buyer `synthetic-functional-review` has two declared test services. The local evidence-review agent received an internal packet with the exact selected findings. Task `85228500e2b2d835114e7eb80c762b0c7c129763a794a68e659b4aa4ba6a3c7c` is `handoff_prepared_for_review`. No clinician reviewed it, no service was delivered, and no external action occurred.

## Verification and usage

**49 focused checks pass**: complete-unit coverage and transport limits, excerpt-only metadata, received-but-unsent content isolation, exact literal-ID boundaries, absent-reference scoping, patient/source identity, changed source invalidation, failure/recovery, historical replay, source citation checking, assessment binding and task-revision races. Multi-pass availability reconciliation is covered by deterministic tests; this live case fit one pass.

Real HTTP checks cover preview, replay, evidence needs, stored assessment, declared buyer matching, idempotent internal queue/handoff and both new/historical task-availability views. Browser checks cover the answer and handoff inspector. **61 prior protected files**, five earlier paid answers and **19 external research files** remain unchanged/current. Active original-cohort v1.23 remains unchanged. No fitting, human labels, independent clinical evaluation or model promotion was performed.

One new Claude request and one token count; actual provider usage **7,151 input / 1,967 output tokens**, **$0.067944 estimated**, not an invoice. The token-count endpoint reported 7,156 input tokens. Exact replay added no inference. The shared ledger remains uncapped; its priced historical total is $6.162467524, not a whole-project or daily invoice total. No failed provider calls were added. One read-only localhost check initially ran before server startup, then passed after the server was ready; the startup log is retained.

## Remaining work

Broader source-supported clinical/program review and population recommendations are still needed beyond the current three protocol definitions. Prioritize functional/support, respiratory, behavioral and other curriculum domains using exact received evidence, current primary program/guideline sources and explicit patient-goal/benefit requirements. Address structured-field semantic ambiguity through versioned source interpretation rather than overwriting paid answers or accepting Boolean shortcuts. Buyer matching still uses declared services and operator-selected needs; it does not prove clinical indication, member coverage, network, consent, provider participation or outcome benefit. General prediction/discovery, independent clinical quality and portable whole-stack acceptance remain incomplete.

New receipt-bound modules: `synthesis_availability.py`, `availability_source_stage.py`, `availability_reconciliation.py`. Preserve these after this live delivery; future changed prompts or contract behavior require an explicit new version. `synthesis_work_availability.py` is the separately fingerprinted read-only work adapter. Historical pinned engines, learning partitions, generator and Documents corpus remain untouched.

Evidence: `artifacts/agere-availability-synthesis-v1/implementation-receipt.json`, `focused-tests.log`, `work-availability-tests.log`, `answer.json`, `http-verification.json`, `work-availability-http.json`, `preservation.json`, `availability-answer.png`, `availability-handoff.png`.
