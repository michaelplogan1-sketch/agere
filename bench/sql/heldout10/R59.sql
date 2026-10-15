WITH awv AS (
  SELECT patient FROM records WHERE stream = 'wellness_visit_services'
    AND (json_extract(payload,'$.visit_type') = 'medicare_annual_wellness' OR upper(json_extract(payload,'$.hcpcs_code')) IN ('G0438', 'G0439'))
    AND event BETWEEN '2029-01-01' AND '2029-12-31'
  UNION
  SELECT patient FROM records WHERE stream = 'payer_medical_claim_lines'
    AND upper(json_extract(payload,'$.procedure_code')) IN ('G0438', 'G0439') AND json_extract(payload,'$.service_date') BETWEEN '2029-01-01' AND '2029-12-31'
  UNION
  SELECT patient FROM records WHERE stream = 'medical_claim_lines'
    AND upper(json_extract(payload,'$.service_code')) IN ('G0438', 'G0439') AND json_extract(payload,'$.service_date') BETWEEN '2029-01-01' AND '2029-12-31'
)
SELECT COUNT(DISTINCT patient) AS count FROM awv
