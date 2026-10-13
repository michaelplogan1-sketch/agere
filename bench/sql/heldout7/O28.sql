-- Claim correction records (claim_corrections, distinct record) with event_date in 2028 by reason; columns reason, corrections, claims (distinct claim_id).
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='claim_corrections')
SELECT json_extract(payload,'$.reason') AS reason, count(DISTINCT json_extract(payload,'$.record_id')) AS corrections, count(DISTINCT json_extract(payload,'$.claim_id')) AS claims
FROM r WHERE json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31' GROUP BY 1 ORDER BY 1;
