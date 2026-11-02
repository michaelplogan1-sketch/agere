# Precommercial stack and delivery-plan audit — November 2, 2026

**Decision: retain the modular architecture, correct the delivery sequence, and assess a frozen release through executable customer workflows. The current local synthetic candidate is not a commercially qualified whole-product release.**

Mike requested this audit because the product is intended for sale. The inspected baseline is integration head `f9a2d130e156ce3cdc5537f978524e129bfc1ce9`, product `74984dd95cf72e043e14d2a10c44601dddfcd103`. Their difference contains only the control, owner, continuation and delivery documents. Runtime code remained unchanged during this review. The October 11 principal review remains the architectural baseline, and the October 31 reconciliation remains applicable implementation guidance. All ten acceptance areas, broader population/VBC/OAP scope and standing restrictions remain intact.

This is a bounded owner technical audit plus one independent Claude subscription review, not a completed penetration test, clinical adjudication, license clearance or regulatory determination. The [exact independent critique](reviews/2026-11-02-claude-commercial-architecture-audit.md) is retained separately from the owner's verified conclusions below. No new services, real patient data, external contacts, protected evaluation keys, training or source-reading campaign were used.

## Verified findings, in delivery order

### 1. P1 — Per-question cost is ambiguous under concurrent untagged worker dispatch

**Reproduced defect.** An isolated fixture starts two questions, each producing one synthetic ledger attempt from a worker without question context. The shared ledger contains exactly two attempts. Both question receipts attribute both attempts, report two paid calls and report known USD. Each correctly exposes `windowed_attempts=2`, but that ambiguity does not make its per-question USD unknown.

The mechanism is in `clinical_core/agere/clinical/cost_telemetry.py:111–133` and `:255–279`: untagged attempts are admitted to overlapping question windows, while the receipt consistency check rejects under-attribution but accepts over-attribution. Outer worker submission in `clinical_core/agere/clinical/document_review.py:669–672` and `backstop_agent.py:1765–1774` does not copy the parent context. The later batch-level context copy cannot recreate a question identity already lost by its parent worker. Failed tagging can also lose identity because `cost_telemetry.tag` catches write failures.

This is not evidence of duplicate provider charges, duplicate shared-ledger rows or an incorrect invoice. It means the cost of a particular answer is not dependable under that concurrency condition. It can also affect learned execution cost-regression decisions in `harness_improvement._finish`.

**Next engineering increment:** bind each new dispatch to an explicit originating question before it leaves the parent context, preserve identity through actual worker paths, and retain genuinely unattributed attempts as ambiguous rather than known per-question spend. Cache reuse and deliberately shared requests need their own dispositions. Do not blindly require equality with a planner's call count: that count may exclude separately attributed review/artifact work. Do not alter or rewind old attempts, and preserve the three cache-bound source producer files until compatibility is demonstrated.

**Acceptance:** two concurrent questions each receive only their own new attempt; the union reconciles to the unique ledger delta. Retry, failed, pending, reused, intentionally shared and missing-tag cases retain every attempt without presenting uncertain ownership as known spend. Exercise the real patient/source worker boundary, not just a generic executor. All current source/financial values and reuse safeguards must remain intact.

Evidence: `artifacts/agere-commercial-readiness-audit-2026-11-02/cost-ownership-reproduction.json`. Both fixture ledgers are synthetic test records, not provider usage or billable spend.

### 2. P1 — The remaining plan unnecessarily puts unfinished engineering behind clinical qualification

**Observed plan defect.** `AGERE_BUILD_CONTROL.json` puts blocked G5 before active G3 and says to obtain independent labels before returning to clinical engineering acceptance and performance. Independent clinical labels are required for the relevant clinical claims and model promotion. They do not prevent fixing dispatch accounting, diagnosing verifier inputs, validating unknown-state behavior, checking ordinary workflow completion or improving deployment packaging.

The corrected sequence returns G3 engineering to the current gate. G5 remains blocked with its independent evidence requirements. Missing labels must not become an indefinite stop for unrelated engineering, and passing engineering must not be relabeled clinical qualification. An explicitly supported unknown can satisfy an engineering predicate when the original request, relevant evidence, unresolved dependency and safe next review are represented correctly. The current partial question cannot be restamped as a fully answered question merely to pass that predicate.

### 3. P1 — Runtime verifier uncertainty remains undiagnosed; it is distinct from missing independent clinical labels

**Observed runtime limitation.** The current retained review reaches all 66 scheduled packets, but **zero packets have a deciding provider**. Their raw argmax labels are 16 supported, 32 not-established, 13 unknown and five contradicted. All 1,497 claim-feedback entries are unresolved, with zero settled located claim faults and no failed-packet IDs.

