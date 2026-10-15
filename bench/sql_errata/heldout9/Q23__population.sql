-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Screening mammogram in 2029: cancer_screening_results test 'bilateral_screening_mammography', result_status 'final', performed_date 2029; or
-- a payer claim line CPT 77067 (includes interpretation) / HCPCS G0202 with service_date 2029 (voided claims excluded). Distinct patients.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('cancer_screening_results','payer_medical_claims','payer_medical_claim_lines')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup))
SELECT COUNT(DISTINCT patient) AS count FROM (
  SELECT patient FROM r WHERE stream='cancer_screening_results' AND json_extract(payload,'$.test')='bilateral_screening_mammography' AND json_extract(payload,'$.result_status')='final' AND json_extract(payload,'$.performed_date') BETWEEN '2029-01-01' AND '2029-12-31'
  UNION SELECT patient FROM r WHERE stream='payer_medical_claim_lines' AND json_extract(payload,'$.procedure_code') IN ('77067','G0202') AND json_extract(payload,'$.service_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_extract(payload,'$.payer_claim_id') NOT IN (SELECT json_extract(payload,'$.payer_claim_id') FROM r WHERE stream='payer_medical_claims' AND json_extract(payload,'$.claim_status')='voided'));
