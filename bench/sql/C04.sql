WITH a1c AS MATERIALIZED (
 SELECT id,patient,event,arrival,json_extract(payload,'$.value') AS value FROM records
 WHERE stream='labs' AND json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c','Hemoglobin A1c/Hemoglobin.total')
 AND json_extract(payload,'$.unit')='%' AND json_type(payload,'$.value') IN ('integer','real')
) SELECT COUNT(*) AS count FROM (SELECT patient FROM a1c WHERE value>9 GROUP BY patient HAVING COUNT(*)>=2);
