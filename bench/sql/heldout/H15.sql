WITH stays AS (
 SELECT patient,COUNT(DISTINCT json_extract(payload,'$.encounter_id')) AS n FROM records WHERE stream='encounters'
  AND json_extract(payload,'$.type')='inpatient' AND json_extract(payload,'$.encounter_id') IS NOT NULL GROUP BY patient
) SELECT COUNT(*) AS count FROM stays WHERE n>=2;
