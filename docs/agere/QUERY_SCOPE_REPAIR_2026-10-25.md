# Agere query scope and refusal repair — October 25, 2026

Agere has **not achieved >95% or 99% correctness**. The last once-only held-out measurement remains30%; it has not been rerun or used for tuning. All new diagnosis uses open dev/BUILD questions, paraphrases, or independent synthetic fixtures. The CHECK half remains closed to this work.

## Current live release

**d73be19** is pushed and live on port8885 from `/private/tmp/agere-note-contract-release` (PID44592, session56567), with rollback f2b10b0. Only the reviewed note-execution files were added to that preceding release; broader unqualified planner and analyst changes remain excluded.

Compiled single-patient note requests now execute AND/OR/NOT with three-valued SQL, preserving structured siblings, citations, short-circuiting and unknowns. Named deceased subjects remain in scope. An open/detail request is not replaced by a Boolean answer. Single-leaf note predicates retain the subject and original question.

Qualification:95focused checks pass;58candidate/postmerge modules versus57baseline, with the same existing native-worker crash and **zero new failures**. The exact runtime's sealed295paraphrases score243/295, **zero lost answers**, unchanged code/definitions/source manifest. A normal-mode smoke check returns the correct0of14days with its May13source citation in29.285seconds, **zero new calls/$0**. These checks do not establish95% clinical or unseen accuracy.

Receipt: `artifacts/agere-note-condition-d73be19/implementation-receipt.json`.

## Preceding qualified release

The preceding release **f2b10b0** ran from `/private/tmp/agere-scoped-retrieval-order` on port8885 (PID43114, session77426). It uses the original active synthetic warehouse, shared artifacts, normal provider settings, and unchanged definition overlay SHA256 `e2479e663be06b1c146c96b0349f45dbab495f596541448f93d7e6dedc2cd93c`. Rollback a60c519 remains available;5162f78 is retained too.

The release repairs historical lab-completion reads that the query guard was incorrectly changing into global-latest lab reads. Numeric-result uses are resolved through SQL scopes; completion/null-validity reads retain their historical evidence. Explicit latest-within-period reductions retain their own ordering. The saved kidney-testing query now computes219/853(25.67%), instead of9/853(1.06%). Saved plans/results invalidate when execution policy changes.

Answer reviewers receive complete **executed SQL**, including guard transformations. Execution logs no longer appear as answer-table rows. Offline recovery can reuse saved failed responses before current model-dispatch policy is applied. Uncapped budgets no longer cause comparisons against `None`.

Scoped retrieval now starts from a patient's passage IDs instead of scanning the entire content index. On184passages/179unique contents, membership is unchanged; the lookup fell from3.012seconds/about15.956million SQL VM instructions to0.00249seconds/about1,000instructions. Explicit content-ID ordering preserves ranking ties. This is lookup performance, not whole-question latency.

## Measurements and limits

- Final merged-main regression:101modules,9remaining failures versus68before; **59removed, zero new failures**. The separate retrieval comparison passes all8modules.
- Final sealed free paraphrases: **243/295(82.4%)**, matching5162f78, zero lost answers. Code, definitions, and source manifests remained unchanged.
- Normal live check: correct reported medication use0of14days, exact May13source citation,29.538seconds, **zero new calls/$0**. An earlier a60c519check cost$0.000124768(4SystemOnecalls, noClaude).
- Actual executed results from47saved BUILD-only Haiku proposals:21correct versus20before. Of30accepted proposals,20are correct versus19before(66.7%). This is recorded-proposal replay, not a fresh full-cascade accuracy result. The broader analyst acceptance change remains inactive.
- Independent reviewer calibration on those30accepted proposals: both reviewers fully confirmed10, of which9are correct(**90%precision**, low coverage). Including partial confirmations gives9correct/4wrong. The remaining strict error is the unresolved claim-source645versus637dispute; score keys and default definitions remain unchanged.

An initial historical-query prototype broke two A1c answers; replay caught those regressions before release and they were repaired. A first295-item run scored241/295, but its definition overlay changed during the run, so it is not release qualification. The sealed successor scored242/295 with one note timeout; the scoped-retrieval repair restored243/295. The two recovered note probes are **passage-support credits**, not proof of complete clinical answers.

## Spend

The new independent-reviewer run cost a known$0.001546776:50new SystemOne calls,10cached judgments reused, one failed request without usage, no pending or unpriced responses, and no protected-store writes. Together with the earlier live smoke check, known new spend for this continuation is **$0.001671544**.

Known owner subtotal, including older provisional estimates: **$16.621161705**. Unassigned request-window activity$0.033317230 remains excluded. These are recorded estimates, not an account-wide invoice.

## Unpromoted follow-up

The Agere-only complete-request prompt candidate **eb3623c** requires every requested comparison/calculation to appear in executed outputs and clarifies historical completion, within-period reductions, and event-level scope. It leaves the shared competitor prompt unchanged. It passes105focused checks and a9-versus9module regression comparison, both zero failures.

Its first six-item setup attempt made zero network calls because offline configuration disabled Claude. After that was corrected, the first actual Haiku request received **AnthropicHTTP400: insufficient credit balance**. The batch stopped; five questions remain unattempted, no Sonnet calls were made, and known spend is$0with one failed request lacking usage. This is a provider failure, not model accuracy. Do not activate the prompt or retry the paid measurement until credits are restored.

Compound-note candidate **d73be19** preserves AND/OR/NOT through compiled three-valued membership and unknown-membership SQL. It preserves structured siblings, citations, short-circuiting, and named deceased subjects. A mixed/open detail request is not replaced by a Boolean answer. It passes95focused checks and a58-versus57module regression comparison with the same existing native-worker crash and zero new failures. The exact isolated runtime's sealed paraphrase and postmerge regression checks subsequently passed; it is now the current release described above.

This candidate isolates note execution from broader unqualified planner business-definition assumptions. Root main still contains inactive analyst changes and must not be deployed wholesale. All ten product areas remain in scope; G3 stays active, G5 incomplete, full acceptance false.

Evidence: `artifacts/agere-query-scope-retrieval-f2b10b0/implementation-receipt.json`; detailed run receipts under `/tmp/agere_generalization/`.
