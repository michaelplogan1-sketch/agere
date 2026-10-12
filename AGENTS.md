# Agere build continuity

These instructions apply to this repository. Latest explicit user instructions
take precedence. A heartbeat never overrides a user pause.

## Start or resume a turn

1. Read `BUILD_OWNER.json`, `AGERE_BUILD_CONTROL.json` and `CONTINUE_AGERE.md`.
   This also applies after context compaction; do not reconstruct priorities from
   conversation memory or the last component touched.
2. Run `.venv/bin/python check_agere_build_control.py`. Read the current gate,
   exact next action, last sealed receipt and unsealed work it identifies.
3. Before product edits, read the current principal review named by
   `AGERE_BUILD_CONTROL.json.decision_basis`, retain
   `AGERE_PRINCIPAL_REVIEW_2026-10-11.md` as the architectural baseline, and read the full
   acceptance backlog in `AUTONOMOUS_STACK_QUEUE.md`, `AGERE_PRODUCT_DIRECTION.md`,
   `AGERE_RESEARCH_HANDOFF.md`, active warehouse configuration and relevant latest
   receipts. Already-read material may be reused within the same context.
4. Run `.venv/bin/python check_agere_build_control.py --intent build --gate ID`
   using the current gate ID. A paused build permits user-requested review and
   continuity maintenance only. Resume requires an explicit user instruction;
   then reconcile owner and heartbeat status before product work.

## One delivery sequence, full product scope

`AGERE_BUILD_CONTROL.json` owns the current sequence, gate status and next action.
The queue retains the full ten-area acceptance backlog. The product direction
and research handoff retain the wider requirements. Older reports are evidence
and history, not competing next-work instructions.

Work on one connected acceptance increment at a time. Before editing, identify
the current gate, the measured failure or missing user behavior, and the workflow
check that would demonstrate progress. Supporting component work is legitimate
when tied to that increment. Do not substitute model catalogs, new helper paths,
repeated passing tests or additional planning documents for product delivery.

Preserve these architectural decisions:
- Reuse the modular application. Bind original question, population/date scope,
  versioned evidence, interpretation, buyer/payer options, work and follow-up
  through a shared decision context.
- Population health, VBC and outcome-aligned payments remain central. Individual
  cases and HF/CKD examples cannot replace the full product scope.
- Qualify SLMs, classifiers, rerankers, readers and mathematical methods against
  a named workflow failure and appropriate simple baselines. Preserve useful
  research requirements; a catalog or cross-field analogy is not qualification.
- Separate engineering acceptance, model/clinical qualification and external
  deployment prerequisites. A supported unknown can be an engineering success;
  missing wiring cannot. Tests passed and modules added are not completion scores.
- Keep unverified changes identifiable as candidates. Default activation needs
  relevant workflow evidence and a known-good recovery point.

## Handoff and drift correction

Before ending an implementation turn, update the control checkpoint in place:
what user behavior changed, measured failure removed (or why none), evidence,
remaining gap and exact next action. Update the owner receipt only for an actual
sealed delivery. Run the continuity check again. Do not add another competing
plan. Report progress as **gate / workflow change / evidence / remaining gap**.

If two successive implementation increments produce no gate progress or measured
failure reduction, reassess the approach before another feature increment. Record
the reason and correction in the control decision log; this is not a request for
routine user permission. Justified sequencing changes are allowed with recorded
evidence and scope impact. Never silently remove an acceptance area. Only explicit
user direction can narrow the product charter or alter standing restrictions.

## Standing boundaries

Single build owner and protections are in `BUILD_OWNER.json`. Preserve concurrent
edits, the original cohort, frozen experiments, reserved evaluations and source
family boundaries. Keep hidden generator truth out of runtime evidence; do not
edit the separate generator or Documents research corpus. Spend is uncapped under
the user's instruction; retain accounting, bounded inputs, caching and failed-key
protections. No employer access, new services, external publication, contact with
others or real care execution. Do not claim clinical review, model superiority or
complete research without evidence. Keep the heartbeat aligned with this control
file; do not append obsolete historical instructions to its prompt.

The continuity checker detects state and evidence-reference inconsistencies. It
does not run product acceptance, assess clinical quality or prevent an agent from
ignoring instructions. Executable product gates remain required build work.

## Source control (from 2026-10-12)

The repository is under git (`main`). Build owner: see `BUILD_OWNER.json`
(currently Claude Code). Any other agent, including Codex, works on its own branch
(`codex/<topic>`) and opens a pull request; never commit directly to `main` and
never rewrite history. Git history replaces the hash-protected file manifest as
the preservation mechanism: edit modules in place instead of copying them or
adding runtime `install()` patches. Large data (`artifacts/`, `models/`, SQLite,
vectors) stays out of git per `.gitignore`.
