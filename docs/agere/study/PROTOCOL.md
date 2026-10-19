# Agere vs. LLM-for-everything: accuracy and token economics in healthcare analytics

Pre-registered protocol, fixed before any study run (2026-10-18). Changes after the first run are logged with a reason in
the Amendments section at the end.

## Question

Healthcare organizations are spending fast on analytics where a language model handles every request. Does a layered
stack — deterministic specialists with certified error bounds, a distilled local model, caches, and language models only
where judgment is needed — answer more questions correctly, at a fraction of the cost and time? And which components earn
the difference?

## Data

- **Workspace:** the v2.12 native slice of a synthetic health system. It is delivered in customer formats: X12
  837/835/834, NCPDP, HL7 v2, FHIR bulk, CMS CCLF/ALR, and Epic, athena and eCW extracts. It covers 4,095 patients,
  1,034,606 records and 107,239 delivered files.
- **Locked set:** about 50 questions spread over ten domains. Each answer is agreed independently, before any arm runs,
  by the data generator and by Agere's reference derivation. At least half are shapes that Agere's deterministic families
  do not cover.
- **Breadth set:** 150 native questions, scored with the generator's audited key after the dispute corrections.

## Arms

| Arm | What answers | Models |
|---|---|---|
| A. Agere full stack | Specialists first (cohort, data-quality and VBC families under certificates, compiler, caches, bottled model, Perplexity decider), analysts only for what nothing claims | local models; Haiku/Sonnet only when needed |
| B. Frontier for everything | One frontier model writes SQL for every question from the same schema docs, executed once | claude-opus-5-5 |
| C. Mid model for everything | Same as B | claude-sonnet-5-5 |
| D. Small model for everything | Same as B | claude-haiku-5-5 |
| E. Agentic analysts for everything | Agere's hybrid core alone (three analysts, execute and check, vote, tie-break) on every question | Haiku ×3, Sonnet |
| F1–F6. Ablations of A | A with one layer off: F1 no families; F2 no certificates (route as before); F3 no bottled model; F4 no caches; F5 no specialists-first (analysts on every question); F6 no stated-definition rule for engines | as A |

Arms B–D receive the same semantic-layer documentation Agere's analysts receive. They also get one repair attempt when
the SQL fails to execute, which is what a typical text-to-SQL copilot does.

## Metrics (primary first)

