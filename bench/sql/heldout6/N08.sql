-- Latest dated A1c >= 6.5 with no diabetes diagnosis (prediabetes/GDM/family history are not diabetes diagnoses).
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs'),
a AS (SELECT patient, json_extract(payload,'$.event_date') d, json_extract(payload,'$.value') v FROM r
      WHERE json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c','Hemoglobin A1c/Hemoglobin.total') AND json_extract(payload,'$.event_date') IS NOT NULL
        AND coalesce(json_extract(payload,'$.result_status'),'')<>'cancelled' AND typeof(json_extract(payload,'$.value')) IN ('integer','real')),
l AS (SELECT a.patient, max(a.v) v FROM a JOIN (SELECT patient, max(d) md FROM a GROUP BY 1) m ON m.patient=a.patient AND m.md=a.d GROUP BY 1),
c AS (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM records WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM c WHERE ((d LIKE '%diabet%' OR d LIKE 't1dm%' OR d LIKE 't2dm%' OR i LIKE 'E10%' OR i LIKE 'E11%')
  AND d NOT LIKE '%prediabet%' AND d NOT LIKE '%family history%' AND d NOT LIKE 'gdm%' AND i NOT LIKE 'O24%' AND i NOT LIKE 'Z83%'))
SELECT patient AS patient_id FROM l WHERE v >= 6.5 AND patient NOT IN (SELECT patient FROM dm) ORDER BY 1;
