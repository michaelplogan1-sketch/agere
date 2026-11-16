# Question-bound source review and local reader execution

The existing saved-question workflow can now inspect a source reading's latest documentary review and attach that exact revision to its owned work. Attachments retain reading, source, task revision and review digest; replay is idempotent. Later reviews, source changes and changed work make the attachment historical. They do not silently rewrite it. No task/care state or training permission is changed by attachment.

Review effort is optional and explicitly declared in seconds with a declarant. Blank means unknown, zero remains a distinct explicit declaration, and neither identity nor effort is authenticated. The interface never times an open tab or fabricates a reviewer. Existing semantic review storage and question follow-ups are reused; there is no new accounting store or review engine.

The local reader caller now records starts, abstentions and returned comparison/reading identities in the existing execution journal. A cached comparison cannot inherit the original reading's inference time or be counted as new inference. New comparison assembly distinguishes a cached reading, reported inference and failure; interrupted calls preserve the started source/model identity. The observed duration covers the current comparison call including lookups, not a priced CPU/GPU measurement. Review attachment failures retain their original question binding.

## Evidence and limitations

38 focused tests passed. Thirteen new isolated tests exercise the actual semantic review store and owned-work fixtures for exact replay, unknown versus zero effort, malformed effort, wrong reading/revision/digest, superseding review, source/work changes, concurrent revision change, local cache/inference/failure and interruption. The initial new fixture failed because it attempted to introspect mocked reader functions; a fixture-only identity corrected that setup. The initial failure log remains preserved. These are simulated documentary reviews in temporary stores, not human adjudications or clinical accuracy evidence.

Actual HTTP verification uses the existing respiratory question, original received statement, same-source retained Kev comparison and owned task. It checks exact replay, review status, rejection of an invented review reference, wrong patient/work rejection, classifier abstention, saved dispatch identities and preserved question/answer/work. No model calls, new readings or reviewer records were created. Positive review attachment is verified in isolated fixtures; the real reading remains unreviewed. The normal server restart and browser checks are recorded separately with the delivery receipt.

All 170 protected files and 126 prior implementation receipts retain their bytes. No frozen learning/semantic engine, Kev/d1 experiment, original cohort, generator or separate research corpus changed. The current caller version creates a new statement follow-up; earlier statement receipts are retained and correctly report a changed implementation when reopened.

## Gate disposition

Economical routing remains 4/5; full-release engineering remains 4/9. This increment removes the exact review/dispatch linkage gap. It cannot reconstruct uninstrumented historical effort, measure full GPU/child-process cost, supply absent authentic review time or establish cost per independently accepted answer. The fifth routing check stays blocked. No API savings, model superiority, complete clinical qualification or clinical authority is claimed.

The canonical sequence advances to independent G6 local release packaging, while retaining G5 qualification and economics as incomplete and G3 as blocked 5/6. Work next on reproducible startup, portable adapter conformance and transaction/backup/recovery through existing stores; do not add more accounting-only increments. Final release acceptance still requires the unresolved gates, the full ten-area population/VBC/OAP charter and separate external production prerequisites.