The five contradicted argmax labels are not five confirmed clinical errors. The 16 supported argmax labels are not 16 accepted packets. Likewise, the 57 unverified patient interpretations do not imply 0% clinical accuracy. Thresholds, provider availability, input suitability, missing source dependencies and actual source interpretation must be distinguished using their own receipts.

**Acceptance:** classify each unsettled dependency by an observed reason, retaining an explicit undiagnosed category. Inspect existing complete inputs, probability distributions, service/fallback records, native scope and source bindings before changing code or choosing another reader. Use authored engineering mutations to test entity, time, source and arithmetic guards without using protected answers. Fix demonstrated construction/dispatch/composition errors; keep genuine uncertainty unresolved. Only then justify a focused new model comparison or one ordinary full workflow confirmation. Do not repeat the whole cohort because an old model vote was inconclusive.

Evidence: `artifacts/agere-commercial-readiness-audit-2026-11-02/unsettled-source-support.json`; current verdict at `artifacts/agere-source-batch-performance-2026-11-02/current-clinical-validation/verdict.json`.

### 4. P1 — Current scoped checks do not provide one current whole-product release decision

**Observed evidence gap, not a falsified historical receipt.** `CURRENT_LOCAL_MVP_ACCEPTANCE_2026-10-30.json` records nine local engineering passes at `5f095dc`, explicitly leaves full acceptance false and leaves qualification/production unassessed. The latest product is `74984dd`, with scoped source scheduling, patient report and financial checks. Older gate receipts remain useful within their actual code/source/workflow scope; they cannot establish current commercial acceptance.

The scorecard already supports source and implementation compatibility checks (`workflow_scorecard.py:207–234`). Its implementation manifest is optional, so a verifier must actually declare its compatibility scope; the existence of a receipt hash alone does not prove current applicability. This audit does not claim that existing code falsely reports full MVP completion.

**Acceptance:** use the existing ten-area contract and scorecard at the actual release code/config/source identity. Reuse historical evidence when compatibility is demonstrated; rerun only materially affected checks. Every promised current capability must be evidenced, fail with an actionable defect, or name a concrete dependency. Cover original clinical/medication cases, admissions/readmissions, care/access/social context, population/VBC/contract questions, provider/facility comparisons, an unfamiliar supported composition, missing evidence and follow-up. No evaluator-supplied plan, cohort or answer may be presented as ordinary automatic planning.

### 5. P1 before customer-data deployment — The local security boundary is not a customer security model

**Observed scope gap.** The inspected service binds to loopback, checks Host/Origin on both GET and POST, bounds JSON bodies, restricts recovery actions and has selective authorization on execution/packet routes. Those are useful safeguards. The current local handler is not demonstrated as an authenticated customer gateway that derives tenant root, permissions, credentials and usage ownership from a trusted request principal. `workspace_api.handle` exposes hooks for those inputs, but hooks and a default `can_write=True` are not proof of authenticated tenant isolation.

Request/action journals exist; `log_message` being empty does not establish that the product has no audit trail. OS/storage encryption, a hosted identity perimeter and retention controls were not exhaustively assessed. No exploitable network exposure or breach was demonstrated, and none is claimed.

**Acceptance before customer data:** select the intended deployment boundary, derive identity and permissions from authenticated users, test cross-user/workspace isolation and denied mutations, define audit access/retention, protect credentials, sanitize client errors, and verify backup/restore and operational recovery. Establish encryption/transport, incident response and provider-data handling for that deployment. Do not silently introduce a new identity service, cloud provider, real data or external contact. Existing in-process interfaces can be assessed and improved without those actions.

