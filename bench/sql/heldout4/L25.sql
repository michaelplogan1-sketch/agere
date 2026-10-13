-- Distinct claims with a denied record followed by a later paid record.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('medical_claims')), mc AS (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.status') st, json_extract(payload,'$.service_date') sd, json_extract(payload,'$.event_date') ed, json_extract(payload,'$.available_time') at, json_extract(payload,'$.organization_id') payer, json_extract(payload,'$.payer_paid_cents') paid FROM r WHERE stream='medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL)
SELECT COUNT(*) AS count FROM (SELECT cid, MIN(CASE WHEN st='denied' THEN at END) fd, MAX(CASE WHEN st='paid' THEN at END) lp FROM mc GROUP BY cid)
WHERE fd IS NOT NULL AND lp > fd;
