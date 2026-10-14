WITH a1c AS MATERIALIZED (
 SELECT id,patient,event,arrival,json_extract(payload,'$.value') AS value FROM records
 WHERE stream='labs' AND lower(json_extract(payload,'$.name')) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total')
 AND json_extract(payload,'$.unit')='%' AND json_type(payload,'$.value') IN ('integer','real')
), ranked AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY patient ORDER BY event DESC,arrival DESC,id DESC) AS rn FROM a1c WHERE date(event)=event AND event<=:as_of) SELECT patient AS patient_id FROM ranked WHERE rn=1 AND value>9 ORDER BY patient;
