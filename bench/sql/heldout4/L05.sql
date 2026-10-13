-- Latest dated eGFR < 60 and no CKD/ESRD on the condition list.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs')), c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
ckd AS (SELECT DISTINCT patient FROM c WHERE (i LIKE 'N18%' OR lower(d) LIKE '%chronic kidney disease%' OR d LIKE '%CKD%' OR s='46177005' OR lower(d) LIKE '%end-stage renal disease%')),
g AS (SELECT patient, json_extract(payload,'$.event_date') d, json_extract(payload,'$.value') v FROM r
      WHERE stream='labs' AND json_extract(payload,'$.name')='eGFR' AND json_extract(payload,'$.event_date') IS NOT NULL
        AND json_type(payload,'$.value') IN ('integer','real')),
last AS (SELECT patient, max(d) d FROM g GROUP BY patient),
lastv AS (SELECT g.patient, min(g.v) v FROM g JOIN last l ON l.patient=g.patient AND l.d=g.d GROUP BY g.patient)
SELECT patient AS patient_id FROM lastv WHERE v < 60 AND patient NOT IN (SELECT patient FROM ckd) ORDER BY patient;
