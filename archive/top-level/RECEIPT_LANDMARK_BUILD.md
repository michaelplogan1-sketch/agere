# Receipt-time decision windows — October 9, 2026

Agere now evaluates an explicit decision point after an index record first arrives. This is a new registered task, separate from the unchanged index-day prediction experiment. Definitions were frozen before inspecting the new target distribution in `artifacts/agere-receipt-landmarks-v1/predeclared-task.json`.

`decision_landmarks.py` and `landmark_contracts.json` connect the source ledger, baseline features, question graph, local compiler, frontier operator catalog, API and workbench. Both questions evaluate the entire selected population. Patient receipts preserve exact index records, clock dependencies, feature cutoffs, endpoint exclusions and source hashes; a source change invalidates the affected patient's receipt. Reports cannot be coerced into clinical membership or action queues.

The decision starts the calendar day after the earliest unambiguous index receipt. Its clinical date and every supporting clinical-clock dependency must already be known by that receipt. The subsequent 30 calendar days use explicit clinical endpoint dates. An old event reported late, or an endpoint already received before the decision, is excluded. Prior features use received/source-dated records strictly before the decision; the index record itself and future endpoints are excluded from predictors. Current-snapshot corrections still do not provide complete historical reconstruction.

## Actual original-cohort results

Source: `artifacts/agere-receipt-landmarks-v1/verification.json`, the two dataset manifests and protected learning receipts. The active v1.18 warehouse remains 1,614 synthetic patients; no source migration occurred.

| Received trigger → future observation | Admission → discharge | Discharge → admission |
|---|---:|---:|
| All patients evaluated | 1,614 | 1,614 |
| No received index in the window | 1,595 | 1,563 |
| Index clinical clock unavailable at receipt | 13 | 12 |
| Mature recorded zero with baseline inputs | 6 | 38 |
| Incomplete follow-up | 0 | 1 |
| Historical endpoints excluded | 6 | 14 |
| Independent baseline source references checked | 270 | 3,188 |
| Eligible inside protected development partition | 5 | 23 |
| Development train / calibration / validation | 5 / 0 / 0 | 17 / 3 / 3 |
| Model status | Not fitted | Not fitted |

All usable targets are recorded zeros, meaning no matching received future event under the contract. They do not establish absence of actual care. The existing protected comparison machinery applies original-family, household, template and frozen-cohort exclusions, then refuses fitting because both classes are not sufficiently represented. No forecast weights, clinical risks, human labels, treatment actions or new paid calls were created. Three older index-day learning attachments still validate unchanged.

`build_receipt_landmark_stack.py` produces separate X/y files, manifest hashes, protected qualification and guarded model-build receipts. `verify_receipt_landmark_stack.py` independently checks patient joins, raw-source hashes and dates, clock availability, endpoint exclusions and partition separation. Runtime never consumes private generator truth or fitting partitions as clinical evidence.

## Verification and limits

75 focused checks pass across landmarks, baselines, graph composition, economical routing and catalog scope. Actual local questions and HTTP endpoints process the full cohort. Live browser verification caught and fixed a result-renderer variable initialization error; the updated page renders all counts and opens a patient's exact receipt. See `http-verification.json`, `browser-verification.json`, `verification.json` and `regression.txt` in the artifact directory. These checks establish engineering behavior, not clinical validity or model superiority.

Workbench: `http://127.0.0.1:8885/stack`. New buttons: “Future discharge after an admission record arrives” and “Future admission after a discharge record arrives”. APIs: `/api/stack/landmark-contracts` and `/api/stack/decision-landmarks?id=<receipt>`.

Prediction remains limited by available received endpoint variation and reference quality. Do not retune windows against these results or repeat fitting on the same single-class targets. Continue independent financial and program-accountability capabilities.