Use [OWASP ASVS](https://asvs.owasp.org/) as the application-security verification framework and obtain an independent assessment of the chosen deployment. This is a recommendation for the audit, not an ASVS certification claim.

### 6. P2 — Full-workflow performance, portability and component value need current evidence

The 1.581-to-0.747-second source-reader result is one six-note authored comparison with identical request sets and cost. The original ordinary clinical reference remains 1,226.386 seconds, partial and warm-cache. Provider durations overlap; they must not be added as wall time. Current financial requalification is a retained check, not a fresh ordinary benchmark. A matched current full-workflow improvement is not established.

The current run also has a different active route from the installed catalog: source ranking is off, Kev/d1 are not configured for current note reading, and the checked component registry has no newly activated model identity. Exact financial execution reuse is selected; no new trained weights or clinically qualified full-question bottle is selected. These are distinct forms of learning. Do not activate models merely to make an architecture visual look complete.

Local recovery is explicitly same-platform and depends on declared external resources (`local_release.py:184–215`). The b41 runnable installation and current fresh-process/API checks remain valid evidence within scope. They do not prove a standalone customer install of the latest candidate.

**Acceptance:** attribute current latency/cost/abstention to actual roles, compare a component against a suitable simple baseline when a named failure justifies it, and verify the complete changed path. Measure cold and warm work separately. Perform a clean installation/restore drill for the intended delivery artifact with declared dependencies, preserving all accounting. Document software/model/data licenses and commercial-use/redistribution rights. No broad training campaign or architecture rewrite follows from this finding.

## Dispositions of the independent review

The independent review agrees on retaining the modular application, fixing cost ownership, separating clinical qualification from engineering, diagnosing verifier uncertainty and producing current release evidence. Its recommendations are advice, not authority to change the charter.

- The review's P0 labels for accounting and missing current whole-product acceptance are treated here as P1 release blockers. No active commercial exposure or newly demonstrated clinical harm warrants an emergency finding from this bounded evidence.
- Its “50 non-supported packets” count overlooks that none of the 66 has a deciding provider. The owner checked the underlying dispositions and records all 66 as unsettled.
- A new Perplexity billing circuit is not required by this audit: the gateway already has one, with successful-cache access before the circuit check (`gateway.py:222–234`). Persistent degraded-mode presentation and recovery can be assessed without repeated credit probes.
- The optional numeric-role check being off is not, by itself, proof of missing quantitative verification. Native source/domain/lineage guards and final support checks already exist. Inspect their coverage before requiring another model check; preserve the independently reconciled financial values.
- Local request journals and some authorization already exist. Customer authentication/isolation remains unproven; a claim that every security or audit function is absent would exceed the inspected evidence.
- No live ledger rollback is authorized. Restore testing must use an isolated copy and preserve every failed/pending attempt.

## Audit method and release decisions

The best commercial audit is a requirements-to-evidence review that a reviewer other than the implementer can reproduce. Each finding needs an affected promise, exact code/config version, input/trace, expected behavior, observed result, severity, remedy and executable acceptance condition. A model review helps challenge the design; it does not replace independent clinical, security or contractual review.

Use six connected audit lanes:

| Lane | Evidence required |
| --- | --- |
| Product and architecture | Trace the full ten-area charter through real supported workflows; identify missing wiring separately from absent evidence. Retain the existing shared decision context. |
| Source and clinical meaning | Independently assessed source examples; entity, relation, subject, time, negation and contradictory/missing evidence; exact provenance and appropriate abstention. |
| Routing and economics | Actual component invocation, quality, latency, all-attempt cost and review burden; equal information and suitable baselines; explicit unknown ownership/pricing. |
| Learning and bottling | Source-family separation, candidate evaluation, scoped promotion, semantic regressions, adverse-feedback retirement and rollback. Execution reuse is distinct from new weights. |
| Product experience and operations | Useful end-user deliverables, original question/cohort fidelity, saved work/follow-up, changed-evidence behavior, installation, restore and recovery. |
| Customer deployment and claims | Authenticated access/isolation, privacy/security controls, provider contracts, model/data rights, intended-use review and independent assurance. |

Separate three decisions: the synthetic engineering candidate, qualification of specific clinical/model claims, and authorization/readiness for customer deployment. Passing one does not grant the others. The canonical control file remains the one delivery sequence.

The next sequence is: **correct cost ownership → diagnose retained verifier uncertainty → fix demonstrated source/consumer defects → verify the changed ordinary workflows and broader transfer → assess the current ten-area release → complete deployment-specific controls and independent assurance.** Clinical/model evaluation and customer/legal/security evidence progress as distinct tracks; missing labels cannot stop unrelated engineering. No current unverified trace or model is promoted to avoid those requirements.

For the later customer-data assessment, [HHS cloud guidance](https://www.hhs.gov/hipaa/for-professionals/special-topics/health-information-technology/cloud-computing/index.html) explains applicable business-associate arrangements and risk analysis for ePHI processing. The [FDA CDS guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/clinical-decision-support-software) informs an intended-use-specific assessment; this audit makes no device classification. [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework) provides a voluntary AI assurance framework. Actual applicability, provider agreements and review evidence must be established for the product being offered.

## Evidence and what changed in this turn

The owner inspected critical entry, source-verification, learning, telemetry, scorecard and recovery code; confirmed runtime equality of the product/documentation commits; reproduced the concurrency accounting defect in isolated fixtures; and ran **118 targeted tests in 15.914 seconds**. These are the cost telemetry, request lineage, harness improvement, workflow scorecard, source disposition and runnable-release suites. The earlier 141-test scheduling suite is a different suite, not an inconsistent test count.

One independent subscription review returned in 118.604 seconds with product code unchanged. Original review output, request, provider usage, fixture attempts and test logs are retained under `artifacts/agere-commercial-readiness-audit-2026-11-02/`. The fixture-only ledger records are not provider calls. No product defect was fixed or product gate accepted during the audit. The owner corrects the canonical next action to implement the reproduced accounting repair and resume connected engineering, while preserving G5's independent qualification requirements.
