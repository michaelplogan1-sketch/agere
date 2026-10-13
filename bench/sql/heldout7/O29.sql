-- Denial overturn rate: among claims (claim_id) whose first adjudication (earliest event_time) was 'denied', % that have a later 'paid' adjudication. All service dates up to as-of.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='medical_claims'),
c AS (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.status') s, json_extract(payload,'$.event_time') t FROM r WHERE json_extract(payload,'$.claim_id') IS NOT NULL),
f AS (SELECT cid, s, row_number() OVER (PARTITION BY cid ORDER BY t) rn FROM c),
d AS (SELECT cid FROM f WHERE rn=1 AND s='denied')
SELECT round(100.0*sum(EXISTS(SELECT 1 FROM f WHERE f.cid=d.cid AND f.rn>1 AND f.s='paid'))/count(*),1) AS value FROM d;
