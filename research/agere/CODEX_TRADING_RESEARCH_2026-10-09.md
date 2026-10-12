# Trading systems, financial quants, chart analysis, and think tanks

October 9, 2026. Extension to [A new engineering lens](NEW-LENS.md). These are research candidates and proposed experiments, not installed dependencies or validated Agere capabilities.

**The strongest transfers are reliable information arrival, faithful replay, explicit execution states, disciplined signal testing, and decision-making under uncertainty.** These strengthen the population-health, PharmD-program and outcome-aligned-payment core. Ultra-low latency is useful only where measured workload and decision timing justify it.

## What these fields add

| Source discipline | Concrete Agere addition | Healthcare question it could answer |
|---|---|---|
| Exchange market-data infrastructure | Source sequence-gap detection, snapshot/update reconciliation, recovery state and coverage accounting | Did this patient's medication evidence change, or did part of a feed fail to arrive? |
| Trading execution protocols | Explicit command, acknowledgement, acceptance, cancellation, completion and correction states | Was outreach requested, actually delivered, answered, accepted, and followed by completed care? |
| Quantitative backtesting | Reconstruct inputs available at each historical decision; retain all tested model variants; separate discovery and confirmation | Would this insight have worked then, without later information or choosing the best-looking failed experiment? |
| Objective chart analysis | Prespecified trajectory features tested against simple comparators | Does a recurrent trajectory add useful predictive information beyond the latest measurement, trend and observation frequency? |
| Statistical process control | Sequential change detection with explicit baseline and observation process | Is a program's completion rate changing, or did patient mix, coding or source availability change? |
| RAND-style robust decision-making | Discover scenarios where a plan fails and define observable adaptation triggers | When does the current staffing or program plan stop working, and what should change then? |
| NBER-style empirical economics | Study selection, incentives and causal identification before attributing outcomes to a program | Are better results due to the program, patient selection, changing attribution, or another policy? |
| Complexity research | Stress-test feedback between patients, providers, capacity and payment incentives | Could a locally reasonable policy overload another part of the care network? |

The three technical memos supply the primary references, required data, implementation boundaries and failure tests: [HFT systems](cross-hft-systems.md), [quantitative signals](cross-quant-signals.md), and [think-tank research](cross-thinktanks.md).

## Three particularly useful additions to the product concept

### A. Separate signal quality from execution quality

A correct opportunity can still fail to produce care. Build a measurable chain from supported opportunity through clinical review, outreach, consent, scheduling, appropriate intervention, monitoring and outcome ascertainment. Record why each transition did or did not occur.

For example, a PharmD program can have strong case finding but poor contact delivery, or successful enrollment but insufficient monitoring access. These call for different improvements. Patient refusal, a changed clinical assessment, a valid exception and an operational failure are different outcomes; completion is not an objective that overrides patient choice or appropriate care.

The relevant trading lesson is that a decision, an accepted command and a completed execution are distinct. Transfer that discipline into the existing program-work system. The detailed HFT memo examines execution-message and cancellation races; this is more specific than simply adding a dashboard funnel.

### B. Make new information update the right decisions

An independent new echo, a corrected medication report, a changed guideline and a duplicate copy of an old note should have different computational consequences. Use source identity and dependencies to decide what requires new evaluation, while retaining required time-based monitoring and expiration checks.

Borrow snapshot/recovery conventions from market-data systems: do not present a quietly incomplete update stream as a complete current state. Where a healthcare source supplies no trustworthy sequence numbers, use batch manifests, reconciliation and explicit uncertainty instead of inventing completeness. This extends existing event-time and provenance work with source-recovery contracts.

### C. Build an honest laboratory for program strategies

Replay a proposed policy using only the evidence available at the historical decision, with realistic delivery delays, workload and capacity. Keep rule/model versions and all candidate trials. A simulator can establish operational feasibility under its assumptions; it cannot reveal an unobserved patient's outcome under a treatment they did not receive.

For clinical and economic effects, pair replay with an appropriate study design and supported outcome data. For uncertain future operating conditions, test scenarios and identify vulnerabilities. This brings the discipline of trading research to healthcare without confusing a simulated result with a causal clinical finding.

