# Agere MVP: owner assessment and fresh-chat handoff

**LATEST USER STEERING — 2026-10-29T13:30:27.775574+00:00: Mike reconfirms the review-based stack update toward completing the MVP and explicitly permits useful Git pushes. The inherited push stop below is superseded. Continue verified updates on the existing private integration branch/PR; no main merge or publication.**

**LATEST USER DIRECTION — 2026-10-29T13:01:14.413920+00:00: Mike explicitly requests a new chat and autonomous completion of the MVP update while away. This supersedes the older discussion hold below. Build is ACTIVE; automatic Git pushes stay off. Scheduled continuation is ACTIVE in new sole-owner chat 01a187c0-23ba-7d02-9368-5ff567327554. Sending chat has stopped all product work. Read current control/owner/CONTINUE files for the registered owner and actual automation state. No provider or training job is active. Latest code is c16cad7: generic collection visibility,45offline checks,106dated clinician objects across13patients exposed without changing native rows. The next code task is explicit entity/relation binding for review_documents, not another architecture report.**

Prepared October 29, 2026, after Mike requested critical review of Claude's
architecture report and asked whether a new chat would help. This records a
recommendation and current evidence. It does not activate a new architecture,
resume the paused build, narrow the product charter or authorize automatic pushes.

## My assessment

**Keep the existing application and connect its evidence readers to its existing
typed state and deterministic composition. Stop making a general LLM repeatedly
reconstruct the meaning of retrieved quotes.** This is the strongest part of
Claude's review and the next engineering priority I recommend.

An evidence row should state, in machine-checkable fields, whose record it is,
which entity it concerns, what relationship is documented, whether it is affirmed,
negated or uncertain, when it applies and the exact supporting source. Code should
join and calculate from those rows. A model can interpret unresolved meaning;
it should not repeatedly recreate the whole patient table or do the arithmetic.

The relation contract must work for medication reasons, care relationships,
service status, access barriers and contract terms through the existing system.
HF remains the original integration question, not a new hard-coded handler.

### Findings I checked directly

| Review finding | Owner check | Consequence |
|---|---|---|
| Document decisions select spans but leave interpretation to the actor | `typed_document_review.read` stores `value=span['text']` and the same text as `quote` | A quote's presence is not a verified entity-reason relationship. Connect source decisions to typed relations. |
| Care-team evidence is hard to discover in compact state | `execution_state.catalog` and `semantic_analyst.actor_result` advertise fields inside `review_json`; they do not build an equivalent manifest for `source_clinicians_json` | The original native values and column names still exist. Repair visibility and composition rather than rereading every note. |
| The model ladder does not select by role | `model_policy.ladder(role)` returns the same Haiku/Sonnet sequence; `_resolve` uses `model_for()` without a role argument | Current Haiku-first policy is real. Compare roles before recommending a policy change; stronger-first everywhere is not established. |
| Support checks repeat shared/global state | `support_packets` includes global summary and shared/native bindings in patient packets | Claim-scoped checks and dependency-local reuse are promising; quantify cache loss before rewriting them. |
| Learning invalidation is too broad | A direct correction loops over `ALL_ROLES` in `component_learning.observe` | Attribute feedback to the affected role and dependencies so unrelated useful components survive. |
| Runtime selection does not establish accuracy improvement | `harness_improvement._finish` uses completeness and cost-regression conditions | Preserve existing fresh-executor output validation; add end-to-end semantic regression evidence before claiming learning improved accuracy. |

These are source inspections, not newly run clinical experiments.

### Qualifications and corrections to Claude's recommendations

1. **Reuse existing typed machinery.** Claude did not receive the full repository.
   `assertion_reconciliation.py`, `native_assertions.py`,
   `native_care_assertions.py`, `clinical_state.py`, `interpretation_state.py`,
   `medication_state.py` and `relation_learning.py` already represent relations,
   clocks, subject/assertion status and documentary state in different places.
   Inspect their applicable contracts and join the active document path to them.
   Do not create another universal truth database or parallel interpreter.
2. **Schema validation does not guarantee semantic correctness.** A reader can put
   a valid medication ID on the wrong sentence. Entity-bound inputs reduce
   ambiguity, but attribution still needs source-based checks and meaningful
   transfer tests. Do not describe errors as eliminated by construction.
3. **Narrow retrieval must not hide evidence.** Entity anchors can miss anaphora,
   reasons stated elsewhere and unnamed references. Retain whole-record access,
   nearby context and a coverage path for unanchored material. Adopt pruning only
   with evidence that useful coverage holds. No silent patient/document caps.
4. **The cold cost split is available.** The review called that split unknown.
   `full-run/economics.json` records $8.69577884 Claude API-equivalent and
   $1.148793924 third-party, out of at least $9.844572764: Claude is 88.33% of
   the known total. Four attempts are unpriced. The continuation is 93.57%
   Claude by known API-equivalent cost. This supports targeting repeated
   frontier interpretation; it does not prove every reader is efficient.
