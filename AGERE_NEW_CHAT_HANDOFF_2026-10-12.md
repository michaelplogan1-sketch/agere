# Agere build handoff — October 12, 2026

This is a context and startup guide for the next human-directed build chat.
AGERE_BUILD_CONTROL.json remains the sole sequencing/checkpoint authority. This
document does not create a competing roadmap or claim another product delivery.

## Mandate and engineering standard

Continue building Agere in `/Users/mikel/clinical-insights-local`, with the
existing local MVP in `/Users/mikel/caresignal-mvp`. Treat this as a serious,
ambitious product. Apply the rigor expected of a chief architect at Google or a
frontier AI laboratory: coherent architecture, sound interfaces, substantive
delivery, disciplined evaluation, maintainability, and measured economics. This
describes the requested standard, not an actual employment affiliation.

The user wants a product that materially outperforms **Innovaccer, Arcadia,
System.com, TextQL, and a capable ChatGPT connected to Epic**. Preserve that
ambition. Build and demonstrate an advantage in useful-answer correctness,
breadth, evidence traceability, longitudinal interpretation, actionable insight,
adaptability, latency and total cost. Competitor inferiority, unique invention,
the perfect stack and best token economics have not been demonstrated. Compare
against competent baselines with equivalent information, tools and cutoffs;
actual vendor claims require authorized evidence and current primary sources.

Agere must engage with healthcare and healthcare-adjacent questions across
patients, episodes, populations, physicians, hospitals, ACOs, payers, services and
contracts. Examples include why admission/readmission happened, deterioration,
care gaps, interventions, missing care, social/environmental contributors, ACO
upside/downside risk and shared savings, and physician/hospital performance.
These are examples, never a fixed question allowlist. Anticipate unfamiliar
combinations. Medication is one domain; it is not the product's organizing scope.
Population health, VBC and outcome-aligned payments remain central.

Build the shared path from original question and entity/time scope through
received evidence, interpretation, longitudinal state, analysis and supported
answers, then applicable buyer capabilities, payer/contract rules, work and
follow-up. Analytical questions need not force a clinical intervention. Explicit
unknowns are necessary, but cannot substitute indefinitely for building missing
capabilities or fixing disconnected workflows.

## Read first, in this order

All relative paths below are under `/Users/mikel/clinical-insights-local`.

1. `AGENTS.md`, `BUILD_OWNER.json`, `AGERE_BUILD_CONTROL.json`, `CONTINUE_AGERE.md`.
   Run `.venv/bin/python check_agere_build_control.py`. Repeat these control reads
   and the check after compaction. Read the actual files rather than trusting this
   dated summary over newer explicit user directions or receipts.
2. The current review named by `AGERE_BUILD_CONTROL.json.decision_basis`
   (currently `AGERE_CTO_REVIEW_2026-10-11_EVENING.md`) and the architectural baseline
   `AGERE_PRINCIPAL_REVIEW_2026-10-11.md`.
3. `AGERE_PRODUCT_DIRECTION.md`, the entire **Preserved full-product acceptance
   backlog** in `AUTONOMOUS_STACK_QUEUE.md`, and `AGERE_RESEARCH_HANDOFF.md`.
4. `config/agere/active_warehouse.json`, the latest report/receipt named by
   `BUILD_OWNER.json`, and evidence relevant to the current gate and next action.
   Latest at this handoff: `BROAD_QUESTION_PLANNING_BUILD.md` and
   `artifacts/agere-broad-question-planning-v1/implementation-receipt.json`.
5. Recover the wider discussed work using the research map below. Read relevant
   sections and receipts; don't flood context by rereading every historical file
   on every turn. Historical next-action statements do not override the control.

## Ownership and manual continuation

The source chat is `01a12586-d993-7dc1-bb99-81f420025b4e`. At handoff it is paused,
has no running implementation or provider inference, and is preparing this prompt
only. It has not transferred ownership to an invented destination ID.

When the human supplies this guide with authorization to resume in the new chat:

- Verify the actual new chat ID and that the source owner is idle. Take sole build
  ownership by updating `BUILD_OWNER.json` and recording the transfer in the
  control decision log. Do not dispatch implementation back to either prior chat.
- Use the app automation tools to reconcile the existing
  `continue-building-the-agere-stack` heartbeat's owner/target references with the
  new owner while keeping it **PAUSED**. Preserve other settings; do not create a
  duplicate automation or enable automatic continuation.
- Preserve `continuation_mode=human_turns_with_outcome_next_step_and_master_plan_update`.
  Resume the authorized manual build and run
  `.venv/bin/python check_agere_build_control.py --intent build --gate G3`, using
  the actual current gate if it has changed. Do not guess IDs or bypass a failed
  continuity check.
- The user is present and explicitly requested stopping between implementation
  turns. Finish one meaningful connected increment, explain outcome/evidence/gaps,
  next step and master-plan fit, then pause for the next human continuation.
  Do not repeatedly ask permission for ordinary already-authorized engineering.

## Latest verified state and the immediate build

G3 remains active: connect questions, decisions, services and work. G5 model and
analytical qualification remains open. Other accepted gates establish only their
declared finite local engineering behavior, not universal product completion.

The last increment repaired shared planner dictionary loading and context,
measure-definition and financial-date parameter contracts. Four original broad
development queries improved from one executable result to four. A fifth
financial/appointment/payment question executes after a preserved failure. All
five answers are **partial**; this is not independent broad-query or clinical
qualification.32 focused tests passed;318 protected files/prior receipts were
unchanged. Four final exact-question replays required zero new paid model calls.
The source-state and runtime clinical validators were not relaxed.

