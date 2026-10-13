-- Distinct observation-stay encounter_ids (encounters.type='observation') with event_date 2029-01-01..2029-08-15. Each stay is received as an in_progress and a completed version; ADT observation_start/transfer events are not separate stays.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters')
SELECT COUNT(DISTINCT json_extract(payload,'$.encounter_id')) AS count FROM r WHERE json_extract(payload,'$.type')='observation' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-08-15';
