# Agere build brief: Phases 0–2 (answer first, then economics)

October 21, 2026. Requested by Mike for the build owner (BUILD_OWNER.json). Source: the CTO review of October 21 (live 20-question probe on :8885, code map and token audit). File references are against HEAD 6c19714 on `codex/agere-validated-proof`. The live backend in `/Users/mikel/agere-mvp-run` has an identical `clinical_core/` except for the action-center assets.

The owner keeps sole control of merges, AGERE_BUILD_CONTROL.json, activation and the :8885 restart. This brief adds work items; it does not replace the G3/G5 charter.

## Why

Mike asks a question and doesn't get an answer. On the live probe (`probe20.json` in this folder), 12 questions ran before the Anthropic balance hit zero:

| Outcome | Count |
|---|---|
| Useful | 5 |
| Wrong | 1 (CKD 4–5 not seeing nephrology = 0; direct SQL finds ~60 of 65) |
| Answer buried in a table headline | 1 |
| Hedged to "unknown" | 1 (HFrEF GDMT) |
| Refused | 2 (MSSP avoidable spend, BCS open gaps) |
| Partial | 2 |

The other 8 failed on the API balance. Paid questions cost $0.47–2.11 and took 13–177 s, and the prompt cache read 0 tokens.

The data and engines are mostly in place. The planner gate in front of them refuses, hedges and repeats itself. Phases 0–2 fix that gate, then its cost.

## Phase 0: unblock (days 1–2)

**0.1 Provider failure is not an internal error.** `clinical/context_student.py:90-105` raises `RuntimeError('HTTP 400: …credit balance…')` and the answer becomes `internal_error`.
- Classify provider errors as billing (credit balance, usage limit), rate limit, overload or other, and carry that class to the answer as `status: provider_unavailable` with the provider message.
- On billing, rate-limit or overload errors, retry the same request once on the next model in `AGERE_INTERPRETER_FALLBACK` (default `claude-opus-5-5,claude-sonnet-5-5,claude-haiku-5-5`) and record the fallback in `planner_receipt`.
- Accept: with an invalid or empty key, `/api/stack/compose` returns HTTP 200 with `status: provider_unavailable` and a readable reason, not a 502. A unit test with a fake transport returning the credit-balance 400 shows the fallback model being called.

**0.2 One question path.** `/api/workspaces` still answers through `onboarding/workspace.py:568-680` → `clinical/together.py:721` (`compose_together`, the legacy economical ladder).
- Route workspace questions through `Stack.compose` → `shared_planner.compose` (`clinical/shared_planner.py:710`).
- Delete `Stack._legacy_compose` (`clinical/stack_runtime.py:128`), which has no callers. Delete code reachable only from it (`economical_planner`, `operator_router`, `metric_router`, `answer_guard`, `learned_router`, the bottled hooks, and `local_compiler` except the alias tables that `conversation.py:26` imports) once `grep` shows no live importer.
- Accept: the same question to `/api/stack/compose` and to a workspace ask returns the same `route: shared_planner` and the same answer. `python -m unittest` on the suites listed in SHARED_PLANNER_CORRECTNESS_2026-10-20.md still passes, with removed-route tests deleted rather than skipped.

**0.3 Repo hygiene.**
- Land or close the 10 branches with commits not in HEAD (codex/agere-domain-learning, economics-learning, engine-scope, evidence-learning, question-qualification, request-admission-release, foundational-request, frontier-deliverable-integration, plus the two Oct 14 agent branches) and the detached c7c204d and 0835f13.
- Remove the ~70 merged `.claude/worktrees/*` worktrees.
- Fix `AGERE_SHARED_FRONTIER_2026-10-20.md`: it says "never executable model SQL", but `general_analysis` runs model SQL.
- Commit the live `config/agere/active_warehouse.json` overlay or document why it stays local.
- Accept: `git worktree list` shows only active checkouts. `git branch --no-merged` is empty or every remaining branch has a written reason in PARALLEL_WORK.md.

**0.4 API balance.** Mike tops up credits and lifts the workspace usage limit that blocked 21 questions until 2026-11-01. This item is Mike's.

## Phase 1: answer first, annotate after (week 1)

Rule (Mike, 10-20): answer every question, source it to exact data, and learn from it. Gaps are disclosed on an executed answer and never replace it. "Unknown" is reserved for data that discovery shows is genuinely absent.

**1.1 Remove the typed-plan refusal trap.**
- `shared_planner.py:532`: a typed plan with any `unresolved` entry, or a third failed audit, returns the contract straight away.
- `execute_contract` (`:745-763`) then turns any unresolved item or audit issue into `not_understood` and executes nothing.
- Change: a typed plan with unresolved items, or one that still fails audit after its proposals, falls through to the general-analysis rescue (`:559-603`) with the unresolved quotes as feedback.
- `execute_contract` keeps rejecting only when an unresolved item is a population criterion and no rescue analysis produced a result.
- Remove the prompt instruction at `:95` that sends ambiguous requirements to `unresolved`. Ambiguity goes to the two-readings or clarify rule, and capability gaps go to `{"analysis": …}`.

