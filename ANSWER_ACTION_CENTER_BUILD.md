# Answer to action center — October 19, 2026

The main `/ask` answer now opens a saved action center. It preserves the exact
computed cohort, returned evidence, question and source snapshot, then lets the
user select patients, prepare PharmD/RPM/custom review tasks, assign an owner,
record progress, and export work. Reopening a conversation retains the link.

Custom interactive deliverables use the existing frontier composer with the saved
answer and work bundle. That route cannot fetch a replacement cohort, and generated
HTML runs separately from task controls. Model composition is an explicit API-usage
button; opening the action center or managing review tasks requires no model call.

The existing work backbone supplies durable tasks, revision checks and append-only
events. Duplicate creation preserves progress; out-of-cohort patients, incomplete
answers, absent roster members, stale source snapshots and conflicting edits are
rejected. Program choices are reviews, not eligibility decisions or enrollment.

The screenshot's narrow beta-blocker metric is now rejected for an explicit full
GDMT, reduced-EF and dose request. Requests for per-patient details retain a table
shape. This guard does not establish a correct end-to-end answer to that full query.

## Verification and deployment

- 148 focused tests passed, including existing conversation, answer-contract,
  shape, compiler, deliverable and UI coverage. JavaScript syntax and diff checks pass.
- Actual browser: “List patients with heart failure” returned 329 synthetic
  patients. Opened the action center from that answer, created review tasks for
  P-1001 and P-1007, assigned an owner, and saved P-1001 as blocked pending evidence.
- Restarted :8885 from committed source `8ff383fdea4964884c94863801af8f34b4820cc8`.
  Health passed; browser/API showed the same 329-patient cohort, two tasks and one
  blocked task after restart. Source context is current. Warehouse overlay retained.
- Live worktree: `/Users/mikel/agere-mvp-run`, PID 20524, session 30869.
  Log: `/private/tmp/agere-action-live-20261019.log`. Recovery commit: `d3e84ab`.
- No paid provider calls for this workflow/check. Frontier integration used a
  stubbed model response in tests; actual paid generated-view quality is untested.
- Evidence: `artifacts/agere-answer-action-v1/implementation-receipt.json`,
  `tests.log`, `health.json`, `action-context.json`, and `action-center.jpg`.

## Remaining work

G3 remains active; G5 remains blocked. Complete the original EF/GDMT/dose/provider
assessment with per-patient evidence and explicit unknowns, then use that result
in this workflow. Actual agent outreach, program enrollment, RPM fulfillment and
clinical outcomes are not connected by this increment. No benchmark was rerun;
this is finite local engineering evidence, not clinical or model qualification.
The full ten-area charter, specialist coverage and separate design work are retained.
