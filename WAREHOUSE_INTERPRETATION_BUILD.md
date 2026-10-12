# Native healthcare warehouse interpretation

Built against the pinned original-cohort synthetic release `healthcare-warehouse-v1.14`: 1,614 patients, 894,223 received records and 88 source categories. Output: `artifacts/agere-healthcare-interpretation-v1/`. The active configuration identifies the served snapshot.

## What this adds

The warehouse adapter now preserves separate accounts of prescribing intent, listed medications, pharmacy fulfilment, claim adjudication, administration and reported use. It also interprets referral requests and follow-up, outside-service documentation, affordability screens, billing-account snapshots, advance-care documents and code-status orders. These are reusable source relations, not new condition-specific chat templates.

Every account carries literal source values, field references, record hashes and source/arrival dates. Accounts with missing timing, conflicting same-date statements or unresolved references produce evidence-acquisition steps. Different relations are never collapsed into one vote: an order does not establish medication use; an unconfirmed appointment does not establish missing care; a bill does not establish hardship; a transcribed document does not establish legal validity.

Clinical clocks can follow explicit same-patient links from lab results to specimen collection records and from admission/discharge notifications to their own encounter records. Arrival, result, signature and source timestamps stay separate. Agreeing encounter exports preserve both references; disagreements and cross-patient, missing or late links remain unresolved. A windowed calculation retains its full received reference pool when resolving clocks.

The shared narrative map also reads `social_history.goc`, `imported_documents.course_summary`, `condition_assessments.history_text` and `laboratory_orders.ordering_reason`. Their text reaches lexical/dense retrieval and the existing bounded reader with original field paths and character offsets.

The native interpretation operator is available through the graph planner, exact local compilation, patient-state summaries and the stack workbench. It requires the full snapshot so a date filter cannot manufacture a missing dependency.

## Compute and model boundaries

Existing pinned local encoders, reranker and reader are reused. Only matching content/model-addressed computations may come from the prior model cache; patient results, forecasts and trained student qualification are rebuilt or left unattached. No weights are trained or promoted by this implementation.

The frontier planner receives losslessly factored YAML with aliases for identical values. Round-trip checks preserve literals and contracts. The provider's existing byte guard remains unchanged. Byte size is a request-size measurement, not a token-cost benchmark.

This is source interpretation and integration. It does not prove clinical accuracy, improved outcomes, superior prediction, complete care capture or performance beyond other products. Broader clinical/program reasoning, outcome variation and properly reviewed training targets remain subsequent work on this same synthetic warehouse.

## Executed source verification

Independent SQL over public received records verifies these mappings; private generator truth was not consulted.

| Check | Result | Receipt |
|---|---:|---|
| Patients with any explicit `not_using` report | 69; typed selection and native statements both match | `interpretation-verification.json` |
| Lab results linked to received specimen collection dates | 82,747 | `interpretation-verification.json` |
| Lab specimen links left unresolved | 204 | `interpretation-verification.json` |
| Linked admission / discharge / observation-start dates | 19 / 42 / 23 | `interpretation-verification.json` |
| All lab records with explicit collection dates, including direct fields | 82,849 | `stack/clinical-events.json` |
| All searchable passages | 1,657,478 | `stack/run.json` |
| New-field passages: course summary / goals / history / ordering reason | 857 / 2 / 94 / 6,847 | `interpretation-verification.json` |
| Existing bounded-reader annotations | 24,293 | `annotations/run.json` |
| Reused embedding vectors / newly encoded texts | 74,924 / 43 | `stack/run.json` |
| Paid API calls | 0 | `connection.json`, verification receipts |

The new fields contribute only **3** additional bounded-reader annotations, all in `goc`; their exact quotes were checked against raw source text. The other new fields reach retrieval but produced no labels in the existing reader vocabulary. This is a concrete remaining interpretation limitation, not evidence that the fields contain nothing useful.

The native account layer retains **4,931** undated account groups, **2,641** conflicting account groups and **70** groups with unresolved references. These are source-account groups, not patient totals, confirmed clinical contradictions or unmet-care counts. The corresponding steps ask for timing, statement reconciliation or referenced records; they do not authorize care execution.

**174 focused automated tests passed** (`regression.log`), including clock dependencies outside a selected date window, same-patient link enforcement, duplicate encounter exports, exact text offsets, unknown timing, conflicts, complete cohort membership and planner-catalog round trips. Workbench JavaScript syntax also passed. These checks verify implementation behavior, not clinical accuracy.

## End-to-end result and next build boundary

The live workbench runs this build at http://127.0.0.1:8885/stack. Use **Reconcile warehouse source meanings**. The browser and local HTTP checks verified complete selection and the native interpretation result for all 1,614 patients, with zero paid calls. Repeated acquisition steps are grouped by purpose and source relation; full accounts remain available in each patient's evidence details.

Full-population source inventory, renal-pattern and native-interpretation questions passed. Admission/source-date discharge/clinical-date discharge baseline joins produced **19 / 42 / 42** baseline-observation patients. The previous connection's clinical-date baseline had **0** resolved indices. This change removes an adapter limitation; it does not validate readmission labels, causal explanations or a forecast. See `verification.json` and the exact graph results under `verification/`.

The captured frontier request with the actual source catalog and a **1,500-character ASCII question** was **19,870 bytes**, within the unchanged **20,000-byte** guard. Longer encodings still face that guard. This check did not send a paid provider request or establish frontier planning accuracy.

Next work should consume these native relations in broader documentary requirements and VBC questions, qualify the remaining free-text interpretation against explicitly reviewed targets, and rebuild outcome/training exports from this same warehouse. The frozen Kev/d1 experiments and shadow-student manifests remain untouched. A new source contract does not qualify old model results, and this build is not a completed universal clinical interpreter.
