-- Duplicate lab rows: among lab records with an event_time, the number of EXTRA copies = rows minus distinct (patient, test name, event_time, value) combinations (each duplicate group contributes rows-1). Different record_ids with identical content count as duplicates.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs'),
g AS (SELECT patient, coalesce(json_extract(payload,'$.name'),json_extract(payload,'$.test')) n, json_extract(payload,'$.event_time') t, json_extract(payload,'$.value') v, count(*) c FROM r WHERE json_extract(payload,'$.event_time') IS NOT NULL GROUP BY 1,2,3,4)
SELECT sum(c-1) AS count FROM g;
