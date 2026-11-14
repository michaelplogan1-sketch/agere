# UI requests for the stack owner

## CKD product and mock agents: requested, not delivered

Observed on 2026-11-14 against main `860662fe`, UI branch
`codex/ui-2026-11-14`, local preview on port 8918. This file records missing
stack capabilities; it does not implement or certify them.

### Existing evidence

- Original context: `ANS-f5014b619007a7a4e1e96ca8`.
- The user requested a virtual CKD nephrology program for CKD stage 3+
  patients, excluding dialysis and patients with a nephrologist, plus an
  interactive command center and mock agents for enrollment, check-ins,
  laboratory follow-through, medication pickup and nephrology follow-up.
- Saved report `DLV-0976435B2099` is titled **Evidence and next steps**.
  Its workflow label is **Virtual CKD Nephrology Program Command Center**,
  `status: ready`, `generation: saved_report`. The live gallery preview is
  a historical evidence report with patient browsing and downloads, without
  program controls. Its recorded 308 patients are a historical analytical
  result, not confirmed current enrollment eligibility.
- Custom page `DLV-69128DA3423D` is `rejected`. Its retained generation
  receipt reports **Prompt is too long**; no interactive artifact was saved.
- The earlier read-only diagnosis is at
  `artifacts/agere-ckd-command-center-diagnosis-2026-11-11/diagnosis.json`
  in Claude's workspace. It records zero work items, requests and events.
- Fresh `GET /api/stack/action-context` on port 8918 on 2026-11-14
  confirms this context remains `invalidated: true`, has zero returned work
  items and zero simulations, and still has only the report-only workflow.
- Current `clinical_core/agere/deliverables/synthetic_actions.py` has
  pharmacist, remote-monitoring and generic scripted journeys. It does not
  implement the requested CKD longitudinal journey.

### Required response and capability support

1. Preserve the original question and conversation ID through the analytical
   answer, requested deliverables and program work. Questions continue to use
   `POST /api/stack/compose` with `{question, conversation_id}`.
2. Return fulfillment separately for each requested output. A saved report
   must not satisfy a requested operational product or agent capability.
   Include explicit missing capabilities and recorded failure messages.
3. Return the actual deliverable type, creation status, canonical open URL,
   evidence binding and capability status. A runnable standalone product
   needs a functioning product route; the existing frozen report preview
   cannot perform program actions.
4. Implement the synthetic CKD program on the stack side, bound to a current
   source, cohort and date. Do not activate work from the invalidated saved
   context. Preserve explicit empty cohorts, exclusions and unknowns.
5. Provide bounded mock-agent runs for outreach, patient response/consent,
   enrollment, recurring check-ins, laboratory follow-through, medication
   pickup and virtual nephrology appointments. Return persisted run IDs,
   patient work states, next actions, exceptions, messages and event history.
   These are simulated interactions; no external dispatch or real care.
6. Return which operations are supported and their server-side action
   contracts. The screen must not fabricate agent activity, use a different
   answering path, call providers or databases, calculate figures, or turn a
   generated static page into an ungoverned write-back client.
7. Return display-ready program measures and their definitions, sources,
   limitations and freshness. Keep analytical completion, artifact creation
   and agent execution as separate facts. Continue returning tier, elapsed
   time, cost and sources, with a visible unchecked status for takeover.

### Acceptance for the missing user behavior

- The original CKD request produces either the requested operational product
  and supported simulated workflows, or an explicit incomplete result naming
  what is missing. A report alone cannot be marked as full completion.
- Open the standalone product from the gallery, inspect its source-bound
  synthetic cohort, launch a bounded mock run, inspect simulated patient
  responses and subsequent tasks, and reopen the same persisted history.
- Demonstrate successful enrollment, decline, no response, missing laboratory
  completion, medication pickup follow-up and a missed virtual appointment.
  Show the stack's returned outcomes and messages without inventing results.
- Repeated clicks do not duplicate work. Pausing prevents further steps.
  Historical/invalidated answers remain inspectable with new work disabled.

The screens can improve the answer hierarchy and make these states and
artifacts discoverable. They cannot supply the missing program execution.

## Exact additions needed to connect the new screens

The UI now exposes `/ask?app=ckd&context=<ANS-id>#program` as a dedicated
population/program experience. It reads the existing action-context API. It
does **not** call a nonexistent agent endpoint, use generic pharmacy/RPM
simulations as CKD agents, invent activity, or declare a report operational.

### 1. Restore saved conversations and expose execution metadata

