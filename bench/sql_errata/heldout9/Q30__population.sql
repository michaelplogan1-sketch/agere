-- Patients with a Medicare annual wellness visit in 2029, population-wide: wellness_visit_services visit_type
-- 'medicare_annual_wellness', status 'completed', event_date in 2029 (original roster), or a payer claim line HCPCS G0438 (initial AWV) /
-- G0439 (subsequent AWV) with service_date in 2029 (voided claims excluded). Preventive-medicine visits (9938x/9939x) are not AWVs.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('wellness_visit_services','payer_medical_claims','payer_medical_claim_lines'))
SELECT COUNT(DISTINCT patient) AS count FROM (
  SELECT patient FROM r WHERE stream='wellness_visit_services' AND json_extract(payload,'$.visit_type')='medicare_annual_wellness' AND json_extract(payload,'$.status')='completed' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31'
  UNION SELECT patient FROM r WHERE stream='payer_medical_claim_lines' AND json_extract(payload,'$.procedure_code') IN ('G0438','G0439') AND json_extract(payload,'$.service_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_extract(payload,'$.payer_claim_id') NOT IN (SELECT json_extract(payload,'$.payer_claim_id') FROM r WHERE stream='payer_medical_claims' AND json_extract(payload,'$.claim_status')='voided'));
