# Agere canonical analytic model (canonical/1)

Status: design, 2026-10-15. Keep this file current with the code.

## Why

Agere and the LLM baseline both failed heldout10 on the same questions: ED visits, admissions, readmissions, payer paid dollars, statin fills, screening from claim lines and condition prevalence. Each of those concepts lives in 3–4 raw streams with different keys, versioning and duplicate copies. Today every question rebuilds the concept from the 237 raw streams plus a partial semantic layer, so every answer re-derives the dedupe rules. Commercial stacks (Innovaccer, Arcadia, Epic Caboodle, Databricks healthcare accelerators, the Tuva Project) put a canonical model between the feeds and the questions. This is ours.

Principles:
1. **One row per real-world thing.** A visit is one row whether it arrived as an EHR encounter, ADT messages, a facility claim and a professional claim. A fill is one row whether it arrived as a dispense and a payer claim.
2. **Versions resolved once.** A record is current unless a later record supersedes it, through `supersedes_record_id`, `replaces_result_record_id`, claim frequency 7/8, `adjudication_sequence` or `claim_corrections.replacement_*`. The full history stays in `*_history` tables, for questions like "denials later overturned".
3. **Provenance kept.** Every canonical row lists its source streams and record IDs (`source_streams`, `source_record_ids`) and carries `available_date`, the latest arrival among its contributors, so as-of filtering works.
4. **Standard vocabularies.** ICD-10-CM, SNOMED, CPT/HCPCS, LOINC + UCUM, RxNorm/NDC, POS, TOB, revenue codes, MS-DRG, CVX. Local codes are mapped through the normalization dictionary (Jev / UltraBERT fallback), and the mapping confidence is stored.
5. **Every stream accounted for.** `config/agere/canonical/stream_map.json` maps each of the 237 raw streams to canonical tables, or declares it unused with a reason. The source-coverage gate fails the build on any unmapped stream.
6. **Aligned to OMOP CDM v5.4 and the Tuva Project core.** Column names follow Tuva core where one exists, and the OMOP equivalent is noted. Analytic marts follow Tuva's mart layer.

Location: `clinical_core/agere/canonical/` (transforms, one module per table), output `<build>/stack/canonical.sqlite3`, version `canonical/1`. It is built after load and before the semantic layer, and runs under the release-diff and source-coverage gates.

## Core tables

### person (OMOP person; Tuva patient)
- One row per person.
- Columns: `person_id, birth_date, sex, race, ethnicity, language, zip, county_fips, death_date, death_source, segment (deep_chart|population), source_count`.
- Sources:
  - roster / patients;
  - `demographic_updates`: latest version per field;
  - `death_records` (date of death beats `demographic_updates.date_of_death`; `death_source` is recorded);
  - `sdoh_vendor_attributes` for county_fips when nothing else has it.
- **MPI:** `person_source(person_id, source_system, source_patient_id, link_basis)` is a crosswalk built from `identity_link_basis` / `source_patient_id`. Duplicate charts (the same person under two source IDs in one system) merge to one person_id, and the rule is recorded. All facts are keyed on person_id.

### enrollment_span, member_month (Tuva eligibility, member_months)
- Source: `enrollment_spans`, current versions only. Retro-terminations replace the prior span. The `9999-12-31` placeholder end becomes open.
- Columns: `person_id, payer_id, product (commercial|medicaid|medicare_ffs|medicare_advantage|dual_ma|dual_ffs|marketplace|medigap|cob_secondary), coverage_start, coverage_end, dual_status, medicare_entitlement_reason, member_id`.
- `member_month`: one row per person per payer per month with coverage on the 1st. When spans overlap, the latest coverage_start wins, then the latest arrival.
- `coverage` (EHR registration coverage_type) is kept as `registration_coverage`. It is documented as the EHR's view, not the payer's.

### encounter: the ENCOUNTER GROUPER (Tuva encounter; OMOP visit_occurrence)
One row per real visit or stay. Inputs:

