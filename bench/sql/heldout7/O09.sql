-- Latest dated eGFR (labs named 'eGFR', unit mL/min/1.73m2, non-cancelled numeric; undated rows incl. 'Glomerular filtration rate' ignored; ties on latest date -> lowest) < 30.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs'),
e AS (SELECT patient, json_extract(payload,'$.event_date') d, CAST(json_extract(payload,'$.value') AS REAL) v FROM r
      WHERE coalesce(json_extract(payload,'$.name'),json_extract(payload,'$.test'))='eGFR' AND json_extract(payload,'$.event_date') IS NOT NULL
        AND coalesce(json_extract(payload,'$.result_status'),'final')<>'cancelled' AND typeof(json_extract(payload,'$.value')) IN ('integer','real')),
l AS (SELECT patient, max(d) d FROM e GROUP BY 1),
lv AS (SELECT e.patient, min(v) v FROM e JOIN l USING(patient,d) GROUP BY 1)
SELECT COUNT(*) AS count FROM lv WHERE v < 30;
