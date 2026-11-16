# Agere: source interpretation in population questions

October 9, 2026. Executable in the local synthetic stack at
http://127.0.0.1:8885/stack. Server session: 10993.

The source readers are now connected to complete-population composition. A question
can join saved statement interpretations and explicit source reviews to structured
records and documentary timelines, select patients by review status, and run another
operator on that entire group. No new inference is required for this reconciliation.

For example, **Investigate medication reader disagreements** examines the complete
observed population, selects patients with disagreements among saved readers, and
calculates source completeness for that full selected group. This selects a source-
review priority, not a diagnosis, care gap or treatment-eligibility population.

## Connected implementation

`clinical_core/agere/clinical/interpretation_state.py` adds a versioned derived
interpretation receipt. It reuses the existing assertion reducer and received-source
identities instead of changing the warehouse, existing annotations or clinical-state
artifact. Its inputs include literal structured assertions, baseline narrative
proposals, saved specialist/local readings and the latest explicit review revision.

- A consistently reviewed exact occurrence can replace the earlier machine
  interpretation of that occurrence. Replacement links preserve the original
  assertion identity. Structured assertions are not silently overwritten.
- Unreviewed reader outputs stay proposals. Multiple outputs never create
  independent corroboration or win a majority vote.
- Conflicting accepted/corrected reviews produce `review_conflict`. An explicitly
  unresolved review is separately represented as `unresolved_review`; either blocks
  the occurrence from becoming an effective reviewed interpretation.
- Partial-context reviews remain incomplete. Another model's rejected output does
  not undo a consistent source review. Reviewed unknowns still carry unknown meaning.
- Source, arrival, clinical and literal-reference dates remain distinct. Copied or
  historical statements do not become current because a later note repeated them.
- Source-reviewed assertions enter the existing documentary timeline reducer.
  They retain self-reported review authority and do not establish current use,
  tolerance, care completion, clinical state or program eligibility.
- Received units without mapped assertions and streams without records remain
  visible. The system generates evidence-review/acquisition steps without declaring
  that unrecorded care was not delivered.

The new `interpretation` graph node produces a full membership set, so its output
can feed existing analysis, source retrieval, set composition and worklist operators.
Allowed filters describe review metadata: `all`, `needs_review`, `model_disagreement`,
`review_conflict`, `unresolved_review` and `reviewed_source`. Clinical eligibility is
not an allowed filter. Unknown upstream membership stays unknown. Empty populations
stay empty, and returned patient previews contain only matches to the chosen filter.

The local example compiler and frontier graph schema both expose this operator.
The latter's redundant descriptive prose was reduced to keep the observed maximum
ASCII-question request at **19,971 bytes**, inside the existing transport limit.
The frontier was not called in this build.

In `/stack`, the new controls cover medication, outside care, affordability and the
disagreement-to-source-completeness example. **Inspect interpreted timeline** opens
the exact source-linked groups, retained machine assertions, documentary clocks,
replacement links and remaining evidence steps. Each saved source reading can be
opened in the existing correction form.

GET `/api/stack/interpretation?id=...` verifies source and review revisions before
returning a receipt. Derived results are stored in
`artifacts/agere-population-stack-v3/interpretation-state.sqlite3`.
The original source-assertion and model-reading stores remain separate inputs.

## Corrections, reuse and training

Each patient's derived receipt depends on that patient's baseline assertion identity,
saved reading IDs and latest review revisions. Changing one review invalidates the
dependent population result and that patient's receipt; unchanged patient receipts
are reused. No new model call is triggered. Reviews changing during a calculation
prevent an aggregate result from being committed under the earlier review snapshot.

The existing documentary-prerequisite overlay and training-candidate export now
use the same occurrence-level review resolver. Conflicting or explicitly unresolved
reviews of the same passage cannot supply a financial-report prerequisite or enter
the reviewed training export. No new clinical requirement bindings were added.
No source review was invented in the live workspace, and no model weights trained.
Review and correction transitions were exercised in isolated synthetic fixtures.

## Warehouse execution

| Scope | Synthetic patients evaluated | Baseline machine assertions retained | Saved refined readings | Reviewed source occurrences |
|---|---:|---:|---:|---:|
| Medication and dose preferences | 10,000 | 25,703 | 8 | 0 |
| Outside care / nephrology | 10,000 | 40,000 | 0 | 0 |
| Affordability | 10,000 | 1,270 | 0 | 0 |

Source: `artifacts/agere-interpretation-state-v1/run.json`, each interpretation
node's `patients_evaluated` and `processing_counts`. The medication scope now includes
`preference declines`, so its assertion count differs from the earlier medication-
only source-reconciliation scope. This is a scope change, not measured reader gain.

The disagreement filter evaluated **10,000**, matched **2** and passed those **2**
patients to source-completeness analysis. They are `ECO-11AEB2190629` and
`ECO-1B9860A33469`, the deliberately selected development cases from the prior build.
The node reused **10,000** patient receipts when filtering the previously reconciled
medication scope. The workflow and its replay made **zero new paid API calls**.
See `run.json` and `integration.json` in the same artifact directory.

The broad `needs_review` status covers all **10,000** patients in each scope because
baseline machine assertions, unassessed source units and/or missing contributing
streams remain. This is an implementation/evidence backlog, not 10,000 demonstrated
care gaps or a clinically prioritized worklist. Only the two selected development
patients currently have the new saved specialist/local statement readings.

## Verification

**124 focused software checks passed** across interpretation state, semantic reader,
assertion reconciliation, context passes, task context, analysis graph and program
work. They cover exact-occurrence replacement, conflicting/unresolved/rejected reviews,
copy/history/date boundaries, changed-source rejection, partial context, per-patient
cache invalidation, training/prerequisite exclusion, complete membership, downstream
composition and unknown/empty populations. Both rendered workspace scripts passed
JavaScript syntax checking.

All four authored questions compile locally. The complete population runner blocked
network calls while executing and replaying the graph. The running HTTP service
verified source inspection, the two-patient downstream population, matching-only
previews and exact cached replay. No new paid calls, model promotions, benchmark
edits or clinical eligibility decisions occurred.

These checks establish integration/accounting behavior, not clinical accuracy,
broader extraction recall, outcome benefit, causal explanation or vendor superiority.
Frozen Kev/d1 model-selection evidence remains unchanged.

## Remaining build

The new capability makes source meaning usable in population questions. It does
not yet turn the whole warehouse into a validated clinical state. Next extend
cross-source event/therapy identity, temporal reconciliation and explicit state
bindings for use, indication, exceptions, tolerance, completed care and patient
goals, preserving the distinction between unavailable data and absent care.

Connect those supported states to versioned clinical/program/outcome definitions,
attribution, denominators and persistent program work. Existing program-work
operators still use their existing assessment definitions; a newly selected review
group does not change their eligibility logic or mark an action performed. Continue
the correction-to-training/calibration/release pipeline on fresh patient/source
families; no automatic training or broad local-model promotion is implied.

Population health, VBC, provider performance, access, commercial metrics and
outcome-aligned payments remain in scope. HF/CKD are examples; action-execution
agents remain a later boundary.
