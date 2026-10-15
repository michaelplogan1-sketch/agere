-- AMBIGUOUS (reading A): claim denial rate (%) = initial denials: claims (distinct claim_id) whose earliest medical_claims adjudication (event_date in 2029) has status 'denied' / all claims first adjudicated in 2029. Reading B (final denial: denied and never subsequently paid by as-of) is lower. 2 dp.
WITH c AS MATERIALIZED (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.status') st, json_extract(payload,'$.event_date') d, json_extract(payload,'$.record_id') rid FROM records
  WHERE stream='medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL),
f AS (SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY cid ORDER BY d, CASE st WHEN 'reversed' THEN 1 ELSE 0 END, rid) rn FROM c) WHERE rn=1)
SELECT ROUND(100.0*SUM(st='denied')/COUNT(*),2) AS value FROM f WHERE d BETWEEN '2029-01-01' AND '2029-12-31';
