# Complete literal source comparison — October 10, 2026

Saved review questions can now compare two explicitly selected received fields or original submitted source-unit spans locally. The result retains the original question, exact source records, field paths, full values, source dates, hashes and comparison scope. No new model inference is needed for literal equality. The feature is connected to the existing exact-reference controls in `/stack`, with preview, save and history API routes.

Equality preserves scalar types and every character. It does not normalize units, case, Unicode, whitespace or line breaks. Missing fields are rejected rather than converted to null; null presence is explicitly reported as unobserved value. String differences retain codepoint ranges and bounded explanatory excerpts while comparing the complete inputs. The 262,144-byte per-value input bound rejects oversize values instead of silently comparing a prefix. Preview/display omissions are labeled separately. Source-unit comparisons validate their exact received-field offsets and hashes. Comparing two names for the same source location is explicitly labeled.

Receipts are content addressed, idempotent and immutable in `reference-comparisons.sqlite3`. Source/code changes invalidate current verification; historical results remain inspectable with stale reasons. Literal equality does not settle authorship, independent corroboration, identity, clinical equivalence or the original question. No clinical disposition, human review or care action is created.

## Actual saved-question result

For medication answer `3c26f4b1afff1ff8048117a7c55bed945dcd40306ec1324c1240af2dd3cc8c36`, references `p1:u6` and `p2:u3` point to distinct received records. Their complete 208-character `text` fields match exactly. Their submitted unit spans also cover those complete fields and match. Their `event_date` values differ. All three comparisons are saved against the original review question, reproduce exactly and verify as current. First API timings were 1.051, 0.974 and 0.971 seconds; these individual measurements are not production latency guarantees.

## Verification and preservation

`artifacts/agere-reference-comparison-v1/implementation-receipt.json` binds the implementation and evidence. There are **77 passing focused tests, including 16 new cases**, plus actual HTTP replay/rejection/history checks and browser verification of the new controls and result. Python compilation and JavaScript syntax checks pass. An initial test command named a nonexistent test module; the corrected suite passed, and the initial failure log is retained.

The 77 previous protected files, eight paid answers, six buyer tasks, functional work history and 19 separate Documents research files remain unchanged/current. The new comparison module extends the protected list to 78. The active v1.23 original-cohort warehouse is unchanged. No new provider/token-count calls, external actions or human reviews occurred; the ledger is unchanged. Spending remains uncapped. Server PID 380 / session 6377 serves port 8885.

## Remaining work

Semantic/clinical interpretation and validated care recommendations remain separate. Literal comparison is not a general contradiction adjudicator. Current-source model qualification attachment, richer clinical/benefit integration, stale-answer retirement and portable full-product acceptance remain incomplete. The existing sole-owner continuation is active every five minutes after the user's overnight instruction; no full-completion or morning deadline guarantee is made.
