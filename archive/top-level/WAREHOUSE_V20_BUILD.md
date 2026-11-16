# Agere on synthetic warehouse v2.0 (candidate, not active)

October 13, 2026. All patients and records are synthetic. The live build stays `artifacts/agere-healthcare-v123`;
`config/agere/active_warehouse.json` is unchanged.

## Build

- Release: `/Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v2.0` (manifest `healthcare-warehouse/2`,
  sha256 `d370026c…`, cutoff 2030-05-27). Same 1,614 original patient IDs (digest `87afb631…`, identical to v1.23).
- Build: `artifacts/agere-healthcare-v20`, as_of = release cutoff 2030-05-27 (the pipeline takes as_of from the manifest).
  1,739,068 records, 223 source categories, all searchable; 4,100,861 passages; 204,651 dense texts; 66,910 annotation
  bindings; 149,431 note facts. 36 GB on disk (stack.sqlite3 16 GB, semantic 5.4 GB, vectors 4.5 GB, warehouse 4.9 GB,
  longitudinal 3.8 GB).
- Candidate configuration: `config/agere/warehouse_v20.json` (passes `serve_current_warehouse.py --check`).
- Commands (run with `.venv/bin/python`):
  `build_healthcare_warehouse_stack.py --release …/healthcare-warehouse-v2.0 --output artifacts/agere-healthcare-v20 --stage all`
  (stages can be resumed one at a time: `--stage readers`, `--stage finish`; a crashed reader run needs its partial
  `annotations/` moved aside), then `semantic_layer.build(stack)`.
- Compute: ingestion + clinical 6 min, retrieval (lexical + embeddings, v1.23 model cache reused) 79 min, readers 4 min,
  finish 11 min, semantic layer + note facts 14 min. No paid calls, no new weights.

## Adapter

`clinical_core/agere/healthcare_warehouse_v2.py` (`original-cohort-warehouse-adapter/2`) accepts both manifest schemas,
checks that the roster is preserved, reads only `patients`, `source_records`, `organizations`, `clinicians` and
`data_dictionary`, and records demographics, code-system coverage and the dictionary digest in `ingestion.json`.
Records go through the unchanged adapter/1 projector, so record payloads and every downstream record contract are
identical. The build script picks adapter/1 for `single-healthcare-warehouse/1` manifests, so v1.23 rebuilds keep their
release identity (adapter/1 file is byte-identical).

## Semantic layer (semantic-layer/13)

Source standard codes sit beside existing columns; every existing column keeps its meaning except `claim.final_status`
(see below). Provenance columns say which source was used.

| Area | Columns / tables |
|---|---|
| patient | birth_date, race, ethnicity, preferred_language, interpreter_needed, marital_status, zip, county (FIPS), state |
| condition | icd10 (source), icd10_source |
| measurement | loinc (source first, dictionary fallback), loinc_source, loinc_dictionary, reference_range, abnormal_flag; source LOINC fills a NULL concept when the dictionary maps that code to exactly one concept |
| medication | rxnorm (source product RxCUI, else dictionary single-ingredient CUI), rxnorm_source, rxnorm_ingredient, ndc |
| visit / encounter_diagnosis | principal_icd10, place_of_service; encounter_diagnosis(rank, icd10, type) |
| claim / claim_line | drg, drg_grouper_version, principal_diagnosis, diagnosis_codes, billing_npi, rendering_npi, type_of_bill, revenue_code, cpt, hcpcs, local_charge_code; claim_line(cpt, hcpcs, local_charge_code, revenue_code, units, charge_cents) |
| immunization, clinician, organization | cvx/mvx; npi, taxonomy_code, tin |

`claim.final_status` now follows the adjudication chain (reversal paired with the payment it reverses; final = last
unreversed adjudication by event time). Arrival-order status stays in `received_final_status`; `live_payer_paid_cents`
added. v1.23: 223 claims reversed→paid plus 1 denied→paid; v2.0: 355 reversed→paid plus 1 denied→paid.
`net_payer_paid_cents` matches the live-payment sum on every claim in both releases.

