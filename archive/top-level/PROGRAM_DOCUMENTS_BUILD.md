# Quoted specifications connected to evidence, work and model context

Delivered October 9 local / October 10 UTC, 2026. All patients, source records and
the two demonstration specifications are synthetic. This is an engineering build,
not clinical or payer-rule validation.

The existing `Program` / `compile_document` interpreter now consumes the shared
versioned requirement and protocol layer through `program_documents.py`. It does
not require a new condition-specific executor or foundation-model retraining.
The original legacy registry and native analytical code identity remain unchanged.

## What is connected

- Immutable source document, quoted spans, document/rule versions, effective
  period, received date, declared purpose and program/measure role. The package
  hash covers the mappings and document as well as the predicate. Changing any
  of them under an existing version is rejected.
- Typed requirement/protocol input pins, with exact same-patient source receipts.
  An unavailable input has a declared type and reason; it is not filled from a
  convenient but nonequivalent coverage, financial or demographic field.
- Append-only user-reported mapping reviews with revision checks. A correction
  invalidates prior selection/work context. These events are not independently
  verified clinical approvals. Review changes were tested only in isolated
  fixtures; no live human review was manufactured.
- Complete-population graph filtering and immutable review-work assessments.
  The downstream review program still applies its own criteria. Selection does
  not establish eligibility, enroll a patient or activate a rule.
- A version-impact operator showing every patient's before/after status,
  changed inputs and exact receipts on one population and snapshot.
- Bounded source/model context carrying exact selected specifications, quotes,
  unresolved mappings and latest review state. Existing byte limits, source-hash
  checks, native-token checks and incomplete-context dispatch guards remain.

Live workspace: http://127.0.0.1:8885/program-documents. The main `/stack` question
path and `/programs` assessment inspector also expose the attached rule evidence.
The interface supports JSON intake, source inspection, complete-cohort evaluation,
version comparison, review preparation and bounded patient-source context.
The HTTP intake is limited to 32,000 UTF-8 bytes; the direct Python source compiler
has a separate 60,000-character document bound. No automatic CMS/payer crawling or
document-to-rule extraction was added.

## Original-cohort execution

The active received warehouse remains v1.18: **1,614 synthetic patients**,
**1,049,339 received records**, **113 source categories**, cutoff **2029-02-15**.
No source migration, generator edits or private truth access occurred.

| Synthetic engineering specification | Supported | Contradicted | Unresolved |
|---|---:|---:|---:|
| v1: received affordability-report criterion | 115 | 334 | 1,165 |
| v2: same criterion plus explicitly unbound attribution | 0 | 334 | 1,280 |

These are results of quoted example predicates, not validated clinical need or
actual program eligibility. Version 2 makes **115** previously supported
documentary selections unresolved; **334** remain contradicted and **1,165**
remain unresolved. This illustrates a missing dependency, not worsened patient
care. The unavailable attribution input remains visible even when another false
criterion determines the conjunction.

The new adapter's full patient sets agree with the prior independent public
received-source query, whose SHA and identical source snapshot were checked.
The new audit checked **3,228** patient-rule receipts, **4,842** exact quote spans
and **2,694** received source fields (the same relevant fields checked under both
versions). All **115** selected review assessments retain quoted-rule context.
The live comparison independently checks **1,614** patient transitions and
**3,228** before/after receipt hashes. No live work items were created.

For **P-2004**, a bounded context with the v2 specification and the explicitly
selected `financial_screens` stream contains **one source unit** and uses **5,209
UTF-8 bytes** within a **10,000-byte** ceiling. Its exact quote, source record,
unbound attribution and unreviewed mapping status remain present. A **2,048-byte**
budget blocks dispatch instead of discarding the specification. These byte counts
are not provider token counts; no native provider tokenizer or model quality was
measured in this check.

## Evidence and limits

Evidence directory: `artifacts/agere-program-documents-v1/`.

- `verification.json`: full-cohort sets, source fields and work-selection audit.
- `http-verification.json`: actual intake/question/work/impact API execution and
  all patient transition/receipt checks; replay hits recorded separately.
- `context-verification.json`: current-patient context API, exact rule retention,
  budget rejection and source checks before dispatch.
- `browser-verification.json`: actual DOM interactions, patient field inspection,
  version transitions, work preparation and bounded-context display; no browser
  errors observed. Pixel screenshot verification is not claimed.
- `integration-checks.json`: native analytical identity unchanged; two quoted
  packages, zero live mapping reviews, zero work items/events.
- Focused suites: **114** initial checks, **29** comparison integration checks,
  **39** graph/catalog checks, **33** context checks. Suites overlap and import
  some fixtures; these are not counts of unique tests or clinical accuracy.
- `planner-wire-context/verification.json`: offline provider-wire simulation
  using the actual catalog; **19,894 / 16,487 bytes**, under the **20,000-byte**
  request guard. It does not test hosted model understanding. Additional catalog
  growth still needs general paging; the index has little remaining headroom.

All new production evaluation routes used **zero paid model calls**. No new model
weights, model winner, human labels, outcome improvement or vendor superiority
were established. A deliberately isolated review test initially expected a work
exception class rather than the existing selection guard's `ValueError`; the
expectation was corrected and all focused checks passed.

Live bound inputs currently provide boolean requirement/protocol values; raw
numeric/string source bindings remain explicit unsupported inputs. Role labels
such as denominator, numerator, exclusion, attribution and payment prerequisite
are preserved, but they do not yet compose a certified measure or settlement
engine. Exact quotation is provenance, not proof of semantic equivalence or
completeness. The examples are not CMS, commercial or Medicaid specifications.

Next: connect unresolved imported-rule inputs to conditional evidence acquisition
and source-to-rule correction, then compose explicit measure denominator,
numerator, exclusion and attribution requirements on the complete population.
Reuse existing tri-valued evaluation and work machinery. Broader qualified
narrative interpretation, useful prediction targets, contract meaning, catalog
paging and full product acceptance remain unfinished. Frozen Kev/d1 experiments,
reserved cohorts, source-family protections and failed promotion receipts remain
unchanged. Current owned server session: **94687**.
