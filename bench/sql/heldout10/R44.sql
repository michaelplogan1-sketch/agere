WITH visits AS (
  SELECT patient, MIN(event) AS d FROM records
  WHERE stream = 'adt_messages' AND json_extract(payload,'$.patient_class') = 'E'
  GROUP BY patient, json_extract(payload,'$.visit_number')
  UNION
  SELECT patient, MIN(event) FROM records
  WHERE stream = 'adt' AND (json_extract(payload,'$.event') = 'emergency_registration' OR json_extract(payload,'$.patient_class') = 'emergency')
  GROUP BY patient, json_extract(payload,'$.encounter_id')
  UNION
  SELECT patient, MIN(event) FROM records
  WHERE stream = 'encounters' AND (lower(json_extract(payload,'$.type')) IN ('emergency', 'ed') OR lower(json_extract(payload,'$.type')) LIKE 'ed %')
  GROUP BY patient, json_extract(payload,'$.encounter_id')
  UNION
  SELECT patient, json_extract(payload,'$.service_from') FROM (SELECT * FROM records WHERE stream='payer_medical_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_medical_claims' AND supersedes IS NOT NULL))
  WHERE json_extract(payload,'$.place_of_service') = '23'
  UNION
  SELECT patient, json_extract(payload,'$.service_date') FROM records
  WHERE stream = 'medical_claim_lines'
    AND (json_extract(payload,'$.revenue_code') = '0450' OR json_extract(payload,'$.service_code') IN ('99281','99282','99283','99284','99285'))
)
SELECT patient AS patient_id FROM visits
WHERE d BETWEEN '2029-05-28' AND '2030-05-27'
GROUP BY patient HAVING COUNT(DISTINCT d) >= 4