**1.2 Make the rescue path answer.**
- Relaxed `prepare` (`clinical/general_analysis.py:370-480`) still raises on hard validators. The patient-selection regex at `:470-476` killed the G3 compound request ("Patient table selection needs a literal individual-patient listing clause"). In relaxed mode, validator failures become disclosed limitations on the executed answer, never `PlanError`.
- Keep the SQL-safety checks (read-only, authorizer, compile) as hard failures.
- Infer patient selection from the result shape: a `patient_id` column plus a question that asks "which" or "who".
- Words that `uncovered()` (`:428-434`) can't map stay listed as disclosed gaps. They don't block actionability on their own; only a population criterion the reviewer flags as unimplemented blocks it.

**1.3 Default definitions instead of refusals.**
- The prompt lines at `general_analysis.py:103` and `:185` ("Never invent … maximum tolerated dose …"), together with the BCS refusal ("recreating the governed definition would be prohibited"), produce refusals.
- When no governed definition exists, the model uses a named standard default and shows it in `definitions` and on the answer:
  - avoidable spend: AHRQ PQI admissions plus NYU/Billings ED avoidability
  - screening gap: the governed measure's own denominator minus numerator, exposed as a patient list
  - adherence: PDC ≥ 0.8
  - target dose: guideline target tables already in `config/agere`
- Expose the numerator and denominator membership of governed measures (`measure.*`) so that gap lists come from the governed definition rather than a re-implementation.
- Accept: "Where is the avoidable spend in our MSSP population?" and "What is our breast cancer screening rate by practice, and which patients have open gaps?" both return executed answers that name the definition used.

**1.4 Medication state reconciliation.**
- The rescue audit instruction (`shared_planner.py:577-580`, "use unknown values where evidence does not establish a state") leads to all four GDMT pillars being "unknown" for P-1007. P-1007 has all four on the med list at 2030-05-24 and no fill since 2029-02-10.
- Add one shared medication-state function, available as a governed macro, with these states:
  - `filled_current` (fill or claim covering the as-of date plus 30 days of grace)
  - `listed_not_filled` (on the list or ordered, no fill in 120 days)
  - `documented_stopped_or_intolerant` (note_therapy_change or therapy_barrier)
  - `never_recorded`
- Each state carries its evidence dates. For "not on X" questions, `listed_not_filled` counts as likely not taking, with the evidence shown. `unknown` applies only when no medication record of any source exists.
- Accept: on the probe's GDMT question, P-1007 shows `listed_not_filled` for at least the RASi/ARNI, beta-blocker and SGLT2i pillars with dates. No pillar is "unknown" for a patient with medication records for that class.

**1.5 The headline is the answer.**
- `general_analysis.py:653` and `shared_planner.py:830` build the headline as `"{n} rows · {columns}"`.
- For `how many / what is / what's` questions, the headline states the computed scalar ("9 patients …"), and any breakdown table follows it. For "which/who" questions, it states the count and the first ranked rows.
- If the model returns a breakdown with an `answers_question` flag, the headline sums the flagged rows.
- Accept: "How many patients with diabetes have an A1c over 9 and haven't seen their PCP in the last 6 months?" headlines a number, not "3 rows".

**1.6 Zero-result probe** (Mike, 10-14: never answer 0 or empty without a full-catalog probe).
- Before returning 0 or an empty set, run a discovery probe over alternative sources for each population predicate, using the `request_discovery` probes:
  - condition codes in `condition.icd10`, `payer_claim.diagnosis_codes` and the problem list
  - specialty via `clinician.specialty` joined from encounters, payer claims (`rendering_npi`) and care team
- If the probe finds candidates, re-plan once with the probe as discovery. Otherwise return the 0 with the searched scope listed.
- Accept: "Which patients with CKD stage 4 or 5 are not seeing a nephrologist?" returns a non-empty list. The reference is about 60 of 65 patients with N18.4/N18.5/N18.6 who have no nephrology encounter or claim. The exact count depends on the stated window, and that window must be shown.

**1.7 Total time budget.**
- There is no end-to-end budget today: up to 6 typed turns, 3 rescue rounds and 600 s HTTP and SQL timeouts each.
- Add `AGERE_ANSWER_BUDGET_SECONDS`. Mike sets the value; propose 90 to him. When the budget runs out, return the best executed candidate so far marked partial, never nothing.
- This is a latency setting, not a spend cap. The token budget and repair count stay as they are.
- Accept: no probe question exceeds the configured budget plus one in-flight call.

