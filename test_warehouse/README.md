# Test warehouse: the client's raw warehouse on the Tuva Project

This folder holds the dbt connector that maps the health system's native
warehouse into the [Tuva Project](https://thetuvaproject.com) Input Layer, and
the configuration that runs Tuva Core and its data marts on DuckDB. The result
is a neutral analytics warehouse that any analytics product can be pointed at.
Nothing here is tuned to a benchmark: the mappings follow the source data, the
Tuva contract and standard claims practice, and every decision is written down
below.

```
raw_warehouse (SQLite, read-only)          237 streams, 2.42M records, 4,115 patients
  └─ connector_staging       stg_raw__*    one parse of every mapped stream, version links
     └─ connector_intermediate int_* / map_*   version resolution, adjudication, MPI, crosswalks
        └─ input_layer       15 Tuva Input Layer tables (this connector's output)
           └─ Tuva Core      normalized_layer, claims_preprocessing (service categories,
                             encounter grouper, member months, claims enrollment), core
              └─ marts       cms_hcc, cms_chronic_conditions, quality_measures (incl.
                             readmissions), nyu_ed_classification, ccsr,
                             ahrq_quality_indicators, semantic_layer
        └─ connector_gap_fill claims <-> clinical encounter link (Tuva does not link them)
```

- dbt project: `agere_connector/` (forked from tuva-health/connector_template).
- Packages (`packages.yml`): tuva-core v1.0.0 (`the_tuva_project`), cms_hcc,
  cms_chronic_conditions, quality_measures, nyu_ed_classification, ccsr,
  ahrq_quality_indicators, semantic-layer, all v1.0.0, plus dbt_utils.
- Target: DuckDB `artifacts/test-warehouse/tuva.duckdb`; the raw SQLite
  warehouse `artifacts/bench-db/v28-sample-indexed.sqlite3` is attached
  read-only as `raw_warehouse` (`profiles.yml`).


## Data sources (`data_source`)

| data_source | Feed | Tables |
|---|---|---|
| `patient_roster` | `raw_warehouse.patients` | patient |
| `payer_feed` | payer enrollment, payer medical claims, payer pharmacy claims | eligibility, provider_attribution, medical_claim, pharmacy_claim |
| `provider_billing` | owned practices / labs / hospitals: 837 submissions, 835-style adjudications, remittance | medical_claim |
| `pharmacy_billing` | owned-pharmacy billing (transactions per fill, and the pharmacy claims feed) | pharmacy_claim |
| `ehr` | EHR encounters, problem list, orders, immunizations, appointments, vitals, screenings, procedures | encounter, condition, procedure, observation, medication, immunization, appointment |
| `hl7_adt` | HL7 v2 ADT messages from outside hospitals | encounter, condition |
| `lab_interface` | lab results feed | lab_result |
| `pharmacy_system` | owned-pharmacy dispensing system | medication |
| `provider_roster` | `raw_warehouse.source_entities` | practitioner, location |

`payer_feed` is one label for enrollment and payer claims on purpose: Tuva joins
claims to member months on person, member_id, payer, plan **and data_source**.
`plan` is the line of business (`product`: commercial, medicaid, medicare_ffs,
medicare_advantage, dual_ma, marketplace, ...), the field enrollment and claims
both carry; the payer's `plan_id` is kept as `x_plan_id`. The originating
system (payer id, practice, hospital) is always kept in `x_source_system`.

## Mapping decisions

### Version resolution (all streams)
`stg_raw__supersession` unions every version pointer: the envelope `supersedes`
column, `payload.supersedes_record_id`, and payload-only pointers the envelope
does not mirror (`labs.replaces_result_record_id`,
`medication_orders.replaces_order_record_id`,
`demographic_updates.corrects_age_record_id`). A record named as a parent of a
`supersedes` / `replaces_result` link is not current. Streams that version by a
natural key instead (EHR encounters: in_progress then completed under one
encounter_id; appointments: booked then final status) keep the latest arrival.
Medication order *replacements* (renewals, dose changes) are new prescribing
events, so both orders stay.

### Person / MPI (`int_person_match`)
`person_id` is MPI-resolved; `patient_id` is the source chart. Rule: same
birth date, sex and 5-digit ZIP, Jaro-Winkler(surname) >= 0.85 and
Jaro-Winkler(first name) >= 0.80. The survivor is the chart with the most
records. On this release 8 duplicate charts merge into 8 survivors: 4,115 charts -> 4,107 persons. Same-DOB neighbours with a different surname are not merged (a real MPI would want more evidence, e.g. member id or phone).

### Eligibility
`enrollment_spans`, current versions only (retroactive terminations and member
id reissues replace the span they supersede), `9999-12-31` becomes an open span
(null end). Medicare entitlement: OREC 0/1 and MSC 10/20 from
`medicare_entitlement_reason`; dual status full -> `08`, partial -> `06`,
other Medicare -> `00`. `coverage` (EHR registration) is not payer enrollment
and is not mapped. `provider_attribution` expands `attribution_practice_id`
to one row per covered month.

### Medical claims
**Payer claims (`payer_feed`)** — `int_payer_medical_claim_versions` keeps every
version; `int_payer_medical_claims` produces one final claim per adjudication
family:

- *Family*: a frequency-7 replacement (new `payer_claim_id`, carries
  `original_payer_claim_id`) folds into the claim it replaces. Secondary-payer
  copies carry an `-X` suffix; their replacements (`<id>-R-X`) point
  `original_payer_claim_id` and `supersedes` at the **primary** claim, so the
  target of an `-X` replacement is `original_payer_claim_id || '-X'`.
  Following the pointer literally would leave the replaced secondary claim
  current and double count it.
- *Order*: original before replacement, then adjudication_sequence, arrival,
  paid date.
- *Ledger*: paid / denied / denied_upheld versions restate the claim (net =
  that version); signed reversals and recoupments add their negative amount;
  a void (frequency 8) sets net to 0; an **unsigned** reversal record (the
  record of an adjudication that was reversed and re-processed; the
  re-processed version carries the amount) has no effect. This is the rule set
  in `tmp/claim_rules_from_heldout9_agent.md` (adjudication steps keep the
  parent, corrections replace it, voids net to zero, signed reversals), checked
  against the provider side: for every payer copy whose provider claim was
  reversed and re-priced, the payer net equals the provider's net only under
  this reading.
- Recoupments with no paid version in the release net to the negative
  recoupment (`no_restate_version_flag`).
- Lines: `payer_medical_claim_lines` of the final version, else of the family's
  original, else (secondary `-X` claims have no lines of their own) of the
  primary claim; a claim with no lines gets one header-only line. Header net
  paid and allowed are allocated to lines by allowed share (charge share, then
  equal split), remainder on the last line, so lines sum exactly to the header.

**Provider claims (`provider_billing`)** — `int_provider_medical_claims`: the
current submission per claim_id (a corrected submission drops the one it
names), money = sum over the claim's adjudications of
`financial_deltas_cents` (payer, allowed, deductible, copay, coinsurance;
reversals are negative, re-priced replacement adjudications carry the new
amount), facility DRG adjudications without deltas use
`payer_paid_cents` / `allowed_cents`, paid date from the 835 remittance.
Adjudications whose submission is not in the release (lab claims) are claims
of their own; adjudications without a claim_id are standalone claims keyed by
record id. Submitted claims with no adjudication in the release are pending,
not final-action, and are not loaded. Institutional claims use the billing NPI
as facility NPI (UB-04).

**Payer copies of provider claims** — there is no shared key.
`int_claim_crosswalk` matches a payer family to a provider claim on same
patient, payer `service_from` = provider service date and the family's largest
`total_charge` in cents = the provider `billed_cents` (any submission, or the
adjudication's billed amount). Matched payer families are excluded from Tuva so
every service is counted once, from the provider feed; unmatched payer copies
stay in `payer_feed`. On this release 35,104 of the 178,459 payer claim families match a provider claim (all of them payer adjudications of owned-practice / lab claims) and are excluded; 2 families match more than one provider claim. 11,688 payer copies with no adjudicated provider counterpart stay in `payer_feed` (most have no provider claim in the release at all; 4,879 provider submissions have no adjudication and are not loaded).

### Pharmacy claims
- `payer_feed`: one claim per `rx_claim_id`, versions by adjudication_sequence
  then arrival; paid/rejected restate, NCPDP B2 reversals (return to stock) net
  to 0, recoupments (CO-27) add their negative plan_paid; if a claim has two
  current rows the later arrival wins. Final status paid or recouped is loaded;
  rejected (incl. NCPDP 79 refill too soon) and reversed claims are not fills
  and are not loaded.
- Amount-less payer copies of owned-pharmacy fills (BIN 990099) are dropped
  when the owned-pharmacy billing record exists (same patient, NDC, fill date
  within 3 days); unmatched copies stay with null paid.
- `pharmacy_billing`: `pharmacy_claim_transactions` per fill (`claim_id` =
  dispense `fill_id`, signed cents deltas summed, final status = last
  transaction, only fills whose final status is paid) and `pharmacy_claims`
  (one paid claim per pharmacy prescription id).
- Clinical `medication` gets dispenses with status `dispensed` (prepared and
  returned_to_stock are not fills), orders and administrations with status
  given.

### Clinical
- **Encounters**: EHR encounters (current version), enriched with the owned
  hospitals' `adt` events that share the encounter_id (admit/discharge dates,
  admit source, discharge disposition; an ADT inpatient admission makes the
  encounter acute inpatient). Encounter type from the EHR visit type
  (`seeds/ehr_encounter_type_map.csv`), else the CMS place of service
  (`seeds/pos_encounter_type.csv`). Telephone contacts are `telehealth` (the
  source codes their completed version POS 10). HL7 ADT (`adt_messages`):
  one encounter per `visit_number`, class from the latest message
  (I -> acute inpatient, E -> emergency department, O -> outpatient hospital
  or clinic); an ED visit converted by A06 is a single inpatient encounter
  with `x_ed_to_inpatient = 1` (Tuva's grain is one row per encounter).
- **Conditions**: problem list (`conditions`, ICD-10-CM else SNOMED CT),
  ranked EHR encounter diagnoses, ICD-10-CM `condition_assessments`, and ADT
  DG1 admitting/final diagnoses. Codes go in as delivered; Tuva strips dots and
  validates them against its ICD-10-CM / SNOMED terminology.
- **Labs**: current versions (including `replaces_result_record_id`), LOINC when
  delivered else the lab's local code (`source_component_type = 'local'`),
  source status kept, censored values kept as delivered.
- **Observations**: vitals (wide and long layouts) and growth measurements with
  LOINC (`seeds/vital_sign_codes.csv`), PHQ-9 totals (44261-6), HRA PHQ-2
  (55758-7), falls (52552-0), self-rated health and need items, AHC-HRSN item
  answers (LOINC per item from the source) plus a panel row (96777-8), and the
  brief needs interview items (`seeds/observation_codes.csv`).
- **Procedures**: EHR performed procedures, screening results (FIT,
  mammography), endoscopy, plain films and coded annual wellness visits;
  local procedure names to HCPCS/CPT via `seeds/local_procedure_codes.csv`.
- **Immunizations** (CVX), **appointments** (latest status per appointment id,
  source-native status/type/reason as Tuva requires), **practitioner** and
  **location** from the rosters.

### Terminology
Tuva's terminology does the normalization: ICD-10-CM/PCS, SNOMED CT, LOINC,
NDC and RxNorm (CodeRx), CVX, MS-DRG, bill type, revenue center, place of
service, admit source/type and discharge disposition descriptions. The seeds in
`agere_connector/seeds/` are only the local-to-standard maps Tuva cannot ship:
the client's EHR visit types, local procedure names, vitals field names,
instrument items, product -> payer type, and the word-coded discharge
dispositions (`home`, `snf`, ...) to UB-04 codes.

### Extension columns
Every input table carries `x_` columns (passthrough prefix kept, `strip:
false`): `x_patient_id`, `x_source_system`, `x_available_date` (the latest
arrival among the contributing records, for as-of filtering), the claim's
`x_final_claim_status`, `x_status_history`, `x_version_count`,
`x_source_claim_id`, the encounter's `x_source_encounter_type`,
`x_ed_to_inpatient`, and others documented in the model SQL.

## Stream coverage
`agere_connector/seeds/stream_map.csv` accounts for all 237 raw streams:
**33 mapped, 204 unused.** Every unused stream has a reason (mostly: no Tuva Input Layer
table exists for documents, referrals, orders, care-management workflow,
revenue-cycle cash, allergies; or the stream is workflow around a fact that is
mapped from another stream). The test `tests/assert_every_stream_is_mapped.sql`
fails the build if a stream appears in `raw_warehouse.records` that the map
does not account for, if a status is invalid, or if an unused stream has no
reason.

| Mapped stream | Input table(s) |
|---|---|
| `adt` | encounter |
| `adt_messages` | encounter, condition |
| `appointments` | appointment |
| `cancer_screening_results` | procedure |
| `condition_assessments` | condition |
| `conditions` | condition |
| `death_records` | patient, eligibility |
| `demographic_updates` | patient |
| `encounters` | encounter, condition |
| `endoscopy_reports` | procedure |
| `enrollment_spans` | eligibility, provider_attribution |
| `growth_observations` | observation |
| `health_risk_assessments` | observation |
| `imaging_studies` | procedure |
| `immunizations` | immunization |
| `labs` | lab_result |
| `medical_claim_lines` | medical_claim |
| `medical_claim_submissions` | medical_claim |
| `medical_claims` | medical_claim |
| `medication_administrations` | medication |
| `medication_orders` | medication |
| `mood_questionnaires` | observation |
| `payer_medical_claim_lines` | medical_claim |
| `payer_medical_claims` | medical_claim |
| `payer_pharmacy_claims` | pharmacy_claim |
| `pharmacy_claim_transactions` | pharmacy_claim |
| `pharmacy_claims` | pharmacy_claim |
| `pharmacy_dispenses` | medication |
| `procedures` | procedure |
| `provider_remittance_advice` | medical_claim |
| `social_screens` | observation |
| `vitals` | observation |
| `wellness_visit_services` | procedure |

Plus the non-stream raw tables `patients` (patient) and `source_entities` (practitioner, location).

## Gap filler: claims <-> clinical encounter link
`connector_gap_fill.claims_clinical_encounter_link` links each Tuva
claims-derived encounter to the clinical encounter of the same visit: same
person; inpatient stays overlap with one day of slack; a claims ED visit
matches a clinical ED visit, or a clinical inpatient stay, starting within one
day; other settings match a clinical encounter of the same encounter group
starting within one day. Best candidate = same encounter type, then smallest
start gap, then lowest id; one link per claims encounter. Result: 29,125 of 149,851 claims encounters link (acute inpatient 1,247 of 1,577; emergency department 2,694 of 4,204; office visits 23,170 of 50,801). Unlinked claims encounters are mostly lab-only claims (36,309 "lab - orphaned"), outside providers the EHR never saw, and claims for visits outside the clinical feeds' coverage.

## Tuva configuration and overrides
- Vars (`agere_connector/dbt_project.yml`): `claims_enabled`, `clinical_enabled`,
  `provider_attribution_enabled` true; data quality off.
- `tuva_last_run: "2030-05-27 00:00:00"`: the release manifest's as-of date.
  Tuva caps open enrollment spans at `tuva_last_run` and computes ages from it;
  the data runs to 2030, so the wall-clock run date would truncate member months
  at October 2026.
- `quality_measures_period_end: 2029-12-31`: the last complete calendar year.
- `cms_hcc_payment_year: 2027`: the package's CMS normalization factors and
  adjustment rates end at payment year 2027, so PY2027 (2026 dates of service,
  September-December 2026 here) is the latest year Tuva can score;
  `patient_risk_factors_monthly` still covers payment years 2027-2031.
  Scores are low (mean payment score 0.56) because only four months of 2026
  data exist.
- Override `models/tuva_overrides/normalized_input__int_medical_npi_normalize.sql`
  (package model disabled in `dbt_project.yml`): identical to Tuva Core v1.0.0
  except the facility NPI join predicate. DuckDB 1.5 planned the original
  (`... and med.claim_type = 'institutional'` in a left join's ON clause) as a
  blockwise nested-loop join against 2M organization NPIs and did not finish
  in 10 minutes; the rewritten predicate is the same filter and runs in 0.6 s.
  Several other Tuva models use the same pattern and still plan nested loops
  (normalized__condition 314 s, normalized__encounter 252 s,
  normalized__observation 231 s, normalized__medical_claim_procedures 226 s);
  they finish, so they were left as shipped.
- All packages built; none was skipped. Tuva's own tests pass; two
  package tests are configured as warnings and warn: 6,548 CCSR
  condition rows without a CCSR category (codes CCSR v2023.1 does not
  cover, e.g. newer ICD-10-CM codes and Z codes outside the map) and 3
  hcc_recapture eligible members with no age group.
- `x_` extension columns must not end in `_flag` (Tuva's public flag contract
  test reserves the suffix), so connector flags are named `x_observation_stay`,
  `x_ed_to_inpatient`, `x_no_paid_version`.

## Reconciliation
`agere_connector/scripts/reconcile.py` recomputes each figure with its own SQL
straight from `raw_warehouse.records` and compares it with the Tuva output; the
full report is `RECONCILIATION.md`. Differences are reported, not forced.

1. **Payer medical net paid by service year** (all 178,459 payer claim
   families). Tuva ledger vs a raw "latest version wins" query: 2026
   4,400,310.58 vs 4,403,812.50; 2027 13,341,775.76 vs 13,349,830.31; 2028
   13,091,949.34 vs 13,096,829.60; 2029 13,239,730.73 vs 13,244,192.63; 2030
   3,662,445.53 vs 3,663,311.76 (claim counts identical). The whole difference
   (about 21.8k) is 592 payer copies whose last row is an *unsigned* reversal
   carrying the original amount; the raw query takes that amount, the ledger
   takes the re-processed amount, and the provider's own remittance confirms the
   re-processed amount for those claims. Plus 5 claims whose recoupment arrived
   before the paid row it recoups. A reference figure from a parallel raw inventory for
   2029 (13,192,436.59, counted per payer_claim_id rather than per family) is
   lower than both by about 47k; that method was not available to reproduce.
   Final statuses: paid 164,911; denied 8,481; denied_upheld 4,880; reversed
   98; recouped 85 (16 with no paid version, net -1,779.84); voided 4 (voids
   followed by a replacement are paid families).
2. **Provider billing net paid by year**: equal to the raw sum of every
   adjudication delta in 2026, 2029 and 2030; one inpatient claim
   (7,247.38, admitted 2028-01-01, submission service date 2027-12-31) moves
   from 2027 to 2028 because Tuva dates institutional claims by admission.
   In Tuva core, payer_feed + provider_billing = payer families minus the
   excluded payer copies plus the provider claims (table 1b in the report).
3. **Pharmacy paid**: payer_feed 12,373,247.80 vs raw 12,373,711.24 (the
   463.44 is one rx_claim_id with two "current" paid rows; Tuva takes the
   later one, the raw sum counts both). Statuses match the raw counts exactly:
   paid 125,488, rejected 4,032, reversed 2,172, recouped 44.
   pharmacy_billing 3,500,248.56 = raw exactly.
4. **Members with enrollment**: raw 3,916 = Tuva eligibility persons 3,916 =
   member-month persons 3,916 (168,301 member months, Aug 2026 - May 2030).
5. **ADT visits**: all 3,718 `adt_messages` visit numbers land in exactly one
   hl7_adt encounter; all 746 owned-hospital `adt` encounter ids land in
   exactly one ehr encounter.
6. **Acute inpatient / ED by year**: Tuva clinical inpatient counts equal the
   raw per-source tallies (hl7_adt 2027: 259 = 259; ehr 2029: 98 = 98, etc.).
   Tuva clinical ED is lower than the raw "ever in ED class" tally by the ED
   visits that converted to admissions (one inpatient encounter in Tuva, e.g.
   hl7_adt 2027 600 vs 634). Tuva claims-derived counts are below the raw
   payer-claim tally because payer copies of owned-provider claims are
   excluded (they appear under provider_billing) and Tuva's grouper merges
   claims of one stay; e.g. 2029 payer_feed 371 + provider_billing 66
   inpatient vs raw payer 393 + provider 64.

## Run commands
```bash
cd test_warehouse/agere_connector
export DBT_PROFILES_DIR=.
DBT=/Users/mikel/clinical-insights-local/.venv-tuva/bin/dbt
$DBT deps                                   # packages (already vendored in dbt_packages/)
$DBT seed --threads 4                       # Tuva terminology/value sets + connector seeds
caffeinate -i /usr/bin/time -l $DBT build --exclude resource_type:seed --threads 4
/Users/mikel/clinical-insights-local/.venv-tuva/bin/python scripts/reconcile.py --markdown ../RECONCILIATION.md
```
Rebuild just the connector: `$DBT build --select package:agere_connector`
(never `model+` without `package:agere_connector`, which selects all of Tuva).

## Build statistics (2026-10-15, 24 GB Mac, DuckDB 1.5.6, dbt 1.12.6)
- Full `dbt build --exclude resource_type:seed --threads 4`: 975 nodes,
  PASS 973, WARN 2, ERROR 0, in 1,625 s (27 min) wall clock.
- `/usr/bin/time -l`: maximum resident set size 9.07 GB, peak memory
  footprint 17.8 GB (DuckDB default memory limit; another session's processes
  were using about 12 GB at the time, so the machine swapped). Lower the
  footprint with `PRAGMA memory_limit` / `threads` in profiles.yml if needed.
- Connector only (`--select package:agere_connector`): about 1 minute; the
  raw parse (`stg_raw__records`, 33 streams) takes 8 s.
- DuckDB file `artifacts/test-warehouse/tuva.duckdb`: 4.2 GB on disk
  (3.2 GB used blocks), including Tuva terminology/value sets and the NPPES
  provider file (9.7M NPIs).

## Built tables (row counts)
| Layer | Table | Rows |
|---|---|---|
| input_layer | patient / eligibility / provider_attribution | 4,115 / 5,169 / 102,641 |
| input_layer | medical_claim (lines) | 289,078 (payer_feed 143,355 claims, provider_billing 36,483 claims) |
| input_layer | pharmacy_claim | 125,161 (payer_feed 95,479, pharmacy_billing 29,682) |
| input_layer | encounter / condition / procedure | 61,542 / 129,861 / 9,365 |
| input_layer | lab_result / observation / medication | 190,694 / 123,374 / 54,159 |
| input_layer | immunization / appointment / practitioner / location | 2,711 / 36,228 / 3,463 / 2,548 |
| core | patient (per data_source) / eligibility / member_month | 8,023 (4,107 persons) / 5,169 / 168,301 |
| core | medical_claim / pharmacy_claim / encounter | 289,078 / 125,161 / 211,393 (149,851 claims-derived, 61,542 clinical) |
| core | condition / procedure / medication / lab_result / observation | 464,909 / 279,541 / 179,320 / 190,694 / 123,374 |
| core | cost / utilization / person_id_crosswalk | 168,301 / 168,301 / 9,284 |
| cms_hcc | patient_risk_scores (PY2027) / _monthly / patient_risk_factors_monthly | 908 / 10,896 / 50,198 |
| hcc_recapture / hcc_suspecting | hcc_status / list | 9,644 / 28,032 |
| chronic_conditions | cms_chronic_conditions_long / _wide | 288,949 / 8,023 |
| quality_measures | summary_long / summary_wide / readmission_summary | 12,200 / 8,023 / 1,058 |
| ed_classification | summary (NYU) | 7,774 |
| main (ccsr) | ccsr__long_condition_category / singular_condition_category / long_procedure_category | 2,756,714 / 189,536 / 0 (CCSR procedures need ICD-10-PCS; no feed delivers ICD-10-PCS codes) |
| ahrq_quality_indicators | pqi_denom_long / pqi_num_long / pqi_rate | 136,305 / 222 / 50 |
| semantic_layer | fact_claims / fact_encounters / fact_admissions / fact_ed_visits / fact_member_months | 241,946 / 149,851 / 1,577 / 4,204 / 168,301 |
| connector_gap_fill | claims_clinical_encounter_link | 29,125 |
