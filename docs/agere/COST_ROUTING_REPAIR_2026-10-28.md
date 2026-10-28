# Cost routing correction — October 28, 2026

Mike rejected the recorded $14.18 analysis/debugging subtotal and $1.77 incremental cached analysis attempt as commercially unacceptable. Neither is a measured production cost per accepted answer. Claude used the subscription; the dollar figures are versioned API equivalents, not external Anthropic charges. Uncapped authorization does not establish acceptable unit economics.

## What caused unnecessary expensive work

The final investigator was forced onto Sonnet before this same tool loop had tried Haiku. Earlier Haiku interpreters declining a different role was treated as failure of the investigator. A nested reader could also inherit a parent's escalated rung.

The initial full document-review pass recorded 54 Haiku responses and 23 Sonnet responses over all 57 patients and 4,610 received documents. Its 77 typed checks include 27 unresolved checks with provider errors. The code escalated a validated Haiku extraction when the verifier failed, re-extracting with Sonnet without repairing the verification service. These aggregate observations do not identify every historical escalation's cause or predict a production saving.

Every typed call also asked for one supporting source. For aggregate verification this question is unnecessary: all sources remain evidence, and selecting one is not entailment. The largest patient package contained 314 documents, producing 315 citation choices, above TypeSafe's documented 255-option limit. Historical HTTP400/422 response bodies were not retained, so exact rejection causes remain unconfirmed. Eighteen Jev steps also reported an invalid probability distribution; the parser now identifies which answer field failed, without relaxing its validation.

## General changes in the existing stack

- The investigator starts on Haiku. A failed answer/format/progress check can escalate the same conversation to Sonnet, retaining the executed native tools and every check. No restart or paid provider-outage retry is introduced. Escalations retain reasons and the actual model identity.
- Each reader starts its own first rung even under an escalated parent. A failed verifier leaves exact source-quoted findings explicitly unverified; it does not by itself buy another extraction. Genuine semantic uncertainty or contradiction can still escalate.
- Document and final-answer verification omit the unused single-citation selection. All evidence remains supplied. Existing citation-selecting callers and quote validation keep their checks. No clinical thresholds were lowered.
- A repeated failed check with unchanged evidence stops as partial even when the actor rewords its answer. The reviewer runs first, so a corrected answer can pass; new evidence or a different failure permits continued work. This is progress detection, not an assistant-selected call/spend cap.

## Paper and practical application

A likely match for Mike's paper is [Decision models can offload structured clinical tasks from large language models](https://www.medrxiv.org/content/10.64898/2026.10.09.26365130v1). Its recommended decomposition improved Jev's staging accuracy; fully confident staging decisions plus an LLM fallback cut flagship costs by 33–44% under the study's protocol. It does not establish that Jev alone answers almost every healthcare query. The author's [code and aggregate results](https://github.com/deanariel-md/decision-model-offload) were inspected, not executed or adopted as Agere labels.

TypeSafe's [extraction cascade](https://docs.typesafe.ai/cookbooks/sde_cascade) recommends cheap extraction, focused verification and selective reasoning. Its [value-extraction pattern](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook) selects received spans that code copies. Its [Choice documentation](https://docs.typesafe.ai/primitives/choice) documents the option limit; [model documentation](https://docs.typesafe.ai/models) gives separate request/question context limits. These are design inputs, not Agere qualification results. The study's confidence policy is not installed as a universal threshold.

## Validation and remaining acceptance

Sixteen identical authored routing cases exercise the existing cascade, shared tool loop, reader and typed-provider adapter. Pre-fix source has four failed assertions and eleven missing-interface errors, with one passing existing-behavior case; the repaired source passes all sixteen. This is engineering coverage, not a model accuracy gain. A scripted verifier-outage fixture drops from two extractor calls to one while retaining findings and uncertainty. Supported cheap answers avoid Sonnet; failed strong-model answers remain partial. New source evidence can continue the investigation.

Final guarded verification: **359 checks, 358 passed/one optional live skip**, zero failures/errors, blocked reads, provider calls or new adapter jobs. Source fingerprint `f61b308fdd47302c4892bb68de31f4b3f7010fc25ae86168241098286930228a` stayed unchanged. The broader guarded verification result is in `artifacts/agere-live-integration-2026-10-28/verification-economical-routing.json`. The initial 357-check run retained one fixture error because a student-specific test assumed its environment was enabled under a deliberately disabled runtime. That test now explicitly enables only its temporary authored fixture; no runtime student is activated. The failed receipt is retained.

No new Agere provider run, deployment, model activation or large training study was performed for this repair. System One still needs appropriately decomposed, source-bound tasks; this does not yet make it a replacement for all open narrative extraction. The global all-evidence verification packet, same-request artifact-feedback continuation, clinical interpretation errors and accepted end-to-end deliverable remain open. Repair verification granularity next, then meter a full analysis with cold/reused evidence and optional presentation costs separated. Do not claim savings until quality and actual usage are measured together.
