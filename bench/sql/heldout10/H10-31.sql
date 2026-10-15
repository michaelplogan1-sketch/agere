WITH col AS (
  SELECT patient, json_extract(payload,'$.service_date') d, 'colo' k FROM records WHERE stream='payer_medical_claim_lines' AND json_extract(payload,'$.procedure_code') IN ('45378','45380','45385')
  UNION SELECT patient, json_extract(payload,'$.service_date'), 'colo' FROM records WHERE stream='medical_claim_lines' AND json_extract(payload,'$.service_code') IN ('45378','45380','45385')
  UNION SELECT patient, json_extract(payload,'$.service_date'), 'fit' FROM records WHERE stream='payer_medical_claim_lines' AND json_extract(payload,'$.procedure_code')='82274'
  UNION SELECT patient, json_extract(payload,'$.service_date'), 'fit' FROM records WHERE stream='medical_claim_lines' AND json_extract(payload,'$.service_code')='82274'
  UNION SELECT patient, coalesce(json_extract(payload,'$.performed_date'), event), 'fit' FROM records WHERE stream='cancer_screening_results' AND json_extract(payload,'$.test')='FIT')
SELECT COUNT(*) AS count FROM patients p WHERE (CAST(strftime('%Y','2029-12-31') AS INTEGER) - CAST(strftime('%Y',json_extract(p.payload,'$.birth_date')) AS INTEGER) - (strftime('%m-%d','2029-12-31') < strftime('%m-%d',json_extract(p.payload,'$.birth_date')))) BETWEEN 46 AND 75
  AND p.id IN (SELECT patient FROM col WHERE (k='colo' AND d BETWEEN '2020-01-01' AND '2029-12-31') OR (k='fit' AND d BETWEEN '2029-01-01' AND '2029-12-31'))