`GET /api/stack/action-context?context=<ANS-id>` must return the original
`conversation_id` (or `conversation.id`) and the retained conversation history.
Current saved contexts do not return that ID. The UI therefore disables
follow-up submission for these saved answers rather than generate a new ID
and falsely imply the saved cohort/conversation was retained. Fresh questions
and their live follow-ups use exactly `{question, conversation_id}` and keep
the same ID. Do not require a UI-side patient list or extra question path.

Also retain `tiered_core.final_tier`, `tiered_core.seconds`, `cost_receipt`,
`cost_telemetry` and explicit model-cost scope in saved responses. Currently
the saved planner receipt retains the tier and model estimate, but elapsed
time survives only inside `answer.facts.answered_by`. The UI displays that
original string; it must not recover numerical fields by parsing prose.
Provide display-ready time and cost labels if a particular numeric format is
required. Keep tier model estimates and all-attempt accounting separate.
Include the pinned source identity and snapshot in the compose response,
as well as named sources/evidence references. A query alone is not a complete
source inventory; the UI must not infer one from the warehouse-wide inventory.

### 2. Return a CKD program contract through the existing saved context

Proposed addition, absent today:

```json
{
  "program": {
    "schema": "mock-care-program/1",
    "id": "<persistent program ID>",
    "kind": "ckd_virtual_nephrology",
    "context_id": "<exact current ANS-id>",
    "conversation_id": "<original conversation ID>",
    "synthetic": true,
    "source": {"source_digest": "<bound version>", "as_of": "<bound date>"},
    "status": "draft",
    "revision": 1,
    "cohort": {"patient_ids": [], "selection_status": "review_required"},
    "metrics": [],
    "capabilities": [],
    "patients": [],
    "runs": [],
    "activity": [],
    "limitations": []
  }
}
```

These empty collections illustrate structure, not actual program counts or
success. Each metric needs an explicit `key`, `label`, `display_value`, unit,
definition, source and observation window. Each capability needs an ID,
`available`/`unavailable` status, a reason and a server-owned action contract.
Absent capabilities remain unavailable; returning generic `programs.custom`
does not authorize a CKD run.

Each patient program record must bind `patient_id`, consent/enrollment state,
responsible owner, current tasks, due dates, unresolved evidence and the next
supported action. Enrollment, clinical appropriateness, payer eligibility and
observed source selection must remain separate states.

### 3. Implement bounded mock runs and return their real history

Required capabilities: `outreach`, `consent_enrollment`, `check_in`,
`lab_follow_through`, `medication_pickup` and `nephrology_follow_up`.
Combine lab/medication displays if helpful, but preserve the separate tasks.

The stack owner must add authenticated/guarded actions for creating, advancing,
pausing and resuming a **synthetic** run. Return action URLs and accepted
payloads in the capability contract; no new URLs are called by this UI today.
Every mutation must bind program ID, current revision, exact context/source,
selected patients, scenario and an idempotency key. Return persisted state
and the updated revision; reject duplicate/conflicting/stale requests without
silently broadening their population. A pause must stop subsequent steps.

Run responses must include run ID, state, per-patient dispositions, requested
and completed steps, pending next action, limits and events. Each event needs
an ID, timestamp, patient/run reference, actor, event type, outcome and
supporting record/task references. Simulated patient messages must be labelled
as simulated and record their scenario/inputs. Generate no real dispatch.

Required scenarios: reached/consented, patient declines, no response,
missing records, lab not completed, medication not collected and missed
nephrology appointment. Persist the conversation and exception/handoff history
so opening the workspace later reproduces the same state. Do not use a
timer-driven UI to manufacture progress or infer a successful outcome.

### 4. Return display semantics and charts

Use `answer.table_columns` / `column_metadata` to supply readable labels,
units, code meanings and explicit boolean labels. In particular the CKD
`coded_stage3plus`, `referral_only` and `qualifying_basis` fields need
source-defined display meanings. A missing value must stay unknown; 0 is
not automatically a negative clinical fact.

Return presentation-ready chart series, labels, units, domains and tooltip
values for comparisons, trends and program measures. The redesign shows
returned numeric cells directly and does not invoke legacy visual routines
that calculate additional figures in the browser. All specialized analytical
values must remain accessible in the returned evidence/data. Future charts
must project server-supplied values without computing deltas, rates, savings,
eligibility, treatment interpretations or program completion in the screen.

### 5. Handle stack failure as a response

During preview setup a count question reached the bounded-failure branch
after the tiered semantic database's relative default path was unavailable.
The server raised `NameError: name 'QuestionNotUnderstood' is not defined`
in `clinical/conversation.py:_bounded_only`, closing HTTP without a response.
Claude must repair the exception import/handling in the answering lane and
return the stack's failure message as JSON. The UI cannot supply a stack
message when the server closes the socket. No alternate answering path was
added. Configuring `AGERE_TIERED_DB` to the existing absolute semantic path
allowed the later count question to answer normally.

