WITH obs AS (
  SELECT patient, coalesce(substr(json_extract(payload,'$.start_time'),1,10), event) d FROM records WHERE stream='encounters' AND lower(json_extract(payload,'$.type'))='observation'
  UNION SELECT patient, event FROM records WHERE stream='adt' AND json_extract(payload,'$.event') IN ('observation_start','transfer_to_observation')
  UNION SELECT patient, event FROM records WHERE stream='adt_messages' AND json_extract(payload,'$.trigger_event')='A01' AND json_extract(payload,'$.patient_class')='O')
SELECT COUNT(*) AS count FROM obs WHERE d BETWEEN '2029-01-01' AND '2029-12-31'
