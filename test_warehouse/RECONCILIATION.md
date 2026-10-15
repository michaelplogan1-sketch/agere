# Reconciliation: Tuva warehouse vs independent raw SQL

## 1a. Payer medical claims: final net paid by service year (all payer claims, before payer-copy removal)

raw = latest-version method: per claim family (frequency-7 replacements fold into the claim they replace) the version with the latest arrival is current; current status voided / reversed / denied nets to zero, recouped nets to the parent paid plus the negative recoupment. Tuva side = connector_intermediate.int_payer_medical_claims (ledger method). Differences come from versions that arrive out of sequence.

| service_year | raw_claims | tuva_claims | raw_paid | tuva_paid | diff |
|---|---|---|---|---|---|
| 2026 | 19,819 | 19,819 | 4,403,812.50 | 4,400,310.58 | -3,501.92 |
| 2027 | 47,223 | 47,223 | 13,349,830.31 | 13,341,775.76 | -8,054.55 |
| 2028 | 47,818 | 47,818 | 13,096,829.60 | 13,091,949.34 | -4,880.26 |
| 2029 | 49,336 | 49,336 | 13,244,192.63 | 13,239,730.73 | -4,461.90 |
| 2030 | 14,263 | 14,263 | 3,663,311.76 | 3,662,445.53 | -866.23 |

## 1b. Medical claims in Tuva core: paid by service year and data_source

core.medical_claim. payer_feed excludes the payer copies of provider claims (int_claim_crosswalk); provider_billing is the owned providers' adjudicated claims. raw_payer_copy_paid = the excluded payer copies' net paid, shown so the two views reconcile.

| service_year | payer_feed_paid | provider_billing_paid | payer_copy_paid_excluded |
|---|---|---|---|
| 2026 | 3,733,401.28 | 815,975.34 | 666,909.30 |
| 2027 | 12,226,029.97 | 1,140,673.18 | 1,115,745.79 |
| 2028 | 11,923,619.64 | 1,195,909.56 | 1,168,329.70 |
| 2029 | 11,972,748.99 | 1,287,474.47 | 1,266,981.74 |
| 2030 | 3,326,853.40 | 347,616.86 | 335,592.13 |

## 1c. Provider billing: net paid by service year, raw sum of all adjudication deltas vs Tuva

raw = sum over every medical_claims adjudication row of financial_deltas_cents.payer (payer_paid_cents when no deltas), grouped by the earliest submitted service date (adjudication service date, else the remittance event date, when no submission exists); Tuva = core.medical_claim data_source provider_billing.

| service_year | raw_paid | tuva_paid | diff |
|---|---|---|---|
| 2026 | 815,975.34 | 815,975.34 | 0.00 |
| 2027 | 1,147,920.56 | 1,140,673.18 | -7,247.38 |
| 2028 | 1,188,662.18 | 1,195,909.56 | 7,247.38 |
| 2029 | 1,287,474.47 | 1,287,474.47 | 0.00 |
| 2030 | 347,616.86 | 347,616.86 | 0.00 |

## 2. Pharmacy paid total by source

raw payer = sum of plan_paid on paid and recouped rows, minus the paid amount of every rx_claim_id that has a reversal; raw owned-pharmacy billing = sum of all pharmacy_claim_transactions payer_paid_delta_cents + pharmacy_claims payer_paid_cents. Tuva = core.pharmacy_claim. Owned-pharmacy fills whose last transaction is a reversal or rejection are not loaded to Tuva, so their (net zero) deltas are absent there.

| feed | raw_paid | tuva_paid | tuva_claims |
|---|---|---|---|
| payer_feed | 12,373,711.24 | 12,373,247.80 | 95,479 |
| pharmacy_billing | 3,500,248.56 | 3,500,248.56 | 29,682 |

## 3. Distinct members with enrollment

raw = distinct patient ids on enrollment_spans rows that no other row supersedes; Tuva = distinct person_id in core.eligibility and core.member_month. person_id is MPI-resolved, so merged duplicate charts can only lower the Tuva count.

| raw_members | tuva_eligibility_persons | tuva_member_month_persons | tuva_member_months |
|---|---|---|---|
| 3,916 | 3,916 | 3,916 | 168,301 |

## 4. Every ADT visit lands in exactly one clinical encounter

