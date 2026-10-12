WITH egfr AS MATERIALIZED (
 SELECT patient,event,json_extract(payload,'$.value') AS value FROM records
 WHERE stream='labs' AND json_extract(payload,'$.name')='eGFR'
 AND json_extract(payload,'$.unit') IN ('mL/min/1.73m2','mL/min/1.73m²') AND json_type(payload,'$.value') IN ('integer','real')
) SELECT COUNT(DISTINCT e.patient) AS count FROM egfr e WHERE e.value<30 AND e.patient NOT IN (SELECT patient FROM records WHERE stream='social_screens' AND json_type(payload,'$.food_difficulty_reported')='true');
