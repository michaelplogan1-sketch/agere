# Follow-up interpretations return to original questions — October 10, 2026

The unresolved-question workflow now retains saved follow-up interpretations. Candidate selection, complete received-account selection, and dated selection rooted in a candidate question can attach a complete saved answer to the exact original evidence-needs receipt and question. Attachments retain original source/answer hashes, selection/context identity, the new answer, its evidence needs and current/stale dependency state. A multi-pass answer must include its required reconciliation.

`evidence_answers.py` stores append-only machine attachments in `evidence-answers.sqlite3`; SQLite rejects update/delete. Replay is idempotent. Different questions, patients, contexts, incomplete answers and forged resolution fields are rejected. Changed source/question/answer dependencies make saved history stale. No original question, documentary disposition, task review, clinical state or execution event is changed.

Two API routes and `evidence_answers.js` connect the actual question cards, candidate/account preparation and candidate-window preparation. Successful interpretation uses the existing saved-answer callback to attach its result. A separate “Attach saved follow-up interpretation” button locates an already saved answer and attaches it without model dispatch. The question card can load attachment history and open a current saved answer.

## Verification and limits

Eight new attachment tests pass; 118 combined focused tests pass, including the 13 new timing tests and existing context, financial, source, attachment and work-state coverage. Actual HTTP verifies exact replay, context/question/forged-resolution rejection, unchanged original needs and current dependency history.

The existing medication candidate answer `d2e5418b026facabbd62a9c33ed741cb3dda390d317c8f8de22cfe28a992b936` is attached to its original reassessment question. It contains three findings, five exact citations and three new review questions (one with located references, two unbound). The answer describes an older imported record; attachment does not establish that the newer reassessment happened or that the original question is answered. No new model call or charge was needed.

Generic-context date windows retain full timing/selection provenance but do not yet have the question-attachment shortcut; candidate-rooted windows do. Account answer attachment is tested with synthetic fixtures; the real 17-record account interpretation remains pending the previously requested exact-payload approval. Do not route around that rejection or represent the live account interpretation as completed.

All 27 protected engine/catalog files, the active original-cohort warehouse configuration, five prior answer identities, the original financial account receipt and the task follow-up attachment remain current. No clinician review, external care action, source migration, generator/research/frozen-cohort edit or new credential occurred. Source timing and selection are documented in `EVIDENCE_TIMING_BUILD.md`. Full product acceptance remains incomplete.

Evidence: `artifacts/agere-evidence-answers-v1/implementation-receipt.json`, attachment/history/HTTP receipts, focused test log, preservation audit and browser proof.
