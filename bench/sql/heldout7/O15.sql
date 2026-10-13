-- Mean observation stay length in hours: ADT observation_start to the discharge event on the same encounter_id (event_time), over observation stays whose start is in 2029-01-01..2029-08-15 and that have a discharge.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='adt'),
s AS (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_time')) t FROM r WHERE json_extract(payload,'$.event')='observation_start' GROUP BY 1),
d AS (SELECT json_extract(payload,'$.encounter_id') eid, max(json_extract(payload,'$.event_time')) t FROM r WHERE json_extract(payload,'$.event')='discharge' GROUP BY 1)
SELECT round(avg((julianday(d.t)-julianday(s.t))*24),1) AS value FROM s JOIN d USING(eid) WHERE substr(s.t,1,10) BETWEEN '2029-01-01' AND '2029-08-15';