## How to approach stock-chart reading

There is a serious quantitative question beneath subjective chart patterns: can a formally defined trajectory predict something useful beyond a simple baseline? Lo, Mamaysky and Wang operationalized technical patterns and tested changes in conditional return distributions. Their reported incremental information in historical stock data is not evidence that a named stock pattern predicts clinical deterioration or that it is reliably profitable out of sample. [Original MIT/NBER research](https://www.nber.org/papers/w7613)

For Agere, candidate patterns might involve a sustained departure from a patient's observed baseline, repeated interruptions in a care pathway, or changes in program throughput. Clinical variables are sampled irregularly, often precisely because something is wrong. Copy-forward text, assay changes and documentation delays also produce apparent shapes.

Compare any proposed pattern detector with the latest value, a slope, threshold rules and a model that includes observation frequency. Freeze definitions before testing on later patients/sites. Keep clinical risk, operational bottlenecks and source failures as different targets. A chart image or indicator alone should not determine medication changes.

## Software worth inspecting, with a specific job

| Software | Useful engineering idea | Adoption decision |
|---|---|---|
| [LMAX Disruptor](https://github.com/LMAX-Exchange/disruptor) | Efficient in-process event transfer and consumer coordination | Benchmark only if fan-out or contention is a measured bottleneck. This is not durable source storage. Repository identifies Apache-2.0. |
| [Aeron](https://github.com/aeron-io/aeron) | Transport plus recording/replay infrastructure | Consider when reliable distributed event transport/replay becomes necessary. Start with current infrastructure and measured recovery tests. Repository identifies Apache-2.0. |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | An event-driven architecture connecting historical simulation and live execution | Primarily an architecture reference; financial instruments and order semantics are not clinical pathways. Current repository identifies LGPL-3.0. |
| [HdrHistogram](https://github.com/HdrHistogram/HdrHistogram) | Measuring latency distributions across a wide range, including coordinated-omission considerations | A small performance-measurement candidate if the deployment language fits. Repository documents CC0 and a BSD-2-Clause alternative. |

These repositories were inspected as public references, not installed or benchmarked. Pin versions and review actual integration/dependency terms before adoption. A fast trading library is not automatically a good fit for a local Python healthcare stack.

An additional design principle is an independent check between a proposed action and its execution: current evidence version, valid authority, permitted action, duplicate/conflicting work and required prerequisites. Financial market-access controls illustrate this separation, including rejecting duplicate or erroneous requests. The SEC source describes financial controls; it does not impose healthcare requirements or supply clinical rules. [SEC market-access control explanation](https://www.sec.gov/files/faq-15c-5-risk-management-controls-bd.htm)

## Experiments that determine whether these ideas belong

1. **Break the feeds deliberately.** Introduce missing updates, resets, reconnects, reordered batches and late corrections. Compare current freshness checks with source-recovery contracts. The result should identify scope that is incomplete and recover without silent duplication or omission.
2. **Replay the complete action lifecycle.** Exercise retries, late acknowledgements, cancellation races, duplicate delivery and partial completion. Compare current work statuses with explicit execution transitions and reconciliation. Historical replay must not send real messages or execute clinical actions.
3. **Run a quant-style selection audit.** Record all pattern/model variants, preprocessing choices and thresholds. Evaluate in forward time with appropriate handling of overlapping outcomes, patient/copy lineage, delayed labels and held-out sites. Compare to simple models at equal information and review budgets.
4. **Stress the operating plan.** Vary staffing, contact success, lab access, drug access, claims delay and contract assumptions. Identify compact descriptions of failure conditions and test whether proposed adaptation triggers arrive early enough to be useful. Scenario frequencies are not probabilities unless justified.

Keep source completeness and execution-state correctness in the core engineering plan. Treat trajectory signals, broader simulator models and complex trading infrastructure as experiments. Evaluate gains in completed appropriate care, fewer false opportunities, trustworthy commercial measures, review burden and total cost. Faster generation of misleading insights is not an improvement.