The immediate measured gap: `appointment_followthrough` already exists as a
dedicated UI/service/work-adapter capability, but the shared open-question graph
planner reports generic appointment follow-through unavailable. Read
`APPOINTMENT_FOLLOWTHROUGH_BUILD.md` and inspect the actual shared graph,
contracts, planner, service and work adapters. Connect the existing documentary
semantics through that shared path. Verify an open care-gap question composed
with another domain, exact patient/full-population and time scope, provenance,
unknowns, and applicable existing work/follow-up. No question-specific answer
handler. Start substantive implementation after orientation, not another
catalog-only or plan-only turn. The canonical checkpoint owns the exact action.

This is an integration step toward breadth, not the whole remaining stack.
Additional gaps include broad source interpretation, reusable temporal states,
clinical/program qualification, measure/attribution definitions, prediction,
discovery, intervention benefit, provider comparisons and performance. Recent
ACO replay took80.99s; earlier population allocation111.438s and three-handoff
execution274.606s remain measured latency gaps. Do not keep adding small plumbing
increments without delivering more useful answers. After two increments without
gate progress or measured failure reduction, reassess and record the correction.

## Recover the wider stack; do not lose discussed layers

The ten preserved areas are: native source meaning; evidence dependencies and
acquisition; deliberate warehouse migration; broader learned interpretation;
training/model qualification; clinical/program/action breadth; longitudinal
outcomes/prediction; discovery/explanation; financial/provider/contract performance;
and product/full-stack acceptance. Retain all ten.

Use these existing sources to reconcile proposed work with actual code:

- `AGERE_STACK_RECONCILIATION.md`, `AGERE_CONNECTED_STACK.md`,
  `AGERE_BUILD_GAPS.md`, `ENGINEERING_RESEARCH.md`.
- `AGERE_RESEARCH_RECOVERY_2026-10-10.md` and
  `artifacts/agere-multipass-synthesis-v1/research-recovery-index.json`, which maps
  66 previously recovered files and their dispositions. Check for newer material;
  the index is not proof that every historical conversation idea was recovered.
- `research/vbc-landscape/`: healthcare/VBC/program knowledge, question curriculum,
  evidence catalog, source and coverage notes. Questions are resources, not limits.
- `research/model-economics/` and `research/agere/`: model roles, bounded specialists,
  context engineering, source learning, state versus observation, missingness,
  information acquisition, symbolic constraints, incremental caching, selective
  computation, Bayesian/change-point methods, partial pooling, causal designs,
  constrained allocation, tensor-network candidates, and BOTTLED disposition.
- Read-only separate corpus:
  `/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/`.
  `AGERE_RESEARCH_HANDOFF.md` maps its product, model-design, competitors,
  open-source, system-review and cross-field computing/physics/economics/HFT/quant/
  small-model/policy documents. Preserve useful ideas without accepting analogies
  as proofs of applicability or novelty. Do not edit that corpus.
- `MVP_PRODUCT_REFRESH_BUILD.md` and
  `/Users/mikel/caresignal-mvp/public/agere/PRODUCT_THESIS.md` describe the original
  MVP integration and the explicitly labeled product/research/competitive thesis.

Maintain the existing research-to-capability reconciliation: proposed, researched,
implemented in isolation, connected, evaluated, qualified, or deferred with a
specific reason. Cite actual code/receipts and prerequisites. Missing source data,
missing training targets, missing implementation and failed qualification are
different gaps. Reconcile alongside delivery, not as an endless audit phase.
Do not silently drop suggestions that remain unreconciled.

## Economics, runtime and protections

The user deliberately uses an expensive frontier engineering model and expects
substantive value per turn. Use targeted reads, bounded relevant model calls,
exact caching and focused verification; avoid unnecessary repeated tests,
full-history dumps or additional models without a measured role. Distinguish
development-chat tokens from product inference. Last increment's18 distinct
product-provider responses had a configured estimate of$1.282504 including failed
validations, excluding Codex/Astra, local hardware and human review; not invoice
verified. This does not establish optimal product economics. Spend remains
uncapped under the user's October10 instruction, with cost accounting and all
duplicate/failed-request protections retained.

Active configuration at handoff: v1.23, original1,614 synthetic patients,
1,238,686 received records, cutoff2029-08-15. Read the configuration for current
truth. Runtime last verified at `http://127.0.0.1:8885/stack`; original MVP at
`http://127.0.0.1:5178/app.html#overview`. Stored process IDs/sessions are historical
observations: verify listeners/process ownership before reusing or restarting.
Keep the MVP connected to delivered capabilities and label research separately.

Preserve concurrent work, frozen Kev/d1 experiments, reserved evaluations,
source-family boundaries, original cohort, immutable receipts and the preceding
G6 recovery release. The citation-recovery candidate remains inactive as recorded
in owner/control files. Do not activate it implicitly. Keep hidden generator truth
out of runtime evidence. Do not edit the separate generator or Documents corpus.
Inspect current public release metadata before any deliberate warehouse migration.
Do not expose credentials, buy services, publish externally, contact patients or
others, or access the employer environment. Preserve any existing specific
authorization only within its actual scope. Do not fabricate clinical review,
novel mathematics, model superiority, savings or finished capabilities.

Before handoff, update the canonical checkpoint with delivered behavior, measured
change, evidence, remaining gap and exact next action; seal actual deliveries and
rerun the continuity check. Report engineering acceptance, clinical/model
qualification and external deployment prerequisites separately. Build toward the
full ambition while making each turn's delivered value concrete and reviewable.