Note: bumping the version makes `ensure()` rebuild `semantic.sqlite3` on first use of any older build, including v1.23,
when this branch reaches the live service.

## Verification

- Unit suites (rewrite_guards, analysis_graph, operator_router, survival, monitor, outreach, vbc, program_effect, profile,
  pathway, demand, market, conformal, conversation, economical_planner, healthcare_warehouse, plus new warehouse_v2 and
  semantic_sql): 280 tests OK, 9 skipped (real-release tests skip on the v1.23 stack because its semantic layer is not at version 13).
- Real-release tests pointed at v2.0 (`MONITOR_STACK`, `PATHWAY_STACK`): the two that assert the v1.23 23-month acute gap
  (D2) now fail because the gap is gone.
- Service on port 8887 without `--allow-live-stack`: 39 acceptance questions (3 per bench file). 21 answered locally with
  the expected operator and 0 paid calls; 18 need hosted planning (explain, survival, trajectory and some phrasings of
  discover, monitor, outreach, program_effect, vbc). The local compiler gives the same routes on the v1.23 profile except
  one: "Where is heart failure unusually common in our network?" compiles on v1.23 and falls back on v2.0. Results:
  `artifacts/agere-healthcare-v20/compose-sample-8887.json`.

## Parity, v1.23 vs v2.0

Same SQL on each semantic layer (`tools/warehouse_parity.py`); window = event and arrival on or before 2029-08-15.
Full output: `artifacts/agere-healthcare-v20/parity-v123.json`.

| Measure | v1.23 @ 2029-08-15 | v2.0 @ 2029-08-15 | v2.0 @ 2030-05-27 |
|---|---:|---:|---:|
| Patients | 1,614 | 1,614 | 1,614 |
| Visible records | 1,238,686 | 1,295,797 | 1,739,068 |
| ED / inpatient / observation visits | 381 / 102 / 68 | 812 / 344 / 131 | 1,006 / 398 / 191 |
| D1 stays with discharge | 96 of 169 (56.8%) | 473 of 473 (100%) | 587 of 587 |
| D1 inpatient encounters with end time | 27.5% | 100% | 100% |
| D2 ED / inpatient visits 2026-11..2028-09 (zero months) | 0 / 0 (23 / 23) | 360 / 174 (0 / 0) | same |
| D4 ED 2026-08 / 09 / 10 | 4 / 92 / 3 | 4 / 35 / 11 | same |
| D15 ED per 1,000 per year (last 12 months) | 71 | 155 | 152 |
| D3 social screens 2026-10..2028-06; intake 2026-11..2028-07 | 0; 0 | 1,040; 2,312 | same |
| D5 cancellations 2026 / 27 / 28 / 29; reschedules | 61 / 0 / 0 / 0; 13 | 251 / 181 / 218 / 180; 256 | …/346 (2029), 89 (2030); 346 |
| D6 uninsured with cost difficulty | 108 of 108 | 61 of 126 | same |
| D8 diabetics with latest A1c > 9 | 1 of 224 (0.4%), max 9.7 | 36 of 224 (16.1%), max 11.8 | 38 of 226 (16.8%) |
| D19 A1c >= 6.5 without diabetes diagnosis | 474 | 474 | 474 |
| D9 HF patients (297) with SGLT2i / MRA / ARNI, any evidence | 5 / 4 / 40 | 5 / 4 / 40 | 5 / 4 / 40 |
| D10 CM agreed; share Medicare 65+ | 231; 82.3% | 231; 82.3% | 231; 82.3% |
| D11 first SGLT2i dispense after 2026 | 0 | 0 | 0 (statin: 7 in 2029) |
| D13 coverage records per patient (max) | 1 | 1 | 1 |
| D7 conditions without a date | 6,929 of 6,929 | 42 of 6,929 | 42 |
| D17 negative lab values | 1 | 0 | 0 |

