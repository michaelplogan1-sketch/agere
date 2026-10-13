-- Home health visits per home-health patient: distinct home_health encounter_ids / distinct patients with any home_health encounter (all dates up to as-of).
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters')
SELECT round(1.0*count(DISTINCT json_extract(payload,'$.encounter_id'))/count(DISTINCT patient),2) AS value FROM r WHERE json_extract(payload,'$.type')='home_health';
