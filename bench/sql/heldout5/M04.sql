-- Patients with prediabetes on the condition list (SNOMED 714628002, ICD-10 R73.03 or description 'Prediabetes') who do NOT have diabetes (same diabetes definition as M03) and whose most recent dated numeric A1c (HbA1c / Hemoglobin A1c / Hemoglobin A1c/Hemoglobin.total; same-day ties -> lowest) is 6.5 or higher (inclusive) -- possible unrecognized diabetes.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs')),
c AS (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r WHERE stream='conditions'),
dm AS (SELECT DISTINCT patient FROM c WHERE substr(i,1,3) IN ('E10','E11','E13') OR s='44054006' OR (d LIKE '%diabetes mellitus%' AND d NOT LIKE '%family history%') OR d LIKE '%t1dm%' OR d LIKE '%t2dm%'),
pre AS (SELECT DISTINCT patient FROM c WHERE s='714628002' OR i='R73.03' OR d='prediabetes'),
a AS (SELECT patient, json_extract(payload,'$.event_date') ed, json_extract(payload,'$.value') v FROM r WHERE stream='labs'
      AND json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c','Hemoglobin A1c/Hemoglobin.total')
      AND typeof(json_extract(payload,'$.value')) IN ('integer','real') AND json_extract(payload,'$.event_date') IS NOT NULL
      AND coalesce(json_extract(payload,'$.result_status'),'final')<>'cancelled'),
last AS (SELECT patient, max(ed) ed FROM a GROUP BY patient),
lv AS (SELECT a.patient, min(a.v) v FROM a JOIN last USING(patient, ed) GROUP BY a.patient)
SELECT COUNT(*) AS count FROM lv WHERE v >= 6.5 AND patient IN (SELECT patient FROM pre) AND patient NOT IN (SELECT patient FROM dm);
