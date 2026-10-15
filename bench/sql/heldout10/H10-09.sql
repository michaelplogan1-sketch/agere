WITH ed AS (
  SELECT patient, coalesce(substr(json_extract(payload,'$.start_time'),1,10), event) AS d FROM records
    WHERE stream='encounters' AND lower(json_extract(payload,'$.type')) IN ('emergency','ed')
  UNION SELECT patient, event FROM records WHERE stream='adt' AND json_extract(payload,'$.event')='emergency_registration'
  UNION SELECT patient, event FROM records WHERE stream='adt_messages' AND json_extract(payload,'$.trigger_event')='A04' AND json_extract(payload,'$.patient_class')='E'
  UNION SELECT patient, json_extract(payload,'$.service_from') FROM records WHERE stream='payer_medical_claims' AND json_extract(payload,'$.place_of_service')='23'
  UNION SELECT patient, json_extract(payload,'$.service_date') FROM records WHERE stream='medical_claims' AND json_extract(payload,'$.place_of_service')='23')
SELECT COUNT(*) AS count FROM (SELECT DISTINCT patient, d FROM ed WHERE d BETWEEN '2029-01-01' AND '2029-12-31')
