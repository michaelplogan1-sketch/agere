# Agere handoff — 2026-10-22 afternoon (scorecard build, waves 1–2)

Owner: Claude (sole). Trunk `main`. Live service: `:8885` (Browser pane `preview_start` name `agere`, from ~/.claude/launch.json). Server keys load from Mike's Desktop key file via `run_integrated_stack.keys()`; Anthropic has no credits until Mike tops up, so Claude tiers fail fast and System One (Jev/Perplexity) plus the free tiers answer.

## Where things are
- **main = 383d950**: System One (6 integrations) + wave 1 (55 scorecard items). Wired-component test (test_component_reachability) passes; 355-component ledger.
- **claude/wave2-integration** (worktree `.claude/worktrees/integration-w2`): wave 2 merged except one group — revenue/payer, hospital ops, product/quality (per-resource locks, care actions, fault containment, EHRSQL harness), local models/learning (grammar decoding, vCache, TeCoD, Chronos/TimesFM/TabPFN challengers, NER, Parquet exporter, HarnessSQL, LoRA-all-layers config, SQL-base swap), evidence/ingestion (reranker policy, compaction, derived values, completeness, MedGraphRAG, protected channels, C-CDA, public fixtures, reversible identity, CQL measures). Also: monitors auto-refresh at server start (Mike: "whatever you think is best").
- **Still running when written:** builder for `claude/w2-checks-stats` — finish its 13 items AND fix follow-up narrowing ("Of those, which are women?" fails 0/3 on the free tier, FM-20). Merge it into claude/wave2-integration when it reports.

## To land wave 2
1. Merge `claude/w2-checks-stats`; resolve `engine_suite.py` conflicts by keeping all MODULES and all ROUTE_ORDER entries (pattern used for every wave-2 merge); keep test_operator_router's step-1 menu < 20K (briefs are at 120 chars; a two-level menu is the next fix if it overflows).
2. Rebuild the ledger: `python tools/component_ledger_build.py` (auto-discovers planner-menu engines and cascade tiers; add new non-engine modules to `WAVE1_MODULES` in that file) → `python -m unittest test_component_reachability` green.
3. Retrain the learned router: `AGERE_LOCAL_TYPED=off AGERE_LOCAL_NORMALIZER=off python tools/learned_routing/train.py --write`.
4. Wide regression: scratchpad `run_wide.sh` pattern — `AGERE_LOCAL_TYPED=off AGERE_LOCAL_NORMALIZER=off python -m unittest <every test_*.py that imports the planner modules>` on main vs integration; land only with 0 new failures (known: usage gate red until the full usage trace runs; test_token_scorecard e2e is order-dependent in the full suite).
5. `git -C /Users/mikel/clinical-insights-local merge --ff-only claude/wave2-integration && git push origin main`; restart the server; update the scorecard doc (https://claude.ai/artifact/PeonFW8qcCvZo94M4XnwWw — Claude Docs, update via the docs connector, not a publish).

## Then testing (Mike's next ask)
- Heavy jobs one at a time (the Mac crashed once from overload): usage trace on the fixed 48-question set (`tools/component_usage.py`, makes the usage gate meaningful), bottled-SQL retrain (`train_all --learners bottled --allow-mlx`, fixed OOM settings), OmniSQL vs Qwen (`tools/bottling/swap_base.py`, weights at models/omnisql-7b-4bit), typed adapter training.
- Measurement plan for the paper ("Agere: Verified, Self-Improving Analytics for Healthcare Data"): freeze a version; sealed bank (free tier first; scored only by the sealed program, aggregates only); EHRSQL 2024 + MIMIC-IV (bench/external); after credits: paid head-to-head vs LLM+semantic layer and frontier agent; ablations per layer family (verification, engines, clinical logic, System One, local models, learning).

## Cost lessons (Mike asked)
- Agere API spend to date: $324, 66% Opus. Fixes landed: Sonnet-first, 1h prompt cache, fallback never escalates, wrapped billing errors skip later Claude tiers, no hidden planning calls.
- Building with 6 parallel Opus builders burned ~$50 of Claude Code usage in minutes. Run builders on Sonnet, ≤3 at a time, fresh contexts from branch commits.

## Rules (memory has them): answer every question; every component on the path (never remove/default-off); heal not block; System One before Claude; synthetic only; never open private/ or the sealed bank.
