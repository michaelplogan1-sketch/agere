# Exact Claude subscription audit — November 2, 2026

Requested by Mike as part of a review of the built stack and remaining plan before sale. This is an independent model critique of supplied excerpts/evidence; the reviewer executed no tests and did not assess the whole repository, clinical truth, security certification or legal/regulatory applicability. The owner's verified dispositions and corrected sequence are in `docs/agere/PRECOMMERCIAL_STACK_AUDIT_2026-11-02.md`. Product source was unchanged throughout.

# Independent architecture audit: Agere frozen head f9a2d13 / product 74984dd

**Basis and limits.** This audit uses only the supplied excerpts and owner evidence. I executed no tools or tests and invented no results. Excerpts are partial, so "not seen" does not mean "absent". Verifier labels, Claude/Jev judgments and the owner's synthetic receipts are treated as machine evidence, never clinical truth.

## Verdict

The modular architecture is sound and should stay. The owner's accounting discipline is unusually honest: unknowns are preserved, scopes are declared, and the micro-benchmark is labeled as a microstudy. The stack is **not** a commercially releasable whole product. The plan also contains three planning errors:

1. It sequences unfinished engineering behind an external dependency (G5).
2. It leaves machine-verifier uncertainty undiagnosed.
3. It uses a loopback safeguard as if it were a deployment security model.

There is also one proven, fixable defect in per-workflow cost attribution.

## Direct answers

**1. Does unavailable independent clinical qualification block all unfinished engineering? No.**
- It blocks clinical-accuracy and model-superiority claims, G5 acceptance, trace/model promotion on qualification grounds, and customer clinical use.
- It does not block G3 engineering acceptance, cost attribution, verifier diagnosis, whole-product re-acceptance, or deployment engineering.
- The checkpoint `next_action` says to qualify roles when evidence arrives "then return to unfinished G3". That inverts the charter in AGENTS.md ("Separate engineering acceptance, model/clinical qualification and external deployment prerequisites"). `current_gate` is a *blocked* gate (G5) while G3, which has engineering work, is merely "active".
- A supported unknown is an acceptable engineering outcome. The 57 patients can remain "unverified, work refused" and still pass an engineering predicate. That predicate must be written down.

**2. Does machine source-verifier uncertainty equal missing clinical labels? No.**
- The two are different failure classes. Missing labels is an evidence-supply gap. Verifier uncertainty is observed runtime behavior with possible engineering causes.
- The owner's own interpretation line concedes the labels "alone" do not diagnose why the verifier is uncertain.
- The 66 checked packets break down as 16 supported, 32 not_established, 13 unknown and 5 contradicted. Coverage is "answers_partially" at p=0.87.
- Candidate engineering causes that no excerpt rules out:
  - Incomplete dependency: `backstop_agent.py:1677` returns unknown when `dependency_complete is False`.
  - Provider outage or quota: `:1313`, and Perplexity is exhausted.
  - Verifier abstention.
  - Extraction omission.
  - Malformed packet construction.
- None of these needs a clinical label to diagnose. Equally, a "supported" verdict or a "contradicted" verdict is not clinical truth.

**3. Do historical gates and current scoped tests establish a current whole-product release? No.**
- The owner evidence itself says `historical_full_acceptance: false` and `current_full_acceptance: false`. Historical acceptance was at 5f095dc, with 9 engineering passes under a local scope. The current product is 74984dd.
- The scorecard mechanism enforces the same principle. `workflow_scorecard.py:219` requires a matching snapshot digest and `:229-233` rejects receipts whose evaluated implementation hash changed. By the repo's own rule, old receipts do not transfer to changed code.
- The 118 tests (15.9s) are scoped. The doc cites a different "141 regressions in 17.794s", so the suite identity should be named.
- The ordinary reference (1226s, partial, warm caches) predates the scheduler change and is not a measurement on 74984dd.
- Confirm that f9a2d13 differs from 74984dd only in non-runtime files. The owner asserts `product_code_unchanged`, but I did not see the diff.

