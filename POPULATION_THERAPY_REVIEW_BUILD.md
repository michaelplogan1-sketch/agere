# Population therapy review — October 12, 2026

G3 progress: the existing reduced-EF therapy-review protocol now connects its
complete population to declared buyer review services, owned internal work and
outcome/financial follow-up. This is a documentary engineering delivery, not a
completed treatment recommendation or a clinically qualified HF program.

## Delivered behavior

The current v1.23 cohort contains 1,614 people. Before this change, the protocol
identified 15 source-review candidates, but all 15 had empty review-topic lists
and could not proceed to service matching. The pinned protocol's explicit
therapy-review purpose now supplies a medication-review topic. This changes no
clinical threshold, interpretation, readiness rule or eligibility determination.
Undeclared protocol versions are not automatically mapped.

The saved population retains all 1,614 members: 15 nominations and 1,599 without
a supported nomination. It also accounts for 864 members with item-level
medication accounts and 750 without received accounts. The UI opens each member's
protocol findings and literal medication reports, including reported use,
directions, review dispositions, dates, exact source fields, conflicting accounts,
missing links and copy lineage. Reports do not establish a verified current
regimen, ingestion, maximum tolerated dose or missing care.

All 15 candidates reached dated patient, coverage and participation review through
the existing service path. A fictional buyer portfolio and an explicit local
review limit of three produced three owned internal handoffs and 12 waiting
candidates. The three include a candidate without medication accounts. All
1,614 members remain in outcome/financial follow-up, which includes 32,241
received accounts and the same three work items. Attributable program payment
and causal savings remain unknown. No person was contacted or care executed.

## Evidence

- `artifacts/agere-population-therapy-v1/baseline.json`: original 15 candidates,
  zero review topics, complete cohort.
- `http-v2/independent-source-audit.json`: independent received-record SQL agrees
  with the 15 nominations and medication-report coverage; 472 displayed field
  witnesses verified against exact same-patient source payloads and hashes.
- `http-v2/verification.json`: actual HTTP preparation/replay, every candidate's
  evidence and service route, bounded allocation, handoffs, idempotent execution,
  outcome follow-up and preserved prior populations. Four prior bound population
  decisions remain current; the earlier deliberately unbound history stays historical.
- `local-tests-final.txt`: 54 focused tests pass, including negative/unknown reports,
  conflicts, broken links, stale sources, protocol versions, old population paths
  and scoped invalidation. Changes outside a selected cohort do not invalidate its
  medication dependency; newly received accounts inside it do.
- `restart-verification.json`: a fresh server process retains the same population,
  three tasks, exact member evidence and current outcome follow-up. It also confirms
  equivalence after the final scoped-dependency change.
- `browser-dom.txt` and `population-browser.png`: actual browser verification of
  cohort counts, member evidence, missing-account presentation, disabled service
  review for an unsupported member, persisted handoffs and reconciliation. No
  browser console errors were observed in the final check.

Paths above are relative to `artifacts/agere-population-therapy-v1/` unless stated
otherwise. The immutable implementation receipt records final file hashes.

## Corrections, cost and limitations

The first candidate embedded every medication source chain in the population
response: 39,864,588 bytes. It was replaced before work creation with a 1,406,756
byte population response and on-demand member evidence. The original candidate
and failed verifier log are retained. That verifier compared the per-request
execution ID; the immutable decision IDs actually matched. The corrected verifier
separates request audit identity from saved-result equality. Verification resumed
from saved member evidence/routes without resending provider requests. Its prior
population check correctly distinguishes current bound decisions from historical
unbound decisions.

Observed final-run timings still expose a material performance gap: allocation
took 111.438 seconds and preparing three handoffs took 274.606 seconds. Existing
route validation repeatedly rechecks population dependencies. These are development
observations, not a controlled speed comparison or production performance claim.
The saved timing file covers the final resumed verifier; it is not a complete CPU
or human-review cost ledger. Provider requests added: zero; no paid inference was
needed to project literal structured reports. No savings or model superiority is
claimed.

All 170 protected files and 146 captured prior implementation receipts remain
unchanged. The original cohort, frozen experiments, reserved evaluations, source
families, provider ledger/failed keys and inactive payer citation candidate are
preserved. No generator, Documents research corpus or active warehouse configuration
was changed. The previous G6 recovery release remains the known-good recovery point.

## Remaining gap and next human-directed increment

The protocol's documentary readiness remains unresolved for all 1,614 members.
Native reports can now be inspected and carried into owned work, but the older
medication-use bindings do not yet consume their supported meanings. Current
regimen, indication, exceptions, clinical dates, contraindications, tolerability
and patient goals remain separately unresolved or incompletely implemented.

Next: connect received medication-use statements into reusable longitudinal state
and versioned program inputs, distinguishing explicit reports and uncertainty from
verified use. Measure missing wiring separately from missing clinical evidence,
retain all cohort members and test source changes through the same work/follow-up
path. The measured repeated-validation latency also remains a concrete engineering
gap; do not conceal it by weakening dependency checks.

This advances shared evidence/state/program/work capabilities in backlog areas
1, 2, 6 and 10, with population/VBC/OAP follow-up retained. HF remains the verified
example, not the product boundary. All ten backlog areas remain. G3 stays active;
no full-release, clinical/model qualification or production gate is promoted.
Automatic continuation remains paused, and this batch ends at a human turn boundary.
