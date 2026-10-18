# Native-set diagnosis summary (2026-10-18)

72 recoverable native questions were wrong in both N5 and N6. Each was derived literally from the delivered files
(landing.sqlite) by an agent that never saw keys, then compared with Agere's semantic layer and answers.
Details per question: g1.md (RX, RWE), g2.md (DQ, REV, HOS, CLN), g3.md (QUA, BH), g4.md (VBC, ACC).

## Key disputes (Agere's answer = the literal raw-file derivation; sent to the generator for audit)
RX-01, RX-05, RX-07, RX-08, RX-10, RWE-01, RWE-02, RWE-04, RWE-06, RWE-11, RWE-12, RWE-13,
CLN-04, CLN-14, REV-14, QUA-04, QUA-07, QUA-08, QUA-13, BH-10, BH-12, BH-14, VBC-12, VBC-13, ACC-07 (25).

## Stack data bugs (to fix in staging / semantic layer)
- FHIR bulk practice resources not loaded: notes (DocumentReference), PHQ-2/9 surveys for eCW practices, appointments
  for athena practices (1,716); PHQ-9 valueInteger lost for FHIR-native practices.
- CCLF Part D dedupe misses 12-character MBIs (162 duplicate fills); pharmacy keeps only the final event and overwrites
  the original fill date.
- eGFR rows without LOINC not mapped by component name; problem_list_entry.first_recorded is the onset date.
- Inpatient discharge date missing on 837I (use statement-through); admission_type not modeled.
- payer_claim.first_status / adjudications wrong for 15,468 claims (payer_adjudication is right).
- Enrollment: pre-reissue spans dropped though definitions keep them; FFS attribution practice blank (roster group_npi);
  no death entity (CCLF8); booked_date only from "booked" versions (appt_made_dttm on every Epic version).
- Note extractor drug_key not the first word.

## Docs / conventions (analyst-facing)
product vs line_of_business; event vs claim grain; the question's own definition overrides counts_in_metrics and
superseded; money in dollars; strict ">" for "more than"; "documented" = documentation clock; commit to the literal
reading of an explicit definition instead of clarify; PDC convention.

## Native-only detail
Delivery metadata (raw/manifest.json, file-level parse issues, resends, v2, truncation, empty files) is not in the
semantic layer: expose a delivery_file table and document it (DQ-01..05, DQ-13, DQ-14). Identity/chart structure view
(person_source_key, duplicate vs migration) for DQ-08, DQ-14.

## Question-set issues (sent to the generator)
Definitions that only reference another question ("as CLN-15", "as QUA-03", "as RWE-05", "as REV-01") cannot be
answered by a product that sees one question; they should be inlined.
