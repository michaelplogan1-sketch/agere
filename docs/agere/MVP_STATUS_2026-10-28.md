# Agere MVP status — October 28, 2026

The latest complete run took **380.1 seconds (6 minutes 20 seconds)** and ended with a **partial answer and rejected deliverable**. This is not an accepted automatic workflow or a ready MVP. There is no defensible remaining completion estimate yet; the earlier 20–35 minute estimate was too optimistic.

## What actually improved

- All 57 patient rows survive analytical repair and deliverable preparation. The whole-document pass covered 4,610 documents: 40 patients received model-supported review, 14 need further review, and 3 had no documents. Model support is not physician acceptance.
- The live output now uses the registered HF evidence-based beta-blocker class: **14 patients**, correcting the previous overly broad count of 22. Other reported filled-pillar counts are 19 RAS inhibitor, 13 MRA and 11 SGLT2 inhibitor; fill evidence does not establish adherence or concurrent appropriateness.
- On the same 12 received reference-drug prescription records, native documentary dose recovery improved from **0 to 11**, with 7 active documented orders. This does not establish ingestion or maximum tolerated doses.
- Both finite SQL/typed learning runs and native reviews are finished. No candidate was selected or activated. SQL correct answers increased from 55 to 173 of 304 authored engineering comparisons, with 62 wrong and 69 declines remaining. The typed corrective round gained 11 and lost 11 correct answers, while wrong answers rose from 33 to 42. These are engineering studies, not clinical accuracy estimates.

## Why the workflow still fails

The final support check sends accumulated tool and document-review evidence in one oversized closed-judgment request. Provider failures leave answer support unknown. Exact HTTP400 rejection causes are unconfirmed because the retained errors lack structured response bodies. Downstream artifact feedback currently reaches the analyst only through an explicit recovery run. That manual cycle is not an acceptable automatic product experience.

There are also real meaning errors to repair. The headline calls 13 patients note-only, but the computed patient table shows **13 with both coded and note evidence, 17 note-only and 27 coded-only**. The dashboard review found confusing per-row target-versus-maximum dose labels and omitted source exceptions. Candidate reason counts contain non-reasons and cannot be sold as confirmed clinical explanations.

Current use, appropriateness and maximum tolerated dose remain open where the sources do not settle them. PCP/cardiology identifiers and visits are available, but current responsibility is incompletely established. Service eligibility, consent, enrollment and device fulfillment are unavailable and must remain proposed intake tasks. No care or shipment was executed. The warehouse clock is simulated 2030-05-27; execution and guideline review occurred in October 2026.

## MVP direction

Retain the existing architecture. Deterministic SQL and governed definitions compute; whole-document readers interpret notes; Jev/Perplexity judge closed questions; Sonnet investigates; Opus creates deliverables. The current priority is reliable integration, faithful evidence and usable outputs across general healthcare analytics. More training or a larger component catalog does not resolve these observed failures.

Next, repair the evidence-size handling at the existing verification boundary and close the review-to-analyst continuation in the same original request. Verify the failed native receipts and generic cases before another complete run. A useful, explicitly qualified unknown must remain possible; missing source facts must never be invented to pass review. Keep the full product charter, and measure release progress by completed workflows.

## Observed components and costs

The run used native SQLite/governed definitions, the dose parser, registered medication groups, whole-document review, Claude Haiku/Sonnet/Opus through the subscription, Jev 1.13 and Perplexity. MiniLM calls hit cache. BGE weights loaded, but no reranker forward invocation was observed. Experimental SLMs were not activated. No final-path UltraBERT or mathematical operator invocation is claimed. RSI collected observations and supported guarded execution recovery; it did not demonstrate an autonomously improving deployed clinical model.

Across the retained workflow and separate proposed-service guide, recorded successful usage corresponds to **$30.42 in versioned API-equivalent estimates**, including **$26.00 for this resumed work**. Claude used the subscription, with no external Anthropic API charges. These estimates are not the subscription invoice. Failed usage, local compute, engineering and physician review are not fully priced. **Cost per clinically accepted answer remains unknown.**

Native evidence: `/private/tmp/agere-live-resume-20261028/attempt7/result.json`, `run-receipt.json`, `mvp-checkpoint.json`, `cost-snapshot.json`; source/guard verification: `artifacts/agere-live-integration-2026-10-28/verification-review-loop.json` (271 offline checks, no provider calls). Passing engineering checks does not establish physician acceptance or competitor superiority.