| Source | Contribution |
|---|---|
| `encounters` (EHR) | type, POS, dates, diagnoses, disposition_text, owned practice |
| `adt_messages` / `adt` (HL7) | grouped by (`organization_id`, `visit_number`): A04/A01 start, A03 end, A06 class change, A08 update; `patient_class` E/I/O |
| `payer_medical_claims` institutional | TOB 011x inpatient, 013x outpatient/ED/obs (revenue 045x ED, 0762 obs), 021x/018x SNF, 081x/082x hospice, 032x/033x home health; admission/discharge dates, DRG, discharge_status |
| `payer_medical_claims` professional + `payer_medical_claim_lines` | POS 23 ED, 21 inpatient, 22 outpatient, 11 office, 02/10 telehealth; E/M codes |
| `medical_claim_submissions` + `medical_claim_lines` | the owned practice's own billing (professional; institutional where present) |

Grouping rules, applied per person:
1. **Version resolution first** (ADT A08 updates, claim replacements and voids), so voided or denied-then-replaced claims don't create visits. A claim whose final status is denied still evidences a visit and is kept with `claim_status_final`.
2. **Setting** is resolved per contributor into `encounter_type ∈ {inpatient, observation, emergency, outpatient_hospital, office, telehealth, urgent_care, snf, home_health, hospice, other}`, with precedence inpatient > observation > emergency > others.
3. **Stays** (inpatient, observation, SNF, hospice) group by overlapping or adjacent date spans (admit..discharge, gap ≤ 1 day). An observation stay followed by an inpatient admit in the same span becomes one inpatient stay with `observation_hours`. Transfers between facilities are separate stays linked by `transfer_from_encounter_id`.
4. **Visits** (emergency, office, outpatient, telehealth, urgent care) group per person per encounter_type per calendar date. An ED visit that leads to admission stays its own emergency encounter with `admitted = 1` and `next_encounter_id`, so ED visit counts include admitted patients.
5. **Dates** follow precedence ADT > EHR > institutional claim > professional claim. **Disposition** follows ADT A03 `discharge_disposition` > claim `discharge_status` > EHR `disposition_text` (mapped to a standard set: home, home_health, snf, rehab, transfer_acute, ama, expired, hospice, other).
6. Diagnoses: principal (claim `principal_diagnosis` > ADT discharge dx > ADT admit dx > EHR first dx), all diagnoses, DRG (from the claim).

Columns: `encounter_id, person_id, encounter_type, start_date, end_date, length_of_stay_days, admit_type, admit_source, discharge_disposition, principal_dx, drg, facility_organization_id, owned (0|1), admitted, observation_hours, source_streams, source_record_ids, available_date`.

Companion tables:
- `encounter_source(encounter_id, stream, record_id)` is the full lineage.
- `encounter_grouper_audit` holds per-rule counts (merged, split, dropped), so every grouping decision can be counted and tested.

### condition (OMOP condition_occurrence; Tuva condition)
- One row per (person, code, date, evidence).
- Columns: `person_id, code, code_system (icd10cm|snomed), normalized_icd10 (dotless), date, evidence_type, position (principal|secondary|n), encounter_id, status (active|resolved), source_stream, record_id`.
- `evidence_type` values:

| evidence_type | Source |
|---|---|
| `problem_list` | `conditions` |
| `encounter_dx` | EHR `encounters.diagnoses` |
| `adt_admit_dx`, `adt_discharge_dx` | ADT messages |
| `provider_claim_dx` | provider claim submissions |
| `payer_claim_dx` | payer claims; final status not voided, denied kept |
| `hie_dx` | HIE documents |
| `death_cause` | death records |

- Ascertainment is any evidence unless a question restricts it. Marts state their rule.

### procedure (OMOP procedure_occurrence; Tuva procedure)
- One row per (person, code, date), deduplicated across sources.
- Sources:
  - EHR `procedures`;
  - provider `medical_claim_lines`;
  - `payer_medical_claim_lines` from current, non-voided claims;
  - `immunizations` (CPT administration codes);
  - `wellness_visit_services` (HCPCS G0438/G0439);
  - screening results (`cancer_screening_results`, `preventive_screening`).
- Columns: `person_id, code, code_system (cpt|hcpcs|icd10pcs|loinc|local), date, encounter_id, sources, claim_status_final`.
- Code families (mammography 77065-77067/G0202, colonoscopy 45378/45380/45385, FIT 82274, AWV G0438/G0439, A1c 83036, flu vaccine codes + G0008) live in `config/agere/canonical/value_sets.json` with citations, never in code.