1. **Accuracy:** exact match on the locked set (patient lists by identity, values within the scorer's tolerance). The
   breadth set is scored with the audited key.
2. **Cost per question and cost per correct answer** (USD). Computed from ledgered token counts and the pricing file
   committed with the run.
3. **Latency:** median and p90 seconds per question.
4. **Free-tier share:** fraction of questions answered with no paid model call.
5. **Certificate validity:** the observed error rate of certified tiers, compared with their stated bounds.
6. **Repeatability:** identical answers across two runs of each arm.

## Analysis

- **Paired comparisons** on the same questions:
  - accuracy differences tested with McNemar's test;
  - cost and latency differences reported with bootstrap 95% intervals over questions.
- **Ablations** report each component's marginal contribution to accuracy, cost and latency.
- **Projection:** the cost results are scaled to an organization's monthly question volume, using the measured cost per
  question per arm. For example, 10,000 questions a month.
- **Headline claims:** stated only where the interval excludes no difference.

## Procedure

1. Lock the question set and write its fingerprint into this protocol.
2. Commit code, configuration and the pricing file; record the commit hash.
3. Run every arm twice on cold caches, except arm A's cache arm, which runs warm on the second pass by design. Record
   every model call in the ledger.
4. Score with the locked references and the audited key.
5. Write the report from the scored outputs only. Every number in it links to a run file.

## Reproducibility

Run with `bench/study/run.py`. Each arm writes answers, the ledger, timings and the scored results under
`artifacts/study/<run_id>/`. The report generator reads only those files.

## Amendments

1. 2026-10-19, before the first study run (owner's decision: time and cost). The study runs the four arms that carry the
   headline claim, A (Agere), B (Opus for everything), D (Haiku for everything) and E (analysts for everything), one pass
   each. Arm C and the ablations F1-F6 are deferred to a later run; repeatability is reported from Gold 10 (identical
   answers across full rebuilds) instead of a second pass. The locked set is Gold 10 (9 questions, generator digest
   05c5b54b) until Gold-50 is sealed; the breadth set is the native 150 on the generator's corrected key e76655d2 (nine
   answers still under audit, reported as provisional). Arms B and D receive the full modeled-table documentation.
2. 2026-10-19, before any Gold-50 run. Gold-50 (41 new questions from the generator, sealed answers sha256 431bced9a54ecde1,
   34 of the 39 answerable outside Agere's family shapes, 2 expected declines) is scored for every arm directly against the
   generator's sealed key with its --gold50 scorer, run S2-gold50, arms A, B, D and E, one pass. Question-by-question locking
   against an Agere derivation is skipped: locking only where Agere agrees would bias the set toward Agere, and the sealed
   key is the same independent reference for every arm. Disputes found afterwards are audited by the generator and, if
   upheld, applied to all arms alike and logged here.
3. 2026-10-19, before any Gold-50 run and before run S1 is scored. Key audit after the runs: every question where an arm's
   answer misses the sealed key but agrees with at least one other, independent arm (A, B, D or E) goes to the generator
   for the record-by-record (a)/(b)/(c) audit, whichever arm it is. Upheld corrections are applied to every arm alike.
   The report states both numbers, accuracy against the sealed key and accuracy after the audited corrections, labels
   them provisional until the audit is complete, and lists the audit in an appendix. Results are rescored from the saved
   raw answers; no arm is rerun.
4. 2026-10-19, run S2 not yet started. Runs S1 and S2-gold50 execute at the same time on one machine (owner's decision:
   wall-clock time). Shared CPU slows locally computed answers (arm A) more than API-bound arms, so reported latency is
   conservative for Agere.
5. 2026-10-19, after runs S1 and S2. The API credit balance ran out during S1 arm E (all 450 calls refused) and late in S1
   arm D (48 calls refused); both are invalid and rerun as S1b. A new arm A2 (Agere with Opus as the analyst and tie-break
   model for questions no specialist claims) is added as an exploratory arm after seeing S2, run on Gold-50 as S3; it is
   reported as post hoc, not as a pre-registered comparison.
6. 2026-10-19, Codex takeover under Mike's explicit direction. Amendment 2's unvalidated-key shortcut and amendment 5's
   queued reruns are suspended. Every selected reference must have an independent derivation reconciled through the
   generator scorer before any run or rescore. `reference_validation.json` binds that status to the exact question text
   and evidence; the study runner, worker, scorer and native runner fail closed. Gold 10 inherits its nine previously
   reconciled references. Gold-50 and native 150 remain pending. Raw source diagnostics are permitted; they are not
   scores or automatic validation. No existing arm's answer is changed, and no question is selected for validation
   based on which arm wins. Resolve ambiguous wording for every arm alike; changed wording requires a new question
   fingerprint and cannot be scored against old answers as if it were the same task.

   The immediate proof route is independent Gold-50 validation and one rescore of the saved A/B outputs, with all
   excluded/void questions and reasons disclosed. Public EHRSQL and BIRD remain external replication options: they
   require a different data/schema integration and test a different workload, so neither substitutes for this ACO
   comparison. Gold 10 can support a descriptive demonstration now, not a general accuracy-superiority claim. Report
   incremental model API charges separately from unknown local compute, development and operating cost. The original
   Gold 10 scorer uses patient-list F1 >= 0.98, not exact-match accuracy; report that threshold explicitly.

   A2 remains unchanged and exploratory. A3 is a new, unrun exploratory candidate: the existing stack with one bounded
   Opus review when the analyst vote produces no answer or clarification. A clarification can be overridden only by
   checked SQL, a resolved contract, and an exact quote from the user's definition explaining the resolved choice.
   Missing-input unanimous declines and successful answers do not cause extra calls. A3 remains disabled by default;
   deterministic fixture checks establish routing mechanics, not model quality. One Gold-10-only regression run of A
   is permitted for the longer-question input repair; it is not a new comparative study or an accuracy claim.
