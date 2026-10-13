-- Average days from service_date to the FIRST adjudication record (earliest medical_claims event_time for the claim_id) for claims with service_date in 2028.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='medical_claims'),
c AS (SELECT json_extract(payload,'$.claim_id') cid, min(json_extract(payload,'$.service_date')) sd, min(json_extract(payload,'$.event_date')) ad FROM r WHERE json_extract(payload,'$.claim_id') IS NOT NULL GROUP BY 1)
SELECT round(avg(julianday(ad)-julianday(sd)),1) AS value FROM c WHERE sd BETWEEN '2028-01-01' AND '2028-12-31';