Fixed by the release: D1, D2, D3, D5, D7, D8, D6 (largely), D4 (spike reduced, still about 3x neighbours), D17.
Unchanged: D9, D10, D11, D13, D19; D15 improved but still low.

Demographics (v2.0): race White 1,233, Black 131, Asian 103, declined 56, unrecorded 43, Other 22, NHPI 19, two or more 5,
AIAN 2; ethnicity Hispanic or Latino 182, not 1,376, declined 56; language English 1,479, Spanish 78, Portuguese 26,
Chinese 12, Vietnamese 7, Haitian Creole 7, Khmer 2, Cape Verdean Creole 2, Korean 1; interpreter needed 97; 14 MA counties.

Code coverage (v2.0): conditions with ICD-10 100%; labs with source LOINC 89.7% (98.9% including dictionary fallback);
claim lines with CPT/HCPCS 99.5% (259 UB-04 room-and-board lines without); medication records with source RxNorm 98.3%
(orders, MAR, lists, dispenses, history, reported entries ~100%; legacy `pharmacy_claims` 0%); NDC on 99.8% of dispenses;
immunizations with CVX 100%; visits with principal ICD-10 90.7%; 187 claims with MS-DRG; all clinicians and organizations
carry NPI (and TIN for organizations).

## Release issues to forward to the generator

1. Typed views point at fields that do not exist: `v_medical_claims.paid_cents` reads `$.paid_cents` (facts carry
   `payer_paid_cents`, 100% populated); `v_pharmacy_dispenses.quantity_dispensed` reads `$.quantity_dispensed` (facts carry
   `quantity`, 96%); `v_pharmacy_dispenses.product_description` has no source field. The data dictionary reports 0%.
2. MS-DRG coverage: 201 inpatient facility claims (TOB 011x) for 398 inpatient admissions; 14 of those 201 lack a DRG.
3. Legacy `pharmacy_claims` stream (1,869 rows) carries neither RxNorm nor NDC (`pharmacy_claim_transactions` does).
4. `medical_claims.service_code` changed meaning: it held the local charge code in v1.23 and now holds CPT (local code moved
   to `local_charge_code`; 3,255 legacy rows keep `synthetic_local`). Agere's `claim.local_charge_code` and
   `claim_line.local_charge_code` keep one local column across releases; duplicate-claim checks keyed on service_code now
   compare CPT codes.
5. Still open from the defect log: D9 GDMT uptake, D10 care-management targeting, D11 incident starts, D13 coverage churn,
   D19 lab/diagnosis disagreement, D15 acute volume (155 ED/1,000/yr), D4 residual September 2026 spike.
6. Late reversals: 355 repriced claims receive the reversal of the original payment after the replacement payment
   (223 in v1.23). Realistic; Agere now handles it.

Not a release defect: v2.0 codes eGFR as LOINC 98979-8 (CKD-EPI 2021) where Agere's normalization dictionary has the older
33914-3 for the same lab name (9,000 rows); the source code wins and the dictionary entry should be updated.

## Making v2.0 active

1. Produce the activation receipts against this build with the current code: `verify_healthcare_warehouse_stack.py --build
   artifacts/agere-healthcare-v20`, `verify_native_requirements.py --build artifacts/agere-healthcare-v20 --out
   artifacts/agere-healthcare-v20/native-requirements`, `verify_received_diagnostics.py --build artifacts/agere-healthcare-v20`,
   and the received-source extension check (`verify_received_source_extension.py --build artifacts/agere-healthcare-v20
   --previous artifacts/agere-healthcare-v123 --inventory <v2.0 inventory>`) plus a `migration-audit.json`
   (identical roster, prior rows unchanged), which `warehouse_activation.activate` requires.
2. Stop the 8885 service, run `activate_healthcare_warehouse.py --build artifacts/agere-healthcare-v20` (the prior config
   goes to `config/agere/activation-history/`; the cutoff moves forward to 2030-05-27, which activation allows).
3. Restart `serve_current_warehouse.py --port 8885` and rerun the HTTP checks. Rollback: restore the saved
   `active_warehouse.json` from activation history.
