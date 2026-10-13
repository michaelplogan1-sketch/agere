-- Kidney health evaluation for diabetics aged 18-85 (latest reported age): percent with BOTH an eGFR (lab name 'eGFR' or 'Glomerular filtration rate') AND a urine albumin-creatinine ratio ('Urine albumin/creatinine ratio', 'Microalbumin/Creatinine', 'UACR') dated in calendar 2028. Numeric, non-cancelled results; undated rows cannot count. Percent, 1 decimal.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs','demographic_updates')),
c AS (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM c WHERE substr(i,1,3) IN ('E10','E11','E13') OR s='44054006' OR (d LIKE '%diabetes mellitus%' AND d NOT LIKE '%family history%') OR d LIKE '%t1dm%' OR d LIKE '%t2dm%'),
du AS (SELECT patient, json_extract(payload,'$.age_reported') a, json_extract(payload,'$.event_date') ed FROM r WHERE stream='demographic_updates'),
age AS (SELECT p.id patient, coalesce((SELECT a FROM du WHERE du.patient=p.id ORDER BY ed DESC LIMIT 1), json_extract(p.payload,'$.age_reported')) a FROM patients p),
den AS (SELECT patient FROM dm JOIN age USING(patient) WHERE a BETWEEN 18 AND 85),
l AS (SELECT patient, json_extract(payload,'$.name') n FROM r WHERE stream='labs' AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31'
      AND typeof(json_extract(payload,'$.value')) IN ('integer','real') AND coalesce(json_extract(payload,'$.result_status'),'final')<>'cancelled'),
eg AS (SELECT DISTINCT patient FROM l WHERE n IN ('eGFR','Glomerular filtration rate')),
ua AS (SELECT DISTINCT patient FROM l WHERE n IN ('Urine albumin/creatinine ratio','Microalbumin/Creatinine','UACR'))
SELECT ROUND(100.0*(SELECT COUNT(*) FROM den WHERE patient IN (SELECT patient FROM eg) AND patient IN (SELECT patient FROM ua))/(SELECT COUNT(*) FROM den),1) AS value;