### medication_fill (Tuva pharmacy_claim + dispense; OMOP drug_exposure type "dispensed")
- One row per fill the patient actually received.
- Sources:
  - `pharmacy_dispenses` with status `dispensed` only (`prepared` and `returned_to_stock` are excluded);
  - `payer_pharmacy_claims` with final status `paid` (`rejected` and `reversed` excluded; reversals/recoupments via `pharmacy_claim_transactions`);
  - `pharmacy_claims` (owned pharmacy billing).
- **Dedupe:** the same person + RxNorm ingredient (via NDC→RxCUI) + fill date ±1 day + days_supply is one fill seen twice. The payer amounts and the dispense label details are both kept.
- Columns: `fill_id, person_id, rxcui, ingredient, drug_name, ndc, fill_date, days_supply, quantity, drug_class, plan_paid, patient_pay, sources, available_date`.
- `pharmacy_claim` keeps one row per `rx_claim_id`, with final status, reject_code (NCPDP 79 = refill too soon) and amounts, for questions about rejections and plan spend.

### lab_result (OMOP measurement; Tuva lab_result)
- Current version only (`supersedes_record_id`, `replaces_result_record_id`; cancelled excluded). `lab_result_history` keeps all versions.
- Columns: `person_id, loinc, concept, name_raw, value_raw, value_numeric, censor ('<'|'>'|null), unit_raw, unit_ucum, value_canonical, result_date, encounter_id, source_system, mapping_confidence`.
- Censored values ("<5", ">90") keep the bound and a censor flag. They never become missing and never become the bound value silently.

### observation (OMOP observation/measurement; Tuva observation)
- `vital_sign`: one row per person, measurement and timestamp.
  - Blood pressure is split into systolic and diastolic. Wide vitals are unpivoted.
  - UCUM units.
- `screening`: one row per completed instrument.
  - Instruments and sources:
    - PHQ-2 and PHQ-9: `mood_questionnaires` and `health_risk_assessments`;
    - AHC-HRSN: `social_screens`;
    - fall risk and cognitive: HRA;
    - developmental, vision and hearing: youth streams.
  - Columns: `instrument, total, positive, positive_domains, items, date, person_id, record_id`.
  - Positive thresholds come from config with citations.
- `sdoh_attribute`: vendor SVI and indices from `sdoh_vendor_attributes`, using the latest delivery per person, with the match type.

### medical_claim, claim_line, claim_adjudication_history (Tuva medical_claim; OMOP cost)
- **Payer view, authoritative for plan money.**
  - `payer_medical_claims` + `payer_medical_claim_lines`, keyed on `payer_claim_id`.
  - The final adjudication follows the supersedes chain and `adjudication_sequence`. Frequency 7 = replacement, 8 = void.
  - Final status ∈ {paid, denied, denied_upheld, voided, reversed, recouped}; `paid_amount` is the final net.
  - `claim_adjudication_history` keeps every version in order, so the canonical model answers "denied then paid" directly.
- **Provider view, authoritative for practice billing and collections.**
  - `medical_claim_submissions` (original/corrected) + `provider_remittance_advice` + `claim_corrections` + `medical_payment_*` + `patient_payments` / `patient_refunds`, keyed on the provider `claim_id`.
- **Payer copy vs provider claim.** No shared key exists. Match on person + service date + billing NPI + total charge, recording the match rule and confidence in `claim_crosswalk`. Each money question then reads one perspective, never both, so dollars are never double counted.
- **Facility rows.** Institutional claims carry DRG, TOB, admission and discharge dates and revenue lines.
- Columns:
  - medical_claim: `claim_id, perspective (payer|provider), person_id, payer_id, product, claim_type, bill_type, place_of_service, service_from, service_to, received_date, paid_date, final_status, billed, allowed, paid, patient_responsibility, drg, principal_dx, dx_codes, rendering_npi, billing_npi, network_status, encounter_id, available_date`;
  - claim_line: `claim_id, line_number, code, code_system, revenue_code, units, service_date, charge, allowed`.

