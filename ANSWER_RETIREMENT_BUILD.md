# Explicit work-answer retirement — October 10/11, 2026

An attached machine answer can now be explicitly retired or replaced from a saved work item. The operator previews the exact removal, preserved attachments, verified replacements and affected buyer tasks, then records a revision-bound local operational explanation. Ordinary refresh still cannot silently discard answers. Original answer objects, assessments and work events remain immutable.

The change creates a fresh assessment, increments the work revision, requires reassessment and reopens terminal work for review. A transactional database exception is limited to that exact before/after assessment and revision. Old proposals cannot reattach a retired answer. Unrelated machine answers, selection/protocol context and financial/accountability context must still verify and are preserved. Explicit restoration of a previously retired answer is not yet supported.

The service guards buyer origins derived from affected current or prior work assessments. Existing task history is retained, but plans/handoff preparation based on retired context fail current verification. Original answer origins outside this work scope remain available. This guard is installed at service bootstrap; standalone tools directly calling frozen buyer engines must install the same guard. It is not a tenant security or authenticated clinical review system. Actor labels retain the existing self-reported local operational contract; no staff identity, consent, clinical judgment or execution authority is invented.

The workbench supports direct program/item links and an accessible preview form. Editing the form invalidates its preview. GET `/api/stack/work/answers`, POST `/api/stack/work/answers/prepare` and POST `/api/stack/work/answers/apply` use the current synthetic warehouse.

## Verification

- **91 focused tests pass**, including 20 new retirement tests. They cover all three work engines, concurrent changes, exact idempotency, rollback, old-proposal rejection, immutable history, retained optional contexts, staleness before/after preview, terminal reopening and buyer handoff invalidation.
- A real HTTP service against a disposable warehouse passes the successful replacement lifecycle and dependent-handoff rejection. Saved answer verification is an explicit fixture double in that test; no model quality claim follows.
- Actual warehouse HTTP inspection and preview pass. The functional case has a current answer and one dependent buyer task. False stale-dependency claims and revision mismatch reject correctly. **No live answer was retired**: all existing live work/events and buyer tasks/events remained identical.
- Browser preview and edit invalidation pass; direct linking opens the selected patient from a 256-item worklist. The screenshot is `artifacts/agere-answer-retirement-v1/retirement-preview.png`. One cold preview exceeded the browser's short locator wait, then appeared successfully; no console error was recorded.
- All earlier **79 protected files**, eight paid answers, six buyer tasks and 19 separate Documents research files verify unchanged. The new module extends the protected set to **80**. Active warehouse/config remain unchanged; provider ledger unchanged and uncapped. Zero new provider calls, clinical reviews or external actions.

Initial verification mistakes were corrected: a fixture used an unsupported review disposition and the first live-check script named a nonexistent buyer-event table. Neither changed live work. Final logs and receipts identify the corrected passing runs.

## Remaining boundaries

This resolves the explicit work-context removal path, not global answer retraction, clinical correction adjudication or patient communication. Existing source needs, standalone answers and research objects are not silently deleted or semantically resolved. Broader clinical meaning, patient goals, actionable member benefits, useful prediction, independent clinical qualification and production integrations remain incomplete. See the overnight report and the full product acceptance charter.
