# Care request and received-report references — October 12, 2026

One manual G3 increment connects native care requests, status reports, reported bookings and documented performed services through the existing assertion, interpretation, source-state and pre-event graph. G3 remains active. Automatic continuation remains paused.

The original question, explicit population, source scope and event window remain in the same saved graph. The planner receives the actual observed request vocabulary and reusable contracts; there is no nephrology-specific handler. Exact same-patient, correctly typed links connect a completion statement to its request and received imported report. The report must name that same request. No source scope is silently expanded.

Bookings, unconfirmed service flags and completed conversations are distinct from documented performed services. A limited dental evaluation does not establish completion of every requested treatment. Each request stays separate, including requests whose service meaning is unresolved. The latest patient-level documentary state and the request inventory answer different questions; neither establishes current clinical care or episode closure. Report categories can overlap because one source can express both a status and a booking.

Source, receipt and explicit service dates remain separate. A linked assertion's usable arrival is the latest receipt among its required references; every original clock and exact field/hash remains inspectable. The pre-event operation excludes future dependencies and retains same-day or missing-clock uncertainty. Current narrative reviews are still not qualified historical knowledge.

## Delivered evidence

- Three actual free-form workflows: P-1001 care follow-through plus transport difficulty; the entire population's care accounts plus affordability; care evidence before the first admission notification in an explicit window. The final graphs use the existing source_state and pre_event_state operations.
- **1,614 patients and 2,523 requests retained.** Of the requests, **110 have documented performed-service evidence**, **2,400 have no mapped completion evidence**, and **13 have unresolved service meaning**. Separately, there are **299 reported bookings**. The 2,400 are not established care gaps or nonattendance.
- The 110 service reports concern 106 patients. Latest patient-state counts are 105 documented affirmations, 1,496 unobserved and 13 unresolved; one patient with service evidence also has an unresolved request. Patient-state and request denominators must not be conflated.
- P-1001 retains four requests: transport assistance, dental care access, vision assessment and audiology. One has a documented limited service; three lack mapped completion evidence. Two bookings remain reported bookings.
- Before admission notifications from 2026-01-01 through 2029-06-30, received by 2029-06-30: 24 patients have an index; 1,590 remain without one. Ten prior request accounts remain, including one documented performed service. Thirty-six later assertions are excluded. Missing prior reports do not establish missing care.
- **166 tests pass.** The independent audit checks every request denominator and booking count, explicit request/report joins, raw event selection and all **15,735 unique source field references**. Adversarial cases cover wrong patients/types, mismatched requests, missing sources, later receipts, same-day timing, unfamiliar literal service types, limited service scope and false confirmation.
- Browser entry, actual patient answer, four-request drill-down and exact evidence inspection pass with no console errors. The result clearly exposes incomplete evidence. Pixel/layout acceptance is not claimed; earlier screenshot capture returned blank pixels.
- Exact HTTP replay and process restart preserve the final result IDs and make zero new paid calls.

The first live attempt caught a local catalog-directory error before any provider call. It was corrected by loading care vocabulary through the existing graph catalog. All captures are retained. Initial population results also exposed the 13 unmapped service types; final accounting retains those requests instead of dropping them. The approval system initially mistook synthetic workflow checks for real-patient egress; verification of the synthetic source and outbound question/schema payload cleared it. The user subsequently reaffirmed that all data is synthetic. No approval remains blocked.

## Latency and cost

First requests took 21.25, 53.12 and 73.50 seconds with two hosted planning responses each. These were partially warm development runs; the latter two initially overlapped and are not isolated latency trials. Final execution reused all planning responses: 2.71 seconds for the patient, 31.93 seconds for the population rebuild, and 3.26 seconds for the prior-event result with shared interpretation reuse. Exact HTTP replays took 0.71–1.02 seconds; restart replay took 2.45 seconds.

The actual final population graph was also run in a disposable copy with empty derived caches: **26.24 seconds cold versus 0.52 seconds cached**, with identical counts, memberships, coverage and care-account/report totals. This excludes planning, startup, model loading, clone preparation and OS-cache control. Fully cold end-to-end performance remains unqualified.

All six product-provider responses total a configured estimate of **$0.268880**, not invoice verified. Engineering-chat tokens, local hardware and human review are separate and unmeasured. No additional model was used to interpret literal care links. This is not an optimal-cost or competitive-superiority claim.

## Master-plan connection and remaining work

This increment advances native source meaning, evidence dependencies, longitudinal composition and the actual question interface (areas 1, 2, 4, 6, 7 and 10). All ten acceptance areas and the earlier research-to-code reconciliation remain preserved. Scientific applicability, broad program support, measures and attribution, prediction, discovery, financial/provider comparisons and G5 model qualification remain required work. G5's eight-of-24 unsupported labels remain unresolved. No model, clinical, causal or competitive qualification was gained here.

**Next connected G3 increment:** bind these shared graph findings and request-specific evidence gaps to the existing question-bound internal review and follow-up flow. The current question_decision source binding requires source_answer findings; shared graph source-state results cannot yet enter that flow directly. Reuse the existing decision/work/acquisition contracts and store, preserving the original question, population/window, exact request/report versions and unresolved dependencies. Demonstrate a patient review and population routing/reopen/reassessment without creating an unrelated clinical program or treating source absence as need. The present delivery creates no new work item or real care action.

The current design reference is the scroll-driven website in **Find Agere site inspiration**:
https://agere-inside-intelligence.michael-p-loga331119.chatgpt.site/#top
Its whole-population, evidence-to-meaning-to-work framing guides the local product. The public site and its separate concurrent checkout were inspected and left intact. The delivered interaction is available in the local workspace at http://127.0.0.1:8885/stack. The illustrative pharmacy/RPM story remains distinct from implemented capabilities.

All 170 protected files and 152 prior receipts remain byte-identical. The original warehouse, roster, full ten-area backlog and captured work histories match their baselines. Frozen experiments, separate generator and Documents research were untouched. Citation recovery stays inactive. The earlier G6 package remains the recovery point; no new portable release or public publication was created.

Evidence: `artifacts/agere-care-reference-state-v1/implementation-receipt.json`, `source-audit.json`, `verification.json`, `tests.txt`, `cold-derived-cache-benchmark.json`, `product-provider-accounting.json`, `browser-verification.json`, `website-preview.json` and `preservation-final.json`.
