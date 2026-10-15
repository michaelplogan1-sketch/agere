WITH mammo AS (
  SELECT patient, coalesce(json_extract(payload,'$.performed_date'), event) d FROM records WHERE stream='cancer_screening_results' AND json_extract(payload,'$.test') LIKE '%mammograph%'
  UNION SELECT patient, json_extract(payload,'$.service_date') FROM records WHERE stream='payer_medical_claim_lines' AND json_extract(payload,'$.procedure_code') IN ('77065','77066','77067')
  UNION SELECT patient, json_extract(payload,'$.service_date') FROM records WHERE stream='medical_claim_lines' AND json_extract(payload,'$.service_code') IN ('77065','77066','77067'))
SELECT COUNT(*) AS count FROM patients p WHERE json_extract(p.payload,'$.sex_reported')='F' AND (CAST(strftime('%Y','2029-12-31') AS INTEGER) - CAST(strftime('%Y',json_extract(p.payload,'$.birth_date')) AS INTEGER) - (strftime('%m-%d','2029-12-31') < strftime('%m-%d',json_extract(p.payload,'$.birth_date')))) BETWEEN 52 AND 74
  AND p.id IN (SELECT patient FROM mammo WHERE d BETWEEN '2027-10-01' AND '2029-12-31')
