# Source timing and dated evidence selection — October 10, 2026

The question/evidence workflow now offers source-timing diagnostics for candidate search results and prepared record contexts. It preserves source dates, date precision, event/recorded/signed/arrival timestamps, represented offset dates, UTC instants, and existing explicit clinical clock evidence. Different calendar dates are not silently converted into contradictions or a guessed local timezone. No source, clinical clock engine or financial balance is rewritten.

An explicit source-event, clinical or arrival date window separates records into within / outside / unresolved groups. Unknown-date retention is an explicit choice, enabled initially in the UI. Preparing dated evidence retains complete typed-reference closure, so linked records outside the selected roots remain visible. Empty scope and overlarge context handling keep the existing guards; no clinical absence claim follows from an empty window. Receipts bind source versions, upstream context/question, chosen dates and resulting scope, and reject stale dependencies.

The new `evidence_timing.py`, six API routes and `evidence_timing.js` connect this behavior to candidate discovery and the normal bounded interpretation path. Existing native clock, date-window validation, typed references and context packing are reused. The public v1.23 manifest and public table columns were inspected; no timezone/date-basis contract was found in those materials. Organizations contain no timezone column. Private/generator/evaluation files were not read. Public source precision counts are 29,010 day / 1,163,026 minute / 46,650 unknown; no migration occurred.

## Verified examples

- Account scope: 17 records have unresolved separate date/timestamp calendar relationships. A December 1, 2026–February 1, 2027 source-event window includes eight records and excludes nine. All 17 explicit clinical dates are unobserved under the existing contract. The account’s unresolved balance remains unchanged.
- Medication candidates: a 2029 source-event window includes one of eight records; seven are older. The clinical-date view has one outside-window date and seven unknown dates. A 2029 source date does not establish a 2029 clinical reassessment.
- Outside care: one of six source records falls in the chosen September 21, 2028–August 15, 2029 window; its linked referral expands the selected context to two records. All six explicit clinical dates remain unknown under the current catalog.

Thirteen new timing tests and 110 focused tests pass; the final suite with question attachments passes 118 (overlapping suites). Actual HTTP tests verify exact replay/inspection, original-context preservation, snapshot-bound rejection and selection through normal dispatch planning. The browser prepares the eight-record account context; no model interpretation was dispatched. No new API charges, clinician reviews, external care actions or altered balances.

Clinical meaning, temporal sufficiency and retrieval relevance remain unvalidated. The selected window is a declared scope choice, not automatic date comprehension or a complete clinical chronology. See `artifacts/agere-evidence-timing-v1/implementation-receipt.json`, the domain receipts, test log, public-contract inspection and desktop screenshot.