An additional live conversation failure needs attention: after “How many ED
visits were there in 2029?” and “And in 2028?”, the question “Which of those
two years was worse for the emergency department, and by how much? Explain
what worse means here.” was rejected with:

> This analysis has no single patient cohort. Choose the population you want
> to follow from its saved counts; no whole-warehouse or empty cohort was substituted.

Here “those two years” refers to periods, not patients. The screen displayed
the exact message and made no automatic retry. The later explicit-date
comparison was a separate manually submitted question through the same
compose endpoint, not a fallback path.

## UI lane implementation checkpoint

Gate: G7 presentation increment only. Existing source/control/owner files and
answering modules remain outside this branch's edits.

Changed behavior: result-first answer displays; visible tier, returned time
and model estimate; persistent unchecked takeover mark; direct original error
messages; dedicated CKD overview, population/dossiers, requested pathway,
agent availability and evidence views; home/gallery entry points.

Missing behavior: operational mock CKD agents, new program measurements,
refresh of the invalidated population, and saved-conversation resumption need
the stack contracts above. The CKD workspace is a new UI over a historical
population, not a completed program service.

## Verification of this UI candidate — 2026-11-14

All 17 checks in `test_answer_presentation`, `test_connected_site` and
`test_creation_gallery` pass. They include source-value preservation,
unknown/zero distinctions, takeover status overriding a checked flag, one
conversation ID across turns, exact two-field question bodies, original
failure messages without retry, context/profile isolation and unavailable
agent controls. `git diff --check` passes.

Actual Chrome observations on port 8918:

| Input / interaction | Observed result |
| --- | --- |
| How many ED visits were there in 2029? | 1501; deterministic recipe, 0.02 s tier time, model estimate $0, code checks passed. |
| And in 2028? | 1465; deterministic recipe, 0.01 s tier time, model estimate $0; the returned finding explicitly says the period was swapped into the preceding question. |
| Contextual “those two years” comparison above | Exact scope failure displayed; no retry. |
| List patients with CKD. | 410 returned patient rows; frontier recipe, 5.97 s tier time; unchecked. |
| How many of those patients have diabetes? | 290; deterministic recipe, 0.01 s tier time; returned finding says every patient table was restricted to the preceding 410 selected patients. |
| How many ED visits were there in 2029, and how many inpatient admissions were there in 2029? | Separate sections with 1501 and 533; deterministic recipe, 2.87 s tier time. |
| Explicit 2028/2029 ED comparison with limitations | New answer used frontier recipe, 7.14 s tier time, unchecked. It did not reach takeover. |
| Reopened real takeover context `ANS-5ffa2564ec7d839e3ff5e74d` | Frontier takeover and Unchecked remain visible across Summary and Data. Original 19.4-second execution string is preserved; raw returned 1501/1465/36 values are displayed without recalculation. This is a retained response, not a fresh takeover measurement. |

The current answering path does not expose an interface to force a tier;
none was added for the browser checks. No clinical accuracy or superiority
claim follows from these screen checks.

CKD screen: opened the original historical population; inspected an individual
patient and exact unknown/0/1 values; searched a patient by name; filtered by
the returned selection-basis value; opened the program pathway, unavailable
mock-agent view and evidence. The agent-run button is disabled and no program
mutation endpoint is called. Home, navigation and gallery expose the dedicated
program URL while retaining the separate saved report.

The 390-pixel Chrome layout and the 1474-pixel desktop layout have no
page-wide horizontal overflow. Narrow patient tables scroll within their own
labelled region. The synthetic/historical state remains visible. A generic
navigation delegate that accidentally matched `body[data-page]` and stripped
the program URL's context was corrected; clicks now retain the complete URL.

The original-row CSV downloaded successfully. Its 308 patient IDs and order,
every source column and all scalar values match the original returned table.
CSV nulls are empty fields; the original JSON retains null. The browser
download-event wait timed out despite the completed file; that tool failure
is not a product-export failure or a provider run.

Screenshots are local review outputs, outside the repository:
`/private/tmp/agere-ui-ckd-desktop.jpg`,
`/private/tmp/agere-ui-ckd-mobile.jpg`, and
`/private/tmp/agere-ui-answer-desktop.jpg`.

The continuity check in this isolated clone reports a missing ignored build
receipt at `artifacts/agere-source-obligations-2026-11-11/current-integrated-recovery-delivery-receipt.json`.
No control file or artifact was changed to satisfy it. Mike's explicit UI-lane
authorization applies; this candidate does not seal a stack gate.