**Phase 1 acceptance.** Run `probe20.json` once through the real compose path after Phase 0.4.
- All 20 return an executed answer (`answered` or `partial` with results). Zero return `not_understood`, `internal_error` or a 502.
- An independent SQL check by someone other than the implementer agrees on at least 18. Record the checks next to the run.
- This is a dev set: report it as such, never as held-out accuracy.

## Phase 2: token economics (weeks 1–2)

Current state, from the token audit (`/private/tmp/claude-501/-Users-mikel/ddfd89a9-a763-4787-9fde-08e66490f3b1/scratchpad/econ/`):
- Paid question mean $0.51, p90 $0.99, max $2.45.
- Planner input is about 42–50K tokens per call.
- Cost by role: repairs 30%, schema-request round trips 16%, judge 12%.
- Cache reads are 0 on the live ledger.

**2.1 Fix prompt caching.**
- `HostedModel.__call__` (`clinical/nl2sql.py:319-325`) splits the user text on `"\n\nEXAMPLES\n"` and marks the first part `cache_control`. The planner JSON has no such marker, so the whole per-question message becomes the cached block: every call writes a new entry and none is read.
- Change to explicit blocks:
  1. `system` = SYSTEM, cached with the 1h TTL (the helper exists at `clinical/planner_scope.py:18`).
  2. The question-independent catalog (analytical_operators, governed_measures, named_concepts, governed_knowledge, all_received_streams, source_coverage; about 70K chars of the 98K), serialized deterministically (sorted keys) and cached with the 1h TTL.
  3. Question-dependent parts, uncached: selected_schemas, discovery, operator contracts, question, feedback.
- Split `schema_slice` / `schema_prompt_view` (`shared_planner.py:262`, `:335`) to return static and dynamic parts. Build the user message at `:460-465` and `:562-569` from them.
- Accept:
  - On the second and later calls of one question, `usage.cache_read_input_tokens` is at least 30K.
  - Across two different questions within an hour, the second question's first call reads the static block.
  - Mean planner-call cost on probe20 falls by at least 50% against the pre-change ledger.

**2.2 Send likely schemas up front.**
- 32 `need_tables / need_operations / need_streams` turns since 10-19 each cost a full 43K-token call for about 110 output tokens.
- Use `request_discovery.search` (now stemmed and synonym-aware) on the question before the first call. Include the top schemas and the full parameter schemas of the top matching engines (raise the "at most 3 by word overlap" limit at `:290-301` to top-k by search score).
- Accept: on probe20, fewer than 1 in 5 questions spends a turn on a `need_*` request.

**2.3 Repairs send only the change.**
- Repair turns (`:533-535`, `:577-580`) resend the full prompt. With 2.1 in place the static part is a cache read; also stop resending `previous_proposal` in full when the audit names specific issues. Send the issues plus the failing fragment.
- Do not cap repairs. Repair count is Mike's policy.
- Accept: repair calls carry less than 15K uncached input tokens on average.

**2.4 Model roles by measurement.**
- Today the planner and judge are both Opus at effort low (`shared_planner.py:614`, `gateway.py:152`, `nl2sql.py:322`).
- A/B on probe20 after Phase 1 passes, with one run each, and with equal tools and prompts:
  - (a) planner Opus, judge Opus
  - (b) planner Sonnet, judge Opus
  - (c) planner Sonnet, judge Haiku
  - (d) planner Sonnet with escalation to Opus only when the audit fails
- Choose the cheapest configuration that keeps Phase 1 acceptance (18/20 independently correct). Record the result in BUILD_LOG.md and make it the default through `AGERE_SHARED_INTERPRETER_MODEL` plus a new `AGERE_SHARED_JUDGE_MODEL`.

**2.5 Cost accounting.**
- Add `execution_id` and `request_hash` columns to `answers` (`clinical/answer_log.py:9`) and an `at` timestamp column to the ledger `calls` table (`gateway.py:25`).
- Record `api_usd` on `internal_error` and `provider_unavailable` rows. $5.51 is unlogged today.
- Keep unknown cost as unknown.
- Accept: `SELECT` joins every ledger row since the change to exactly one answer row. The `/api/stack/economics` summary reports cost per question and per answered question by route.

**Phase 2 targets, measured on probe20 with the selected configuration:**

| Measure | Target |
|---|---|
| Mean API cost per new (uncached) question | ≤ $0.15 |
| p50 latency | ≤ 30 s |
| p95 latency | ≤ the Phase 1 budget |
| Exact repeats | $0 and under 2 s |
| Accuracy | no regression from Phase 1 acceptance |

## Out of scope for this brief

Phase 3 (an independent accuracy set in Mike's own wording, 90 → 95% gate) and Phase 4 (hosted deploy, design partner) follow these phases. Do not add new engines or narrow helpers during Phases 0–2 unless an acceptance check fails for lack of one.

## Files in this folder

- `probe20.json`: the 20 probe questions, used as a development regression for both phases.
