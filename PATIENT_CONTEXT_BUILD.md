# Received patient/support context in buyer work — October 10/11, 2026

Agere can now inspect exact dated patient, support and access reports from a saved answer or assessment, explicitly bind them to a buyer-service match and carry them into the internal handoff. The projection covers eight received source categories, including support interviews, social screens, functional reports, care-management contacts and responses to specific referrals/orders. Source identity, date, receipt date, respondent, false/null values and literal differences remain visible.

The available reports do **not** establish current patient goals, current service preferences, general consent, helper authority or service eligibility. These remain explicit questions. Virtual, in-person, home and hybrid services expose appropriate practical-access questions. A response to one referral is never treated as a standing service preference; a historical helper-contact permission is never authorization to contact someone now. Manual preference text remains separate from received evidence. No option is automatically promoted, denied or assigned a clinical benefit score from these reports.

## Integration and actual result

The existing question/answer → assessment → buyer matching → internal queue → evidence packet path supports the new binding. Exact report and plan hashes are retained in immutable sidecar receipts. Changed source records block reuse. Service refresh preserves the selected context; it does not substitute newer records. Combined work state includes the patient context, and the existing credential-scoped local packet checker rejects an absent or stale attachment. If a multi-database handoff attachment fails after the core event, combined work state remains incomplete; retry repairs the same event idempotently. This is fail-closed recovery, not a claim of a single transaction spanning all stores.

Actual synthetic patient **SYN-69283717-971** has seven projected reports from five categories. They now accompany six explicit review questions in task **693081077ff08e07f21bdb3203a57511d9b0e45b754c5612a50b4674809de1a4**, under the clearly labeled integration buyer `synthetic-patient-access-review`. This is a local agent evidence-review capability, not a newly verified customer service. The six prior buyer tasks and all program work/history remain unchanged. No clinician review, consent, patient contact, referral, appointment or treatment was performed.

## Evidence

- **91 focused checks pass**, including **17 patient-context tests**, plus **17 source-availability/runtime checks** including six runtime-cache tests. Tests cover dates, null/false, declared fields, bounded complete display, identity, source changes, missing reports, literal differences, referral scope, received coverage composition, stale packet rejection and partial-write recovery.
- Actual HTTP matching, exact replay, queueing, internal packet preparation and combined work state pass. Browser verification covers source inspection, explicit binding, service-specific questions and the saved packet attachment. Source fields were checked directly against received warehouse records.
- Full-roster audit: **1,614 patients**, **1,591 with projected reports**, **4,062 reports**, **18,765 exact values** and **56 sets of differing literal reports**. No hidden generator truth, evaluation label or clinical accuracy score is used.
- The earlier **80 protected files**, eight paid answers, six prior buyer tasks and 19 separate Documents research files verify unchanged. New protected set: **82 files**. Active warehouse remains v1.23. No provider/token-count calls were added; the provider ledger remains unchanged and uncapped.
- One added fixture initially paired a commercial coverage report with a Medicare-only route; the existing route correctly rejected queueing. The test now uses its intended compatible reported payer, without weakening routing. The initial failure is retained in the artifacts.

## Measured runtime improvement

Profiling exposed repeated compilation and scanning of literal record-ID regular expressions during existing source-answer verification. The additive `literal_reference_runtime.py` caches bounded compiled patterns and uses exact token rejection only for the recognized word/hyphen literal pattern; all other expressions use ordinary regex behavior. Every source/context/answer freshness check still executes. Tests cover Unicode, boundaries, overlapping IDs, regex metacharacters, flags, concurrency, eviction, changed text and exact request-body equivalence. All paid answers still pass their original identity checks.

For this one actual work item, a profiled combined-state check fell from **42.362 s → 8.583 s**. Actual final HTTP checks took **4.732 s** and **4.776 s**, with responses exactly equal to the prior saved state. These are local case measurements, not production-scale or clinical performance claims. Earlier compilation-only measurements are retained. Some repeated dependency reconstruction remains and latency can improve further.

## Remaining work

The six questions are preserved review context, not completed patient interviews or individually resolved acquisition tasks. Broader source categories and documented goals need careful integration. Current clinical goals, preferences, consent and execution authority still require appropriate confirmation. The additive patient-context and retirement guards are installed by the service; standalone tools calling frozen engines must install the same guards. Production identity/tenant controls, external care adapters, validated clinical meaning and useful calibrated prediction remain incomplete.

Primary receipt: `artifacts/agere-patient-context-v1/implementation-receipt.json`. Morning status: `AGERE_OVERNIGHT_READINESS_2026-10-11.md`.
