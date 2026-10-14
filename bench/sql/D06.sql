WITH a1c AS MATERIALIZED (
 SELECT id,patient,event,arrival,json_extract(payload,'$.value') AS value FROM records
 WHERE stream='labs' AND lower(json_extract(payload,'$.name')) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total')
 AND json_extract(payload,'$.unit')='%' AND json_type(payload,'$.value') IN ('integer','real')
) SELECT COUNT(DISTINCT patient) AS count FROM a1c WHERE value>9;
