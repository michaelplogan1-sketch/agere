# Versioned review protocols into persistent program work

Delivered October 9, 2026 against the active original-cohort v1.18 synthetic warehouse, following `PROGRAM_ACCOUNTABILITY_BUILD.md`.

Program work now consumes any registered care-review protocol through `protocol:<protocol_id>`, instead of requiring another disease-specific workflow adapter. The existing HF and affordability workflows remain available as distinct definitions. New registered protocols appear in the program workspace and local question examples automatically; the frontier planner receives the same namespaced program contract and existing protocol vocabulary.

## Implemented path

`registered protocol + complete selected population → exact current protocol receipts → immutable patient assessments → existing owned work lifecycle`

`protocol_programs.py` adapts the existing protocol evaluations without writing new clinical analyses or fabricating clinician review. Each assessment retains the exact protocol version/hash, source receipt/hash, supported/unresolved/nonmatching status, documentary readiness, missing inputs and separate unimplemented clinical requirements. Candidate assessments additionally carry the existing source record links and proposed review actions. A candidate can be a source-conflict review; it is not automatically a supported clinical criterion.

Both the natural-language question path and `/api/stack/work/prepare` use the adapter. The existing save/update, ownership, review, consent, monitoring and history code is reused. Changed source interpretation or a changed program definition blocks a stale proposal or event; preparing fresh evidence retains prior history and flags reassessment. Lifecycle transitions were checked in isolated synthetic fixtures; no live program work items or staff reviews were fabricated.

The program workspace now lists registered protocol programs and displays assessment status counts. Its clinical-prerequisite control opens the program's own protocol receipt instead of incorrectly falling back to an affordability panel. The optional financial/contract evidence attachment also works for protocol-backed programs.

## Full-cohort results

Every row below assessed all 1,614 synthetic patients. The same counts were verified through the question and program-workspace APIs.

| Existing protocol | Review candidates | Supported criterion | Criterion not supported | Insufficient evidence | Conflicting evidence |
|---|---:|---:|---:|---:|---:|
| Reduced-EF history and therapy review | 14 | 14 | 0 | 1,600 | 0 |
| Low-eGFR evidence and specialist follow-through | 1 | 0 | 683 | 930 | 1 |
| Affordability report to assistance review | 0 | 0 | 0 | 1,614 | 0 |

Source: `artifacts/agere-protocol-programs-v1/verification.json` and `http-verification.json`. Each protocol's 1,614 immutable assessment references was checked against its exact current receipt. Candidate evidence links: 76 for reduced-EF review, 22 for the kidney conflict review, and zero for the current affordability protocol. These are source-link counts, not independent evidence items or accuracy measurements.

**The affordability result exposes a real binding limitation.** That existing protocol uses legacy `notes`/`financial` documentary source accounts for `financial hardship / reported_barrier`. It does not yet consume the newer `native_affordability_report` requirement over native source accounts. All-unresolved is not evidence that the patients have no financial need. The separate existing affordability workflow and native requirement queries remain available. This build preserves the old protocol definition rather than changing clinical/source meaning to produce more candidates.

Likewise, the kidney candidate is a conflicting-evidence review, not a verified referral indication. The current definitions remain limited review criteria; other clinical indications, acute/chronic context, timing, medication use, preferences and eligibility remain separate.

## Verification

- 98 focused checks passed, including dynamic registration, full-population state retention, exact source links, source-review changes, definition changes, immutable work history, existing financial accountability and graph behavior. Some imported fixture cases repeat across suites; do not add milestone counts as unique cases.
- All three real full-cohort local questions passed, with exact graph replay and zero paid calls.
- All three live API question/workspace counts agree. A kidney protocol proposal with financial accountability attaches evidence to all 1,614 assessments without changing its one source-conflict candidate.
- The browser displayed all registered programs, full kidney status counts, and 14,620 attached financial/contract review requests. No requests were sent or work items materialized. Captured JavaScript errors were empty. Pixel screenshots were not verified.
- The offline provider-wire simulation still fits the existing bound (18,660-byte selection, 16,031-byte selected plan); this is not evidence of live planner semantic quality.
- No clinical analyses, source warehouse, generator, frozen experiments, foundation weights, human reviews or current predictor qualification were changed.

## Next concrete build

Allow protocol bindings to consume registered documentary requirements, including their native source-account predicates, as explicit typed inputs to trigger/readiness logic. Today those requirements can be attached as supporting context but cannot directly supply a protocol trigger. Use `native_affordability_report` as the first application under a new, explicit definition version. Preserve exact requirement receipts, missingness/conflicts and current data lineage; do not equate a received difficulty-paying report with verified hardship, benefits eligibility or need for a particular intervention. Keep the current all-unresolved version and its result as historical evidence.

Then continue versioned external program/measure intake, source-to-rule review and downstream impact. Clinical/financial eligibility and settlement, broad qualified interpretation, usable current-source prediction and full product acceptance remain unfinished.

## Reproduce

- `.venv/bin/python build_protocol_program_stack.py --output <fresh-directory>`
- `.venv/bin/python verify_protocol_program_http.py --output <artifact-directory>`
- `.venv/bin/python -m unittest test_protocol_programs test_program_work test_program_accountability test_analysis_graph test_protocol_source_context test_care_protocols -q`
- Evidence and implementation manifest: `artifacts/agere-protocol-programs-v1/`.
