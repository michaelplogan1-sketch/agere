-- Patients whose most recent dated numeric potassium (name 'Potassium'; mmol/L and mEq/L are equivalent 1:1) is strictly above 5.0. Hemolyzed rows with no numeric value and cancelled results are excluded; undated rows cannot be most recent; same-day ties -> highest value that day.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs'),
k AS (SELECT patient, json_extract(payload,'$.event_date') d, json_extract(payload,'$.value') v FROM r
      WHERE json_extract(payload,'$.name')='Potassium' AND typeof(json_extract(payload,'$.value')) IN ('integer','real')
        AND json_extract(payload,'$.event_date') IS NOT NULL AND coalesce(json_extract(payload,'$.result_status'),'final')<>'cancelled'),
last AS (SELECT patient, max(d) d FROM k GROUP BY patient),
lv AS (SELECT k.patient, max(k.v) v FROM k JOIN last USING(patient, d) GROUP BY k.patient)
SELECT COUNT(*) AS count FROM lv WHERE v > 5.0;
