# Information value attached to review work

Information-value scenarios now attach directly to an existing evidence-review question. Users enter named states, review choices, prior probabilities, observation likelihoods, availability, acquisition costs and a shared utility unit. The existing finite Bayesian calculator compares the no-information baseline with each acquisition alternative and exposes hypothetical observation/posterior branches. Scenarios retain exact question, patient, received-source, review and linked work dependencies; changed dependencies mark history stale. No probability is learned from the chart and no acquisition or clinical action is dispatched.

This connects established finite expected-value-of-sample-information mathematics through the actual work path; it adds no model weights, learned probabilities or novel mathematical claim. The unchanged calculator is `clinical_core/information_value.py`; the new adapter is `clinical_core/agere/clinical/work_information.py`.

## Executable path

Open an existing evidence requirement and choose **Compare the value of gathering information**. Name possible states, review choices, information alternatives and observations; enter the probability, utility and reliability tables with their provenance. Preview and save an immutable scenario. Existing reference applicability, service requirement and original evidence-need targets use the same typed target resolver. Task revisions are bound when the target has an explicit task identity; a generic plan requirement does not invent a unique task.

No-information expected utility, each acquisition's gross/net value and conditional branches are displayed. The selected channel is the highest positive value among options explicitly assumed available, not a request to gather information. Exact ties follow the unchanged calculator's alphabetical rule; unavailable channels remain visible. Changing form inputs invalidates the pending preview. No clinical disposition or original task state changes.

## Actual verification

79 focused checks pass, including 16 new tests: analytical noisy/perfect/uninformative cases, zero-probability branches, unavailable channels, same-source channel duplication without adding values, immutable replay, tampered-history rejection, invalid assumptions, source/review/task/reference changes, source acquisition and generic service requirements. JavaScript syntax and Python compilation pass. Earlier in this run, functional options and components passed 147 checks including 17 new tests; these suites overlap and are not an aggregate unique-test count.

The actual browser entered and saved a two-state/two-choice/two-channel hypothetical scenario for the existing functional assessment question, then reloaded it and opened its hypothetical branches. Browser errors: zero. HTTP replay and invalid-input/preview rejection pass. The source-backed question, task and work history remain unchanged.

Saved scenario `79b6926a417874269c7e485cffc0d8fd8cd36d304a1baa19053b1de0f761efa5` uses baseline utility0.5, an assumed informative check with gross value0.3 and cost0.1 (net0.2), and an assumed uninformative repeat with gross0 and cost0.05 (net−0.05). Units are **hypothetical review usefulness units**. These numbers were entered as engineering assumptions, not derived from this patient's records or measured service performance.

All 111 earlier protected files match; the new manifest protects113, including this adapter and the unchanged calculator. Original work histories (380 items/383 events; 10 historical capability tasks/31 events), respiratory/behavioral/functional reviews, earlier capacity/financial/payment scenarios and 19 Documents files remain preserved.

Zero provider/token-count calls; uncapped ledger remains812 validated/10 failed/$6.292875524 historical estimated API cost. Local compute and review labor remain unpriced. Server10954, session77048, port8885. No verification jobs remain. Checkpoint 2026-10-11T06:00:02.789271+00:00.

## Remaining boundaries

- All probabilities, utilities, reliability, availability and costs are supplied assumptions; no calibrated clinical probability, actual service availability, benefit or measured savings is established
- One question and one acquisition alternative are evaluated at a time; named joint states are possible, but shared cross-question decision value, joint acquisitions and sequential adaptive policies remain absent
- Correlated acquisition values are never added; no independence assumption is generated from marginal inputs
- Numerical computation uses the unchanged floating-point research calculator and validation tolerances; display rounds to eight significant digits while stored values remain unchanged
- The explicit editor supports up to eight states, choices, channels and observations; constructing defensible priors and utility models requires separate evidence and review
- Source hashes bind the current received patient snapshot, not hidden clinical state; reconstruction and replay checks do not validate assumptions
- Separate stores use optimistic checks rather than a global transaction; provider calls, patient contact and external actions remain zero

Next: Integrate shared acquisition value across selected existing review questions. Reuse evidence_portfolio.py for exact shared request identities and work_information.py / information_value.py for the explicit-assumption boundary. Require a supplied joint-state/observation model and common utility objective before assigning decision value to a shared request or bundle; compare with the no-information and single-request baselines, count each shared acquisition cost once, and never infer independence or add separate channel values. Bind exact question/source/work versions and demonstrate the actual question/work UI. Preserve the existing structural portfolio metric as a separate baseline. Then continue qualified prediction/discovery, broad clinical/payer interpretation, empirical operational inputs and portable acceptance; no repeated single-class fitting, source migration or pending outbound-payload retry.

The sole-owner five-minute continuation remains active. Full product acceptance is false; reviewed clinical validity, qualified prediction, real payer/service integration, production portability and other documented gaps remain. Three prior exact outbound payload approvals remain pending and untouched.

![Saved information-value scenario](artifacts/agere-work-information-v1/browser-scenario.png)