5. **Do not equate incomplete comparison runs with zero correct answers.** The
   current automatic comparison lacks an established complete-correct workflow
   denominator. It is not a statement of 0% clinical accuracy, nor a rejection of
   Mike's independent acceptance of earlier outputs. Preserve that acceptance;
   do not require him to repeat those reviews.
6. **Model removal and lower latency remain hypotheses.** A timeout or decline on
   one compound question does not make a component universally useless. Avoid
   whole-question SQL bottling on tasks it cannot express; retain useful local
   models, encoders and rerankers for suitable roles. Ranking every block in an
   exhaustive review has no demonstrated benefit here. Scope switches by task.
7. **Transport needs engineering repair, not an unauthorized provider switch.**
   Preserve usage and partial output, honor tool-choice semantics, validate
   writable receipt storage before generation and resume recoverable work.
   Continue using Mike's Claude subscription. Do not impose custom output
   ceilings, response word counts or workflow/spend quotas. The provider's
   technical boundaries require continuation, not loss of the task. Stop on an
   actual subscription limit and tell Mike.
8. **The ten experiments in the review are a menu, not ten new prerequisites.**
   Use existing receipts first. Run only the checks needed to resolve a material
   implementation decision. Do not turn this review into another long research
   program or require new clinician reviews as a condition for ordinary fixes.

## Recommended delivery sequence

One connected product increment should first remove the known shared meaning
failures. A subsequent frozen comparison should establish whether the integrated
route earns its cost. Follow-on learning uses that stable successful path.

### 1. Correct the general source-to-result boundary

- Trace the known wrong-class reason through the saved reader output, SQL/JSON
  binding and final cell before changing the extractor.
- Reuse applicable assertion/state contracts to bind subject, entity, relationship,
  polarity, stated timing, visibility and exact source. Keep the original source
  text and contradictory accounts available.
- Carry documented clinician roles into the existing relationship view and
  compact state. Authorship, addressee, referral, current assignment and observed
  attendance remain separate.
- Bind numeric claims to their population, row grain, domain predicate, unit and
  executed value. For dose comparisons use the existing reference/status columns;
  distinguish a reference range, its upper bound, documented dose and an
  individual tolerated dose. Unknown prerequisites remain explicit.

**Proof of progress:** the existing source-derived cohort and fill-policy cells
stay unchanged; a reason is no longer assigned to the wrong medication class;
the 89 mixed medication rows cannot be labeled as 89 GDMT rows; dated clinician
objects remain discoverable and appear in the final applicable fields. Checks
exercise other entity/domain bindings as well. No new full-population note scan
is needed merely to repair composition of unchanged evidence.

### 2. Make verification local and capable of repairing the identified claim

Use deterministic checks for values, scope, dates, domains, units, join cardinality
and quote binding. Use existing typed readers for the remaining relation meaning.
Feedback should name the failing claim, source and reason. Recheck changed claims
and dependencies; preserve valid unaffected checks. Test with seeded mistakes and
clean examples so fewer calls cannot conceal weaker verification.

**Proof of progress:** wrong-domain counts, wrong-class reasons and dropped roles
are detected and corrected, while a clean supported unknown stays acceptable.
This is shared verification, not a new HF-specific checker.

### 3. Qualify a simpler execution order and a fair baseline

Recommended task-dependent shape:

```text
Question and saved context
  -> scope, dates, definitions and requested outputs
  -> governed SQL/engines for structured work
  -> relevant source retrieval and typed relation reading for text work
  -> deterministic state reconciliation and result composition
  -> source/claim checks and targeted repair of unresolved meaning
  -> code-rendered interactive result and PDF
```

SLMs can interpret suitable closed tasks; Jev/Perplexity judge source-bound
questions; encoders/rerankers help when selecting evidence. Use frontier reasoning
for unfamiliar planning or meaning that the relevant existing components do not
settle. Do not require every component to attempt the complete compound request.
Do not change model order merely because a model is larger or cheaper.

After the shared repairs, freeze code, source and configuration once. Run the
original HFrEF request through the application and compare with simple routes
using identical sources, definitions and deliverable expectations. The old
Sonnet-only arm had an invalid final contract and cannot establish superiority
or inferiority. A corrected new arm must preserve the old result. Separate
component comparisons from different end-to-end trajectories.

**Proof of progress:** complete requested fields or specific source-supported
unknowns, correct source/domain relationships, faithful dashboard/PDF and honest
all-attempt cost/latency. Add varied utilization, access/care-gap and financial
compositions through the same path to expose HF-specific overfitting. Do not
reuse protected evaluations or generated exact-match answer keys.

### 4. Make bottling and learning earn a repeatable saving

First reuse a validated parameterized workplan across different populations,
entity groups and output combinations. It must rerun native calculations and
refresh changed evidence. Measure construction, failed attempts, application,
fallback and regression costs. Exact-question replay is useful memoization but
does not establish transfer.

