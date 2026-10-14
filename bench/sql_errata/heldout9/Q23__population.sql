-- Patients with a screening mammogram in 2029, population-wide: cancer_screening_results test 'bilateral_screening_mammography',
-- result_status 'final', performed_date in 2029 (original roster), or a payer claim line CPT 77067 (screening mammography, includes the
-- interpretation) / HCPCS G0202 with service_date in 2029 (voided claims excluded). Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('cancer_screening_results','payer_medical_claims','payer_medical_claim_lines'))
SELECT COUNT(DISTINCT patient) AS count FROM (
  SELECT patient FROM r WHERE stream='cancer_screening_results' AND json_extract(payload,'$.test')='bilateral_screening_mammography' AND json_extract(payload,'$.result_status')='final' AND json_extract(payload,'$.performed_date') BETWEEN '2029-01-01' AND '2029-12-31'
  UNION SELECT patient FROM r WHERE stream='payer_medical_claim_lines' AND json_extract(payload,'$.procedure_code') IN ('77067','G0202') AND json_extract(payload,'$.service_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_extract(payload,'$.payer_claim_id') NOT IN (SELECT json_extract(payload,'$.payer_claim_id') FROM r WHERE stream='payer_medical_claims' AND json_extract(payload,'$.claim_status')='voided'));
