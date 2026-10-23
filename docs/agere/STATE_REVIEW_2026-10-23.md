# Agere: where the build stands, what is failing, and why (2026-10-23)

Written by Claude (build owner) for Mike and for an outside review by Codex.

## 1. The goal

Agere should be the best healthcare analytics stack available, beating the commercial products Mike has named, with the best
token economics. A user asks a question in plain English about a warehouse (synthetic; about 1M records, about 4,100
patients). Agere returns a correct, sourced answer.

Most questions should be answered by free deterministic layers and by **System One**, the cheap typed decision models: Jev from
TypeSafe, and the Perplexity decider. Claude should be needed only rarely, for genuinely new question shapes. Wrong answers
must never ship.

The reference point Mike keeps returning to is the MGB/Harvard "Clinical System One" paper (arXiv 2610.27099, Oct 2026). In
it, the hosted Jev API led the benchmarks: LongHealth 91.9%, TCGA pathology 97.6%, MedAgentBench 99.4%. Its architecture:
- an LLM breaks the question into small typed sub-questions (with scope and an aggregation rule);
- **System One answers each sub-question per document or per patient**;
- plain code adds up the results;
- an LLM writes the cited answer.

That used about 690 times fewer LLM tokens than having an LLM read every record.

## 2. What has been built (Oct 19–23)

| When | What landed |
|---|---|
| Oct 21–22 overnight | Full component build-out: every listed component wired onto the question path; component ledger plus a reachability test that proves nothing is unwired. |
| Oct 22 | System One wave 1; wave 2 (71 scorecard items); route arbitration with an overlap test (0 wrong winners on the engine templates); time bounds per cascade tier. |
| Oct 22 night | Mac crashed 3 times (GPU out of memory from several local models at once). Fixed with a local-model lock, a capped attention cache, a persistent typed worker and a memory watchdog. |
| Oct 23 early | The first sealed-bank numbers exposed 11 wrong answers. Fixed with stricter checks, an all-source diagnosis table, narrower terminology and a smaller local prompt. |
| Oct 23 morning | Cascade reordered into 5 stages with System One choice placed before the free planners. 9,785 generated governed metrics for System One to pick from. Latency work (30 s budget, cheap tiers first, background continuation). |
| Now (side branches) | `claude/overnight-fixes` (foreground priority, generated metrics, wording fixes) and `claude/s1-fanout` (in progress: per-patient fact sheets plus Jev judging every patient in parallel). Neither is on main. |

Size today: 488 Python modules in `clinical_core/agere/clinical` (about 132,000 lines), 433 ledger components, and 34 cascade tiers.

## 3. How a question flows today

1. **Route arbiter** (`route_arbiter.py`): specialist engines (about 40 workflow engines such as care gaps, readmissions and
   HCC) bid against the native compiler plan; the best fit wins.
2. **Shared planner.** It first tries a hand-written grammar (`request_language.parse`). The grammar must consume every word of
   the question; any word left over raises "Unconsumed request".
3. **Checked-proposer cascade** (`proposers.py`, five stages). Each stage proposes a plan, then the **answer_checks gate** runs
   SQL, a zero-result probe, value-existence, source coverage, the literal check and System One confirmation:
   - **A. Reuse/certified:** exact or paraphrase reuse, semantic cache, learned verified SQL, template decoding, early specialist
     families.
   - **B. System One choice:** Jev or Perplexity pick a governed or generated metric (`s1_metric_router`) or an operator
     (`s1_operator_router`), plus typed decider, terminology mapping and HCC suspecting.
   - **C. Free planners:** deterministic local compiler, late families, a local Qwen typed interpreter (no trained adapter),
     local normalizer, Qwen rewrite compiler, bottled SQL, local free SQL. System One must confirm their answers.
   - **D. System One evidence:** evidence retrieval and review, and **atomic System One** (per-patient judging, MGB-style).
   - **E. Claude:** metric and operator routers, hosted SQL and interpreters, analysts, planners, legacy ladder.
4. **Learning hooks** record the outcome.

## 4. Measurements so far (free tier, Claude off; sealed bank scored only by the sealed program)

| Run | Answered | Correct | Notes |
|---|---|---|---|
| Usage trace, 48 fixed dev questions | 37 / 48 | not scored | $0.014 total; median 88 s; 155 wired components never ran |
| Sealed bank, "definition" mode, bcd3c3e | 0 / 147 | 21 / 147 counted correct | 0 answered, so this score comes from refusals graded as correct; a harness date sentence broke every plan's literal check |
| Sealed bank, "definition" mode, dd2b2df | 16 / 147 | 24 / 147 counted correct | 5 of the 16 answers right, **11 wrong** |
| Sealed bank, "plain" mode, 8c159cd (running, 100 of 147 so far) | 17 / 100 | scored at the end | answered p50 4 s; refusals p50 31 s, p90 93 s; total spend $0.006 |

