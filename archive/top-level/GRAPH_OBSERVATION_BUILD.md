# Original-question observation-process reasoning — October 12, 2026

G3 progress: Agere can investigate whether patterns in received records could reflect reporting processes, under the same original question, full population, dated source evidence and owned follow-up. This connects an existing analytical layer to the actual product. It does not establish a calibrated joint model of clinical state and observation.

## Delivered behavior

The reusable `observation_process` graph report compares monthly arrivals, recorded source dates among records received by the cutoff, source dates of those arrivals, and exact-text sensitivity. It retains every selected member, unknown clocks, exclusions, source-system/organization strata, explicit correction pointers and competing explanations. It reuses the existing Poisson partition and constant-rate baseline. Recorded source dates are not independently verified events; source organizations are not patient attribution.

Three reusable evidence questions address feed history, record lineage and independent event ascertainment. A declared owner and actor can save an internal request under the exact original question, population, node, source version and cutoff. Requests reopen as current or historical; no external request is sent. Analytical reports do not nominate clinical services. Member evidence retains exact record IDs, hashes, dates and source boundaries.

Implementation: `graph_observation.py`, shared graph contracts/planner/executor, report adapters, population review history, service endpoints and browser controls. No question-specific answer handler. Existing source/state/reference/financial/work modules and frozen experiments remain preserved.

## Actual evidence

- Hospital-summary question: all **1,614** members retained; **55** records across **50** people and **eight** literal source organizations. **1,564** people have no arrival in the selected window; their source coverage remains unknown. **30** records arrived after their source date; median lag **one day**, range **0–5 days**.
- Arrival series, September 2028 through August 15, 2029: `0,0,0,0,0,3,7,4,18,10,11,2`. Recorded-date series: `0,0,0,0,0,5,5,4,18,10,12,1`. Independent raw SQL arithmetic matches both.
- The constant-rate baseline is **55/349 = 0.15759 received records/day**. The descriptive partition has **25.73265** penalized objective gain. This is in-sample fit on one selected stream/window, not predictive accuracy, significance, causal identification or competitive advantage. Exact-text sensitivity removes no counts in this example.
- A second free-form ACO question composes observation analysis with existing financial reconciliation and payment prerequisites, retaining all **1,614** members and exact member evidence. All **1,217** referral status updates precede its six-month window; the zero series remains an explicit capture limitation, not evidence of no referrals or completed care.
- A private consistent clone moves one record's arrival date, then adds a same-patient/source correction pointer, then moves its receipt beyond the cutoff. Each edit changes **one** member receipt and preserves **1,613** others. The delayed arrival changes the arrival series while preserving the recorded-date series. A correction pointer does not automatically replace either record; the later cutoff exclusion leaves **54** included records. Old reviews, requests and prior work remain intact. **Zero** new model calls; **9.41 seconds** for the private verification including cloning. These are invented engineering fixtures, not hidden generator truth or independent validation.
- **76 focused tests pass.** Actual HTTP questions, browser request saving/member inspection and restart replays pass. The MVP builds and links to the delivered reviews. Desktop interaction evidence is recorded; mobile/accessibility qualification is not claimed.

Evidence lives in `artifacts/agere-graph-observation-v1/`: workflow/composition/change/restart/browser verification, raw captures, tests, preservation, economics and the immutable implementation receipt.

## Economics, defects and limits

First executions took **23.45 seconds** and **28.78 seconds**, each with two frontier-model requests for bounded capability selection and typed planning. The four attributable workflow responses cost **$0.192236** at configured prices. All eight new ledger responses observed during this increment estimate **$0.374204**; the other four requests are separately listed as unattributed, not charged to this verification's claimed workflow cost. Configured estimates are not invoices. Final exact-question replay and source-change recomputation make no new product model calls. Engineering-chat tokens, local compute, maintenance and reviewer effort are separate and unmeasured; total cost and comparative efficiency remain unknown.

The initial oversized selector failed before inference. A concise, semantically equivalent new operator description brings the complete selector to **19,178 bytes** within the unchanged **20,000-byte** transport limit. The initial browser exposed missing parent follow-up history; the backend now returns that exact parent history, with a regression test. A private verifier initially required an unchanged table despite legitimate appended reviews; the corrected assertion requires all prior rows unchanged. Initial failures remain archived. The final audit's display-only status-key error is retained; the recorded release assessment itself succeeded.

Limits: one stream/window per node; 2–24 calendar months; reject streams above 100,000 loaded records or above 32 literal source groups rather than truncate. Different selected streams can compose in a graph. Unknown or post-cutoff arrivals cannot establish source visibility. An explicit replacement pointer is evidence to inspect, not verified correction authority. Competing explanations are named alternatives, not inferred posterior probabilities. No admission rate, latent health state, feed outage, provider ranking, clinical benefit, causal effect or broad generalization is established.

## Preservation and master-plan connection

All **330** baseline files (**170** protected files, **160** prior receipts), original warehouse hash, all **1,614** roster IDs and the ten-area backlog are unchanged. All prior rows in ten work tables and all **1,094** baseline provider-ledger rows remain; new reviews and follow-ups are append-only. No tasks or events were changed. Source families, frozen evaluations, generator/research corpus and the separate active design checkout remain untouched. The public design preview was inspected read-only; no publication occurred.

Research status advances from isolated source-pattern tools to connected, source-bound observation-process analysis and evidence requests. This advances native source meaning, evidence acquisition, discovery/explanation and product integration while retaining population/provider/contract/VBC/OAP scope. It does not qualify the more ambitious clinical-state/observation model.

Finite release engineering remains **6/9, failed**. Broad graph implementation hashes make older saved graph contexts historical after this change; prior evidence and work are retained. Current-code evidence-change compatibility, historical individual/criteria cases and incomplete medication integration remain explicit gaps. G3 and G5 are not promoted.

The next substantive increment should connect **population hypothesis investigation**: use existing pre-event state, explicit outcome, comparison and observation operators under one question; preserve a declared comparison family, denominator and missingness sensitivity; compare with a simple complete-case/unstratified baseline. Demonstrate a broad healthcare/adjacent population question and controlled source perturbation through the product. Report associations and alternatives without converting them to causal effects or qualified risk scores. This moves systematic science into the shared workflow, alongside retained prediction, feasible interventions, outcome learning and independent qualification work.

Build and heartbeat pause at the human-turn boundary. The canonical control checkpoint owns the exact next action.