**4. Is per-workflow cost dependable under concurrent dispatch? No. Defect proven by fixture.**
- Two concurrent questions each created 1 attempt per the fixture design (2 actual). Each receipt reports 2 paid calls and a known USD, so the total is double-counted.
- The mechanism is by design:
  - `cost_telemetry.py:1-9` and `:126-131` attribute untagged rows in a window to every overlapping question.
  - `receipt()` only flags `windowed_attempts`. It still sets `usd_known` true.
  - `ledger_consistent` checks only `reported <= calls` (`:268`), so over-attribution passes.
- Context loss is plausible at pools that submit without copied context: `document_review.py:669-670` and `backstop_agent.py:1767-1774`. Contrast `typed_document_review.py:451`, which copies context. This is inferred from excerpts and needs verification.
- `tag()` (`:51-61`) swallows write errors, including SQLite lock contention. A failed tag silently becomes window attribution.
- Downstream impact: `harness_improvement.py:389-403` uses `cost_receipt.usd` for full-request regression and quarantine decisions, so over-attribution can wrongly quarantine a candidate.
- This is an attribution defect. It is not evidence that provider invoices or the shared ledger duplicate charges.

**5. Are loopback safeguards sufficient for a customer deployment? No. Product engineering is missing.**
- What exists (`service.py:166-168`, `:814-817`, `:1520`):
  - Bind to 127.0.0.1.
  - Host and Origin allowlists. These are DNS-rebinding and CSRF mitigations for a single local user.
  - Bounded JSON bodies.
- What is absent in the excerpts:
  - Authentication and per-user authority. A request with no Origin header is accepted.
  - RBAC and tenant isolation. `workspace_api.handle` defaults `can_write=True` (`:92`), and its hosted hooks (`root`, `credentials`, `on_usage`, `can_write`) are caller-supplied. They are not implemented in this service.
  - Access and audit logging. `log_message` is a no-op (`:1519`).
  - TLS, encryption at rest, retention and deletion.
  - Rate and thread limits. `ThreadingHTTPServer` is unbounded.
- Error text is echoed to clients (`:150`, `:181`), which is a plausible information-disclosure risk.
- I did not see how `Authorization` is validated in `internal_execution` or `reference_scope_packets` (`:603`, `:606`). Inspect this before drawing conclusions.
- `local_release.py:361` states its own scope: "Same-machine inspection recovery... Not clinical or production qualification". It relies on absolute external paths and the current shared ledger.

## Prioritized findings

| # | Sev | Class | Finding | Fix now / needs outside evidence |
|---|---|---|---|---|
| 1 | P0 | Proven defect | Window attribution double-counts concurrent spend (cost_telemetry.py:111-133, 255-281; harness_improvement.py:389-403) | **Now.** Propagate copied context in every pool via one helper. Treat window-attributed spend as shared/ambiguous (usd range or unknown, not a known per-question figure). Make `ledger_consistent` fail on any mismatch, not only under-count. Surface tag-write failures. Invariant test: across N concurrent questions, the sum of unique attributed hashes equals the ledger delta, and each hash is owned once or explicitly shared. Exercise every pool site. |
| 2 | P0 | Proven gap | No current whole-product acceptance at head | **Now.** Re-run `release_acceptance` at the frozen head with a fresh snapshot digest. Requirements without receipts stay `unassessed`. |
| 3 | P1 | Plan error | G3 engineering is sequenced behind blocked G5 (control JSON) | **Now.** Make G3 engineering the current gate. Record G5 as `blocked_external` with an explicit list of permitted and forbidden claims. Record the reassessment: the scheduler increment improved a sub-second component microstudy while the 1226s partial workflow was not re-measured. |
| 4 | P1 | Undiagnosed (proven unknown) | 50 of 66 packets are non-supported, with no cause breakdown (backstop_agent.py:1304-1347, 1677) | **Now, offline, from retained receipts.** Classify every non-supported packet by cause. Measure Claude/Jev disagreement as uncertainty, not accuracy. Build engineer-authored mutation fixtures (outside protected, dev3 and held-out sets) to test verifier sensitivity. Keep thresholds unchanged. |
| 5 | P1 | Plausible risk | A disabled numeric-role check may read as "no uncertainty". `numeric_unknown=any(...)` over empty judgments is False (`:1316-1318`), and the check is OFF in the runtime | **Now.** Verify, and if confirmed record an explicit `unassessed` state that blocks "confirmed", rather than silently passing. |
| 6 | P1 | Missing engineering | No authN/Z, audit log, TLS, at-rest encryption or tenancy (service.py; workspace_api.py:92) | **Partly now** (in-process). Add authentication, roles, audit log, error sanitisation and request limits. **Needs your direction:** AGENTS.md prohibits "new services", so an IdP or reverse proxy needs explicit approval. **External:** ASVS-based pen test. |
| 7 | P1 | Missing engineering | Package is same-machine, with absolute external dependencies (local_release.py:184-215, 361) | **Now.** Clean-room install drill on a second profile, including ledger restore, plus SBOM and license inventory. **External:** model and data redistribution rights. |
| 8 | P2 | Positioning risk | Active route differs from the installed catalog: ranking OFF, numeric check OFF, qualified component NULL, Kev/d1 inactive, and many proposers OFF_DEFAULT after 0 accepted answers (proposers.py:311-323) | **Now.** Describe the active route and measured roles only. Do not enable anything to improve the story. |
| 9 | P2 | Plausible risk | Typed order is Perplexity then Jev while Perplexity is quota-exhausted | **Now.** Add a recorded health-aware circuit breaker that reports degraded mode. Do not weaken any source guard. |
| 10 | P2 | Claim hygiene | Performance evidence is a six-note microstudy (n=1 pair) and a warm 1226s partial run | **Now.** Measure cold and warm latency distributions across several question families at head. Make no speedup claims until then. |
| 11 | P2 | External | USD totals are API-equivalent, with 274 unpriced attempts, not invoices | **External.** Contracted pricing and real reviewer economics. |