**Which tier produced the 17 answers in the current run:**
- local_compiler: 11
- learned_verified_sql: 3 (one of them reached through the legacy ladder)
- bottled_sql: 1
- other: 2

The System One routers produced **0** answers directly.

**Why the 83 others ended unanswered:**
- 80 ended as "not understood" from the shared planner, with the message "part of its meaning needs clarification". The causes:
  - 24 × "Specify the requested calculation and warehouse source"
  - 14 × "Explicit source or saved cohort required"
  - about 35 × "Unconsumed request: …", where the grammar could not consume part of the question
- 3 ended "not available".
- 56 of the 82 "not understood" questions did make paid System One calls (about $0.005 in total). So System One ran but did not
  produce an accepted answer. The refusal reason shown is just the last tier's exception text, which hides what System One
  decided.
- Harness bug: `bench/adapters.post_json` drops the 400 response body (its `error` field is not a dict). Each tier's diagnostics
  are lost from the benchmark rows.

## 5. Root causes, in my assessment

1. **The architecture is inverted compared with MGB.** Agere is planner-first: a grammar, compiler or SQL writer must produce a
   whole plan, and System One only picks among metrics we generated in advance or confirms plans others wrote. MGB is
   judge-first: the question becomes a typed per-record judgment, System One makes that judgment for every record, and code
   aggregates. When none of our planners covers a question's shape, System One has nothing to judge and the question is
   refused. That is the 80 "not understood" rows.
2. **The decomposer is missing when Claude is off.** In MGB an LLM writes the atomic sub-questions; that is a small number of
   tokens. In Agere, with Claude off, the only decomposers are a hand grammar that refuses unless every word is consumed and a
   local Qwen interpreter with no trained adapter. "Claude off entirely" removes the one component MGB relied on to turn a novel
   question into System One work. The token-economics goal should probably be "the LLM only plans, in a few hundred tokens,
   and System One does every per-record judgment", not "no LLM at all".
3. **The per-patient System One path cannot run in time.** atomic_system_one gathers each patient's evidence at question time
   by retrieval, at 5–12 s per patient. A panel of 4,100 patients would take over 30 minutes, so the tier always times out.
   Jev itself is fast: about 525 calls/s, p50 0.23 s, about $0.000026 per call. The bottleneck is evidence gathering, not the
   model. This is what `claude/s1-fanout` fixes with precomputed per-patient fact sheets.
4. **Breadth over depth.** Eleven-plus layers of gates, 34 tiers and 433 components. Every check added after the 11 wrong
   answers can only refuse. Failures show up as the last tier's message, so the real reason is invisible. Each fix has added a
   component instead of making one path work end to end.
5. **Measurement has been slow and fragile.** Local MLX models crashed the Mac, and runs take hours. Each fix cycle has cost a
   night.

## 6. Plan in flight

`claude/s1-fanout` (Sonnet builder, running):
- **Patient fact sheets:** one per patient, built once per stack. Each holds diagnoses from every source with codes and dates,
  medications and fills, latest labs, utilization, coverage and social needs.
- **Fan-out judging:** for a patient question, a cheap pre-filter, then Jev judges every remaining sheet concurrently (128 at a
  time) and Perplexity settles the cases Jev is unsure of. Code aggregates, with per-patient evidence kept as receipts.
- **Placement:** a stage B tier, ahead of the local models.
- **Checking:** compared against direct SQL on 3–5 dev questions, capped at 3,000 Jev calls.

Open problem it does not solve: something still has to turn the question into the per-patient criterion. Without Claude, that
falls back to a template or the existing atom decomposer, so phrasing coverage is the risk.

## 7. Questions for the reviewer

1. Is the diagnosis right, that a planner-first architecture with System One as a confirmer is why System One answers almost
   nothing?
2. What should the primary path be: judge-first (MGB-style fan-out over fact sheets), metric-choice, or something else? Which
   should come first in the cascade?
3. Should a small LLM call (Haiku, a few hundred tokens) be the decomposer, with Jev doing every judgment? Or can Jev or
   Perplexity do the decomposition themselves, for example by choosing among typed criterion templates?
4. What should be cut or merged? Which of the 34 tiers and gates add risk without answers?
5. Is the fan-out builder's design sound (fact-sheet contents, pre-filter recall, aggregation, guard against SQL)?
6. What is the fastest way to get a trustworthy measurement loop that doesn't use the sealed bank for development?
