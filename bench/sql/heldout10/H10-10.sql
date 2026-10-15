WITH ip AS (
  SELECT patient, coalesce(substr(json_extract(payload,'$.start_time'),1,10), event) AS d FROM records
    WHERE stream='encounters' AND lower(json_extract(payload,'$.type'))='inpatient'
  UNION SELECT patient, event FROM records WHERE stream='adt' AND json_extract(payload,'$.event') IN ('admission','inpatient_admission')
  UNION SELECT patient, event FROM records WHERE stream='adt_messages' AND json_extract(payload,'$.trigger_event')='A01' AND json_extract(payload,'$.patient_class')='I'
  UNION SELECT patient, json_extract(payload,'$.admission_date') FROM records WHERE stream='payer_medical_claims'
    AND json_extract(payload,'$.claim_type')='institutional' AND (json_extract(payload,'$.type_of_bill') LIKE '011%' OR json_extract(payload,'$.type_of_bill') LIKE '11%')
    AND json_extract(payload,'$.admission_date') IS NOT NULL)
SELECT COUNT(*) AS count FROM (SELECT DISTINCT patient, d FROM ip WHERE d BETWEEN '2029-01-01' AND '2029-12-31')
