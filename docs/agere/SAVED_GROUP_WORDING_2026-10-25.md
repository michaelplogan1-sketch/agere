# Saved-group follow-up wording — October 25, 2026

Candidate, not yet deployed. Ordinary grouping commands such as “now by payer type,” “show it broken out by LOB,” and “can you group that by line of business” use the existing saved-group or measure paths. Courtesy requests for the patient list use the verified saved membership. LOB and type-of-payer wording map to the existing field names.

A measure re-asked with a new grouping now retains its saved input patient scope, including an explicitly empty scope. A missing saved reference never becomes a whole-warehouse search. Extra filters are retained for interpretation; an unsupported patient-list qualification does not silently return the unfiltered list. Legacy structured conversation resolution remains available.

Focused checks:27 pass. The open population-quality paraphrase trial improves from44/59 to47/59 scored correct, with6refusals versus12, at$0. It recovers three patient-list/grouping answers without losing a passing answer. Three newly answered hypertension-control groupings still disagree with the historical key: the current patient line-of-business attribute follows the latest member-month, while the key assigns the latest enrollment span. Overall hypertension-control totals agree (505/707); payer allocation differs. This is not a PMPM result. No clinical attribution policy or historical key was changed to improve the score.

The initial missing-context test accidentally supplied an invalid Boolean conversation ID. It now sends the supported request shape and specifically requires QuestionNotUnderstood before any compose call. Qualification must use this corrected test, not the earlier broad exception assertion.

Full regression and frozen complete-bank qualification are pending. The current live release remains6fb43d4. No new provider calls were made; paid Haiku remains blocked by insufficient credits. No held-out/CHECK questions were accessed or rerun.

## Recorded code status and the question actually asked

The candidate also repairs an audited note-answer error: a documented DNR/DNI record was prefixed “Yes” to a question asking whether the patient wants CPR or intubation. It now states “No: Recorded code status: DNR/DNI (no CPR; no intubation)” with the original dated citation. Open wishes questions receive the translated recorded status without a Boolean prefix. The projection uses existing code-status note-frame routing; a bare CPR procedure question does not become a code-status read.

The projection uses received code-status enums, not the presence of documentation as agreement. DNR alone does not establish intubation preference; DNI alone does not establish CPR preference. Conflicting or unresolved recorded status remains unknown. A question about a discussion or mention retains its documentation meaning. Negated preference wording that lacks its own parsed proposition remains undecided. No clinical order or care execution is created.

130focused checks pass, including three-valued combinations, partial orders, conflicts, documentation-versus-status intent, record preservation and the existing compound-note contract tests. Three open dev note wordings retain their historical score credits at$0; the first two now answer the intervention proposition instead of merely confirming that a status was documented. This does not establish representative clinical accuracy. The broader check caught a documentation-presence regression; it was repaired without changing the existing test expectation. Combined whole-bank and final regression qualification are pending.


Projection scope is restricted to code-status and resuscitation-preference readings. It cannot replace an answer containing an unrelated predicate. Boolean connectors are read between the intervention/status operands, so an “or” in a setting such as “clinic or hospital” does not turn an AND into OR. Conflicting explicit preferences remain unknown; a compatible explicit preference can supply an intervention unspecified by a partial order. The broad CPR keyword routing from the initial prototype was removed before activation to preserve procedure questions.
