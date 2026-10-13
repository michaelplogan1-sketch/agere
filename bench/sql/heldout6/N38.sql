-- Distinct encounter_ids with an in_progress version and no completed version.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='encounters')
SELECT count(*) AS count FROM (SELECT json_extract(payload,'$.encounter_id') eid FROM r GROUP BY 1
  HAVING sum(json_extract(payload,'$.status')='in_progress')>0 AND sum(json_extract(payload,'$.status')='completed')=0);
