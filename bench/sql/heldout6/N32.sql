-- % of diagnosed diabetics with >=1 dated, non-cancelled numeric A1c in 2028-08-16..2029-08-15.
WITH dmc AS MATERIALIZED (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM records WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM dmc WHERE ((d LIKE '%diabet%' OR d LIKE 't1dm%' OR d LIKE 't2dm%' OR i LIKE 'E10%' OR i LIKE 'E11%')
  AND d NOT LIKE '%prediabet%' AND d NOT LIKE '%family history%' AND d NOT LIKE 'gdm%' AND i NOT LIKE 'O24%' AND i NOT LIKE 'Z83%')),
a AS (SELECT DISTINCT patient FROM records WHERE stream='labs' AND json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c','Hemoglobin A1c/Hemoglobin.total')
      AND coalesce(json_extract(payload,'$.result_status'),'')<>'cancelled' AND typeof(json_extract(payload,'$.value')) IN ('integer','real')
      AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15')
SELECT round(100.0*(SELECT count(*) FROM dm WHERE patient IN (SELECT patient FROM a))/(SELECT count(*) FROM dm),1) AS value;
