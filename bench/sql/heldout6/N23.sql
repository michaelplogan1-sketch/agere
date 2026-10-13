-- % of claim_ids with a 'denied' adjudication that later received a 'paid' adjudication (by event_time).
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='medical_claims'),
c AS MATERIALIZED (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.status') s, json_extract(payload,'$.event_time') t FROM r WHERE json_extract(payload,'$.claim_id') IS NOT NULL),
d AS MATERIALIZED (SELECT cid, min(t) fd FROM c WHERE s='denied' GROUP BY 1)
SELECT round(100.0*(SELECT count(*) FROM d WHERE EXISTS (SELECT 1 FROM c WHERE c.cid=d.cid AND c.s='paid' AND c.t>d.fd))/(SELECT count(*) FROM d),1) AS value;
