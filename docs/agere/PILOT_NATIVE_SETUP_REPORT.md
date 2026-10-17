# Pilot workspace setup report: v2.12 native sample (2026-10-17)

Workspace `pilot-native-v2-12` (artifacts/workspaces/pilot-native-v2-12, visible at /workspaces) onboards the synthetic
customer delivery `healthcare-warehouse-v2.12-sample-0.30-native/raw` the way a real customer's feeds would arrive.

## Pipeline
| Step | Result |
|---|---|
| Ingest | 107,242 files: X12 837P/837I/835/834, NCPDP D.0, HL7 v2 ADT/ORU, FHIR R4 bulk, CCLF CSV + ALR, Epic/athena/eCW extracts, payer flat files, programs, rosters, SDOH. Fixed-width CCLF skipped (same data as the CSV copy). Every row keeps file and line lineage. |
| Identity | 4,095 persons from 25,551 source identifiers (manifest: 4,113). Tiers: source key, practice MRN, Medicare MBI, demographic key (name + DOB + sex or ZIP). Deep chart 1,614 (exact). 0 clusters mixing birth dates. 5 of 5 MBI re-issues linked. |
| Staging | 1.0M canonical records (payer claims and adjudications, claim lines, pharmacy fills, enrollment spans, practice billing and remittances, encounters, conditions, labs, vitals, medications, appointments, notes, immunizations, allergies, ADT, SDOH); 2,909 clinicians and 4,201 organizations; every version kept in the received store. 32 records (0.003%) could not be tied to a person, all from mangled source rows. |
| Semantic layer | The same builder as every Agere build (semantic-layer/18): all 55 tables present. |
| Raw tables | Every landing table queryable as raw_<table>. |
| Docs | Generated from the workspace's own data. |
| Answering | Hybrid core end to end: "patients with an HbA1c >= 9% in 2029" = 118, three independent runs unanimous, $0.037. |

## Compared with the v2.8 build of the same population
Within about 15%: patients, enrollment spans, member months, payer claims, adjudications, claim diagnoses and lines,
measurements, visits, notes, stays, immunizations, SDOH.

Lower because the native delivery does not contain the source (by the generator's design, stated in the manifest):
- Deep-chart encounters, notes, vitals, orders and appointments at systems outside the Epic instance (specialty,
  urgent care, MEDCARE, outside dental/optometry/audiology, GI, rehab, home care): encounter 0.50, condition 0.50.
- The owned pharmacy's dispensing system and its transactions: medication 0.45, prescription_fill 0.08,
  pharmacy_claim_event 0.54, returned_fill 0.34.
- Hospital and MEDCARE provider billing (only practice 837/835 are delivered): claim_line 0.25, claim_adjustment 0.12.
- Social screens, smoking history, scanned and HIE documents, incident diagnosis streams: 0.
- Payers WH-PAYER-0/1/2 send no pharmacy files (no NCPDP, no flat pharmacy), so their members' fills are absent.

problem_list_entry is higher (8,368 vs 1,176) because EHR problem-list entries arrive natively as problem lists; in
v2.8 most of them were delivered as `conditions` records.

## Fixed during setup
- Providers and organizations were empty: added the entities adapter (roster, EHR providers, departments, hospitals,
  payers, pharmacies, claim NPIs).
- Practice revenue-cycle tables were empty: provider-side 837/835 now feed submissions, lines, adjudications,
  remittances and adjustments, while still being kept out of the payer claim streams.
- Adjudication history and record corrections were missing: staging now writes the received store with every version.
- The hybrid core's three runs sent identical requests, which the provider ledger collapsed (two runs failed; when
  cached, one reply would have been copied three times). Each run now sends its own request.
- Lab results are dated specimen, else collected, else event, else recorded date.

## Blocks correct scoring (sent to the generator)
1. The 9 patient_set questions are keyed on warehouse person ids (P-/SYN-/WHP-) that no native file carries, so no
   contestant can return matching ids. Needs a key on delivered ids or scoring by person cluster.
2. Some definitions use warehouse-only fields (e.g. "data_segment deep_chart"); they need delivered terms
   ("patients in the EPIC-DEEPCHART instance").
Count, value and table questions (141 of 150) can be scored as delivered. Key digest to pin: v2.12 native 0be74c86.
