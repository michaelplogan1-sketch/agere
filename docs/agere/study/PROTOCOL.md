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

(none yet)
