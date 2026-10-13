-- % of diabetics with a dated A1c in 2029 YTD.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs')), c AS (SELECT patient, json_extract(payload,'$.description') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM c WHERE (substr(i,1,3) IN ('E10','E11','E13') OR s='44054006' OR (lower(d) LIKE '%diabetes mellitus%' AND lower(d) NOT LIKE 'family history%') OR d LIKE '%T1DM%' OR d LIKE '%T2DM%')),
t AS (SELECT DISTINCT patient FROM r WHERE stream='labs' AND json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c','Hemoglobin A1c/Hemoglobin.total')
      AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-08-15')
SELECT round(100.0*SUM(patient IN (SELECT patient FROM t))/COUNT(*),1) AS value FROM dm;
