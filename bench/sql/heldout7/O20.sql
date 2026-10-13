-- Distinct urgent care encounters (type 'urgent_care') at any date up to the as-of date whose completed disposition is 'referred_to_ed'.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
uc AS (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d, max(json_extract(payload,'$.disposition')='referred_to_ed') ref FROM r WHERE json_extract(payload,'$.type')='urgent_care' GROUP BY 1)
SELECT COUNT(*) AS count FROM uc WHERE ref=1;