`typed_document_review.py:384-477` looks internally consistent on the paths I traced: ordered same-source batches, stop on first failure, original exception preserved. Its loop terminates only because a failed future always remains in `pending`. An explicit guard for `failed and not pending` would make that invariant visible and low-risk.

## Needs independently supplied evidence (cannot be fixed by the owner)

- Clinical adjudication on source-family-disjoint data, plus outcome labels.
- Security assessment against OWASP ASVS and AI assurance against NIST AI RMF.
- HIPAA risk analysis, BAAs and provider retention terms. Synthetic subscription use is not PHI authorization.
- FDA CDS applicability determination for the intended use.
- Commercial-use and redistribution licenses for models and data.
- Reviewer and compute economics.

I make no legal or regulatory classification.

## Corrected delivery sequence

1. **Freeze and define.**
   - Confirm that f9a2d13 and 74984dd are runtime-identical.
   - Write the G3 engineering predicate: deterministic verdict, every unresolved dependency named, no work created from unverified items, save/reopen/follow-up passing.
   - Record the gate resequencing and the reassessment.
2. **Cost attribution fix and invariant** (finding 1). It is small, provable and is a prerequisite for any cost claim.
3. **Verifier-uncertainty decomposition** (findings 4 and 5), offline with no providers. Then fix only the engineering causes it proves.
4. **G3 acceptance at head.** Run cold and warm ordinary runs and a non-HF transfer, with all attempts accounted. Then a second, broader composition.
5. **Whole-product re-acceptance** across the ten areas (finding 2). Unrun requirements stay `unassessed`.
6. **Deployment engineering inside the existing app** (findings 6 and 7): authentication, roles, audit, install and restore drills, SBOM.
7. **In parallel, the external evidence track.** Start it now. It is the long pole for any sale involving real data.

## Release conditions

- **A. Engineering release candidate (synthetic).** All of the following:
  - Current-head ten-area acceptance with no stale receipts.
  - Cost invariant passing under concurrency.
  - Every non-supported packet cause-classified.
  - G3 predicate met, cold and warm.
  - Clean-room install and restore drill.
  - Candidates identifiable and a known-good recovery point retained.
- **B. Diligence or demo package.** A plus:
  - Claims limited to measured synthetic engineering behavior.
  - Uncertainty, partial status and unpriced attempts shown.
  - No clinical-accuracy, model-superiority or speedup claims.
- **C. Customer deployment with real data.** B plus all external items above, plus in-product authentication, authorization, audit and encryption verified by independent review.

I have not assessed commercial value, model quality or clinical safety.