Then consider distilling stable source-bound relation tasks into the existing SLM
adapters, with source-family separation, candidate isolation, correction lineage,
honest gains/losses and rollback. Do not launch another large training run before
the target is stable and runtime use is demonstrated. Physician acceptance,
machine agreement, source equality and clinical correctness remain distinct.

### 5. Package a demonstrable MVP with explicit remaining dependencies

Use a finite matrix of promised current capabilities, checks, measured results and
known limitations. Preserve the full ten-area charter in `AUTONOMOUS_STACK_QUEUE.md`;
do not silently remove difficult areas or require every long-term research idea
to become a new launch project. Distinguish engineering capability from research
operators and unqualified predictions. Ensure local startup, app integration,
exports, result provenance and recovery work. Synthetic-data success does not
establish performance on client data. No sale-ready or universal-accuracy claim
is justified by this review alone.

## Current state to preserve in a new chat

- **Checkout:** `/private/tmp/agere-codex-integration-20261026`.
- **Branch:** `codex/integration-20261026`; current HEAD `d727e6d`.
- **Latest product code:** `1fffde9faf5d9fcf59a72f2001dc147d7e7a079e`.
  Later commits contain review/documentation. The remote branch was pushed
  through `1fffde9`; no push after Mike's stop instruction.
- **Private draft PR:** <https://github.com/michaelplogan1-sketch/agere/pull/2>.
- **Status:** build PAUSED, heartbeat PAUSED, no automatic Git pushes, no active
  provider or training job. New implementation has not been authorized by this
  review request; discuss this recommendation with Mike first. Document and
  continuity edits made during the assessment remain uncommitted.
- **Authority:** read `AGENTS.md`, `AGERE_BUILD_CONTROL.json`, `BUILD_OWNER.json`
  and the newest `CONTINUE_AGERE.md` header first. G3 remains active in the delivery
  sequence, and all ten acceptance areas are retained.
- **Original workflow evidence:** 57 patients, 228 matching source-defined
  fill-policy cells, 606 matching source quotations. The automatic result is
  partial; broader clinical accuracy is unscored. Existing observations are
  reusable only under their exact source/question/time guards.
- **Known automatic-run results:** integrated cold 57.1 min / at least $9.8446;
  Haiku-only 31.5 min / $3.8188, both partial. Sonnet-only 17.6 min / $11.0814
  had an invalid final contract. Continuation 32.0 min / $2.7183, still partial.
  Original cold + failed repair + continuation: at least $13.041780032, with four
  unpriced attempts. Baselines and reviews are separate R&D. All dollar figures
  here are API-equivalent estimates, not a Claude subscription invoice.
- **Claude's exact review:** `docs/agere/reviews/2026-10-29-claude-chief-architect.md`.
  No edits were made to its original text. It is advisory; some code and receipts
  were not supplied to Claude. This owner assessment resolves several omissions.
- **Review provenance/raw accounting:**
  `/private/tmp/agere-overnight-20261029/architecture-review-v3-20261029`.
  Completed October 29 at 06:27:27 America/Chicago in 616.76 seconds; successful
  request reports $1.375166 API-equivalent. Two preceding attempts remain
  unpriced and preserved. No subscription limit was observed.
- **Decision-only repository critique:**
  `/private/tmp/agere-decision-only-review-20261029/CRITICAL_REVIEW.md`, pinned
  commit `ab87ef2ee9c5e8668c8a25a33fcfb1d3021ff9e9`. Eight offline probes,
  zero providers or Agere product changes. Borrow robustness testing ideas;
  do not install its repeated-deletion runtime or copy triage thresholds.
- **Standing rules:** accuracy, then cost, then speed; heal unresolved work;
  no unilateral quotas or output-length caps; Claude subscription only; stop on
  an actual subscription limit; no dev3/held-out answer keys, protected corpora,
  generator truth or Documents research corpus. Existing synthetic payload
  authorizations persist. No agents/new chats, main merge, publication, contacting
  others or real-care execution without explicit new authorization. The earlier
  ban on new chats may be superseded only if Mike explicitly requests the move.
- **Runtime:** the primary product was at `http://127.0.0.1:8885/ask`; its current
  health/source version has not been freshly verified. Do not kill unrelated
  servers or confuse an inspection preview with a deployed candidate.

## Starting prompt for a fresh chat

> Continue the Agere discussion in the existing checkout
> `/private/tmp/agere-codex-integration-20261026`. Start with
> `docs/agere/MVP_REVIEW_AND_HANDOFF_2026-10-29.md`, then the current control/owner
> files and Claude's exact review. Preserve the paused build and automatic-push
> stop. Explain the first shared integration increment and its proof of progress
> before implementation. Do not restart training, repeat frozen runs, add new
> components, or inherit claims of accuracy/MVP readiness from passing unit tests.

This prompt preserves the current discussion hold. Mike can explicitly add an
instruction to resume implementation when ready. A fresh chat is recommended
for focus, not required as a cure for a full context window: compaction supports
long interactions. It does not automatically transfer every prior decision;
the handoff and authoritative repository state must be read.

Official context-management reference:
<https://developers.openai.com/api/docs/guides/compaction>.
