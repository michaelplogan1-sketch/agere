# Codex takeover prompt: Agere build and proof (2026-10-19)

Paste everything below the line into Codex.

---

You are taking over the Agere build from Claude Code, at Mike's request. Mike is Agere's founder. Agere is a healthcare
data-analytics-to-action product going to market with value-based-care groups (ACOs first). Your job is to own the build
and to produce credible proof that Agere's layered stack beats "an LLM for everything" on accuracy and cost per correct
answer, and to make that proof something a buyer or investor would trust.

## 0. Before anything else

1. Repository: `/Users/mikel/clinical-insights-local` (git, branch `main`). Read, in order:
   `AGENTS.md`, `BUILD_OWNER.json`, `AGERE_BUILD_CONTROL.json`, `docs/agere/HANDOFF_2026-10-18.md`,
   `docs/agere/study/PROTOCOL.md` (including amendments 1 to 5), and the notes in
   `/Users/mikel/Library/Application Support/Claude/side-session-notes/local_e685aca8-58f1-45db-8ce0-c78d4fd07b93/`.
2. `AGENTS.md` currently marks Codex as paused and Claude Code as build owner. Mike is assigning you the build; update
   `BUILD_OWNER.json` to record the transfer (quote Mike's instruction), then run
   `.venv/bin/python check_agere_build_control.py`. Follow `AGENTS.md` for branches, commits and the continuity checkpoint.
3. Commit as "Michael Logan" <michael.p.logan1@gmail.com>. Stage only your own files; other sessions have uncommitted work
   in this checkout (for example `config/agere/active_warehouse.json`, `bench/capability/`). Work on a branch unless Mike
   tells you otherwise.

## 1. What exists (all on main)

- Native ingestion of customer formats (X12 837/835/834, NCPDP, HL7 v2, FHIR bulk, CMS CCLF/ALR, Epic/athena/eCW extracts)
  into a canonical record store and a 61-table semantic layer, with streaming per-file ingest.
- Answer path, in request order: intent router and learned router (local classifiers); rule compiler, MiniLM embeddings,
  bge-reranker and schema reranker; caches, deterministic SQL families (cohort, data quality, VBC) under Learn-then-Test
  certificates, analytic engines, governed metric router; Perplexity typed decider choosing curated recipes and a bottled
  local Qwen3-4B filling parser slots; finally the hybrid core (three Haiku analysts write contract + SQL, the system
  executes, checks and votes; Sonnet tie-break). Opus composes deliverables; every number comes from a tool.
- Record-level tools used inside answers: Jev/TypeSafe, BioClinical ModernBERT, KEV-4B, D1-3B, Granite English R2, UltraBERT.
- Action backbone (plans, agents, simulated counterparties), population simulator, deliverable composer, app, workspaces
  with warehouse connectors, hosted edition. Architecture visual: https://claude.ai/artifact/27N1kno6TjWAexjNisjJq7.
- Pilot workspace: `artifacts/workspaces/pilot-native-v2-12` (4,095 people, native v2.12 slice). Live service on
  127.0.0.1:8885 runs from the clean worktree `/Users/mikel/agere-mvp-run` (detached at a5bafb2).
- Study harness: `bench/study/` (`run.py`, arms in `common.py`, cold caches per arm-pass, per-arm ledgers, McNemar and
  paired bootstrap in `analyze.py`). Gold 10 harness: `bench/gold10_run.py`. Native bank runner: `bench/native_run.py`.

## 2. Results so far, and how much to trust them

| Set | Reference status | Agere | Opus writing SQL for everything |
|---|---|---|---|
| Gold 10 | validated: every list reconciled by two independent derivations before scoring | 9/9, $0 | 7/9, $1.89 |
| Native 150 | generator key corrected after a record-by-record dispute audit (digest e76655d2); 9 answers still under audit | 128/150, $4.94 | 124/150, $10.84 |
| Gold-50 (41 new questions) | NOT validated: scored straight against the generator's sealed key (431bced9) | 19/41 | 22/41 |

Run folders: `artifacts/study/S1-lean` (Gold 10 + native 150) and `artifacts/study/S2-gold50`. In S1, arms D and E are
invalid: the API credit balance ran out mid-run. Runs `S3-gold50-opus` and `S1b-rerun` were stopped incomplete.

Gold-50 is not a usable number. Of Agere's 22 misses, about 10 were missed by every arm while independent arms agreed with
each other (three questions had identical answers from all four arms, all marked wrong): a dispute batch is with the
generator (`gold50-dispute-batch-1.md` in the notes folder). About 5 were Agere abstaining or asking for clarification
where an answer was possible; about 4 were plain Agere errors where Opus was right.

## 3. What went wrong (so you do not repeat it)

- The answer keys come from a separate generator session (`/Users/mikel/synthetic-patient-ecosystem`), which is kept
  independent so Agere never grades itself. Those keys have repeatedly contained defects: 13+ in the native bank, about 10
  suspected in Gold-50. Every score against an unvalidated key mixes Agere's errors with the key's.
- To save time, Claude skipped validating Gold-50 before scoring (protocol amendment 2). That produced a misleading 19/41
  and more paid runs. Mike's standing rule now: no test, rerun or rescore against a key until each answer in it has been
  validated, and no open-ended test loops. He wants the product built, with one credible proof, not a cycle of benchmarks.

## 4. What Mike wants from you

1. One proof he can show buyers and investors: Agere is more accurate and cheaper per correct answer than an LLM for
   everything, on questions whose answers are independently established. Decide how to get it. Options to weigh:
   - Validate Gold-50 the way Gold 10 was validated (an independent derivation per question, reconciled with the
     generator through its `--gold50` scorer, which reports correct/incorrect without revealing values), then score
     every arm against only the validated questions.
   - Add an external benchmark not built by our own generator, so the number does not depend on our keys at all.
   Pick the cheapest route to a number that stands up, and say which you chose and why.
2. Fix the real product gaps Gold-50 exposed, independent of the key: Agere abstains or asks to clarify too often on
   novel question shapes, and questions no specialist claims fall to small analyst models. Mike's direction: latency does
   not matter; cost is acceptable when it buys accuracy; the best component must be used at the right time. A candidate
   (arm `A2` in `bench/study/common.py`: Opus as analyst and tie-break for unclaimed questions) exists but was never run
   to completion.
3. Keep the stack's design: classify, retrieve, answer in code, pick a curated recipe, then generate; certificates on
   free tiers; models write SQL and the system executes it; deliverables take numbers only from tools.

## 5. Rules

- Never open any `private/` folder in the generator repository and never read answer keys. Compare only through the
  generator's scorers. The generator session must never write Agere code.
- Fix rules generally, never per question id.
- Data is synthetic. No real care execution, no contact with outside people, no publication without Mike.
- Keep the live service on :8885 running from the clean worktree after code changes.
- Report to Mike as: what changed, the evidence, what remains. Keep it short; he is cost-conscious and wants decisions,
  not status chatter.
