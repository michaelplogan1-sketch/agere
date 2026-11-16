# Native source accounts into population questions and program review

Implemented October 9, 2026. All patients and records are synthetic. This builds on
`WAREHOUSE_INTERPRETATION_BUILD.md` and original-cohort warehouse v1.14. The whole
clinical/VBC interpreter is not finished.

The existing requirements engine now accepts typed predicates over native medication,
care, access, finance and goals/document accounts alongside reviewed narrative and
structured assertions. Definitions specify an account relation, literal field scope,
conditions and any/all quantifier. Both kinds of input compose through all/any/not.
There is no question-specific executor, generated SQL or paid inference for these
calculations. An adapter exposes source meaning; a contract defines the question.

Separate prescriptions, referrals, billing accounts and documents remain separate.
Same-date conflicts, undated competing statements, missing fields and unresolved
source links stay unknown. Scope filtering cannot hide a competing selected account.
An empty source set is unknown for both quantifiers. Reported use is not established
ingestion. Unconfirmed service completion is not evidence of absent care.

The question graph and workbench expose four new documentary contracts and an
affordability-to-program-review composition. Receipts retain exact fields, hashes,
source dates, identities and dependencies. Inspection verifies original and linked
records. Evidence changes invalidate affected patient results and work proposals;
unchanged patients reuse stored calculations. The UI shows unresolved fields/links.

## Verified original-cohort results

Every row assesses all **1,614** patients against public received records in v1.14,
cutoff **2028-09-30**. These are documentary conditions, not clinical accuracy, true
care gaps, eligibility or outcome improvements.

| Documentary condition | Supported | Contradicted | Unresolved |
|---|---:|---:|---:|
| Reported difficulty paying medical bills | 115 | 334 | 1,165 |
| Selected medication account reports `not_using` | 69 | 82 | 1,463 |
| Outside-service documentation with source links | 64 | 0 | 1,550 |
| Advance-care document explicitly says validity not determined | 223 | 0 | 1,391 |

All supported and unknown patient sets match an independent computation on public
source SQLite records, without generator truth or private labels. Repeating each
contract reused all **1,614** patient calculations. The affordability composition
prepared **115** assessments with source-selection receipts attached; it did not
enroll/contact patients or establish clinical eligibility.

**85** focused checks passed across the new projection, requirements, source state,
program work, planning and native interpretation. The actual browser query and
source inspection completed. No paid API calls or new model weights were used.
Lossless catalog factoring keeps the captured provider request with a **1,500-character
ASCII question** at **19,974 bytes**, under the existing 20,000-byte guard. This is
a payload check, not a measured token bill or guarantee for all Unicode questions.

## Receipts and continuation

`artifacts/agere-native-requirements-v1/` contains `verification.json`, exact patient
examples, full graph results, `work-result.json`, `regression.log`, `http-contracts.json`,
`workbench.png` and `implementation-receipt.json` with code hashes.

The active warehouse remains `artifacts/agere-healthcare-interpretation-v1/`, with
new immutable requirements/work receipts inside its stack directory. Live: port 8885.
The separate synthetic ecosystem has published v1.18 with the same roster and more
source categories. Next: pin/inventory that received release, extend diagnostic and
medication-reconciliation inputs and qualify the new build before activation.
Existing learning qualifications and forecasts do not transfer automatically.
Autonomous continuation: `AUTONOMOUS_STACK_QUEUE.md`.
