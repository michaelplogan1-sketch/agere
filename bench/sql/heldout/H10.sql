WITH women AS MATERIALIZED (
 SELECT id AS patient FROM patients WHERE json_extract(payload,'$.sex_reported')='F'
  AND json_type(payload,'$.age_reported')='integer' AND json_extract(payload,'$.age_reported') BETWEEN 18 AND 44
), hgb AS MATERIALIZED (
 SELECT patient,json_extract(payload,'$.value') AS v FROM records WHERE stream='labs'
  AND json_extract(payload,'$.name') IN ('Hemoglobin','Hgb') AND json_extract(payload,'$.unit')='g/dL'
  AND json_type(payload,'$.value') IN ('integer','real')
) SELECT COUNT(DISTINCT women.patient) AS count FROM women JOIN hgb USING(patient) WHERE hgb.v<10;
