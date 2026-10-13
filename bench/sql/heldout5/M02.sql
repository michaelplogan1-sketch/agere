-- Patients whose most recent dated eGFR (lab name 'eGFR' in either unit spelling, or 'Glomerular filtration rate') is strictly below 60. Numeric, dated, non-cancelled results only; same-day ties resolved to the lowest value. Undated results cannot be 'most recent'.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs'),
g AS (SELECT patient, json_extract(payload,'$.event_date') d, json_extract(payload,'$.value') v FROM r
      WHERE json_extract(payload,'$.name') IN ('eGFR','Glomerular filtration rate')
        AND typeof(json_extract(payload,'$.value')) IN ('integer','real')
        AND json_extract(payload,'$.event_date') IS NOT NULL
        AND coalesce(json_extract(payload,'$.result_status'),'final')<>'cancelled'),
last AS (SELECT patient, max(d) d FROM g GROUP BY patient),
lv AS (SELECT g.patient, min(g.v) v FROM g JOIN last USING(patient, d) GROUP BY g.patient)
SELECT patient AS patient_id FROM lv WHERE v < 60 ORDER BY patient;
