WITH spo2 AS MATERIALIZED (
 SELECT patient,json_extract(payload,'$.value') AS v FROM records WHERE stream='vitals'
  AND json_extract(payload,'$.measurement')='spo2' AND json_extract(payload,'$.unit')='%'
  AND json_type(payload,'$.value') IN ('integer','real')
 UNION ALL
 SELECT patient,json_extract(payload,'$.oxygen_saturation_percent') FROM records WHERE stream='vitals'
  AND json_type(payload,'$.oxygen_saturation_percent') IN ('integer','real')
) SELECT COUNT(DISTINCT patient) AS count FROM spo2 WHERE v<90;
