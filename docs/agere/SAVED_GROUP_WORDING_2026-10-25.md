# Saved-group follow-up wording — October 25, 2026

Candidate, not yet deployed. Ordinary grouping commands such as “now by payer type,” “show it broken out by LOB,” and “can you group that by line of business” use the existing saved-group or measure paths. Courtesy requests for the patient list use the verified saved membership. LOB and type-of-payer wording map to the existing field names.

A measure re-asked with a new grouping now retains its saved input patient scope, including an explicitly empty scope. A missing saved reference never becomes a whole-warehouse search. Extra filters are retained for interpretation; an unsupported patient-list qualification does not silently return the unfiltered list. Legacy structured conversation resolution remains available.

Focused checks:27 pass. The open population-quality paraphrase trial improves from44/59 to47/59 scored correct, with6refusals versus12, at$0. It recovers three patient-list/grouping answers without losing a passing answer. Three newly answered hypertension-control groupings still disagree with the historical key: the current patient line-of-business attribute follows the latest member-month, while the key assigns the latest enrollment span. Overall hypertension-control totals agree (505/707); payer allocation differs. This is not a PMPM result. No clinical attribution policy or historical key was changed to improve the score.

The initial missing-context test accidentally supplied an invalid Boolean conversation ID. It now sends the supported request shape and specifically requires QuestionNotUnderstood before any compose call. Qualification must use this corrected test, not the earlier broad exception assertion.

Full regression and frozen complete-bank qualification are pending. The current live release remains6fb43d4. No new provider calls were made; paid Haiku remains blocked by insufficient credits. No held-out/CHECK questions were accessed or rerun.
