-- Diabetics whose most recent dated A1c (lowest value on the latest date) is >= 8.0%.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs')), c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM c WHERE (substr(i,1,3) IN ('E10','E11','E13') OR s='44054006' OR (lower(d) LIKE '%diabetes mellitus%' AND lower(d) NOT LIKE 'family history%') OR d LIKE '%T1DM%' OR d LIKE '%T2DM%')),
a1c AS (SELECT patient, json_extract(payload,'$.event_date') d, json_extract(payload,'$.value') v FROM r
        WHERE stream='labs' AND json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c','Hemoglobin A1c/Hemoglobin.total')
          AND json_extract(payload,'$.event_date') IS NOT NULL AND json_type(payload,'$.value') IN ('integer','real')),
last AS (SELECT patient, max(d) d FROM a1c WHERE patient IN (SELECT patient FROM dm) GROUP BY patient),
lastv AS (SELECT a.patient, min(a.v) v FROM a1c a JOIN last l ON l.patient=a.patient AND l.d=a.d GROUP BY a.patient)
SELECT patient AS patient_id FROM lastv WHERE v >= 8.0 ORDER BY patient;
