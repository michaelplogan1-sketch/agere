# Agere hand-off, 2026-10-25: build the LLM backstop agent and use each model for what it does best

Mike is away all day. Do not ask him anything. Finish today. Decide from evidence and record each decision in
`docs/agere/HANDOFF_2026-10-24.md`.

## Where things stand

- Code: origin/main around 7f6d8a1. Work from the worktree `/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2`.
  Push with `git push origin HEAD:main` after `git fetch && git merge origin/main`.
  - The main checkout holds the Codex lane's uncommitted edits. Leave it alone.
  - A Codex lane also commits to origin/main, so merge frequently.
- Anthropic API credits are EXHAUSTED. Test through Mike's subscription instead:
  - set `AGERE_CLAUDE_TRANSPORT=subscription` (`clinical_core/agere/subscription_transport.py`, installed on import of `clinical_core.agere`);
  - the token is in `~/.agere-claude-oauth-token`. Never print it.
  - Parity is verified: dev3 check half 40.8 % via subscription vs 43.7 % via API.
- Servers:
  - `agere-measure-sub` on :8888 (subscription; launch.json is in `~/.claude/launch.json`, cwd is the worktree);
  - Codex serves its own build on :8885. Leave it alone.
- Scoreboard: dev3 check half, 71 questions never tuned on.
  - Command: `python -m tools.dev_set.run --url http://localhost:8888/api/stack/compose --set dev3 --half check`.
  - Competitor via subscription: `AGERE_CLAUDE_TRANSPORT=subscription python -c "...run_baseline --set dev3 --half check"`, as in
    the handoff.
  - Last result, Agere 40.8 % vs competitor 32.4 % (45 % on its 51 completed questions; 20 errors were transport timeouts and JSON
    parse failures).

| Bucket | Agere | Competitor |
|---|---|---|
| single patient, structured | 56 % | 56 % |
| notes | 8 % | 17 % |
| population quality | 67 % | 25 % |
| claims | 56 % | 28 % |
| insight | 8 % | 31 % |

- Never read held-out text. Never tune on the dev3 check half; look at its per-question failures only to diagnose classes.

## Diagnosis (from the check-half answers)

- **Notes, 1/12:**
  - 3 were RIGHT but scored wrong because the answer did not include the quoted passage. The scorer needs a source with record_id
    plus quote, or `RECORD_ID ...: "quote"` in the text.
  - 3 were follow-ups refused because the prior turn's patient and visit were not carried ("what did the ECG show?", "PHQ-9 that
    day", "was it DKA?").
  - 3 routed to spec_path or evidence_retrieval and gave the wrong note or no quote.
  - 1 absent topic (dialysis) answered "partial" instead of "not documented".
- **Insight, 1/13:** 6 were refused with no layer answering; 5 gave 1–2 of the 2–4 required facts; 1 HCC question was not stated
  absent.
- **Root cause (Mike is right):** the LLM backstop is under-used.
  - It is Haiku with 8 SQL calls and no notes tools.
  - It never sees the conversation.
  - Sonnet escalation rarely runs.
  - Checks still discard or truncate its answers.
  - System One (Jev and Perplexity) are closed-list classifiers. They are excellent at reading a passage, but they cannot search
    records or compute.

## Build this (in order)

1. **`clinical_core/agere/clinical/backstop_agent.py`: the final backstop agent.**
   - Model: Sonnet (`claude-sonnet-5-5`), escalating to Opus for insight and multi-part questions if Sonnet's answer misses
     required parts.
   - Tools, through `semantic_analyst.tool_loop`, `max_tools` up to 20:
     - `query_sql`: reuse `semantic_analyst._sql_executor` with `semantic.define(as_of)` definition views and
       `analyst_definitions.agere_context` docs;
     - `list_documents(patient_id, date_from, date_to)`: `note_evidence._texts(db, pid, sources, as_of)` gives record_id, source,
       type, date and title;
     - `read_document(record_id)`: full text, truncated to about 8k characters;
     - `search_documents(patient_id, terms, window)`: `note_evidence.retrieve(db, pid, {'term': terms}, ...)`.
   - System prompt:
     - `semantic_analyst.SEMANTIC_RULES` (fix its "8 calls" wording) plus `CONTRACT` and `analyst_definitions.CONTRACT_SUFFIX`;
     - `decision_context.prompt_block()`;
     - rules: answer EVERY part the question asks; for insight give each requested fact (top and bottom groups, rates, counts,
       sizes); for notes quote the exact passage with record_id and date; say "not documented" or "not in the data" when it is truly
       absent.
   - Input: the question plus the conversation context (prior resolved questions and the prior subject patient from
     `patient_ids.current_prior()`; also pass `conversation.prior_context` history if reachable).
2. **Wire it into `proposers.Cascade._run`:**
   - Call it after `reserved_analyst` and before `escalate`, when nothing has been accepted.
   - Accept its answer with checks as ANNOTATIONS (heal, don't block). Build `self.contract_of('backstop_agent', graph, receipt,
     result)` with `answer.sources` carrying record_id and quote.
   - Register it in `TIERS`, `STAGES` (claude stage) and the component ledger.
   - Give it its own budget slot (`AGERE_BACKSTOP_SECONDS`, about 90 s).
3. **Notes answers always carry the quoted supporting passage** (chart_reader and chart_notes render plus the spec_path note path).
   This is a format fix; it recovers right answers that currently score wrong.
4. **Notes follow-ups inherit the prior patient and visit** (conversation and patient_ids prior; `chart_notes.back_references`
   exists).
5. **Measure on the dev3 check half through the subscription.** Also run dev and dev2 once to confirm no regression. Run
   `tools/runtests_diff.py` against origin/main before every push; ship only with 0 new failures.
6. **If the check half improves, run ONE held-out milestone** end to end (subscription). Report correct, $ (API-equivalent) per
   question and p50/p90 against the competitor's 50 % (held-out, API).

## Mike's rules

- Accuracy first, then cost, then speed; a 120 s answer budget is fine.
- Haiku first where it suffices.
- Heal, don't block.
- No unilateral caps.
- Synthetic data only. Never open `private/`, `/Users/mikel/agere-sealed-eval`, the generator, held-out text, or the
  learning/question-plans/answers stores.
- Builders: Sonnet subagents, at most 3, no API calls (they may use the subscription transport only for small smoke tests).