### provider, organization (Tuva provider; OMOP provider/care_site)
- Rosters and NPIs from `clinician`/`organization` sources and claim NPIs.
- `owned` = the client's own practices (`WH-PRACTICE-*`), versus outside hospitals (`WHN-HOSPITAL-*`), labs and payers.

### appointment
- One row per appointment, latest version.
- Columns: `final_status (completed|no_show|cancelled|rescheduled|scheduled)`, `cancel_channel`, `reschedule_reason` (from `appointment_scheduling_contacts` / `telephone_messages` / `outreach_attempts` where linked), `visit_type`, `purpose` (free text when visit_type is empty), `booked_date`, `slot_date`, `lead_time_days`, `attended_encounter_id`.

### Other mapped tables
- `immunization` (CVX; administered only)
- `referral`
- `care_management`
- `document` (notes, HIE, scanned, C-CDA, with a pointer to text)
- `death`
- `data_delivery` (per source system delivery dates, for feed-gap questions)
- `record_correction` (per stream supersessions, for data-quality questions)

## Data marts (Tuva mart layer)

| Mart | Built from | Rule source |
|---|---|---|
| `chronic_condition` | condition | CMS CCW-style value sets, configurable evidence (any / 2 outpatient or 1 inpatient) |
| `cms_hcc` | condition (payer + provider dx), enrollment | CMS-HCC V24/V28 (existing risk_adjustment engine, already matches CMS software 28/28) |
| `quality_measure` | procedure, lab_result, observation, medication_fill, encounter | measure library (BCS, COL, CBP, HBD, KED, SPD, PDC…) |
| `readmission` | encounter (inpatient stays) | 30-day all-cause, index/readmit flags, planned exclusions |
| `pmpm` | medical_claim (payer perspective), pharmacy_claim, member_month | plan paid / member months by product |
| `ed_classification` | encounter (emergency) + principal_dx | NYU ED algorithm-style preventable/non-emergent buckets (Tuva-compatible) |
| `utilization` | encounter, member_month | visits/admits per 1,000, LOS |
| `medication_adherence` | medication_fill | PDC per class (fills de-duplicated, overlap shifted) |

Each mart documents its definition and the source tables. A question that states its own definition is answered from the core tables, not from the mart's default.

## Gates and tests
- **Source coverage.** Every stream in the release appears in `stream_map.json` (mapped table(s) or `unused: reason`). An unmapped stream fails the build.
- **Release diff.** Per table row counts and key sets compared with the prior release, with reasons required for drops (as in the activation restatement gate).
- **Transform tests.** Each table has unit tests on synthetic fixtures for every rule above (version chains, voids, payer copy vs provider claim, ED→admit, obs→inpatient, censored labs, returned-to-stock), plus reconciliation checks against the raw warehouse: e.g. the sum of final paid amounts equals the independent raw computation, and every ADT visit_number lands in exactly one encounter.
- **Grouper audit.** `encounter_grouper_audit` counts are reported in the build receipt.

## Consumers
1. The semantic-analyst LLM rung reads `canonical.sqlite3` plus generated documentation (tables, columns, rules, value sets, marts, and verified examples that are never drawn from heldout sets).
2. The compiler, governed metrics and engines move from raw streams and the semantic layer to the canonical tables. The semantic layer becomes a thin view layer on top, or is retired.
3. Bench: `frontier_canonical` = the same tool loop as `frontier_semantic`, pointed at canonical docs and DB.

## Proof plan
- Re-run heldout10 with the same LLM (Haiku 5.5, the bake-off winner, plus Opus 5.5) on the canonical model vs today's semantic layer, scored strictly and reported by domain.
- Today's baselines on the semantic layer: Haiku 42/80, Sonnet 38/80, Opus 38/80. Agere alone: 26/80.
- heldout10 is no longer blind for the fixes made against it, so the confirming number comes from a fresh truth-backed set (docs/agere/REQUEST_GENERATOR_TRUTH_QUESTIONS.md).

## Build order
1. Version resolution + person/MPI + enrollment/member_month
2. medical_claim / claim_line / history / crosswalk; pharmacy_claim; medication_fill
3. encounter grouper
4. condition, procedure, lab_result, observation, appointment, remaining tables
5. stream_map coverage gate, docs generator, `frontier_canonical` bench system
6. marts
