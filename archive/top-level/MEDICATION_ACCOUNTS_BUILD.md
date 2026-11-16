# Medication item reconciliation — original-cohort warehouse v1.18

The live implementation now has a reusable item-level medication account relation. It connects the reported product, historical medication list, interview, information request, original pharmacy response, received copy, original dispense, clinician-review entry and subsequent clarification contact. Exact record IDs and nested field/index paths accompany each edge. Requested-name matching associates a response with a question; it does not establish therapeutic equivalence.

## Source evidence and results

All data are synthetic. The active warehouse remains 1,614 patients and 1,049,339 received records at the fictional cutoff 2029-02-15. No generator files, private labels, frozen experiments or employer environment were changed.

Full-population verification: `artifacts/agere-medication-accounts-v1/verification.json`.

| Item | Verified count |
|---|---:|
| Patients with item accounts | 860 |
| Item accounts, including unreviewed reports and orphan review entries | 3,810 |
| Received clinician-review entries | 3,670 |
| Received reported-product entries | 3,808 |
| Resolved documentary chains | 3,288 |
| Accounts with unresolved dependencies | 522 |
| Copied pharmacy-label items traced to the original response/dispense | 61 |

Counts describe received documentation, not medication accuracy, prescribing quality or actual ingestion. The two orphan review entries and the unreviewed reports remain visible.

| Full-population documentary question | Supported | Contradicted | Unresolved |
|---|---:|---:|---:|
| A linked review entry explicitly requires regimen confirmation | 731 | 0 | 883 |
| A linked review entry explicitly reports an available dispensing label | 54 | 677 | 883 |

Both complete patient sets match an independent read/join of public source facts. Repeating either contract reuses 1,614 cached patient receipts. Neither condition establishes clinical eligibility, current directions or nonadherence. A negative label-report predicate describes the selected source account; it does not prove no dispensing occurred elsewhere.

The two-contract, deliberately full-roster engineering acquisition run contains 4,254 source-review proposals across 883 patients. This demonstrates source requirements, not an indication to request every document for every patient. No requests were sent. Exact source field paths were checked against 213,944 received literal field occurrences; repeated provenance is counted repeatedly, so this is not a distinct-record count or an accuracy score.

## Connected product behavior

The existing question graph, local example planner, requirements engine, three-valued state, immutable evidence-acquisition queue and workbench all use the same relation. The workbench's new “Medication regimen confirmation review” and “Linked medication dispensing-label reports” questions open the source chain from **Inspect requirement evidence**. The acquisition selector exposes both contracts for the chosen cohort.

The projector reuses the pinned medication-name catalog, release-notation interpretation and dose-notation normalizer. Unmatched terminology and unparseable doses remain unresolved. It does not multiply a label's tablet count by an assumed strength, infer equivalent products, determine a maximum tolerated dose, turn a completed conversation into verified directions, or create a prescription/stop order.

Source identity is per patient and includes the item-bearing records and dependency sources. Editing a received item invalidates that patient's requirement/acquisition receipts even if an older parent analysis has not been rebuilt. Old receipts remain saved and reject stale inspection. Copies retain original lineage and are not extra votes.

## Verification and limits

Adversarial checks cover missing/wrong/cross-patient links, source availability, copy modification, duplicate-item ambiguity, same-name ambiguity, literal type disagreement, cyclic request lineage, multiple medications, conflicting/undated reviews and affected-patient cache invalidation. Full question/acquisition execution and public-source parity are saved separately from these tests.

Two earlier planner-transport checks exceeded the existing 20,000-byte guard; the failure logs are retained. Equivalent instruction wording was shortened without removing operations or vocabulary. The final representative 1,500-character ASCII question request is 19,969 bytes. Non-ASCII or larger catalogs may still hit the existing guard; there is no unlimited-payload claim.

Zero new paid API calls, no new weights, no generated human reviews. Item-level semantics currently cover the explicit medication interview/review chain; broader free-text medication directions, product equivalence, indication, tolerance and clinical decisions remain further work.
