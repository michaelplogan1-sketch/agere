-- KED-style: diabetics age 18-85 with both a dated 2028 eGFR and a dated 2028 UACR.
WITH dmc AS MATERIALIZED (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM records WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM dmc WHERE ((d LIKE '%diabet%' OR d LIKE 't1dm%' OR d LIKE 't2dm%' OR i LIKE 'E10%' OR i LIKE 'E11%')
  AND d NOT LIKE '%prediabet%' AND d NOT LIKE '%family history%' AND d NOT LIKE 'gdm%' AND i NOT LIKE 'O24%' AND i NOT LIKE 'Z83%')),
den AS (SELECT dm.patient FROM dm JOIN patients p ON p.id=dm.patient WHERE json_extract(p.payload,'$.age_reported') BETWEEN 18 AND 85),
l AS MATERIALIZED (SELECT patient, json_extract(payload,'$.name') n FROM records WHERE stream='labs' AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31'
      AND coalesce(json_extract(payload,'$.result_status'),'')<>'cancelled' AND typeof(json_extract(payload,'$.value')) IN ('integer','real'))
SELECT round(100.0*(SELECT count(*) FROM den WHERE patient IN (SELECT patient FROM l WHERE n IN ('eGFR','Glomerular filtration rate'))
                                         AND patient IN (SELECT patient FROM l WHERE n IN ('Urine albumin/creatinine ratio','Microalbumin/Creatinine')))/(SELECT count(*) FROM den),1) AS value;
