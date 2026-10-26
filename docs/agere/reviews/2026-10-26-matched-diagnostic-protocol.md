# One diagnostic cycle: received data, evidence reading, tools, and full-stack answering

Owner coordination: Claude owns product integration and runtime; Codex owns this protocol, the new offline receipt tool and its tests. This implements Mike's October 26 request to work together on the demonstrated failures and create defensible research evidence. It does not authorize protected question inspection, generator access, new services, real-care activity or public submission.

## Decisions

- Keep the current product and components. Fix reproduced failures rather than launch a rewrite.
- Separate structural data validity, clinical realism, key correctness, and runtime correctness. None substitutes for the others.
- Do not diagnose from reserved question text. The reused CHECK set remains validation; held-out remains aggregate-only. Fresh/open BUILD cases are the diagnostic material.
- Do not claim a fixed model or a paper result from a passing unit suite or a partial live run.
- The publication candidate is a verified analytics **system** using existing models. No new model-weight training or CS1-equivalent training corpus has been established.

## The controlled experiment

Use the same independently checked question, received source snapshot, as-of cutoff, patient/cohort, definitions and requested outputs for three arms:

| Arm | What the answerer receives | What it isolates |
|---|---|---|
| exact_evidence | Question, explicit definitions and sufficient raw supporting evidence; no expected answer | Can the chosen model read/reason correctly when evidence delivery is solved? |
| investigator | Same question/definitions and access to current warehouse/document tools; supporting records not preselected | What additional failure appears when the model must find/compute the evidence? |
| full_stack | Ordinary product request, same scope and configuration | What additional failure appears in routing, acceptance, memory and presentation? |

The exact-evidence and investigator arms use the same underlying model, transport and decoding settings. Record the full stack's actual models and escalation. Agent/tool instructions necessarily differ; save their exact versions. Do not present this as a pure model-weight comparison. Preserve multi-turn context identically in follow-up cases, or label the comparison as standalone.

Before running a case, a reviewer checks the received evidence and reference outside the answering runtime. Explicitly resolve numerator, denominator, unit, event grain, dates, source coverage and missing-data semantics. A genuinely absent field has a specific absence reference. A vague or implausible question is not automatically removed: explain whether the answer is unsupported, the definition is ambiguous, the key is wrong, or the source is internally inconsistent. Report excluded/unresolved cases and their counts.

Keep reference answers in a separate adjudication artifact. Do not put them in the answering prompts, warehouse tools, cache or procedural memory. Do not select exact-evidence passages from hidden generator truth. Record evidence and reference hashes before answers are collected. Use a small, explicitly diagnostic packet spanning notes and analytical composition first; it is not the paper's final benchmark.

Score the **complete final answer**, including all requested parts, patient/time scope, arithmetic, row/column meaning, and whether a passage entails the claim. A quotation's presence and a number's appearance in tool output establish neither entailment nor a correct denominator. Provider failures are recorded separately from reasoning errors. Supported absence, partial results and unverified answers must remain distinguishable.

## Executable receipt and report

New code: `tools/diagnostic_cycle.py`; tests: `test_diagnostic_cycle.py`. It imports no product inference modules, reads no question banks and makes no provider calls. It validates explicit receipts and aggregates independent verdicts. It does **not** implement the three inference arms or independently prove a reviewer's reference is correct; the owner supplies actual arm outputs and adjudication. This boundary prevents an offline fixture from being presented as an end-to-end measurement.

Every receipt includes:

- Full candidate SHA and SHA-256 hashes for data, configuration, definitions, scorer, evidence and reference artifacts; as-of, cache policy, transport and run ID.
- Case origin restricted to `authored` or `open_build`, bucket, named reference reviewer, evidence basis and evidence sufficiency.
- All three arm entries, even `not_run`; actual model, transport, output hash, elapsed time, usage cost or an explicit unknown, final answer coverage and independent correctness adjudicator.
- Reference issues separated into warehouse inconsistency, incorrect key, ambiguous definition or missing required evidence.

