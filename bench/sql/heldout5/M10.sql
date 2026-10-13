-- Patients with anemia on the condition list (description containing 'anemia'/'anaemia', SNOMED 271737000, or ICD-10 D50-D64) whose most recent dated numeric hemoglobin (names 'Hemoglobin' or 'Hgb', g/dL; same-day ties -> lowest) is strictly below 11.0 g/dL.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs')),
an AS (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND (lower(json_extract(payload,'$.description')) LIKE '%anemia%' OR lower(json_extract(payload,'$.description')) LIKE '%anaemia%' OR json_extract(payload,'$.snomed')='271737000' OR substr(coalesce(json_extract(payload,'$.icd10'),''),1,3) BETWEEN 'D50' AND 'D64')),
h AS (SELECT patient, json_extract(payload,'$.event_date') d, json_extract(payload,'$.value') v FROM r WHERE stream='labs' AND json_extract(payload,'$.name') IN ('Hemoglobin','Hgb')
      AND typeof(json_extract(payload,'$.value')) IN ('integer','real') AND json_extract(payload,'$.event_date') IS NOT NULL AND coalesce(json_extract(payload,'$.result_status'),'final')<>'cancelled'),
last AS (SELECT patient, max(d) d FROM h GROUP BY patient),
lv AS (SELECT h.patient, min(h.v) v FROM h JOIN last USING(patient, d) GROUP BY h.patient)
SELECT COUNT(*) AS count FROM lv WHERE v < 11.0 AND patient IN (SELECT patient FROM an);