adt_messages visit_number (HL7 PV1-19) against core.encounter data_source hl7_adt; owned-hospital adt encounter_id against core.encounter data_source ehr.

| feed | visits | exactly_one | no_encounter | multiple |
|---|---|---|---|---|
| adt_messages visit_number | 3,718 | 3,718 | 0 | 0 |
| adt (owned hospital) encounter_id | 746 | 746 | 0 | 0 |

## 5a. Acute inpatient and ED encounters by year: Tuva core.encounter

Claims-derived encounters come from Tuva's encounter grouper over core.medical_claim; clinical encounters from the input-layer encounter table. Claims and clinical encounters of the same visit are both present (Tuva does not link them; see connector_gap_fill.claims_clinical_encounter_link).

| yr | src | data_source | acute_inpatient | emergency_department |
|---|---|---|---|---|
| 2021 | clinical | ehr | 0 | 1 |
| 2022 | clinical | ehr | 0 | 1 |
| 2023 | clinical | ehr | 1 | 3 |
| 2024 | clinical | ehr | 2 | 7 |
| 2025 | clinical | ehr | 19 | 50 |
| 2026 | claim | payer_feed | 89 | 252 |
| 2026 | claim | provider_billing | 20 | 45 |
| 2026 | clinical | ehr | 80 | 192 |
| 2026 | clinical | hl7_adt | 68 | 144 |
| 2027 | claim | payer_feed | 329 | 966 |
| 2027 | claim | provider_billing | 90 | 121 |
| 2027 | clinical | ehr | 129 | 221 |
| 2027 | clinical | hl7_adt | 258 | 601 |
| 2028 | claim | payer_feed | 392 | 1,045 |
| 2028 | claim | provider_billing | 83 | 119 |
| 2028 | clinical | ehr | 116 | 215 |
| 2028 | clinical | hl7_adt | 338 | 704 |
| 2029 | claim | payer_feed | 371 | 1,056 |
| 2029 | claim | provider_billing | 66 | 159 |
| 2029 | clinical | ehr | 98 | 264 |
| 2029 | clinical | hl7_adt | 309 | 719 |
| 2030 | claim | payer_feed | 119 | 387 |
| 2030 | claim | provider_billing | 18 | 54 |
| 2030 | clinical | ehr | 40 | 108 |
| 2030 | clinical | hl7_adt | 165 | 332 |
|  | clinical | ehr | 1 | 8 |

## 5b. Acute inpatient and ED by year: independent raw per-source tally

hl7_adt = distinct visit_number by the patient class on its latest message (I / E); ehr = distinct encounter_id whose latest version is type inpatient / emergency (or POS 21 / 23); payer = distinct patient + admission date on non-voided institutional TOB 011x claims (inpatient) and distinct patient + service date on claims with an ED revenue line 045x or POS 23 (ED); provider = same rules on medical_claim_submissions / lines. ED visits that become admissions are inpatient in Tuva (one encounter), but count in both raw columns, so raw ED >= Tuva ED.

| yr | src | ip | ed |
|---|---|---|---|
| 2021 | ehr | 0 | 1 |
| 2022 | ehr | 0 | 1 |
| 2023 | ehr | 1 | 3 |
| 2024 | ehr | 2 | 7 |
| 2025 | ehr | 19 | 50 |
| 2026 | ehr | 80 | 192 |
| 2026 | hl7_adt | 68 | 150 |
| 2026 | payer_claims | 103 | 308 |
| 2026 | provider_claims | 22 | 55 |
| 2027 | ehr | 128 | 220 |
| 2027 | hl7_adt | 261 | 634 |
| 2027 | payer_claims | 387 | 1,190 |
| 2027 | provider_claims | 100 | 141 |
| 2028 | ehr | 117 | 216 |
| 2028 | hl7_adt | 338 | 770 |
| 2028 | payer_claims | 444 | 1,342 |
| 2028 | provider_claims | 88 | 133 |
| 2029 | ehr | 98 | 264 |
| 2029 | hl7_adt | 308 | 771 |
| 2029 | payer_claims | 393 | 1,369 |
| 2029 | provider_claims | 64 | 180 |
| 2030 | ehr | 40 | 108 |
| 2030 | hl7_adt | 165 | 365 |
| 2030 | payer_claims | 121 | 492 |
| 2030 | provider_claims | 21 | 68 |
|  | ehr | 1 | 8 |