Run on an actual prepared receipt:

```bash
.venv/bin/python -m tools.diagnostic_cycle /path/to/actual-receipt.json --out /path/to/actual-report.json
```

`artifacts/agere-diagnostic-cycle-2026-10-26/fixture-receipt.json` and `fixture-report.json` are **unit-test fixtures only**. Their fake model/transport, placeholder hashes and one passing case demonstrate serialization; they are not product scores.

The report emits per-arm and per-bucket completeness, correctness, precision on adjudicated answers, provider/execution failures, p50/p90, known cost and missing-usage counts. It refuses to emit a clean overall accuracy for an arm that has incomplete or unadjudicated cases. Cost per correct answer is unavailable when usage or adjudication is incomplete. The p90 convention is nearest rank.

## Interpret the differences conservatively

| Pattern, after reference validation | Diagnostic signal |
|---|---|
| Exact evidence fails and both other arms fail | Evidence reading or reasoning; not proof the warehouse is bad |
| Exact evidence passes; investigator and stack fail | Retrieval, tool semantics or tool planning |
| Exact evidence and investigator pass; stack fails | Cascade, acceptance, context or presentation |
| All pass | No reproduced failure on this case |
| Outcomes are non-monotonic | Repeat before assigning a cause; stochasticity or different prompting may explain it |
| Any arm has an infrastructure error | Paired comparison confounded; preserve and repair the infrastructure failure |
| Reference unresolved or invalid | Resolve separately; do not count it as an ordinary model error |

A single paired trial is a localization signal, not a causal proof. Repeat discordant outcomes on the unchanged candidate before attributing a defect. Do not silently rerun only failed answers until the score improves; retain every attempt and its usage.

## Release and paper gates

1. Independently reproduce each reported defect. Add meaningful synthetic tests and repair it in the existing path. Keep source scope, event/availability clocks, quote-to-claim meaning and complete requested output intact.
2. Claude runs the required regression comparison against the declared baseline. Report existing failures, new failures, skips and process errors; zero new failure names does not make the baseline green. Freeze source and configuration while the comparison runs.
3. Restart only the owner-controlled candidate server after that gate. Record the revision actually loaded, not merely the checkout's latest HEAD. Perform BUILD-only functional checks.
4. Run the matched diagnostic on pre-audited open cases. Use failures to make specific fixes, repeating affected comparisons as needed. Label these adaptive runs as development.
5. Freeze the final candidate, definitions, scorer and transport before the milestone. Keep reused validation separate from a fresh independent final test and compare to the baseline under the same evidence access and scoring conditions.
6. Report uncertainty, per-bucket results, coverage, supported absence, complete-answer precision, cost per correct grounded answer, cold/warm latency and failures. Predeclare the statistical test and the sample needed to support any 95% claim; a small perfect packet cannot establish it.
7. Write the paper around the demonstrated contribution and limitations. Synthetic-only evidence supports synthetic-benchmark claims. Clinical realism, real-world generalization and patient-outcome benefits require appropriate independent qualification. Public submission remains separate from this build authorization.

## Evidence available now

A read-only metadata audit of the configured **received** synthetic source found 4,115 patients and 2,417,748 records. There were zero orphan patient references, zero records lacking the synthetic marker, zero invalid JSON payloads, zero malformed populated event/availability dates, and zero received-after-cutoff records. There were 39,763 unknown event dates. These unknowns require explicit treatment; they are not automatically invalid observations.

Receipt: `artifacts/agere-diagnostic-cycle-2026-10-26/source-metadata-audit.json`. This audit examines source metadata only; it does not establish narrative realism, clinical consistency, semantic-adapter correctness, or key validity. The source and active runtime snapshot must be identified separately.

Independent review confirmed Claude's follow-up `f305034` now rejects the uncached future-document reproduction. The full model diagnostic and final evaluation remain unperformed by this Codex lane. Claude's coordination receipt records integration and running-measurement status; no partial number here is promoted to a final result.
