# Population questions connected to buyer review and internal work

G4 implementation, October 11, 2026. The existing Programs page can now save an
original population question, typed cohort/program specification, received-snapshot
date and buyer. The existing question decision store retains every selected member,
nonmatched and unresolved cohort membership, versioned program assessments, buyer
service nominations and payer prerequisites. No new product module or store was
introduced.

The saved decision opens in `/stack?decision=…`. A nominated member's exact program
assessment supplies documentary review topics to the existing patient-context and
service-matching path. The user can queue owned internal work, prepare an internal
handoff and reopen it with the original question and full cohort identity intact.
Source, program or buyer changes invalidate the dependent decision. The caller no
longer has to inject need tags for these documentary program reviews.

## Actual execution

Both compositions use the same operators, decision path and fictional buyer:

| Program | Original roster / selected | Source-review nominations | Need unresolved without nomination | Internal handoff verified |
|---|---:|---:|---:|---|
| Respiratory | 1,614 / 1,614 | 100 | 1,514 | P-1004 |
| Depression/anxiety | 1,614 / 1,614 | 24 | 1,590 | P-2002 |

The exact nomination sets and roster independently reconcile to read-only SQL on
public received warehouse views, using the pinned program's selector definitions.
This verifies documentary accounting, not independent clinical relevance. Runtime
evidence contains no evaluation labels or hidden generator truth. The active
original-cohort v1.23 snapshot is unchanged: 1,238,686 records, received cutoff
2029-08-15. Historical program assessment is explicitly rejected.

The development buyer declares four fictional slots for each service. These are
displayed beside the candidate counts, with overlap and unresolved payer/consent
prerequisites. They are neither reserved slots nor actual clinical capacity. One
member per program traversed actual HTTP matching, idempotent queueing, internal
handoff and reopen. Both original decisions and work reopened after a fresh server
restart. The browser form submitted the respiratory question and displayed its
saved roster, nominations, services and existing owned handoff.

## Verification and measured correction

- 45 focused tests passed across population decisions, individual decisions and
  changed-source review. They cover membership unknowns, buyer/source staleness,
  wrong-member/wrong-match rejection, zero capacity, idempotence and work binding.
  The small unit workflow uses explicit context stubs; the two full-roster HTTP
  workflows use actual existing context, matching, participation and work engines.
- Python and JavaScript syntax checks passed. Actual HTTP, browser and restart
  evidence is in `artifacts/agere-population-decision-v1/`.
- The initial local attempt exposed redundant full-cohort revalidation and a form
  initialization race. Both were corrected and the interrupted attempt retained.
  A verifier-only capability-order comparison was also corrected and retained.
- Exact current-request replay now reuses the immutable decision after checking
  dependencies. Observed replay fell from about 26–30 seconds to 0.154–0.962 seconds
  in this local run. This is an observation, not a general performance benchmark.
- All 170 protected files and 204 prior sealed evidence files remain unchanged.
  No provider calls, provider spend, external dispatch or clinical actions occurred.

## Acceptance limits and remaining work

G4 remains active. Four of six `complete_population_program` engineering checks
have dedicated passing evidence. Buyer/payer and owned-work checks remain blocked
at full-population scope: aggregate declared services and two member handoffs do
not establish a disposition, patient-specific prerequisites or follow-through for
every nominated member. The dedicated partial acceptance receipt is checked with
the existing scorecard contract; it is not a claim that the main release is accepted.

The typed program and cohort define execution. The original question is retained,
but arbitrary natural-language clinical intent is not compiled into a new program.
Source-review nominations are not clinical indications, negative care findings,
benefit eligibility or qualified recommendations. Mapping uses the explicit
documentary baseline and remains clinically unqualified. Clinical need, goals,
consent, benefits and actual service participation require separate evidence.

Next close full-population member routing and accountable disposition within this
same saved question, including unmatched/blocked members, capacity constraints and
internal work status. Then connect existing outcome/accountability and versioned
contract/financial operators with independent received-amount reconciliation.
G3 remains blocked at 5/6 pending the specific medication-transfer approval and
actual changed-source reinterpretation. G5 model/method qualification and G6
packaging remain open. All ten product backlog areas are preserved; no full
release, clinical validation, superiority or production readiness is claimed.

The sole exact next action remains in `AGERE_BUILD_CONTROL.json`.
