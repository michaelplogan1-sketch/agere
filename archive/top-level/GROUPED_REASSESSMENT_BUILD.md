# Grouped question reassessment — G3

The existing saved-question path now accepts complete multi-group evidence instead
of rejecting it at preparation. It reuses source-plan storage, per-group answers,
explicit complete answer selection, original-question composition, buyer/payer
matching and replacement internal work. No new database or alternate model route
was introduced. Partial group answers cannot replace a complete question.

This is a partial engineering delivery. **85 focused tests pass** (74 grouped,
source-plan and existing reassessment checks plus 11 acquisition regressions).
Those answer-to-work tests use explicitly stubbed model output. Actual production
HTTP, browser interaction/DOM and fresh-process checks verify preparation of the
real saved P-1006 payer question into **two groups, 24 records and 53 source units**,
with no omitted selected units. They verify idempotency, missing-answer rejection,
stale-preview rejection and retained original work. Real current interpretation,
replacement-work execution from that interpretation, and clinical quality are
**not verified**. Changed-evidence acceptance stays **1/4** and overall finite
release engineering stays **6/9**. No acceptance condition was weakened.

## Exact behavior and compatibility

- Each complete grouped selection remains bound to the original question, patient,
  date window, source versions and task revision. A complete saved answer is
  required for every group; exact plan identity binds reassessment to the prepared
  scope. Source or work changes reject stale plans and answers.
- Existing answer composition retains group reference scopes and unresolved
  cross-group relationships. It does not invent a combined clinical conclusion.
  Replacement decisions use the existing documentary baseline and the original
  buyer; current payer/participation prerequisites remain independent.
- The UI continues from the existing review-changed-sources control, allows group
  reading/selection, and returns the composed answer to reassessment. The final
  composed-answer/save interaction awaits genuine answers; it has not been
  represented as browser-verified.
- A repeated grouped preparation exposed a uniqueness collision during review;
  reassessment plan persistence now checks exact idempotency. Distinct reassessment
  preview contracts have their own digest. Earlier preparations remain immutable.
- The preservation check caught an edit to the protected source-plan module before
  sealing. It was restored byte-for-byte. A scoped adapter in the reassessment
  layer handles only the new preview kind and delegates ordinary plans to the
  unchanged engine. It reuses the same immutable store and answer contracts.
  The rejected edit and subsequent correction are retained as development evidence.
  Current saved payer decisions/answers reopen unchanged after restart. An older,
  already historical answer still fails its prior rule-compatibility check.
- The reassessment implementation changed with this delivery. Its explicit v1
  compatibility accepts the previously saved implementation only for the old
  single-group schema, while retaining all exact context/evidence/scope checks.
  A before/after binding comparison on the actual pending P-3002 context is equal;
  its saved preparation and payload bytes are unchanged. This local contract probe
  did not create an interpreted answer or dispatch a request.
- Uncapped UI copy now describes an unavailable route without suggesting a spend
  ceiling. Spending remains uncapped; accounting and failed-key safeguards remain.

## Actual replay and limits

The isolated replay copies received P-1006/P-1004 records and existing work. It
changes one P-1006 coverage-verification record only in that copy, adding a marked
controlled correction that leaves verification pending. This is an engineering
source-change fixture, not an adjudicated clinical/benefit fact. The original
warehouse, task and sources are unchanged. The replay inherits original run
counts for service compatibility; its two-patient contents are not a full-cohort
evaluation. Original answer stores are not copied, so its parent is shown as
historical; the actual original workbench still reopens the current parent.

The initial replay selector chose an earlier incomplete eight-record task and
stopped before mutation. A subsequent selector exposed an old historical answer.
Both observations were retained; the final replay selects a verified current,
complete payer decision. An idempotency and request-type review added duplicate preparation and non-string-ID protection; final tests cover both. An initial adverse HTTP assertion expected 400 where the
service correctly returns 409 for stale work; the assertion was corrected.

Actual source interpretation was rejected **before execution** by automatic
approval review: it requires explicit authorization to send the specific P-1006
payer evidence to Anthropic. The payload is 24 selected synthetic records in two
bounded groups. The user was asked for that approval. No retry, alternate provider
or local interpretation workaround was attempted. The separate P-3002 appointment
and P-1001 medication transfer blocks remain. Zero new provider calls were made;
this is not a savings or model-quality claim.

Browser interaction, accessibility and DOM checks passed, including reopening
both groups after a service restart. Visual layout is not claimed. Temporary
browser tabs14/15 and the isolated8887 server were closed. The original8885 workbench
runs the final code. All170 protected files,144 captured prior receipt files and
the pending appointment artifacts remain unchanged. Receipt counts include archive
copies, not144 milestones. The previous G6 archive remains the recovery point.

## Remaining release work

G3 needs actual current interpretations and replacement work, changed/late/conflict
behavior, unchanged finding identity, and the medication workflow. P-1006 inference
and an HTTP finish verifier are saved in `verify_grouped_reassessment.py`; execute
inference only after its specific approval. P-3002 retains its separate verifier.
A passed preparation or stubbed test cannot close those checks.

G5 still lacks complete historical/reviewer economics, defensible support for eight
classifier labels and independent qualification. The original ten-area backlog,
population health/VBC/OAP requirements and external deployment prerequisites remain.
All research and clinical qualification are not complete. Do not add more helpers
or try another patient's hosted transfer merely to avoid the pending approvals;
resume from the canonical checkpoint when a prerequisite changes, or perform only
independent work tied to a named measured failure.
