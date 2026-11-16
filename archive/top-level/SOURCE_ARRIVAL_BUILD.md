# Received-source follow-up — G3

New question decisions now retain an exact inventory of the patient's locally
received source versions. The existing decision/work view can check and persist
new arrivals, changed versions and records absent from the current snapshot before
a new interpretation is available. The comparison retains the original question,
answer and work revision. It never completes care or changes a task automatically.
No new module, model or database was introduced.

The previous selected-record-only comparison could not distinguish newly received
records from records already outside an answer's selection. The new baseline fixes
that for new decisions. Historical decisions retain their original payload and
explicitly report that outside-selection arrivals cannot be established. Source
changes block further queue/handoff operations on new stale decisions. Checking a
saved answer also checks the received inventory, so an unchanged selected answer
cannot report no new evidence when the inventory has changed.

Verification used the actual saved functional question for synthetic P-1004,
the local source-category planner and its existing buyer-owned internal handoff.
The HTTP path captured all165 received records while retaining the answer's eight
selected records, saved an observation idempotently, reopened it and preserved it
through a server restart. Browser rendering and the no-model observation button
were verified. No provider request ran; the ledger is unchanged.

A small isolated copy of that decision, task and patient's received records tested
three controlled changes: a plainly labeled local engineering probe row, a version
marker on an existing selected row, and removal of an outside-selection row. All
three were detected and saved; the work state and original question stayed intact.
The active warehouse was not modified. The isolated copy intentionally lacks the
complete interpretation cache: this verifies arrival observation, not a new reader
answer, clinical change, clinical correctness or external source integration.

Forty focused tests passed, including cross-patient/future/unmatched/non-synthetic
rejection, duplicate identities, source/task changes during observation, historical
coverage, stale work guards and the unchanged-answer/new-arrival regression.
JavaScript syntax passed. All170 protected files and170 prior sealed evidence files
remain unchanged. Evidence and code hashes are in
`artifacts/agere-source-arrival-v1/implementation-receipt.json`.

G3 remains5/6 individual engineering workflows. Medication's bounded Anthropic
transfer is still awaiting the specific approval already requested after automatic
approval review rejected it; no retry or bypass occurred. Changed-source clinical
interpretation and affected-finding recomputation remain unverified. Current source
invalidation is conservative at the patient inventory level; relevance to a given
question is unassessed. Atomicity across separate evidence/work stores remains a
packaging concern rather than an asserted guarantee. G4 population/VBC/OAP, G5 model
and analytical qualification, and G6 packaging remain open. The ten-area product
charter, original cohort, warehouse v1.23 and model activation decisions are unchanged.

Current sequence and exact next action remain in `AGERE_BUILD_CONTROL.json`.
