-- Distinct encounter_ids that have an 'in_progress' record but no 'completed' record as of the cutoff (open/unclosed encounters); legacy encounters without any status are excluded.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, max(json_extract(payload,'$.status')='in_progress') ip, max(json_extract(payload,'$.status')='completed') c FROM r WHERE json_extract(payload,'$.status') IS NOT NULL GROUP BY 1)
SELECT COUNT(*) AS count FROM e WHERE ip=1 AND c=0;
