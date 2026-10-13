-- Distinct patients with a type 1/type 2 diabetes diagnosis (incl. diabetic complications); excludes prediabetes, GDM/O24 and family history. Condition list only.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='conditions'),
c AS (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM r)
SELECT COUNT(DISTINCT patient) AS count FROM c WHERE ((d LIKE '%diabet%' OR d LIKE 't1dm%' OR d LIKE 't2dm%' OR i LIKE 'E10%' OR i LIKE 'E11%')
  AND d NOT LIKE '%prediabet%' AND d NOT LIKE '%family history%' AND d NOT LIKE 'gdm%' AND i NOT LIKE 'O24%' AND i NOT LIKE 'Z83%');
